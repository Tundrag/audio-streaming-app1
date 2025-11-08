"""
Status-based locking mechanism using atomic database transitions.
Replaces simple_track_lock.py with cleaner status-based approach.

Key Principle:
  Lock = status IN ('generating', 'segmenting') + processing_voice
  NOT Lock = status IN ('complete', 'failed') OR processing_voice IS NULL
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple
from pathlib import Path

import anyio
from sqlalchemy import select, update, or_, and_, not_
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from models import Track
from track_status_manager import TrackStatusManager

logger = logging.getLogger(__name__)


def _is_async(db) -> bool:
    """Check if database session is async."""
    return isinstance(db, AsyncSession)


async def _exec(db, stmt):
    """Execute a SQLAlchemy statement on either AsyncSession or sync Session."""
    if _is_async(db):
        return await db.execute(stmt)
    return await anyio.to_thread.run_sync(db.execute, stmt)


async def _commit(db):
    """Commit transaction."""
    if _is_async(db):
        await db.commit()
    else:
        await anyio.to_thread.run_sync(db.commit)


async def _rollback(db):
    """Rollback transaction."""
    if _is_async(db):
        await db.rollback()
    else:
        await anyio.to_thread.run_sync(db.rollback)


class StatusLock:
    """
    Voice-aware locking using atomic status transitions.

    Each (track, voice) combination is independently lockable.
    Lock indicated by: status IN ('generating', 'segmenting') + processing_voice
    """

    LOCK_TIMEOUT_MINUTES = 90
    CLEANUP_INTERVAL_MINUTES = 30

    def __init__(self):
        self._cleanup_task: Optional[asyncio.Task] = None

    async def try_lock_voice(
        self,
        track_id: str,
        voice_id: Optional[str],
        process_type: str,
        db: Session
    ) -> Tuple[bool, str]:
        """
        Atomically acquire lock for specific voice using status transition.

        Args:
            track_id: Track ID
            voice_id: Voice being processed (None for non-TTS tracks)
            process_type: 'voice_regeneration', 'initial', 'tts_generation', etc.
            db: Database session (sync or async)

        Returns:
            (success: bool, reason: str)
        """
        try:
            from models import VoiceGenerationStatus
            from sqlalchemy import insert
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            # First check for stale lock and take it over if needed
            res = await _exec(db, select(Track).where(Track.id == track_id))
            track = res.scalar_one_or_none()
            if not track:
                return False, "Track not found"

            now = datetime.now(timezone.utc)

            # VOICE-SPECIFIC LOCK: Check VoiceGenerationStatus table
            if voice_id is not None:
                # Check if this specific voice is already being processed
                res = await _exec(
                    db,
                    select(VoiceGenerationStatus).where(
                        and_(
                            VoiceGenerationStatus.track_id == track_id,
                            VoiceGenerationStatus.voice_id == voice_id
                        )
                    )
                )
                existing = res.scalar_one_or_none()

                if existing and existing.status == 'generating':
                    # Check if stale
                    age = now - existing.started_at
                    if age.total_seconds() >= (self.LOCK_TIMEOUT_MINUTES * 60):
                        logger.info(f"Taking over stale voice lock for {track_id}/{voice_id} (age: {age})")
                        # Will update below
                    else:
                        # Voice is actively being processed
                        return False, f"Voice {voice_id} already generating (age: {age})"

                # Upsert into VoiceGenerationStatus
                stmt = pg_insert(VoiceGenerationStatus).values(
                    track_id=track_id,
                    voice_id=voice_id,
                    status='generating',
                    started_at=now,
                    completed_at=None,
                    error_message=None
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=['track_id', 'voice_id'],
                    set_={
                        'status': 'generating',
                        'started_at': now,
                        'completed_at': None,
                        'error_message': None
                    }
                )
                await _exec(db, stmt)
                await _commit(db)

                logger.info(f"✅ LOCKED track {track_id} (voice: {voice_id}) for {process_type}")
                return True, "locked"

            # FULL TRACK LOCK: Traditional exclusive lock for regular audio
            # Check if lock is stale (older than 90 minutes)
            if track.processing_locked_at:
                age = now - track.processing_locked_at
                if age.total_seconds() >= (self.LOCK_TIMEOUT_MINUTES * 60):
                    logger.info(f"Taking over stale lock for track {track_id} (age: {age})")
                    # Will proceed to acquire lock below

            where_conditions = and_(
                Track.id == track_id,
                or_(
                    # Track is free
                    Track.processing_voice == None,

                    # Track completed/failed
                    Track.status.in_(['complete', 'failed']),

                    # Lock is stale
                    Track.processing_locked_at < (now - timedelta(minutes=self.LOCK_TIMEOUT_MINUTES))
                )
            )

            # Atomic UPDATE
            result = await _exec(
                db,
                update(Track)
                .where(where_conditions)
                .values(
                    status='generating',
                    processing_voice=voice_id,
                    processing_locked_at=now,
                    processing_type=process_type,
                    hls_ready=False
                )
            )
            await _commit(db)

            if result.rowcount == 1:
                voice_info = f" (voice: {voice_id})" if voice_id else " (full track)"
                logger.info(f"✅ LOCKED track {track_id}{voice_info} for {process_type}")
                return True, "locked"

            # Lock failed - get current state for detailed reason
            res = await _exec(db, select(Track).where(Track.id == track_id))
            track = res.scalar_one_or_none()

            if not track:
                return False, "Track not found"

            if track.processing_voice == voice_id:
                age = now - track.processing_locked_at if track.processing_locked_at else timedelta(0)
                return False, f"Voice {voice_id} already processing (status: {track.status}, age: {age})"
            elif track.processing_voice:
                return False, f"Track locked by {track.processing_type} (voice: {track.processing_voice})"
            else:
                return False, f"Track locked by {track.processing_type}"

        except Exception as e:
            logger.error(f"Error locking track {track_id}: {e}", exc_info=True)
            try:
                await _rollback(db)
            except Exception:
                pass
            return False, f"Database error: {str(e)}"

    async def unlock_voice(
        self,
        track_id: str,
        voice_id: Optional[str],
        success: bool,
        db: Session
    ):
        """
        Release lock by marking track complete or failed.
        Delegates to TrackStatusManager for status updates.
        Validates HLS segments exist before marking as complete.

        Args:
            track_id: Track ID
            voice_id: Voice that was processed (for HLS validation)
            success: Whether processing succeeded
            db: Database session
        """
        try:
            from models import VoiceGenerationStatus

            res = await _exec(db, select(Track).where(Track.id == track_id))
            track = res.scalar_one_or_none()
            if not track:
                logger.warning(f"Cannot unlock - track {track_id} not found")
                return

            voice_info = f" (voice: {voice_id})" if voice_id else ""

            if voice_id is not None:
                # VOICE-SPECIFIC UNLOCK: Update VoiceGenerationStatus table
                # Each voice processes independently, track status remains unchanged
                # Validate HLS segments exist
                hls_valid = False
                if success:
                    hls_valid = await self._validate_hls_segments(track_id, voice_id)

                # Update VoiceGenerationStatus
                now = datetime.now(timezone.utc)
                if success and hls_valid:
                    await _exec(
                        db,
                        update(VoiceGenerationStatus)
                        .where(
                            and_(
                                VoiceGenerationStatus.track_id == track_id,
                                VoiceGenerationStatus.voice_id == voice_id
                            )
                        )
                        .values(
                            status='complete',
                            completed_at=now,
                            error_message=None
                        )
                    )
                    logger.info(f"🔓 UNLOCKED track {track_id}{voice_info} - SUCCESS")
                else:
                    error_msg = "HLS validation failed" if success and not hls_valid else "Processing failed"
                    await _exec(
                        db,
                        update(VoiceGenerationStatus)
                        .where(
                            and_(
                                VoiceGenerationStatus.track_id == track_id,
                                VoiceGenerationStatus.voice_id == voice_id
                            )
                        )
                        .values(
                            status='failed',
                            completed_at=now,
                            error_message=error_msg
                        )
                    )
                    logger.warning(f"🔓 UNLOCKED track {track_id}{voice_info} - FAILED ({error_msg})")

                await _commit(db)

            else:
                # FULL TRACK UNLOCK: Update track status
                # Validate HLS segments if caller claims success
                if success:
                    hls_valid = await self._validate_hls_segments(track_id, voice_id)
                    if not hls_valid:
                        logger.error(f"Track {track_id} marked success but HLS validation failed")
                        success = False

                if success:
                    await TrackStatusManager.mark_complete(track, db)
                    logger.info(f"🔓 UNLOCKED track {track_id}{voice_info} - SUCCESS")
                else:
                    await TrackStatusManager.mark_failed(
                        track, db,
                        Exception("Processing failed or HLS validation failed"),
                        'voice_generation'
                    )
                    logger.info(f"🔓 UNLOCKED track {track_id}{voice_info} - FAILED")

        except Exception as e:
            logger.error(f"Error unlocking track {track_id}: {e}", exc_info=True)
            try:
                await _rollback(db)
            except Exception:
                pass

    async def is_voice_locked(
        self,
        track_id: str,
        voice_id: Optional[str],
        db: Session
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if specific voice is currently locked.

        Returns:
            (is_locked: bool, processing_type: Optional[str])
        """
        try:
            from models import VoiceGenerationStatus

            if voice_id is not None:
                # VOICE-SPECIFIC: Check VoiceGenerationStatus table
                res = await _exec(
                    db,
                    select(VoiceGenerationStatus).where(
                        and_(
                            VoiceGenerationStatus.track_id == track_id,
                            VoiceGenerationStatus.voice_id == voice_id
                        )
                    )
                )
                voice_status = res.scalar_one_or_none()

                if voice_status and voice_status.status == 'generating':
                    # Check if stale
                    now = datetime.now(timezone.utc)
                    age = now - voice_status.started_at
                    if age.total_seconds() >= (self.LOCK_TIMEOUT_MINUTES * 60):
                        return False, "stale"
                    return True, "voice_generation"

                return False, None

            # FULL TRACK: Check track status
            res = await _exec(db, select(Track).where(Track.id == track_id))
            track = res.scalar_one_or_none()
            if not track:
                return False, None

            # Check if lock is stale
            if track.processing_locked_at:
                now = datetime.now(timezone.utc)
                age = now - track.processing_locked_at
                if age.total_seconds() >= (self.LOCK_TIMEOUT_MINUTES * 60):
                    return False, "stale"

            # Check if track is locked (full track processing)
            if (track.status in ['generating', 'segmenting']
                and track.processing_voice is None):
                return True, track.processing_type

            return False, None

        except Exception as e:
            logger.error(f"Error checking lock for {track_id}: {e}", exc_info=True)
            return False, None

    async def _validate_hls_segments(self, track_id: str, voice_id: Optional[str]) -> bool:
        """
        Enhanced validation with completeness checks.
        Returns True if segments are valid and complete, False otherwise.

        Enhancements:
        - 2-second delay for filesystem sync
        - Checks for #EXT-X-ENDLIST tag (confirms complete generation)
        - Verifies segment count matches playlist entries
        - Detailed logging for failures
        """
        try:
            # Add 2-second delay for filesystem sync
            await asyncio.sleep(2)

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
                logger.warning(f"Validation failed: master.m3u8 missing for {track_id}/{voice_id}")
                return False

            # Check variant directory and playlist
            variant_playlist = variant_dir / "playlist.m3u8"
            if not variant_dir.exists() or not variant_playlist.exists():
                logger.warning(f"Validation failed: variant playlist missing for {track_id}/{voice_id}")
                return False

            # NEW: Check playlist completeness (#EXT-X-ENDLIST tag)
            try:
                # Read playlist content
                if _is_async(variant_playlist):
                    # If Path object supports async (unlikely), handle it
                    playlist_content = variant_playlist.read_text()
                else:
                    # Sync read is fine for small playlist files
                    playlist_content = variant_playlist.read_text()

                if "#EXT-X-ENDLIST" not in playlist_content:
                    logger.warning(f"Validation failed: playlist incomplete (no ENDLIST) for {track_id}/{voice_id}")
                    return False

                # NEW: Count expected segments from playlist
                import re
                extinf_pattern = re.compile(r'#EXTINF:')
                expected_segments = len(extinf_pattern.findall(playlist_content))

                # Count actual segment files
                segment_files = list(variant_dir.glob("segment_*.ts"))
                actual_segments = len(segment_files)

                if actual_segments < expected_segments:
                    logger.warning(f"Validation failed: segment count mismatch for {track_id}/{voice_id}. Expected: {expected_segments}, Found: {actual_segments}")
                    return False

                if actual_segments == 0:
                    logger.warning(f"Validation failed: no segments found for {track_id}/{voice_id}")
                    return False

                logger.info(f"✅ HLS validation passed for {track_id}/{voice_id}: {actual_segments} segments, playlist complete")
                return True

            except Exception as read_err:
                logger.error(f"Error reading playlist for {track_id}/{voice_id}: {read_err}")
                return False

        except Exception as e:
            logger.error(f"Validation error for {track_id}/{voice_id}: {e}", exc_info=True)
            return False

    async def clear_all_on_startup(self, db: Session) -> int:
        """
        Clear all processing tracks at startup (server restarted).
        Mark interrupted tracks as failed so users can retry.
        Also cleans up interrupted voice-specific processing.

        PRESERVES LOGIC FROM simple_track_lock.clear_all_locks_on_startup()
        """
        try:
            cleaned_count = 0

            # 0. Clean up interrupted voice generations in database
            from models import VoiceGenerationStatus

            try:
                # Mark all generating voices as failed on startup
                now = datetime.now(timezone.utc)
                result = await _exec(
                    db,
                    update(VoiceGenerationStatus)
                    .where(VoiceGenerationStatus.status == 'generating')
                    .values(
                        status='failed',
                        completed_at=now,
                        error_message='Server restarted during generation'
                    )
                )
                voice_gen_cleaned = result.rowcount if hasattr(result, 'rowcount') else 0
                if voice_gen_cleaned > 0:
                    logger.info(f"✅ Marked {voice_gen_cleaned} interrupted voice generations as failed")
            except Exception as e:
                logger.error(f"Error cleaning interrupted voice generations: {e}", exc_info=True)
                voice_gen_cleaned = 0

            # 1. Clean up full-track locks (regular audio)
            res = await _exec(
                db,
                select(Track).where(Track.status.in_(['generating', 'segmenting']))
            )
            processing_tracks = res.scalars().all() or []

            for track in processing_tracks:
                # Check if track is actually complete despite processing status
                is_complete = (
                    track.hls_ready and
                    track.segmentation_status == 'complete'
                )

                if is_complete:
                    # Track completed but status wasn't updated - mark complete
                    logger.info(f"Track {track.id} is complete, marking as complete")
                    await TrackStatusManager.mark_complete(track, db)
                else:
                    # Track was interrupted during processing - mark as failed
                    logger.info(f"Marking track {track.id} as failed (server restart)")
                    await TrackStatusManager.mark_failed(
                        track,
                        db,
                        error=Exception("Server restarted during processing"),
                        pipeline="server_restart"
                    )
                cleaned_count += 1

            # 2. Clean up voice-specific processing (voice-isolated locks)
            # Voice locks don't update track.status, so check HLS directories on disk
            voice_cleaned = await self._cleanup_interrupted_voices(db)
            cleaned_count += voice_cleaned

            await _commit(db)

            if cleaned_count == 0:
                logger.info("✅ No interrupted processing found on startup")
            else:
                logger.info(f"✅ Cleaned up {cleaned_count} interrupted operations on startup")

            return cleaned_count

        except Exception as e:
            logger.error(f"Error during startup cleanup: {e}", exc_info=True)
            try:
                await _rollback(db)
            except Exception:
                pass
            return 0

    async def _cleanup_interrupted_voices(self, db: Session) -> int:
        """
        Clean up voice-specific processing interrupted by server restart.
        Checks HLS directories for incomplete voice processing.
        """
        try:
            from hls_streaming import stream_manager
            import os

            cleaned = 0
            segment_dir = stream_manager.segment_dir

            # Iterate through all track directories
            if not os.path.exists(segment_dir):
                return 0

            for track_id in os.listdir(segment_dir):
                track_path = segment_dir / track_id
                if not os.path.isdir(track_path):
                    continue

                # Check for voice subdirectories
                for item in os.listdir(track_path):
                    if not item.startswith('voice-'):
                        continue

                    voice_id = item.replace('voice-', '')
                    voice_path = track_path / item

                    # Check if voice HLS is incomplete
                    master_playlist = voice_path / "master.m3u8"
                    if not os.path.exists(master_playlist):
                        # Incomplete voice processing - was interrupted
                        logger.info(f"Cleaning up interrupted voice processing: {track_id}/{voice_id}")

                        # Remove incomplete HLS directory
                        import shutil
                        try:
                            shutil.rmtree(voice_path)
                            logger.info(f"Removed incomplete voice HLS: {track_id}/{voice_id}")
                            cleaned += 1
                        except Exception as rm_err:
                            logger.warning(f"Could not remove incomplete voice HLS: {rm_err}")

            return cleaned

        except Exception as e:
            logger.error(f"Error cleaning up interrupted voices: {e}", exc_info=True)
            return 0

    async def cleanup_stale_locks(self, db: Session) -> int:
        """
        Clear locks older than LOCK_TIMEOUT_MINUTES (90 minutes).
        Called periodically and on demand.

        PRESERVES LOGIC FROM simple_track_lock.cleanup_stale_locks_on_startup()
        """
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.LOCK_TIMEOUT_MINUTES)
            res = await _exec(
                db,
                select(Track)
                .where(Track.processing_locked_at < cutoff)
                .where(Track.status.in_(['generating', 'segmenting']))
            )
            stale = res.scalars().all() or []

            if not stale:
                return 0

            for track in stale:
                age = datetime.now(timezone.utc) - track.processing_locked_at
                logger.info(f"Clearing stale lock for track {track.id} (age: {age})")
                await TrackStatusManager.mark_failed(
                    track,
                    db,
                    error=Exception(f"Lock timeout - age: {age}"),
                    pipeline="stale_lock_cleanup"
                )

            await _commit(db)
            logger.warning(f"🧹 Cleaned up {len(stale)} stale locks")
            return len(stale)

        except Exception as e:
            logger.error(f"Error during stale lock cleanup: {e}", exc_info=True)
            try:
                await _rollback(db)
            except Exception:
                pass
            return 0

    async def start_periodic_cleanup(self):
        """
        Start the periodic cleanup task (runs every 30 minutes).

        PRESERVES LOGIC FROM simple_track_lock.start_periodic_cleanup()
        """
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
            logger.info(f"🔄 Started periodic lock cleanup (interval: {self.CLEANUP_INTERVAL_MINUTES} min)")

    async def stop_periodic_cleanup(self):
        """
        Stop the periodic cleanup task.

        PRESERVES LOGIC FROM simple_track_lock.stop_periodic_cleanup()
        """
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            logger.info("🛑 Stopped periodic lock cleanup")

    async def _periodic_cleanup(self):
        """
        Background task that periodically cleans up stale locks.

        PRESERVES LOGIC FROM simple_track_lock._periodic_cleanup()
        """
        while True:
            try:
                await asyncio.sleep(self.CLEANUP_INTERVAL_MINUTES * 60)
                logger.debug("🔍 Running periodic stale lock cleanup...")

                # Use a fresh DB session for each cleanup
                from database import get_db
                db = next(get_db())
                try:
                    cleared = await self.cleanup_stale_locks(db)
                    if cleared > 0:
                        logger.info(f"🧹 Periodic cleanup cleared {cleared} stale locks")
                finally:
                    db.close()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in periodic lock cleanup: {e}", exc_info=True)
                # Continue the loop even if cleanup fails


# Global singleton instance
status_lock = StatusLock()
