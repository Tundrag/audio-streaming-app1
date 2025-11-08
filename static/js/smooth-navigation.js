# routers/progress.py

from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy import and_
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import logging

from database import get_db
from models import User, Track, PlaybackProgress
from auth import login_required

# Initialize logger
logger = logging.getLogger(__name__)

# Create router without prefix (prefix is handled in __init__.py)
router = APIRouter()

# Routes
@router.post("/progress/save")
async def save_progress(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Save playback progress for a track"""
    try:
        progress = await request.json()
        track_id = int(progress.get('track_id'))
        
        if not track_id:
            raise HTTPException(status_code=400, detail="Invalid track ID")

        # Validate track existence
        track = db.query(Track).filter(Track.id == track_id).first()
        if not track:
            raise HTTPException(status_code=404, detail="Track not found")

        # Get or create progress record
        progress_record = db.query(PlaybackProgress).filter(
            and_(
                PlaybackProgress.user_id == current_user.id,
                PlaybackProgress.track_id == track_id
            )
        ).first()

        now = datetime.now(timezone.utc)
        duration = max(1, progress.get('duration', 0))
        current_position = progress.get('position', 0)
        completion_rate = min(100, (current_position / duration * 100)) if duration > 0 else 0
        is_completed = progress.get('completed', False) or completion_rate >= 90

        if progress_record:
            # Update existing record
            progress_record.position = current_position
            progress_record.duration = duration
            progress_record.completed = is_completed
            progress_record.device_info = progress.get('device_info')
            progress_record.updated_at = now
            progress_record.last_played = now
            
            if is_completed and not progress_record.completed:
                progress_record.play_count += 1
            
            progress_record.completion_rate = completion_rate
        else:
            # Create new record
            progress_record = PlaybackProgress(
                user_id=current_user.id,
                track_id=track_id,
                position=current_position,
                duration=duration,
                completed=is_completed,
                device_info=progress.get('device_info'),
                completion_rate=completion_rate,
                play_count=1 if is_completed else 0,
                last_played=now
            )
            db.add(progress_record)

        # Update track statistics
        track.last_accessed = now
        track.access_count = (track.access_count or 0) + 1

        try:
            db.commit()
            logger.info(f"Saved progress for track {track_id}: {completion_rate:.1f}% complete")
            return {
                "status": "success",
                "position": current_position,
                "completion_rate": completion_rate,
                "is_completed": is_completed
            }
        except Exception as db_error:
            db.rollback()
            logger.error(f"Database error: {str(db_error)}")
            raise HTTPException(status_code=500, detail="Database error")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving progress: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Server error")

@router.get("/progress/load/{track_id}")
async def load_progress(
    track_id: int,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Load playback progress for a track"""
    try:
        # Validate track exists
        track = db.query(Track).filter(Track.id == track_id).first()
        if not track:
            raise HTTPException(status_code=404, detail="Track not found")

        progress = db.query(PlaybackProgress).filter(
            and_(
                PlaybackProgress.user_id == current_user.id,
                PlaybackProgress.track_id == track_id
            )
        ).first()

        if progress:
            return {
                "position": progress.position,
                "duration": progress.duration,
                "completed": progress.completed,
                "completion_rate": progress.completion_rate,
                "play_count": progress.play_count,
                "last_played": progress.last_played.isoformat() if progress.last_played else None,
                "device_info": progress.device_info
            }
            
        return {
            "position": 0,
            "duration": 0,
            "completed": False,
            "completion_rate": 0,
            "play_count": 0,
            "last_played": None,
            "device_info": None
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error loading progress: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error loading progress")

@router.get("/progress/in-progress")
async def get_in_progress_tracks(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get all tracks that are in progress but not completed"""
    try:
        in_progress = db.query(PlaybackProgress).filter(
            and_(
                PlaybackProgress.user_id == current_user.id,
                PlaybackProgress.completed == False,
                PlaybackProgress.position > 0
            )
        ).order_by(PlaybackProgress.last_played.desc()).all()

        return [{
            "track_id": progress.track_id,
            "position": progress.position,
            "duration": progress.duration,
            "completion_rate": progress.completion_rate,
            "last_played": progress.last_played.isoformat() if progress.last_played else None,
            "device_info": progress.device_info
        } for progress in in_progress]

    except Exception as e:
        logger.error(f"Error getting in-progress tracks: {str(e)}")
        raise HTTPException(status_code=500, detail="Error retrieving tracks")