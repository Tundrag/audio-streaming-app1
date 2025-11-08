"""
Redis-backed upload stats using generic RedisStateManager.
Provides upload progress visibility across all containers.
"""
import logging
from typing import Dict, Optional, Any
from dataclasses import dataclass, asdict
from redis_state.state_manager import RedisStateManager

logger = logging.getLogger(__name__)


@dataclass
class WriteStats:
    """Write stats structure - same as upload_queue.py"""
    file_id: str
    status: str
    path: str
    queued_at: float
    bytes_written: int = 0
    chunks_written: int = 0
    total_size: Optional[int] = None
    duration: Optional[float] = None
    speed: Optional[float] = None
    error: Optional[str] = None
    worker_id: Optional[str] = None


class RedisUploadStats:
    """
    Upload stats manager using generic RedisStateManager.
    Provides dict-like interface for backward compatibility with upload_queue.py
    """

    STATS_TTL = 7200  # 2 hours (uploads should complete within this time)

    def __init__(self, container_id: Optional[str] = None):
        # Use generic manager with "upload_stats" namespace
        self._manager = RedisStateManager("upload_stats", container_id=container_id)
        self.container_id = self._manager.container_id
        logger.info(f"RedisUploadStats initialized: container={self.container_id}")

    @property
    def file_stats(self):
        """Dict-like interface for backward compatibility"""
        class FileStatsDict:
            def __init__(self, parent):
                self.parent = parent

            def __contains__(self, file_id: str) -> bool:
                """Check if file stats exist"""
                return self.parent._manager.get_session(file_id) is not None

            def __getitem__(self, file_id: str) -> WriteStats:
                """Get file stats"""
                data = self.parent._manager.get_session(file_id)
                if data is None:
                    raise KeyError(f"File stats {file_id} not found")

                # Filter out fields added by RedisStateManager
                redis_fields = {'container_id', 'created_at', 'updated_at', 'ttl'}
                data = {k: v for k, v in data.items() if k not in redis_fields}

                # Convert dict back to WriteStats dataclass
                return WriteStats(**data)

            def __setitem__(self, file_id: str, value: WriteStats):
                """Set file stats"""
                # Convert WriteStats dataclass to dict for Redis storage
                stats_data = asdict(value)

                self.parent._manager.create_session(file_id, stats_data, ttl=self.parent.STATS_TTL)

            def get(self, file_id: str, default=None) -> Optional[WriteStats]:
                """Get file stats with default"""
                try:
                    return self[file_id]
                except KeyError:
                    return default

            def pop(self, file_id: str, default=None) -> Optional[WriteStats]:
                """Remove and return file stats"""
                try:
                    stats = self[file_id]
                    self.parent._manager.delete_session(file_id)
                    return stats
                except KeyError:
                    return default

            def update(self, file_id: str, updates: Dict[str, Any]):
                """Update file stats (partial update)"""
                data = self.parent._manager.get_session(file_id)
                if data:
                    data.update(updates)
                    self.parent._manager.update_session(file_id, data, extend_ttl=True)
                else:
                    logger.warning(f"Attempted to update non-existent file stats: {file_id}")

            def clear(self):
                """Clear all file stats"""
                logger.debug("Upload stats clear() called - entries will expire via TTL")

            def values(self):
                """Get all file stats values"""
                sessions = self.parent._manager.get_all_sessions()
                result = []
                for session in sessions:
                    if session:
                        try:
                            # Filter out Redis metadata fields
                            redis_fields = {'container_id', 'created_at', 'updated_at', 'ttl'}
                            data = {k: v for k, v in session.items() if k not in redis_fields}
                            if data:  # Only add if there's actual data
                                result.append(WriteStats(**data))
                        except Exception as e:
                            logger.warning(f"Error converting session to WriteStats: {e}")
                            continue
                return result

            def keys(self):
                """Get all file IDs"""
                sessions = self.parent._manager.get_all_sessions()
                return [s.get('file_id') for s in sessions if s and 'file_id' in s]

            def items(self):
                """Get all (file_id, WriteStats) pairs"""
                sessions = self.parent._manager.get_all_sessions()
                result = []
                for session in sessions:
                    if session and 'file_id' in session:
                        try:
                            # Filter out Redis metadata fields
                            redis_fields = {'container_id', 'created_at', 'updated_at', 'ttl'}
                            data = {k: v for k, v in session.items() if k not in redis_fields}
                            result.append((session['file_id'], WriteStats(**data)))
                        except Exception as e:
                            logger.warning(f"Error converting session to WriteStats: {e}")
                            continue
                return result

        return FileStatsDict(self)


# Global instance
upload_stats = RedisUploadStats()
