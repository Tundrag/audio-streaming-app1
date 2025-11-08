"""
Redis-backed upload state manager V2 - MIGRATED TO USE GENERIC RedisStateManager

This is a compatibility wrapper that uses the generic RedisStateManager underneath
while maintaining the exact same API as redis_upload_state.py for backward compatibility.

Migration: redis_upload_state.py (specialized) → RedisStateManager("upload") (generic)
"""

import logging
from typing import Dict, Optional, Any, List
from redis_state.state_manager import RedisStateManager

logger = logging.getLogger(__name__)


class RedisUploadState:
    """
    Upload state manager - now using generic RedisStateManager.

    This class provides the exact same interface as the old redis_upload_state.py
    but uses the enterprise-grade generic RedisStateManager underneath for consistency.

    All existing code continues to work without changes.
    """

    # Redis key prefixes (for reference, actual keys managed by RedisStateManager)
    UPLOAD_SESSION_PREFIX = "upload:session:"
    UPLOAD_CHUNKS_PREFIX = "upload:set:chunks:"  # Now using sets
    UPLOAD_LOCK_PREFIX = "upload:lock:"
    UPLOAD_STATUS_PREFIX = "upload:status:"

    # TTL values (delegated to RedisStateManager config)
    SESSION_TTL = 7200  # 2 hours
    COMPLETED_TTL = 300  # 5 minutes
    FAILED_TTL = 600  # 10 minutes
    LOCK_TTL = 60  # 1 minute

    def __init__(self, container_id: Optional[str] = None):
        """
        Initialize upload state manager using generic RedisStateManager.

        Args:
            container_id: Optional container identifier
        """
        # Use generic manager with "upload" namespace
        self._manager = RedisStateManager("upload", container_id=container_id)
        self.container_id = self._manager.container_id

        logger.info(f"RedisUploadState V2 initialized using RedisStateManager: container={self.container_id}")

    # ===== Session Management =====

    def create_session(self, upload_data: Dict[str, Any]) -> bool:
        """
        Create a new upload session.

        Args:
            upload_data: Upload session data (must include 'upload_id')

        Returns:
            True if successful, False otherwise
        """
        upload_id = upload_data.get("upload_id")
        if not upload_id:
            logger.error("Cannot create session without upload_id")
            return False

        return self._manager.create_session(upload_id, upload_data, ttl=self.SESSION_TTL)

    def get_session(self, upload_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve upload session data.

        Args:
            upload_id: Upload identifier

        Returns:
            Session data or None if not found
        """
        return self._manager.get_session(upload_id)

    def update_session(self, upload_id: str, updates: Dict[str, Any]) -> bool:
        """
        Update upload session with new data.

        Args:
            upload_id: Upload identifier
            updates: Fields to update

        Returns:
            True if successful, False otherwise
        """
        return self._manager.update_session(upload_id, updates, extend_ttl=True)

    def delete_session(self, upload_id: str) -> bool:
        """
        Delete an upload session.

        Args:
            upload_id: Upload identifier

        Returns:
            True if deleted, False otherwise
        """
        # Also delete associated chunks
        self._manager.redis.delete(self._manager._set_key(f"chunks:{upload_id}"))
        return self._manager.delete_session(upload_id)

    # ===== Chunk Tracking =====

    def register_chunk(self, upload_id: str, chunk_index: int) -> bool:
        """
        Register that a chunk has been received.

        Args:
            upload_id: Upload identifier
            chunk_index: Chunk index

        Returns:
            True if registered, False otherwise
        """
        # Use set operations for chunk tracking
        added = self._manager.add_to_set(f"chunks:{upload_id}", str(chunk_index))
        return added > 0

    def get_received_chunks_count(self, upload_id: str) -> int:
        """
        Get count of received chunks.

        Args:
            upload_id: Upload identifier

        Returns:
            Number of chunks received
        """
        return self._manager.get_set_count(f"chunks:{upload_id}")

    def get_received_chunks(self, upload_id: str) -> set:
        """
        Get set of received chunk indices.

        Args:
            upload_id: Upload identifier

        Returns:
            Set of chunk indices (as integers)
        """
        chunks = self._manager.get_set_members(f"chunks:{upload_id}")
        # Convert back to integers
        return {int(c) for c in chunks}

    def is_chunk_received(self, upload_id: str, chunk_index: int) -> bool:
        """
        Check if a specific chunk has been received.

        Args:
            upload_id: Upload identifier
            chunk_index: Chunk index to check

        Returns:
            True if chunk received, False otherwise
        """
        return self._manager.is_in_set(f"chunks:{upload_id}", str(chunk_index))

    # ===== Lock Management =====

    def acquire_lock(self, upload_id: str, timeout: Optional[int] = None) -> bool:
        """
        Acquire exclusive lock on upload (atomic operation).

        Args:
            upload_id: Upload identifier
            timeout: Lock TTL in seconds (uses LOCK_TTL if not provided)

        Returns:
            True if lock acquired, False if already locked
        """
        return self._manager.acquire_lock(upload_id, timeout=timeout or self.LOCK_TTL)

    def release_lock(self, upload_id: str) -> bool:
        """
        Release lock on upload.

        Args:
            upload_id: Upload identifier

        Returns:
            True if released, False otherwise
        """
        return self._manager.release_lock(upload_id)

    def extend_lock(self, upload_id: str, additional_time: Optional[int] = None) -> bool:
        """
        Extend lock TTL.

        Args:
            upload_id: Upload identifier
            additional_time: Seconds to add to TTL

        Returns:
            True if extended, False otherwise
        """
        return self._manager.extend_lock(upload_id, additional_time=additional_time or self.LOCK_TTL)

    # ===== Upload Status Tracking (for mega_upload_manager) =====

    def set_upload_status(
        self,
        file_id: str,
        status: str,
        **metadata
    ) -> bool:
        """
        Set upload status (for mega_upload_manager compatibility).

        Args:
            file_id: File/track identifier
            status: Status value (queued, uploading, completed, failed)
            **metadata: Additional metadata

        Returns:
            True if successful, False otherwise
        """
        return self._manager.set_status(file_id, status, metadata=metadata)

    def get_upload_status(self, file_id: str) -> Optional[Dict[str, Any]]:
        """
        Get upload status.

        Args:
            file_id: File/track identifier

        Returns:
            Status data or None if not found
        """
        return self._manager.get_status(file_id)

    def update_upload_status(self, file_id: str, updates: Dict[str, Any]) -> bool:
        """
        Update specific fields in upload status.

        Args:
            file_id: File/track identifier
            updates: Fields to update

        Returns:
            True if successful, False otherwise
        """
        status = self.get_upload_status(file_id)
        if not status:
            # If status doesn't exist, create it
            return self.set_upload_status(file_id, "queued", **updates)

        # Update status - use new status from updates if provided
        new_status = updates.get("status", status.get("status", "queued"))
        metadata = {**status, **updates}
        # Remove status from metadata to avoid duplication
        metadata.pop("status", None)

        return self._manager.set_status(file_id, new_status, metadata=metadata)

    def delete_upload_status(self, file_id: str) -> bool:
        """
        Delete upload status.

        Args:
            file_id: File/track identifier

        Returns:
            True if deleted, False otherwise
        """
        return self._manager.delete_status(file_id)

    # ===== Cleanup & Utilities =====

    def cleanup_old_sessions(self, max_age_seconds: int = 7200) -> int:
        """
        Clean up old upload sessions.

        Args:
            max_age_seconds: Maximum age before cleanup

        Returns:
            Number of sessions cleaned up
        """
        return self._manager.cleanup_old_keys(max_age_seconds=max_age_seconds)

    def get_all_active_uploads(self) -> List[Dict[str, Any]]:
        """
        Get all active upload sessions.

        Returns:
            List of session data dicts
        """
        return self._manager.get_all_sessions()

    def get_all_sessions(self) -> List[Dict[str, Any]]:
        """
        Alias for get_all_active_uploads() for compatibility.

        Returns:
            List of session data dicts
        """
        return self._manager.get_all_sessions()

    def get_stats(self) -> Dict[str, Any]:
        """
        Get upload state statistics.

        Returns:
            Dict with statistics
        """
        return self._manager.get_stats()

    # ===== Helper Methods (for compatibility) =====

    def _session_key(self, upload_id: str) -> str:
        """Generate Redis key for session (for reference/debugging)"""
        return self._manager._session_key(upload_id)

    def _chunks_key(self, upload_id: str) -> str:
        """Generate Redis key for chunks (for reference/debugging)"""
        return self._manager._set_key(f"chunks:{upload_id}")

    def _lock_key(self, upload_id: str) -> str:
        """Generate Redis key for lock (for reference/debugging)"""
        return self._manager._lock_key(upload_id)

    def _status_key(self, file_id: str) -> str:
        """Generate Redis key for status (for reference/debugging)"""
        return self._manager._status_key(file_id)

    def _get_ttl_for_status(self, status: str) -> int:
        """Get appropriate TTL based on status"""
        return self._manager._get_ttl_for_status(status)


# Global singleton instance (same interface as old version)
_redis_upload_state = None


def get_redis_upload_state(container_id: Optional[str] = None) -> RedisUploadState:
    """
    Get global RedisUploadState instance.

    Args:
        container_id: Optional container identifier

    Returns:
        RedisUploadState instance
    """
    global _redis_upload_state

    if _redis_upload_state is None:
        _redis_upload_state = RedisUploadState(container_id=container_id)

    return _redis_upload_state


# Global instance for direct imports
upload_state = RedisUploadState()

# Backward compatibility exports
__all__ = ["RedisUploadState", "get_redis_upload_state", "upload_state"]
