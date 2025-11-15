"""
Redis-backed text storage cache using generic RedisStateManager.
Shares text cache across all containers to reduce memory usage (8GB → shared).
"""
import logging
import time
from typing import Dict, Optional, Any, NamedTuple, Union
from redis_state.state_manager import RedisStateManager

logger = logging.getLogger(__name__)


class CacheEntry(NamedTuple):
    """Cache entry structure - same as text_storage_service.py"""
    content: Union[str, bytes]
    size: int
    created_at: float
    last_accessed: float
    access_count: int
    file_mtime_ns: int
    expires_at: float


class RedisTextCache:
    """
    Text cache manager using generic RedisStateManager.
    Provides dict-like interface for backward compatibility with text_storage_service.py

    Reduces memory usage from 8GB per container to shared 8GB across all containers.
    """

    CACHE_TTL = 3600  # 1 hour default (can be customized per entry)
    MAX_CACHE_TTL = 86400  # 24 hours max

    def __init__(self, container_id: Optional[str] = None):
        # Use generic manager with "text_cache" namespace
        self._manager = RedisStateManager("text_cache", container_id=container_id)
        self.container_id = self._manager.container_id

        # Binary-safe Redis client (decode_responses=False for pickle support)
        # Use the same connection as the manager but with binary mode
        from redis import Redis
        existing_client = self._manager.redis._get_client()
        if existing_client:
            # Get connection params from existing client
            conn_kwargs = existing_client.connection_pool.connection_kwargs.copy()
            conn_kwargs["decode_responses"] = False  # Critical: keep binary data as bytes
            self._redis = Redis(**conn_kwargs)
        else:
            # Fallback: use localhost
            self._redis = Redis(host='localhost', port=6379, decode_responses=False)

        logger.info(f"RedisTextCache initialized: container={self.container_id}")

    @property
    def cache(self):
        """Dict-like interface for backward compatibility"""
        class TextCacheDict:
            def __init__(self, parent):
                self.parent = parent

            def __contains__(self, cache_key: str) -> bool:
                """Check if cache entry exists (checks both binary and JSON storage)"""
                # Check binary storage first (faster for word timings)
                bin_key = f"text_cache:bin:{cache_key}"
                if self.parent._redis.exists(bin_key):
                    return True
                # Fallback to JSON storage
                return self.parent._manager.get_session(cache_key) is not None

            def __getitem__(self, cache_key: str) -> CacheEntry:
                """Get cache entry - optimized for binary data (no JSON/base64 overhead)"""
                import pickle
                import base64

                # Try binary-optimized path first (for word timings)
                key = f"text_cache:bin:{cache_key}"
                raw_data = self.parent._redis.get(key)

                if raw_data:
                    # Fast path: Direct binary storage (no JSON, no base64)
                    try:
                        entry_dict = pickle.loads(raw_data)
                        return CacheEntry(
                            content=entry_dict["content"],
                            size=entry_dict["size"],
                            created_at=entry_dict["created_at"],
                            last_accessed=entry_dict.get("last_accessed", entry_dict["created_at"]),
                            access_count=entry_dict.get("access_count", 1),
                            file_mtime_ns=entry_dict["file_mtime_ns"],
                            expires_at=entry_dict["expires_at"]
                        )
                    except Exception as e:
                        logger.warning(f"Binary cache read failed for {cache_key}: {e}")

                # Fallback: JSON-based storage (for text content and old cached data)
                data = self.parent._manager.get_session(cache_key)
                if data is None:
                    raise KeyError(f"Cache entry {cache_key} not found")

                # Handle old base64-encoded binary data (migration path)
                content = data["content"]
                if data.get("content_is_bytes", False):
                    content = base64.b64decode(content.encode('utf-8'))

                return CacheEntry(
                    content=content,
                    size=data["size"],
                    created_at=data["created_at"],
                    last_accessed=data.get("last_accessed", data["created_at"]),
                    access_count=data.get("access_count", 1),
                    file_mtime_ns=data["file_mtime_ns"],
                    expires_at=data["expires_at"]
                )

            def __setitem__(self, cache_key: str, value: CacheEntry):
                """Set cache entry - optimized for binary data (no JSON/base64 overhead)"""
                import pickle

                is_bytes = isinstance(value.content, bytes)

                # Calculate TTL
                ttl = int(value.expires_at - time.time())
                ttl = max(60, min(ttl, self.parent.MAX_CACHE_TTL))

                if is_bytes:
                    # 🚀 FAST: Binary-optimized storage (no JSON, no base64)
                    # Store metadata + content as pickled bytes directly in Redis
                    entry_dict = {
                        "content": value.content,  # Raw bytes, no encoding
                        "size": value.size,
                        "created_at": value.created_at,
                        "last_accessed": value.last_accessed,
                        "access_count": value.access_count,
                        "file_mtime_ns": value.file_mtime_ns,
                        "expires_at": value.expires_at
                    }

                    key = f"text_cache:bin:{cache_key}"
                    pickled = pickle.dumps(entry_dict, protocol=pickle.HIGHEST_PROTOCOL)
                    self.parent._redis.set(key, pickled, ex=ttl)

                else:
                    # String content: Use JSON-based storage (backward compatible)
                    cache_data = {
                        "content": value.content,
                        "size": value.size,
                        "created_at": value.created_at,
                        "last_accessed": value.last_accessed,
                        "access_count": value.access_count,
                        "file_mtime_ns": value.file_mtime_ns,
                        "expires_at": value.expires_at
                    }
                    self.parent._manager.create_session(cache_key, cache_data, ttl=ttl)

            def get(self, cache_key: str, default=None) -> Optional[CacheEntry]:
                """Get cache entry with default"""
                try:
                    return self[cache_key]
                except KeyError:
                    return default

            def pop(self, cache_key: str, default=None) -> Optional[CacheEntry]:
                """Remove and return cache entry (handles both binary and JSON storage)"""
                try:
                    entry = self[cache_key]
                    # Delete from both storage types
                    bin_key = f"text_cache:bin:{cache_key}"
                    self.parent._redis.delete(bin_key)
                    self.parent._manager.delete_session(cache_key)
                    return entry
                except KeyError:
                    return default

            def clear(self):
                """Clear all cache entries"""
                logger.debug("Text cache clear() called - entries will expire via TTL")

            def update_access(self, cache_key: str):
                """Update last_accessed and access_count for an entry"""
                data = self.parent._manager.get_session(cache_key)
                if data:
                    data["last_accessed"] = time.time()
                    data["access_count"] = data.get("access_count", 0) + 1

                    # Recalculate TTL
                    ttl = int(data["expires_at"] - time.time())
                    ttl = max(60, min(ttl, self.parent.MAX_CACHE_TTL))

                    self.parent._manager.update_session(cache_key, data, extend_ttl=True)

        return TextCacheDict(self)


# Global instance
text_cache = RedisTextCache()
