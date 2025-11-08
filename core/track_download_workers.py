# core.track_download_workers.py
import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Optional, Callable
from contextlib import contextmanager

from worker_config import worker_config

logger = logging.getLogger(__name__)

# --- CONCURRENCY LIMIT --------------------------------------------------------
MAX_CONCURRENT_DOWNLOADS_PER_USER = 2  # tune to taste


class ConcurrentLimitExceeded(Exception):
    """Raised when a user exceeds the concurrent download limit."""
    def __init__(self, limit: int, active: int):
        self.limit = limit
        self.active = active
        super().__init__("concurrent_limit_exceeded")


# ------------------------------------------------------------------------------
class TrackDownloadWorker:
    """Worker dedicated to handling individual track downloads with S4 support."""
    def __init__(self, worker_id: str, download_queue: asyncio.Queue, db_factory: Optional[Callable] = None, concurrency: int = 5):
        self.worker_id = worker_id
        self.download_queue = download_queue
        from database import SessionLocal
        self._db_factory = db_factory or SessionLocal
        self.concurrency = concurrency

        self.temp_dir = Path("/tmp/mega_downloads/tracks")
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        self._is_running = False
        self._worker_tasks = []
        self.task_timeout = 1800       # 30 min/task
        self.progress_timeout = 300    # 5 min without progress
        self.chunk_timeout = 30        # 30s per chunk read

        # Live-task tracking (for stuck checks)
        self.current_task_start: Optional[float] = None
        self.last_progress_time: Optional[float] = None
        self.current_download_id: Optional[str] = None
        self._current_task: Optional[Dict] = None

    # Lifecycle ----------------------------------------------------------------
    async def start(self):
        if self._is_running:
            return
        self._is_running = True

        for _ in range(self.concurrency):
            self._worker_tasks.append(asyncio.create_task(self._process_queue()))

        logger.info(f"Track worker {self.worker_id} started (concurrency={self.concurrency})")

    async def stop(self):
        self._is_running = False
        for _ in range(self.concurrency):
            await self.download_queue.put(None)
        for t in self._worker_tasks:
            t.cancel()
        await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks.clear()
        logger.info(f"Track worker {self.worker_id} stopped")

    # Credits ------------------------------------------------------------------
    async def _confirm_credit_reservation(self, reservation_id: str):
        try:
            from database import get_db
            from credit_reservation import CreditReservationService
            db = next(get_db())
            try:
                ok, msg = CreditReservationService.confirm_reservation(db, reservation_id)
                if ok:
                    logger.info(f"Credit confirmed for {reservation_id}: {msg}")
                else:
                    logger.error(f"Failed to confirm credit for {reservation_id}: {msg}")
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Error confirming credit: {e}")

    async def _release_credit_on_failure(self, reservation_id: str):
        try:
            from database import get_db
            from credit_reservation import CreditReservationService
            db = next(get_db())
            try:
                ok, msg = CreditReservationService.release_reservation(db, reservation_id, "failed")
                if ok:
                    logger.info(f"Credit released for {reservation_id}: {msg}")
                else:
                    logger.error(f"Failed to release credit for {reservation_id}: {msg}")
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Error releasing credit: {e}")

    # Queue loop ---------------------------------------------------------------
    async def _process_queue(self):
        while self._is_running:
            try:
                task = await asyncio.wait_for(self.download_queue.get(), timeout=5.0)
                if task is None:
                    break

                download_id = task.get("download_id")
                self.current_task_start = time.time()
                self.last_progress_time = self.current_task_start
                self.current_download_id = download_id
                self._current_task = task

                try:
                    await asyncio.wait_for(self._process_download(task), timeout=self.task_timeout)
                except asyncio.TimeoutError:
                    logger.error(f"Task timeout for {download_id} after {self.task_timeout}s")
                    await self._handle_timeout_error(task)
                finally:
                    self.current_task_start = None
                    self.last_progress_time = None
                    self.current_download_id = None
                    self._current_task = None

                self.download_queue.task_done()

            except asyncio.TimeoutError:
                await self._check_for_stuck_state()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker {self.worker_id} loop error: {e}")
                await asyncio.sleep(0.05)

    # S4 helpers ---------------------------------------------------------------
    async def _get_s4_object_size(self, object_key: str) -> Optional[int]:
        try:
            from mega_s4_client import mega_s4_client
            if not mega_s4_client._started:
                await mega_s4_client.start()
            objects = await mega_s4_client.list_objects(prefix=object_key, max_keys=1)
            for obj in objects:
                if obj["key"] == object_key:
                    logger.info(f"S4 object {object_key} size: {obj['size']} bytes")
                    return obj["size"]
            logger.error(f"S4 object not found: {object_key}")
            return None
        except Exception as e:
            logger.error(f"S4 size error: {e}")
            return None

    async def _download_from_s4_with_progress(
        self, object_key: str, file_path: Path, expected_size: Optional[int], download_id: str
    ) -> int:
        response = None
        try:
            from mega_s4_client import mega_s4_client
            if not mega_s4_client._started:
                await mega_s4_client.start()

            logger.info(f"Starting S4 download: {object_key} -> {file_path}")
            response = await asyncio.wait_for(mega_s4_client.download_file_stream(object_key), timeout=60.0)
            if not response:
                raise Exception(f"Failed to start S4 download for {object_key}")
            if response.status != 200:
                raise Exception(f"S4 status {response.status}: {await response.text()}")

            downloaded = 0
            start = time.time()
            last_update_t = start
            last_size = 0
            last_progress_t = time.time()

            with open(file_path, "wb") as f:
                while True:
                    now = time.time()
                    if now - last_progress_t > self.progress_timeout:
                        raise Exception(f"Download stalled > {self.progress_timeout}s")

                    try:
                        chunk = await asyncio.wait_for(response.content.read(8192), timeout=self.chunk_timeout)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if downloaded > last_size:
                            self.last_progress_time = now
                            last_progress_t = now

                        if (downloaded - last_size >= 1024 * 1024) or (now - last_update_t >= 0.5):
                            dt = max(now - last_update_t, 1e-3)
                            speed = (downloaded - last_size) / (1024 * 1024 * dt)  # MB/s
                            pct = (downloaded / expected_size * 100) if expected_size else 0.0

                            async with track_download_manager._lock:
                                track_download_manager.active_downloads[download_id] = {
                                    "status": "processing",
                                    "progress": pct,
                                    "message": "Downloading from S4",
                                    "downloaded": downloaded,
                                    "total_size": expected_size,
                                    "speed": f"{speed:.2f} MB/s",
                                    "voice": track_download_manager.active_downloads.get(download_id, {}).get("voice"),
                                    "track_type": track_download_manager.active_downloads.get(download_id, {}).get("track_type", "audio"),
                                }

                            last_size = downloaded
                            last_update_t = now

                    except asyncio.TimeoutError:
                        raise Exception("Download timeout - connection lost")

            if not file_path.exists():
                raise Exception("S4 download failed - file not created")
            final_size = file_path.stat().st_size
            if final_size == 0:
                raise Exception("S4 download failed - file is empty")
            if expected_size and abs(final_size - expected_size) > 1024:
                logger.warning(f"Size mismatch: expected {expected_size}, got {final_size}")

            elapsed = time.time() - start
            avg_speed = final_size / (1024 * 1024 * max(elapsed, 1e-3))
            logger.info(
                f"S4 download OK: file={object_key}, size={final_size:,} bytes, "
                f"time={elapsed:.1f}s, avg={avg_speed:.2f} MB/s"
            )
            return final_size

        except Exception as e:
            logger.error(f"S4 download error: {e}")
            if file_path.exists():
                try:
                    file_path.unlink()
                    logger.info(f"Removed partial file: {file_path}")
                except Exception as ce:
                    logger.error(f"Cleanup error: {ce}")
            raise
        finally:
            if response and hasattr(response, "close"):
                try:
                    await response.close()
                except Exception as ce:
                    logger.error(f"Response close error: {ce}")

    # Timeouts / stuck ---------------------------------------------------------
    async def _handle_timeout_error(self, task: Dict):
        try:
            download_id = task.get("download_id")
            should_charge = task.get("should_charge", False)
            reservation_id = task.get("reservation_id")
            user_id = task.get("user_id")
            track_id = task.get("track_id")
            voice = task.get("voice")

            # preserve voice/track_type before clearing
            await self._set_download_error(download_id, "Download timeout")

            # credits
            if should_charge and reservation_id:
                await self._release_credit_on_failure(reservation_id)

            # failure history (timeout)
            await self._record_download_history(
                user_id=user_id,
                track_id=track_id,
                creator_id=(task.get("track_info") or {}).get("creator_id"),
                voice=voice,
                status="failure",
                error_message="Download timeout",
            )

            # remove temp file if any
            file_path = self.temp_dir / f"{download_id}.mp3"
            if file_path.exists():
                try:
                    file_path.unlink()
                except Exception as e:
                    logger.error(f"Timeout cleanup error {file_path}: {e}")

            # clear active
            async with track_download_manager._lock:
                track_download_manager.active_downloads.pop(download_id, None)

        except Exception as e:
            logger.error(f"Timeout handler error for {task.get('download_id')}: {e}")

    async def _check_for_stuck_state(self):
        if not self.current_task_start:
            return
        now = time.time()
        if now - self.current_task_start > self.task_timeout:
            logger.warning(f"Worker {self.worker_id} appears stuck on {self.current_download_id}")
            if self._current_task:
                await self._handle_timeout_error(self._current_task)

    # Core process -------------------------------------------------------------
    async def _process_download(self, task: Dict):
        """Process a single track download using S4."""
        download_id = task["download_id"]
        track_info = task["track_info"]
        user_id = task["user_id"]
        track_id = task["track_id"]
        should_charge = task.get("should_charge", False)
        reservation_id = task.get("reservation_id")
        voice = task.get("voice")

        filename = f"{download_id}.mp3"
        file_path = self.temp_dir / filename

        try:
            # mark active
            async with track_download_manager._lock:
                track_download_manager.active_downloads[download_id] = {
                    "status": "processing",
                    "progress": 0,
                    "message": "Starting S4 download",
                    "voice": voice,
                    "track_type": track_info.get("track_type", "audio"),
                }

            # resolve object key
            mega_path = track_info["mega_path"]
            track_type = track_info.get("track_type", "audio")
            if track_type == "tts":
                object_key = mega_path
                logger.info(f"TTS file path: {object_key}")
            else:
                src_name = Path(mega_path).name
                object_key = f"audio/{src_name}"
                logger.info(f"Audio path converted: {mega_path} -> {object_key}")

            # size
            total_size = await self._get_s4_object_size(object_key)
            if not total_size:
                raise Exception(f"Failed to get S4 size: {object_key}")

            # init progress
            async with track_download_manager._lock:
                track_download_manager.active_downloads[download_id].update({
                    "message": "Downloading from S4",
                    "downloaded": 0,
                    "total_size": total_size,
                    "speed": "0 MB/s",
                })

            # download
            final_size = await self._download_from_s4_with_progress(object_key, file_path, total_size, download_id)

            # final progress
            async with track_download_manager._lock:
                track_download_manager.active_downloads[download_id].update({
                    "progress": 100,
                    "message": "Download complete",
                    "downloaded": final_size,
                    "speed": "0 MB/s",
                })

            # credits
            if should_charge and reservation_id:
                try:
                    await self._confirm_credit_reservation(reservation_id)
                except Exception as ce:
                    logger.error(f"Credit confirm error for user {user_id}: {ce}")

            # add to user inventory (and code-level success history)
            try:
                track_title = track_info.get("title", "Unknown Track")
                if track_type == "tts" and voice:
                    voice_name = voice.replace("en-US-", "").replace("Neural", "")
                    safe_title = f"{voice_name} - {track_title}"
                else:
                    safe_title = track_title

                safe_title = "".join(c for c in safe_title if c.isalnum() or c in (" ", "-", "_", "(", ")")).strip() or f"track_{track_id}"

                downloads_dir = Path("/tmp/user_downloads")
                downloads_dir.mkdir(exist_ok=True, parents=True)

                ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                unique_filename = f"{safe_title}_{ts}.mp3"
                persistent_path = downloads_dir / unique_filename

                import shutil
                shutil.copy2(file_path, persistent_path)

                asyncio.create_task(
                    track_download_manager.add_to_my_downloads(
                        user_id=user_id,
                        track_id=track_id,
                        download_path=str(persistent_path),
                        original_filename=f"{safe_title}.mp3",
                        voice=voice,
                        download_id=download_id,
                    )
                )
            except Exception as e:
                logger.error(f"Add to user downloads failed: {e}", exc_info=True)

            # cleanup temp later
            asyncio.create_task(self._cleanup_file(file_path))

            # mark complete (preserve voice/track_type)
            await self._set_download_complete(download_id, str(file_path))

        except Exception as e:
            logger.error(f"S4 processing error for {track_info.get('title', 'Unknown')} ({download_id}): {e}")

            if should_charge and reservation_id:
                await self._release_credit_on_failure(reservation_id)

            # failure history
            await self._record_download_history(
                user_id=user_id,
                track_id=track_id,
                creator_id=track_info.get("creator_id"),
                voice=voice,
                status="failure",
                error_message=str(e),
            )

            # remove temp
            if file_path.exists():
                try:
                    file_path.unlink()
                except Exception as ce:
                    logger.error(f"Temp cleanup error {file_path}: {ce}")

            # mark error (preserve voice/track_type)
            await self._set_download_error(download_id, str(e))

        finally:
            async with track_download_manager._lock:
                track_download_manager.active_downloads.pop(download_id, None)

    # File cleanup / status ----------------------------------------------------
    async def _cleanup_file(self, file_path: Path, delay: int = 10):
        await asyncio.sleep(delay)
        try:
            if file_path.exists():
                file_path.unlink()
                logger.info(f"Cleaned temp file: {file_path}")
        except Exception as e:
            logger.error(f"Cleanup error {file_path}: {e}")

    async def _set_download_complete(self, download_id: str, file_path: str):
        voice = None
        track_type = "audio"
        async with track_download_manager._lock:
            active = track_download_manager.active_downloads.get(download_id, {})
            voice = active.get("voice")
            track_type = active.get("track_type", "audio")

        track_download_manager.completed_downloads[download_id] = {
            "status": "completed",
            "file_path": file_path,
            "completed_at": datetime.now(timezone.utc),
            "voice": voice,
            "track_type": track_type,
        }

    async def _set_download_error(self, download_id: str, error: str):
        voice = None
        track_type = "audio"
        async with track_download_manager._lock:
            active = track_download_manager.active_downloads.get(download_id, {})
            voice = active.get("voice")
            track_type = active.get("track_type", "audio")

        track_download_manager.completed_downloads[download_id] = {
            "status": "error",
            "error": error,
            "completed_at": datetime.now(timezone.utc),
            "voice": voice,
            "track_type": track_type,
        }

    # Inventory + success history (code-level trigger) -------------------------
    async def add_to_my_downloads(
        self,
        user_id: int,
        track_id: str,
        download_path: str,
        original_filename: str,
        voice: Optional[str] = None,
        download_id: Optional[str] = None,
    ):
        """
        Add a completed track download to user's inventory (TTL ~24h),
        and log SUCCESS to download_history. Best-effort for history;
        inventory commit is never blocked.
        """
        try:
            from database import get_db
            from sqlalchemy import text
            db = next(get_db())
            try:
                # capacity / TTL cleanup
                current_count = (db.execute(text("""
                    SELECT COUNT(id) FROM public.user_downloads
                    WHERE user_id = :uid AND is_available = true
                """), {"uid": user_id}).scalar() or 0)

                max_downloads = 10
                if current_count >= max_downloads:
                    rows = db.execute(text("""
                        SELECT id, download_path
                        FROM public.user_downloads
                        WHERE user_id = :uid AND is_available = true
                        ORDER BY downloaded_at ASC
                        LIMIT :excess
                    """), {"uid": user_id, "excess": current_count - (max_downloads - 1)}).fetchall()

                    import os
                    for r in rows:
                        p = r.download_path
                        if p and os.path.exists(p):
                            try:
                                os.remove(p)
                                logger.info(f"Deleted old file: {p}")
                            except OSError as oe:
                                logger.error(f"Old file delete error {p}: {oe}")
                        db.execute(text("UPDATE public.user_downloads SET is_available=false WHERE id=:id"), {"id": r.id})
                    db.commit()

                now_utc = datetime.now(timezone.utc)
                expiry = now_utc + timedelta(hours=24)

                # insert inventory
                new_ud_id = db.execute(text("""
                    INSERT INTO public.user_downloads
                        (user_id, download_type, track_id, download_path,
                         original_filename, voice_id, is_available, expires_at, downloaded_at)
                    VALUES
                        (:uid, 'track'::downloadtype, :tid, :path,
                         :name, :voice, true, :exp, :now)
                    RETURNING id
                """), {
                    "uid": user_id,
                    "tid": track_id,
                    "path": download_path,
                    "name": original_filename,
                    "voice": voice,
                    "exp": expiry,
                    "now": now_utc,
                }).scalar()
                db.commit()

                if new_ud_id:
                    logger.info(f"user_downloads id={new_ud_id} created for user={user_id}")
                else:
                    logger.error(f"user_downloads insert failed for user={user_id}")

                # success history (code-level trigger)
                try:
                    # resolve creator_id
                    creator_row = db.execute(text("""
                        SELECT created_by_id AS cid FROM public.tracks
                        WHERE id = :tid LIMIT 1
                    """), {"tid": track_id}).fetchone()
                    if creator_row and creator_row.cid is not None:
                        creator_id = creator_row.cid
                    else:
                        u = db.execute(text("""
                            SELECT id AS uid, is_creator AS ic, created_by AS cb
                            FROM public.users WHERE id=:uid LIMIT 1
                        """), {"uid": user_id}).fetchone()
                        creator_id = u.uid if (u and (u.ic is True)) else (u.cb if u else user_id)

                    db.execute(text("""
                        INSERT INTO public.download_history
                            (user_id, creator_id, download_type, entity_id,
                             voice_id, status, downloaded_at)
                        VALUES
                            (:uid, :cid, 'track', :tid, :voice, 'success', :now)
                    """), {
                        "uid": user_id,
                        "cid": creator_id,
                        "tid": str(track_id),
                        "voice": voice,
                        "now": now_utc,
                    })
                    db.commit()
                except Exception as hist_err:
                    db.rollback()
                    logger.warning(f"[history] success log failed (user={user_id}, track={track_id}): {hist_err}")

            finally:
                db.close()
        except Exception as e:
            logger.error(f"user_downloads/history error: {e}", exc_info=True)

    # Failure history (only) ---------------------------------------------------
    async def _record_download_history(
        self,
        user_id: int,
        track_id: str,
        creator_id: Optional[int],
        voice: Optional[str],
        status: str,
        error_message: Optional[str] = None,
    ):
        """Record a failure (or other non-success) in download_history."""
        try:
            from database import get_db
            from models import DownloadHistory, Track, User
            db = next(get_db())
            try:
                if not creator_id:
                    track = db.query(Track).filter(Track.id == track_id).first()
                    if track and getattr(track, "creator_id", None):
                        creator_id = track.creator_id
                    else:
                        user = db.query(User).filter(User.id == user_id).first()
                        if user:
                            creator_id = user.id if getattr(user, "is_creator", False) else getattr(user, "created_by", None)

                if not creator_id:
                    logger.warning(f"Skip failure history (no creator_id): user={user_id}, track={track_id}")
                    return

                DownloadHistory.record_download(
                    db=db,
                    user_id=user_id,
                    creator_id=creator_id,
                    download_type="track",
                    entity_id=str(track_id),
                    voice_id=voice,
                    status=status,
                    error_message=error_message,
                )
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"Failure history write error: {e}")


# ------------------------------------------------------------------------------
class TrackDownloadManager:
    def __init__(self, max_workers: int = None, concurrency: int = None):
        self.download_queue = asyncio.Queue()

        # MIGRATED TO REDIS: Use RedisDownloadState for cross-container state
        from redis_state.state.download import get_track_download_state
        self._download_state = get_track_download_state()

        # Expose Redis-backed state as properties for backward compatibility
        self.active_downloads = self._download_state.active_downloads
        self.completed_downloads = self._download_state.completed_downloads

        self.workers = []
        self.max_workers = max_workers or worker_config.worker_configs["track_downloaders"]["max_workers"]
        self.concurrency = concurrency or 5
        self._lock = asyncio.Lock()
        self._is_running = False
        self._scaling_task = None
        self._worker_restart_task = None

        self.temp_dir = Path("/tmp/mega_downloads/tracks")
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Track download manager initialized (Redis-backed). Max workers={self.max_workers}, Concurrency={self.concurrency}, container={self._download_state.container_id}")

    def _count_user_actives_locked(self, user_id: int) -> int:
        prefix = f"track_{user_id}_"
        return sum(
            1
            for k, v in self.active_downloads.items()
            if isinstance(k, str) and k.startswith(prefix) and (v or {}).get("status") in ("queued", "processing")
        )

    async def _start_workers(self, count: int):
        current = len(self.workers)
        if count > current:
            for i in range(current, count):
                w = TrackDownloadWorker(worker_id=f"track_{i}", download_queue=self.download_queue, db_factory=None, concurrency=self.concurrency)
                self.workers.append(w)
                await w.start()
                worker_config.register_worker("track_downloaders")
                logger.info(f"Started worker {i} (concurrency={self.concurrency})")
        elif count < current:
            to_remove = self.workers[count:]
            self.workers = self.workers[:count]
            for w in to_remove:
                try:
                    await w.stop()
                    worker_config.unregister_worker("track_downloaders")
                    logger.info("Stopped worker (scale down)")
                except Exception as e:
                    logger.error(f"Error stopping worker: {e}")

    async def _monitor_scaling(self):
        while self._is_running:
            try:
                async with self._lock:
                    qsize = self.download_queue.qsize()
                worker_config.update_queue_length("track_downloaders", qsize)
                current = len(self.workers)
                needed = worker_config.get_worker_count("track_downloaders")
                if needed != current:
                    logger.info(f"Scaling workers: {current} -> {needed} (queue={qsize}, max={self.max_workers})")
                    await self._start_workers(needed)
            except Exception as e:
                logger.error(f"Scaling monitor error: {e}")
            await asyncio.sleep(worker_config.scale_check_interval)

    async def start(self):
        if self._is_running:
            return
        self._is_running = True

        try:
            from mega_s4_client import mega_s4_client
            await mega_s4_client.start()
            logger.info("S4 client initialized")
        except Exception as e:
            logger.error(f"S4 init failed: {e}")
            raise

        await self._start_workers(1)
        self._worker_restart_task = asyncio.create_task(self._monitor_worker_restarts())
        self._scaling_task = asyncio.create_task(self._monitor_scaling())

        logger.info(
            "Track download manager running: "
            f"initial=1, max={self.max_workers}, per-worker={self.concurrency}, "
            f"target_queue={worker_config.worker_configs['track_downloaders']['target_queue_per_worker']}"
        )

    async def stop(self):
        if not self._is_running:
            return
        self._is_running = False

        for t in (self._worker_restart_task, self._scaling_task):
            if t:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

        for w in self.workers:
            try:
                await w.stop()
                worker_config.unregister_worker("track_downloaders")
            except Exception as e:
                logger.error(f"Stop worker error: {e}")

        try:
            from mega_s4_client import mega_s4_client
            await mega_s4_client.close()
            logger.info("S4 client closed")
        except Exception as e:
            logger.error(f"S4 close error: {e}")

        wc = len(self.workers)
        self.workers.clear()
        logger.info(f"Track download manager stopped (workers cleaned={wc})")

    async def _monitor_worker_restarts(self):
        while self._is_running:
            try:
                now = time.time()
                for i, w in enumerate(self.workers):
                    if w.current_task_start and (now - w.current_task_start > w.task_timeout + 60):
                        logger.warning(f"Restarting stuck worker {w.worker_id}")
                        try:
                            await w.stop()
                            nw = TrackDownloadWorker(worker_id=f"track_{i}_restarted_{int(now)}", download_queue=self.download_queue, db_factory=None, concurrency=self.concurrency)
                            self.workers[i] = nw
                            await nw.start()
                            worker_config.register_worker("track_downloaders")
                            logger.info(f"Worker restarted as {nw.worker_id}")
                        except Exception as e:
                            logger.error(f"Worker restart failed: {e}")
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker restart monitor error: {e}")
                await asyncio.sleep(60)

    async def add_to_my_downloads(self, *args, **kwargs):
        # Implemented on the worker; call through for convenience
        return await self.workers[0].add_to_my_downloads(*args, **kwargs)

    # Queue / status -----------------------------------------------------------
    async def queue_download(
        self,
        user_id: int,
        track_id: str,
        track_info: dict,
        should_charge: bool = True,
        reservation_id: Optional[str] = None,
        voice: Optional[str] = None,
        is_creator: bool = False,
    ) -> str:
        if voice and track_info.get("track_type") == "tts":
            download_id = f"track_{user_id}_{track_id}_{voice}"
        else:
            download_id = f"track_{user_id}_{track_id}"

        async with self._lock:
            if download_id in self.active_downloads or download_id in self.completed_downloads:
                return download_id

            if not is_creator:
                active = self._count_user_actives_locked(user_id)
                if active >= MAX_CONCURRENT_DOWNLOADS_PER_USER:
                    raise ConcurrentLimitExceeded(MAX_CONCURRENT_DOWNLOADS_PER_USER, active)

            self.active_downloads[download_id] = {
                "status": "queued",
                "progress": 0,
                "queued_at": datetime.now(timezone.utc),
                "track_info": track_info,
                "voice": voice,
                "track_type": track_info.get("track_type", "audio"),
            }

            await self.download_queue.put({
                "download_id": download_id,
                "user_id": user_id,
                "track_id": track_id,
                "track_info": track_info,
                "should_charge": should_charge,
                "reservation_id": reservation_id,
                "voice": voice,
                "queued_at": datetime.now(timezone.utc),
                "status": "queued",
            })

        return download_id

    async def get_download_status(self, download_id: str) -> Dict:
        if download_id in self.completed_downloads:
            st = self.completed_downloads[download_id]
            if st["status"] == "completed":
                return {
                    "status": "completed",
                    "progress": 100,
                    "download_path": st["file_path"],
                    "voice": st.get("voice"),
                    "track_type": st.get("track_type", "audio"),
                }
            else:
                return {
                    "status": "error",
                    "progress": 0,
                    "error": st.get("error", "Unknown error"),
                    "voice": st.get("voice"),
                    "track_type": st.get("track_type", "audio"),
                }

        async with self._lock:
            if download_id in self.active_downloads:
                s = self.active_downloads[download_id]
                return {
                    "status": "processing" if s.get("status") != "queued" else "queued",
                    "progress": s.get("progress", 0),
                    "message": s.get("message", "Download in progress"),
                    "speed": s.get("speed", "0 MB/s"),
                    "downloaded": s.get("downloaded", 0),
                    "total_size": s.get("total_size", 0),
                    "voice": s.get("voice"),
                    "track_type": s.get("track_type", "audio"),
                }

            queue_list = list(self.download_queue._queue)
            for i, task in enumerate(queue_list):
                if isinstance(task, dict) and task.get("download_id") == download_id:
                    return {
                        "status": "queued",
                        "progress": 0,
                        "queue_position": i + 1,
                        "message": f"Waiting in queue (Position: {i + 1})",
                        "voice": None,
                        "track_type": "audio",
                    }

        return {"status": "not_found", "progress": 0, "message": "Download not found", "voice": None, "track_type": "audio"}


# Singleton manager ------------------------------------------------------------
track_download_manager = TrackDownloadManager()
