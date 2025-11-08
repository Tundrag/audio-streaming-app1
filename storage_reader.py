# storage_reader.py
# Simple consolidated storage reader - everything you need in one file

import asyncio
import logging
import os
import shutil
import psutil
from pathlib import Path
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class AsyncStorageReader:
    """Simple async storage reader that doesn't block the event loop"""
    
    def __init__(self, max_workers: int = 4):
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="storage_reader")
        logger.info("AsyncStorageReader initialized")

    async def cleanup(self):
        """Cleanup resources"""
        self.executor.shutdown(wait=False)
        logger.info("AsyncStorageReader cleanup complete")

    # ============================================================================
    # SYSTEM STORAGE
    # ============================================================================

    def _get_system_storage_sync(self, path: Path) -> Dict:
        """Get system storage info synchronously (runs in thread pool)"""
        try:
            total, used, free = shutil.disk_usage(path)
            usage_percent = (used / total) * 100 if total > 0 else 0
            
            return {
                'total_bytes': total,
                'used_bytes': used,
                'free_bytes': free,
                'total_gb': total / (1024**3),
                'used_gb': used / (1024**3),
                'free_gb': free / (1024**3),
                'usage_percent': usage_percent,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            logger.error(f"Error getting system storage: {e}")
            raise

    async def get_system_storage(self, path: Path) -> Dict:
        """Get system storage information asynchronously"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, self._get_system_storage_sync, path)

    # ============================================================================
    # MEMORY INFO
    # ============================================================================

    def _get_memory_info_sync(self) -> Dict:
        """Get memory info synchronously (runs in thread pool)"""
        try:
            memory = psutil.virtual_memory()
            return {
                'total_bytes': memory.total,
                'available_bytes': memory.available,
                'used_bytes': memory.used,
                'total_gb': memory.total / (1024**3),
                'available_gb': memory.available / (1024**3),
                'used_gb': memory.used / (1024**3),
                'usage_percent': memory.percent,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            logger.error(f"Error getting memory info: {e}")
            raise

    async def get_memory_info(self) -> Dict:
        """Get memory information asynchronously"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, self._get_memory_info_sync)

    # ============================================================================
    # HLS DIRECTORY SCANNING (MAIN FEATURE YOU NEED)
    # ============================================================================

    def _scan_hls_directory_sync(self, hls_dir: Path) -> Dict:
        """Scan HLS directory synchronously (runs in thread pool)"""
        try:
            if not hls_dir.exists():
                return {
                    'exists': False,
                    'total_size_bytes': 0,
                    'total_size_gb': 0,
                    'track_count': 0,
                    'total_files': 0,
                    'tracks': [],
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }

            total_size = 0
            total_files = 0
            tracks = []
            
            # Look for segments directory
            segments_dir = hls_dir / "segments"
            if segments_dir.exists():
                for track_dir in segments_dir.iterdir():
                    if track_dir.is_dir():
                        track_id = track_dir.name
                        track_size = 0
                        track_files = 0
                        
                        # Count files and size in track directory
                        for file_path in track_dir.rglob('*'):
                            if file_path.is_file():
                                try:
                                    file_size = file_path.stat().st_size
                                    track_size += file_size
                                    track_files += 1
                                except (OSError, FileNotFoundError):
                                    continue
                        
                        if track_size > 0:
                            tracks.append({
                                'track_id': track_id,
                                'size_bytes': track_size,
                                'size_gb': track_size / (1024**3),
                                'file_count': track_files
                            })
                            
                        total_size += track_size
                        total_files += track_files

            # Sort tracks by size (largest first)
            tracks.sort(key=lambda x: x['size_bytes'], reverse=True)

            return {
                'exists': True,
                'total_size_bytes': total_size,
                'total_size_gb': total_size / (1024**3),
                'track_count': len(tracks),
                'total_files': total_files,
                'tracks': tracks,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error scanning HLS directory: {e}")
            raise

    async def get_hls_directory_info(self, hls_dir: Path) -> Dict:
        """Get HLS directory information asynchronously"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, self._scan_hls_directory_sync, hls_dir)

    # ============================================================================
    # SPECIFIC TRACK INFO
    # ============================================================================

    def _get_track_info_sync(self, hls_dir: Path, track_id: str) -> Dict:
        """Get specific track info synchronously (runs in thread pool)"""
        try:
            track_dir = hls_dir / "segments" / track_id
            
            if not track_dir.exists():
                return {
                    'exists': False,
                    'track_id': track_id,
                    'size_bytes': 0,
                    'size_gb': 0,
                    'file_count': 0,
                    'files': []
                }

            total_size = 0
            files_info = []
            
            for file_path in track_dir.rglob('*'):
                if file_path.is_file():
                    try:
                        file_size = file_path.stat().st_size
                        total_size += file_size
                        files_info.append({
                            'name': file_path.name,
                            'relative_path': str(file_path.relative_to(track_dir)),
                            'size_bytes': file_size,
                            'size_mb': file_size / (1024**2),
                            'modified': datetime.fromtimestamp(file_path.stat().st_mtime).isoformat()
                        })
                    except (OSError, FileNotFoundError):
                        continue

            return {
                'exists': True,
                'track_id': track_id,
                'size_bytes': total_size,
                'size_gb': total_size / (1024**3),
                'file_count': len(files_info),
                'files': files_info
            }
            
        except Exception as e:
            logger.error(f"Error getting track info for {track_id}: {e}")
            raise

    async def get_track_info(self, hls_dir: Path, track_id: str) -> Dict:
        """Get specific track information asynchronously"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, self._get_track_info_sync, hls_dir, track_id)

    # ============================================================================
    # DIRECTORY SIZE
    # ============================================================================

    def _get_directory_size_sync(self, directory: Path) -> Dict:
        """Get directory size synchronously (runs in thread pool)"""
        try:
            if not directory.exists():
                return {
                    'exists': False,
                    'size_bytes': 0,
                    'size_gb': 0,
                    'file_count': 0
                }

            total_size = 0
            file_count = 0
            
            for file_path in directory.rglob('*'):
                if file_path.is_file():
                    try:
                        total_size += file_path.stat().st_size
                        file_count += 1
                    except (OSError, FileNotFoundError):
                        continue

            return {
                'exists': True,
                'size_bytes': total_size,
                'size_gb': total_size / (1024**3),
                'size_mb': total_size / (1024**2),
                'file_count': file_count,
                'path': str(directory)
            }
            
        except Exception as e:
            logger.error(f"Error getting directory size for {directory}: {e}")
            raise

    async def get_directory_size(self, directory: Path) -> Dict:
        """Get directory size asynchronously"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, self._get_directory_size_sync, directory)

    # ============================================================================
    # LIST ALL TRACKS
    # ============================================================================

    def _list_tracks_sync(self, hls_dir: Path) -> Dict:
        """List all tracks synchronously (runs in thread pool)"""
        try:
            segments_dir = hls_dir / "segments"
            
            if not segments_dir.exists():
                return {
                    'track_count': 0,
                    'tracks': []
                }

            tracks = []
            for track_dir in segments_dir.iterdir():
                if track_dir.is_dir():
                    track_id = track_dir.name
                    
                    # Quick size calculation
                    track_size = 0
                    file_count = 0
                    try:
                        for file_path in track_dir.rglob('*'):
                            if file_path.is_file():
                                track_size += file_path.stat().st_size
                                file_count += 1
                    except Exception:
                        continue
                    
                    tracks.append({
                        'track_id': track_id,
                        'size_bytes': track_size,
                        'size_gb': track_size / (1024**3),
                        'file_count': file_count
                    })

            # Sort by size (largest first)
            tracks.sort(key=lambda x: x['size_bytes'], reverse=True)

            return {
                'track_count': len(tracks),
                'tracks': tracks
            }
            
        except Exception as e:
            logger.error(f"Error listing tracks: {e}")
            raise

    async def list_tracks(self, hls_dir: Path) -> Dict:
        """List all tracks asynchronously"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, self._list_tracks_sync, hls_dir)

    # ============================================================================
    # COMBINED OVERVIEW
    # ============================================================================

    async def get_storage_overview(self, system_path: Path, hls_dir: Path) -> Dict:
        """Get complete storage overview asynchronously"""
        try:
            # Run all queries concurrently
            system_task = asyncio.create_task(self.get_system_storage(system_path))
            memory_task = asyncio.create_task(self.get_memory_info())
            hls_task = asyncio.create_task(self.get_hls_directory_info(hls_dir))
            
            system_info, memory_info, hls_info = await asyncio.gather(
                system_task, memory_task, hls_task
            )
            
            return {
                'system_storage': system_info,
                'memory': memory_info,
                'hls_directory': hls_info,
                'summary': {
                    'system_usage_percent': system_info['usage_percent'],
                    'memory_usage_percent': memory_info['usage_percent'],
                    'hls_size_gb': hls_info['total_size_gb'],
                    'total_tracks': hls_info['track_count'],
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }
            }
            
        except Exception as e:
            logger.error(f"Error getting storage overview: {e}")
            raise


# ============================================================================
# SIMPLE USAGE
# ============================================================================

# Just create instances when you need them:
# storage_reader = AsyncStorageReader()
# try:
#     hls_info = await storage_reader.get_hls_directory_info(Path("/path/to/hls"))
# finally:
#     await storage_reader.cleanup()