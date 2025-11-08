# tts_api_endpoints.py
# TTS creation endpoint for album integration

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel, validator
from typing import Optional
import uuid
import asyncio
import logging
import time
import json
from datetime import datetime, timezone

# Import existing dependencies
from database import get_db, SessionLocal
from models import Album, Track, User
from auth import login_required

logger = logging.getLogger(__name__)

# ========================================
# REQUEST/RESPONSE MODELS
# ========================================

class TTSCreateRequest(BaseModel):
    title: str
    text: str
    voice: str = 'en-US-AvaNeural'
    
    @validator('title')
    def validate_title(cls, v):
        if not v or not v.strip():
            raise ValueError('Title is required')
        if len(v.strip()) > 200:
            raise ValueError('Title must be less than 200 characters')
        return v.strip()
    
    @validator('text')
    def validate_text(cls, v):
        if not v or not v.strip():
            raise ValueError('Text content is required')
        if len(v.strip()) < 10:
            raise ValueError('Text must be at least 10 characters')
        if len(v.strip()) > 500000:
            raise ValueError('Text must be less than 500,000 characters')
        return v.strip()
    
    @validator('voice')
    def validate_voice(cls, v):
        available_voices = [
            'en-US-AvaNeural', 'en-US-AriaNeural', 'en-US-GuyNeural',
            'en-US-JennyNeural', 'en-US-ChristopherNeural', 'en-US-EricNeural',
            'en-US-MichelleNeural', 'en-US-RogerNeural', 'en-US-SteffanNeural'
        ]
        if v not in available_voices:
            raise ValueError(f'Voice must be one of: {", ".join(available_voices)}')
        return v

class TTSCreateResponse(BaseModel):
    track_id: str
    status: str
    message: str
    text_size_mb: float
    estimated_duration_minutes: float
    total_chunks: int
    voice: str

class TTSProgressResponse(BaseModel):
    track_id: str
    status: str
    progress: float
    chunks_processed: Optional[int] = None
    total_chunks: Optional[int] = None
    current_phase: Optional[str] = None
    estimated_time_remaining: Optional[int] = None

# ========================================
# UTILITY FUNCTIONS
# ========================================

def check_album_access(user: User, album: Album) -> bool:
    """Check if user can create tracks in album"""
    if user.is_creator and album.created_by_id == user.id:
        return True
    if user.is_team and album.created_by_id == user.created_by:
        return True
    return False

# ========================================
# ROUTER SETUP
# ========================================

router = APIRouter()

# ========================================
# TTS CREATION ENDPOINT
# ========================================

@router.post("/api/albums/{album_id}/tracks/create-tts", response_model=TTSCreateResponse)
async def create_tts_track(
    album_id: str,
    request: TTSCreateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Create TTS track from text"""
    
    try:
        # Validate album and permissions
        album = db.query(Album).filter(Album.id == album_id).first()
        if not album:
            raise HTTPException(status_code=404, detail="Album not found")
        
        if not check_album_access(current_user, album):
            raise HTTPException(status_code=403, detail="Permission denied")
        
        # Generate track ID
        track_id = str(uuid.uuid4())
        
        logger.info(f"🎤 Creating TTS track: {request.title}")
        logger.info(f"📊 Text: {len(request.text):,} chars, Voice: {request.voice}")
        
        # Quick duration estimate
        word_count = len(request.text.split())
        estimated_duration = (word_count / 150) * 60  # 150 WPM
        estimated_chunks = max(1, len(request.text) // 8000)  # 8k char chunks
        
        # Calculate text metadata
        text_metadata = {
            'size_bytes': len(request.text.encode('utf-8')),
            'size_mb': round(len(request.text.encode('utf-8')) / 1024 / 1024, 2),
            'word_count': word_count,
            'character_count': len(request.text),
            'estimated_chunks': estimated_chunks,
            'estimated_duration': estimated_duration,
            'voice': request.voice,
            'approach': 'standard_tts'
        }
        
        # Create track record
        try:
            new_track = Track(
                id=track_id,
                title=request.title,
                file_path=f"/tts/{track_id}/complete.mp3",
                album_id=album_id,
                created_by_id=current_user.id,
                upload_status='processing',
                track_type='tts',
                source_text=request.text,  # Store source text
                default_voice=request.voice,
                has_read_along=True,
                tts_status='processing',
                tts_progress=0,
                duration=estimated_duration,
                format='mp3',
                codec='mp3',
                bit_rate=128000,
                sample_rate=24000,
                channels=1,
                audio_metadata=text_metadata,
                tier_requirements={
                    "is_public": True,
                    "minimum_cents": 0,
                    "allowed_tier_ids": []
                },
                access_count=0,
                segmentation_status='pending'
            )
            
            db.add(new_track)
            db.commit()
            logger.info(f"✅ Created TTS track record: {track_id}")
            
        except Exception as e:
            logger.error(f"Database error: {e}")
            db.rollback()
            raise HTTPException(status_code=500, detail="Database error")
        
        # Start TTS creation asynchronously
        try:
            background_tasks.add_task(
                process_tts_track,
                track_id=track_id,
                title=request.title,
                text_content=request.text,
                voice=request.voice,
                user_id=current_user.id
            )
            
            logger.info(f"✅ TTS background task queued for {track_id}")
            
        except Exception as tts_error:
            logger.error(f"TTS creation error: {tts_error}")
            try:
                new_track.tts_status = 'error'
                new_track.upload_status = 'failed'
                db.commit()
            except:
                pass
            raise HTTPException(status_code=500, detail=f"TTS creation failed: {str(tts_error)}")
        
        return TTSCreateResponse(
            track_id=track_id,
            status="processing",
            message=f"TTS track creation started for '{request.title}'",
            text_size_mb=text_metadata['size_mb'],
            estimated_duration_minutes=round(estimated_duration / 60, 1),
            total_chunks=estimated_chunks,
            voice=request.voice
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating TTS track: {str(e)}", exc_info=True)
        if 'db' in locals():
            db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create TTS track: {str(e)}")

@router.get("/api/tts/progress/{track_id}", response_model=TTSProgressResponse)
async def get_tts_progress(
    track_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Get TTS processing progress"""
    
    try:
        # Get track info
        track = db.query(Track).filter(Track.id == track_id).first()
        if not track:
            raise HTTPException(status_code=404, detail="Track not found")
        
        if track.track_type != 'tts':
            raise HTTPException(status_code=400, detail="Track is not a TTS track")
        
        # Check access
        album = db.query(Album).filter(Album.id == track.album_id).first()
        if not album:
            raise HTTPException(status_code=404, detail="Album not found")
        
        if not check_album_access(current_user, album):
            raise HTTPException(status_code=403, detail="Permission denied")
        
        # Extract progress information
        progress = track.tts_progress or 0
        status = track.tts_status or 'pending'
        
        # Get chunk information from metadata
        chunks_completed = 0
        total_chunks = 0
        
        if track.audio_metadata:
            try:
                if isinstance(track.audio_metadata, str):
                    metadata = json.loads(track.audio_metadata)
                else:
                    metadata = track.audio_metadata
                total_chunks = metadata.get('estimated_chunks', 0)
                chunks_completed = metadata.get('chunks_processed', 0)
            except:
                pass
        
        # Determine status and progress
        if progress >= 100 or status == 'ready':
            status = 'ready'
            progress = 100
        elif status in ['error', 'failed']:
            pass  # Keep error status
        elif progress > 0:
            status = 'processing'
        else:
            status = 'pending'
        
        return TTSProgressResponse(
            track_id=track_id,
            status=status,
            progress=progress,
            chunks_processed=chunks_completed,
            total_chunks=total_chunks,
            current_phase='audio_generation' if status == 'processing' else None
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting TTS progress: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get progress")

# ========================================
# BACKGROUND PROCESSING FUNCTIONS
# ========================================

async def process_tts_track(
    track_id: str,
    title: str,
    text_content: str,
    voice: str,
    user_id: int
):
    """Background processing for TTS track - simplified version"""
    
    db = SessionLocal()
    
    try:
        logger.info(f"🎤 Starting TTS processing for track {track_id} with voice {voice}")
        
        # Step 1: Set processing status
        track = db.query(Track).filter(Track.id == track_id).first()
        if track:
            track.tts_status = 'processing'
            track.tts_progress = 10
            db.commit()
        
        # Step 2: Simulate TTS processing (replace with actual TTS service)
        await simulate_tts_processing(track_id, text_content, voice, db)
        
        # Step 3: Mark as complete
        if track:
            track.tts_status = 'ready'
            track.tts_progress = 100
            track.upload_status = 'complete'
            track.segmentation_status = 'complete'
            
            # Update metadata with completion info
            if track.audio_metadata:
                if isinstance(track.audio_metadata, str):
                    metadata = json.loads(track.audio_metadata)
                else:
                    metadata = track.audio_metadata
                metadata['chunks_processed'] = metadata.get('estimated_chunks', 1)
                metadata['processing_completed'] = datetime.now(timezone.utc).isoformat()
                track.audio_metadata = metadata
            
            db.commit()
        
        logger.info(f"✅ TTS processing completed for track {track_id}")
        
    except Exception as e:
        logger.error(f"❌ TTS processing error for track {track_id}: {str(e)}", exc_info=True)
        
        try:
            track = db.query(Track).filter(Track.id == track_id).first()
            if track:
                track.tts_status = 'error'
                track.upload_status = 'failed'
                db.commit()
        except Exception as cleanup_error:
            logger.error(f"Error updating failed track status: {str(cleanup_error)}")
    finally:
        db.close()

async def simulate_tts_processing(track_id: str, text_content: str, voice: str, db: Session):
    """Simulate TTS processing - replace with actual TTS service call"""
    
    # Simulate processing steps
    steps = [
        ("Initializing TTS...", 20),
        ("Processing text chunks...", 40),
        ("Generating audio...", 60),
        ("Creating segments...", 80),
        ("Finalizing...", 100)
    ]
    
    for step_name, progress in steps:
        logger.info(f"TTS {track_id}: {step_name} ({progress}%)")
        
        # Update progress in database
        track = db.query(Track).filter(Track.id == track_id).first()
        if track:
            track.tts_progress = progress
            
            # Update metadata
            if track.audio_metadata:
                if isinstance(track.audio_metadata, str):
                    metadata = json.loads(track.audio_metadata)
                else:
                    metadata = track.audio_metadata
                metadata['current_step'] = step_name
                metadata['last_updated'] = datetime.now(timezone.utc).isoformat()
                track.audio_metadata = metadata
            
            db.commit()
        
        # Simulate processing time
        await asyncio.sleep(2)
    
    logger.info(f"✅ TTS simulation completed for track {track_id}")

# Export router
__all__ = ['router']