"""
Scheduled Visibility Worker

Background worker that processes scheduled visibility changes for albums and tracks.
Runs periodically to check for items whose scheduled time has elapsed and updates
their visibility status automatically.
"""

import logging
import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any
from sqlalchemy import and_
from sqlalchemy.orm import Session
from database import SessionLocal
from models import Album, Track, User

logger = logging.getLogger(__name__)

# Worker configuration
CHECK_INTERVAL_SECONDS = 60  # Check every 1 minute
BATCH_SIZE = 100  # Process up to 100 items at a time


class VisibilitySchedulerWorker:
    """Worker that processes scheduled visibility changes"""

    def __init__(self):
        self.is_running = False
        self._task = None

    async def start(self):
        """Start the background worker"""
        if self.is_running:
            logger.warning("Visibility scheduler worker is already running")
            return

        self.is_running = True
        logger.info("🕐 Starting visibility scheduler worker...")

        # Process any missed schedules on startup
        await self.process_missed_schedules()

        # Start periodic task
        self._task = asyncio.create_task(self._run_periodic())

    async def stop(self):
        """Stop the background worker"""
        self.is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Visibility scheduler worker stopped")

    async def _run_periodic(self):
        """Run periodic checks for scheduled visibility changes"""
        while self.is_running:
            try:
                await self.process_scheduled_changes()
                await asyncio.sleep(CHECK_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in visibility scheduler worker: {e}", exc_info=True)
                await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    async def process_missed_schedules(self):
        """Process any schedules that were missed (e.g., during server downtime)"""
        logger.info("Checking for missed scheduled visibility changes...")

        try:
            albums_processed = await self._process_albums()
            tracks_processed = await self._process_tracks()

            total = albums_processed + tracks_processed
            if total > 0:
                logger.warning(f"⚠️ Processed {total} missed schedules ({albums_processed} albums, {tracks_processed} tracks)")
            else:
                logger.info("No missed schedules found")
        except Exception as e:
            logger.error(f"Error processing missed schedules: {e}", exc_info=True)

    async def process_scheduled_changes(self):
        """Process scheduled visibility changes that are due"""
        try:
            albums_processed = await self._process_albums()
            tracks_processed = await self._process_tracks()

            total = albums_processed + tracks_processed
            if total > 0:
                logger.info(f"✅ Processed {total} scheduled visibility changes ({albums_processed} albums, {tracks_processed} tracks)")
        except Exception as e:
            logger.error(f"Error processing scheduled changes: {e}", exc_info=True)

    async def _process_albums(self) -> int:
        """Process scheduled visibility changes for albums"""
        processed_count = 0

        with SessionLocal() as db:
            try:
                now = datetime.now(timezone.utc)

                # Find albums with elapsed schedules
                albums = db.query(Album).filter(
                    and_(
                        Album.scheduled_visibility_change_at <= now,
                        Album.scheduled_visibility_change_at.isnot(None),
                        Album.scheduled_visibility_status.isnot(None)
                    )
                ).limit(BATCH_SIZE).all()

                for album in albums:
                    try:
                        # Store old status for logging
                        old_status = album.visibility_status
                        scheduled_status = album.scheduled_visibility_status
                        scheduled_time = album.scheduled_visibility_change_at

                        # Update visibility status
                        album.visibility_status = scheduled_status

                        # Clear scheduled fields
                        album.scheduled_visibility_change_at = None
                        album.scheduled_visibility_status = None

                        db.commit()

                        # Log the change
                        logger.info(
                            f"📅 Album '{album.title}' (id={album.id}): "
                            f"Changed visibility from '{old_status}' to '{scheduled_status}' "
                            f"(scheduled for {scheduled_time})"
                        )

                        # Create audit log entry
                        try:
                            from models import AuditLog, AuditLogType
                            audit_log = AuditLog(
                                user_id=album.created_by_id,
                                action_type=AuditLogType.UPDATE,
                                table_name="albums",
                                record_id=str(album.id),
                                old_values={"visibility_status": old_status},
                                new_values={"visibility_status": scheduled_status},
                                description=f"Scheduled visibility change: {old_status} → {scheduled_status}",
                                created_at=datetime.now(timezone.utc),
                                updated_at=datetime.now(timezone.utc)
                            )
                            db.add(audit_log)
                            db.commit()
                        except Exception as e:
                            logger.warning(f"Failed to create audit log for album {album.id}: {e}")

                        # TODO: Send notification to creator
                        # await self._send_notification(album.created_by_id, album, old_status, scheduled_status)

                        processed_count += 1

                    except Exception as e:
                        logger.error(f"Failed to process scheduled change for album {album.id}: {e}", exc_info=True)
                        db.rollback()
                        continue

            except Exception as e:
                logger.error(f"Error querying albums for scheduled changes: {e}", exc_info=True)
                db.rollback()

        return processed_count

    async def _process_tracks(self) -> int:
        """Process scheduled visibility changes for tracks"""
        processed_count = 0

        with SessionLocal() as db:
            try:
                now = datetime.now(timezone.utc)

                # Find tracks with elapsed schedules
                tracks = db.query(Track).filter(
                    and_(
                        Track.scheduled_visibility_change_at <= now,
                        Track.scheduled_visibility_change_at.isnot(None),
                        Track.scheduled_visibility_status.isnot(None)
                    )
                ).limit(BATCH_SIZE).all()

                for track in tracks:
                    try:
                        # Store old status for logging
                        old_status = track.visibility_status
                        scheduled_status = track.scheduled_visibility_status
                        scheduled_time = track.scheduled_visibility_change_at

                        # Update visibility status
                        track.visibility_status = scheduled_status

                        # Clear scheduled fields
                        track.scheduled_visibility_change_at = None
                        track.scheduled_visibility_status = None

                        db.commit()

                        # Log the change
                        logger.info(
                            f"📅 Track '{track.title}' (id={track.id}): "
                            f"Changed visibility from '{old_status}' to '{scheduled_status}' "
                            f"(scheduled for {scheduled_time})"
                        )

                        # Create audit log entry
                        try:
                            from models import AuditLog, AuditLogType
                            audit_log = AuditLog(
                                user_id=track.created_by_id,
                                action_type=AuditLogType.UPDATE,
                                table_name="tracks",
                                record_id=track.id,
                                old_values={"visibility_status": old_status},
                                new_values={"visibility_status": scheduled_status},
                                description=f"Scheduled visibility change: {old_status} → {scheduled_status}",
                                created_at=datetime.now(timezone.utc),
                                updated_at=datetime.now(timezone.utc)
                            )
                            db.add(audit_log)
                            db.commit()
                        except Exception as e:
                            logger.warning(f"Failed to create audit log for track {track.id}: {e}")

                        # TODO: Send notification to creator
                        # await self._send_notification(track.created_by_id, track, old_status, scheduled_status)

                        processed_count += 1

                    except Exception as e:
                        logger.error(f"Failed to process scheduled change for track {track.id}: {e}", exc_info=True)
                        db.rollback()
                        continue

            except Exception as e:
                logger.error(f"Error querying tracks for scheduled changes: {e}", exc_info=True)
                db.rollback()

        return processed_count

    async def _send_notification(
        self,
        user_id: int,
        item: Any,
        old_status: str,
        new_status: str
    ):
        """Send notification to user about visibility change"""
        # TODO: Implement notification system
        # This would integrate with your existing notification system
        pass


# Global singleton instance
_worker_instance = None


def get_worker() -> VisibilitySchedulerWorker:
    """Get or create the global worker instance"""
    global _worker_instance
    if _worker_instance is None:
        _worker_instance = VisibilitySchedulerWorker()
    return _worker_instance


async def start_worker():
    """Start the visibility scheduler worker"""
    worker = get_worker()
    await worker.start()


async def stop_worker():
    """Stop the visibility scheduler worker"""
    worker = get_worker()
    await worker.stop()
