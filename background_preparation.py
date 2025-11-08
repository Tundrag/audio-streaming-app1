# background_preparation.py
import asyncio
import logging
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import anyio
import psutil
from datetime import datetime

from sqlalchemy.orm import Session  # type hints only
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy import select

from worker_config import worker_config
from models import Track, AuditLogType
from database import async_engine

logger = logging.getLogger(__name__)

# Create async session factory
async_session = async_sessionmaker(async_engine, expire_on_commit=False)


class PreparationStatus(Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    SEGMENTING = "segmenting"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkerStatus(Enum):
    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"


class TaskPhase(Enum):
    INIT = "initializing"
    METADATA = "extracting_metadata"
    SEGMENTING = "creating_segments"
    FINALIZING = "finalizing"
    ERROR = "error"


class BackgroundPreparationManager:
    """
    Non-blocking background preparation manager.

    Key changes:
      - Never pass DB sessions through queues or across tasks.
      - Use short-lived *sync* Sessions for locking/unlocking only.
      - Let downstream services own their session if needed (pass db=None).
      - File system work is moved to threads via anyio.to_thread.run_sync.
    """
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.status_store: Dict[str, Dict] = {}
        self.callbacks: Dict[str, Callable] = {}

        self._global_queue: asyncio.Queue = asyncio.Queue()

        self._worker_queues: Dict[str, asyncio.Queue] = {}
        self._worker_semaphores: Dict[str, asyncio.Semaphore] = {}
        self._worker_statuses: Dict[str, Dict] = {}
        self._active_worker_tasks: Dict[str, asyncio.Task] = {}

        self.max_tasks_per_worker = 3
        self.max_memory_percent = 70
        self.max_queue_per_worker = worker_config.worker_configs["background"]["target_queue_per_worker"]
        self.task_rate_limit = 3

        recommended = worker_config.get_worker_count("background")
        min_workers = worker_config.worker_configs["background"]["min_workers"]
        self._current_workers = max(recommended, min_workers)

        self._monitor_task: Optional[asyncio.Task] = None
        self._dispatcher_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._worker_metrics: Dict[str, Dict] = {}
        self.last_task_time = datetime.now()
        self._active_tasks = 0

        self.segment_progress: Dict[str, Dict] = {}
        self.processing_metrics: Dict[str, Dict] = {}

        self._initialized = True
        logger.info(
            "BackgroundPreparationManager initialized:\n"
            f"- Initial workers: {self._current_workers}\n"
            f"- Target queue/worker: {self.max_queue_per_worker}\n"
            f"- Tasks/worker: {self.max_tasks_per_worker}"
        )

    # -------------------- Lifecycle --------------------

    async def start(self):
        logger.info(
            "Starting background system with "
            f"{self._current_workers} workers (target queue/worker: {self.max_queue_per_worker})"
        )
        for i in range(self._current_workers):
            await self._create_worker(i)

        self._dispatcher_task = asyncio.create_task(self._dispatcher(), name="dispatcher")
        self._monitor_task = asyncio.create_task(self._monitor_resources(), name="resource_monitor")
        logger.info("Background preparation system started")

    async def stop(self):
        logger.info("Stopping background preparation system")

        if self._dispatcher_task:
            self._dispatcher_task.cancel()
            try:
                await self._dispatcher_task
            except asyncio.CancelledError:
                pass

        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        tasks_to_cancel = []
        for worker_task in self._active_worker_tasks.values():
            worker_task.cancel()
            tasks_to_cancel.append(worker_task)

        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

        for _ in range(self._current_workers):
            worker_config.unregister_worker("background")

        self._current_workers = 0
        self._active_worker_tasks.clear()
        self._worker_queues.clear()
        self._worker_semaphores.clear()
        self.status_store.clear()
        self.callbacks.clear()
        logger.info("Background preparation system stopped")

    # -------------------- Dispatcher --------------------

    async def _dispatcher(self):
        logger.info("Dispatcher started")
        while True:
            try:
                task_info = await self._global_queue.get()
                assigned = False
                while not assigned:
                    worker_id = self._get_least_loaded_worker()
                    if not worker_id:
                        await asyncio.sleep(0.05)
                        continue

                    sem = self._worker_semaphores[worker_id]
                    # Acquire a slot for this worker
                    await sem.acquire()

                    await self._worker_queues[worker_id].put(task_info)
                    self._worker_statuses[worker_id]["active_tasks"].append(task_info["stream_id"])
                    self._worker_statuses[worker_id]["task_count"] += 1
                    self._worker_statuses[worker_id]["status"] = WorkerStatus.BUSY.value
                    logger.info(f"Dispatched task {task_info['stream_id']} to {worker_id}")
                    assigned = True

                self._global_queue.task_done()
                worker_config.update_queue_length("background", self._global_queue.qsize())

            except asyncio.CancelledError:
                logger.info("Dispatcher received cancel signal")
                break
            except Exception as e:
                logger.error(f"Dispatcher error: {e}", exc_info=True)
                await asyncio.sleep(0.5)
        logger.info("Dispatcher shutting down")

    def _get_least_loaded_worker(self) -> Optional[str]:
        min_tasks = self.max_tasks_per_worker + 1
        selected: Optional[str] = None
        for wid, status in self._worker_statuses.items():
            if status["status"] == WorkerStatus.ERROR.value:
                continue
            tc = status["task_count"]
            if tc < min_tasks:
                min_tasks = tc
                selected = wid
        return selected

    # -------------------- Worker --------------------

    async def _worker(self, worker_id: str):
        q = self._worker_queues[worker_id]
        sem = self._worker_semaphores[worker_id]
        logger.info(f"{worker_id} started")

        while True:
            try:
                task_info = await q.get()
                stream_id = task_info.get("stream_id")

                # record phase -> INIT
                self._worker_statuses[worker_id].update(
                    {"current_phases": {stream_id: TaskPhase.INIT.value}, "last_active": datetime.now().isoformat()}
                )

                await self._update_status(stream_id, {
                    "status": PreparationStatus.PROCESSING.value,
                    "worker_id": worker_id,
                    "started_at": datetime.now().isoformat(),
                    "phase": TaskPhase.INIT.value
                })

                # Run the task concurrently but bounded by the per-worker semaphore (already acquired in dispatcher)
                asyncio.create_task(self._process_single_task(worker_id, task_info), name=f"{worker_id}_{stream_id}")

                q.task_done()

            except asyncio.CancelledError:
                logger.info(f"{worker_id} received cancel signal")
                break
            except Exception as e:
                logger.error(f"{worker_id} unexpected error: {e}", exc_info=True)
                self._worker_statuses[worker_id]["status"] = WorkerStatus.ERROR.value
                await asyncio.sleep(0.5)

        logger.info(f"{worker_id} shutting down")
        self._worker_statuses[worker_id].update({
            "status": WorkerStatus.IDLE.value,
            "active_tasks": [],
            "task_count": 0,
            "current_phases": {},
            "last_active": None,
            "error": None,
        })
        worker_config.unregister_worker("background")

    async def _process_single_task(self, worker_id: str, task_info: Dict):
        """
        Process a single task. Uses short-lived sync DB sessions for locking/unlocking only.
        Respects lock_already_held flag to avoid double-locking.
        """
        from status_lock import status_lock
        from database import get_db  # generator yielding a sync Session

        stream_id: str = task_info["stream_id"]
        filename: str = task_info["filename"]
        prepare_func: Callable = task_info["prepare_func"]
        task_info_data: Dict = task_info.get("task_info", {}) or {}
        start_time = datetime.now()

        actual_track_id = task_info_data.get("track_id", stream_id)
        is_voice_stream = False
        voice_id: Optional[str] = None

        # detect if this is a voice stream
        if task_info_data.get("voice"):
            is_voice_stream = True
            voice_id = task_info_data["voice"]

        # ✅ CHECK IF LOCK ALREADY HELD BY CALLER
        lock_already_held = task_info_data.get("lock_already_held", False)
        locked = False

        try:
            if not lock_already_held:
                # Acquire DB-backed lock with a fresh sync Session
                lock_db = next(get_db())
                try:
                    locked, reason = await status_lock.try_lock_voice(
                        track_id=actual_track_id,
                        voice_id=voice_id,
                        process_type=("voice_switch" if (task_info_data.get("is_voice_switch") or voice_id) else "initial"),
                        db=lock_db,
                    )
                finally:
                    try:
                        lock_db.close()
                    except Exception:
                        pass

                if not locked:
                    logger.info(f"{worker_id} could not lock track {actual_track_id}: {reason}")
                    return

                logger.info(f"{worker_id} ✅ LOCKED track {actual_track_id} ({'voice' if is_voice_stream else 'regular'})")
            else:
                # Lock already held by caller (TTS worker)
                logger.info(f"{worker_id} ✅ Using EXISTING LOCK for track {actual_track_id} ({'voice' if is_voice_stream else 'regular'})")
                locked = True  # Mark as locked so we unlock at the end

            # phase progression & processing
            result = await self._prepare_func_with_phases(
                prepare_func=prepare_func,
                filename=filename,
                stream_id=stream_id,
                worker_id=worker_id,
                db=None,  # DO NOT pass sessions through
                task_info=task_info_data,
            )

            # ✅ UNLOCK WITH SUCCESS (always unlock if we hold the lock)
            if locked:
                logger.info(f"{worker_id} 🔧 Lock state: locked={locked}, voice_id={voice_id}")
                logger.info(f"{worker_id} 🔧 Unlocking track {actual_track_id} with validation")
                unlock_db = next(get_db())
                try:
                    await status_lock.unlock_voice(
                        actual_track_id,
                        voice_id=voice_id,
                        success=True,
                        db=unlock_db
                    )
                    logger.info(f"{worker_id} ✅ UNLOCKED track {actual_track_id} (success)")

                    # Mark voice generation complete if this is a voice stream
                    if voice_id:
                        try:
                            from voice_cache_manager import voice_cache_manager
                            await voice_cache_manager.mark_voice_complete(actual_track_id, voice_id, unlock_db)
                            logger.info(f"{worker_id} ✅ Marked voice {voice_id} complete for track {actual_track_id}")
                        except Exception as voice_mark_error:
                            logger.warning(f"{worker_id} Failed to mark voice complete (non-fatal): {voice_mark_error}")
                finally:
                    try:
                        unlock_db.close()
                    except Exception:
                        pass

            processing_time = (datetime.now() - start_time).total_seconds()
            await self._update_status(stream_id, {
                "status": PreparationStatus.COMPLETED.value,
                "completed_at": datetime.now().isoformat(),
                "processing_time": processing_time
            })
            logger.info(f"{worker_id} completed task {stream_id}")

        except Exception as e:
            logger.error(f"Error processing task {stream_id}: {e}", exc_info=True)

            # Log upload failure activity
            try:
                from activity_logs_router import log_activity_isolated
                from database import async_engine
                from sqlalchemy.ext.asyncio import async_sessionmaker

                # Get track info for logging
                temp_session_factory = async_sessionmaker(async_engine, expire_on_commit=False)
                async with temp_session_factory() as temp_s:
                    result = await temp_s.execute(select(Track).where(Track.id == actual_track_id))
                    failed_track = result.scalar_one_or_none()

                    if failed_track:
                        await log_activity_isolated(
                            user_id=failed_track.created_by_id,
                            action_type=AuditLogType.CREATE,
                            table_name='tracks',
                            record_id=actual_track_id,
                            description=f"Failed to process '{failed_track.title}': {str(e)[:100]}"
                        )
            except Exception as log_err:
                logger.warning(f"Failed to log upload failure activity: {log_err}")

            # ✅ UNLOCK WITH FAILURE (if we hold the lock)
            if locked:
                logger.info(f"{worker_id} 🔧 Lock state: locked={locked}, voice_id={voice_id}")
                logger.info(f"{worker_id} 🔧 Unlocking track {actual_track_id} with validation (failure)")
                from database import get_db
                fail_db = next(get_db())
                try:
                    await status_lock.unlock_voice(
                        actual_track_id,
                        voice_id=voice_id,
                        success=False,
                        db=fail_db
                    )
                    logger.info(f"{worker_id} ✅ UNLOCKED track {actual_track_id} (failed)")

                    # Mark voice generation failed if this is a voice stream
                    if voice_id:
                        try:
                            from voice_cache_manager import voice_cache_manager
                            await voice_cache_manager.mark_voice_failed(actual_track_id, voice_id, str(e), fail_db)
                            logger.info(f"{worker_id} ✅ Marked voice {voice_id} failed for track {actual_track_id}")
                        except Exception as voice_mark_error:
                            logger.warning(f"{worker_id} Failed to mark voice failed (non-fatal): {voice_mark_error}")
                finally:
                    try:
                        fail_db.close()
                    except Exception:
                        pass

            await self._update_status(stream_id, {
                "status": PreparationStatus.FAILED.value,
                "error": str(e),
                "phase": TaskPhase.ERROR.value
            })

        finally:
            # release per-worker slot
            self._worker_semaphores[worker_id].release()

            async with self._lock:
                if stream_id in self._worker_statuses[worker_id]["active_tasks"]:
                    self._worker_statuses[worker_id]["active_tasks"].remove(stream_id)
                self._worker_statuses[worker_id]["task_count"] -= 1
                self._worker_statuses[worker_id]["current_phases"].pop(stream_id, None)
                self._worker_statuses[worker_id]["status"] = (
                    WorkerStatus.IDLE.value if self._worker_statuses[worker_id]["task_count"] == 0
                    else WorkerStatus.BUSY.value
                )

            # Non-blocking cleanup - ONLY CLEAN UP THE SPECIFIC FILE, NOT DIRECTORIES
            # This prevents deleting shared temp directories that other concurrent voice generations may still need
            temp_path = Path("/tmp/media_storage") / filename
            if temp_path.exists() and temp_path.is_file():
                try:
                    await anyio.to_thread.run_sync(temp_path.unlink)
                    logger.info(f"Cleaned up temp file: {temp_path}")
                except Exception as cleanup_error:
                    logger.error(f"Failed to cleanup temp file {temp_path}: {cleanup_error}")

            # REMOVED: Session directory cleanup - causes race condition when multiple voices generate concurrently
            # The shared /tmp/media_storage/tts/ directory contains files for all concurrent voice generations
            # Deleting it here removes files that other tasks still need (e.g., s4_restored_*.jsonl)
            # Let background cleanup tasks handle directory cleanup when ALL tasks are complete

    # -------------------- Preparation phases --------------------

    async def _prepare_func_with_phases(
        self,
        prepare_func: Callable,
        filename: str,
        stream_id: str,
        worker_id: str,
        db: Optional[Session] = None,
        task_info: Optional[Dict] = None
    ) -> Any:
        """
        Process a task through phases with version management and cache cleanup.
        Handles version increment and cache clearing for TTS tracks.
        Uses TrackStatusManager for consistent status updates.
        """
        start_time = datetime.now()

        actual_track_id = task_info.get("track_id", stream_id) if task_info else stream_id
        is_regeneration = bool(task_info.get("is_regeneration")) if task_info else False
        voice_id = task_info.get("voice") if task_info else None
        is_voice_stream = bool(voice_id)

        self._worker_statuses[worker_id]["current_phases"][stream_id] = TaskPhase.INIT.value
        await self._update_status(stream_id, {"phase": TaskPhase.INIT.value})

        logger.info(
            f"{worker_id} starting {'regeneration' if is_regeneration else 'preparation'} task:\n"
            f"- Stream ID: {stream_id}\n"
            f"- Track ID: {actual_track_id}\n"
            f"- Filename: {filename}\n"
            f"- Voice: {voice_id or 'N/A'}"
        )

        try:
            if not is_regeneration:
                self._worker_statuses[worker_id]["current_phases"][stream_id] = TaskPhase.METADATA.value
                await self._update_status(stream_id, {"phase": TaskPhase.METADATA.value})

            self._worker_statuses[worker_id]["current_phases"][stream_id] = TaskPhase.SEGMENTING.value
            await self._update_status(stream_id, {"phase": TaskPhase.SEGMENTING.value})

            if is_voice_stream and voice_id:
                logger.info(f"{worker_id} detected VOICE STREAM - using voice-specific HLS preparation")
                from hls_streaming import stream_manager

                result = await stream_manager.prepare_hls_stream_with_voice(
                    file_path=Path(task_info["temp_path"]),
                    filename=filename,
                    track_id=actual_track_id,
                    voice=voice_id,
                    db=None,
                )
                logger.info(f"{worker_id} voice-specific HLS preparation complete for {voice_id}")
            else:
                logger.info(f"{worker_id} processing regular audio stream")
                result = await prepare_func(filename, db=None, task_info=task_info)

            self._worker_statuses[worker_id]["current_phases"][stream_id] = TaskPhase.FINALIZING.value
            await self._update_status(stream_id, {"phase": TaskPhase.FINALIZING.value})

            try:
                from track_status_manager import TrackStatusManager

                async with async_session() as s:
                    # Fetch track using async query
                    result = await s.execute(select(Track).where(Track.id == actual_track_id))
                    track = result.scalar_one_or_none()

                    if track:
                        is_tts_track = track.track_type == 'tts'

                        # Use TrackStatusManager for consistent status updates
                        await TrackStatusManager.mark_complete(track, s)

                        # Additional TTS-specific updates
                        if is_tts_track:
                            old_version = track.content_version or 0
                            track.content_version = old_version + 1
                            logger.info(
                                f"{worker_id} version increment: "
                                f"track={actual_track_id}, v{old_version} -> v{track.content_version}"
                            )

                            # ✅ Invalidate authorization grants when content changes
                            try:
                                from authorization_service import invalidate_on_content_change
                                await invalidate_on_content_change(actual_track_id)
                            except Exception as e:
                                logger.warning(f"Failed to invalidate grants for {actual_track_id}: {e}")

                        await s.commit()
                        logger.info(
                            f"{worker_id} Track {actual_track_id} marked complete: "
                            f"status={track.status}, tts_status={getattr(track, 'tts_status', 'N/A')}, "
                            f"upload_status={track.upload_status}"
                        )

                        # Log activity - Track processing completed
                        # Uses isolated session to prevent poisoning main transaction
                        from activity_logs_router import log_activity_isolated
                        if is_tts_track:
                            # TTS track with voice info
                            voice_id_log = getattr(track, 'voice_id', None)
                            description = f"Completed TTS generation for '{track.title}'"
                            if voice_id_log:
                                description += f" (voice_id: {voice_id_log})"

                            await log_activity_isolated(
                                user_id=track.created_by_id,
                                action_type=AuditLogType.CREATE,
                                table_name='tracks',
                                record_id=actual_track_id,
                                description=description
                            )
                        else:
                            # Non-TTS audio upload completed
                            await log_activity_isolated(
                                user_id=track.created_by_id,
                                action_type=AuditLogType.CREATE,
                                table_name='tracks',
                                record_id=actual_track_id,
                                description=f"Completed upload and processing for '{track.title}'"
                            )

                        # Cache cleanup for TTS tracks (after commit)
                        if is_tts_track:
                            try:
                                from read_along_cache import clear_track_cache, clear_old_versions
                                
                                if is_voice_stream and voice_id:
                                    await clear_track_cache(actual_track_id, voice_id)
                                    await clear_old_versions(
                                        actual_track_id, 
                                        voice_id, 
                                        f"v{track.content_version}"
                                    )
                                    logger.info(
                                        f"{worker_id} cache cleared: "
                                        f"track={actual_track_id}, voice={voice_id}, "
                                        f"version=v{track.content_version}"
                                    )
                                else:
                                    default_voice = track.default_voice or "default"
                                    await clear_track_cache(actual_track_id, default_voice)
                                    await clear_old_versions(
                                        actual_track_id, 
                                        default_voice, 
                                        f"v{track.content_version}"
                                    )
                                    logger.info(
                                        f"{worker_id} cache cleared: "
                                        f"track={actual_track_id}, voice={default_voice}, "
                                        f"version=v{track.content_version}"
                                    )
                                    
                            except Exception as cache_error:
                                logger.warning(
                                    f"{worker_id} cache clear failed (non-fatal): {cache_error}"
                                )

            except Exception as db_error:
                logger.error(
                    f"{worker_id} DB update error for {actual_track_id}: {db_error}", 
                    exc_info=True
                )
                raise

            return result

        except Exception as e:
            try:
                from database import get_db
                from track_status_manager import TrackStatusManager
                s = next(get_db())
                
                try:
                    track = s.query(Track).filter(Track.id == actual_track_id).first()
                    if track:
                        # Use TrackStatusManager for consistent error handling
                        await TrackStatusManager.mark_failed(track, s, e, 'background_preparation')
                    s.commit()
                    logger.info(
                        f"{worker_id} Track {actual_track_id} marked failed: "
                        f"status={track.status if track else 'N/A'}, error={str(e)}"
                    )
                    
                except Exception:
                    try:
                        s.rollback()
                    except Exception:
                        pass
                        
                finally:
                    try:
                        s.close()
                    except Exception:
                        pass
                        
            except Exception:
                pass

            if stream_id:
                await self._update_status(stream_id, {
                    "status": PreparationStatus.FAILED.value,
                    "error": str(e),
                    "phase": TaskPhase.ERROR.value
                })
            raise

    # -------------------- Monitor & scale --------------------

    async def _monitor_resources(self):
        logger.info("Resource monitor started")
        scale_interval = worker_config.scale_check_interval
        elapsed = 0
        while True:
            try:
                mem = psutil.virtual_memory().percent
                active_workers = []
                total_active_tasks = 0

                for wid, status in self._worker_statuses.items():
                    if status["status"] == WorkerStatus.BUSY.value:
                        active_workers.append({
                            "worker_id": wid,
                            "active_tasks": status.get("active_tasks", []),
                            "phases": status.get("current_phases", {}),
                        })
                        total_active_tasks += len(status.get("active_tasks", []))

                queue_sizes = {wid: q.qsize() for wid, q in self._worker_queues.items()}

                logger.info(
                    "System Status:\n"
                    f"- Active workers: {len(active_workers)}/{self._current_workers}\n"
                    f"- Active tasks: {total_active_tasks}\n"
                    f"- Memory usage: {mem:.1f}% (limit: {self.max_memory_percent}%)\n"
                    f"- Queue sizes per worker: {queue_sizes}"
                )

                if any(size > self.max_queue_per_worker for size in queue_sizes.values()):
                    logger.warning("High queue pressure: worker queue exceeds target queue per worker.")

                elapsed += 5
                if elapsed >= scale_interval:
                    elapsed = 0
                    await self._scale_workers()

                await asyncio.sleep(5)

            except asyncio.CancelledError:
                logger.info("Resource monitor stopping")
                break
            except Exception as e:
                logger.error(f"Resource monitor error: {e}", exc_info=True)
                await asyncio.sleep(5)
        logger.info("Resource monitor stopped")

    async def _scale_workers(self):
        recommended = worker_config.get_worker_count("background")
        current = self._current_workers

        if recommended > current:
            need = recommended - current
            logger.info(f"Scaling UP workers by {need} (from {current} to {recommended})")
            for i in range(need):
                await self._create_worker(current + i)
            self._current_workers = recommended

        elif recommended < current:
            drop = current - recommended
            logger.info(f"Scaling DOWN workers by {drop} (from {current} to {recommended})")
            workers_to_remove = []
            for wid in sorted(self._worker_queues.keys()):
                if drop <= 0:
                    break
                status = self._worker_statuses[wid]
                if status["status"] == WorkerStatus.IDLE.value and status["task_count"] == 0:
                    workers_to_remove.append(wid)
                    drop -= 1
            for wid in workers_to_remove:
                await self._shutdown_worker(wid)
            self._current_workers = current - len(workers_to_remove)

    async def _create_worker(self, index: int):
        worker_id = f"background_{index}"
        self._worker_queues[worker_id] = asyncio.Queue()
        self._worker_semaphores[worker_id] = asyncio.Semaphore(self.max_tasks_per_worker)

        self._worker_metrics[worker_id] = {
            "tasks_completed": 0,
            "total_processing_time": 0,
            "avg_processing_time": 0,
            "failed_tasks": 0,
        }
        self._worker_statuses[worker_id] = {
            "status": WorkerStatus.IDLE.value,
            "active_tasks": [],
            "task_count": 0,
            "current_phases": {},
            "last_active": None,
            "error": None,
        }

        worker_config.register_worker("background")
        logger.info(f"Creating new background worker {worker_id}")

        task = asyncio.create_task(self._worker(worker_id), name=f"background_{index}")
        self._active_worker_tasks[worker_id] = task
        logger.info(f"Started worker {worker_id}")

    async def _shutdown_worker(self, worker_id: str):
        logger.info(f"Shutting down worker {worker_id}")
        task = self._active_worker_tasks.get(worker_id)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            del self._active_worker_tasks[worker_id]
            del self._worker_queues[worker_id]
            del self._worker_semaphores[worker_id]
            del self._worker_statuses[worker_id]
            del self._worker_metrics[worker_id]
            worker_config.unregister_worker("background")
            logger.info(f"Worker {worker_id} successfully shut down")

    # -------------------- Public API --------------------

    async def queue_preparation(
        self,
        stream_id: str,
        filename: str,
        prepare_func: Callable,
        file_size: int = 0,
        priority: str = "normal",
        db_session: Optional[AsyncSession] = None,   # kept for API compatibility; ignored
        status_callback: Optional[Callable] = None,
        task_info: Optional[Dict] = None
    ) -> Dict:
        """
        Queue a stream for preparation. Any db_session passed in will be ignored to prevent
        cross-task session reuse.
        """
        async with self._lock:
            if stream_id in self.status_store:
                existing = self.status_store[stream_id]
                if existing.get("status") in (PreparationStatus.COMPLETED.value, PreparationStatus.FAILED.value):
                    logger.info(f"Removing terminal status for {stream_id}, allowing re-queue")
                    self.status_store.pop(stream_id, None)
                    self.callbacks.pop(stream_id, None)
                else:
                    return existing

            task_info = task_info or {}
            task_info.setdefault("track_id", stream_id)

            queue_priority = self._determine_priority(file_size, priority)

            status = {
                "stream_id": stream_id,
                "track_id": task_info.get("track_id"),
                "filename": filename,
                "status": PreparationStatus.QUEUED.value,
                "queued_at": datetime.now().isoformat(),
                "progress": 0,
                "file_size": file_size,
                "priority": queue_priority,
                "error": None,
                "phase": TaskPhase.INIT.value,
                "db_session": None  # <= explicitly null; we do not carry sessions across tasks
            }
            self.status_store[stream_id] = status

            if status_callback:
                self.callbacks[stream_id] = status_callback

            queue_item = {
                "stream_id": stream_id,
                "filename": filename,
                "prepare_func": prepare_func,
                "file_size": file_size,
                "db_session": None,  # ignored by workers; do not pass sessions
                "task_info": task_info,
            }

            # concise logging
            meta = task_info.get("metadata", {})
            logger.info(
                "Queueing preparation task:\n"
                f"- Stream ID: {stream_id}\n"
                f"- Track ID: {task_info.get('track_id')}\n"
                f"- Priority: {queue_priority}\n"
                f"- Voice: {task_info.get('voice', 'N/A')}\n"
                f"- Duration: {meta.get('duration', 'Unknown')}s\n"
                f"- File size: {file_size / (1024*1024):.1f}MB"
            )

            await self._global_queue.put(queue_item)
            worker_config.update_queue_length("background", self._global_queue.qsize())
            logger.info(f"Queued preparation for stream {stream_id} with {queue_priority} priority")
            return status

    def _determine_priority(self, file_size: int, requested_priority: str) -> str:
        if requested_priority != "normal":
            return requested_priority
        if file_size < 10 * 1024 * 1024:
            return "high"
        elif file_size > 100 * 1024 * 1024:
            return "low"
        return "normal"

    async def _update_status(self, stream_id: str, updates: Dict):
        async with self._lock:
            if stream_id in self.status_store:
                self.status_store[stream_id].update(updates)
                if stream_id in self.callbacks:
                    try:
                        await self.callbacks[stream_id](self.status_store[stream_id])
                    except Exception as e:
                        logger.error(f"Callback error for {stream_id}: {e}", exc_info=True)

    def get_status(self, stream_id: str) -> Optional[Dict]:
        status = self.status_store.get(stream_id)
        if status:
            if stream_id in self.segment_progress:
                status["segmentation"] = self.segment_progress[stream_id]
            if stream_id in self.processing_metrics:
                status["metrics"] = self.processing_metrics[stream_id]
        return status

    def get_worker_status(self) -> Dict:
        return {
            "workers": self._worker_statuses,
            "queue_sizes": {wid: q.qsize() for wid, q in self._worker_queues.items()},
        }

    def get_worker_metrics(self) -> Dict:
        worker_metrics = {}
        for wid, metrics in self._worker_metrics.items():
            worker_metrics[wid] = {
                **metrics,
                "status": self._worker_statuses[wid]["status"],
                "active_tasks": self._worker_statuses[wid].get("active_tasks", []),
                "task_count": self._worker_statuses[wid].get("task_count", 0),
                "current_phases": self._worker_statuses[wid].get("current_phases", {}),
            }
        return worker_metrics

    def update_segment_progress(self, stream_id: str, current: int, total: int):
        self.segment_progress[stream_id] = {
            "current": current,
            "total": total,
            "percentage": (current / total * 100) if total > 0 else 0,
        }


__all__ = [
    "BackgroundPreparationManager",
    "PreparationStatus",
    "WorkerStatus",
]
