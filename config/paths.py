"""
File System Paths

All directory paths and file storage locations.
"""

from pathlib import Path

# ============================================================================
# BASE DIRECTORIES
# ============================================================================

# Application root
APP_ROOT = Path(__file__).parent.parent

# Media storage
MEDIA_ROOT = Path("/media")
STATIC_ROOT = Path("/static")


# ============================================================================
# TEMPORARY DIRECTORIES
# ============================================================================

TEMP_DIR = Path("/tmp")
TEMP_UPLOAD_DIR = TEMP_DIR / "uploads"
TEMP_MEDIA_STORAGE = TEMP_DIR / "media_storage"


# ============================================================================
# STORAGE DIRECTORIES
# ============================================================================

# Blob storage
BLOB_STORAGE_DIR = Path("/blobs")

# Text storage
TEXT_STORAGE_DIR = TEMP_MEDIA_STORAGE / "texts"

# Document extraction
DOCUMENT_EXTRACTION_DIR = TEMP_MEDIA_STORAGE / "document_extraction"

# Audio files
AUDIO_STORAGE_DIR = MEDIA_ROOT / "audio"

# Images
IMAGE_STORAGE_DIR = MEDIA_ROOT / "images"

# Covers
COVER_STORAGE_DIR = IMAGE_STORAGE_DIR


# ============================================================================
# HLS (HTTP Live Streaming) PATHS
# ============================================================================

HLS_STORAGE_DIR = MEDIA_ROOT / "hls"
HLS_TEMP_DIR = TEMP_DIR / "hls"


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def ensure_directories():
    """Create all required directories if they don't exist"""
    directories = [
        TEMP_UPLOAD_DIR,
        TEMP_MEDIA_STORAGE,
        BLOB_STORAGE_DIR,
        TEXT_STORAGE_DIR,
        DOCUMENT_EXTRACTION_DIR,
        AUDIO_STORAGE_DIR,
        IMAGE_STORAGE_DIR,
        HLS_STORAGE_DIR,
        HLS_TEMP_DIR,
    ]

    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)


__all__ = [
    # Base
    'APP_ROOT',
    'MEDIA_ROOT',
    'STATIC_ROOT',

    # Temp
    'TEMP_DIR',
    'TEMP_UPLOAD_DIR',
    'TEMP_MEDIA_STORAGE',

    # Storage
    'BLOB_STORAGE_DIR',
    'TEXT_STORAGE_DIR',
    'DOCUMENT_EXTRACTION_DIR',
    'AUDIO_STORAGE_DIR',
    'IMAGE_STORAGE_DIR',
    'COVER_STORAGE_DIR',

    # HLS
    'HLS_STORAGE_DIR',
    'HLS_TEMP_DIR',

    # Functions
    'ensure_directories',
]
