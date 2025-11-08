"""
Redis module for multi-container state management.

This module provides Redis-backed state managers and caches that ensure
consistency across multiple containers in load-balanced deployments.

Usage:
    # Import from state
    from redis_state.state import progress_state, conversion_state, upload_state, download_state

    # Import from cache
    from redis_state.cache import text_cache, word_timing_cache, voice_access_tracker, upload_stats

    # Import core components
    from redis_state.config import redis_client
    from redis_state.state_manager import RedisStateManager
"""

# Core components
from redis_state.config import redis_client, ResilientRedisClient, ResilientAsyncRedisClient
from redis_state.state_manager import RedisStateManager, RedisStateConfig

# State managers
from redis_state.state import (
    progress_state,
    conversion_state,
    upload_state,
    download_state,
    album_download_state,
    track_download_state,
)

# Caches
from redis_state.cache import (
    text_cache,
    word_timing_cache,
    voice_access_tracker,
    upload_stats,
)

__all__ = [
    # Core
    'redis_client',
    'ResilientRedisClient',
    'ResilientAsyncRedisClient',
    'RedisStateManager',
    'RedisStateConfig',
    # State
    'progress_state',
    'conversion_state',
    'upload_state',
    'download_state',
    'album_download_state',
    'track_download_state',
    # Cache
    'text_cache',
    'word_timing_cache',
    'voice_access_tracker',
    'upload_stats',
]
