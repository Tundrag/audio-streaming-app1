"""
TTL (Time-To-Live) Constants

All cache expiration times and session timeouts in seconds.
Consolidated from: authorization_service.py, redis_state/, and 15+ other files
"""

# ============================================================================
# AUTHORIZATION & TOKENS
# ============================================================================

GRANT_TOKEN_TTL = 600  # 10 minutes - HLS access tokens


# ============================================================================
# UPLOAD SESSION TTLs
# ============================================================================

UPLOAD_SESSION_TTL = 7200  # 2 hours - Active uploads
UPLOAD_COMPLETED_TTL = 300  # 5 minutes - Completed uploads
UPLOAD_FAILED_TTL = 600  # 10 minutes - Failed uploads
UPLOAD_LOCK_TTL = 60  # 1 minute - Upload locks


# ============================================================================
# CACHE TTLs
# ============================================================================

# Word timing cache
WORD_TIMING_CACHE_TTL = 86400  # 24 hours - Word timings rarely change

# Text cache
TEXT_CACHE_TTL = 3600  # 1 hour - Default text cache
TEXT_CACHE_MAX_TTL = 86400  # 24 hours - Maximum text cache

# Progress tracking
PROGRESS_CACHE_TTL = 7200  # 2 hours - Upload/conversion progress

# Voice access cache
VOICE_ACCESS_CACHE_TTL = 7200  # 2 hours - Voice permission cache

# Upload stats
UPLOAD_STATS_TTL = 7200  # 2 hours - Upload statistics

# Document extraction
DOCUMENT_PROGRESS_TTL = 3600  # 1 hour - Document extraction progress


# ============================================================================
# CONVERSION & PROCESSING TTLs
# ============================================================================

CONVERSION_TTL = 3600  # 1 hour - Audio conversions
CONVERSION_LOCK_TTL = 300  # 5 minutes - Conversion locks


# ============================================================================
# SESSION TTLs
# ============================================================================

USER_SESSION_TTL = 86400  # 24 hours - User login sessions
WEBSOCKET_SESSION_TTL = 3600  # 1 hour - WebSocket connections


__all__ = [
    # Authorization
    'GRANT_TOKEN_TTL',

    # Uploads
    'UPLOAD_SESSION_TTL',
    'UPLOAD_COMPLETED_TTL',
    'UPLOAD_FAILED_TTL',
    'UPLOAD_LOCK_TTL',

    # Cache
    'WORD_TIMING_CACHE_TTL',
    'TEXT_CACHE_TTL',
    'TEXT_CACHE_MAX_TTL',
    'PROGRESS_CACHE_TTL',
    'VOICE_ACCESS_CACHE_TTL',
    'UPLOAD_STATS_TTL',
    'DOCUMENT_PROGRESS_TTL',

    # Conversion
    'CONVERSION_TTL',
    'CONVERSION_LOCK_TTL',

    # Sessions
    'USER_SESSION_TTL',
    'WEBSOCKET_SESSION_TTL',
]
