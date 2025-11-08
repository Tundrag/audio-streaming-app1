# voice_playlist_manager.py
# PURE VOICE-SPECIFIC ARCHITECTURE (No Legacy Support)

import asyncio
import aiofiles
import os
import numpy as np
from pathlib import Path
from typing import Dict, Optional
from sqlalchemy.orm import Session
from models import Track
import logging

logger = logging.getLogger(__name__)

# Configuration
BASE_HLS_DIR = Path(os.path.expanduser("~")) / ".hls_streaming"
SEGMENTS_DIR = BASE_HLS_DIR / "segments"

# Pure voice-specific architecture
DEFAULT_VOICE_ID = "en-US-AvaNeural"

class VoicePlaylistManager:
    """Pure Voice-Specific Playlist Manager - No legacy support"""
    
    @staticmethod
    def get_voice_directory_path(track_id: str, voice_id: str) -> Path:
        """Get voice-specific directory path - ALWAYS voice-{voice_id} format"""
        voice_dir = SEGMENTS_DIR / track_id / f"voice-{voice_id}"
        return voice_dir
    
    @staticmethod
    def get_voice_playlist_path(track_id: str, voice_id: str) -> Path:
        """Get voice-specific playlist path"""
        voice_dir = VoicePlaylistManager.get_voice_directory_path(track_id, voice_id)
        return voice_dir / "playlist.m3u8"
    
    @staticmethod
    async def create_voice_playlist_if_needed(track_id: str, voice_id: str, db: Session) -> bool:
        """Create voice-specific playlist.m3u8 if segments exist but playlist is missing"""
        
        try:
            voice_dir = VoicePlaylistManager.get_voice_directory_path(track_id, voice_id)
            playlist_path = VoicePlaylistManager.get_voice_playlist_path(track_id, voice_id)
            
            # If playlist already exists, nothing to do
            if playlist_path.exists():
                logger.info(f"📝 Playlist already exists for voice {voice_id}: {playlist_path}")
                return True
            
            # Check if we have segments for this voice
            segments = list(voice_dir.glob("segment_*.ts")) if voice_dir.exists() else []
            if not segments:
                logger.info(f"📝 No segments found for voice {voice_id} in {voice_dir}")
                return False
            
            # Get segment info
            segment_numbers = sorted([int(s.stem.split('_')[1]) for s in segments])
            first_segment = min(segment_numbers)
            last_segment = max(segment_numbers)
            
            logger.info(f"📝 Creating playlist for voice {voice_id}: segments {first_segment}-{last_segment} ({len(segments)} total)")
            
            # Get track duration from database
            track = db.query(Track).filter(Track.id == track_id).first()
            if not track or not track.duration:
                logger.error(f"❌ No track duration found for {track_id}")
                return False
            
            total_duration = track.duration
            segment_duration = 30.0  # Standard TTS segment duration
            total_segments = int(np.ceil(total_duration / segment_duration))
            
            # Generate playlist content
            playlist_content = [
                "#EXTM3U",
                "#EXT-X-VERSION:3",
                f"#EXT-X-TARGETDURATION:{int(segment_duration)}",
                "#EXT-X-MEDIA-SEQUENCE:0",
                "#EXT-X-PLAYLIST-TYPE:VOD"
            ]
            
            # Add segments with accurate durations
            remaining_duration = total_duration
            for i in range(total_segments):
                current_duration = min(segment_duration, remaining_duration)
                playlist_content.extend([
                    f"#EXTINF:{current_duration:.3f},",
                    f"segment_{i:05d}.ts"
                ])
                remaining_duration -= current_duration
            
            playlist_content.append("#EXT-X-ENDLIST")
            
            # Create directory and write playlist
            voice_dir.mkdir(parents=True, exist_ok=True)
            
            async with aiofiles.open(playlist_path, 'w') as f:
                await f.write('\n'.join(playlist_content))
            
            logger.info(f"✅ Created voice playlist: {playlist_path}")
            logger.info(f"📊 Playlist: {total_segments} segments, {total_duration:.2f}s duration for voice {voice_id}")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Error creating voice playlist for {voice_id}: {e}")
            return False

    @staticmethod
    async def get_voice_playlist_status(track_id: str, voice_id: str) -> Dict:
        """Get status of voice playlist and segments"""
        
        try:
            voice_dir = VoicePlaylistManager.get_voice_directory_path(track_id, voice_id)
            playlist_path = VoicePlaylistManager.get_voice_playlist_path(track_id, voice_id)
            
            # Check segments
            segments = list(voice_dir.glob("segment_*.ts")) if voice_dir.exists() else []
            segment_count = len(segments)
            
            # Check playlist
            has_playlist = playlist_path.exists()
            
            status = {
                "voice_id": voice_id,
                "has_playlist": has_playlist,
                "segment_count": segment_count,
                "playlist_path": str(playlist_path),
                "voice_dir": str(voice_dir),
                "ready_for_hls": has_playlist and segment_count > 0,
                "is_default_voice": voice_id == DEFAULT_VOICE_ID,
                "architecture": "pure_voice_specific"
            }
            
            if segments:
                segment_numbers = [int(s.stem.split('_')[1]) for s in segments]
                status.update({
                    "first_segment": min(segment_numbers),
                    "last_segment": max(segment_numbers),
                    "segments_available": sorted(segment_numbers)
                })
            
            return status
            
        except Exception as e:
            logger.error(f"❌ Error getting voice status for {voice_id}: {e}")
            return {
                "voice_id": voice_id,
                "has_playlist": False,
                "segment_count": 0,
                "architecture": "pure_voice_specific",
                "error": str(e)
            }

    @staticmethod
    async def ensure_voice_directory_exists(track_id: str, voice_id: str) -> bool:
        """Ensure voice-specific directory exists"""
        try:
            voice_dir = VoicePlaylistManager.get_voice_directory_path(track_id, voice_id)
            voice_dir.mkdir(parents=True, exist_ok=True)
            
            logger.info(f"📁 Voice directory ensured: {voice_dir}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error ensuring voice directory for {voice_id}: {e}")
            return False

    @staticmethod
    async def get_voice_segments_info(track_id: str, voice_id: str) -> Dict:
        """Get detailed information about voice segments"""
        
        try:
            voice_dir = VoicePlaylistManager.get_voice_directory_path(track_id, voice_id)
            
            if not voice_dir.exists():
                return {
                    "voice_id": voice_id,
                    "exists": False,
                    "segment_count": 0,
                    "total_size_mb": 0.0,
                    "segments": []
                }
            
            # Get all segments
            segments = list(voice_dir.glob("segment_*.ts"))
            total_size = 0
            segment_info = []
            
            for segment_path in sorted(segments):
                try:
                    segment_size = segment_path.stat().st_size
                    total_size += segment_size
                    
                    segment_index = int(segment_path.stem.split('_')[1])
                    
                    segment_info.append({
                        "index": segment_index,
                        "filename": segment_path.name,
                        "size_bytes": segment_size,
                        "size_mb": round(segment_size / (1024 * 1024), 2),
                        "path": str(segment_path)
                    })
                except Exception as seg_error:
                    logger.error(f"Error processing segment {segment_path}: {seg_error}")
            
            return {
                "voice_id": voice_id,
                "exists": True,
                "voice_dir": str(voice_dir),
                "segment_count": len(segment_info),
                "total_size_bytes": total_size,
                "total_size_mb": round(total_size / (1024 * 1024), 2),
                "segments": segment_info,
                "playlist_exists": VoicePlaylistManager.get_voice_playlist_path(track_id, voice_id).exists(),
                "architecture": "pure_voice_specific"
            }
            
        except Exception as e:
            logger.error(f"❌ Error getting voice segments info for {voice_id}: {e}")
            return {
                "voice_id": voice_id,
                "exists": False,
                "error": str(e)
            }

    @staticmethod
    async def cleanup_voice_segments(track_id: str, voice_id: str) -> Dict:
        """Clean up segments and playlist for a specific voice"""
        
        try:
            voice_dir = VoicePlaylistManager.get_voice_directory_path(track_id, voice_id)
            
            if not voice_dir.exists():
                return {
                    "voice_id": voice_id,
                    "status": "nothing_to_clean",
                    "segments_removed": 0,
                    "playlist_removed": False
                }
            
            # Count segments before cleanup
            segments = list(voice_dir.glob("segment_*.ts"))
            playlist_path = VoicePlaylistManager.get_voice_playlist_path(track_id, voice_id)
            
            segments_removed = 0
            playlist_removed = False
            
            # Remove segments
            for segment_path in segments:
                try:
                    segment_path.unlink()
                    segments_removed += 1
                except Exception as e:
                    logger.error(f"Error removing segment {segment_path}: {e}")
            
            # Remove playlist
            if playlist_path.exists():
                try:
                    playlist_path.unlink()
                    playlist_removed = True
                except Exception as e:
                    logger.error(f"Error removing playlist {playlist_path}: {e}")
            
            # Remove directory if empty
            try:
                if voice_dir.exists() and not any(voice_dir.iterdir()):
                    voice_dir.rmdir()
                    logger.info(f"🗑️ Removed empty voice directory: {voice_dir}")
            except Exception as e:
                logger.error(f"Error removing voice directory {voice_dir}: {e}")
            
            logger.info(f"🧹 Cleaned up voice {voice_id}: {segments_removed} segments, playlist: {playlist_removed}")
            
            return {
                "voice_id": voice_id,
                "status": "cleaned",
                "segments_removed": segments_removed,
                "playlist_removed": playlist_removed,
                "voice_dir_removed": not voice_dir.exists()
            }
            
        except Exception as e:
            logger.error(f"❌ Error cleaning up voice {voice_id}: {e}")
            return {
                "voice_id": voice_id,
                "status": "error",
                "error": str(e)
            }

# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

# Global instance
voice_playlist_manager = VoicePlaylistManager()

# Export convenience functions
async def create_voice_playlist_if_needed(track_id: str, voice_id: str, db: Session) -> bool:
    """Create voice-specific playlist if needed"""
    return await voice_playlist_manager.create_voice_playlist_if_needed(track_id, voice_id, db)

async def get_voice_playlist_status(track_id: str, voice_id: str) -> Dict:
    """Get voice playlist status"""
    return await voice_playlist_manager.get_voice_playlist_status(track_id, voice_id)

async def ensure_voice_directory_exists(track_id: str, voice_id: str) -> bool:
    """Ensure voice directory exists"""
    return await voice_playlist_manager.ensure_voice_directory_exists(track_id, voice_id)

async def get_voice_segments_info(track_id: str, voice_id: str) -> Dict:
    """Get detailed voice segments info"""
    return await voice_playlist_manager.get_voice_segments_info(track_id, voice_id)

async def cleanup_voice_segments(track_id: str, voice_id: str) -> Dict:
    """Clean up voice segments"""
    return await voice_playlist_manager.cleanup_voice_segments(track_id, voice_id)

__all__ = [
    'VoicePlaylistManager', 
    'voice_playlist_manager', 
    'create_voice_playlist_if_needed', 
    'get_voice_playlist_status',
    'ensure_voice_directory_exists',
    'get_voice_segments_info',
    'cleanup_voice_segments',
    'DEFAULT_VOICE_ID'
]