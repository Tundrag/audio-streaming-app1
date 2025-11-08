# read_along_cache.py
"""
Production-ready read-along cache system with version-based invalidation.
Implements atomic writes, corruption handling, thundering herd prevention, 
and automatic cleanup.
"""
import hashlib
import msgpack
import asyncio
import os
import time
from pathlib import Path
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from models import Track
from collections import defaultdict
import logging
from redis_state.state_manager import RedisStateManager

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================
CACHE_DIR = Path(os.getenv("READALONG_CACHE_DIR", "/tmp/media_storage/readalong_cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_PAGE_SIZE = 300
MAX_PAGE_SIZE = 600
TOKENIZATION_CONCURRENCY = 6

# Cleanup configuration
MAX_VERSIONS_TO_KEEP = 2  # Keep current + 1 previous version
MAX_CACHE_SIZE_GB = 2.0   # Maximum total cache size
CACHE_TTL_DAYS = 7        # Delete cache older than this
CLEANUP_INTERVAL_HOURS = 6  # Run cleanup every N hours

# ============================================================================
# OBSERVABILITY
# ============================================================================
_cache_stats = {
    "hits": 0,
    "misses": 0,
    "writes": 0,
    "write_errors": 0,
    "read_errors": 0,
    "recompute_times": [],  # Keep last 100
    "corrupted_files": 0
}
_stats_lock = asyncio.Lock()

async def record_cache_hit():
    """Record a cache hit"""
    async with _stats_lock:
        _cache_stats["hits"] += 1

async def record_cache_miss(recompute_ms: float):
    """Record a cache miss and recompute time"""
    async with _stats_lock:
        _cache_stats["misses"] += 1
        _cache_stats["recompute_times"].append(recompute_ms)
        if len(_cache_stats["recompute_times"]) > 100:
            _cache_stats["recompute_times"].pop(0)

async def record_cache_write():
    """Record a successful cache write"""
    async with _stats_lock:
        _cache_stats["writes"] += 1

async def record_write_error():
    """Record a cache write error"""
    async with _stats_lock:
        _cache_stats["write_errors"] += 1

async def record_read_error():
    """Record a cache read error"""
    async with _stats_lock:
        _cache_stats["read_errors"] += 1

async def record_corrupted_file():
    """Record a corrupted cache file"""
    async with _stats_lock:
        _cache_stats["corrupted_files"] += 1

async def get_cache_stats() -> Dict[str, Any]:
    """Get cache statistics"""
    async with _stats_lock:
        total = _cache_stats["hits"] + _cache_stats["misses"]
        hit_rate = (_cache_stats["hits"] / total * 100) if total > 0 else 0
        
        times = _cache_stats["recompute_times"]
        avg_recompute = sum(times) / len(times) if times else 0
        max_recompute = max(times) if times else 0
        min_recompute = min(times) if times else 0
        
        # Calculate disk usage
        try:
            total_size = sum(f.stat().st_size for f in CACHE_DIR.rglob("*.msgpack"))
            total_size_mb = total_size / (1024 ** 2)
        except Exception:
            total_size_mb = 0
        
        return {
            "cache_hits": _cache_stats["hits"],
            "cache_misses": _cache_stats["misses"],
            "cache_writes": _cache_stats["writes"],
            "hit_rate_percent": round(hit_rate, 2),
            "avg_recompute_ms": round(avg_recompute, 2),
            "max_recompute_ms": round(max_recompute, 2),
            "min_recompute_ms": round(min_recompute, 2),
            "total_requests": total,
            "write_errors": _cache_stats["write_errors"],
            "read_errors": _cache_stats["read_errors"],
            "corrupted_files": _cache_stats["corrupted_files"],
            "disk_usage_mb": round(total_size_mb, 2),
            "disk_limit_gb": MAX_CACHE_SIZE_GB
        }

# ============================================================================
# DISTRIBUTED LOCK MANAGEMENT (Redis-backed for multi-container coordination)
# ============================================================================

# Initialize Redis state manager for readalong namespace
_readalong_state = RedisStateManager("readalong")

# Local asyncio locks for in-container concurrency (performance optimization)
# These prevent multiple asyncio tasks in the SAME container from hitting Redis
_local_locks: Dict[str, asyncio.Lock] = {}
_local_lock_creation = asyncio.Lock()


class RedisDistributedLock:
    """
    Distributed lock using Redis for multi-container coordination.
    Follows asyncio.Lock interface but provides cross-container synchronization.

    Architecture:
    - Local asyncio.Lock: Prevents same-container contention (fast path)
    - Redis lock: Prevents cross-container contention (distributed coordination)

    This hybrid approach follows the pattern from conversion.py (lines 26-38).
    """

    def __init__(self, cache_key: str, timeout: int = 60):
        self.cache_key = cache_key
        self.timeout = timeout
        self.local_lock: Optional[asyncio.Lock] = None
        self.redis_acquired = False

    async def __aenter__(self):
        """Acquire both local and distributed locks"""
        # Step 1: Acquire local lock (fast path for same-container tasks)
        async with _local_lock_creation:
            if self.cache_key not in _local_locks:
                _local_locks[self.cache_key] = asyncio.Lock()
            self.local_lock = _local_locks[self.cache_key]

        await self.local_lock.acquire()

        # Step 2: Acquire distributed Redis lock (cross-container coordination)
        # Try for up to timeout seconds with exponential backoff
        max_attempts = 30
        attempt = 0
        backoff = 0.1  # Start with 100ms

        while attempt < max_attempts:
            # Atomic acquire via SET NX (only if not exists)
            self.redis_acquired = _readalong_state.acquire_lock(
                self.cache_key,
                timeout=self.timeout,
                owner_id=_readalong_state.container_id
            )

            if self.redis_acquired:
                logger.debug(
                    f"Acquired distributed lock: {self.cache_key} "
                    f"by {_readalong_state.container_id} (attempt {attempt + 1})"
                )
                return self

            # Lock held by another container, wait with exponential backoff
            attempt += 1
            await asyncio.sleep(min(backoff, 5.0))  # Cap at 5 seconds
            backoff *= 1.5  # Exponential backoff

        # Timeout: proceed anyway (degrade gracefully)
        logger.warning(
            f"Failed to acquire distributed lock after {max_attempts} attempts: {self.cache_key}. "
            f"Proceeding without cross-container lock (potential duplicate work)."
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Release both distributed and local locks"""
        # Release distributed Redis lock
        if self.redis_acquired:
            success = _readalong_state.release_lock(self.cache_key)
            if success:
                logger.debug(f"Released distributed lock: {self.cache_key}")
            else:
                logger.warning(f"Failed to release distributed lock: {self.cache_key}")

        # Release local asyncio lock
        if self.local_lock and self.local_lock.locked():
            self.local_lock.release()

        return False  # Don't suppress exceptions


async def get_page_lock(cache_key: str) -> RedisDistributedLock:
    """
    Get distributed lock for a cache key to prevent duplicate work across containers.

    Returns:
        RedisDistributedLock: Async context manager for distributed locking

    Usage:
        lock = await get_page_lock(cache_key)
        async with lock:
            # Only ONE container across the cluster will execute this block
            result = expensive_computation()
    """
    return RedisDistributedLock(cache_key, timeout=60)


async def cleanup_page_lock(cache_key: str):
    """
    Clean up local lock after use to prevent memory leak.
    Distributed Redis locks auto-expire via TTL (no manual cleanup needed).
    """
    async with _local_lock_creation:
        _local_locks.pop(cache_key, None)

    logger.debug(f"Cleaned up local lock: {cache_key}")

# ============================================================================
# VERSION MANAGEMENT
# ============================================================================
def get_cache_version(track_id: str, voice_id: str, db: Session) -> str:
    """
    Get cache version string for a track+voice.
    Returns version like 'v1', 'v2', etc.
    Version changes when Track.content_version changes (text edits).
    """
    try:
        track = db.query(Track).filter_by(id=track_id).first()
        if track and track.content_version:
            return f"v{track.content_version}"
    except Exception as e:
        logger.warning(f"Version check failed for {track_id}: {e}")
    
    # Fallback: unique but uncached
    return f"v0_{hashlib.md5(f'{track_id}:{voice_id}'.encode()).hexdigest()[:8]}"

def increment_version(track_id: str, db: Session) -> int:
    """
    Increment track version number.
    Call this AFTER successful TTS regeneration.
    Returns new version number.
    """
    try:
        track = db.query(Track).filter_by(id=track_id).first()
        if track:
            old_version = track.content_version or 0
            track.content_version = old_version + 1
            db.commit()
            logger.info(f"Version incremented: {track_id} v{old_version} → v{track.content_version}")
            return track.content_version
    except Exception as e:
        logger.error(f"Version increment failed for {track_id}: {e}")
        db.rollback()
        raise
    return 0

# ============================================================================
# CACHE KEY GENERATION
# ============================================================================
async def get_page_cache_key(
    track_id: str, 
    voice_id: str, 
    page: int, 
    page_size: int, 
    db: Session
) -> str:
    """
    Generate versioned cache key for a page.
    Format: trackid::voiceid::version::pagesize::pN
    Uses :: as delimiter to avoid conflicts with _ in track IDs
    """
    version = get_cache_version(track_id, voice_id, db)
    # ⭐ FIX: Use :: instead of _ to avoid splitting track_id
    return f"{track_id}::{voice_id}::{version}::{page_size}::p{page}"

def get_cache_path(cache_key: str) -> Path:
    """
    Convert cache key to filesystem path.
    trackid::voiceid::v2::300::p0 → /cache/trackid/voiceid/v2/300/p0.msgpack
    """
    # ⭐ FIX: Split on :: instead of _
    parts = cache_key.split('::')
    
    if len(parts) >= 5:
        track_id = parts[0]
        voice_id = parts[1]
        version = parts[2]    # v1, v2, etc.
        page_size = parts[3]  # 300, 500, etc.
        page = parts[4]       # p0, p1, etc.
        
        # Replace :: with _ in path components to make filesystem-safe
        # But keep structure separate
        safe_track_id = track_id.replace('/', '_')
        safe_voice_id = voice_id.replace('/', '_')
        
        return CACHE_DIR / safe_track_id / safe_voice_id / version / page_size / f"{page}.msgpack"
    
    # Fallback for malformed keys
    safe_key = cache_key.replace('/', '_').replace('::', '_')
    return CACHE_DIR / f"{safe_key}.msgpack"
# ============================================================================
# CACHE OPERATIONS (ATOMIC + CORRUPTION HANDLING)
# ============================================================================
async def get_cached_page(cache_key: str) -> Optional[Dict[str, Any]]:
    """
    Read page from disk cache with corruption handling.
    Returns None if not cached or corrupted.
    """
    try:
        path = get_cache_path(cache_key)
        if path.exists():
            data = msgpack.unpackb(path.read_bytes(), raw=False)
            logger.debug(f"Cache hit: {cache_key}")
            await record_cache_hit()
            return data
    except (msgpack.exceptions.ExtraData, msgpack.exceptions.UnpackException) as e:
        # Corrupted cache file - delete and recompute
        logger.warning(f"Corrupted cache file {cache_key}, deleting: {e}")
        await record_corrupted_file()
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        return None
    except Exception as e:
        logger.debug(f"Cache read failed for {cache_key}: {e}")
        await record_read_error()
    
    return None

async def set_cached_page(cache_key: str, data: Dict[str, Any]) -> None:
    """
    Write page to disk cache atomically.
    Uses temporary file + atomic rename to prevent corruption.
    """
    try:
        path = get_cache_path(cache_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write to temporary file first
        tmp_path = path.with_suffix('.tmp')
        packed = msgpack.packb(data, use_bin_type=True)
        tmp_path.write_bytes(packed)
        
        # Atomic rename (on same filesystem)
        os.replace(tmp_path, path)
        
        logger.debug(f"Cache saved: {cache_key} ({len(packed)} bytes)")
        await record_cache_write()
        
    except Exception as e:
        logger.error(f"Cache write failed for {cache_key}: {e}")
        await record_write_error()
        
        # Clean up temp file if it exists
        try:
            tmp_path = path.with_suffix('.tmp')
            if tmp_path.exists():
                tmp_path.unlink()
        except:
            pass

# ============================================================================
# CACHE CLEANUP
# ============================================================================
async def clear_track_cache(track_id: str, voice_id: str) -> None:
    """
    Clear all cached pages for a track+voice.
    Call this after text edits to force regeneration.
    """
    try:
        voice_cache_dir = CACHE_DIR / track_id / voice_id
        if voice_cache_dir.exists():
            import shutil
            shutil.rmtree(voice_cache_dir)
            logger.info(f"Cache cleared: {track_id}/{voice_id}")
    except Exception as e:
        logger.error(f"Cache clear failed for {track_id}/{voice_id}: {e}")

async def clear_old_versions(track_id: str, voice_id: str, current_version: str) -> None:
    """
    Keep only current and previous version to prevent disk bloat.
    Example: If current is v5, keep v5 and v4, delete v1-v3.
    """
    try:
        voice_cache_dir = CACHE_DIR / track_id / voice_id
        if not voice_cache_dir.exists():
            return
        
        # Get all version directories with their numbers
        version_dirs = []
        for item in voice_cache_dir.iterdir():
            if item.is_dir() and item.name.startswith('v'):
                try:
                    # Extract version number (v1 -> 1, v2 -> 2, etc.)
                    version_num = int(item.name[1:])
                    version_dirs.append((version_num, item))
                except ValueError:
                    # Skip malformed version directories
                    continue
        
        if not version_dirs:
            return
        
        # Sort by version number descending (newest first)
        version_dirs.sort(reverse=True, key=lambda x: x[0])
        
        # Keep top N versions, delete the rest
        versions_to_delete = version_dirs[MAX_VERSIONS_TO_KEEP:]
        
        for version_num, version_dir in versions_to_delete:
            import shutil
            shutil.rmtree(version_dir)
            logger.info(f"Deleted old version: {track_id}/{voice_id}/v{version_num}")
            
    except Exception as e:
        logger.warning(f"Old version cleanup failed for {track_id}/{voice_id}: {e}")

async def enforce_cache_limits():
    """
    Cleanup task that runs periodically to:
    1. Delete old versions (keep only MAX_VERSIONS_TO_KEEP recent)
    2. Delete cache older than TTL
    3. Enforce total size limit
    """
    try:
        logger.info("Starting cache cleanup...")
        
        # 1. Delete old versions for each track+voice
        for track_dir in CACHE_DIR.iterdir():
            if not track_dir.is_dir():
                continue
            for voice_dir in track_dir.iterdir():
                if not voice_dir.is_dir():
                    continue
                
                # Get all version directories sorted by number
                versions = []
                for d in voice_dir.iterdir():
                    if d.is_dir() and d.name.startswith('v'):
                        try:
                            version_num = int(d.name[1:])
                            versions.append((version_num, d))
                        except ValueError:
                            continue
                
                # Sort descending and keep only MAX_VERSIONS_TO_KEEP
                versions.sort(reverse=True, key=lambda x: x[0])
                for version_num, old_version in versions[MAX_VERSIONS_TO_KEEP:]:
                    import shutil
                    shutil.rmtree(old_version)
                    logger.info(f"Cleanup: deleted old version {old_version}")
        
        # 2. Delete cache older than TTL
        cutoff_time = time.time() - (CACHE_TTL_DAYS * 86400)
        deleted_count = 0
        for cache_file in CACHE_DIR.rglob("*.msgpack"):
            try:
                if cache_file.stat().st_mtime < cutoff_time:
                    cache_file.unlink()
                    deleted_count += 1
            except Exception:
                pass
        
        if deleted_count > 0:
            logger.info(f"Cleanup: deleted {deleted_count} expired cache files")
        
        # 3. Enforce total size limit
        total_size = sum(f.stat().st_size for f in CACHE_DIR.rglob("*.msgpack"))
        total_size_gb = total_size / (1024 ** 3)
        
        if total_size_gb > MAX_CACHE_SIZE_GB:
            logger.warning(f"Cache size ({total_size_gb:.2f}GB) exceeds limit ({MAX_CACHE_SIZE_GB}GB)")
            
            # Delete oldest files until under limit
            all_files = sorted(
                CACHE_DIR.rglob("*.msgpack"),
                key=lambda f: f.stat().st_mtime
            )
            
            deleted_count = 0
            for old_file in all_files:
                if total_size <= MAX_CACHE_SIZE_GB * (1024 ** 3):
                    break
                try:
                    size = old_file.stat().st_size
                    old_file.unlink()
                    total_size -= size
                    deleted_count += 1
                except Exception:
                    pass
            
            logger.info(f"Cleanup: deleted {deleted_count} files to enforce size limit")
            total_size_gb = total_size / (1024 ** 3)
        
        logger.info(f"Cache cleanup complete. Size: {total_size_gb:.2f}GB")
        
    except Exception as e:
        logger.error(f"Cache cleanup failed: {e}")

async def start_cache_cleanup_task():
    """Background task to cleanup cache periodically"""
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL_HOURS * 3600)
            await enforce_cache_limits()
        except Exception as e:
            logger.error(f"Cache cleanup task error: {e}")
            await asyncio.sleep(3600)  # Retry in 1 hour on error