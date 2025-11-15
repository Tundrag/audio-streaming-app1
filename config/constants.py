"""
General Application Constants

Constants that don't fit into other specific categories.
Quality settings, defaults, and other general values.
"""

# ============================================================================
# AUDIO QUALITY SETTINGS
# ============================================================================

DEFAULT_BITRATE = 128  # kbps for audio encoding
HLS_SEGMENT_DURATION = 10  # seconds per HLS segment


# ============================================================================
# TIMING & RETRY SETTINGS
# ============================================================================

MAX_RETRY_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 2
REQUEST_TIMEOUT_SECONDS = 30
WEBSOCKET_PING_INTERVAL = 30  # seconds


# ============================================================================
# PLATFORM TYPES
# ============================================================================

PLATFORM_TYPES = ["PATREON", "KOFI"]


# ============================================================================
# VERSION
# ============================================================================

APP_VERSION = "1.0.0"  # Application version for cache busting


__all__ = [
    # Audio
    'DEFAULT_BITRATE',
    'HLS_SEGMENT_DURATION',

    # Timing
    'MAX_RETRY_ATTEMPTS',
    'RETRY_DELAY_SECONDS',
    'REQUEST_TIMEOUT_SECONDS',
    'WEBSOCKET_PING_INTERVAL',

    # Platform
    'PLATFORM_TYPES',

    # Version
    'APP_VERSION',
]
