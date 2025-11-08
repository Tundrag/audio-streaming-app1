# hls_core.py - Time-based HLS processing with voice awareness

import asyncio
import logging
import shutil
import aiofiles
import json
import time
import os
import math
import bisect
import anyio
from pathlib import Path
from typing import Dict, Optional, List, Callable, Any
from fastapi import HTTPException
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from concurrent.futures import ProcessPoolExecutor
from models import Track, SegmentMetadata
from database import get_db
import numpy as np
import multiprocessing as mp
import hashlib
from storage import storage as storage_manager
from duration_manager import duration_manager
from hls_storage_config import check_hls_storage_before_track_creation
from background_preparation import BackgroundPreparationManager

# Redis state managers for multi-container support
from redis_state.state.progress import progress_state
from redis_state.cache.word_timing import word_timing_cache as redis_word_timing
from redis_state.state.conversion import conversion_state

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Configuration constants
HLS_SEGMENT_CONTAINER = os.getenv("HLS_SEGMENT_CONTAINER", "ts")
SEGMENT_DURATION = float(os.getenv("HLS_SEGMENT_DURATION", "30"))
FFMPEG_THREADS = int(os.getenv("FFMPEG_THREADS", str(max(1, mp.cpu_count() // 2))))

BASE_DIR = Path(os.path.expanduser("~")) / ".hls_streaming"
SEGMENT_DIR = BASE_DIR / "segments"
TEMP_DIR = Path("/tmp/mega_storage")

DEFAULT_BITRATE = {
    'bitrate': 64,
    'name': 'default',
    'codec': 'aac',
    'codec_options': {
        'aac_coder': 'fast',
        'preset': 'ultrafast',
        'compression_level': 7,
        'cutoff': 18000,
        'profile': 'aac_low',
        'quality': 3
    },
    'segment_duration': SEGMENT_DURATION
}

class DatabaseExecutor:
    @staticmethod
    async def execute(operation: Callable[[], Any]) -> Any:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, operation)

def _get_track_by_id(db: Session, track_id: str) -> Optional[Track]:
    return db.query(Track).filter(Track.id == track_id).first()

def _update_track_status(db: Session, track: Track, status: str, hls_ready: bool = None, error_msg: str = None):
    try:
        track.segmentation_status = status
        if hls_ready is not None:
            track.hls_ready = hls_ready
        if error_msg is not None:
            track.processing_error = error_msg
        db.commit()
    except Exception as e:
        if db.is_active:
            db.rollback()
        raise e

def _delete_segment_metadata(db: Session, track_id: str) -> int:
    """Delete segment metadata for a track (used by hls_streaming.py)"""
    try:
        from models import SegmentMetadata
        deleted_count = db.query(SegmentMetadata).filter(
            SegmentMetadata.track_id == str(track_id)
        ).delete(synchronize_session=False)
        db.commit()
        return deleted_count
    except Exception as db_error:
        if db.is_active:
            db.rollback()
        raise db_error

def _rollback_db(db: Session):
    if db.is_active:
        db.rollback()

def _get_file_hash(file_path: str) -> str:
    hash_obj = hashlib.sha256(str(file_path).encode())
    return hash_obj.hexdigest()

class SimpleFileCache:
    """File-based metadata storage for voice-aware streams"""
    
    def __init__(self, segment_dir: Path):
        self.segment_dir = segment_dir
         
    async def get_metadata(self, key: str) -> Optional[Dict]:
        """Get metadata from metadata.json file"""
        try:
            if ":voice:" in key:
                parts = key.split(":voice:")
                track_id = parts[0]
                voice_id = parts[1]
                base_dir = self.segment_dir / track_id / f"voice-{voice_id}"
            else:
                base_dir = self.segment_dir / key

            metadata_file = base_dir / "metadata.json"
            if metadata_file.exists():
                async with aiofiles.open(metadata_file, "r") as f:
                    content = await f.read()
                    return json.loads(content)

            return None
        except Exception as e:
            logger.error(f"Error reading metadata for {key}: {e}")
            return None

    async def set_metadata(self, key: str, metadata: Dict):
        """Store metadata in metadata.json"""
        try:
            if ":voice:" in key:
                parts = key.split(":voice:")
                track_id = parts[0]
                voice_id = parts[1]
                track_dir = self.segment_dir / track_id / f"voice-{voice_id}"
            else:
                track_dir = self.segment_dir / key

            track_dir.mkdir(parents=True, exist_ok=True)

            metadata_file = track_dir / "metadata.json"
            async with aiofiles.open(metadata_file, "w") as f:
                await f.write(json.dumps(metadata, indent=2))

            logger.debug(f"Stored metadata for {key}")
        except Exception as e:
            logger.error(f"Error storing metadata for {key}: {e}")

    async def set_upload_time(self, stream_id: str):
        """Store upload time in metadata"""
        try:
            metadata = await self.get_metadata(stream_id) or {}
            metadata['upload_time'] = time.time()
            await self.set_metadata(stream_id, metadata)
        except Exception as e:
            logger.debug(f"Failed to set upload time for {stream_id}: {e}")

    async def clear_track_data(self, track_id: str) -> bool:
        """Remove track metadata files"""
        try:
            if "_voice_" in track_id:
                parts = track_id.split("_voice_")
                actual_track_id = parts[0]
                voice_id = parts[1]
                track_dir = self.segment_dir / actual_track_id / f"voice-{voice_id}"
            else:
                track_dir = self.segment_dir / track_id
                
            if track_dir.exists():
                for metadata_file in ["metadata.json", "index.json"]:
                    file_path = track_dir / metadata_file
                    if file_path.exists():
                        file_path.unlink()
            return True
        except Exception as e:
            logger.error(f"Error clearing track data for {track_id}: {e}")
            return False

class BaseHLSManager:
    def __init__(self):
        super().__init__()
        self.base_dir = BASE_DIR
        self.segment_dir = SEGMENT_DIR
        self.temp_dir = TEMP_DIR
        self._ensure_directories()

        self.cache = SimpleFileCache(self.segment_dir)

        # Redis-backed state for multi-container support
        self.active_conversions = conversion_state.active_conversions
        self.segment_locks = conversion_state.segment_locks
        self.conversion_locks = conversion_state.conversion_locks
        self.track_regeneration_locks = conversion_state.track_regeneration_locks

        # Local locks (OK to stay local - protect same-container access)
        self.segment_locks_lock = asyncio.Lock()
        self.track_lock_creation = asyncio.Lock()

        self.default_bitrate = DEFAULT_BITRATE.copy()
        self.preparation_manager = BackgroundPreparationManager()

        # Redis-backed word timing cache (shared across containers)
        self.word_timing_cache = redis_word_timing.cache
        self.word_precision_tolerance = 0.001
        self.enable_word_level_mapping = True

        self.max_concurrent_conversions = mp.cpu_count()
        self.conversion_semaphore = asyncio.Semaphore(self.max_concurrent_conversions)
        self.process_pool = ProcessPoolExecutor(max_workers=mp.cpu_count())

        # Redis-backed progress tracking (shared across containers)
        self.segment_progress = progress_state.segment_progress
        # Local throttling state for websocket broadcasts per progress key
        self._segment_ws_state: Dict[str, Dict[str, float]] = {}

        logger.info("BaseHLSManager initialized with Redis-backed state for multi-container support")

    def _ensure_directories(self):
        try:
            os.makedirs(str(TEMP_DIR), exist_ok=True)
            os.makedirs(str(BASE_DIR), exist_ok=True)
            os.makedirs(str(SEGMENT_DIR), exist_ok=True)
        except Exception as e:
            logger.error(f"Error creating directories: {str(e)}")
            raise

    def _get_progress_key(self, track_id: str, voice_id: Optional[str] = None) -> str:
        """Get voice-aware progress tracking key"""
        if voice_id:
            return f"{track_id}:voice:{voice_id}"
        return track_id

    def _get_stream_dir(self, track_id: str, voice_id: Optional[str] = None) -> Path:
        """Get voice-aware stream directory"""
        if voice_id:
            return self.segment_dir / track_id / f"voice-{voice_id}"
        return self.segment_dir / track_id

    def _parse_progress_key(self, progress_key: str) -> (str, Optional[str]):
        """Split a progress key into track_id and optional voice_id"""
        if ":voice:" in progress_key:
            track_id, voice_id = progress_key.split(":voice:", 1)
            return track_id, voice_id
        return progress_key, None

    def _should_broadcast_segment_update(self, progress_key: str, percentage: float, now: float) -> bool:
        """
        Decide whether to broadcast a segmentation update based on percent/time deltas.
        Ensures we notify on the first event, at least every 1% change, once per second, and always at 100%.
        """
        state = self._segment_ws_state.setdefault(progress_key, {"last_pct": -1.0, "last_time": 0.0})
        last_pct = state["last_pct"]
        last_time = state["last_time"]

        if percentage >= 100.0:
            state["last_pct"] = percentage
            state["last_time"] = now
            return True

        pct_delta = percentage - last_pct if last_pct >= 0 else percentage
        time_delta = now - last_time

        if last_pct < 0 or pct_delta >= 1.0 or time_delta >= 1.0:
            state["last_pct"] = percentage
            state["last_time"] = now
            return True
        return False

    def _queue_segment_ws_broadcast(self, progress_key: str, payload: Dict[str, Any]):
        """
        Schedule websocket broadcast for segmentation updates (voice-specific streams only).
        """
        track_id, voice_id = self._parse_progress_key(progress_key)
        if not voice_id:
            return

        percentage = float(payload.get("percentage", 0.0) or 0.0)
        now = time.monotonic()
        if not self._should_broadcast_segment_update(progress_key, percentage, now):
            return

        segments_completed = payload.get("segments_completed") or payload.get("segments_so_far") or 0
        total_segments = payload.get("total_segments") or payload.get("expected_segments") or 0
        message = payload.get("message") or "Segmenting audio..."

        async def _send():
            try:
                from tts_websocket import broadcast_segmentation_progress
                await broadcast_segmentation_progress(
                    track_id,
                    voice_id,
                    int(round(percentage)),
                    int(segments_completed),
                    int(total_segments),
                    message
                )
            except Exception as e:
                logger.debug(f"Segmentation websocket broadcast failed for {progress_key}: {e}")

        asyncio.create_task(_send())

    def _format_duration(self, seconds: float) -> str:
        """Format duration as MM:SS or H:MM:SS"""
        if seconds < 0:
            return "0:00"
        
        total_seconds = int(seconds)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60
        
        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes}:{secs:02d}"

    async def _get_duration_from_database(self, track_id: str, db: Session, voice_id: Optional[str] = None) -> float:
        """Get duration from database using duration_manager"""
        try:
            duration = await duration_manager.get_duration(track_id, db, voice_id=voice_id)
            return duration if duration > 0 else 0.0
        except Exception as e:
            logger.error(f"Error getting duration from database: {str(e)}")
            return 0.0

    async def _extract_duration_with_fallback(self, file_path: Path, track_id: str, db: Session, voice_id: Optional[str] = None) -> float:
        """Get duration using duration_manager"""
        try:
            if db:
                duration = await self._get_duration_from_database(track_id, db, voice_id)
                if duration > 0:
                    logger.info(f"Duration from DB: {track_id} = {duration}s")
                    return duration
                    
            logger.info(f"Extracting duration from source: {file_path}")
            metadata = await duration_manager._extract_metadata(file_path)
            if metadata:
                duration = float(metadata['duration'])
                logger.info(f"Duration from file: {track_id} = {duration}s")
                return duration
            else:
                raise ValueError("Could not extract duration from file")
                
        except Exception as e:
            logger.error(f"Error getting duration: {str(e)}")
            raise ValueError(f"Failed to get duration for track {track_id}")

    def _parse_m3u8_durations(self, playlist_path: Path) -> List[float]:
        """Parse EXTINF durations from HLS playlist"""
        durations = []
        with playlist_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#EXTINF:"):
                    val = line.split(":", 1)[1].split(",", 1)[0]
                    durations.append(float(val))
        if not durations:
            raise RuntimeError(f"No EXTINF durations found in {playlist_path.name}")
        return durations

    async def _hls_from_source_direct_with_progress(
        self, source_path: Path, variant_dir: Path,
        playlist_name: str, segment_duration: float,
        total_duration: float, progress_key: str
    ):
        """Direct single-pass source -> HLS with time-based progress"""
        
        seg_pat = variant_dir / f"segment_%05d.ts"
        playlist_path = variant_dir / playlist_name

        # Initialize time-based progress
        total_segments_estimate = math.ceil(total_duration / segment_duration) if segment_duration > 0 else 0
        self.segment_progress.setdefault(progress_key, {})
        initial_payload = {
            'status': 'creating_segments',
            'total_duration': total_duration,
            'current_duration': 0.0,
            'percentage': 0.0,
            'message': f'Preparing segments (0.0% complete)...',
            'formatted': {
                'current': '0:00',
                'total': self._format_duration(total_duration),
                'percent': '0%'
            },
            'optimized_single_pass': True,
            'progress_type': 'time_based',
            'segments_completed': 0,
            'total_segments': total_segments_estimate
        }
        self.segment_progress[progress_key] = initial_payload
        self._queue_segment_ws_broadcast(progress_key, self.segment_progress[progress_key])

        # FFmpeg command
        args = [
            "ffmpeg", "-y",
            "-i", str(source_path),
            "-threads", str(FFMPEG_THREADS),
            "-preset", "ultrafast",
            "-c:a", "aac",
            "-b:a", "64k",
            "-ar", "22050",
            "-ac", "1",
            "-f", "hls",
            "-hls_playlist_type", "vod",
            "-hls_segment_type", "mpegts",
            "-hls_segment_filename", str(seg_pat),
            "-hls_time", str(segment_duration),
            "-hls_list_size", "0",
            "-hls_flags", "split_by_time",
            "-avoid_negative_ts", "make_zero",
            "-fflags", "+genpts+bitexact+flush_packets",
            "-progress", "pipe:2",
            "-nostats",
            "-loglevel", "warning",
            "-master_pl_name", "master.m3u8",
            str(playlist_path)
        ]

        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        last_logged_percent = -1

        def _update_time_progress(current_time_seconds: float):
            """Update progress based on processing time"""
            nonlocal last_logged_percent
            
            if total_duration <= 0:
                return
            
            # Clamp to total duration for clean 100%
            current_time = min(current_time_seconds, total_duration)
            percentage = (current_time / total_duration) * 100.0
            
            # Update progress record
            prog = self.segment_progress.get(progress_key, {})
            segments_completed = max(0, math.floor(current_time / segment_duration)) if segment_duration > 0 else 0
            prog.update({
                'current_duration': current_time,
                'percentage': percentage,
                'status': 'creating_segments',
                'message': f'Preparing segments ({percentage:.1f}% complete)...',
                'formatted': {
                    'current': self._format_duration(current_time),
                    'total': self._format_duration(total_duration),
                    'percent': f'{percentage:.1f}%'
                },
                'segments_completed': segments_completed,
                'total_segments': total_segments_estimate
            })
            self.segment_progress[progress_key] = prog
            self._queue_segment_ws_broadcast(progress_key, prog)
            
            # Log every 10% for reasonable granularity
            current_percent_10 = int(percentage // 10) * 10
            if current_percent_10 != last_logged_percent and current_percent_10 % 10 == 0:
                last_logged_percent = current_percent_10
                logger.info(f"Processing: {percentage:.1f}% ({self._format_duration(current_time)}/{self._format_duration(total_duration)})")

        async def _stderr_monitor():
            """Parse FFmpeg progress output for time-based updates"""
            async for raw in process.stderr:
                line = raw.decode('utf-8', 'ignore').strip()

                current_time = None
                if line.startswith("out_time_ms="):
                    try:
                        # Microseconds to seconds
                        current_time = float(line.split("=", 1)[1]) / 1_000_000.0
                    except Exception:
                        continue
                elif line.startswith("out_time="):
                    try:
                        # Parse HH:MM:SS.mmm format
                        time_str = line.split("=", 1)[1]
                        parts = time_str.split(":")
                        hours = float(parts[0])
                        minutes = float(parts[1])
                        seconds = float(parts[2])
                        current_time = hours * 3600 + minutes * 60 + seconds
                    except Exception:
                        continue

                if current_time is not None:
                    _update_time_progress(current_time)

        # Start monitoring
        stderr_task = asyncio.create_task(_stderr_monitor())

        try:
            await process.wait()
        finally:
            await asyncio.gather(stderr_task, return_exceptions=True)

        if process.returncode != 0:
            error_payload = self.segment_progress.setdefault(progress_key, {})
            error_payload.update({
                'status': 'error',
                'message': f'HLS segmentation failed (code {process.returncode})'
            })
            self._queue_segment_ws_broadcast(progress_key, error_payload)
            raise RuntimeError(f"HLS segmentation failed with code {process.returncode}")

        # Finalize to 100%
        actual_segments = list(variant_dir.glob('segment_*.ts'))
        final_progress = {
            'current_duration': total_duration,
            'percentage': 100.0,
            'status': 'segmentation_complete',
            'segments_created': len(actual_segments),
            'message': f'Segmentation complete: {len(actual_segments)} segments created',
            'formatted': {
                'current': self._format_duration(total_duration),
                'total': self._format_duration(total_duration),
                'percent': '100%'
            },
            'segments_completed': len(actual_segments),
            'total_segments': len(actual_segments) or total_segments_estimate
        }
        self.segment_progress[progress_key].update(final_progress)
        self._queue_segment_ws_broadcast(progress_key, self.segment_progress[progress_key])

        logger.info(f"HLS segmentation complete: {self._format_duration(total_duration)} -> {len(actual_segments)} segments")
        if progress_key in self._segment_ws_state:
            self._segment_ws_state.pop(progress_key, None)
        return playlist_path

    async def _get_conversion_lock(self, file_hash: str) -> asyncio.Lock:
        if file_hash not in self.conversion_locks:
            self.conversion_locks[file_hash] = asyncio.Lock()
        return self.conversion_locks[file_hash]

    async def _save_segment_index(
        self, variant_dir: Path, durations: List[float], start_number: int = 0
    ):
        try:
            starts = []
            acc = 0.0
            for d in durations:
                starts.append(acc)
                acc += d
            
            index = {
                "start_number": start_number,
                "durations": durations,
                "starts": starts,
                "nominal": self.default_bitrate["segment_duration"],
                "total_duration": acc,
                "measured": True,
                "optimized_single_pass": True,
                "pipeline": "single_pass_time_based",
                "container": HLS_SEGMENT_CONTAINER,
                "uses_database_duration": True,
                "duration_source": "duration_manager"
            }
            
            idx_path = variant_dir.parent / "index.json"
            async with aiofiles.open(idx_path, "w") as f:
                await f.write(json.dumps(index, indent=2))
                
        except Exception as e:
            logger.error(f"Error saving segment index: {e}")

    async def _create_master_playlist(self, stream_dir: Path, variants: List[Dict]):
        try:
            content = "#EXTM3U\n#EXT-X-VERSION:3\n"
            for variant in variants:
                content += (
                    f'#EXT-X-STREAM-INF:BANDWIDTH={variant["bitrate"]*1000},'
                    f'CODECS="mp4a.40.2",NAME="{variant["name"]}"\n'
                    f'{variant["name"]}/playlist.m3u8\n'
                )
            master_path = stream_dir / "master.m3u8"
            async with aiofiles.open(master_path, 'w') as f:
                await f.write(content)
        except Exception as e:
            logger.error(f"Master playlist creation error: {str(e)}")
            raise

    async def _get_segment_lock(self, track_id: str) -> asyncio.Lock:
        async with self.segment_locks_lock:
            if track_id not in self.segment_locks:
                self.segment_locks[track_id] = asyncio.Lock()
            return self.segment_locks[track_id]

    async def _get_regeneration_lock(self, track_id: str) -> asyncio.Lock:
        async with self.track_lock_creation:
            if track_id not in self.track_regeneration_locks:
                self.track_regeneration_locks[track_id] = asyncio.Lock()
            return self.track_regeneration_locks[track_id]

    async def _get_word_timings_for_segmentation(
        self,
        track_id: str,
        voice_id: str,
        db: Session
    ) -> Optional[List[Dict]]:
        """Get word timings from file storage"""
        cache_key = f"{track_id}:{voice_id}"
        
        if cache_key in self.word_timing_cache:
            cached_data = self.word_timing_cache[cache_key]
            if time.time() - cached_data['timestamp'] < 300:
                return cached_data['timings']
        
        try:
            from text_storage_service import text_storage_service
            word_timings = await text_storage_service.get_word_timings(track_id, voice_id, db)
            
            if word_timings:
                self.word_timing_cache[cache_key] = {
                    'timings': word_timings,
                    'timestamp': time.time(),
                    'source': 'file_storage'
                }
                logger.info(
                    f"Retrieved {len(word_timings)} word timings from file storage "
                    f"for {track_id}:{voice_id}"
                )
                return word_timings
            else:
                logger.warning(
                    f"No word timings found in file storage for {track_id}:{voice_id}"
                )
                return None

        except Exception as e:
            logger.error(
                f"Error getting word timings from file storage for "
                f"{track_id}:{voice_id}: {str(e)}"
            )
            return None

    def _overlap(self, a1: float, a2: float, b1: float, b2: float) -> float:
        """Calculate overlap between two time ranges"""
        return max(0.0, min(a2, b2) - max(a1, b1))

    async def _map_words_to_segments_precise(
        self,
        track_id: str,
        voice_id: str,
        word_timings: List[Dict],
        segment_boundaries: List[Dict],
        db: Session = None,
        total_duration: float = None
    ) -> int:
        """
        Map words to segments using batch processing and file storage append.
        Returns: number of words mapped.
        """
        if not word_timings or not segment_boundaries:
            return 0

        # Precompute arrays for binary search
        starts = [b["start"] for b in segment_boundaries]
        ends = [b["end"] for b in segment_boundaries]
        durs = [b["duration"] for b in segment_boundaries]
        nseg = len(segment_boundaries)

        batch_size = 10_000
        mapped_total = 0

        from text_storage_service import text_storage_service
        can_append = hasattr(text_storage_service, "append_word_timings")

        enhanced_all: List[Dict] = [] if not can_append else None

        def map_one(w: Dict) -> Optional[Dict]:
            ws = float(w["start_time"])
            we = float(w["end_time"])
            if total_duration is not None and (we < 0 or ws > total_duration):
                return None

            # Binary search for segment
            i = bisect.bisect_right(starts, ws) - 1
            if i < 0:
                i = 0
            candidates = [i]
            if i + 1 < nseg:
                candidates.append(i + 1)

            # Choose by max overlap
            best_idx, best_ov = None, 0.0
            for ci in candidates:
                ov = self._overlap(ws, we, starts[ci], ends[ci])
                if ov > best_ov:
                    best_ov = ov
                    best_idx = ci

            if best_idx is None or best_ov <= 0.0:
                mid = 0.5 * (ws + we)
                j = bisect.bisect_right(starts, mid) - 1
                if 0 <= j < nseg and starts[j] <= mid <= ends[j]:
                    best_idx = j
                else:
                    return None

            seg_idx = int(best_idx)
            seg_offset = max(0.0, ws - starts[seg_idx])
            seg_end_offset = max(seg_offset, min(durs[seg_idx], we - starts[seg_idx]))

            enhanced_word = {
                "word": w.get("word", ""),
                "start_time": ws,
                "end_time": we,
                "segment_index": seg_idx,
                "segment_offset": seg_offset,
                "word_index": w.get("word_index"),
                "duration": max(0.0, we - ws),
                "voice": voice_id,
                "optimized_single_pass": True,
            }
            
            for k, v in w.items():
                if k not in enhanced_word:
                    enhanced_word[k] = v
            return enhanced_word

        # Process in batches
        for batch_start in range(0, len(word_timings), batch_size):
            batch = word_timings[batch_start:batch_start + batch_size]
            enhanced_batch = []

            for w in batch:
                mapped = map_one(w)
                if mapped is not None:
                    enhanced_batch.append(mapped)

            mapped_total += len(enhanced_batch)

            # Persist batch
            if can_append:
                if enhanced_batch:
                    await text_storage_service.append_word_timings(
                        track_id, voice_id, enhanced_batch, db=db
                    )
            else:
                if enhanced_batch:
                    enhanced_all.extend(enhanced_batch)

            del enhanced_batch, batch

        # Final write if no append available
        if not can_append and enhanced_all is not None:
            await text_storage_service.store_word_timings(
                track_id, voice_id, enhanced_all, db=db
            )
            del enhanced_all

        # Update cache with metrics
        cache_key = f"{track_id}:{voice_id}"
        self.word_timing_cache[cache_key] = {
            "timings": None,
            "timestamp": time.time(),
            "quality_metrics": {
                "mapping_coverage": (mapped_total / max(1, len(word_timings))) * 100.0,
                "total_words": len(word_timings),
                "supports_precision_switching": mapped_total >= int(0.8 * len(word_timings)),
                "optimized_single_pass": True,
            },
            "storage": "file",
        }

        logger.info(
            f"Segment mapping complete: {mapped_total}/{len(word_timings)} words "
            f"({'append' if can_append else 'overwrite'})"
        )
        return mapped_total

    async def _async_word_mapping(
        self, 
        track_id: str, 
        voice_id: str, 
        measured_durations: List[float], 
        total_duration: float
    ):
        """Background word mapping task"""
        db = None
        try:
            logger.info(f"Starting background word mapping: {track_id}/{voice_id}")
            
            db = next(get_db())
            
            boundaries, t = [], 0.0
            for i, d in enumerate(measured_durations):
                boundaries.append({"index": i, "start": t, "end": t + d, "duration": d})
                t += d

            word_timings = await self._get_word_timings_for_segmentation(track_id, voice_id, db)
            if word_timings:
                logger.info(f"Background word mapping: {len(word_timings)} words for {track_id}")
                await self._map_words_to_segments_precise(
                    track_id=track_id,
                    voice_id=voice_id,
                    word_timings=word_timings,
                    segment_boundaries=boundaries,
                    db=db,
                    total_duration=total_duration
                )
                logger.info(f"Background word mapping complete: {track_id} ({len(word_timings)} words)")
            else:
                logger.warning(f"No word timings found for background mapping: {track_id}/{voice_id}")
        
        except Exception as e:
            logger.error(f"Background word mapping failed for {track_id}/{voice_id}: {str(e)}")
        finally:
            if db:
                try:
                    await DatabaseExecutor.execute(lambda: db.close())
                except Exception:
                    pass

    async def clear_segment_progress(self, progress_key: str):
        try:
            if progress_key in self.segment_progress:
                self.segment_progress.pop(progress_key, None)
            if progress_key in self._segment_ws_state:
                self._segment_ws_state.pop(progress_key, None)
        except Exception as e:
            logger.error(f"Error clearing segment progress for {progress_key}: {e}")

    async def prepare_hls_stream(self, file_path: Path, filename: str, track_id: str, db=None, voice_id: Optional[str] = None) -> Dict:
        """HLS preparation with time-based progress and voice awareness"""
        try:
            progress_key = self._get_progress_key(track_id, voice_id)
            logger.info(f"HLS Pipeline Start: {progress_key}")
            
            await self.clear_segment_progress(progress_key)

            stream_dir = self._get_stream_dir(track_id, voice_id)
            variant_dir = stream_dir / self.default_bitrate['name']

            # Check for existing segments
            index_path = stream_dir / "index.json"
            playlist_exists = await asyncio.get_event_loop().run_in_executor(
                None, lambda: (variant_dir / "playlist.m3u8").exists()
            )
            segments_exist = await asyncio.get_event_loop().run_in_executor(
                None, lambda: list(variant_dir.glob("segment_*.ts"))
            )
            
            if playlist_exists and segments_exist and index_path.exists():
                logger.info(f"HLS Pipeline: Using existing segments ({len(segments_exist)} found) for {progress_key}")
                metadata = await self.cache.get_metadata(progress_key)
                if metadata:
                    return metadata

            # Storage check
            try:
                track = None
                if db:
                    track = await DatabaseExecutor.execute(lambda: _get_track_by_id(db, track_id))
                
                storage_check = await check_hls_storage_before_track_creation(
                    hls_base_dir=self.segment_dir.parent,
                    db=db,
                    track_id=track_id,
                    file_url=getattr(track, 'file_url', None) if track else None
                )
                
                if not storage_check['can_create']:
                    logger.error(f"HLS Pipeline: Storage limit reached for {progress_key}")
                    raise HTTPException(status_code=507, detail="Storage limit exceeded")
            except HTTPException:
                raise
            except Exception as storage_error:
                logger.error(f"HLS Pipeline: Storage check failed for {progress_key}")

            # Get accurate duration from duration_manager
            initial_duration = await self._extract_duration_with_fallback(file_path, track_id, db, voice_id)
            segment_duration = self.default_bitrate['segment_duration']
            
            logger.info(f"HLS Pipeline: Duration {initial_duration:.3f}s from duration_manager (voice={voice_id})")

            await asyncio.get_event_loop().run_in_executor(
                None, lambda: stream_dir.mkdir(parents=True, exist_ok=True)
            )
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: variant_dir.mkdir(parents=True, exist_ok=True)
            )

            # Time-based direct segmentation
            logger.info(f"HLS Stage: Time-based segmentation {file_path.name} -> HLS")
            
            playlist_path = await self._hls_from_source_direct_with_progress(
                source_path=file_path,
                variant_dir=variant_dir,
                playlist_name="playlist.m3u8",
                segment_duration=segment_duration,
                total_duration=initial_duration,
                progress_key=progress_key
            )
            
            # Parse measured durations from generated playlist
            measured_durations = self._parse_m3u8_durations(playlist_path)
            logger.info(f"HLS Stage: Parsed {len(measured_durations)} segment durations from playlist")
            
            # Verify precision
            total_measured = sum(measured_durations)
            precision_error = abs(total_measured - initial_duration)
            
            if precision_error > 1.0:
                logger.warning(f"HLS Stage: Precision warning {precision_error*1000:.1f}ms gap")
            else:
                logger.info(f"HLS Stage: Good precision ({precision_error*1000:.1f}ms gap)")
            
            # Word mapping (async for TTS)
            words_mapped = 0
            if self.enable_word_level_mapping and db and track_id and voice_id:
                asyncio.create_task(self._async_word_mapping(
                    track_id=track_id,
                    voice_id=voice_id,
                    measured_durations=measured_durations,
                    total_duration=initial_duration
                ))
                logger.info(f"Word mapping queued for background processing: {track_id}/{voice_id}")
                words_mapped = -1

            await self._save_segment_index(variant_dir, measured_durations, start_number=0)

            master_exists = await asyncio.get_event_loop().run_in_executor(
                None, lambda: (stream_dir / "master.m3u8").exists()
            )
            if not master_exists:
                await self._create_master_playlist(stream_dir, [self.default_bitrate])

            # Database updates
            if db and track_id:
                try:
                    track = await DatabaseExecutor.execute(lambda: _get_track_by_id(db, track_id))
                    if track:
                        await DatabaseExecutor.execute(lambda: _update_track_status(db, track, 'complete', True))
                except Exception as track_error:
                    logger.error(f"HLS Pipeline: Database update failed: {track_error}")

            stream_info = {
                'stream_id': progress_key,
                'track_id': track_id,
                'voice_id': voice_id,
                'duration': initial_duration,
                'ready': True,
                'segment_duration': segment_duration,
                'total_segments': len(measured_durations),
                'measured_segment_durations': measured_durations,
                'has_measured_durations': True,
                'optimized_single_pass': True,
                'pipeline_type': 'single_pass_time_based',
                'progress_type': 'time_based',
                'precision_error_ms': precision_error * 1000,
                'variants': [{
                    'name': self.default_bitrate['name'],
                    'bitrate': self.default_bitrate['bitrate'],
                    'codec': self.default_bitrate['codec'],
                    'segment_duration': self.default_bitrate['segment_duration'],
                    'url': f"{self.default_bitrate['name']}/playlist.m3u8"
                }]
            }

            await self.cache.set_metadata(progress_key, stream_info)

            # Final progress update
            if progress_key in self.segment_progress:
                word_status = "words mapping in background" if words_mapped == -1 else f"{words_mapped} words mapped"
                self.segment_progress[progress_key].update({
                    'status': 'complete',
                    'percentage': 100,
                    'current_duration': initial_duration,
                    'message': f'HLS processing complete: {len(measured_durations)} segments, {word_status}',
                    'async_word_mapping': words_mapped == -1,
                    'formatted': {
                        'current': self._format_duration(initial_duration),
                        'total': self._format_duration(initial_duration),
                        'percent': '100%'
                    },
                    'segments_completed': len(measured_durations),
                    'total_segments': len(measured_durations)
                })
                self._queue_segment_ws_broadcast(progress_key, self.segment_progress[progress_key])

            word_log = "words mapping async" if words_mapped == -1 else f"{words_mapped} words"
            logger.info(f"HLS Pipeline Complete: {progress_key} ({len(measured_durations)} segments, {word_log})")
            return stream_info

        except Exception as e:
            progress_key = self._get_progress_key(track_id, voice_id)
            logger.error(f"HLS Pipeline Failed: {progress_key} - {str(e)}")
            if progress_key in self.segment_progress:
                self.segment_progress[progress_key].update({
                    'status': 'error',
                    'message': f'Error: {str(e)}',
                    'error': str(e)
                })
                self._queue_segment_ws_broadcast(progress_key, self.segment_progress[progress_key])
                if progress_key in self._segment_ws_state:
                    self._segment_ws_state.pop(progress_key, None)
            raise

    async def cleanup(self):
        try:
            if hasattr(self, 'process_pool'):
                self.process_pool.shutdown(wait=False)

            self.word_timing_cache.clear()

            if await asyncio.get_event_loop().run_in_executor(None, lambda: self.temp_dir.exists()):
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: shutil.rmtree(str(self.temp_dir))
                )
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self.temp_dir.mkdir(parents=True, exist_ok=True)
                )

        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}")

class EnterpriseHLSManager(BaseHLSManager):
    def __init__(self):
        super().__init__()

    async def prepare_stream(self, file_path: Path, track_id: str, db=None) -> Dict:
        """Wrapper that maintains compatibility with existing architecture"""
        try:
            return await self.prepare_hls_stream(file_path, file_path.name, track_id, db=db)
        except Exception as e:
            logger.error(f"Stream preparation error: {str(e)}")
            raise
