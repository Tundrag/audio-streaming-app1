"""
Redis-backed HLS segment progress state using generic RedisStateManager.
Ensures progress visibility across all containers for multi-container deployments.
"""
import logging
from typing import Dict, Optional, Any
from redis_state.state_manager import RedisStateManager

logger = logging.getLogger(__name__)


class RedisProgressState:
    """
    HLS segment progress manager using generic RedisStateManager.
    Provides dict-like interface for backward compatibility.
    """

    PROGRESS_TTL = 7200  # 2 hours (same as upload sessions)

    def __init__(self, container_id: Optional[str] = None):
        # Use generic manager with "progress" namespace
        self._manager = RedisStateManager("progress", container_id=container_id)
        self.container_id = self._manager.container_id
        # In-memory fallback when Redis is unavailable
        self._memory_fallback: Dict[str, Dict[str, Any]] = {}
        logger.info(f"RedisProgressState initialized: container={self.container_id}")

    @property
    def segment_progress(self):
        """Dict-like interface for backward compatibility with hls_core.py"""
        class RedisDict(dict):
            """Dict subclass that writes back to Redis on update()"""
            def __init__(self, parent, progress_key: str, data: dict):
                super().__init__(data)
                self.parent = parent
                self.progress_key = progress_key

            def update(self, other: dict):
                """Override update to write back to Redis"""
                super().update(other)
                # Write the updated dict back to Redis
                success = self.parent._manager.create_session(
                    self.progress_key,
                    dict(self),
                    ttl=self.parent.PROGRESS_TTL
                )
                if not success:
                    # Use memory fallback when Redis fails
                    self.parent._memory_fallback[self.progress_key] = dict(self)

        class ProgressDict:
            def __init__(self, parent):
                self.parent = parent

            def __contains__(self, progress_key: str) -> bool:
                """Check if progress exists"""
                data = self.parent._manager.get_session(progress_key)
                if data is not None:
                    return True
                return progress_key in self.parent._memory_fallback

            def __getitem__(self, progress_key: str):
                """Get progress data - returns special dict that can write back"""
                data = self.parent._manager.get_session(progress_key)
                if data is None:
                    # Try memory fallback
                    data = self.parent._memory_fallback.get(progress_key)
                    if data is None:
                        raise KeyError(f"Progress {progress_key} not found")
                # Return RedisDict that can write back on .update()
                return RedisDict(self.parent, progress_key, data)

            def __setitem__(self, progress_key: str, value: Dict):
                """Set progress data"""
                success = self.parent._manager.create_session(progress_key, value, ttl=self.parent.PROGRESS_TTL)
                if not success:
                    # Use memory fallback when Redis fails
                    self.parent._memory_fallback[progress_key] = value

            def get(self, progress_key: str, default=None):
                """Get progress with default"""
                data = self.parent._manager.get_session(progress_key)
                if data is not None:
                    return data
                return self.parent._memory_fallback.get(progress_key, default)

            def pop(self, progress_key: str, default=None):
                """Remove and return progress"""
                data = self.parent._manager.get_session(progress_key)
                if data:
                    self.parent._manager.delete_session(progress_key)
                    return data
                fallback = self.parent._memory_fallback.pop(progress_key, None)
                if fallback is not None:
                    return fallback
                return default

            def setdefault(self, progress_key: str, default=None):
                """Get progress, setting default if not exists"""
                existing = self.parent._manager.get_session(progress_key)
                if existing is not None:
                    return RedisDict(self.parent, progress_key, existing)
                if default is not None:
                    value = default.copy() if isinstance(default, dict) else dict(default or {})
                    success = self.parent._manager.create_session(progress_key, value, ttl=self.parent.PROGRESS_TTL)
                    if not success:
                        self.parent._memory_fallback[progress_key] = value
                    return RedisDict(self.parent, progress_key, value)
                return default

            def update(self, progress_key: str, updates: dict):
                """Update progress data (merge with existing)"""
                existing = self.parent._manager.get_session(progress_key)
                if existing is None:
                    existing = self.parent._memory_fallback.get(progress_key, {})
                existing.update(updates)
                success = self.parent._manager.create_session(progress_key, existing, ttl=self.parent.PROGRESS_TTL)
                if not success:
                    self.parent._memory_fallback[progress_key] = existing

            def keys(self):
                """Get all progress keys"""
                sessions = self.parent._manager.get_all_sessions()
                # Extract progress keys from session data
                redis_keys = [s.get('session_id') for s in sessions if s and s.get('session_id')]
                # Add memory fallback keys
                all_keys = set(redis_keys) | set(self.parent._memory_fallback.keys())
                return list(all_keys)

            def values(self):
                """Get all progress values"""
                sessions = self.parent._manager.get_all_sessions()
                values = [
                    RedisDict(self.parent, s.get('session_id'), s)
                    for s in sessions
                    if s and s.get('session_id')
                ]
                for key, value in self.parent._memory_fallback.items():
                    if key not in {v.progress_key for v in values}:
                        values.append(RedisDict(self.parent, key, value))
                return values

            def items(self):
                """Get all progress items as (key, value) tuples"""
                sessions = self.parent._manager.get_all_sessions()
                items = [
                    (s.get('session_id'), RedisDict(self.parent, s.get('session_id'), s))
                    for s in sessions
                    if s and s.get('session_id')
                ]
                known_keys = {key for key, _ in items}
                for key, value in self.parent._memory_fallback.items():
                    if key not in known_keys:
                        items.append((key, RedisDict(self.parent, key, value)))
                return items

            def clear(self):
                """Clear all progress entries"""
                # Note: RedisStateManager doesn't have clear_all, so this is a no-op
                # Individual entries will expire via TTL
                logger.debug("Progress clear() called - entries will expire via TTL")

        return ProgressDict(self)


# Global instance
progress_state = RedisProgressState()
