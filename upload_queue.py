from __future__ import annotations
import asyncio
import aiofiles
import logging
import psutil
import time
from typing import Dict, Optional, Callable, List
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

# Import our worker config singleton:
from worker_config import worker_config

# Redis upload stats for multi-container support
from redis_state.cache.upload_stats import upload_stats as redis_upload_stats, WriteStats as RedisWriteStats

logging.getLogger('upload_queue').setLevel(logging.DEBUG)
logger = logging.getLogger(__name__)


class WriteStatus(Enum):
    QUEUED = "queued"
    WRITING = "writing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class WriteStats:
    file_id: str
    status: str
    path: str
    queued_at: float
    bytes_written: int = 0
    chunks_written: int = 0
    total_size: Optional[int] = None
    duration: Optional[float] = None
    speed: Optional[float] = None
    error: Optional[str] = None
    worker_id: Optional[str] = None


class UploadQueue:
    """
    UploadQueue manages disk I/O for incoming file uploads in a
    concurrency-friendly manner. It uses multiple "writer" tasks that
    process queued writes. The number of writer tasks can scale dynamically
    up or down based on CPU usage and queue backlog.
    """
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self.logger = logging.getLogger("upload_queue")
            self.logger.setLevel(logging.INFO)

            # Start with the minimum number of disk_io workers from worker_config
            min_workers = worker_config.worker_configs['disk_io']['min_workers']
            self._current_workers = min_workers

            # We'll have one queue per worker, round-robin distribution
            self.write_queues: List[asyncio.Queue] = []
            self._workers: List[asyncio.Task] = []
            self._next_worker = 0

            # Stats tracking (Redis-backed for multi-container visibility)
            self._file_stats = redis_upload_stats.file_stats
            self._completion_events: Dict[str, asyncio.Event] = {}  # Local events OK
            self._callbacks: Dict[str, Callable] = {}  # Local callbacks OK
            self._stats_lock = asyncio.Lock()  # Local lock for in-container operations

            # Monitoring
            self._monitor_task: Optional[asyncio.Task] = None
            self._running = False

            self._initialized = True
            self.logger.info(
                f"UploadQueue initialized with {min_workers} writer(s). "
                f"Target queue/worker={worker_config.worker_configs['disk_io']['target_queue_per_worker']}"
            )

    async def start(self):
        """Start the writer tasks and the monitor loop if not already running."""
        if self._running:
            return

        self._running = True
        self.logger.info(f"Starting {self._current_workers} disk writers...")

        # Create initial writer queues and tasks
        for i in range(self._current_workers):
            q = asyncio.Queue()
            self.write_queues.append(q)
            worker_task = asyncio.create_task(
                self._disk_writer(f"writer_{i}", q),
                name=f"disk_writer_{i}"
            )
            self._workers.append(worker_task)
            worker_config.register_worker('disk_io')

        # Start monitor
        self._monitor_task = asyncio.create_task(
            self._monitor_queue(),
            name="upload_queue_monitor"
        )

        self.logger.info(
            f"Upload queue workers started with {len(self._workers)} writer(s)."
        )

    async def stop(self):
        """Stop all writer tasks and the monitor."""
        if not self._running:
            return

        self._running = False
        self.logger.info("Stopping upload queue workers...")

        # Cancel workers
        for worker in self._workers:
            worker.cancel()
            worker_config.unregister_worker('disk_io')

        # Cancel the monitor
        if self._monitor_task:
            self._monitor_task.cancel()

        # Wait for everything to finish/cancel
        await asyncio.gather(*self._workers, return_exceptions=True)
        if self._monitor_task:
            await asyncio.gather(self._monitor_task, return_exceptions=True)

        self._workers.clear()
        self.write_queues.clear()
        self._monitor_task = None

        self.logger.info("All upload queue workers stopped.")

    async def _disk_writer(self, worker_id: str, queue: asyncio.Queue):
        """Coroutine that continuously pulls from the queue and writes the file to disk."""
        logger.info(f"Disk writer {worker_id} started.")
        try:
            while self._running:
                file_info = await queue.get()
                if not file_info:
                    queue.task_done()
                    continue

                file_id = file_info['file_id']
                start_time = time.time()

                try:
                    logger.info(f"[Writer][{worker_id}] Starting write for {file_id}")
                    await self._update_stats(file_id, {
                        'status': WriteStatus.WRITING.value,
                        'worker_id': worker_id
                    })

                    total_size = 0
                    chunk_count = 0

                    async with aiofiles.open(file_info['path'], 'wb') as f:
                        file_obj = file_info['file']
                        # Read/write in 32MB chunks
                        while chunk := await file_obj.read(32 * 1024 * 1024):
                            chunk_start = time.time()
                            await f.write(chunk)
                            chunk_size = len(chunk)
                            chunk_duration = time.time() - chunk_start

                            total_size += chunk_size
                            chunk_count += 1

                            # Optionally, log slow writes
                            if chunk_duration > 0.1:
                                logger.warning(
                                    f"[Writer][{worker_id}] Slow write: "
                                    f"{chunk_duration:.3f}s for {chunk_size/1024/1024:.1f}MB"
                                )

                            await self._update_stats(file_id, {
                                'bytes_written': total_size,
                                'chunks_written': chunk_count
                            })

                    duration = time.time() - start_time
                    await self._update_stats(file_id, {
                        'status': WriteStatus.COMPLETED.value,
                        'total_size': total_size,
                        'duration': duration,
                        'speed': (total_size / duration) if duration > 0 else 0
                    })

                    logger.info(
                        f"[Writer][{worker_id}] Completed write for {file_id}: "
                        f"{total_size/1024/1024:.1f}MB in {duration:.2f}s"
                    )

                    # Completion callback
                    if file_id in self._callbacks:
                        try:
                            await self._callbacks[file_id](file_id, file_info)
                        except Exception as cb_err:
                            logger.error(f"Error in completion callback: {cb_err}")

                except Exception as e:
                    logger.error(f"[Writer][{worker_id}] Error writing file {file_id}: {e}")
                    await self._update_stats(file_id, {
                        'status': WriteStatus.FAILED.value,
                        'error': str(e)
                    })
                    raise
                finally:
                    if file_id in self._completion_events:
                        self._completion_events[file_id].set()
                    queue.task_done()

        except asyncio.CancelledError:
            logger.info(f"[Writer][{worker_id}] Cancelled.")
        except Exception as ex:
            logger.error(f"[Writer][{worker_id}] Unexpected error: {ex}")

    async def _monitor_queue(self):
        """
        Periodically checks the queue length, updates WorkerConfig,
        and attempts to scale up or down the number of active disk writers.
        """
        check_interval = 5  # how often (seconds) to re-check queue & system usage
        scale_interval = worker_config.scale_check_interval
        time_since_scale = 0

        while self._running:
            try:
                # Sum the queue sizes for all writers
                total_queued = sum(q.qsize() for q in self.write_queues)
                # Update the worker_config so it knows how many tasks are waiting
                worker_config.update_queue_length('disk_io', total_queued)

                # Log basic info if there's something happening
                memory_percent = psutil.Process().memory_percent()
                cpu_percent = psutil.cpu_percent(interval=0.1)  # quick CPU sample
                status_info = {
                    'queues': [],
                    'memory': round(memory_percent, 2),
                    'cpu': round(cpu_percent, 2),
                }
                async with self._stats_lock:
                    for i, q in enumerate(self.write_queues):
                        active_count = len([
                            s for s in self._file_stats.values()
                            if s.worker_id == f"writer_{i}" and s.status == WriteStatus.WRITING.value
                        ])
                        status_info['queues'].append({
                            'worker': f"writer_{i}",
                            'queue_size': q.qsize(),
                            'active_writes': active_count,
                        })

                # If there's active or queued tasks, log the status for visibility
                if total_queued > 0 or any(x['active_writes'] > 0 for x in status_info['queues']):
                    logger.info(
                        f"UploadQueue Monitor: {status_info}\n"
                        f"Current disk IO writers: {len(self._workers)} / "
                        f"Target CPU usage: {worker_config.worker_configs['disk_io']['target_cpu_percent']}%"
                    )

                # Sleep a bit
                await asyncio.sleep(check_interval)
                time_since_scale += check_interval

                # Check scaling
                if time_since_scale >= scale_interval:
                    time_since_scale = 0
                    if worker_config.can_scale('disk_io', 'any'):
                        await self._scale_workers()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Monitor error in upload queue: {e}")
                await asyncio.sleep(check_interval)

    async def _scale_workers(self):
        """
        Scale the number of writer tasks based on the recommended count
        from the worker_config. This is the core logic that actually spawns
        or kills tasks.
        """
        recommended = worker_config.get_worker_count('disk_io')
        current = len(self._workers)

        if recommended > current:
            # Scale up
            needed = recommended - current
            logger.info(
                f"Scaling UP disk writers by {needed} (from {current} to {recommended})"
            )
            for i in range(needed):
                new_idx = current + i
                q = asyncio.Queue()
                self.write_queues.append(q)
                wtask = asyncio.create_task(
                    self._disk_writer(f"writer_{new_idx}", q),
                    name=f"disk_writer_{new_idx}"
                )
                self._workers.append(wtask)
                worker_config.register_worker('disk_io')
                logger.info(f"Added disk writer writer_{new_idx}")

        elif recommended < current:
            # Scale down
            excess = current - recommended
            logger.info(
                f"Scaling DOWN disk writers by {excess} (from {current} to {recommended})"
            )

            removed = 0
            # We attempt to remove idle or least-busy workers first
            for i in range(len(self._workers) - 1, -1, -1):
                if removed >= excess:
                    break
                qsize = self.write_queues[i].qsize()
                writer_id = f"writer_{i}"

                # Any files currently being written by this worker?
                active_writes = [
                    s for s in self._file_stats.values()
                    if s.worker_id == writer_id and s.status == WriteStatus.WRITING.value
                ]

                # Only remove if queue is empty and no active writes
                if qsize == 0 and len(active_writes) == 0:
                    self._workers[i].cancel()
                    self._workers.pop(i)
                    self.write_queues.pop(i)
                    worker_config.unregister_worker('disk_io')
                    removed += 1
                    logger.info(f"Removed idle disk writer writer_{i}")

            if removed < excess:
                logger.info(
                    f"Not enough idle workers to fully scale down: "
                    f"removed={removed}, needed={excess}"
                )

    async def queue_upload_with_callback(
        self,
        file,
        path: Path,
        completion_callback: Callable,
        file_id: Optional[str] = None
    ) -> WriteStats:
        """
        Public API to enqueue a file for disk writing.
        - file_stream: an async file-like object
        - path: the target file path on disk
        - completion_callback: an async function called after successful write
        - file_id: optional unique ID for tracking
        """
        file_id = file_id or str(path)
        logger.info(
            f"[UploadQueue] Starting upload for file_id={file_id}, path={path}"
        )
        logger.info(f"Queuing file {file_id} for disk write")

        # Create an Event that we'll set once the write is done
        completion_event = asyncio.Event()
        self._completion_events[file_id] = completion_event
        self._callbacks[file_id] = completion_callback

        # Initialize stats object
        async with self._stats_lock:
            self._file_stats[file_id] = WriteStats(
                file_id=file_id,
                status=WriteStatus.QUEUED.value,
                path=str(path),
                queued_at=time.time(),
            )

        try:
            # Simple round-robin queue selection
            idx = self._next_worker % len(self._workers)
            self._next_worker += 1

            logger.info(f"Assigning file {file_id} to disk writer {idx}")
            logger.info(f"[UploadQueue] Successfully queued file_id={file_id} to writer {idx}")

            await self.write_queues[idx].put({
                'file': file,
                'path': path,
                'file_id': file_id
            })

            # Wait until the writer signals completion via the event
            await completion_event.wait()

            # Final check of status
            async with self._stats_lock:
                stats = self._file_stats[file_id]
                if stats.status == WriteStatus.FAILED.value:
                    raise Exception(stats.error or "Unknown error during file write")
                return stats

        except Exception as e:
            logger.error(f"Error queuing upload for {file_id}: {e}")
            async with self._stats_lock:
                if file_id in self._file_stats:
                    self._file_stats[file_id].status = WriteStatus.FAILED.value
                    self._file_stats[file_id].error = str(e)
            raise

        finally:
            # Cleanup references after a delay, so .get_stats(...) can still be called if needed
            self._completion_events.pop(file_id, None)
            self._callbacks.pop(file_id, None)
            asyncio.create_task(self._cleanup_stats(file_id))

    async def _update_stats(self, file_id: str, updates: Dict):
        """Update file stats in a thread-safe manner."""
        async with self._stats_lock:
            if file_id in self._file_stats:
                for k, v in updates.items():
                    setattr(self._file_stats[file_id], k, v)

    async def _cleanup_stats(self, file_id: str, delay: int = 300):
        """
        Remove stats after a given delay to avoid unbounded memory usage
        in long-running processes.
        """
        await asyncio.sleep(delay)
        async with self._stats_lock:
            self._file_stats.pop(file_id, None)

    def get_stats(self, file_id: str) -> Optional[WriteStats]:
        """Optional utility to retrieve stats for a particular file_id."""
        return self._file_stats.get(file_id)


# Create the global/singleton instance
upload_queue = UploadQueue()
