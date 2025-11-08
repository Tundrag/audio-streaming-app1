# mega_upload_manager.py - Same interface, S4 implementation

from __future__ import annotations
import asyncio
import logging
from pathlib import Path
from typing import Dict, Optional, Callable, List
from datetime import datetime
from enum import Enum

import psutil
from dataclasses import dataclass
from worker_config import worker_config

# Import Redis upload state manager V2 (using generic RedisStateManager)
# OLD: from redis_state.state.upload_legacy import get_redis_upload_state
from redis_state.state.upload import get_redis_upload_state

logger = logging.getLogger("MegaUploadManager")

# Get Redis state manager (now using generic RedisStateManager for consistency)
redis_upload_state = get_redis_upload_state()

class UploadStatus(Enum):
    QUEUED = "queued"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass(frozen=True)
class UploadStatusInfo:
    file_id: str
    filename: str
    status: str
    timestamp: str
    error: Optional[str] = None
    worker_id: Optional[str] = None

class MegaUploadManager:
    """MEGA Upload Manager - now S4-powered but same interface"""
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            # Same config as before
            min_workers = worker_config.worker_configs['mega_upload']['min_workers']
            max_workers = worker_config.worker_configs['mega_upload']['max_workers']
            target_queue = worker_config.worker_configs['mega_upload']['target_queue_per_worker']

            self._current_workers = min_workers
            self.upload_queue = asyncio.Queue()
            self._workers: List[asyncio.Task] = []
            self._monitor_task: Optional[asyncio.Task] = None

            # Status tracking - NOW USING REDIS instead of in-memory dict
            # self.status_store: Dict[str, UploadStatusInfo] = {}
            # self._status_lock = asyncio.Lock()

            self._callbacks_lock = asyncio.Lock()
            self._events_lock = asyncio.Lock()

            self._callbacks: Dict[str, Callable] = {}
            self._completion_events: Dict[str, asyncio.Event] = {}
            self._running = False
            self._initialized = True

            logger.info(
                f"MegaUploadManager initialized (S4-powered):\n"
                f"  Initial workers: {self._current_workers}\n"
                f"  Max workers: {max_workers}\n"
                f"  Target queue per worker: {target_queue}"
            )

    async def start(self):
        """Start upload workers and monitoring."""
        if self._running:
            return

        self._running = True
        logger.info(f"Starting {self._current_workers} upload worker(s)")

        # Start workers
        for i in range(self._current_workers):
            worker_task = asyncio.create_task(
                self._upload_worker(f"mega_worker_{i}"),
                name=f"mega_worker_{i}"
            )
            self._workers.append(worker_task)
            worker_config.register_worker('mega_upload')

        # Start monitor
        self._monitor_task = asyncio.create_task(
            self._monitor_queue(),
            name="mega_upload_monitor"
        )

        logger.info(f"Upload system started with {len(self._workers)} worker(s)")

    async def stop(self):
        """Stop all upload workers and monitoring tasks."""
        if not self._running:
            return

        self._running = False
        logger.info("Stopping upload system...")

        # Cancel workers
        for worker in self._workers:
            worker.cancel()
            worker_config.unregister_worker('mega_upload')

        # Cancel monitor
        if self._monitor_task:
            self._monitor_task.cancel()

        # Wait for completion
        await asyncio.gather(*self._workers, return_exceptions=True)
        if self._monitor_task:
            await asyncio.gather(self._monitor_task, return_exceptions=True)

        self._workers.clear()
        self._monitor_task = None
        logger.info("Upload system stopped")

    async def _monitor_queue(self):
        """Monitor queue and scale workers."""
        check_interval = 5
        scale_interval = worker_config.scale_check_interval
        time_since_scale = 0

        while self._running:
            try:
                queue_size = self.upload_queue.qsize()
                worker_config.update_queue_length('mega_upload', queue_size)

                # Log status if active (check Redis for active uploads)
                memory_percent = psutil.Process().memory_percent()
                # Count active uploads from Redis
                active_uploads = 0
                try:
                    # Get all active sessions from Redis (v2 API)
                    all_sessions = redis_upload_state.get_all_sessions()
                    for session in all_sessions:
                        if session and session.get("status") == UploadStatus.UPLOADING.value:
                            active_uploads += 1
                except Exception as e:
                    logger.warning(f"Error counting active uploads: {e}")

                if queue_size > 0 or active_uploads > 0:
                    logger.info(
                        f"Upload Status: queue={queue_size}, active={active_uploads}, "
                        f"workers={len(self._workers)}, memory={memory_percent:.1f}%"
                    )

                await asyncio.sleep(check_interval)
                time_since_scale += check_interval

                # Check scaling
                if time_since_scale >= scale_interval:
                    time_since_scale = 0
                    if worker_config.can_scale('mega_upload', 'any'):
                        await self._scale_workers()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Monitor error: {e}")
                await asyncio.sleep(check_interval)

    async def _scale_workers(self):
        """Scale workers based on queue load."""
        recommended = worker_config.get_worker_count('mega_upload')
        current = len(self._workers)

        if recommended > current:
            # Scale up
            needed = recommended - current
            for i in range(needed):
                idx = current + i
                worker_task = asyncio.create_task(
                    self._upload_worker(f"mega_worker_{idx}"),
                    name=f"mega_worker_{idx}"
                )
                self._workers.append(worker_task)
                worker_config.register_worker('mega_upload')
            logger.info(f"Scaled UP workers: {current} → {recommended}")

        elif recommended < current:
            # Scale down (remove idle workers)
            excess = current - recommended
            removed = 0

            for i in range(len(self._workers) - 1, -1, -1):
                if removed >= excess:
                    break

                worker_id = f"mega_worker_{i}"
                # Check Redis for active uploads by this worker
                has_active = False
                try:
                    all_keys = redis_upload_state.redis.keys(f"{redis_upload_state.UPLOAD_STATUS_PREFIX}*")
                    for key in all_keys:
                        status_data = redis_upload_state.get_upload_status(key.split(":")[-1])
                        if (status_data and
                            status_data.get("worker_id") == worker_id and
                            status_data.get("status") == UploadStatus.UPLOADING.value):
                            has_active = True
                            break
                except Exception as e:
                    logger.warning(f"Error checking worker {worker_id} uploads: {e}")

                if not has_active:
                    self._workers[i].cancel()
                    self._workers.pop(i)
                    worker_config.unregister_worker('mega_upload')
                    removed += 1

            if removed > 0:
                logger.info(f"Scaled DOWN workers: {current} → {current - removed}")

    async def _upload_worker(self, worker_id: str):
        """Worker that processes uploads - now using S4"""
        logger.info(f"Upload worker {worker_id} started")

        while self._running:
            try:
                upload_info = await self.upload_queue.get()
                if not upload_info:
                    self.upload_queue.task_done()
                    continue

                file_id = upload_info['file_id']
                temp_path = Path(upload_info['temp_path'])
                remote_dir = upload_info['remote_dir']  # Not used for S4 but kept for compatibility
                filename = upload_info['filename']

                # Update status in Redis
                redis_upload_state.set_upload_status(
                    file_id,
                    UploadStatus.UPLOADING.value,
                    worker_id=worker_id,
                    filename=filename
                )

                try:
                    logger.info(f"[S4][{worker_id}] Starting upload for: {filename}")
                    
                    # CHANGED: Use S4 instead of MEGA
                    await self._execute_s4_upload(temp_path, filename)

                    # Mark as completed in Redis
                    redis_upload_state.set_upload_status(
                        file_id,
                        UploadStatus.COMPLETED.value,
                        worker_id=worker_id,
                        filename=filename
                    )

                    # Run callback
                    async with self._callbacks_lock:
                        callback = self._callbacks.get(file_id)
                    if callback:
                        try:
                            asyncio.create_task(callback(file_id, upload_info))
                        except Exception as cb_err:
                            logger.error(f"Error in upload callback: {cb_err}")

                except Exception as e:
                    # Mark as failed in Redis
                    error_msg = f"Upload failed: {str(e)}"
                    logger.error(f"[S4][{worker_id}] {error_msg}")
                    redis_upload_state.set_upload_status(
                        file_id,
                        UploadStatus.FAILED.value,
                        worker_id=worker_id,
                        filename=filename,
                        error=error_msg
                    )
                    raise

                finally:
                    # Signal completion
                    async with self._events_lock:
                        event = self._completion_events.get(file_id)
                        if event:
                            event.set()
                    self.upload_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in upload worker {worker_id}: {e}")
                await asyncio.sleep(1)

    async def _execute_s4_upload(self, temp_path: Path, filename: str, max_retries: int = 3):
        """
        CHANGED: Upload to S4 instead of MEGA
        """
        for attempt in range(max_retries):
            try:
                # Import S4 client
                from mega_s4_client import mega_s4_client
                
                # Determine prefix based on filename
                if any(ext in filename.lower() for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']):
                    prefix = "images"
                    content_type = "image/jpeg" if filename.lower().endswith('.jpg') else "image/png"
                else:
                    prefix = "audio"
                    content_type = "audio/mpeg"
                
                # Generate object key
                object_key = mega_s4_client.generate_object_key(filename, prefix=prefix)
                
                # Upload to S4
                success = await mega_s4_client.upload_file(
                    local_path=temp_path,
                    object_key=object_key,
                    content_type=content_type
                )
                
                if success:
                    logger.info(f"S4 upload successful: {object_key}")
                    return  # Success
                else:
                    raise Exception("S4 upload returned False")

            except Exception as e:
                if attempt < max_retries - 1:
                    delay = 2 ** attempt
                    logger.warning(
                        f"Upload attempt {attempt + 1} failed. "
                        f"Retrying in {delay}s. Error: {str(e)}"
                    )
                    await asyncio.sleep(delay)
                else:
                    raise RuntimeError(f"Upload failed after {max_retries} attempts: {str(e)}")

    async def update_status(self, file_id: str, key: str, value):
        """Update status for a file (DEPRECATED - using Redis now, kept for compatibility)."""
        # Convert to Redis update
        updates = {key: value}
        redis_upload_state.update_upload_status(file_id, updates)

    async def queue_upload_with_callback(
        self,
        file_id: str,
        temp_path: Path,
        remote_dir: str,  # Kept for compatibility but not used
        filename: str,
        completion_callback: Callable[[str, Dict], None],
        db: Optional[object] = None,
        track_id: Optional[str] = None
    ) -> Dict:
        """Queue an upload with callback - same interface as before"""
        logger.info(f"Queueing upload for {filename}")

        # Initialize status in Redis
        redis_upload_state.set_upload_status(
            file_id,
            UploadStatus.QUEUED.value,
            filename=filename
        )

        # Create completion event
        async with self._events_lock:
            self._completion_events[file_id] = asyncio.Event()

        # Store callback
        async with self._callbacks_lock:
            self._callbacks[file_id] = completion_callback

        # Queue upload
        await self.upload_queue.put({
            'file_id': file_id,
            'temp_path': temp_path,
            'remote_dir': remote_dir,
            'filename': filename,
            'db': db,
            'track_id': track_id
        })

        # Wait for completion
        await self._completion_events[file_id].wait()

        # Cleanup
        async with self._events_lock:
            self._completion_events.pop(file_id, None)
        async with self._callbacks_lock:
            self._callbacks.pop(file_id, None)

        # Check final status from Redis
        final_status = redis_upload_state.get_upload_status(file_id)
        if final_status and final_status.get("status") == UploadStatus.FAILED.value:
            raise RuntimeError(f"Upload failed: {final_status.get('error', 'Unknown error')}")

        return final_status if final_status else {}

    def get_status(self, file_id: str) -> Optional[Dict]:
        """Retrieve the current status record for an upload (from Redis)."""
        return redis_upload_state.get_upload_status(file_id)

    async def get_queue_stats(self) -> Dict:
        """Return a snapshot of the queue/worker stats for debugging (from Redis)."""
        memory_percent = psutil.Process().memory_percent()
        queue_size = self.upload_queue.qsize()

        # Count uploads by status from Redis
        active_uploads = 0
        failed_uploads = 0
        queued_uploads = 0

        try:
            all_keys = redis_upload_state.redis.keys(f"{redis_upload_state.UPLOAD_STATUS_PREFIX}*")
            for key in all_keys:
                status_data = redis_upload_state.get_upload_status(key.split(":")[-1])
                if status_data:
                    status = status_data.get("status")
                    if status == UploadStatus.UPLOADING.value:
                        active_uploads += 1
                    elif status == UploadStatus.FAILED.value:
                        failed_uploads += 1
                    elif status == UploadStatus.QUEUED.value:
                        queued_uploads += 1
        except Exception as e:
            logger.warning(f"Error getting queue stats from Redis: {e}")

        worker_cfg_status = worker_config.get_worker_status('mega_upload')
        worker_cfg = worker_config.worker_configs['mega_upload']

        return {
            'queue_size': queue_size,
            'active_uploads': active_uploads,
            'failed_uploads': failed_uploads,
            'queued_uploads': queued_uploads,
            'worker_count': len(self._workers),
            'memory_usage': memory_percent,
            'max_workers': worker_cfg['max_workers'],
            'target_queue_per_worker': worker_cfg['target_queue_per_worker'],
            'current_queue_per_worker': queue_size / max(len(self._workers), 1),
            'worker_config_status': worker_cfg_status,
            'scale_cooldown': worker_cfg_status['cooldown_remaining']
        }

    async def cleanup_old_status(self, max_age_seconds: int = 3600):
        """Remove old status entries to avoid unbounded memory usage (from Redis)."""
        # Redis handles this automatically via TTL, but we can manually clean if needed
        cleaned = redis_upload_state.cleanup_old_sessions(max_age_seconds)
        if cleaned > 0:
            logger.info(f"Cleaned up {cleaned} old upload status entries from Redis")

    def __str__(self) -> str:
        """For debugging."""
        # Count Redis statuses
        redis_status_count = 0
        try:
            redis_status_count = len(redis_upload_state.redis.keys(f"{redis_upload_state.UPLOAD_STATUS_PREFIX}*"))
        except Exception:
            pass

        return (
            f"MegaUploadManager(workers={len(self._workers)}, "
            f"queue_size={self.upload_queue.qsize()}, "
            f"redis_statuses={redis_status_count})"
        )

# Same singleton pattern as before
mega_upload_manager = MegaUploadManager()

__all__ = ['mega_upload_manager']