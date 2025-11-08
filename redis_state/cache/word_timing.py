"""
Redis-backed word timing cache using generic RedisStateManager.
Shares computed word timings across all containers to avoid duplicate computation.
"""
import logging
from typing import Dict, Optional, Any, List
from redis_state.state_manager import RedisStateManager

logger = logging.getLogger(__name__)


class RedisWordTimingCache:
    """
    Word timing cache manager using generic RedisStateManager.
    Provides dict-like interface for backward compatibility with hls_core.py
    """

    TIMING_TTL = 86400  # 24 hours (word timings rarely change)

    def __init__(self, container_id: Optional[str] = None):
        # Use generic manager with "word_timing" namespace
        self._manager = RedisStateManager("word_timing", container_id=container_id)
        self.container_id = self._manager.container_id
        logger.info(f"RedisWordTimingCache initialized: container={self.container_id}")

    @property
    def cache(self):
        """Dict-like interface for backward compatibility"""
        class TimingDict:
            def __init__(self, parent):
                self.parent = parent

            def __contains__(self, cache_key: str) -> bool:
                """Check if timing exists"""
                return self.parent._manager.get_session(cache_key) is not None

            def __getitem__(self, cache_key: str):
                """Get timing data"""
                data = self.parent._manager.get_session(cache_key)
                if data is None:
                    raise KeyError(f"Timing {cache_key} not found")
                return data

            def __setitem__(self, cache_key: str, value: Any):
                """Set timing data"""
                # Store as dict if not already
                if not isinstance(value, dict):
                    value = {"data": value}
                self.parent._manager.create_session(cache_key, value, ttl=self.parent.TIMING_TTL)

            def get(self, cache_key: str, default=None):
                """Get timing with default"""
                data = self.parent._manager.get_session(cache_key)
                if data and isinstance(data, dict) and "data" in data:
                    return data["data"]
                return data if data else default

            def pop(self, cache_key: str, default=None):
                """Remove and return timing"""
                data = self.parent._manager.get_session(cache_key)
                if data:
                    self.parent._manager.delete_session(cache_key)
                    if isinstance(data, dict) and "data" in data:
                        return data["data"]
                return data if data else default

            def clear(self):
                """Clear all timing entries"""
                logger.debug("Word timing cache clear() called - entries will expire via TTL")

        return TimingDict(self)


# Global instance
word_timing_cache = RedisWordTimingCache()
