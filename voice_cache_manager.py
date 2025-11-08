# voice_cache_manager.py - Smart voice cache management for TTS tracks
# Integrates with existing session tracking system

import asyncio
import logging
import time
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, desc, and_, or_, text, select
from models import Track, TrackPlays, User, VoiceGenerationStatus
from hls_streaming import stream_manager
import anyio

# Redis voice access tracker for multi-container support
from redis_state.cache.voice_access import voice_access_tracker as redis_voice_tracker

# Centralized popular tracks service
from popular_tracks_service import popular_tracks_service

logger = logging.getLogger(__name__)

# Configuration
MAX_VOICES_REGULAR = 3  # Max voices for regular tracks
MAX_VOICES_POPULAR = 5  # Max voices for popular tracks
VOICE_IDLE_TIMEOUT = 600  # Seconds before voice cache considered idle
# Note: TOP_POPULAR_TRACKS moved to popular_tracks_service.py for centralization

# Async/sync DB compatibility helpers
def _is_async(db) -> bool:
    """Check if database session is async"""
    return isinstance(db, AsyncSession)

async def _exec(db, stmt):
    """Execute a SQLAlchemy statement on either AsyncSession or sync Session without blocking"""
    if _is_async(db):
        return await db.execute(stmt)
    return await anyio.to_thread.run_sync(db.execute, stmt)

# VoiceAccessTracker now uses Redis - old class replaced with redis_voice_tracker
# The class definition is kept for backward compatibility but delegates to Redis
class VoiceAccessTracker:
    """Track voice access using Redis - shared across all containers"""

    def __init__(self):
        # Delegate to Redis tracker
        self._redis_tracker = redis_voice_tracker
        self.voice_access_cache = self._redis_tracker.voice_access_cache
        self.cache_ttl = self._redis_tracker.cache_ttl

    def record_segment_access(self, track_id: str, voice_id: str, segment_id: str = None):
        """Delegate to Redis tracker"""
        self._redis_tracker.record_segment_access(track_id, voice_id, segment_id)

    def get_voice_activity(self, track_id: str, voice_id: str) -> Dict:
        """Delegate to Redis tracker"""
        return self._redis_tracker.get_voice_activity(track_id, voice_id)

    def cleanup_old_entries(self):
        """Delegate to Redis tracker (handled by TTL)"""
        self._redis_tracker.cleanup_old_entries()

# Global tracker instance
voice_access_tracker = VoiceAccessTracker()

class VoiceCacheManager:
    """Manages voice cache limits and cleanup for TTS tracks"""

    def __init__(self):
        self.max_voices_regular = MAX_VOICES_REGULAR
        self.max_voices_popular = MAX_VOICES_POPULAR
        self.voice_idle_timeout = VOICE_IDLE_TIMEOUT
        self.access_tracker = voice_access_tracker
        # Note: Popular track logic delegated to popular_tracks_service
        
    async def is_track_popular(self, track_id: str, creator_id: int, db) -> bool:
        """
        Check if a track is popular (eligible for 5 voices instead of 3).

        Delegates to centralized popular_tracks_service for consistent logic.
        """
        return await popular_tracks_service.is_track_popular(track_id, creator_id, db)
    
    async def get_cached_voices(self, track_id: str, db) -> List[Dict]:
        """Get list of cached voices for a track with activity info"""
        cached_voices = []
        
        try:
            track_dir = stream_manager.segment_dir / track_id
            
            # Use async file check
            track_dir_exists = await anyio.to_thread.run_sync(track_dir.exists)
            if not track_dir_exists:
                return []
            
            # Use async glob
            voice_dirs = await anyio.to_thread.run_sync(lambda: list(track_dir.glob("voice-*")))
            
            for voice_dir in voice_dirs:
                is_dir = await anyio.to_thread.run_sync(voice_dir.is_dir)
                if not is_dir:
                    continue
                    
                voice_id = voice_dir.name.replace("voice-", "")
                master_playlist = voice_dir / "master.m3u8"
                
                playlist_exists = await anyio.to_thread.run_sync(master_playlist.exists)
                if playlist_exists:
                    activity = self.access_tracker.get_voice_activity(track_id, voice_id)
                    
                    # Calculate directory size asynchronously
                    dir_size = await anyio.to_thread.run_sync(
                        lambda: sum(f.stat().st_size for f in voice_dir.rglob('*') if f.is_file())
                    )
                    
                    # Query database for play count
                    stmt = select(func.sum(TrackPlays.play_count)).filter(
                        TrackPlays.track_id == track_id
                    )
                    result = await _exec(db, stmt)
                    track_plays = result.scalar() or 0
                    
                    estimated_voice_plays = (track_plays // 5) if track_plays else 0
                    
                    cached_voices.append({
                        'voice_id': voice_id,
                        'last_access': activity['last_access'],
                        'is_active': activity['is_active'],
                        'segment_access_count': activity['segment_count'],
                        'unique_segments_accessed': activity['unique_segments'],
                        'estimated_plays': estimated_voice_plays,
                        'dir_path': voice_dir,
                        'size_bytes': dir_size,
                        'size_mb': round(dir_size / (1024 * 1024), 2),
                        'time_since_access': activity['time_since_access']
                    })
            
            cached_voices.sort(key=lambda x: (x['is_active'], x['segment_access_count']))
            
            logger.info(f"Found {len(cached_voices)} cached voices for track {track_id}")
            return cached_voices
            
        except Exception as e:
            logger.error(f"Error getting cached voices: {e}", exc_info=True)
            return []

    async def get_inflight_voice_count(self, track_id: str, db) -> int:
        """Count in-flight voice generations from database (excludes stale records > 90 min)"""
        try:
            # Only count recent generating voices to exclude stale records
            from datetime import datetime, timezone, timedelta
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=90)

            stmt = select(func.count()).where(
                and_(
                    VoiceGenerationStatus.track_id == track_id,
                    VoiceGenerationStatus.status == 'generating',
                    VoiceGenerationStatus.started_at > cutoff  # ✅ Exclude stale records
                )
            )
            result = await _exec(db, stmt)
            count = result.scalar() or 0
            return count
        except Exception as e:
            logger.error(f"Error counting in-flight voices: {e}", exc_info=True)
            return 0

    async def is_voice_generating(self, track_id: str, voice_id: str, db) -> bool:
        """Check if a specific voice is currently generating (database-driven lock check)"""
        try:
            stmt = select(VoiceGenerationStatus).where(
                and_(
                    VoiceGenerationStatus.track_id == track_id,
                    VoiceGenerationStatus.voice_id == voice_id,
                    VoiceGenerationStatus.status == 'generating'
                )
            )
            result = await _exec(db, stmt)
            status = result.scalar_one_or_none()
            return status is not None
        except Exception as e:
            logger.error(f"Error checking if voice is generating: {e}", exc_info=True)
            return False

    async def mark_voice_inflight(self, track_id: str, voice_id: str, db) -> bool:
        """
        DEPRECATED: This method should not be used anymore.
        Locking is now handled by status_lock.try_lock_voice().
        This method is kept for backwards compatibility but does nothing.
        """
        logger.warning(f"⚠️ mark_voice_inflight() called - this is deprecated, use status_lock.try_lock_voice() instead")
        return True

    async def can_add_voice(self, track_id: str, voice_id: str, creator_id: int, db) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """Check if a new voice can be added for a track (with database tracking for in-flight)"""
        logger.info(f"🔍 DEBUG: can_add_voice() ENTRY - track={track_id[:8]}..., voice={voice_id}, creator={creator_id}")
        try:
            logger.info(f"🔍 DEBUG: Fetching track from database...")
            stmt = select(Track).filter(Track.id == track_id)
            result = await _exec(db, stmt)
            track = result.scalar_one_or_none()
            default_voice = track.default_voice if track else None
            logger.info(f"🔍 DEBUG: Track fetched, default_voice={default_voice}")

            logger.info(f"🔍 DEBUG: Checking if track is popular...")
            is_popular = await self.is_track_popular(track_id, creator_id, db)
            max_voices = self.max_voices_popular if is_popular else self.max_voices_regular
            logger.info(f"🔍 DEBUG: is_popular={is_popular}, max_voices={max_voices}")

            # Count BOTH completed AND in-flight voices
            logger.info(f"🔍 DEBUG: Getting cached voices from filesystem...")
            cached_voices = await self.get_cached_voices(track_id, db)  # Completed (filesystem)
            completed_count = len(cached_voices)
            logger.info(f"🔍 DEBUG: Found {completed_count} cached voices on filesystem")

            logger.info(f"🔍 DEBUG: Getting in-flight count from database...")
            inflight_count = await self.get_inflight_voice_count(track_id, db)  # In-flight (database)
            logger.info(f"🔍 DEBUG: Found {inflight_count} in-flight voices in database")

            total_voices = completed_count + inflight_count

            logger.info(
                f"🔍 DETAILED TRACKING: Track {track_id}: popular={is_popular}, max={max_voices}, "
                f"completed={completed_count}, inflight={inflight_count}, total={total_voices}, "
                f"default_voice={default_voice}"
            )

            # Check if this specific voice is already being generated
            try:
                check_stmt = select(VoiceGenerationStatus).where(
                    and_(
                        VoiceGenerationStatus.track_id == track_id,
                        VoiceGenerationStatus.voice_id == voice_id,
                        VoiceGenerationStatus.status == 'generating'
                    )
                )
                existing = await _exec(db, check_stmt)
                if existing.scalar_one_or_none():
                    logger.warning(f"Voice {voice_id} already being generated for track {track_id}")
                    return False, f"Voice {voice_id} is already being generated", None
            except Exception:
                pass

            if total_voices < max_voices:
                # Can add - caller will acquire lock via status_lock.try_lock_voice()
                return True, None, None

            # Try to find removable voice (only from completed, not in-flight)
            inactive_voices = [v for v in cached_voices
                               if not v['is_active'] and v['voice_id'] != default_voice]

            if inactive_voices:
                voice_to_remove = inactive_voices[0]
                logger.info(f"Can remove inactive voice {voice_to_remove['voice_id']} (accessed {voice_to_remove['segment_access_count']} segments)")
                # Can add - caller will acquire lock via status_lock.try_lock_voice()
                return True, None, voice_to_remove

            for voice in cached_voices:
                if voice['voice_id'] != default_voice and voice['time_since_access'] > self.voice_idle_timeout:
                    logger.info(f"Voice {voice['voice_id']} idle for {voice['time_since_access']:.0f}s, can be removed")
                    # Can add - caller will acquire lock via status_lock.try_lock_voice()
                    return True, None, voice

            reason = f"Maximum {max_voices} voices for this {'popular' if is_popular else 'regular'} track (completed: {completed_count}, generating: {inflight_count}). All voices are in use."
            logger.warning(f"Cannot add voice to track {track_id}: {reason}")
            return False, reason, None

        except Exception as e:
            logger.error(f"🔴 EXCEPTION in can_add_voice(): {e}", exc_info=True)
            logger.error(f"🔴 Exception type: {type(e).__name__}")
            logger.error(f"🔴 track_id={track_id}, voice_id={voice_id}, creator_id={creator_id}")
            return False, f"Error checking voice limits: {str(e)}", None
    
    async def remove_voice(self, track_id: str, voice_id: str) -> bool:
        """Remove a cached voice for a track"""
        try:
            voice_dir = stream_manager.segment_dir / track_id / f"voice-{voice_id}"
            
            # Use async filesystem check
            voice_dir_exists = await anyio.to_thread.run_sync(voice_dir.exists)
            
            if voice_dir_exists:
                # Use async rmtree
                await anyio.to_thread.run_sync(shutil.rmtree, voice_dir, True)
                logger.info(f"Removed voice directory: {voice_dir}")
                
                if track_id in self.access_tracker.voice_access_cache:
                    self.access_tracker.voice_access_cache[track_id].pop(voice_id, None)
                
                try:
                    from enhanced_tts_voice_service import enhanced_voice_tts_service
                    cache_key = f"{track_id}:{voice_id}"
                    if cache_key in enhanced_voice_tts_service.word_timing_cache:
                        del enhanced_voice_tts_service.word_timing_cache[cache_key]
                except Exception:
                    pass
                
                logger.info(f"Successfully removed voice {voice_id} from track {track_id}")
                return True
            else:
                logger.warning(f"Voice directory not found: {voice_dir}")
                return False
                
        except Exception as e:
            logger.error(f"Error removing voice: {e}", exc_info=True)
            return False
    
    async def enforce_voice_limit(self, track_id: str, new_voice_id: str, creator_id: int, db) -> Tuple[bool, Optional[str]]:
        """Enforce voice limit before adding a new voice"""
        logger.info(f"🔍 DEBUG: enforce_voice_limit() CALLED - track={track_id[:8]}..., voice={new_voice_id}, creator={creator_id}")
        try:
            logger.info(f"🔍 DEBUG: Calling can_add_voice()...")
            can_add, reason, voice_to_remove = await self.can_add_voice(track_id, new_voice_id, creator_id, db)
            logger.info(f"🔍 DEBUG: can_add_voice() returned: can_add={can_add}, reason={reason}, voice_to_remove={voice_to_remove is not None}")
            
            if not can_add:
                return False, reason
            
            if voice_to_remove:
                logger.info(f"Removing voice {voice_to_remove['voice_id']} to make room for {new_voice_id}")
                removed = await self.remove_voice(track_id, voice_to_remove['voice_id'])
                
                if not removed:
                    return False, "Failed to remove old voice to make room"
                
                logger.info(f"Successfully removed {voice_to_remove['voice_id']}, freed {voice_to_remove['size_mb']}MB")
            
            return True, None
            
        except Exception as e:
            logger.error(f"Error enforcing voice limit: {e}", exc_info=True)
            return False, f"Error managing voice cache: {str(e)}"
    
    async def get_voice_cache_status(self, track_id: str, creator_id: int, db) -> Dict:
        """Get comprehensive cache status for a track"""
        try:
            is_popular = await self.is_track_popular(track_id, creator_id, db)
            max_voices = self.max_voices_popular if is_popular else self.max_voices_regular
            cached_voices = await self.get_cached_voices(track_id, db)
            
            stmt = select(Track).filter(Track.id == track_id)
            result = await _exec(db, stmt)
            track = result.scalar_one_or_none()
            default_voice = track.default_voice if track else None
            
            total_size = sum(v['size_bytes'] for v in cached_voices)
            
            return {
                'track_id': track_id,
                'is_popular': is_popular,
                'max_voices': max_voices,
                'current_voices': len(cached_voices),
                'default_voice': default_voice,
                'cached_voices': cached_voices,
                'total_cache_size_mb': round(total_size / (1024 * 1024), 2),
                'can_add_more': len(cached_voices) < max_voices,
                'voices_over_limit': max(0, len(cached_voices) - max_voices)
            }

        except Exception as e:
            logger.error(f"Error getting cache status: {e}", exc_info=True)
            return {
                'track_id': track_id,
                'error': str(e),
                'current_voices': 0,
                'can_add_more': False
            }

    async def mark_voice_complete(self, track_id: str, voice_id: str, db) -> bool:
        """
        DEPRECATED: This method should not be used anymore.
        Unlocking is now handled by status_lock.unlock_voice().
        This method is kept for backwards compatibility but does nothing.
        """
        logger.warning(f"⚠️ mark_voice_complete() called - this is deprecated, use status_lock.unlock_voice() instead")
        return True

    async def mark_voice_failed(self, track_id: str, voice_id: str, error: str, db) -> bool:
        """
        DEPRECATED: This method should not be used anymore.
        Unlocking with failure is now handled by status_lock.unlock_voice(success=False).
        This method is kept for backwards compatibility but does nothing.
        """
        logger.warning(f"⚠️ mark_voice_failed() called - this is deprecated, use status_lock.unlock_voice(success=False) instead")
        return True

# Global instance
voice_cache_manager = VoiceCacheManager()

__all__ = ['voice_cache_manager', 'voice_access_tracker']