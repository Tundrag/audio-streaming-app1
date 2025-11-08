# download_queue.py

import asyncio
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

class DownloadQueue:
    def __init__(self, max_concurrent: int = 5):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.active_downloads = {}
        self.queue = asyncio.Queue()
        
    async def add_download(self, user_id: int, album_id: str):
        """Add a download request to queue"""
        await self.queue.put({
            'user_id': user_id,
            'album_id': album_id,
            'queued_at': datetime.now(timezone.utc)
        })
        
    async def start_download(self, user_id: int, album_id: str):
        """Acquire a download slot"""
        async with self.semaphore:
            self.active_downloads[f"{user_id}_{album_id}"] = {
                'started_at': datetime.now(timezone.utc),
                'user_id': user_id,
                'album_id': album_id
            }
            try:
                yield
            finally:
                del self.active_downloads[f"{user_id}_{album_id}"]
                
    def get_queue_position(self, user_id: int, album_id: str) -> int:
        """Get position in queue for a download"""
        position = 1
        for item in self.queue._queue:
            if item['user_id'] == user_id and item['album_id'] == album_id:
                return position
            position += 1
        return 0
        
    def get_active_downloads(self) -> dict:
        """Get currently active downloads"""
        return self.active_downloads

# Initialize the queue
download_queue = DownloadQueue(DOWNLOAD_CONFIG['max_concurrent_downloads'])