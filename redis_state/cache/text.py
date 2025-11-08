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
        logger.info(f"RedisTextCache initialized: container={self.container_id}")

    @property
    def cache(self):
        """Dict-like interface for backward compatibility"""
        class TextCacheDict:
            def __init__(self, parent):
                self.parent = parent

            def __contains__(self, cache_key: str) -> bool:
                """Check if cache entry exists"""
                return self.parent._manager.get_session(cache_key) is not None

            def __getitem__(self, cache_key: str) -> CacheEntry:
                """Get cache entry"""
                import base64

                data = self.parent._manager.get_session(cache_key)
                if data is None:
                    raise KeyError(f"Cache entry {cache_key} not found")

                # Decode base64 if content was bytes
                content = data["content"]
                if data.get("content_is_bytes", False):
                    content = base64.b64decode(content.encode('utf-8'))

                # Convert dict back to CacheEntry namedtuple
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
                """Set cache entry"""
                import base64

                # Handle bytes content (encode to base64 for JSON serialization)
                content = value.content
                is_bytes = isinstance(content, bytes)
                if is_bytes:
                    content = base64.b64encode(content).decode('utf-8')

                # Convert CacheEntry namedtuple to dict for Redis storage
                cache_data = {
                    "content": content,
                    "content_is_bytes": is_bytes,  # Flag to know if we need to decode
                    "size": value.size,
                    "created_at": value.created_at,
                    "last_accessed": value.last_accessed,
                    "access_count": value.access_count,
                    "file_mtime_ns": value.file_mtime_ns,
                    "expires_at": value.expires_at
                }

                # Calculate TTL based on expires_at
                ttl = int(value.expires_at - time.time())
                ttl = max(60, min(ttl, self.parent.MAX_CACHE_TTL))  # Clamp between 1 min and 24 hours

                self.parent._manager.create_session(cache_key, cache_data, ttl=ttl)

            def get(self, cache_key: str, default=None) -> Optional[CacheEntry]:
                """Get cache entry with default"""
                try:
                    return self[cache_key]
                except KeyError:
                    return default

            def pop(self, cache_key: str, default=None) -> Optional[CacheEntry]:
                """Remove and return cache entry"""
                try:
                    entry = self[cache_key]
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
