# core.album_download_workers.py
import asyncio
import logging
import time
from enum import Enum
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from contextlib import contextmanager
from typing import Callable, Dict, List, Optional, Tuple

import io
import zipfile
from sqlalchemy import text

from database import SessionLocal
from worker_config import worker_config

logger = logging.getLogger(__name__)

# Global lock to protect direct queue accesses (for thread-safe queue position calculations)
download_queue_lock = asyncio.Lock()

# --- CONCURRENCY LIMIT (simple + local) --------------------------------------
MAX_CONCURRENT_ALBUM_DOWNLOADS_PER_USER = 1  # Only 1 concurrent album download per user

def _valid_zip(path: Path) -> bool:
    """Check if ZIP file exists and is valid."""
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        import zipfile
        with zipfile.ZipFile(path, 'r') as zf:
            return len(zf.namelist()) > 0
    except Exception:
        return False



class ConcurrentLimitExceeded(Exception):
    """Raised when a user exceeds the concurrent album download limit."""
    def __init__(self, limit: int, active: int):
        self.limit = limit
        self.active = active
        super().__init__("concurrent_limit_exceeded")


# ------------------------------------------------------------------------------
class ProgressTrackingFile(io.BufferedIOBase):
    """File-like object that tracks bytes written with buffered updates."""
    def __init__(self, file_obj, callback):
        self._file_obj = file_obj
        self._callback = callback
        self._bytes_written = 0
        self._closed = False
        self._last_update = time.time()
        self._write_buffer = 0
        self._update_threshold = 1024 * 1024  # 1MB threshold
        self._time_threshold = 0.1            # 100ms threshold

    def write(self, data: bytes):
        if self._closed:
            raise ValueError("I/O operation on closed file")
        chunk_size = len(data)
        self._bytes_written += chunk_size
        self._write_buffer += chunk_size

        now = time.time()
        if self._write_buffer >= self._update_threshold or (now - self._last_update) >= self._time_threshold:
            try:
                if self._callback:
                    self._callback(self._bytes_written, self._write_buffer)
            except Exception as e:
                logger.error(f"Error in progress callback: {e}")
            self._write_buffer = 0
            self._last_update = now

        return self._file_obj.write(data)

    def flush(self):
        if not self._closed and self._write_buffer > 0:
            try:
                if self._callback:
                    self._callback(self._bytes_written, self._write_buffer)
            except Exception as e:
                logger.error(f"Error in progress callback: {e}")
            self._write_buffer = 0
            self._last_update = time.time()
        if not self._closed:
            return self._file_obj.flush()

    def close(self):
        if not self._closed:
            try:
                self.flush()
                if hasattr(self._file_obj, "close"):
                    self._file_obj.close()
            finally:
                self._closed = True

    # Minimal API for zipfile to be happy:
    def readable(self): return False
    def writable(self): return True
    def seekable(self): return True
    @property
    def closed(self): return self._closed


class DownloadStage(Enum):
    QUEUED = "queued"
    INITIALIZATION = "initialization"
    PREPARATION = "preparation"
    DOWNLOADING = "downloading"
    COMPRESSION = "compression"
    STREAMING = "streaming"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class DownloadStatus:
    stage: DownloadStage
    progress: float
    stage_detail: str
    track_number: Optional[int] = None
    total_tracks: Optional[int] = None
    download_path: Optional[str] = None
    error: Optional[str] = None
    rate: Optional[float] = None  # MB/s
    processed_size: Optional[int] = None
    total_size: Optional[int] = None
    queue_position: Optional[int] = None
    queued_at: Optional[datetime] = None
    timestamp: Optional[datetime] = None


# ------------------------------------------------------------------------------
class DownloadWorker:
    """Worker that handles album downloads (multi-track -> ZIP) with S4."""
    def __init__(
        self,
        worker_id: str,
        download_queue: asyncio.Queue,
        status_queue: asyncio.Queue,
        db_factory: Optional[Callable[[], object]] = None,
        concurrency: int = 2,
    ):
        self.worker_id = worker_id
        self.download_queue = download_queue
        self.status_queue = status_queue
        self._db_factory = db_factory or SessionLocal
        self.temp_dir = Path("/tmp/mega_downloads")
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        self._is_running = False
        self._worker_task: Optional[asyncio.Task] = None

        # Per-worker concurrency (how many albums at once in this worker)
        self.semaphore = asyncio.Semaphore(concurrency)

        # Timeouts / tracking
        self.task_timeout = 3600         # 1 hour/album
        self.progress_timeout = 300      # 5 minutes without progress
        self.chunk_timeout = 30          # 30s per chunk
        self.current_task_start: Optional[float] = None
        self.last_progress_time: Optional[float] = None
        self.current_download_id: Optional[str] = None
        self._current_task: Optional[Dict] = None

        # FS lock to serialize writes in this worker
        self._file_lock = asyncio.Lock()

    async def start(self):
        if self._is_running:
            return
        self._is_running = True

        # Initialize S4 client once here
        try:
            from mega_s4_client import mega_s4_client
            if not getattr(mega_s4_client, "_started", False):
                await mega_s4_client.start()
            logger.info("S4 client initialized for album downloads")
        except Exception as e:
            logger.error(f"S4 init failed: {e}")
            raise

        self._worker_task = asyncio.create_task(self._process_queue())
        logger.info(f"Album worker {self.worker_id} started")

    async def stop(self):
        self._is_running = False
        if self._worker_task:
            try:
                await self.download_queue.put(None)
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        logger.info(f"Album worker {self.worker_id} stopped")

    @contextmanager
    def _db_session(self):
        db = self._db_factory()
        try:
            yield db
        finally:
            try:
                db.close()
            except Exception:
                logger.debug("Failed to close session cleanly for %s", self.worker_id)

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
                    logger.error(f"Credit confirm failed for {reservation_id}: {msg}")
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Credit confirm error: {e}")

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
                    logger.error(f"Credit release failed for {reservation_id}: {msg}")
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Credit release error: {e}")

    # Queue loop ---------------------------------------------------------------
    async def _process_queue(self):
        while self._is_running:
            try:
                task = await asyncio.wait_for(self.download_queue.get(), timeout=5.0)
                if task is None:
                    break

                self._current_task = task
                self.current_task_start = time.time()
                self.last_progress_time = self.current_task_start
                self.current_download_id = task.get("download_id")

                async with self.semaphore:
                    try:
                        await asyncio.wait_for(self._process_download(task), timeout=self.task_timeout)
                    except asyncio.TimeoutError:
                        logger.error(f"Album task timeout: {self.current_download_id}")
                        await self._handle_album_timeout()
                    finally:
                        self.current_task_start = None
                        self.last_progress_time = None
                        self.current_download_id = None
                        self._current_task = None

                self.download_queue.task_done()

            except asyncio.TimeoutError:
                await self._check_album_stuck_state()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Album worker loop error: {e}")
                if self._current_task:
                    await self._handle_error(e)
                await asyncio.sleep(0.05)

    # S4 helpers ---------------------------------------------------------------
    async def _get_s4_object_size(self, object_key: str) -> Optional[int]:
        try:
            from mega_s4_client import mega_s4_client
            if not getattr(mega_s4_client, "_started", False):
                await mega_s4_client.start()
            objects = await mega_s4_client.list_objects(prefix=object_key, max_keys=1)
            for obj in objects:
                if obj["key"] == object_key:
                    return obj["size"]
            logger.error(f"S4 object not found: {object_key}")
            return None
        except Exception as e:
            logger.error(f"S4 size error: {e}")
            return None

    async def _get_track_sizes(self, tracks: List[dict]) -> Dict[str, int]:
        """Return {track_id: size} using consistent S4 key rules."""
        sizes: Dict[str, int] = {}
        for info in tracks:
            try:
                track = info["track"]
                mega_path = info["mega_path"]
                # Unify S4 key derivation:
                if track.get("track_type") == "tts" or "/tts-tracks/" in mega_path:
                    object_key = mega_path
                else:
                    object_key = f"audio/{Path(mega_path).name}"
                size = await self._get_s4_object_size(object_key)
                if size:
                    sizes[track["id"]] = size
                else:
                    logger.error(f"No S4 size for track {track.get('title')} (key: {object_key})")
            except Exception as e:
                logger.error(f"Track size error: {e}")
        if not sizes:
            raise Exception("Failed to obtain any track sizes from S4")
        return sizes

    async def _download_track_s4(
        self,
        mega_path: str,
        file_path: Path,
        track_num: int,
        total_tracks: int,
        download_id: str,
        processed_bytes: int,
        total_size: int,
        expected_size: Optional[int] = None,
    ) -> int:
        """Download a single track; updates overall album progress."""
        response = None
        try:
            from mega_s4_client import mega_s4_client
            # Derive S4 key
            if "/tts-tracks/" in mega_path:
                object_key = mega_path
            else:
                object_key = f"audio/{Path(mega_path).name}"

            if not getattr(mega_s4_client, "_started", False):
                await mega_s4_client.start()

            response = await asyncio.wait_for(mega_s4_client.download_file_stream(object_key), timeout=60.0)
            if not response or response.status != 200:
                err = await response.text() if response else "No response"
                raise Exception(f"S4 download failed: {err}")

            downloaded = 0
            start = time.time()
            last_update_t = start
            last_size = 0

            async with self._file_lock:
                if file_path.exists():
                    file_path.unlink()

            with open(file_path, "wb") as f:
                while True:
                    now = time.time()
                    if now - (self.last_progress_time or now) > self.progress_timeout:
                        raise Exception(f"Track stalled > {self.progress_timeout}s")

                    try:
                        chunk = await asyncio.wait_for(response.content.read(8192), timeout=self.chunk_timeout)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)

                        if downloaded > last_size:
                            self.last_progress_time = now

                        if (downloaded - last_size >= 1024 * 1024) or (now - last_update_t >= 0.5):
                            dt = max(now - last_update_t, 1e-3)
                            speed = (downloaded - last_size) / (1024 * 1024 * dt)  # MB/s
                            file_pct = (downloaded / expected_size * 100) if expected_size else 0.0
                            overall_current = processed_bytes + downloaded
                            overall_pct = (overall_current / total_size * 100) if total_size else 0.0

                            await self._update_status(
                                download_id,
                                DownloadStage.DOWNLOADING,
                                {
                                    "stage_detail": f"Downloading track {track_num}/{total_tracks}",
                                    "track_number": track_num,
                                    "total_tracks": total_tracks,
                                    "processed_size": overall_current,
                                    "total_size": total_size,
                                    "rate": speed,
                                    "progress": overall_pct,
                                    "file_progress": file_pct,
                                },
                            )

                            last_size = downloaded
                            last_update_t = now

                    except asyncio.TimeoutError:
                        raise Exception("Download timeout - connection lost")

            # Verify
            async with self._file_lock:
                if not file_path.exists():
                    raise Exception("File not created")
                final_size = file_path.stat().st_size
                if final_size == 0:
                    raise Exception("File size 0")

            if expected_size and abs(final_size - expected_size) > 1024:
                logger.warning(f"Size mismatch: expected {expected_size}, got {final_size}")

            return final_size

        except Exception as e:
            logger.error(f"S4 track error #{track_num}: {e}")
            async with self._file_lock:
                if file_path.exists():
                    try:
                        file_path.unlink()
                    except Exception as ce:
                        logger.error(f"Partial cleanup failed {file_path}: {ce}")
            raise
        finally:
            if response and hasattr(response, "close"):
                try:
                    if asyncio.iscoroutinefunction(response.close):
                        await response.close()
                    else:
                        response.close()
                except Exception as ce:
                    logger.error(f"Response close error: {ce}")

    # History (code-level) -----------------------------------------------------
    async def _record_history(
        self,
        user_id: int,
        download_type: str,  # 'album' | 'track'
        entity_id: str,
        status: str,         # 'success' | 'failure'
        error_message: Optional[str] = None,
        voice_id: Optional[str] = None,
    ):
        """
        Minimal, raw-SQL history writer (avoids ORM coupling).
        Resolves creator_id via albums/tracks; falls back to user/created_by.
        """
        try:
            with self._db_session() as db:
                try:
                    creator_id = None
                    if download_type == "album":
                        row = db.execute(
                            text("SELECT created_by_id FROM public.albums WHERE id = :id LIMIT 1"),
                            {"id": entity_id},
                        ).fetchone()
                        if row and getattr(row, "created_by_id", None) is not None:
                            creator_id = row.created_by_id
                    else:
                        row = db.execute(
                            text("SELECT creator_id FROM public.tracks WHERE id::text = :id::text LIMIT 1"),
                            {"id": entity_id},
                        ).fetchone()
                        if row and getattr(row, "creator_id", None) is not None:
                            creator_id = row.creator_id

                    if creator_id is None:
                        u = db.execute(
                            text("SELECT id AS uid, is_creator AS ic, created_by AS cb FROM public.users WHERE id=:uid"),
                            {"uid": user_id},
                        ).fetchone()
                        creator_id = (u.uid if (u and (u.ic is True)) else (u.cb if u else user_id))

                    db.execute(
                        text("""
                            INSERT INTO public.download_history
                                (user_id, creator_id, download_type, entity_id, voice_id, status, error_message, downloaded_at)
                            VALUES
                                (:user_id, :creator_id, :dtype, :entity_id, :voice_id, :status, :error_message, :now)
                        """),
                        {
                            "user_id": user_id,
                            "creator_id": creator_id,
                            "dtype": download_type,
                            "entity_id": str(entity_id),
                            "voice_id": voice_id,
                            "status": status,
                            "error_message": error_message,
                            "now": datetime.now(timezone.utc),
                        },
                    )
                    db.commit()
                except Exception:
                    db.rollback()
                    raise
        except Exception as e:
            logger.warning(f"[history] write failed ({download_type}/{status}): {e}")

    # ZIP creation -------------------------------------------------------------
    async def _create_zip(self, files: List[Tuple[Path, str]], zip_path: Path, download_id: str) -> int:
        """Create a ZIP from files with progress reporting."""
        logger.info(f"Creating ZIP at {zip_path}")
        write_complete = False
        total_size = sum(p.stat().st_size for p, _ in files) or 0
        total_mb = total_size / (1024 * 1024)
        total_files = len(files)

        await self._update_status(
            download_id,
            DownloadStage.COMPRESSION,
            {
                "stage_detail": "Starting compression...",
                "progress": 0,
                "processed_size": 0,
                "total_size": total_size,
                "files_processed": 0,
                "total_files": total_files,
            },
        )

        zip_file = open(zip_path, "wb")
        bytes_written = 0
        last_rate = 0.0
        last_update_time = time.time()

        def progress_callback(written_bytes: int, chunk_size: int):
            nonlocal bytes_written, last_rate, last_update_time, write_complete
            if write_complete:
                return
            bytes_written = written_bytes
            now = time.time()
            dt = now - last_update_time
            if chunk_size and dt > 0:
                last_rate = chunk_size / (1024 * 1024 * dt)
            last_update_time = now
            progress = (bytes_written / total_size * 100) if total_size else 0
            asyncio.create_task(
                self._update_status(
                    download_id,
                    DownloadStage.COMPRESSION,
                    {
                        "stage_detail": f"Writing ZIP: {bytes_written/(1024*1024):.1f}/{total_mb:.1f} MB",
                        "progress": progress,
                        "processed_size": bytes_written,
                        "total_size": total_size,
                        "rate": last_rate,
                    },
                )
            )

        tracking_file = ProgressTrackingFile(zip_file, progress_callback)

        try:
            with zipfile.ZipFile(tracking_file, mode="w", compression=zipfile.ZIP_STORED) as zipf:
                for idx, (file_path, title) in enumerate(files, start=1):
                    if not file_path.exists() or file_path.stat().st_size == 0:
                        raise Exception(f"Bad file in ZIP: {file_path}")

                    await self._update_status(
                        download_id,
                        DownloadStage.COMPRESSION,
                        {
                            "stage_detail": f"Adding: {title}",
                            "current_file": title,
                            "files_processed": idx,
                            "total_files": total_files,
                            "processed_size": bytes_written,
                            "total_size": total_size,
                        },
                    )

                    zipf.write(str(file_path), f"{title}.mp3")

            write_complete = True
            tracking_file.close()

            if not zip_path.exists():
                raise Exception("ZIP not created")
            zip_size = zip_path.stat().st_size
            if zip_size == 0:
                raise Exception("ZIP empty")

            await self._update_status(
                download_id,
                DownloadStage.COMPRESSION,
                {
                    "stage_detail": "ZIP complete",
                    "progress": 100,
                    "processed_size": zip_size,
                    "total_size": zip_size,
                },
            )
            await asyncio.sleep(0.1)
            return zip_size

        except Exception as e:
            logger.error(f"ZIP error: {e}")
            if zip_path.exists():
                try:
                    zip_path.unlink()
                except Exception as ce:
                    logger.error(f"ZIP cleanup failed: {ce}")
            raise

    # Status / housekeeping ----------------------------------------------------
    async def _update_status(self, download_id: str, stage: DownloadStage, status_info: Dict):
        logger.info(f"[{download_id}] {stage.value} - {status_info.get('stage_detail', '')}")

        queue_position = None
        if stage == DownloadStage.QUEUED:
            async with download_queue_lock:
                queue_list = list(self.download_queue._queue)
                for i, task in enumerate(queue_list):
                    if isinstance(task, dict) and task.get("download_id") == download_id:
                        queue_position = i + 1
                        break
            status_info["queue_position"] = queue_position

        status = DownloadStatus(
            stage=stage,
            progress=float(status_info.get("progress", 0)),
            stage_detail=status_info.get("stage_detail", ""),
            track_number=status_info.get("track_number"),
            total_tracks=status_info.get("total_tracks"),
            download_path=status_info.get("download_path"),
            error=status_info.get("error"),
            rate=status_info.get("rate"),
            processed_size=status_info.get("processed_size"),
            total_size=status_info.get("total_size"),
            queue_position=queue_position,
            queued_at=datetime.now(timezone.utc) if stage == DownloadStage.QUEUED else None,
            timestamp=datetime.now(timezone.utc),
        )

        await self.status_queue.put(
            {
                "download_id": download_id,
                "worker_id": self.worker_id,
                "status": stage.value,
                "stage": stage.value,
                **asdict(status),
            }
        )

    async def _cleanup_files(self, directory: Path):
        """Recursively delete directory contents, then directory."""
        try:
            async with self._file_lock:
                if not directory.exists():
                    return
                for item in directory.iterdir():
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        await self._cleanup_files(item)
                        if item.exists():
                            item.rmdir()
                if directory.exists():
                    directory.rmdir()
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

    async def _handle_error(self, error: Exception):
        if self._current_task:
            await self._update_status(
                self._current_task.get("download_id"),
                DownloadStage.ERROR,
                {"error": str(error), "stage_detail": f"Error: {error}"},
            )

    async def _handle_album_timeout(self):
        if not self._current_task:
            return
        should_charge = self._current_task.get("should_charge", False)
        reservation_id = self._current_task.get("reservation_id")
        user_id = self._current_task.get("user_id")
        album_id = self._current_task.get("album_id")

        if should_charge and reservation_id:
            try:
                await self._release_credit_on_failure(reservation_id)
            except Exception as e:
                logger.error(f"Credit release on timeout failed: {e}")

        await self._record_history(user_id, "album", album_id, "failure", error_message="Album download timeout")

        await self._update_status(
            self._current_task.get("download_id"),
            DownloadStage.ERROR,
            {"error": "Album download timeout", "stage_detail": "Download timed out"},
        )

    async def _check_album_stuck_state(self):
        if not self.current_task_start:
            return
        now = time.time()
        if now - self.current_task_start > self.task_timeout:
            logger.warning(f"Album worker {self.worker_id} appears stuck")
            await self._handle_album_timeout()

    # Core process -------------------------------------------------------------
    async def _process_download(self, task: Dict):
        """
        Download album: per-track S4 -> zip -> persist -> user_downloads + history.
        """
        download_id = task["download_id"]
        user_id = task["user_id"]
        album_id = task["album_id"]
        tracks = task["tracks"]
        should_charge = task.get("should_charge", True)
        reservation_id = task.get("reservation_id")

        album_dir = self.temp_dir / download_id
        album_dir.mkdir(parents=True, exist_ok=True)

        processed_bytes = 0
        total_size = 0

        try:
            # 1) Sizing
            await self._update_status(download_id, DownloadStage.INITIALIZATION, {"stage_detail": "Sizing tracks..."})
            track_sizes = await self._get_track_sizes(tracks)
            total_size = sum(track_sizes.values())
            if not total_size:
                raise Exception("No track sizes available")

            # 2) Download each track
            downloaded_files: List[Tuple[Path, str]] = []
            total_tracks = len(tracks)
            for idx, info in enumerate(tracks, start=1):
                track = info["track"]
                title = track.get("title", f"Track {idx}")
                file_path = album_dir / f"{title}.mp3"
                expected = track_sizes.get(track["id"], 0)

                try:
                    await self._update_status(
                        download_id,
                        DownloadStage.DOWNLOADING,
                        {
                            "stage_detail": f"Starting {idx}/{total_tracks}",
                            "track_number": idx,
                            "total_tracks": total_tracks,
                            "processed_size": processed_bytes,
                            "total_size": total_size,
                            "progress": (processed_bytes / total_size * 100),
                        },
                    )

                    file_size = await self._download_track_s4(
                        info["mega_path"], file_path, idx, total_tracks, download_id, processed_bytes, total_size, expected
                    )
                    processed_bytes += file_size
                    downloaded_files.append((file_path, title))
                except Exception as e:
                    logger.error(f"Track {title} failed: {e}")
                    # Skip to next track

            if not downloaded_files:
                raise Exception("All tracks failed to download")

            # 3) Create ZIP (single implementation)
            zip_path = self.temp_dir / f"{download_id}.zip"
            await self._update_status(download_id, DownloadStage.COMPRESSION, {"stage_detail": "Preparing ZIP..."})
            await self._create_zip(downloaded_files, zip_path, download_id)

            # 4) Credits (success)
            if should_charge and reservation_id:
                try:
                    await self._confirm_credit_reservation(reservation_id)
                except Exception as e:
                    logger.error(f"Credit confirm failed (user={user_id}): {e}")

            # 5) Persist ZIP to user inventory and log success history
            new_id = None
            try:
                with self._db_session() as db:
                    try:
                        row = db.execute(
                            text("SELECT title FROM public.albums WHERE id = :aid"),
                            {"aid": album_id},
                        ).fetchone()
                        album_title = (row[0] if row else None) or f"album_{album_id}"
                        safe_title = "".join(c for c in album_title if c.isalnum() or c in (" ", "-", "_")).strip() or f"album_{album_id}"

                        downloads_dir = Path("/tmp/user_downloads")
                        downloads_dir.mkdir(parents=True, exist_ok=True)
                        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                        unique_name = f"{safe_title}_{stamp}.zip"
                        persistent_path = downloads_dir / unique_name

                        import shutil
                        shutil.copy2(zip_path, persistent_path)

                        try:
                            if zip_path.exists():
                                zip_path.unlink()
                        except Exception as e:
                            logger.error(f"Could not remove temp ZIP: {e}")

                        album_voice = None
                        for info in tracks:
                            v = (info.get("track") or {}).get("voice")
                            if v:
                                album_voice = v
                                break

                        existing = db.execute(
                            text("""
                                SELECT id, download_path
                                FROM public.user_downloads
                                WHERE user_id=:uid
                                  AND album_id=CAST(:aid AS uuid)
                                  AND download_type='album'::downloadtype
                                  AND (voice_id IS NOT DISTINCT FROM :voice_id)
                                  AND is_available=true
                                LIMIT 1
                            """),
                            {"uid": user_id, "aid": album_id, "voice_id": album_voice},
                        ).fetchone()

                        if existing:
                            try:
                                p = Path(existing.download_path)
                                if p.exists():
                                    p.unlink()
                            except Exception:
                                pass
                            db.execute(text("UPDATE public.user_downloads SET is_available=false WHERE id=:id"), {"id": existing.id})
                            db.commit()

                        expiry = datetime.now(timezone.utc) + timedelta(hours=24)
                        new_id = db.execute(
                            text("""
                                INSERT INTO public.user_downloads
                                    (user_id, download_type, album_id, track_id, download_path,
                                     original_filename, is_available, expires_at, downloaded_at, voice_id)
                                VALUES
                                    (:uid, 'album'::downloadtype, :aid, NULL, :path,
                                     :name, true, :exp, :now, :voice_id)
                                RETURNING id
                            """),
                            {
                                "uid": user_id,
                                "aid": album_id,
                                "path": str(persistent_path),
                                "name": f"{safe_title}.zip",
                                "exp": expiry,
                                "now": datetime.now(timezone.utc),
                                "voice_id": album_voice,
                            },
                        ).scalar()
                        db.commit()

                        await self._record_history(user_id, "album", album_id, "success")
                    except Exception:
                        db.rollback()
                        raise

            except Exception as inv_err:
                logger.error(f"Inventory/history write failed: {inv_err}", exc_info=True)
                raise  # This will trigger the exception handler below

            # 6) Final status with my-downloads path
            await self._update_status(
                download_id,
                DownloadStage.COMPLETED,
                {
                    "download_path": f"/api/my-downloads/{new_id}/file",  # Serve from user_downloads only
                    "stage_detail": "Album ready",
                    "progress": 100,
                    "processed_size": total_size,
                    "total_size": total_size,
                },
            )

        except Exception as e:
            logger.error(f"Album process error [{download_id}]: {e}")

            if should_charge and reservation_id:
                await self._release_credit_on_failure(reservation_id)

            # Failure history
            await self._record_history(user_id, "album", album_id, "failure", error_message=str(e))

            await self._update_status(
                download_id,
                DownloadStage.ERROR,
                {
                    "error": str(e),
                    "stage_detail": f"Error: {e}",
                    "download_path": None,
                    "processed_size": processed_bytes,
                    "total_size": total_size,
                },
            )
            raise
        finally:
            try:
                await self._cleanup_files(album_dir)
            except Exception as ce:
                logger.error(f"Album cleanup error: {ce}")

# ------------------------------------------------------------------------------
class DownloadManager:
    def __init__(
        self,
        max_workers: int = None,
        concurrency: int = None,
        db_factory: Optional[Callable[[], object]] = None,
    ):
        self.download_queue = asyncio.Queue()
        self.status_queue = asyncio.Queue()

        # MIGRATED TO REDIS: Use RedisDownloadState for cross-container state
        from redis_state.state.download import get_album_download_state
        self._download_state = get_album_download_state()

        # Expose Redis-backed state as properties for backward compatibility
        self.active_downloads = self._download_state.active_downloads
        self.completed_downloads = self._download_state.completed_downloads

        self.workers: List[DownloadWorker] = []

        self.max_workers = max_workers or worker_config.worker_configs["download_worker"]["max_workers"]
        self.concurrency = concurrency if concurrency is not None else 2

        self._db_factory = db_factory or SessionLocal
        self._lock = asyncio.Lock()
        self._is_running = False
        self._status_monitor_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None
        self._scaling_task: Optional[asyncio.Task] = None
        self._worker_restart_task: Optional[asyncio.Task] = None

        self.temp_dir = Path("/tmp/mega_downloads")
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            f"Album manager init (Redis-backed): temp={self.temp_dir}, max_workers={self.max_workers}, concurrency={self.concurrency}, container={self._download_state.container_id}"
        )

    def _count_user_actives_locked(self, user_id: int) -> int:
        prefix = f"{user_id}_"
        return sum(
            1
            for k, v in self.active_downloads.items()
            if isinstance(k, str)
            and k.startswith(prefix)
            and (v or {}).get("stage") in (
                DownloadStage.QUEUED.value,
                DownloadStage.INITIALIZATION.value,
                DownloadStage.PREPARATION.value,
                DownloadStage.DOWNLOADING.value,
                DownloadStage.COMPRESSION.value,
            )
        )

    async def _start_workers(self, count: int):
        current = len(self.workers)
        if count > current:
            for i in range(current, count):
                w = DownloadWorker(
                    worker_id=f"download_{i}",
                    download_queue=self.download_queue,
                    status_queue=self.status_queue,
                    db_factory=self._db_factory,
                    concurrency=self.concurrency,
                )
                self.workers.append(w)
                await w.start()
                worker_config.register_worker("download_worker")
                logger.info(f"Started album worker {i} (concurrency={self.concurrency})")
        elif count < current:
            to_remove = self.workers[count:]
            self.workers = self.workers[:count]
            for w in to_remove:
                await w.stop()
                worker_config.unregister_worker("download_worker")
            logger.info("Scaled down album workers")

    async def _monitor_scaling(self):
        while self._is_running:
            try:
                qsize = self.download_queue.qsize()
                worker_config.update_queue_length("download_worker", qsize)
                current = len(self.workers)
                needed = worker_config.get_worker_count("download_worker")
                if needed != current:
                    logger.info(f"Scaling album workers: {current} -> {needed} (queue={qsize})")
                    await self._start_workers(needed)
            except Exception as e:
                logger.error(f"Album scaling monitor error: {e}")
            await asyncio.sleep(worker_config.scale_check_interval)

    async def start(self):
        if self._is_running:
            return
        self._is_running = True

        # Ensure S4 is up at manager start
        try:
            from mega_s4_client import mega_s4_client
            if not getattr(mega_s4_client, "_started", False):
                await mega_s4_client.start()
        except Exception as e:
            logger.error(f"S4 init (manager) failed: {e}")
            raise

        # Start minimum workers
        initial = worker_config.worker_configs["download_worker"].get("min_workers", 1)
        await self._start_workers(initial)

        # Monitors
        self._status_monitor_task = asyncio.create_task(self._monitor_status())
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        self._scaling_task = asyncio.create_task(self._monitor_scaling())
        self._worker_restart_task = asyncio.create_task(self._monitor_worker_restarts())

        logger.info(
            f"Album manager started: initial={initial}, max={self.max_workers}, per-worker={self.concurrency}, "
            f"target_queue={worker_config.worker_configs['download_worker']['target_queue_per_worker']}"
        )

    async def stop(self):
        if not self._is_running:
            return
        self._is_running = False

        for t in (self._status_monitor_task, self._cleanup_task, self._scaling_task, self._worker_restart_task):
            if t:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

        for w in self.workers:
            try:
                await w.stop()
                worker_config.unregister_worker("download_worker")
            except Exception as e:
                logger.error(f"Stop worker error: {e}")
        self.workers.clear()

        logger.info("Album manager stopped")

    async def _monitor_worker_restarts(self):
        while self._is_running:
            try:
                now = time.time()
                for i, w in enumerate(self.workers):
                    if w.current_task_start and (now - w.current_task_start > w.task_timeout + 60):
                        logger.warning(f"Restarting stuck album worker {w.worker_id}")
                        try:
                            await w.stop()
                            nw = DownloadWorker(
                                worker_id=f"download_{i}_restarted_{int(now)}",
                                download_queue=self.download_queue,
                                status_queue=self.status_queue,
                                db_factory=self._db_factory,
                                concurrency=self.concurrency,
                            )
                            self.workers[i] = nw
                            await nw.start()
                            worker_config.register_worker("download_worker")
                            logger.info(f"Album worker restarted as {nw.worker_id}")
                        except Exception as e:
                            logger.error(f"Album worker restart failed: {e}")
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Restart monitor error: {e}")
                await asyncio.sleep(60)

    # Queue / status -----------------------------------------------------------
    async def queue_download(
        self,
        user_id: int,
        album_id: str,
        tracks: List[dict],
        should_charge: bool = True,
        reservation_id: Optional[str] = None,
        is_creator: bool = False,
    ) -> Dict:
        """
        Enqueue an album download; enforces per-user concurrency cap for non-creators.
        """
        download_id = reservation_id or f"{user_id}_{album_id}"

        async with self._lock:
            # FORCE CLEAR any existing stale data
            if download_id in self.active_downloads:
                logger.info(f"Clearing stale active download: {download_id}")
                self.active_downloads.pop(download_id, None)
            if download_id in self.completed_downloads:
                logger.info(f"Clearing stale completed download: {download_id}")
                self.completed_downloads.pop(download_id, None)

            # Concurrency cap
            if not is_creator:
                active = self._count_user_actives_locked(user_id)
                if active >= MAX_CONCURRENT_ALBUM_DOWNLOADS_PER_USER:
                    raise ConcurrentLimitExceeded(MAX_CONCURRENT_ALBUM_DOWNLOADS_PER_USER, active)

            # Remove any stale ZIP files
            zip_path = self.temp_dir / f"{download_id}.zip"
            if zip_path.exists():
                try:
                    zip_path.unlink()
                    logger.info(f"Removed stale ZIP: {zip_path}")
                except Exception as e:
                    logger.error(f"Stale ZIP remove failed: {e}")

            task = {
                "download_id": download_id,
                "tracks": tracks,
                "user_id": user_id,
                "album_id": album_id,
                "should_charge": should_charge,
                "reservation_id": reservation_id,
            }

            # Initial state
            self.active_downloads[download_id] = {
                "status": DownloadStage.QUEUED.value,
                "stage": DownloadStage.QUEUED.value,
                "progress": 0,
                "queued_at": datetime.now(timezone.utc),
                "stage_detail": "Queued for download",
                "download_path": None,
                "track_number": 0,
                "total_tracks": len(tracks),
                "tracks": tracks,
            }

            await self.download_queue.put(task)
            logger.info(f"Queued album: {download_id} (charge={should_charge}, reservation={reservation_id})")
            return self.active_downloads[download_id]


    async def get_download_status(self, download_id: str) -> Optional[Dict]:
        async with self._lock:
            st = self.active_downloads.get(download_id)
            if not st:
                return None
            if st.get("stage") == DownloadStage.QUEUED.value:
                queue_position = None
                async with download_queue_lock:
                    for i, task in enumerate(list(self.download_queue._queue), start=1):
                        if task and task.get("download_id") == download_id:
                            queue_position = i
                            break
                st["queue_position"] = queue_position
            return st

    async def clear_download_cache(self, download_id: str = None, user_id: int = None):
        """Clear cached download data - useful for debugging stale data."""
        async with self._lock:
            if download_id:
                # Clear specific download
                self.active_downloads.pop(download_id, None)
                self.completed_downloads.pop(download_id, None)
                logger.info(f"Cleared cache for download_id: {download_id}")
            elif user_id:
                # Clear all downloads for a user
                prefix = f"{user_id}_"
                to_remove = [k for k in self.active_downloads.keys() if k.startswith(prefix)]
                to_remove += [k for k in self.completed_downloads.keys() if k.startswith(prefix)]
                
                for k in to_remove:
                    self.active_downloads.pop(k, None)
                    self.completed_downloads.pop(k, None)
                
                logger.info(f"Cleared cache for user_id {user_id}: {len(to_remove)} items")
            else:
                # Clear everything
                count = len(self.active_downloads) + len(self.completed_downloads)
                self.active_downloads.clear()
                self.completed_downloads.clear()
                logger.info(f"Cleared all download cache: {count} items")

    async def _monitor_status(self):
        """Merge worker status updates into in-memory state."""
        stage_order = {
            DownloadStage.QUEUED.value: 0,
            DownloadStage.INITIALIZATION.value: 1,
            DownloadStage.PREPARATION.value: 2,
            DownloadStage.DOWNLOADING.value: 3,
            DownloadStage.COMPRESSION.value: 4,
            DownloadStage.STREAMING.value: 5,
            DownloadStage.COMPLETED.value: 6,
            DownloadStage.ERROR.value: 7,
        }

        while self._is_running:
            try:
                update = await self.status_queue.get()
                download_id = update["download_id"]

                async with self._lock:
                    if download_id not in self.active_downloads:
                        continue

                    current = self.active_downloads[download_id]
                    cur_stage = current.get("stage")
                    new_stage = update["stage"]

                    # Disallow stage regression
                    if cur_stage in stage_order and new_stage in stage_order:
                        if stage_order[new_stage] < stage_order[cur_stage]:
                            continue

                    # Preserve fields if not present
                    if "total_tracks" not in update and "total_tracks" in current:
                        update["total_tracks"] = current["total_tracks"]
                    if "queued_at" not in update and "queued_at" in current:
                        update["queued_at"] = current["queued_at"]

                    current.update(update)
                    current["last_updated"] = datetime.now(timezone.utc)

                    if new_stage in (DownloadStage.COMPLETED.value, DownloadStage.ERROR.value):
                        current["completed_at"] = datetime.now(timezone.utc)
                        self.completed_downloads[download_id] = current
                        self.active_downloads.pop(download_id, None)
                        if new_stage == DownloadStage.COMPLETED.value:
                            logger.info(f"Album complete: {download_id}")
                        else:
                            logger.error(f"Album failed: {download_id}")

                    if cur_stage != new_stage:
                        logger.info(f"Album {download_id}: {cur_stage} -> {new_stage}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Status monitor error: {e}")
                await asyncio.sleep(0.05)

    async def _cleanup_loop(self):
        while self._is_running:
            try:
                await self._cleanup_old_downloads()
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cleanup loop error: {e}")
                await asyncio.sleep(60)

    async def _cleanup_old_downloads(self):
        async with self._lock:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            to_remove = []
            for download_id, st in self.completed_downloads.items():
                completed_at = st.get("completed_at")
                if completed_at and completed_at < cutoff:
                    path = st.get("download_path")
                    if path:
                        p = Path(path)
                        try:
                            if p.exists():
                                p.unlink()
                        except Exception as e:
                            logger.error(f"Old file delete error {path}: {e}")
                    to_remove.append(download_id)
            for download_id in to_remove:
                self.completed_downloads.pop(download_id, None)
                logger.info(f"Cleaned old album: {download_id}")


# Global instance
download_manager = DownloadManager(db_factory=SessionLocal)
