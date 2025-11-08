# text_storage_service.py - OPTIMIZED for streaming timing storage

import asyncio
import aiofiles
import hashlib
import logging
import time
import struct
import json
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Any, NamedTuple, Union, AsyncGenerator
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from collections import defaultdict
import anyio
import bisect

# Redis cache for multi-container support (reduces 8GB×N containers to shared 8GB)
from redis_state.cache.text import text_cache as redis_text_cache
_HEADER_FMT = "<BIddI"
_WORD_META_FMT = "<QQIB"
_WORD_OFFSET_FMT = "<I"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)
_WORD_META_SIZE = struct.calcsize(_WORD_META_FMT)
_WORD_OFFSET_SIZE = struct.calcsize(_WORD_OFFSET_FMT)
_TIMING_FMT_VERSION = 4
_CACHE_MTIME_DRIFT_NS = 10_000_000
_SMALL_ITEM_THRESHOLD = 1_024_000

try:
    import zstandard as zstd
    COMPRESSION_AVAILABLE = 'zstd'
except ImportError:
    import zlib
    COMPRESSION_AVAILABLE = 'zlib'

from models import Track, TTSWordTiming, FileStorageMetadata

logger = logging.getLogger(__name__)

def _is_async(db) -> bool:
    return isinstance(db, AsyncSession)

async def _exec(db, stmt):
    if _is_async(db):
        return await db.execute(stmt)
    return await anyio.to_thread.run_sync(db.execute, stmt)

async def _commit(db):
    if _is_async(db):
        await db.commit()
    else:
        await anyio.to_thread.run_sync(db.commit)

async def _rollback(db):
    if _is_async(db):
        await db.rollback()
    else:
        await anyio.to_thread.run_sync(db.rollback)

async def _flush(db):
    if _is_async(db):
        await db.flush()
    else:
        await anyio.to_thread.run_sync(db.flush)

class TextStorageError(Exception):
    pass

class FileCorruptionError(TextStorageError):
    pass

class CacheEntry(NamedTuple):
    content: Union[str, bytes]
    size: int
    created_at: float
    last_accessed: float
    access_count: int
    file_mtime_ns: int
    expires_at: float

class CacheStats(NamedTuple):
    total_entries: int
    total_memory_mb: float
    hit_ratio: float
    expired_entries: int
    stale_entries: int
    avg_entry_size_kb: float
    oldest_entry_age: float

class TrackCentricTextStorageService:

    def __init__(
        self,
        hls_segment_dir: Optional[Path] = None,
        max_cache_memory_mb: int = 8192,
        compression_level: int = 3,
        enable_integrity_checks: bool = True,
        cache_ttl_seconds: int = 3600,
        max_cache_ttl_seconds: int = 86400,
        cleanup_interval_seconds: int = 300,
    ):
        self.hls_segment_dir = hls_segment_dir
        self.max_cache_memory = max_cache_memory_mb * 1024 * 1024
        self.compression_level = compression_level
        self.enable_integrity_checks = enable_integrity_checks
        self.cache_ttl = cache_ttl_seconds
        self.max_cache_ttl = max_cache_ttl_seconds
        self.cleanup_interval = cleanup_interval_seconds

        # Redis-backed cache (shared across containers, reduces memory from 8GB×N to shared 8GB)
        self.cache = redis_text_cache.cache
        self.current_cache_memory = 0  # Note: This is now approximate per-container, actual is in Redis
        self.cache_lock = asyncio.Lock()  # Local lock for in-container operations
        
        self._io_sem = asyncio.Semaphore(16)
        self._compress_sem = asyncio.Semaphore(4)
        self._python_cpu_sem = asyncio.Semaphore(2)
        self._file_locks = defaultdict(asyncio.Lock)
        
        self.cache_hits = 0
        self.cache_misses = 0
        self.cache_expired_hits = 0
        self.cache_stale_hits = 0
        self.cache_evictions = 0
        self.cache_cleanups = 0
        self.operations_count = 0
        self.total_files_created = 0
        self.total_bytes_written = 0
        self.total_bytes_read = 0
        
        self.cleanup_task = None
        self._shutdown = False
        self._start_cleanup_task()
        
        logger.info(f"TextStorageService: cache={max_cache_memory_mb}MB, compression={COMPRESSION_AVAILABLE}, version={_TIMING_FMT_VERSION}")
    
    def set_hls_segment_dir(self, segment_dir: Path):
        self.hls_segment_dir = segment_dir
    
    def _get_track_storage_dir(self, track_id: str) -> Path:
        if not self.hls_segment_dir:
            raise TextStorageError("HLS segment directory not initialized")
        return self.hls_segment_dir / track_id
    
    def _get_text_file_path(self, track_id: str) -> Path:
        return self._get_track_storage_dir(track_id) / "texts" / "source.txt.zst"
    
    def _get_timing_file_path(self, track_id: str, voice_id: str) -> Path:
        return self._get_track_storage_dir(track_id) / f"voice-{voice_id}" / "timings.zst"
    
    def _get_timings_parts_dir(self, track_id: str, voice_id: str) -> Path:
        return self._get_track_storage_dir(track_id) / f"voice-{voice_id}" / "timings.parts"
    
    def _get_temp_dir(self, track_id: str) -> Path:
        return self._get_track_storage_dir(track_id) / "temp"
    
    def _lock_key(self, kind: str, track_id: str, voice_id: Optional[str] = None) -> tuple:
        return (kind, track_id, voice_id or "")
    
    async def _mkdir(self, p: Path, parents=True, exist_ok=True):
        async with self._io_sem:
            await anyio.to_thread.run_sync(lambda: p.mkdir(parents=parents, exist_ok=exist_ok))
    
    async def _exists(self, p: Path) -> bool:
        async with self._io_sem:
            return await anyio.to_thread.run_sync(p.exists)
    
    async def _stat(self, p: Path):
        async with self._io_sem:
            return await anyio.to_thread.run_sync(p.stat)
    
    async def _rename(self, src: Path, dst: Path):
        async with self._io_sem:
            return await anyio.to_thread.run_sync(os.replace, str(src), str(dst))
    
    async def _unlink(self, p: Path, missing_ok=False):
        def _do():
            try:
                p.unlink()
            except FileNotFoundError:
                if not missing_ok:
                    raise
        async with self._io_sem:
            return await anyio.to_thread.run_sync(_do)
    
    async def _rmtree(self, p: Path, ignore_errors=True):
        async with self._io_sem:
            return await anyio.to_thread.run_sync(shutil.rmtree, p, ignore_errors)
    
    async def _ensure_track_directories(self, track_id: str):
        track_dir = self._get_track_storage_dir(track_id)
        await self._mkdir(track_dir / "texts")
        await self._mkdir(track_dir / "temp")
    
    async def _ensure_voice_directory(self, track_id: str, voice_id: str):
        voice_dir = self._get_track_storage_dir(track_id) / f"voice-{voice_id}"
        await self._mkdir(voice_dir)
    
    def _start_cleanup_task(self):
        if self.cleanup_task is None:
            try:
                loop = asyncio.get_running_loop()
                self.cleanup_task = loop.create_task(self._cleanup_loop())
            except RuntimeError:
                pass
    
    async def _cleanup_loop(self):
        while not self._shutdown:
            try:
                await asyncio.sleep(self.cleanup_interval)
                if not self._shutdown:
                    await self._cleanup_expired_entries()
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(30)
    
    async def _cleanup_expired_entries(self):
        if not self.cache:
            return
        
        current_time = time.time()
        async with self.cache_lock:
            items = list(self.cache.items())
        
        expired_keys = []
        stale_keys = []
        
        for key, entry in items:
            if current_time > entry.expires_at:
                expired_keys.append(key)
                continue
            
            try:
                file_path = self._get_file_path_from_cache_key(key)
                if not file_path or not await self._exists(file_path):
                    stale_keys.append(key)
                    continue
                stat_info = await self._stat(file_path)
                if stat_info.st_mtime_ns > entry.file_mtime_ns + _CACHE_MTIME_DRIFT_NS:
                    stale_keys.append(key)
            except Exception:
                stale_keys.append(key)
        
        async with self.cache_lock:
            for key in expired_keys + stale_keys:
                entry = self.cache.pop(key, None)
                if entry:
                    self.current_cache_memory = max(0, self.current_cache_memory - entry.size)
        
        if expired_keys or stale_keys:
            self.cache_cleanups += 1
    
    def _get_file_path_from_cache_key(self, cache_key: str) -> Optional[Path]:
        try:
            if cache_key.startswith("text:"):
                return self._get_text_file_path(cache_key[5:])
            elif cache_key.startswith("timing:"):
                parts = cache_key.split(":", 2)
                if len(parts) == 3:
                    return self._get_timing_file_path(parts[1], parts[2])
            return None
        except Exception:
            return None
    
    def _calculate_hash(self, content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()
    
    def _compress_data(self, data: bytes) -> bytes:
        if COMPRESSION_AVAILABLE == 'zstd':
            return zstd.ZstdCompressor(level=self.compression_level, write_content_size=True).compress(data)
        return zlib.compress(data, level=self.compression_level)
    
    def _decompress_data(self, compressed_data: bytes) -> bytes:
        """
        Decompress bytes. If the zstd frame omits the uncompressed size
        (common with streaming encoders), fall back to a streaming reader.
        """
        if COMPRESSION_AVAILABLE == 'zstd':
            import io
            try:
                # Fast path: works when the frame has a known content size
                dctx = zstd.ZstdDecompressor()
                return dctx.decompress(compressed_data)
            except zstd.ZstdError as e:
                # Fallback: handle frames with unknown content size (or concatenated frames)
                try:
                    dctx = zstd.ZstdDecompressor()
                    with dctx.stream_reader(io.BytesIO(compressed_data)) as reader:
                        chunks = []
                        while True:
                            chunk = reader.read(131072)  # 128 KiB
                            if not chunk:
                                break
                            chunks.append(chunk)
                    return b"".join(chunks)
                except Exception:
                    # Preserve the original, more-informative exception
                    raise e

        # zlib path (used only if zstd not available)
        return zlib.decompress(compressed_data)    
    async def _with_sem(self, sem: asyncio.Semaphore, fn, *args):
        async with sem:
            return await anyio.to_thread.run_sync(fn, *args)
    
    async def _compress_async(self, data: bytes) -> bytes:
        if len(data) < 256_000:
            return self._compress_data(data)
        return await self._with_sem(self._compress_sem, self._compress_data, data)
    
    async def _decompress_async(self, compressed_data: bytes) -> bytes:
        if len(compressed_data) < 256_000:
            return self._decompress_data(compressed_data)
        return await self._with_sem(self._compress_sem, self._decompress_data, compressed_data)
    
    async def _pack_word_timings_async(self, word_timings: List[Dict]) -> bytes:
        if len(word_timings) < 2_000:
            return self._pack_word_timings_v4(word_timings)
        return await self._with_sem(self._python_cpu_sem, self._pack_word_timings_v4, word_timings)
    
    async def _unpack_word_timings_async(self, data: bytes) -> List[Dict]:
        if len(data) < 256_000:
            return self._unpack_word_timings_v4(data)
        return await self._with_sem(self._python_cpu_sem, self._unpack_word_timings_v4, data)
    
    async def _evict_cache_lru(self, bytes_needed: int):
        if not self.cache_lock.locked():
            raise RuntimeError("_evict_cache_lru must be called under cache_lock")
        
        if not self.cache:
            return
        
        sorted_entries = sorted(self.cache.items(), key=lambda x: x[1].last_accessed)
        bytes_freed = 0
        keys_to_remove = []
        
        for cache_key, entry in sorted_entries:
            if bytes_freed >= bytes_needed:
                break
            keys_to_remove.append((cache_key, entry.size))
            bytes_freed += entry.size
        
        for key, size in keys_to_remove:
            if key in self.cache:
                del self.cache[key]
                self.current_cache_memory = max(0, self.current_cache_memory - size)
        
        self.cache_evictions += len(keys_to_remove)
    
    async def _cache_get(self, cache_key: str) -> Optional[Union[str, bytes]]:
        async with self.cache_lock:
            entry = self.cache.get(cache_key)
            if not entry:
                self.cache_misses += 1
                return None
            
            current_time = time.time()
            if current_time > entry.expires_at:
                self.cache_expired_hits += 1
                self.cache_misses += 1
                self.current_cache_memory = max(0, self.current_cache_memory - entry.size)
                del self.cache[cache_key]
                return None
        
        stale = False
        file_path = self._get_file_path_from_cache_key(cache_key)
        try:
            if file_path and await self._exists(file_path):
                stat_info = await self._stat(file_path)
                if stat_info.st_mtime_ns > entry.file_mtime_ns + _CACHE_MTIME_DRIFT_NS:
                    stale = True
            else:
                stale = True
        except Exception:
            stale = True
        
        async with self.cache_lock:
            current_entry = self.cache.get(cache_key)
            if not current_entry:
                self.cache_misses += 1
                return None
            
            if current_entry is not entry:
                self.cache_hits += 1
                updated = current_entry._replace(
                    last_accessed=time.time(),
                    access_count=current_entry.access_count + 1
                )
                self.cache[cache_key] = updated
                return current_entry.content
            
            if stale:
                self.cache_stale_hits += 1
                self.cache_misses += 1
                self.current_cache_memory = max(0, self.current_cache_memory - entry.size)
                del self.cache[cache_key]
                return None
            
            updated = entry._replace(
                last_accessed=time.time(),
                access_count=entry.access_count + 1
            )
            self.cache[cache_key] = updated
            self.cache_hits += 1
            return entry.content
    
    async def _cache_set(self, cache_key: str, content: str, file_path: Optional[Path] = None):
        encoded = content.encode('utf-8')
        content_size = len(encoded)
        
        if content_size > 512 * 1024 * 1024:
            return
        
        file_mtime_ns = 0
        if file_path and await self._exists(file_path):
            try:
                stat_info = await self._stat(file_path)
                file_mtime_ns = stat_info.st_mtime_ns
            except Exception:
                file_mtime_ns = time.time_ns()
        else:
            file_mtime_ns = time.time_ns()
        
        current_time = time.time()
        base_ttl = self.cache_ttl
        dynamic = base_ttl * 2 if content_size < _SMALL_ITEM_THRESHOLD else max(60, base_ttl // 2)
        dynamic_ttl = min(self.max_cache_ttl, dynamic)
        
        async with self.cache_lock:
            available_memory = self.max_cache_memory - self.current_cache_memory
            if content_size > available_memory:
                await self._evict_cache_lru(content_size - available_memory + (content_size // 4))
            
            entry = CacheEntry(
                content=content,
                size=content_size,
                created_at=current_time,
                last_accessed=current_time,
                access_count=1,
                file_mtime_ns=file_mtime_ns,
                expires_at=current_time + dynamic_ttl
            )
            
            self.cache[cache_key] = entry
            self.current_cache_memory += content_size
    
    async def _cache_get_bytes(self, cache_key: str) -> Optional[bytes]:
        result = await self._cache_get(cache_key)
        if isinstance(result, bytes):
            return result
        return None
    
    async def _cache_set_bytes(self, cache_key: str, data: bytes, file_path: Optional[Path] = None):
        content_size = len(data)
        if content_size > 512 * 1024 * 1024:
            return
        
        file_mtime_ns = time.time_ns()
        if file_path and await self._exists(file_path):
            try:
                stat_info = await self._stat(file_path)
                file_mtime_ns = stat_info.st_mtime_ns
            except Exception:
                file_mtime_ns = time.time_ns()
        
        current_time = time.time()
        base_ttl = self.cache_ttl
        dynamic = base_ttl * 2 if content_size < _SMALL_ITEM_THRESHOLD else max(60, base_ttl // 2)
        dynamic_ttl = min(self.max_cache_ttl, dynamic)
        
        async with self.cache_lock:
            available_memory = self.max_cache_memory - self.current_cache_memory
            if content_size > available_memory:
                await self._evict_cache_lru(content_size - available_memory + (content_size // 4))
            
            entry = CacheEntry(
                content=data,
                size=content_size,
                created_at=current_time,
                last_accessed=current_time,
                access_count=1,
                file_mtime_ns=file_mtime_ns,
                expires_at=current_time + dynamic_ttl
            )
            
            self.cache[cache_key] = entry
            self.current_cache_memory += content_size

    async def get_word_timings_range(
        self,
        track_id: str,
        voice_id: str,
        start: float,
        end: float,
        limit: int = 10000,
        db: Optional[Session] = None
    ) -> List[Dict]:
        """
        Get word timings within a time range using binary search.
        
        Args:
            track_id: Track ID
            voice_id: Voice ID
            start: Start time in seconds
            end: End time in seconds
            limit: Maximum words to return
            db: Optional database session
        
        Returns:
            List of word timing dictionaries within the range
        """
        try:
            # Get all words for this voice
            words = await self.get_word_timings(track_id, voice_id, db)
            
            if not words:
                return []
            
            # Build list of start times for binary search
            starts = [w.get("start_time", 0.0) for w in words]
            
            # Find starting index with small cushion for boundary words
            i = max(0, bisect.bisect_left(starts, start) - 5)
            
            # Collect words in range
            result: List[Dict] = []
            for w in words[i:]:
                word_start = w["start_time"]
                word_end = w.get("end_time", word_start + w.get("duration", 0.0))
                
                # Stop if we're past the end time
                if word_start > end:
                    break
                
                # Include if word overlaps with [start, end]
                if word_end >= start:
                    result.append(w)
                    
                    # Respect limit
                    if len(result) >= limit:
                        break
            
            logger.info(f"Word range query: {track_id}:{voice_id} [{start:.1f}-{end:.1f}s] = {len(result)} words")
            return result
            
        except Exception as e:
            logger.error(f"Error getting word timings range for {track_id}:{voice_id}: {e}")
            return []
    
    async def store_source_text(self, track_id: str, text: str, db: Optional[Session] = None) -> Dict[str, Any]:
        lock_key = self._lock_key("text", track_id)
        async with self._file_locks[lock_key]:
            if not text or not text.strip():
                raise TextStorageError("Cannot store empty text")
            
            text_bytes = text.encode('utf-8')
            text_hash = self._calculate_hash(text_bytes)
            
            await self._ensure_track_directories(track_id)
            file_path = self._get_text_file_path(track_id)
            compressed_data = await self._compress_async(text_bytes)
            
            temp_dir = self._get_temp_dir(track_id)
            temp_path = temp_dir / f"text_{int(time.time())}.tmp"
            
            async with aiofiles.open(temp_path, 'wb') as f:
                await f.write(compressed_data)
                await f.flush()
                try:
                    await anyio.to_thread.run_sync(os.fsync, f.fileno())
                except Exception:
                    pass
            
            await self._rename(temp_path, file_path)
            cache_key = f"text:{track_id}"
            await self._cache_set(cache_key, text, file_path)
            
            relative_path = file_path.relative_to(self.hls_segment_dir)
            
            result = {
                'file_path': str(relative_path),
                'absolute_path': str(file_path),
                'hash': text_hash,
                'original_size': len(text_bytes),
                'compressed_size': len(compressed_data),
                'compression_ratio': len(compressed_data) / len(text_bytes),
                'compression_method': COMPRESSION_AVAILABLE,
                'word_count': len(text.split()),
                'character_count': len(text),
                'track_id': track_id,
                'created_at': datetime.now(timezone.utc).isoformat()
            }
            
            if db:
                await self._update_track_file_metadata(track_id, result, db)
                await self._create_file_storage_metadata(track_id, 'source_text', result, db)
            
            self.total_files_created += 1
            self.total_bytes_written += len(compressed_data)
            self.operations_count += 1
            
            return result
    
    async def get_source_text(self, track_id: str, db: Optional[Session] = None, bypass_cache: bool = False) -> Optional[str]:
        if not bypass_cache:
            cache_key = f"text:{track_id}"
            cached_content = await self._cache_get(cache_key)
            if cached_content:
                return cached_content
        
        file_path = self._get_text_file_path(track_id)
        
        if await self._exists(file_path):
            async with aiofiles.open(file_path, 'rb') as f:
                compressed_data = await f.read()
            
            if compressed_data:
                text_bytes = await self._decompress_async(compressed_data)
                text = text_bytes.decode('utf-8')
                
                if not bypass_cache:
                    await self._cache_set(f"text:{track_id}", text, file_path)
                
                self.total_bytes_read += len(compressed_data)
                self.operations_count += 1
                return text
        
        return None
    
    async def store_word_timings(self, track_id: str, voice_id: str, word_timings: List[Dict], db: Optional[Session] = None) -> Dict[str, Any]:
        lock_key = self._lock_key("timing", track_id, voice_id)
        async with self._file_locks[lock_key]:
            if not word_timings:
                raise TextStorageError("Cannot store empty word timings")
            
            packed_data = await self._pack_word_timings_async(word_timings)
            timing_hash = self._calculate_hash(packed_data)
            
            await self._ensure_track_directories(track_id)
            await self._ensure_voice_directory(track_id, voice_id)
            
            file_path = self._get_timing_file_path(track_id, voice_id)
            compressed_data = await self._compress_async(packed_data)
            
            temp_dir = self._get_temp_dir(track_id)
            if not await self._exists(temp_dir):
                await self._mkdir(temp_dir)
            
            temp_path = temp_dir / f"timing_{voice_id}_{int(time.time())}.tmp"
            
            async with aiofiles.open(temp_path, 'wb') as f:
                await f.write(compressed_data)
                await f.flush()
                try:
                    await anyio.to_thread.run_sync(os.fsync, f.fileno())
                except Exception:
                    pass
            
            await self._rename(temp_path, file_path)
            
            relative_path = file_path.relative_to(self.hls_segment_dir)
            
            result = {
                'file_path': str(relative_path),
                'absolute_path': str(file_path),
                'hash': timing_hash,
                'original_size': len(packed_data),
                'compressed_size': len(compressed_data),
                'compression_ratio': len(compressed_data) / len(packed_data),
                'compression_method': COMPRESSION_AVAILABLE,
                'word_count': len(word_timings),
                'track_id': track_id,
                'voice_id': voice_id,
                'first_word_time': word_timings[0]['start_time'],
                'last_word_time': word_timings[-1]['end_time'],
                'total_duration': word_timings[-1]['end_time'] - word_timings[0]['start_time'],
                'created_at': datetime.now(timezone.utc).isoformat()
            }
            
            if db:
                await self._update_timing_file_metadata(track_id, voice_id, result, db)
                await self._create_file_storage_metadata(track_id, 'word_timings', result, db, voice_id)
            
            self.total_files_created += 1
            self.total_bytes_written += len(compressed_data)
            self.operations_count += 1
            
            return result
    
    # ====================================================================
    # OPTIMIZED: STREAMING TIMING STORAGE FROM FILES
    # ====================================================================
    
    async def store_word_timings_from_files(
        self, 
        track_id: str, 
        voice_id: str, 
        timings_dir: Path, 
        total_duration: float,
        db: Optional[Session] = None
    ) -> Dict[str, Any]:
        """
        Store word timings by streaming from individual chunk files directly to a compressed file.
        Avoids loading all timings into memory at once.
        """
        lock_key = self._lock_key("timing", track_id, voice_id)
        async with self._file_locks[lock_key]:
            if not await self._exists(timings_dir):
                raise TextStorageError(f"Timings directory not found: {timings_dir}")
            
            # Get all timing files in order
            timing_files = sorted(timings_dir.glob("chunk_*.json"))
            if not timing_files:
                raise TextStorageError(f"No timing files found in {timings_dir}")
            
            # Prepare output file
            await self._ensure_track_directories(track_id)
            await self._ensure_voice_directory(track_id, voice_id)
            
            file_path = self._get_timing_file_path(track_id, voice_id)
            temp_dir = self._get_temp_dir(track_id)
            if not await self._exists(temp_dir):
                await self._mkdir(temp_dir)
            
            # Count total words for header
            total_words = 0
            for timing_file in timing_files:
                async with aiofiles.open(timing_file, 'r') as f:
                    content = await f.read()
                    if content:
                        data = json.loads(content)
                        word_boundaries = data.get('word_boundaries', [])
                        total_words += len(word_boundaries)
            
            # Build header once (we know totals)
            first_time = 0.0
            last_time  = float(total_duration)
            header = struct.pack(_HEADER_FMT, _TIMING_FMT_VERSION, total_words, first_time, last_time, 0)
            
            # Create a temporary file to store uncompressed data first
            tmp_uncompressed = temp_dir / f"timing_{voice_id}_{int(time.time())}.tmp"
            uncompressed_size = 0
            
            # First write all data to an uncompressed file
            async with aiofiles.open(tmp_uncompressed, 'wb') as out:
                # write header
                await out.write(header)
                uncompressed_size += len(header)
                
                cumulative = 0.0
                for ix, timing_file in enumerate(timing_files):
                    async with aiofiles.open(timing_file, 'r') as tf:
                        data = json.loads(await tf.read() or "{}")
                    chunk_dur = float(data.get("duration", 0.0))
                    words = data.get("word_boundaries", []) or []
                    
                    for w in words:
                        word_bytes = (w.get("word","") or "").encode("utf-8")
                        if len(word_bytes) > 255: 
                            word_bytes = word_bytes[:252] + b"..."
                        start_ms = int((float(w.get("start_time",0.0)) + cumulative) * 1000)
                        dur_ms   = int(float(w.get("duration",0.0)) * 1000)
                        seg_idx  = int(w.get("segment_index", w.get("chunk_index", 0)))
                        flags    = 0
                        
                        packed = (struct.pack("<B", len(word_bytes)) + word_bytes +
                                  struct.pack(_WORD_META_FMT, start_ms, dur_ms, seg_idx, flags))
                        
                        await out.write(packed)
                        uncompressed_size += len(packed)
                    
                    cumulative += chunk_dur
            
            # Now compress the file
            tmp_zst = temp_dir / f"timing_{voice_id}_{int(time.time())}.zst.tmp"
            
            # Read the uncompressed file and compress it
            async with aiofiles.open(tmp_uncompressed, 'rb') as fin:
                uncompressed_data = await fin.read()
                
            compressed_data = await self._compress_async(uncompressed_data)
            
            # Write compressed data to file
            async with aiofiles.open(tmp_zst, 'wb') as out:
                await out.write(compressed_data)
            
            # Clean up uncompressed file
            await self._unlink(tmp_uncompressed)
            
            # Calculate hash
            timing_hash = self._calculate_hash(compressed_data)
            
            # Atomic rename
            await self._rename(tmp_zst, file_path)
            
            relative_path = file_path.relative_to(self.hls_segment_dir)
            
            result = {
                'file_path': str(relative_path),
                'absolute_path': str(file_path),
                'hash': timing_hash,
                'original_size': uncompressed_size,  # This should be the uncompressed size
                'compressed_size': len(compressed_data),
                'compression_ratio': len(compressed_data) / uncompressed_size,
                'compression_method': COMPRESSION_AVAILABLE,
                'word_count': total_words,
                'track_id': track_id,
                'voice_id': voice_id,
                'first_word_time': first_time,
                'last_word_time': last_time,
                'total_duration': total_duration,
                'created_at': datetime.now(timezone.utc).isoformat()
            }
            
            if db:
                await self._update_timing_file_metadata(track_id, voice_id, result, db)
                await self._create_file_storage_metadata(track_id, 'word_timings', result, db, voice_id)
            
            self.total_files_created += 1
            self.total_bytes_written += len(compressed_data)
            self.operations_count += 1
            
            return result
    
    async def append_word_timings(self, track_id: str, voice_id: str, word_batch: List[Dict], db: Optional[Session] = None) -> bool:
        if not word_batch:
            return True
        
        try:
            await self._ensure_voice_directory(track_id, voice_id)
            parts_dir = self._get_timings_parts_dir(track_id, voice_id)
            await self._mkdir(parts_dir)
            
            timestamp_ms = int(time.time() * 1000)
            shard_name = f"part-{timestamp_ms:016d}.bin.zst"
            shard_path = parts_dir / shard_name
            temp_path = parts_dir / f"{shard_name}.tmp"
            
            packed_data = await self._pack_word_timings_async(word_batch)
            compressed_data = await self._compress_async(packed_data)
            
            async with aiofiles.open(temp_path, 'wb') as f:
                await f.write(compressed_data)
                await f.flush()
                try:
                    await anyio.to_thread.run_sync(os.fsync, f.fileno())
                except Exception:
                    pass
            
            await self._rename(temp_path, shard_path)
            
            self.total_bytes_written += len(compressed_data)
            self.operations_count += 1
            
            try:
                shards = await anyio.to_thread.run_sync(lambda: list(parts_dir.glob("part-*.bin.zst")))
                total_size = sum((sf.stat().st_size for sf in shards))
                if len(shards) >= 64 or total_size > 64 * 1024 * 1024:
                    asyncio.create_task(self.consolidate_timing_shards(track_id, voice_id, db=None))
            except Exception:
                pass
            
            return True
            
        except Exception as e:
            logger.error(f"Append timing shard failed {track_id}:{voice_id}: {e}")
            if 'temp_path' in locals() and await self._exists(temp_path):
                try:
                    await self._unlink(temp_path)
                except Exception:
                    pass
            return False
    
    async def get_word_timings(self, track_id: str, voice_id: str, db: Optional[Session] = None) -> List[Dict]:
        cache_key = f"timing:{track_id}:{voice_id}"
        cached_packed = await self._cache_get_bytes(cache_key)
        if cached_packed:
            return await self._unpack_word_timings_async(cached_packed)
        
        parts_dir = self._get_timings_parts_dir(track_id, voice_id)
        has_parts = await self._exists(parts_dir)
        shard_files = []
        if has_parts:
            shard_files = sorted(await anyio.to_thread.run_sync(lambda: list(parts_dir.glob("part-*.bin.zst"))))
        
        all_words: List[Dict] = []
        
        if has_parts and shard_files:
            for shard_file in shard_files:
                try:
                    async with aiofiles.open(shard_file, 'rb') as f:
                        compressed_data = await f.read()
                    if compressed_data:
                        packed_data = await self._decompress_async(compressed_data)
                        shard_words = await self._unpack_word_timings_async(packed_data)
                        all_words.extend(shard_words)
                except Exception:
                    continue
        
        if not all_words:
            main_file = self._get_timing_file_path(track_id, voice_id)
            if await self._exists(main_file):
                async with aiofiles.open(main_file, 'rb') as f:
                    compressed_data = await f.read()
                if compressed_data:
                    packed_data = await self._decompress_async(compressed_data)
                    all_words = await self._unpack_word_timings_async(packed_data)
        
        if not all_words:
            return []
        
        all_words.sort(key=lambda w: w.get('start_time', 0))
        packed_merged = await self._pack_word_timings_async(all_words)
        await self._cache_set_bytes(cache_key, packed_merged)
        self.operations_count += 1
        
        return all_words
    
    async def consolidate_timing_shards(self, track_id: str, voice_id: str, db: Optional[Session] = None) -> bool:
        lock_key = self._lock_key("timing", track_id, voice_id)
        async with self._file_locks[lock_key]:
            try:
                parts_dir = self._get_timings_parts_dir(track_id, voice_id)
                if not await self._exists(parts_dir):
                    return True
                
                shard_files = sorted(await anyio.to_thread.run_sync(lambda: list(parts_dir.glob("part-*.bin.zst"))))
                if not shard_files:
                    return True
                
                all_words: List[Dict] = []
                for shard_file in shard_files:
                    try:
                        async with aiofiles.open(shard_file, 'rb') as f:
                            compressed_data = await f.read()
                        packed_data = await self._decompress_async(compressed_data)
                        shard_words = await self._unpack_word_timings_async(packed_data)
                        all_words.extend(shard_words)
                    except Exception:
                        continue
                
                if not all_words:
                    return False
                
                all_words.sort(key=lambda w: w.get('start_time', 0))
                
                file_path = self._get_timing_file_path(track_id, voice_id)
                packed_data = await self._pack_word_timings_async(all_words)
                compressed_data = await self._compress_async(packed_data)
                
                temp_dir = self._get_temp_dir(track_id)
                temp_path = temp_dir / f"timing_{voice_id}_consolidated_{int(time.time())}.tmp"
                
                async with aiofiles.open(temp_path, 'wb') as f:
                    await f.write(compressed_data)
                    await f.flush()
                    try:
                        await anyio.to_thread.run_sync(os.fsync, f.fileno())
                    except Exception:
                        pass
                
                await self._rename(temp_path, file_path)
                
                for shard_file in shard_files:
                    try:
                        await self._unlink(shard_file)
                    except Exception:
                        pass
                
                try:
                    await self._rmtree(parts_dir, ignore_errors=True)
                except Exception:
                    pass
                
                cache_key = f"timing:{track_id}:{voice_id}"
                async with self.cache_lock:
                    self.cache.pop(cache_key, None)
                
                logger.info(f"Consolidated {len(shard_files)} shards -> {len(all_words)} words: {track_id}:{voice_id}")
                return True
                
            except Exception:
                return False
    
    def _pack_word_timings_v4(self, word_timings: List[Dict]) -> bytes:
        header = struct.pack(_HEADER_FMT, _TIMING_FMT_VERSION, len(word_timings),
                           float(word_timings[0]['start_time']), float(word_timings[-1]['end_time']), 0)
        packed_words = []
        for wd in word_timings:
            word_bytes = wd['word'].encode('utf-8')
            if len(word_bytes) > 255:
                word_bytes = word_bytes[:252] + b'...'
            start_ms = int(wd['start_time'] * 1000)
            duration_ms = int(wd.get('duration', wd['end_time'] - wd['start_time']) * 1000)
            segment_idx = int(wd.get('segment_index', 0))
            flags = 0
            has_seg_off = 'segment_offset' in wd
            if has_seg_off:
                flags |= 1
            packed = (struct.pack("<B", len(word_bytes)) + word_bytes +
                     struct.pack(_WORD_META_FMT, start_ms, duration_ms, segment_idx, flags))
            if has_seg_off:
                segment_offset_ms = int(wd.get('segment_offset', 0) * 1000)
                packed += struct.pack(_WORD_OFFSET_FMT, segment_offset_ms)
            packed_words.append(packed)
        return header + b"".join(packed_words)
    
    def _unpack_word_timings_v4(self, data: bytes) -> List[Dict]:
        if len(data) < _HEADER_SIZE:
            return []
        try:
            version, word_count, first_time, last_time, _reserved = struct.unpack(_HEADER_FMT, data[:_HEADER_SIZE])
        except struct.error:
            return []
        if version != _TIMING_FMT_VERSION:
            return []
        words: List[Dict] = []
        offset = _HEADER_SIZE
        for _ in range(word_count):
            if offset + 1 > len(data):
                break
            word_len = data[offset]
            offset += 1
            need = word_len + _WORD_META_SIZE
            if offset + need > len(data):
                break
            word = data[offset:offset + word_len].decode('utf-8', errors='replace')
            offset += word_len
            try:
                start_ms, duration_ms, segment_idx, flags = struct.unpack(_WORD_META_FMT, data[offset:offset + _WORD_META_SIZE])
            except struct.error:
                break
            offset += _WORD_META_SIZE
            wd = {
                'word': word,
                'start_time': start_ms / 1000.0,
                'duration': duration_ms / 1000.0,
                'end_time': (start_ms + duration_ms) / 1000.0,
                'segment_index': segment_idx
            }
            if flags & 0x01:
                if offset + _WORD_OFFSET_SIZE > len(data):
                    break
                (segment_offset_ms,) = struct.unpack(_WORD_OFFSET_FMT, data[offset:offset + _WORD_OFFSET_SIZE])
                wd['segment_offset'] = segment_offset_ms / 1000.0
                offset += _WORD_OFFSET_SIZE
            words.append(wd)
        return words
    
    async def _update_track_file_metadata(self, track_id: str, result: Dict, db):
        try:
            res = await _exec(db, select(Track).where(Track.id == track_id))
            track = res.scalar_one_or_none()
            if track:
                track.source_text_path = result['file_path']
                track.source_text_hash = result['hash']
                track.source_text_size = result['original_size']
                track.source_text_compressed_size = result['compressed_size']
                await _commit(db)
        except Exception:
            await _rollback(db)
    
    async def _update_timing_file_metadata(self, track_id: str, voice_id: str, result: Dict, db):
        try:
            from models import TTSTextSegment
            res = await _exec(db, select(TTSWordTiming).join(TTSTextSegment, TTSWordTiming.segment_id == TTSTextSegment.id)
                            .where(TTSTextSegment.track_id == track_id, TTSWordTiming.voice_id == voice_id))
            timing_records = res.scalars().all()
            for timing in timing_records:
                timing.timings_file_path = result['file_path']
                timing.timings_file_hash = result['hash']
                timing.timings_file_size = result['compressed_size']
            await _commit(db)
        except Exception:
            await _rollback(db)
    
    async def _create_file_storage_metadata(self, track_id: str, file_type: str, result: Dict, db, voice_id: Optional[str] = None):
        try:
            from sqlalchemy.dialects.postgresql import insert
            metadata_data = {
                'file_path': result['file_path'],
                'file_hash': result['hash'],
                'file_type': file_type,
                'track_id': track_id,
                'voice_id': voice_id,
                'original_size': result['original_size'],
                'compressed_size': result['compressed_size'],
                'compression_method': result['compression_method'],
                'compression_level': self.compression_level,
                'status': 'active'
            }
            stmt = insert(FileStorageMetadata).values(**metadata_data)
            stmt = stmt.on_conflict_do_update(
                index_elements=['file_path'],
                set_={'file_hash': stmt.excluded.file_hash, 'original_size': stmt.excluded.original_size,
                     'compressed_size': stmt.excluded.compressed_size}
            )
            await _exec(db, stmt)
            await _commit(db)
        except Exception:
            await _rollback(db)
    
    async def get_cache_stats(self) -> CacheStats:
        async with self.cache_lock:
            if not self.cache:
                return CacheStats(0, 0.0, 0.0, 0, 0, 0.0, 0.0)
            current_time = time.time()
            expired_count = sum(1 for entry in self.cache.values() if current_time > entry.expires_at)
            total_accesses = self.cache_hits + self.cache_misses
            hit_ratio = (self.cache_hits / total_accesses * 100) if total_accesses > 0 else 0
            sizes = [entry.size for entry in self.cache.values()]
            avg_size = sum(sizes) / len(sizes) if sizes else 0
            ages = [current_time - entry.created_at for entry in self.cache.values()]
            oldest_age = max(ages) if ages else 0
            return CacheStats(len(self.cache), self.current_cache_memory / (1024 * 1024), hit_ratio,
                            expired_count, self.cache_stale_hits, avg_size / 1024, oldest_age / 3600)
    
    async def get_statistics(self) -> Dict[str, Any]:
        cache_stats = await self.get_cache_stats()
        return {
            'operations_count': self.operations_count,
            'total_files_created': self.total_files_created,
            'total_bytes_written': self.total_bytes_written,
            'total_bytes_read': self.total_bytes_read,
            'cache_entries': cache_stats.total_entries,
            'cache_memory_mb': cache_stats.total_memory_mb,
            'cache_max_memory_mb': self.max_cache_memory // 1024 // 1024,
            'cache_hit_ratio': cache_stats.hit_ratio,
            'cache_hits': self.cache_hits,
            'cache_misses': self.cache_misses,
            'cache_evictions': self.cache_evictions,
            'storage_architecture': 'track_centric_v4',
            'compression_method': COMPRESSION_AVAILABLE,
            'format_version': _TIMING_FMT_VERSION
        }
    
    async def health_check(self) -> Dict[str, Any]:
        health_status = {
            'storage_accessible': False,
            'hls_segment_dir_set': self.hls_segment_dir is not None,
            'compression_available': COMPRESSION_AVAILABLE is not None,
            'cache_functional': False,
            'cleanup_task_running': False,
            'memory_usage_ok': False
        }
        try:
            if not self.hls_segment_dir:
                health_status['error'] = "HLS segment directory not initialized"
                health_status['overall_healthy'] = False
                return health_status
            test_track_dir = self.hls_segment_dir / "health_check_track"
            test_temp_dir = test_track_dir / "temp"
            await self._mkdir(test_temp_dir)
            test_file = test_temp_dir / "health_check.tmp"
            async with aiofiles.open(test_file, "w") as f:
                await f.write("health check")
            await self._unlink(test_file)
            await self._rmtree(test_track_dir)
            health_status['storage_accessible'] = True
            health_status['cache_functional'] = True
            health_status['cleanup_task_running'] = self.cleanup_task is not None and not self.cleanup_task.done()
            memory_usage_ratio = self.current_cache_memory / self.max_cache_memory
            health_status['memory_usage_ok'] = memory_usage_ratio < 0.95
            cache_stats = await self.get_cache_stats()
            health_status.update({
                'cache_hit_ratio': cache_stats.hit_ratio,
                'cache_memory_mb': cache_stats.total_memory_mb,
                'cache_entries': cache_stats.total_entries
            })
        except Exception as e:
            health_status['error'] = str(e)
        health_status['overall_healthy'] = all([
            health_status['storage_accessible'], health_status['hls_segment_dir_set'],
            health_status['cache_functional'], health_status['cleanup_task_running'],
            health_status['memory_usage_ok']
        ])
        return health_status
    
    async def cleanup(self):
        self._shutdown = True
        if self.cleanup_task and not self.cleanup_task.done():
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
        async with self.cache_lock:
            self.cache.clear()
            self.current_cache_memory = 0

text_storage_service = TrackCentricTextStorageService(
    max_cache_memory_mb=8192,
    cache_ttl_seconds=3600,
    max_cache_ttl_seconds=86400,
    cleanup_interval_seconds=300
)

async def initialize_text_storage():
    global text_storage_service
    try:
        from hls_streaming import stream_manager
        if hasattr(stream_manager, 'segment_dir') and stream_manager.segment_dir:
            text_storage_service.set_hls_segment_dir(stream_manager.segment_dir)
    except ImportError:
        raise TextStorageError("Stream manager not available for text storage")
    text_storage_service._start_cleanup_task()
    health = await text_storage_service.health_check()
    return text_storage_service