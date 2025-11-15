"""
URL and Path Constants

All URL prefixes and patterns used across the application.
Moved from: app.py, enhanced_app_routes_voice.py
"""

# ============================================================================
# MEDIA & STATIC URLs
# ============================================================================

MEDIA_URL = "/media"
STATIC_URL = "/static"

# ============================================================================
# IMAGE URLs
# ============================================================================

DEFAULT_COVER_URL = f"{MEDIA_URL}/images/default-album.jpg"
COVER_URL_PREFIX = f"{MEDIA_URL}/images"

# ============================================================================
# AUDIO URLs
# ============================================================================

AUDIO_URL_PREFIX = f"{MEDIA_URL}/audio"

# ============================================================================
# API URLs
# ============================================================================

API_V1_PREFIX = "/api/v1"
API_PREFIX = "/api"

__all__ = [
    'MEDIA_URL',
    'STATIC_URL',
    'DEFAULT_COVER_URL',
    'COVER_URL_PREFIX',
    'AUDIO_URL_PREFIX',
    'API_V1_PREFIX',
    'API_PREFIX',
]
