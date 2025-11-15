"""
Centralized Configuration System for Audio Streaming App

Modular configuration similar to redis_state/ structure.
Import everything from here for easy access across the app.

Usage:
    from config import MEDIA_URL, GLOBAL_MAX_TTS_SLOTS
    from config import settings
    from config.limits import TTS, UPLOAD, CACHE
"""

# Import all constant modules
from config.constants import *
from config.limits import *
from config.paths import *
from config.urls import *
from config.ttl import *

# Import settings (environment-specific config)
from config.settings import settings

__all__ = [
    # Settings
    'settings',

    # Core modules are imported with *
    # Individual constants accessible directly
]
