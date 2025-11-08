"""
Redis-backed voice access tracking using generic RedisStateManager.
Shares voice access metrics across all containers for accurate cleanup decisions.
"""
import logging
import time
from typing import Dict, Optional, Any
from redis_state.state_manager import RedisStateManager

logger = logging.getLogger(__name__)


class RedisVoiceAccessTracker:
    """
    Voice access tracker using generic RedisStateManager.
    Provides dict-like interface for backward compatibility with voice_cache_manager.py
    """

    ACCESS_TTL = 7200  # 2 hours (matches original cache_ttl)

    def __init__(self, container_id: Optional[str] = None):
        # Use generic manager with "voice_access" namespace
        self._manager = RedisStateManager("voice_access", container_id=container_id)
        self.container_id = self._manager.container_id
        self.cache_ttl = self.ACCESS_TTL
        logger.info(f"RedisVoiceAccessTracker initialized: container={self.container_id}")

    @property
    def voice_access_cache(self):
        """Dict-like interface for backward compatibility"""
        class VoiceAccessDict:
            def __init__(self, parent):
                self.parent = parent

            def __contains__(self, track_id: str) -> bool:
                """Check if track has voice access data"""
                return self.parent._manager.get_session(f"track:{track_id}") is not None

            def __getitem__(self, track_id: str) -> Dict:
                """Get voice access data for track"""
                data = self.parent._manager.get_session(f"track:{track_id}")
                if data is None:
                    # Return empty dict like original behavior
                    return {}
                return data

            def __setitem__(self, track_id: str, value: Dict):
                """Set voice access data for track"""
                self.parent._manager.create_session(
                    f"track:{track_id}",
                    value,
                    ttl=self.parent.ACCESS_TTL
                )

            def get(self, track_id: str, default=None) -> Optional[Dict]:
                """Get voice access data with default"""
                return self.parent._manager.get_session(f"track:{track_id}") or default

            def pop(self, track_id: str, default=None) -> Optional[Dict]:
                """Remove and return voice access data"""
                data = self.parent._manager.get_session(f"track:{track_id}")
                if data:
                    self.parent._manager.delete_session(f"track:{track_id}")
                return data if data else default

            def keys(self):
                """Get all tracked track IDs (note: this is expensive in Redis)"""
                # Note: RedisStateManager doesn't expose keys(), so we can't implement this efficiently
                # The original code used this in cleanup_old_entries()
                # We'll rely on Redis TTL for cleanup instead
                logger.warning("voice_access_cache.keys() called - not efficiently implemented in Redis")
                return []

        return VoiceAccessDict(self)

    def record_segment_access(self, track_id: str, voice_id: str, segment_id: str = None):
        """Record segment access - updates Redis state"""
        # Get current track data
        track_data = self._manager.get_session(f"track:{track_id}") or {}

        # Get or create voice data
        if voice_id not in track_data:
            track_data[voice_id] = {
                'last_access': time.time(),
                'segment_count': 0,
                'segments_accessed': []  # Use list instead of set for JSON serialization
            }

        voice_data = track_data[voice_id]
        voice_data['last_access'] = time.time()
        voice_data['segment_count'] += 1

        if segment_id:
            segments = voice_data.get('segments_accessed', [])
            if segment_id not in segments:
                segments.append(segment_id)
            voice_data['segments_accessed'] = segments

        # Update in Redis
        self._manager.create_session(f"track:{track_id}", track_data, ttl=self.ACCESS_TTL)

        logger.debug(f"Voice access recorded: track={track_id}, voice={voice_id}, segment={segment_id}")

    def get_voice_activity(self, track_id: str, voice_id: str) -> Dict:
        """Get recent activity for a voice"""
        track_data = self._manager.get_session(f"track:{track_id}")

        if track_data and voice_id in track_data:
            data = track_data[voice_id]
            time_since_access = time.time() - data['last_access']

            return {
                'is_active': time_since_access < 600,  # VOICE_IDLE_TIMEOUT from original
                'last_access': data['last_access'],
                'time_since_access': time_since_access,
                'segment_count': data['segment_count'],
                'unique_segments': len(data.get('segments_accessed', []))
            }

        return {
            'is_active': False,
            'last_access': None,
            'time_since_access': float('inf'),
            'segment_count': 0,
            'unique_segments': 0
        }

    def cleanup_old_entries(self):
        """Remove old entries - now handled by Redis TTL"""
        # Original implementation iterated over self.voice_access_cache.keys()
        # With Redis, TTL handles cleanup automatically
        logger.debug("Cleanup called - Redis TTL handles automatic expiration")


# Global instance
voice_access_tracker = RedisVoiceAccessTracker()
