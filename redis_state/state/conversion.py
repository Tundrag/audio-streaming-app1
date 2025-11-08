"""
Redis-backed conversion tracking using generic RedisStateManager.
Prevents duplicate conversions across containers and provides distributed locks.
"""
import logging
import asyncio
from typing import Dict, Optional, Any
from redis_state.state_manager import RedisStateManager

logger = logging.getLogger(__name__)


class RedisConversionState:
    """
    Conversion state manager using generic RedisStateManager.
    Tracks active conversions and provides distributed locking.
    """

    CONVERSION_TTL = 3600  # 1 hour (conversions should complete quickly)
    LOCK_TTL = 300  # 5 minutes (lock timeout)

    def __init__(self, container_id: Optional[str] = None):
        self._manager = RedisStateManager("conversion", container_id=container_id)
        self.container_id = self._manager.container_id

        # Local asyncio locks for in-container concurrency
        # These are OK to stay local - they protect same-container access
        self._local_locks: Dict[str, asyncio.Lock] = {}
        self._local_lock_creation = asyncio.Lock()

        logger.info(f"RedisConversionState initialized: container={self.container_id}")

    async def _get_local_lock(self, key: str) -> asyncio.Lock:
        """Get or create local asyncio lock for a key"""
        async with self._local_lock_creation:
            if key not in self._local_locks:
                self._local_locks[key] = asyncio.Lock()
            return self._local_locks[key]

    @property
    def active_conversions(self):
        """Dict-like interface for active conversions"""
        class ConversionDict:
            def __init__(self, parent):
                self.parent = parent

            def __contains__(self, conversion_id: str) -> bool:
                return self.parent._manager.get_session(f"active:{conversion_id}") is not None

            def __getitem__(self, conversion_id: str):
                data = self.parent._manager.get_session(f"active:{conversion_id}")
                if data is None:
                    raise KeyError(f"Conversion {conversion_id} not found")
                return data

            def __setitem__(self, conversion_id: str, value: Dict):
                self.parent._manager.create_session(
                    f"active:{conversion_id}",
                    value,
                    ttl=self.parent.CONVERSION_TTL
                )

            def get(self, conversion_id: str, default=None):
                return self.parent._manager.get_session(f"active:{conversion_id}") or default

            def pop(self, conversion_id: str, default=None):
                data = self.parent._manager.get_session(f"active:{conversion_id}")
                if data:
                    self.parent._manager.delete_session(f"active:{conversion_id}")
                return data if data else default

        return ConversionDict(self)

    @property
    def segment_locks(self):
        """Dict-like interface for segment locks"""
        class SegmentLockDict:
            def __init__(self, parent):
                self.parent = parent

            def __contains__(self, lock_id: str) -> bool:
                return self.parent._manager.acquire_lock(f"seg_lock:{lock_id}", timeout=0)

            def __getitem__(self, lock_id: str):
                # Return local asyncio lock
                return asyncio.create_task(self.parent._get_local_lock(lock_id))

            def get(self, lock_id: str, default=None):
                # Check if distributed lock exists
                has_lock = self.parent._manager.get_session(f"seg_lock:{lock_id}")
                return has_lock if has_lock else default

        return SegmentLockDict(self)

    @property
    def conversion_locks(self):
        """Dict-like interface for conversion locks"""
        class ConversionLockDict:
            def __init__(self, parent):
                self.parent = parent

            def __contains__(self, lock_id: str) -> bool:
                return self.parent._manager.get_session(f"conv_lock:{lock_id}") is not None

            async def __getitem__(self, lock_id: str):
                # Return local asyncio lock
                return await self.parent._get_local_lock(f"conv:{lock_id}")

            def get(self, lock_id: str, default=None):
                has_lock = self.parent._manager.get_session(f"conv_lock:{lock_id}")
                return has_lock if has_lock else default

        return ConversionLockDict(self)

    @property
    def track_regeneration_locks(self):
        """Dict-like interface for track regeneration locks"""
        class RegenerationLockDict:
            def __init__(self, parent):
                self.parent = parent

            def __contains__(self, lock_id: str) -> bool:
                return self.parent._manager.get_session(f"regen_lock:{lock_id}") is not None

            async def __getitem__(self, lock_id: str):
                # Return local asyncio lock
                return await self.parent._get_local_lock(f"regen:{lock_id}")

            def get(self, lock_id: str, default=None):
                has_lock = self.parent._manager.get_session(f"regen_lock:{lock_id}")
                return has_lock if has_lock else default

        return RegenerationLockDict(self)


# Global instance
conversion_state = RedisConversionState()
