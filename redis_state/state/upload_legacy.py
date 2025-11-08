"""
Redis-backed upload state manager for multi-container deployments.

This module provides centralized upload state management across multiple
application containers using Redis as the shared state store.
"""

import json
import time
import logging
from typing import Dict, Optional, Any, List
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from redis_state.config import redis_client

logger = logging.getLogger(__name__)


@dataclass
class UploadSessionState:
    """State for a chunked upload session"""
    upload_id: str
    track_id: str
    album_id: str
    filename: str
    title: str
    creator_id: str
    file_path: str
    file_size: int
    track_order: int
    chunks_dir: str
    total_chunks: int
    received_chunks: int
    status: str  # initialized, chunks_complete, processing, completed, cancelled, failed
    created_at: float
    last_updated: float
    track_created: bool
    error: Optional[str] = None
    container_id: Optional[str] = None  # Which container is handling this


class RedisUploadState:
    """
    Centralized upload state manager using Redis.

    Replaces in-memory `active_uploads` dict in chunked_upload.py with
    Redis-backed storage that all containers can access.
    """

    # Redis key prefixes
    UPLOAD_SESSION_PREFIX = "upload:session:"
    UPLOAD_CHUNKS_PREFIX = "upload:chunks:"
    UPLOAD_LOCK_PREFIX = "upload:lock:"
    UPLOAD_STATUS_PREFIX = "upload:status:"

    # TTL values (seconds)
    SESSION_TTL = 7200  # 2 hours for active uploads
    COMPLETED_TTL = 300  # 5 minutes for completed uploads
    FAILED_TTL = 600  # 10 minutes for failed uploads
    LOCK_TTL = 60  # 1 minute for upload locks

    def __init__(self, container_id: Optional[str] = None):
        self.redis = redis_client
        self.container_id = container_id or f"container_{time.time()}"
        logger.info(f"RedisUploadState initialized for container: {self.container_id}")

    # ===== Session Management =====

    def create_session(self, upload_data: Dict[str, Any]) -> bool:
        """Create a new upload session"""
        try:
            upload_id = upload_data.get("upload_id")
            if not upload_id:
                logger.error("Cannot create session without upload_id")
                return False

            # Add container ID and timestamps
            upload_data["container_id"] = self.container_id
            upload_data["created_at"] = time.time()
            upload_data["last_updated"] = time.time()

            # Store in Redis as JSON
            key = self._session_key(upload_id)
            value = json.dumps(upload_data)

            success = self.redis.set(key, value, ex=self.SESSION_TTL)
            if success:
                logger.info(f"Created upload session: {upload_id}")
            else:
                logger.error(f"Failed to create upload session: {upload_id}")

            return success
        except Exception as e:
            logger.error(f"Error creating upload session: {e}")
            return False

    def get_session(self, upload_id: str) -> Optional[Dict[str, Any]]:
        """Get upload session data"""
        try:
            key = self._session_key(upload_id)
            data = self.redis.get(key)

            if data:
                return json.loads(data)
            return None
        except Exception as e:
            logger.error(f"Error getting upload session {upload_id}: {e}")
            return None

    def update_session(self, upload_id: str, updates: Dict[str, Any]) -> bool:
        """Update upload session with new data"""
        try:
            # Get current session
            session = self.get_session(upload_id)
            if not session:
                logger.warning(f"Cannot update non-existent session: {upload_id}")
                return False

            # Merge updates
            session.update(updates)
            session["last_updated"] = time.time()

            # Save back to Redis
            key = self._session_key(upload_id)
            value = json.dumps(session)

            # Adjust TTL based on status
            ttl = self._get_ttl_for_status(session.get("status", "initialized"))

            success = self.redis.set(key, value, ex=ttl)
            if success:
                logger.debug(f"Updated upload session: {upload_id}")
            else:
                logger.error(f"Failed to update upload session: {upload_id}")

            return success
        except Exception as e:
            logger.error(f"Error updating upload session {upload_id}: {e}")
            return False

    def delete_session(self, upload_id: str) -> bool:
        """Delete an upload session"""
        try:
            key = self._session_key(upload_id)
            deleted = self.redis.delete(key)

            # Also delete chunk tracking
            chunk_key = self._chunks_key(upload_id)
            self.redis.delete(chunk_key)

            if deleted:
                logger.info(f"Deleted upload session: {upload_id}")

            return deleted > 0
        except Exception as e:
            logger.error(f"Error deleting upload session {upload_id}: {e}")
            return False

    # ===== Chunk Tracking =====

    def register_chunk(self, upload_id: str, chunk_index: int) -> bool:
        """Register that a chunk has been received"""
        try:
            key = self._chunks_key(upload_id)
            # Use Redis set to track received chunks
            added = self.redis.sadd(key, str(chunk_index))
            self.redis.expire(key, self.SESSION_TTL)

            if added:
                logger.debug(f"Registered chunk {chunk_index} for upload {upload_id}")

            return added > 0
        except Exception as e:
            logger.error(f"Error registering chunk {chunk_index} for {upload_id}: {e}")
            return False

    def get_received_chunks_count(self, upload_id: str) -> int:
        """Get count of received chunks"""
        try:
            key = self._chunks_key(upload_id)
            count = self.redis.scard(key)
            return count
        except Exception as e:
            logger.error(f"Error getting chunk count for {upload_id}: {e}")
            return 0

    def get_received_chunks(self, upload_id: str) -> set:
        """Get set of received chunk indices"""
        try:
            key = self._chunks_key(upload_id)
            chunks = self.redis.smembers(key)
            return {int(c) for c in chunks}
        except Exception as e:
            logger.error(f"Error getting chunks for {upload_id}: {e}")
            return set()

    def is_chunk_received(self, upload_id: str, chunk_index: int) -> bool:
        """Check if a specific chunk has been received"""
        try:
            key = self._chunks_key(upload_id)
            return self.redis.sismember(key, str(chunk_index))
        except Exception as e:
            logger.error(f"Error checking chunk {chunk_index} for {upload_id}: {e}")
            return False

    # ===== Upload Locks (for atomic operations) =====

    def acquire_lock(self, upload_id: str, timeout: int = None) -> bool:
        """
        Acquire a lock for upload operations.
        Uses Redis SET NX (set if not exists) for atomic locking.
        """
        try:
            key = self._lock_key(upload_id)
            ttl = timeout or self.LOCK_TTL

            # SET with NX (only if not exists) and EX (expiration)
            locked = self.redis.set(
                key,
                self.container_id,
                ex=ttl,
                nx=True  # Only set if key doesn't exist
            )

            if locked:
                logger.debug(f"Acquired lock for upload {upload_id}")
            else:
                logger.debug(f"Failed to acquire lock for upload {upload_id}")

            return locked
        except Exception as e:
            logger.error(f"Error acquiring lock for {upload_id}: {e}")
            return False

    def release_lock(self, upload_id: str) -> bool:
        """Release upload lock"""
        try:
            key = self._lock_key(upload_id)
            deleted = self.redis.delete(key)

            if deleted:
                logger.debug(f"Released lock for upload {upload_id}")

            return deleted > 0
        except Exception as e:
            logger.error(f"Error releasing lock for {upload_id}: {e}")
            return False

    def extend_lock(self, upload_id: str, additional_time: int = None) -> bool:
        """Extend lock TTL (useful for long operations)"""
        try:
            key = self._lock_key(upload_id)
            ttl = additional_time or self.LOCK_TTL

            return self.redis.expire(key, ttl)
        except Exception as e:
            logger.error(f"Error extending lock for {upload_id}: {e}")
            return False

    # ===== Status Tracking (for mega_upload_manager) =====

    def set_upload_status(
        self,
        file_id: str,
        status: str,
        **metadata
    ) -> bool:
        """
        Set upload status (for mega_upload_manager compatibility).

        Args:
            file_id: Unique file/track ID
            status: queued, uploading, completed, failed
            **metadata: Additional metadata (filename, error, worker_id, etc.)
        """
        try:
            key = self._status_key(file_id)

            status_data = {
                "file_id": file_id,
                "status": status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "container_id": self.container_id,
                **metadata
            }

            value = json.dumps(status_data)

            # Adjust TTL based on status
            ttl = self._get_ttl_for_status(status)

            success = self.redis.set(key, value, ex=ttl)
            if success:
                logger.debug(f"Set upload status for {file_id}: {status}")

            return success
        except Exception as e:
            logger.error(f"Error setting upload status for {file_id}: {e}")
            return False

    def get_upload_status(self, file_id: str) -> Optional[Dict[str, Any]]:
        """Get upload status"""
        try:
            key = self._status_key(file_id)
            data = self.redis.get(key)

            if data:
                return json.loads(data)
            return None
        except Exception as e:
            logger.error(f"Error getting upload status for {file_id}: {e}")
            return None

    def update_upload_status(self, file_id: str, updates: Dict[str, Any]) -> bool:
        """Update specific fields in upload status"""
        try:
            status = self.get_upload_status(file_id)
            if not status:
                # If status doesn't exist, create it
                return self.set_upload_status(file_id, "queued", **updates)

            # Update fields
            status.update(updates)
            status["timestamp"] = datetime.now(timezone.utc).isoformat()

            key = self._status_key(file_id)
            value = json.dumps(status)

            ttl = self._get_ttl_for_status(status.get("status", "queued"))

            success = self.redis.set(key, value, ex=ttl)
            if success:
                logger.debug(f"Updated upload status for {file_id}")

            return success
        except Exception as e:
            logger.error(f"Error updating upload status for {file_id}: {e}")
            return False

    def delete_upload_status(self, file_id: str) -> bool:
        """Delete upload status"""
        try:
            key = self._status_key(file_id)
            deleted = self.redis.delete(key)

            if deleted:
                logger.info(f"Deleted upload status: {file_id}")

            return deleted > 0
        except Exception as e:
            logger.error(f"Error deleting upload status for {file_id}: {e}")
            return False

    # ===== Cleanup =====

    def cleanup_old_sessions(self, max_age_seconds: int = 7200) -> int:
        """
        Clean up old upload sessions (optional, Redis TTL handles most cleanup).
        Returns count of cleaned sessions.
        """
        try:
            # Find all upload session keys
            pattern = f"{self.UPLOAD_SESSION_PREFIX}*"
            keys = self.redis.keys(pattern)

            cleaned = 0
            now = time.time()

            for key in keys:
                try:
                    data = self.redis.get(key)
                    if data:
                        session = json.loads(data)
                        age = now - session.get("last_updated", now)

                        # Only clean completed/failed/cancelled sessions
                        if age > max_age_seconds and session.get("status") in [
                            "completed", "failed", "cancelled"
                        ]:
                            self.redis.delete(key)
                            cleaned += 1
                except Exception as e:
                    logger.warning(f"Error checking session {key}: {e}")
                    continue

            if cleaned > 0:
                logger.info(f"Cleaned up {cleaned} old upload sessions")

            return cleaned
        except Exception as e:
            logger.error(f"Error cleaning up old sessions: {e}")
            return 0

    # ===== Helper Methods =====

    def _session_key(self, upload_id: str) -> str:
        """Generate Redis key for upload session"""
        return f"{self.UPLOAD_SESSION_PREFIX}{upload_id}"

    def _chunks_key(self, upload_id: str) -> str:
        """Generate Redis key for chunk tracking"""
        return f"{self.UPLOAD_CHUNKS_PREFIX}{upload_id}"

    def _lock_key(self, upload_id: str) -> str:
        """Generate Redis key for upload lock"""
        return f"{self.UPLOAD_LOCK_PREFIX}{upload_id}"

    def _status_key(self, file_id: str) -> str:
        """Generate Redis key for upload status"""
        return f"{self.UPLOAD_STATUS_PREFIX}{file_id}"

    def _get_ttl_for_status(self, status: str) -> int:
        """Get appropriate TTL based on upload status"""
        if status in ["completed"]:
            return self.COMPLETED_TTL
        elif status in ["failed", "cancelled"]:
            return self.FAILED_TTL
        else:
            return self.SESSION_TTL

    # ===== Debug/Monitoring =====

    def get_all_active_uploads(self) -> List[Dict[str, Any]]:
        """Get all active upload sessions (for debugging)"""
        try:
            pattern = f"{self.UPLOAD_SESSION_PREFIX}*"
            keys = self.redis.keys(pattern)

            sessions = []
            for key in keys:
                data = self.redis.get(key)
                if data:
                    sessions.append(json.loads(data))

            return sessions
        except Exception as e:
            logger.error(f"Error getting active uploads: {e}")
            return []

    def get_stats(self) -> Dict[str, Any]:
        """Get Redis upload state statistics"""
        try:
            session_keys = self.redis.keys(f"{self.UPLOAD_SESSION_PREFIX}*")
            status_keys = self.redis.keys(f"{self.UPLOAD_STATUS_PREFIX}*")
            lock_keys = self.redis.keys(f"{self.UPLOAD_LOCK_PREFIX}*")

            return {
                "active_sessions": len(session_keys),
                "tracked_uploads": len(status_keys),
                "active_locks": len(lock_keys),
                "container_id": self.container_id
            }
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return {}


# Global singleton instance
_redis_upload_state = None


def get_redis_upload_state(container_id: Optional[str] = None) -> RedisUploadState:
    """Get global RedisUploadState instance"""
    global _redis_upload_state

    if _redis_upload_state is None:
        _redis_upload_state = RedisUploadState(container_id=container_id)

    return _redis_upload_state
