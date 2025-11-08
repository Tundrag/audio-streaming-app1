# voice_sample_api.py - Simple Voice Sample API

from fastapi import APIRouter, HTTPException, Depends, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from database import get_db
from models import User, AvailableVoice
from auth import login_required
from voice_sample_service import voice_sample_service
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/api/voices/{voice_id}/sample")
async def get_voice_sample(
    voice_id: str,
    force_regenerate: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Get voice sample audio file - simple and direct"""
    try:
        # Validate voice exists
        voice = db.query(AvailableVoice).filter(
            AvailableVoice.voice_id == voice_id,
            AvailableVoice.is_active == True
        ).first()
        
        if not voice:
            raise HTTPException(status_code=404, detail="Voice not found")
        
        # Generate/get sample
        sample_file = await voice_sample_service.get_voice_sample(voice_id, force_regenerate)
        
        if not sample_file or not sample_file.exists():
            raise HTTPException(status_code=500, detail="Failed to generate voice sample")
        
        # Return audio file directly
        return FileResponse(
            path=sample_file,
            media_type='audio/mpeg',
            filename=f"{voice_id}_sample.mp3",
            headers={
                'Cache-Control': 'public, max-age=86400',  # Cache for 1 day
                'X-Voice-ID': voice_id,
                'X-Sample-Text': voice_sample_service.sample_text
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving voice sample {voice_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get voice sample")

@router.get("/api/voices/{voice_id}/sample/info")
async def get_voice_sample_info(
    voice_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Get voice sample information without generating audio"""
    try:
        # Validate voice exists
        voice = db.query(AvailableVoice).filter(
            AvailableVoice.voice_id == voice_id,
            AvailableVoice.is_active == True
        ).first()
        
        if not voice:
            raise HTTPException(status_code=404, detail="Voice not found")
        
        # Check if sample exists
        sample_file = voice_sample_service.sample_dir / f"{voice_id}.mp3"
        sample_exists = sample_file.exists()
        
        return {
            "voice_id": voice_id,
            "sample_text": voice_sample_service.sample_text,
            "sample_exists": sample_exists,
            "sample_url": voice_sample_service.get_sample_url(voice_id),
            "voice_info": {
                "display_name": voice.display_name,
                "language_code": voice.language_code,
                "gender": voice.gender
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting voice sample info {voice_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get voice sample info")

# Export router
__all__ = ['router']