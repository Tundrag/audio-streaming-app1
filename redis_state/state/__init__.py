"""
Redis state managers for multi-container deployments.

Provides shared state management across containers for:
- HLS segment progress tracking
- Conversion tracking and distributed locks
- Upload/download state management
"""

from redis_state.state.progress import progress_state
from redis_state.state.conversion import conversion_state
from redis_state.state.upload import upload_state
from redis_state.state.download import album_download_state, track_download_state

# Legacy support
from redis_state.state.download import get_album_download_state, get_track_download_state
download_state = album_download_state  # Alias for backward compatibility

__all__ = [
    'progress_state',
    'conversion_state',
    'upload_state',
    'download_state',
    'album_download_state',
    'track_download_state',
    'get_album_download_state',
    'get_track_download_state',
]
