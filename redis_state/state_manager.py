"""
Enterprise-grade generic Redis state manager for distributed systems.

This module provides a unified, namespaced Redis state management layer that
can be used across all application domains (uploads, TTS, downloads, sessions, etc.).

Design Principles:
- Single Responsibility: One manager for all Redis state operations
- DRY: Reusable methods across all domains
- Namespaced: Isolated keyspaces for different features
- Consistent: Same patterns and TTLs everywhere
- Scalable: Easy to add new features/domains
- Enterprise-grade: Resilient, monitored, well-tested

Usage:
    # For uploads
    upload_state = RedisStateManager("upload")
    upload_state.create_session(upload_id, {...})

    # For TTS
    tts_state = RedisStateManager("tts")
    tts_state.set_progress(job_id, {"phase": "generating", "percent": 50})

    # For downloads
    download_state = RedisStateManager("download")
    download_state.acquire_lock(download_id)
"""

import json
import time
import logging
from typing import Dict, Optional, Any, List, Set, Union
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from redis_state.config import redis_client

logger = logging.getLogger(__name__)


@dataclass
class RedisStateConfig:
    """Configuration for Redis state management"""
    # Default TTL values (seconds)
    SESSION_TTL: int = 7200  # 2 hours
    PROGRESS_TTL: int = 3600  # 1 hour
    LOCK_TTL: int = 60  # 1 minute
    COMPLETED_TTL: int = 300  # 5 minutes
    FAILED_TTL: int = 600  # 10 minutes
    CACHE_TTL: int = 86400  # 24 hours

    # Key patterns
    SESSION_SUFFIX: str = "session"
    PROGRESS_SUFFIX: str = "progress"
    LOCK_SUFFIX: str = "lock"
    STATUS_SUFFIX: str = "status"
    SET_SUFFIX: str = "set"
    COUNTER_SUFFIX: str = "counter"
    HASH_SUFFIX: str = "hash"


class RedisStateManager:
    """
    Generic Redis state manager with namespace support.

    Provides consistent, reusable Redis operations for any application domain.
    All operations are automatically namespaced and include error handling,
    logging, and fallback support via ResilientRedisClient.

    Key Features:
    - Namespaced keys (prevents key collisions between domains)
    - Automatic TTL management
    - Lock acquisition/release with atomic operations
    - Session/progress tracking
    - Set operations (for tracking collections)
    - Counter operations (for rate limiting, concurrency tracking)
    - Hash operations (for structured data)
    """

    def __init__(
        self,
        namespace: str,
        container_id: Optional[str] = None,
        config: Optional[RedisStateConfig] = None
    ):
        """
        Initialize Redis state manager for a specific namespace.

        Args:
            namespace: Domain identifier (e.g., "upload", "tts", "download")
            container_id: Optional container/instance identifier
            config: Optional custom configuration (uses defaults if not provided)
        """
        self.namespace = namespace
        self.redis = redis_client
        self.container_id = container_id or f"container_{time.time()}"
        self.config = config or RedisStateConfig()

        logger.info(f"RedisStateManager initialized: namespace={namespace}, container={self.container_id}")

    # ===== Key Management =====

    def _key(self, *parts: str) -> str:
        """
        Generate namespaced Redis key.

        Args:
            *parts: Key components to join

        Returns:
            Namespaced key string

        Example:
            _key("session", "abc123") → "upload:session:abc123"
        """
        return f"{self.namespace}:{':'.join(parts)}"

    def _session_key(self, session_id: str) -> str:
        """Generate key for session data"""
        return self._key(self.config.SESSION_SUFFIX, session_id)

    def _progress_key(self, job_id: str) -> str:
        """Generate key for progress tracking"""
        return self._key(self.config.PROGRESS_SUFFIX, job_id)

    def _lock_key(self, resource_id: str) -> str:
        """Generate key for locks"""
        return self._key(self.config.LOCK_SUFFIX, resource_id)

    def _status_key(self, entity_id: str) -> str:
        """Generate key for status tracking"""
        return self._key(self.config.STATUS_SUFFIX, entity_id)

    def _set_key(self, set_name: str) -> str:
        """Generate key for sets"""
        return self._key(self.config.SET_SUFFIX, set_name)

    def _counter_key(self, counter_name: str) -> str:
        """Generate key for counters"""
        return self._key(self.config.COUNTER_SUFFIX, counter_name)

    def _hash_key(self, hash_name: str) -> str:
        """Generate key for hashes"""
        return self._key(self.config.HASH_SUFFIX, hash_name)

    # ===== Session Management =====

    def create_session(
        self,
        session_id: str,
        data: Dict[str, Any],
        ttl: Optional[int] = None
    ) -> bool:
        """
        Create a new session with data.

        Args:
            session_id: Unique session identifier
            data: Session data to store (must be JSON-serializable)
            ttl: Optional custom TTL (uses SESSION_TTL if not provided)

        Returns:
            True if successful, False otherwise
        """
        try:
            # Add metadata (preserve original payload to avoid side effects)
            session_data = dict(data)
            session_data["session_id"] = session_id
            session_data.update({
                "container_id": self.container_id,
                "created_at": time.time(),
                "updated_at": time.time(),
            })

            key = self._session_key(session_id)
            value = json.dumps(session_data)
            ttl_seconds = ttl or self.config.SESSION_TTL

            success = self.redis.set(key, value, ex=ttl_seconds)

            if success:
                logger.debug(f"[{self.namespace}] Created session: {session_id}")
            else:
                logger.error(f"[{self.namespace}] Failed to create session: {session_id}")

            return success

        except Exception as e:
            logger.error(f"[{self.namespace}] Error creating session {session_id}: {e}")
            return False

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve session data.

        Args:
            session_id: Session identifier

        Returns:
            Session data dict or None if not found
        """
        try:
            key = self._session_key(session_id)
            data = self.redis.get(key)

            if data:
                session = json.loads(data)
                session.setdefault("session_id", session_id)
                return session
            return None

        except Exception as e:
            logger.error(f"[{self.namespace}] Error getting session {session_id}: {e}")
            return None

    def update_session(
        self,
        session_id: str,
        updates: Dict[str, Any],
        extend_ttl: bool = True
    ) -> bool:
        """
        Update existing session with new data.

        Args:
            session_id: Session identifier
            updates: Fields to update
            extend_ttl: Whether to reset TTL (default True)

        Returns:
            True if successful, False otherwise
        """
        try:
            session = self.get_session(session_id)
            if not session:
                logger.warning(f"[{self.namespace}] Cannot update non-existent session: {session_id}")
                return False

            # Merge updates
            session.update(updates)
            session["session_id"] = session_id
            session["updated_at"] = time.time()

            key = self._session_key(session_id)
            value = json.dumps(session)

            if extend_ttl:
                # Determine TTL based on status
                ttl = self._get_ttl_for_status(session.get("status", "active"))
                success = self.redis.set(key, value, ex=ttl)
            else:
                success = self.redis.set(key, value)

            if success:
                logger.debug(f"[{self.namespace}] Updated session: {session_id}")
            else:
                logger.error(f"[{self.namespace}] Failed to update session: {session_id}")

            return success

        except Exception as e:
            logger.error(f"[{self.namespace}] Error updating session {session_id}: {e}")
            return False

    def delete_session(self, session_id: str) -> bool:
        """
        Delete a session.

        Args:
            session_id: Session identifier

        Returns:
            True if deleted, False otherwise
        """
        try:
            key = self._session_key(session_id)
            deleted = self.redis.delete(key)

            if deleted:
                logger.info(f"[{self.namespace}] Deleted session: {session_id}")

            return deleted > 0

        except Exception as e:
            logger.error(f"[{self.namespace}] Error deleting session {session_id}: {e}")
            return False

    def get_all_sessions(self) -> List[Dict[str, Any]]:
        """
        Get all sessions in this namespace.

        Returns:
            List of session data dicts
        """
        try:
            pattern = self._session_key("*")
            keys = self.redis.keys(pattern)
            prefix = f"{self.namespace}:{self.config.SESSION_SUFFIX}:"

            sessions = []
            for key in keys:
                # Decode key and derive session_id (supports keys with colons in id)
                if isinstance(key, bytes):
                    key_str = key.decode("utf-8")
                else:
                    key_str = key
                session_id = key_str[len(prefix):] if key_str.startswith(prefix) else key_str

                data = self.redis.get(key)
                if data:
                    try:
                        session = json.loads(data)
                        session.setdefault("session_id", session_id)
                        sessions.append(session)
                    except Exception as e:
                        logger.warning(f"[{self.namespace}] Error parsing session {key}: {e}")

            return sessions

        except Exception as e:
            logger.error(f"[{self.namespace}] Error getting all sessions: {e}")
            return []

    # ===== Lock Management =====

    def acquire_lock(
        self,
        resource_id: str,
        timeout: Optional[int] = None,
        owner_id: Optional[str] = None
    ) -> bool:
        """
        Acquire exclusive lock on a resource (atomic operation).

        Args:
            resource_id: Resource to lock
            timeout: Lock TTL in seconds (uses LOCK_TTL if not provided)
            owner_id: Optional lock owner identifier (uses container_id if not provided)

        Returns:
            True if lock acquired, False if already locked
        """
        try:
            key = self._lock_key(resource_id)
            ttl = timeout or self.config.LOCK_TTL
            owner = owner_id or self.container_id

            # SET with NX (only if not exists) for atomic locking
            locked = self.redis.set(key, owner, ex=ttl, nx=True)

            if locked:
                logger.debug(f"[{self.namespace}] Acquired lock: {resource_id} by {owner}")
            else:
                logger.debug(f"[{self.namespace}] Failed to acquire lock: {resource_id}")

            return locked

        except Exception as e:
            logger.error(f"[{self.namespace}] Error acquiring lock {resource_id}: {e}")
            return False

    def release_lock(self, resource_id: str) -> bool:
        """
        Release lock on a resource.

        Args:
            resource_id: Resource to unlock

        Returns:
            True if released, False otherwise
        """
        try:
            key = self._lock_key(resource_id)
            deleted = self.redis.delete(key)

            if deleted:
                logger.debug(f"[{self.namespace}] Released lock: {resource_id}")

            return deleted > 0

        except Exception as e:
            logger.error(f"[{self.namespace}] Error releasing lock {resource_id}: {e}")
            return False

    def extend_lock(
        self,
        resource_id: str,
        additional_time: Optional[int] = None
    ) -> bool:
        """
        Extend lock TTL.

        Args:
            resource_id: Resource whose lock to extend
            additional_time: Seconds to add to TTL (uses LOCK_TTL if not provided)

        Returns:
            True if extended, False otherwise
        """
        try:
            key = self._lock_key(resource_id)
            ttl = additional_time or self.config.LOCK_TTL

            return self.redis.expire(key, ttl)

        except Exception as e:
            logger.error(f"[{self.namespace}] Error extending lock {resource_id}: {e}")
            return False

    def is_locked(self, resource_id: str) -> bool:
        """
        Check if resource is locked.

        Args:
            resource_id: Resource to check

        Returns:
            True if locked, False otherwise
        """
        try:
            key = self._lock_key(resource_id)
            return self.redis.exists(key) > 0

        except Exception as e:
            logger.error(f"[{self.namespace}] Error checking lock {resource_id}: {e}")
            return False

    def get_lock_owner(self, resource_id: str) -> Optional[str]:
        """
        Get the owner of a lock.

        Args:
            resource_id: Resource to check

        Returns:
            Owner ID or None if not locked
        """
        try:
            key = self._lock_key(resource_id)
            return self.redis.get(key)

        except Exception as e:
            logger.error(f"[{self.namespace}] Error getting lock owner {resource_id}: {e}")
            return None

    # ===== Progress Tracking =====

    def set_progress(
        self,
        job_id: str,
        progress_data: Dict[str, Any],
        ttl: Optional[int] = None
    ) -> bool:
        """
        Set progress information for a job.

        Args:
            job_id: Job identifier
            progress_data: Progress information
            ttl: Optional custom TTL (uses PROGRESS_TTL if not provided)

        Returns:
            True if successful, False otherwise
        """
        try:
            # Add timestamp
            data = {
                **progress_data,
                "updated_at": time.time(),
                "container_id": self.container_id
            }

            key = self._progress_key(job_id)
            value = json.dumps(data)
            ttl_seconds = ttl or self.config.PROGRESS_TTL

            success = self.redis.set(key, value, ex=ttl_seconds)

            if success:
                logger.debug(f"[{self.namespace}] Set progress: {job_id}")

            return success

        except Exception as e:
            logger.error(f"[{self.namespace}] Error setting progress {job_id}: {e}")
            return False

    def get_progress(self, job_id: str) -> Optional[Dict[str, Any]]:
        """
        Get progress information for a job.

        Args:
            job_id: Job identifier

        Returns:
            Progress data or None if not found
        """
        try:
            key = self._progress_key(job_id)
            data = self.redis.get(key)

            if data:
                return json.loads(data)
            return None

        except Exception as e:
            logger.error(f"[{self.namespace}] Error getting progress {job_id}: {e}")
            return None

    def delete_progress(self, job_id: str) -> bool:
        """
        Delete progress tracking.

        Args:
            job_id: Job identifier

        Returns:
            True if deleted, False otherwise
        """
        try:
            key = self._progress_key(job_id)
            deleted = self.redis.delete(key)

            if deleted:
                logger.debug(f"[{self.namespace}] Deleted progress: {job_id}")

            return deleted > 0

        except Exception as e:
            logger.error(f"[{self.namespace}] Error deleting progress {job_id}: {e}")
            return False

    # ===== Status Tracking =====

    def set_status(
        self,
        entity_id: str,
        status: str,
        metadata: Optional[Dict[str, Any]] = None,
        ttl: Optional[int] = None
    ) -> bool:
        """
        Set status for an entity.

        Args:
            entity_id: Entity identifier
            status: Status value
            metadata: Optional additional data
            ttl: Optional custom TTL

        Returns:
            True if successful, False otherwise
        """
        try:
            data = {
                "status": status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "container_id": self.container_id,
                **(metadata or {})
            }

            key = self._status_key(entity_id)
            value = json.dumps(data)

            # Smart TTL based on status
            if ttl is None:
                ttl = self._get_ttl_for_status(status)

            success = self.redis.set(key, value, ex=ttl)

            if success:
                logger.debug(f"[{self.namespace}] Set status: {entity_id} → {status}")

            return success

        except Exception as e:
            logger.error(f"[{self.namespace}] Error setting status {entity_id}: {e}")
            return False

    def get_status(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """
        Get status for an entity.

        Args:
            entity_id: Entity identifier

        Returns:
            Status data or None if not found
        """
        try:
            key = self._status_key(entity_id)
            data = self.redis.get(key)

            if data:
                return json.loads(data)
            return None

        except Exception as e:
            logger.error(f"[{self.namespace}] Error getting status {entity_id}: {e}")
            return None

    def delete_status(self, entity_id: str) -> bool:
        """Delete status tracking"""
        try:
            key = self._status_key(entity_id)
            deleted = self.redis.delete(key)

            if deleted:
                logger.debug(f"[{self.namespace}] Deleted status: {entity_id}")

            return deleted > 0

        except Exception as e:
            logger.error(f"[{self.namespace}] Error deleting status {entity_id}: {e}")
            return False

    # ===== Set Operations =====

    def add_to_set(self, set_name: str, *values: Any) -> int:
        """
        Add values to a set.

        Args:
            set_name: Set identifier
            *values: Values to add

        Returns:
            Number of values added
        """
        try:
            key = self._set_key(set_name)
            # Convert all values to strings for Redis
            str_values = [str(v) for v in values]
            added = self.redis.sadd(key, *str_values)

            if added:
                logger.debug(f"[{self.namespace}] Added {added} items to set: {set_name}")

            return added

        except Exception as e:
            logger.error(f"[{self.namespace}] Error adding to set {set_name}: {e}")
            return 0

    def remove_from_set(self, set_name: str, *values: Any) -> int:
        """Remove values from a set"""
        try:
            key = self._set_key(set_name)
            str_values = [str(v) for v in values]
            removed = self.redis.srem(key, *str_values)

            if removed:
                logger.debug(f"[{self.namespace}] Removed {removed} items from set: {set_name}")

            return removed

        except Exception as e:
            logger.error(f"[{self.namespace}] Error removing from set {set_name}: {e}")
            return 0

    def get_set_members(self, set_name: str) -> Set[str]:
        """Get all members of a set"""
        try:
            key = self._set_key(set_name)
            return self.redis.smembers(key)

        except Exception as e:
            logger.error(f"[{self.namespace}] Error getting set members {set_name}: {e}")
            return set()

    def is_in_set(self, set_name: str, value: Any) -> bool:
        """Check if value is in set"""
        try:
            key = self._set_key(set_name)
            return self.redis.sismember(key, str(value))

        except Exception as e:
            logger.error(f"[{self.namespace}] Error checking set membership {set_name}: {e}")
            return False

    def get_set_count(self, set_name: str) -> int:
        """Get number of items in set"""
        try:
            key = self._set_key(set_name)
            return self.redis.scard(key)

        except Exception as e:
            logger.error(f"[{self.namespace}] Error getting set count {set_name}: {e}")
            return 0

    # ===== Counter Operations =====

    def increment_counter(self, counter_name: str, amount: int = 1) -> int:
        """
        Increment a counter.

        Args:
            counter_name: Counter identifier
            amount: Amount to increment by (default 1)

        Returns:
            New counter value
        """
        try:
            key = self._counter_key(counter_name)

            if amount == 1:
                new_value = self.redis.incr(key)
            else:
                new_value = self.redis.incr(key)
                # Increment by additional amount if needed
                for _ in range(amount - 1):
                    new_value = self.redis.incr(key)

            logger.debug(f"[{self.namespace}] Incremented counter {counter_name}: {new_value}")
            return new_value

        except Exception as e:
            logger.error(f"[{self.namespace}] Error incrementing counter {counter_name}: {e}")
            return 0

    def decrement_counter(self, counter_name: str, amount: int = 1) -> int:
        """Decrement a counter"""
        try:
            key = self._counter_key(counter_name)

            if amount == 1:
                new_value = self.redis.decr(key)
            else:
                new_value = self.redis.decr(key)
                for _ in range(amount - 1):
                    new_value = self.redis.decr(key)

            logger.debug(f"[{self.namespace}] Decremented counter {counter_name}: {new_value}")
            return new_value

        except Exception as e:
            logger.error(f"[{self.namespace}] Error decrementing counter {counter_name}: {e}")
            return 0

    def get_counter(self, counter_name: str) -> int:
        """Get counter value"""
        try:
            key = self._counter_key(counter_name)
            value = self.redis.get(key)
            return int(value) if value else 0

        except Exception as e:
            logger.error(f"[{self.namespace}] Error getting counter {counter_name}: {e}")
            return 0

    def reset_counter(self, counter_name: str) -> bool:
        """Reset counter to 0"""
        try:
            key = self._counter_key(counter_name)
            deleted = self.redis.delete(key)

            if deleted:
                logger.debug(f"[{self.namespace}] Reset counter: {counter_name}")

            return deleted > 0

        except Exception as e:
            logger.error(f"[{self.namespace}] Error resetting counter {counter_name}: {e}")
            return False

    # ===== Hash Operations =====

    def set_hash_field(self, hash_name: str, field: str, value: Any) -> bool:
        """Set a field in a hash"""
        try:
            key = self._hash_key(hash_name)
            # Convert value to JSON if it's not a string
            str_value = json.dumps(value) if not isinstance(value, str) else value

            result = self.redis.hset(key, field, str_value)
            logger.debug(f"[{self.namespace}] Set hash field: {hash_name}.{field}")
            return True

        except Exception as e:
            logger.error(f"[{self.namespace}] Error setting hash field {hash_name}.{field}: {e}")
            return False

    def get_hash_field(self, hash_name: str, field: str) -> Optional[Any]:
        """Get a field from a hash"""
        try:
            key = self._hash_key(hash_name)
            value = self.redis.hget(key, field)

            if value:
                # Try to parse as JSON, fallback to string
                try:
                    return json.loads(value)
                except:
                    return value
            return None

        except Exception as e:
            logger.error(f"[{self.namespace}] Error getting hash field {hash_name}.{field}: {e}")
            return None

    def get_hash_all(self, hash_name: str) -> Dict[str, Any]:
        """Get all fields from a hash"""
        try:
            key = self._hash_key(hash_name)
            data = self.redis.hgetall(key)

            # Try to parse JSON values
            result = {}
            for field, value in data.items():
                try:
                    result[field] = json.loads(value)
                except:
                    result[field] = value

            return result

        except Exception as e:
            logger.error(f"[{self.namespace}] Error getting hash {hash_name}: {e}")
            return {}

    def delete_hash_field(self, hash_name: str, field: str) -> bool:
        """Delete a field from a hash"""
        try:
            key = self._hash_key(hash_name)
            deleted = self.redis.delete(f"{key}:{field}")

            if deleted:
                logger.debug(f"[{self.namespace}] Deleted hash field: {hash_name}.{field}")

            return deleted > 0

        except Exception as e:
            logger.error(f"[{self.namespace}] Error deleting hash field {hash_name}.{field}: {e}")
            return False

    # ===== Cleanup & Utilities =====

    def cleanup_old_keys(self, max_age_seconds: int = 3600) -> int:
        """
        Clean up old completed/failed sessions and statuses.

        Args:
            max_age_seconds: Maximum age before cleanup

        Returns:
            Number of keys cleaned up
        """
        try:
            cleaned = 0
            now = time.time()

            # Clean old sessions
            for session in self.get_all_sessions():
                age = now - session.get("updated_at", now)
                if age > max_age_seconds and session.get("status") in ["completed", "failed", "cancelled"]:
                    session_id = session.get("id") or session.get("session_id") or session.get("upload_id")
                    if session_id and self.delete_session(session_id):
                        cleaned += 1

            # Clean old status entries
            pattern = self._status_key("*")
            keys = self.redis.keys(pattern)

            for key in keys:
                try:
                    data = self.redis.get(key)
                    if data:
                        status_data = json.loads(data)
                        timestamp_str = status_data.get("timestamp")
                        if timestamp_str:
                            timestamp = datetime.fromisoformat(timestamp_str)
                            age = (datetime.now(timezone.utc) - timestamp).total_seconds()

                            if age > max_age_seconds and status_data.get("status") in ["completed", "failed"]:
                                if self.redis.delete(key):
                                    cleaned += 1
                except Exception as e:
                    logger.warning(f"[{self.namespace}] Error checking key {key}: {e}")

            if cleaned > 0:
                logger.info(f"[{self.namespace}] Cleaned up {cleaned} old keys")

            return cleaned

        except Exception as e:
            logger.error(f"[{self.namespace}] Error cleaning up old keys: {e}")
            return 0

    def get_stats(self) -> Dict[str, Any]:
        """
        Get statistics about this namespace.

        Returns:
            Dict with counts of various key types
        """
        try:
            return {
                "namespace": self.namespace,
                "container_id": self.container_id,
                "sessions": len(self.redis.keys(self._session_key("*"))),
                "progress": len(self.redis.keys(self._progress_key("*"))),
                "locks": len(self.redis.keys(self._lock_key("*"))),
                "statuses": len(self.redis.keys(self._status_key("*"))),
                "sets": len(self.redis.keys(self._set_key("*"))),
                "counters": len(self.redis.keys(self._counter_key("*"))),
                "hashes": len(self.redis.keys(self._hash_key("*"))),
            }

        except Exception as e:
            logger.error(f"[{self.namespace}] Error getting stats: {e}")
            return {"error": str(e)}

    def _get_ttl_for_status(self, status: str) -> int:
        """
        Get appropriate TTL based on status.

        Args:
            status: Status value

        Returns:
            TTL in seconds
        """
        status_lower = status.lower() if status else ""

        if status_lower in ["completed", "success", "done"]:
            return self.config.COMPLETED_TTL
        elif status_lower in ["failed", "error", "cancelled", "canceled"]:
            return self.config.FAILED_TTL
        elif status_lower in ["processing", "queued", "active", "uploading", "generating"]:
            return self.config.SESSION_TTL
        else:
            # Default to session TTL
            return self.config.SESSION_TTL

    def __repr__(self) -> str:
        """String representation for debugging"""
        stats = self.get_stats()
        return (
            f"RedisStateManager(namespace='{self.namespace}', "
            f"sessions={stats.get('sessions', 0)}, "
            f"locks={stats.get('locks', 0)}, "
            f"progress={stats.get('progress', 0)})"
        )


# ===== Convenience Functions =====

def get_state_manager(namespace: str, container_id: Optional[str] = None) -> RedisStateManager:
    """
    Get a RedisStateManager instance for a namespace.

    Args:
        namespace: Domain identifier
        container_id: Optional container identifier

    Returns:
        RedisStateManager instance
    """
    return RedisStateManager(namespace, container_id)


# Global instances for common namespaces (optional convenience)
upload_state = RedisStateManager("upload")
tts_state = RedisStateManager("tts")
download_state = RedisStateManager("download")
session_state = RedisStateManager("session")


__all__ = [
    "RedisStateManager",
    "RedisStateConfig",
    "get_state_manager",
    "upload_state",
    "tts_state",
    "download_state",
    "session_state",
]
