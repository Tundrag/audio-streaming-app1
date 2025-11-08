"""
Redis caches for multi-container deployments.

Provides shared caching across containers for:
- Text storage cache (reduces 8GB×N to shared 8GB)
- Word timing cache (eliminates duplicate computation)
- Voice access tracking (accurate cleanup decisions)
- Upload stats (progress visibility)
"""

from redis_state.cache.text import text_cache, CacheEntry
from redis_state.cache.word_timing import word_timing_cache
from redis_state.cache.voice_access import voice_access_tracker
from redis_state.cache.upload_stats import upload_stats, WriteStats

__all__ = [
    'text_cache',
    'CacheEntry',
    'word_timing_cache',
    'voice_access_tracker',
    'upload_stats',
    'WriteStats',
]
