# voice_sample_service.py - SIMPLE Voice Sampling Service

import asyncio
import aiofiles
import edge_tts
import tempfile
import os
import logging
from pathlib import Path
from typing import Optional
import time

logger = logging.getLogger(__name__)

class VoiceSampleService:
    """Simple voice sampling service - no complexity, just samples"""
    
    def __init__(self):
        # Simple sample storage
        tmp = Path("/tmp")
        self.sample_dir = Path("/tmp/media_storage/voice_samples")
        self.sample_dir.mkdir(parents=True, exist_ok=True)
        
        # Fixed sample text for all voices
        self.sample_text = "Hello, this is a sample of my voice. How do you like the way I sound?"
        
        logger.info("Voice Sample Service initialized")

    async def get_voice_sample(self, voice_id: str, force_regenerate: bool = False) -> Optional[Path]:
        """Get voice sample - generate if not cached"""
        try:
            sample_file = self.sample_dir / f"{voice_id}.mp3"
            
            # Return cached if exists and not forced regeneration
            if sample_file.exists() and not force_regenerate:
                # Check if file is recent (less than 30 days old)
                if time.time() - sample_file.stat().st_mtime < 30 * 24 * 3600:
                    logger.info(f"Returning cached sample: {voice_id}")
                    return sample_file
            
            # Generate new sample
            logger.info(f"Generating sample for voice: {voice_id}")
            
            # Use Edge TTS to generate sample
            communicate = edge_tts.Communicate(self.sample_text, voice_id)
            
            # Generate audio data
            audio_data = b""
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_data += chunk["data"]
            
            if not audio_data:
                logger.error(f"No audio data generated for voice {voice_id}")
                return None
            
            # Save to file
            async with aiofiles.open(sample_file, 'wb') as f:
                await f.write(audio_data)
            
            logger.info(f"Generated sample for {voice_id}: {len(audio_data)} bytes")
            return sample_file
            
        except Exception as e:
            logger.error(f"Error generating sample for {voice_id}: {str(e)}")
            return None

    def get_sample_url(self, voice_id: str) -> str:
        """Get URL for voice sample"""
        return f"/api/voices/{voice_id}/sample"

    async def cleanup_old_samples(self, days_old: int = 30):
        """Clean up old sample files"""
        try:
            cutoff_time = time.time() - (days_old * 24 * 3600)
            
            for sample_file in self.sample_dir.glob("*.mp3"):
                if sample_file.stat().st_mtime < cutoff_time:
                    sample_file.unlink()
                    logger.info(f"Cleaned up old sample: {sample_file.name}")
                    
        except Exception as e:
            logger.error(f"Error cleaning up samples: {str(e)}")

# Global instance
voice_sample_service = VoiceSampleService()

__all__ = ['voice_sample_service', 'VoiceSampleService']