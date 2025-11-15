"""
Resource Limits and Constraints

All concurrency limits, rate limits, and size constraints.
Organized by feature/service for easy management.
"""

# ============================================================================
# TTS (TEXT-TO-SPEECH) LIMITS
# ============================================================================

class TTS:
    """Text-to-Speech service limits"""
    GLOBAL_MAX_SLOTS = 30  # System-wide concurrent Edge TTS operations
    PER_USER_CAP = 6  # Maximum concurrent TTS per user
    MAX_FFMPEG_CONCURRENT = 6  # Maximum concurrent FFMPEG processes

    @classmethod
    def validate(cls):
        """Validate TTS configuration"""
        if cls.PER_USER_CAP > cls.GLOBAL_MAX_SLOTS:
            raise ValueError(
                f"TTS.PER_USER_CAP ({cls.PER_USER_CAP}) cannot exceed "
                f"TTS.GLOBAL_MAX_SLOTS ({cls.GLOBAL_MAX_SLOTS})"
            )


# ============================================================================
# UPLOAD & DOWNLOAD LIMITS
# ============================================================================

class UPLOAD:
    """Upload limits and constraints"""
    MAX_SIZE_MB = 500  # Maximum file size in megabytes
    MAX_SIZE_BYTES = MAX_SIZE_MB * 1024 * 1024  # Converted to bytes
    MAX_CONCURRENT_UPLOADS = 5  # Per user


class DOWNLOAD:
    """Download limits"""
    MAX_CONCURRENT = 3  # Maximum concurrent downloads per user
    RATE_LIMIT_PER_HOUR = 100  # Download requests per hour


# ============================================================================
# SESSION LIMITS
# ============================================================================

class SESSION:
    """Playback and connection session limits"""
    MAX_PER_USER = 5  # Concurrent playback sessions
    MAX_WEBSOCKET_CONNECTIONS = 10  # Per user


# ============================================================================
# API RATE LIMITS
# ============================================================================

class RATE_LIMIT:
    """API rate limiting"""
    PER_MINUTE = 60  # General API calls per minute
    PER_HOUR = 1000  # General API calls per hour
    TTS_PER_DAY = 500  # TTS requests per day per user


# ============================================================================
# DATABASE QUERY LIMITS
# ============================================================================

class DATABASE:
    """Database query limits and pagination"""
    DEFAULT_PAGE_SIZE = 20
    MAX_PAGE_SIZE = 100
    MAX_SEARCH_RESULTS = 1000
    QUERY_TIMEOUT_SECONDS = 30


# ============================================================================
# CACHE LIMITS
# ============================================================================

class CACHE:
    """Cache size and retention limits"""
    TEXT_STORAGE_MAX_SIZE_GB = 15  # Maximum text storage cache size
    MAX_CACHE_ENTRIES = 10000  # Maximum cached items


# ============================================================================
# SPECIAL VALUES
# ============================================================================

UNLIMITED_VALUE = 999999999  # Represents "unlimited" in tier limits


# Validate configuration on module import
TTS.validate()


__all__ = [
    # Classes
    'TTS',
    'UPLOAD',
    'DOWNLOAD',
    'SESSION',
    'RATE_LIMIT',
    'DATABASE',
    'CACHE',

    # Special values
    'UNLIMITED_VALUE',
]
