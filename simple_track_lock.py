# simple_track_lock.py
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

import anyio
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from models import Track

logger = logging.getLogger(__name__)

# -------- Async/sync helpers --------

def _is_async(db) -> bool:
    """Check if database session is async."""
    return isinstance(db, AsyncSession)

async def _exec(db, stmt):
    """Execute a SQLAlchemy statement on either AsyncSession or sync Session without blocking."""
    if _is_async(db):
        return await db.execute(stmt)
    return await anyio.to_thread.run_sync(db.execute, stmt)

async def _commit(db):
    """Commit transaction without blocking."""
    if _is_async(db):
        await db.commit()
    else:
        await anyio.to_thread.run_sync(db.commit)

async def _rollback(db):
    """Rollback transaction without blocking."""
    if _is_async(db):
        await db.rollback()
    else:
        await anyio.to_thread.run_sync(db.rollback)

# -------- Lock implementation --------

class SimpleTrackLock:
    """
    Simple track locking to prevent concurrent processing on a track.

    Design:
      - Uses a per-track asyncio.Lock for local process mutual exclusion.
      - Uses a DB row-level lock via SELECT ... FOR UPDATE where supported.
      - Clears stale locks older than lock_timeout_minutes.
    """

    def __init__(self):
        self.lock_timeout_minutes = 90
        self.local_locks: dict[str, asyncio.Lock] = {}
        self.local_lock_creation = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None
        self._cleanup_interval_minutes = 30  # Run every 30 minutes

    async def try_lock_track(
        self, 
        track_id: str, 
        process_type: str, 
        db: Session,
        voice_id: Optional[str] = None  # ✅ NEW
    ) -> Tuple[bool, str]:
        """
        Try to lock a track for processing (optionally voice-specific).
        
        Lock logic:
        - If voice_id provided: Only blocks same voice, allows different voices
        - If no voice_id: Blocks entire track (full serialization)
        """
        async with self.local_lock_creation:
            if track_id not in self.local_locks:
                self.local_locks[track_id] = asyncio.Lock()

        async with self.local_locks[track_id]:
            try:
                res = await _exec(
                    db,
                    select(Track).where(Track.id == track_id).with_for_update()
                )
                track = res.scalar_one_or_none()
                if not track:
                    return False, "Track not found"

                now = datetime.now(timezone.utc)

                # Check if track is locked
                if track.processing_locked_at:
                    age = now - track.processing_locked_at
                    
                    # Check if lock is stale
                    if age.total_seconds() >= (self.lock_timeout_minutes * 60):
                        logger.info(f"Taking over stale lock for track {track_id} (age: {age})")
                    else:
                        # Lock is active - check if it conflicts
                        if voice_id and track.processing_voice:
                            # Voice-specific mode: only block same voice
                            if track.processing_voice != voice_id:
                                logger.info(
                                    f"Allowing concurrent voice generation: {track_id} "
                                    f"(locked={track.processing_voice}, requested={voice_id})"
                                )
                                # Don't update lock fields - other voice owns them
                                # Just return success for this voice
                                return True, "Concurrent voice allowed"
                            else:
                                return False, f"Voice {voice_id} locked by {track.processing_type} ({age})"
                        else:
                            # Track-level lock blocks everything
                            return False, f"Track locked by {track.processing_type} ({age})"

                # Acquire lock
                track.processing_locked_at = now
                track.processing_type = process_type
                track.processing_voice = voice_id  # ✅ Store which voice
                track.hls_ready = False
                await _commit(db)

                logger.info(f"✅ LOCKED track {track_id} for {process_type} (voice: {voice_id or 'full'})")
                return True, "Lock acquired"

            except Exception as e:
                try:
                    await _rollback(db)
                except Exception:
                    pass
                logger.error(f"Error locking track {track_id}: {e}", exc_info=True)
                return False, f"Database error: {str(e)}"

    async def unlock_track(
        self,
        track_id: str,
        success: bool,
        db: Session,
        *,
        voice_id: Optional[str] = None,      # accepted for compatibility
        process_type: Optional[str] = None,  # accepted for compatibility
        **_ignored,                          # swallow any future extras
    ):
        """
        Release the track lock and update status flags.
        Validates HLS segments exist before marking as complete.
        NOTE: voice_id/process_type are accepted for backward compatibility
              but not required by this lock implementation.
        """
        try:
            res = await _exec(db, select(Track).where(Track.id == track_id))
            track = res.scalar_one_or_none()
            if not track:
                return

            # Clear lock fields
            track.processing_locked_at = None
            track.processing_type = None
            track.processing_voice = None

            # Validate HLS segments if caller claims success
            if success:
                success = await self._validate_hls_segments(track_id, voice_id or track.default_voice)
                if not success:
                    logger.error(f"Track {track_id} marked success but HLS segments missing/incomplete")

            # Final status
            if success:
                track.hls_ready = True
                track.segmentation_status = "complete"
                track.processing_error = None
            else:
                track.hls_ready = False
                track.segmentation_status = "incomplete"
                if track.processing_error is None:
                    track.processing_error = "HLS segments missing or incomplete"

            await _commit(db)
            logger.info(f"✅ UNLOCKED track {track_id}: {'SUCCESS' if success else 'FAILED'}")

        except Exception as e:
            logger.error(f"Error unlocking track {track_id}: {e}", exc_info=True)
            try:
                await _rollback(db)
            except Exception:
                pass

    async def _validate_hls_segments(self, track_id: str, voice_id: Optional[str]) -> bool:
        """
        Validate that HLS segments actually exist on disk.
        Returns True if segments are valid, False otherwise.
        """
        try:
            from pathlib import Path

            # Import here to avoid circular dependency
            from hls_streaming import stream_manager

            # Determine segment directory based on voice
            segment_dir = stream_manager.segment_dir / track_id

            if voice_id:
                voice_dir = segment_dir / f"voice-{voice_id}"
                master_playlist = voice_dir / "master.m3u8"
                variant_dir = voice_dir / "default"
            else:
                voice_dir = segment_dir
                master_playlist = segment_dir / "master.m3u8"
                variant_dir = segment_dir / "default"

            # Check master playlist exists
            if not master_playlist.exists():
                logger.warning(f"HLS validation failed: master.m3u8 missing for track {track_id}")
                return False

            # Check variant directory exists
            if not variant_dir.exists():
                logger.warning(f"HLS validation failed: variant directory missing for track {track_id}")
                return False

            # Check for segment files
            segments = list(variant_dir.glob("segment_*.ts"))
            if not segments:
                logger.warning(f"HLS validation failed: no .ts segments found for track {track_id}")
                return False

            logger.debug(f"HLS validation passed: track {track_id} has {len(segments)} segments")
            return True

        except Exception as e:
            logger.error(f"Error validating HLS segments for {track_id}: {e}", exc_info=True)
            # On validation error, assume segments are invalid
            return False

    async def is_track_locked(self, track_id: str, db: Session) -> Tuple[bool, Optional[str]]:
        """
        Check if a track is currently locked.
        Returns (is_locked, processing_type or 'stale'/None).
        """
        try:
            res = await _exec(db, select(Track).where(Track.id == track_id))
            track = res.scalar_one_or_none()
            if not track or not track.processing_locked_at:
                return False, None

            now = datetime.now(timezone.utc)
            age = now - track.processing_locked_at
            if age.total_seconds() >= (self.lock_timeout_minutes * 60):
                return False, "stale"
            return True, track.processing_type

        except Exception as e:
            logger.error(f"Error checking lock for {track_id}: {e}", exc_info=True)
            return False, None

    async def cleanup_stale_locks_on_startup(self, db: Session) -> int:
        """
        Clear locks older than the timeout at startup.
        Use a short-lived Session passed in by the caller.
        """
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.lock_timeout_minutes)
            res = await _exec(db, select(Track).where(Track.processing_locked_at < cutoff))
            stale = res.scalars().all() or []

            if not stale:
                logger.info("✅ No stale locks found on startup")
                return 0

            for track in stale:
                age = datetime.now(timezone.utc) - track.processing_locked_at
                logger.info(f"Clearing stale lock for track {track.id} (age: {age})")
                track.processing_locked_at = None
                track.processing_type = None
                track.processing_voice = None

            await _commit(db)
            logger.info(f"✅ Cleared {len(stale)} stale locks on startup")
            return len(stale)

        except Exception as e:
            logger.error(f"Error during startup cleanup: {e}", exc_info=True)
            try:
                await _rollback(db)
            except Exception:
                pass
            return 0

    async def clear_all_locks_on_startup(self, db: Session) -> int:
        """
        Clear all track locks at startup (since the server restarted).
        Mark interrupted tracks as failed so users can retry.
        Use a short-lived Session passed in by the caller.
        """
        try:
            res = await _exec(db, select(Track).where(Track.processing_locked_at is not None))
            locked_tracks = res.scalars().all() or []

            if not locked_tracks:
                logger.info("✅ No locks found on startup")
                return 0

            # Import TrackStatusManager here to avoid potential circular imports
            from track_status_manager import TrackStatusManager

            for track in locked_tracks:
                # Check if track is actually complete despite having a lock
                is_complete = (
                    track.hls_ready and
                    track.segmentation_status == 'complete'
                )

                if is_complete:
                    # Track completed but lock wasn't released - just clear lock
                    logger.info(f"Track {track.id} is complete, just clearing lock (not marking failed)")
                    track.processing_locked_at = None
                    track.processing_type = None
                    track.processing_voice = None
                else:
                    # Track was interrupted during processing - mark as failed
                    logger.info(f"Marking track {track.id} as failed (server restart)")

                    # Use TrackStatusManager for consistent failure handling
                    await TrackStatusManager.mark_failed(
                        track,
                        db,
                        error=Exception("Server restarted during processing"),
                        pipeline="server_restart"
                    )

            await _commit(db)
            logger.info(f"✅ Processed {len(locked_tracks)} locked tracks on startup")
            return len(locked_tracks)

        except Exception as e:
            logger.error(f"Error during startup cleanup: {e}", exc_info=True)
            try:
                await _rollback(db)
            except Exception:
                pass
            return 0

    async def start_periodic_cleanup(self):
        """Start the periodic cleanup task."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
            logger.info(f"🔄 Started periodic lock cleanup (interval: {self._cleanup_interval_minutes} minutes)")

    async def stop_periodic_cleanup(self):
        """Stop the periodic cleanup task."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            logger.info("🛑 Stopped periodic lock cleanup")

    async def _periodic_cleanup(self):
        """Background task that periodically cleans up stale locks."""
        while True:
            try:
                await asyncio.sleep(self._cleanup_interval_minutes * 60)
                logger.debug("🔍 Running periodic stale lock cleanup...")
                
                # Use a fresh DB session for each cleanup
                from database import get_db
                db = next(get_db())
                try:
                    cleared = await self.cleanup_stale_locks_on_startup(db)
                    if cleared > 0:
                        logger.info(f"🧹 Periodic cleanup cleared {cleared} stale locks")
                finally:
                    db.close()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in periodic lock cleanup: {e}", exc_info=True)
                # Continue the loop even if cleanup fails


# Global instance
simple_track_lock = SimpleTrackLock()