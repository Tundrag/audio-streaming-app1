# core/download_cleanup_service.py
import logging
import threading
import time
import asyncio
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import select, delete, and_, text
from sqlalchemy.orm import joinedload
from models import UserDownload, User
from database import get_db

logger = logging.getLogger(__name__)

# Define the downloads directory
DOWNLOADS_DIR = Path("/tmp/user_downloads")
DOWNLOADS_DIR.mkdir(exist_ok=True, parents=True)

class DownloadCleanupService:
    """Service to clean up expired user downloads and enforce download limits"""
    
    def __init__(self, get_db_func=None, cleanup_interval=3600, max_downloads_per_user=10):
        self.get_db_func = get_db_func  # The function to get a database session
        self.cleanup_interval = cleanup_interval
        self.max_downloads_per_user = max_downloads_per_user
        self._is_running = False
        self._thread = None
        self.downloads_dir = DOWNLOADS_DIR
        
    def start(self):
        """Start the cleanup service"""
        if self._is_running or not self.get_db_func:
            if not self.get_db_func:
                logger.error("Cannot start cleanup service: No database function provided")
            return
            
        self._is_running = True
        self._thread = threading.Thread(target=self._cleanup_loop)
        self._thread.daemon = True  # Allow the thread to exit when the main program exits
        self._thread.start()
        logger.info(f"Download cleanup service started. Monitoring directory: {self.downloads_dir}")
        
    def stop(self):
        """Stop the cleanup service"""
        if not self._is_running:
            return
            
        self._is_running = False
        if self._thread and self._thread.is_alive():
            # The thread will terminate on its next loop
            self._thread.join(timeout=10)  # Wait up to 10 seconds for the thread to finish
                
        logger.info("Download cleanup service stopped")
        
    def _cleanup_loop(self):
        """Periodic cleanup loop"""
        while self._is_running:
            try:
                # First clean up expired downloads that are in the database
                self._cleanup_expired_downloads()
                
                # Then clean up any stray files that don't have database records
                self._cleanup_stray_files()
                
                logger.info("Completed download cleanup cycle")
            except Exception as e:
                logger.error(f"Error in download cleanup: {str(e)}", exc_info=True)
                
            # Sleep for the cleanup interval
            time.sleep(self.cleanup_interval)
            
    def _cleanup_expired_downloads(self):
        """Find and cleanup expired downloads using raw SQL to avoid enum issues"""
        now = datetime.now(timezone.utc)
        logger.info(f"Starting cleanup of expired downloads at {now}")
        
        # Check if we have a valid db getter function
        if not self.get_db_func:
            logger.error("No database function available for cleanup")
            return
            
        # Get a database session
        try:
            db = next(self.get_db_func())
            
            # Find all expired downloads using raw SQL (avoids enum issues)
            query = text("""
                SELECT id, user_id, download_path, original_filename
                FROM user_downloads 
                WHERE expires_at <= :now 
                AND is_available = true
            """)
            
            result = db.execute(query, {"now": now})
            expired_downloads = result.fetchall()
            
            if not expired_downloads:
                logger.info("No expired downloads found")
                return
                
            logger.info(f"Found {len(expired_downloads)} expired downloads to clean up")
            
            for download in expired_downloads:
                try:
                    # Delete the physical file
                    file_path = Path(download.download_path)
                    if file_path.exists():
                        file_path.unlink()
                        logger.info(f"Deleted file: {file_path}")
                    
                    # Mark download as unavailable using raw SQL
                    update_query = text("""
                        UPDATE user_downloads 
                        SET is_available = false 
                        WHERE id = :download_id
                    """)
                    db.execute(update_query, {"download_id": download.id})
                    
                    logger.info(f"Marked download {download.id} as unavailable for user {download.user_id}")
                except Exception as e:
                    logger.error(f"Error cleaning up download {download.id}: {str(e)}", exc_info=True)
            
            # Commit changes
            db.commit()
            logger.info(f"Completed cleanup of {len(expired_downloads)} expired downloads")
            
        except Exception as e:
            logger.error(f"Database error during cleanup: {str(e)}")
    
    def _cleanup_stray_files(self):
        """Find and delete files in the downloads directory that don't have corresponding database records"""
        logger.info("Starting cleanup of stray download files")
        
        if not self.get_db_func:
            logger.error("No database function available for stray file cleanup")
            return
        
        try:
            db = next(self.get_db_func())
            
            # Get all files in the downloads directory
            if not self.downloads_dir.exists():
                logger.warning(f"Downloads directory does not exist: {self.downloads_dir}")
                return
            
            all_files = [f for f in self.downloads_dir.glob('*') if f.is_file()]
            if not all_files:
                logger.info("No files found in downloads directory")
                return
            
            logger.info(f"Found {len(all_files)} files in downloads directory")
            
            # Get all valid download paths from the database
            query = text("""
                SELECT download_path 
                FROM user_downloads 
                WHERE is_available = true
            """)
            result = db.execute(query)
            valid_paths = {Path(row[0]) for row in result.fetchall()}
            
            # Find files that don't have database records
            stray_files = [f for f in all_files if f not in valid_paths]
            
            if not stray_files:
                logger.info("No stray files found")
                return
            
            logger.info(f"Found {len(stray_files)} stray files to delete")
            
            # Delete stray files
            for file_path in stray_files:
                try:
                    file_path.unlink()
                    logger.info(f"Deleted stray file: {file_path}")
                except Exception as e:
                    logger.error(f"Error deleting stray file {file_path}: {str(e)}", exc_info=True)
            
            logger.info(f"Completed cleanup of {len(stray_files)} stray files")
            
        except Exception as e:
            logger.error(f"Error during stray file cleanup: {str(e)}", exc_info=True)

    def enforce_download_limit(self, db: Session, user_id: int, limit: int = None):
        """
        Check and enforce download limits for a user.
        Removes oldest downloads if the limit is exceeded.
        
        Args:
            db: Database session
            user_id: User ID to check limits for
            limit: Override for the default maximum number of downloads allowed
            
        Returns:
            bool: True if limit was enforced and old downloads were removed
        """
        # Use provided limit or class default
        max_downloads = limit if limit is not None else self.max_downloads_per_user
        
        # Check how many downloads this user already has
        from sqlalchemy import text
        count_query = text("""
            SELECT COUNT(id) 
            FROM user_downloads 
            WHERE user_id = :user_id 
            AND is_available = true
        """)
        count_result = db.execute(count_query, {"user_id": user_id})
        current_count = count_result.scalar() or 0
        
        # If user has reached or exceeded limit, remove oldest ones
        if current_count >= max_downloads:
            # Get oldest downloads that exceed our limit
            oldest_query = text("""
                SELECT id, download_path
                FROM user_downloads
                WHERE user_id = :user_id
                AND is_available = true
                ORDER BY downloaded_at ASC
                LIMIT :excess_count
            """)
            excess_count = current_count - (max_downloads - 1)  # Keep only (max_downloads-1) to make room for new one
            oldest_result = db.execute(oldest_query, {
                "user_id": user_id, 
                "excess_count": excess_count
            })
            oldest_downloads = oldest_result.fetchall()
            
            # Delete the oldest downloads
            for old_download in oldest_downloads:
                # Delete the file if it exists
                old_file_path = old_download.download_path
                import os
                if os.path.exists(old_file_path):
                    try:
                        os.remove(old_file_path)
                        logger.info(f"Deleted old download file: {old_file_path}")
                    except OSError as e:
                        logger.error(f"Error deleting old download file {old_file_path}: {e}")
                
                # Mark as unavailable in database
                delete_query = text("""
                    UPDATE user_downloads
                    SET is_available = false
                    WHERE id = :download_id
                """)
                db.execute(delete_query, {"download_id": old_download.id})
            
            # Commit the deletions
            db.commit()
            logger.info(f"Removed {len(oldest_downloads)} old downloads for user {user_id} (limit: {max_downloads})")
            return True
        
        return False

    def cleanup_on_demand(self, user_id=None):
        """Run cleanup immediately, optionally for a specific user"""
        logger.info(f"Running on-demand cleanup for user_id={user_id}")
        self._cleanup_expired_downloads()
        self._cleanup_stray_files()  # Also clean up stray files on demand


class DownloadRecoveryService:
    """
    Service to handle recovery of downloads and credits after system restarts.
    Works with both track and album downloads, and integrates with existing cleanup service.
    Designed to work with existing core/download_cleanup_service.py without breaking anything.
    """
    
    def __init__(self):
        # Recovery thresholds
        self.credit_timeout_minutes = 35  # Release credits after 35 min (longer than worker timeout)
        self.max_recovery_batch = 100     # Process max 100 items per batch to avoid overload
        
    async def full_recovery_on_startup(self) -> Dict[str, int]:
        """
        Perform complete recovery process on system startup.
        Returns statistics about what was recovered.
        """
        logger.info("🔄 Starting download recovery process after system restart...")
        
        recovery_stats = {
            'orphaned_reservations_released': 0,
            'temp_files_cleaned': 0,
            'errors': 0
        }
        
        try:
            # Step 1: Handle orphaned download reservations (both track and album)
            logger.info("📊 Step 1: Recovering orphaned download reservations...")
            reservations_recovered = await self._recover_orphaned_credits()
            recovery_stats['orphaned_reservations_released'] = reservations_recovered
            
            # Step 2: Clean up abandoned temporary files (both track and album temp dirs)
            logger.info("🧹 Step 2: Cleaning up abandoned temporary files...")
            temp_files_cleaned = await self._cleanup_abandoned_temp_files()
            recovery_stats['temp_files_cleaned'] = temp_files_cleaned
            
            # Step 3: Trigger existing cleanup service for user downloads
            logger.info("📁 Step 3: Triggering existing core/download_cleanup_service...")
            await self._trigger_existing_cleanup()
            
            # REMOVED: Step 4 user notifications (as requested)
            
            logger.info(
                f"✅ Recovery completed successfully!\n"
                f"  💰 Reservations released: {recovery_stats['orphaned_reservations_released']}\n"
                f"  🗑️ Temp files cleaned: {recovery_stats['temp_files_cleaned']}"
            )
            
        except Exception as e:
            logger.error(f"❌ Error during recovery process: {str(e)}", exc_info=True)
            recovery_stats['errors'] = 1
            
        return recovery_stats
    
    async def _recover_orphaned_credits(self) -> int:
        """
        Find and release download reservations that were never completed due to system restart.
        Handles both track and album download reservations.
        """
        try:
            from database import get_db
            from sqlalchemy import text
            
            db = next(get_db())
            reservations_released = 0
            
            try:
                # Check if download_reservations table exists first
                check_table_query = text("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_schema = 'public' 
                        AND table_name = 'download_reservations'
                    )
                """)
                
                result = db.execute(check_table_query)
                table_exists = result.scalar()
                
                if not table_exists:
                    logger.info("📊 Download reservations table doesn't exist yet - skipping reservation recovery")
                    return 0
                
                # Find reservations that are older than our timeout and still pending
                cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=self.credit_timeout_minutes)
                
                # Query for orphaned reservations using correct table and column names
                query = text("""
                    SELECT id, user_id, download_id, download_type, reserved_at, expires_at
                    FROM download_reservations 
                    WHERE status = 'reserved' 
                    AND reserved_at < :cutoff_time
                    ORDER BY reserved_at ASC
                    LIMIT :batch_limit
                """)
                
                result = db.execute(query, {
                    "cutoff_time": cutoff_time,
                    "batch_limit": self.max_recovery_batch
                })
                orphaned_reservations = result.fetchall()
                
                logger.info(f"Found {len(orphaned_reservations)} orphaned download reservations")
                
                # Release each orphaned reservation
                for reservation in orphaned_reservations:
                    try:
                        # Update the reservation status to 'failed' due to system restart
                        update_query = text("""
                            UPDATE download_reservations 
                            SET status = 'failed'
                            WHERE id = :reservation_id
                        """)
                        db.execute(update_query, {
                            "reservation_id": reservation.id
                        })
                        
                        reservations_released += 1
                        age = datetime.now(timezone.utc) - reservation.reserved_at
                        logger.info(
                            f"💰 Released orphaned {reservation.download_type} reservation: {reservation.download_id} "
                            f"(user: {reservation.user_id}, age: {age})"
                        )
                            
                    except Exception as e:
                        logger.error(f"❌ Error releasing reservation {reservation.id}: {str(e)}")
                
                db.commit()
                
            finally:
                db.close()
                
            return reservations_released
            
        except Exception as e:
            logger.error(f"❌ Error during download reservation recovery: {str(e)}", exc_info=True)
            return 0
    
    async def _cleanup_abandoned_temp_files(self) -> int:
        """
        Clean up temporary download files that were abandoned due to system restart.
        Handles both track and album temporary directories.
        """
        files_cleaned = 0
        
        # Define temp directories for both track and album downloads
        # Using the same paths as in your lifespan context manager
        temp_directories = [
            Path("/tmp/mega_downloads/tracks"),  # Track downloads
            Path("/tmp/mega_downloads"),         # Album downloads
            Path("/tmp/mega_upload"),            # Upload temp files
            Path("/tmp/mega_stream"),            # Stream temp files
        ]
        
        try:
            cutoff_time = time.time() - (2 * 3600)  # 2 hours old
            
            for temp_dir in temp_directories:
                if not temp_dir.exists():
                    logger.info(f"Temp directory {temp_dir} doesn't exist, skipping")
                    continue
                
                logger.info(f"Cleaning abandoned files in {temp_dir}")
                
                for item in temp_dir.iterdir():
                    try:
                        if item.is_file():
                            # Check if file is older than cutoff
                            file_mtime = item.stat().st_mtime
                            if file_mtime < cutoff_time:
                                file_age_hours = (time.time() - file_mtime) / 3600
                                file_size = item.stat().st_size
                                
                                item.unlink()
                                files_cleaned += 1
                                
                                logger.info(
                                    f"🗑️ Cleaned abandoned temp file: {item.name} "
                                    f"(age: {file_age_hours:.1f}h, size: {file_size:,} bytes)"
                                )
                        
                        elif item.is_dir():
                            # Clean abandoned directories (album download dirs)
                            dir_mtime = item.stat().st_mtime
                            if dir_mtime < cutoff_time:
                                dir_age_hours = (time.time() - dir_mtime) / 3600
                                
                                # Remove all files in the directory first
                                for subitem in item.rglob('*'):
                                    if subitem.is_file():
                                        subitem.unlink()
                                        files_cleaned += 1
                                
                                # Remove empty directories
                                for subdir in sorted(item.rglob('*'), key=lambda p: len(p.parts), reverse=True):
                                    if subdir.is_dir() and not any(subdir.iterdir()):
                                        subdir.rmdir()
                                
                                # Remove main directory if empty
                                if not any(item.iterdir()):
                                    item.rmdir()
                                    logger.info(
                                        f"🗑️ Cleaned abandoned temp directory: {item.name} "
                                        f"(age: {dir_age_hours:.1f}h)"
                                    )
                                
                    except Exception as e:
                        logger.error(f"❌ Error cleaning temp item {item}: {str(e)}")
            
            # Clean up empty parent directories
            for temp_dir in temp_directories:
                try:
                    if temp_dir.exists() and not any(temp_dir.iterdir()):
                        logger.info(f"Removing empty temp directory: {temp_dir}")
                except Exception as e:
                    logger.error(f"Error checking temp directory: {str(e)}")
                    
        except Exception as e:
            logger.error(f"❌ Error during temp file cleanup: {str(e)}", exc_info=True)
        
        return files_cleaned
    
    async def _trigger_existing_cleanup(self):
        """
        Trigger the existing core/download_cleanup_service to handle user downloads cleanup.
        This keeps your existing functionality intact and working.
        """
        try:
            # Get the global download_cleanup_service instance
            global download_cleanup_service
            
            # Check if the service is available and properly initialized
            if not hasattr(download_cleanup_service, 'cleanup_on_demand'):
                logger.warning("⚠️ download_cleanup_service.cleanup_on_demand not available - skipping")
                return
            
            # Check if it has a database function
            if not download_cleanup_service.get_db_func:
                logger.warning("⚠️ download_cleanup_service database function not set - skipping")
                return
            
            # Run on-demand cleanup (this is synchronous as per your implementation)
            await asyncio.get_event_loop().run_in_executor(
                None, 
                download_cleanup_service.cleanup_on_demand
            )
            
            logger.info("✅ Triggered existing core/download_cleanup_service for user downloads")
            
        except Exception as e:
            logger.error(f"❌ Error triggering existing cleanup service: {str(e)}", exc_info=True)
    
    async def schedule_periodic_cleanup(self, interval_hours: int = 6):
        """
        Schedule periodic cleanup to run every few hours.
        This helps catch any issues that occur during normal operation.
        """
        logger.info(f"🔄 Scheduling periodic cleanup every {interval_hours} hours")
        
        while True:
            try:
                await asyncio.sleep(interval_hours * 3600)  # Convert hours to seconds
                
                logger.info("⏰ Running scheduled cleanup...")
                stats = await self.full_recovery_on_startup()
                
                if any(stats.values()):
                    logger.info(f"🧹 Scheduled cleanup completed: {stats}")
                else:
                    logger.debug("✅ Scheduled cleanup completed - nothing to clean")
                    
            except asyncio.CancelledError:
                logger.info("🛑 Periodic cleanup task cancelled")
                break
            except Exception as e:
                logger.error(f"❌ Error in periodic cleanup: {str(e)}", exc_info=True)
                # Continue running despite errors
    
    def get_recovery_status(self) -> Dict[str, any]:
        """
        Get current recovery service status and configuration.
        """
        # Check existence of temp directories (same as in your lifespan)
        temp_directories = {
            'track_downloads': {
                'path': '/tmp/mega_downloads/tracks',
                'exists': Path('/tmp/mega_downloads/tracks').exists()
            },
            'album_downloads': {
                'path': '/tmp/mega_downloads',
                'exists': Path('/tmp/mega_downloads').exists()
            },
            'mega_upload': {
                'path': '/tmp/mega_upload',
                'exists': Path('/tmp/mega_upload').exists()
            },
            'mega_stream': {
                'path': '/tmp/mega_stream',
                'exists': Path('/tmp/mega_stream').exists()
            },
            'user_downloads': {
                'path': '/tmp/user_downloads',
                'exists': Path('/tmp/user_downloads').exists()
            }
        }
        
        return {
            'credit_timeout_minutes': self.credit_timeout_minutes,
            'max_recovery_batch': self.max_recovery_batch,
            'temp_directories': temp_directories,
            'cleanup_service_available': self._check_cleanup_service_available()
        }
    
    def _check_cleanup_service_available(self) -> bool:
        """Check if the existing core/download_cleanup_service is available."""
        try:
            global download_cleanup_service
            return hasattr(download_cleanup_service, 'cleanup_on_demand')
        except:
            return False


# Create global instances
download_cleanup_service = DownloadCleanupService()  # DB function will be set in app.py
download_recovery_service = DownloadRecoveryService()