"""
Redis-backed download state manager using generic RedisStateManager.

This module provides Redis-backed state management for both album and track downloads,
ensuring download progress is visible across all containers.

Usage:
    # Album downloads
    album_download_state = get_album_download_state()

    # Track downloads
    track_download_state = get_track_download_state()
"""

import logging
from typing import Dict, Optional, Any, List
from redis_state.state_manager import RedisStateManager

logger = logging.getLogger(__name__)


class RedisDownloadState:
    """
    Download state manager using generic RedisStateManager.

    Provides dict-like interface for compatibility with existing download managers
    while using Redis for cross-container state synchronization.
    """

    def __init__(self, namespace: str, container_id: Optional[str] = None):
        """
        Initialize download state manager.

        Args:
            namespace: Redis namespace ("download:album" or "download:track")
            container_id: Optional container identifier
        """
        self._manager = RedisStateManager(namespace, container_id=container_id)
        self.container_id = self._manager.container_id
        self.namespace = namespace

        logger.info(f"RedisDownloadState initialized: namespace={namespace}, container={self.container_id}")

    # ===== Dict-Like Interface for active_downloads =====

    @property
    def active_downloads(self):
        """
        Property that returns dict-like object for active downloads.
        Provides backward compatibility with existing code.
        """
        class DownloadDict(dict):
            """Dict subclass that writes back to Redis on update()"""
            def __init__(self, parent, download_id: str, data: Dict):
                super().__init__(data)
                self.parent = parent
                self.download_id = download_id

            def update(self, other: Dict):
                """Override update to write back to Redis"""
                super().update(other)
                # Write the updated dict back to Redis
                self.parent._manager.update_session(
                    self.download_id,
                    dict(self),
                    extend_ttl=True
                )

        class ActiveDownloadsDict:
            def __init__(self, parent):
                self.parent = parent

            def __contains__(self, download_id: str) -> bool:
                """Check if download exists"""
                return self.parent._manager.get_session(download_id) is not None

            def __getitem__(self, download_id: str):
                """Get download data - returns special dict that can write back"""
                data = self.parent._manager.get_session(download_id)
                if data is None:
                    raise KeyError(f"Download {download_id} not found")
                # Return DownloadDict that can write back on .update()
                return DownloadDict(self.parent, download_id, data)

            def __setitem__(self, download_id: str, value: Dict):
                """Set download data"""
                # Add container_id if not present
                if "container_id" not in value:
                    value["container_id"] = self.parent.container_id
                # Add download_id to data
                if "download_id" not in value:
                    value["download_id"] = download_id
                self.parent._manager.create_session(download_id, value, ttl=7200)  # 2 hours

            def get(self, download_id: str, default=None) -> Optional[Dict]:
                """Get download data with default"""
                data = self.parent._manager.get_session(download_id)
                return data if data else default

            def pop(self, download_id: str, default=None):
                """Remove and return download data"""
                data = self.parent._manager.get_session(download_id)
                if data:
                    self.parent._manager.delete_session(download_id)
                    return data
                return default

            def keys(self) -> List[str]:
                """Get all download IDs"""
                sessions = self.parent._manager.get_all_sessions()
                return [s.get("download_id") or s.get("session_id") for s in sessions if s]

            def items(self):
                """Get all download items"""
                sessions = self.parent._manager.get_all_sessions()
                return [(s.get("download_id") or s.get("session_id"), s) for s in sessions if s]

            def clear(self):
                """Clear all downloads"""
                sessions = self.parent._manager.get_all_sessions()
                for session in sessions:
                    download_id = session.get("download_id") or session.get("session_id")
                    if download_id:
                        self.parent._manager.delete_session(download_id)

            def update_download(self, download_id: str, updates: Dict):
                """Update specific fields in download"""
                return self.parent._manager.update_session(download_id, updates, extend_ttl=True)

        return ActiveDownloadsDict(self)

    @property
    def completed_downloads(self):
        """
        Property that returns dict-like object for completed downloads.
        Uses Redis status tracking with "completed" status.
        """
        class CompletedDownloadsDict:
            def __init__(self, parent):
                self.parent = parent

            def __contains__(self, download_id: str) -> bool:
                """Check if completed download exists"""
                status = self.parent._manager.get_status(download_id)
                return status is not None and status.get("status") == "completed"

            def __getitem__(self, download_id: str) -> Dict:
                """Get completed download data"""
                status = self.parent._manager.get_status(download_id)
                if status is None or status.get("status") != "completed":
                    raise KeyError(f"Completed download {download_id} not found")
                return status

            def __setitem__(self, download_id: str, value: Dict):
                """Set completed download data"""
                self.parent._manager.set_status(download_id, "completed", metadata=value)

            def get(self, download_id: str, default=None) -> Optional[Dict]:
                """Get completed download with default"""
                status = self.parent._manager.get_status(download_id)
                if status and status.get("status") == "completed":
                    return status
                return default

            def pop(self, download_id: str, default=None):
                """Remove and return completed download"""
                status = self.parent._manager.get_status(download_id)
                if status and status.get("status") == "completed":
                    self.parent._manager.delete_status(download_id)
                    return status
                return default

            def keys(self) -> List[str]:
                """Get all completed download IDs"""
                # Note: This requires scanning Redis keys
                from redis_state.config import redis_client
                pattern = f"{self.parent.namespace}:status:*"
                keys = []
                for key in redis_client.scan_iter(match=pattern):
                    status = self.parent._manager.redis.get(key)
                    if status:
                        import json
                        data = json.loads(status)
                        if data.get("status") == "completed":
                            # Extract download_id from key
                            download_id = key.decode() if isinstance(key, bytes) else key
                            download_id = download_id.split(":")[-1]
                            keys.append(download_id)
                return keys

            def items(self):
                """Get all completed download items as (id, data) tuples"""
                for download_id in self.keys():
                    status = self.parent._manager.get_status(download_id)
                    if status and status.get("status") == "completed":
                        yield (download_id, status)

            def clear(self):
                """Clear all completed downloads"""
                for download_id in self.keys():
                    self.parent._manager.delete_status(download_id)

        return CompletedDownloadsDict(self)

    # ===== Helper Methods =====

    def set_download_status(self, download_id: str, status_data: Dict):
        """
        Update download status (for progress updates).

        Args:
            download_id: Download identifier
            status_data: Status data to merge with existing session
        """
        return self._manager.update_session(download_id, status_data, extend_ttl=True)

    def get_download_status(self, download_id: str) -> Optional[Dict]:
        """
        Get download status.

        Args:
            download_id: Download identifier

        Returns:
            Download status or None if not found
        """
        return self._manager.get_session(download_id)

    def cleanup_old_downloads(self, max_age_seconds: int = 7200) -> int:
        """
        Clean up old download sessions.

        Args:
            max_age_seconds: Maximum age before cleanup (default 2 hours)

        Returns:
            Number of downloads cleaned up
        """
        return self._manager.cleanup_old_keys(max_age_seconds=max_age_seconds)

    def get_all_active_downloads(self) -> List[Dict]:
        """
        Get all active download sessions.

        Returns:
            List of download data dicts
        """
        return self._manager.get_all_sessions()

    def get_stats(self) -> Dict[str, Any]:
        """
        Get download state statistics.

        Returns:
            Dict with statistics
        """
        stats = self._manager.get_stats()

        # Add download-specific stats
        active_count = 0
        completed_count = 0

        try:
            sessions = self.get_all_active_downloads()
            active_count = len(sessions)

            # Count completed
            for download_id in self.completed_downloads.keys():
                completed_count += 1
        except Exception as e:
            logger.warning(f"Error getting download stats: {e}")

        stats.update({
            "active_downloads": active_count,
            "completed_downloads": completed_count
        })

        return stats


# ===== Global Singleton Instances =====

_album_download_state = None
_track_download_state = None


def get_album_download_state(container_id: Optional[str] = None) -> RedisDownloadState:
    """
    Get global album download state instance.

    Args:
        container_id: Optional container identifier

    Returns:
        RedisDownloadState instance for album downloads
    """
    global _album_download_state

    if _album_download_state is None:
        _album_download_state = RedisDownloadState("download:album", container_id=container_id)

    return _album_download_state


def get_track_download_state(container_id: Optional[str] = None) -> RedisDownloadState:
    """
    Get global track download state instance.

    Args:
        container_id: Optional container identifier

    Returns:
        RedisDownloadState instance for track downloads
    """
    global _track_download_state

    if _track_download_state is None:
        _track_download_state = RedisDownloadState("download:track", container_id=container_id)

    return _track_download_state


# Global instances for direct imports
album_download_state = RedisDownloadState("download:album")
track_download_state = RedisDownloadState("download:track")
download_state = album_download_state  # Alias for backward compatibility

# Backward compatibility exports
__all__ = [
    "RedisDownloadState",
    "get_album_download_state",
    "get_track_download_state",
    "album_download_state",
    "track_download_state",
    "download_state",
]
