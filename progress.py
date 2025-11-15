# routers/progress.py

from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy import and_
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from typing import List, Optional
import logging

from database import get_db
from models import User, Track, Album, PlaybackProgress, TrackPlays
from auth import login_required

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/progress",
    tags=["progress"],
    responses={404: {"description": "Not found"}},
)

@router.post("/save")
async def save_progress(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Save playback progress for a track"""
    try:
        # Get progress data from request body
        progress = await request.json()
        track_id = progress.get('track_id')

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
        client_time = progress.get('client_time')  # Timestamp from client
        completion_rate = min(100, (current_position / duration * 100)) if duration > 0 else 0
        is_listen = completion_rate >= 60  # Listen threshold
        is_completed = progress.get('completed', False) or completion_rate >= 90  # Completion threshold

        # Get or create TrackPlays record
        track_plays = db.query(TrackPlays).filter(
            and_(
                TrackPlays.user_id == current_user.id,
                TrackPlays.track_id == track_id
            )
        ).first()

        if not track_plays:
            track_plays = TrackPlays(
                user_id=current_user.id,
                track_id=track_id,
                play_count=0,
                completions_count=0
            )
            db.add(track_plays)

        if progress_record:
            # Check if incoming save is older than what we already have
            if client_time and progress_record.last_played:
                client_timestamp = datetime.fromtimestamp(client_time / 1000, tz=timezone.utc)
                if client_timestamp < progress_record.last_played:
                    logger.warning(f"Rejected stale progress save for track {track_id}: "
                                 f"client_time={client_timestamp} < last_played={progress_record.last_played}")
                    return {
                        "status": "rejected_stale",
                        "reason": "Server has newer progress",
                        "server_position": progress_record.position,
                        "server_timestamp": progress_record.last_played.isoformat()
                    }

            # Update existing record
            progress_record.position = current_position
            progress_record.duration = duration
            progress_record.device_info = progress.get('device_info')
            progress_record.updated_at = now
            progress_record.last_played = now
            progress_record.completion_rate = completion_rate

            # Store word position for voice-independent position tracking
            word_position_data = progress.get('word_position', {})
            if word_position_data and word_position_data.get('word_index') is not None:
                progress_record.word_position = word_position_data.get('word_index')
                progress_record.last_voice_id = word_position_data.get('voice_id')

            # 🎯 60% THRESHOLD: Count as "listen" (engagement metric)
            if is_listen and not progress_record.counted_as_listen:
                progress_record.counted_as_listen = True
                track_plays.play_count = (track_plays.play_count or 0) + 1
                logger.info(f"✅ Counted as LISTEN (60%) for track {track_id}")

            # 🎯 90% THRESHOLD: Count as "completion"
            if is_completed and not progress_record.counted_as_completion:
                progress_record.counted_as_completion = True
                progress_record.completed = True
                progress_record.play_count = (progress_record.play_count or 0) + 1
                track_plays.completions_count = (track_plays.completions_count or 0) + 1
                logger.info(f"✅ Counted as COMPLETION (90%) for track {track_id}")

            # Allow uncompleting if user seeks back below 90%
            if not is_completed and progress_record.completed:
                progress_record.completed = False

            # Update TrackPlays metrics
            track_plays.increment_play(
                completion_rate=completion_rate,
                play_time=current_position
            )

        else:
            # Create new record
            word_position_data = progress.get('word_position', {})
            word_index = word_position_data.get('word_index') if word_position_data else None
            voice_id = word_position_data.get('voice_id') if word_position_data else None

            progress_record = PlaybackProgress(
                user_id=current_user.id,
                track_id=track_id,
                position=current_position,
                duration=duration,
                completed=is_completed,
                device_info=progress.get('device_info'),
                completion_rate=completion_rate,
                play_count=1 if is_completed else 0,
                counted_as_listen=is_listen,  # Set flag if already at 60%
                counted_as_completion=is_completed,  # Set flag if already at 90%
                last_played=now,
                word_position=word_index,
                last_voice_id=voice_id
            )
            db.add(progress_record)

            # Count initial thresholds if already met
            if is_listen:
                track_plays.play_count = (track_plays.play_count or 0) + 1
                logger.info(f"✅ NEW: Counted as LISTEN (60%) for track {track_id}")

            if is_completed:
                track_plays.completions_count = (track_plays.completions_count or 0) + 1
                logger.info(f"✅ NEW: Counted as COMPLETION (90%) for track {track_id}")

            # Update metrics
            track_plays.increment_play(
                completion_rate=completion_rate,
                play_time=current_position
            )

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
            logger.error(f"Database error saving progress: {str(db_error)}")
            raise HTTPException(status_code=500, detail="Database error")

    except HTTPException:
        raise
    except ValueError as ve:
        logger.error(f"Validation error: {str(ve)}")
        raise HTTPException(status_code=400, detail="Invalid data format")
    except Exception as e:
        logger.error(f"Error saving progress: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Server error")

@router.get("/load/{track_id}")
async def load_progress(
    track_id: str,
    voice: Optional[str] = None,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Load playback progress for a track with optional voice translation"""
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
            result = {
                "position": progress.position,
                "duration": progress.duration,
                "completed": progress.completed,
                "completion_rate": progress.completion_rate,
                "play_count": progress.play_count,
                "last_played": progress.last_played.isoformat() if progress.last_played else None,
                "device_info": progress.device_info,
                "word_position": progress.word_position,
                "last_voice_id": progress.last_voice_id
            }

            # If voice is specified and different from saved voice, translate word position
            if (voice and progress.last_voice_id and voice != progress.last_voice_id
                and progress.word_position is not None and track.track_type == 'tts'):
                try:
                    # Import here to avoid circular dependency
                    from text_storage_service import text_storage_service

                    # Get word timings for the new voice
                    word_timings = await text_storage_service.get_word_timings(str(track_id), voice, db)

                    if word_timings and progress.word_position < len(word_timings):
                        # Translate word position to time in new voice
                        new_time = word_timings[progress.word_position].get('start_time', progress.position)
                        result['position'] = new_time
                        result['voice_translated'] = True
                        logger.info(f"Translated progress for track {track_id}: word {progress.word_position} "
                                  f"from voice {progress.last_voice_id} to {voice} (time: {new_time}s)")
                except Exception as e:
                    logger.warning(f"Failed to translate word position for track {track_id}: {str(e)}")
                    # Fall back to original position if translation fails
                    result['voice_translated'] = False

            return result

        return {
            "position": 0,
            "duration": 0,
            "completed": False,
            "completion_rate": 0,
            "play_count": 0,
            "last_played": None,
            "device_info": None,
            "word_position": None,
            "last_voice_id": None
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error loading progress: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error loading progress")

@router.get("/in-progress")
async def get_in_progress_tracks(
    limit: Optional[int] = None,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get tracks that are in progress but not completed (optionally limited for previews)"""
    try:
        logger.info(f"Fetching in-progress tracks for user: {current_user.email}, limit: {limit}")

        query = db.query(PlaybackProgress).filter(
            and_(
                PlaybackProgress.user_id == current_user.id,
                PlaybackProgress.completed == False,
                PlaybackProgress.position > 0
            )
        ).order_by(PlaybackProgress.last_played.desc())

        if limit is not None:
            query = query.limit(limit)

        in_progress = query.all()

        logger.info(f"Retrieved {len(in_progress)} progress records.")

        # Build a list of track IDs
        track_ids = [progress.track_id for progress in in_progress]
        logger.info(f"Track IDs: {track_ids}")

        # Fetch tracks
        tracks = db.query(Track).filter(Track.id.in_(track_ids)).all()
        logger.info(f"Retrieved {len(tracks)} tracks.")

        # Build a mapping from track_id to track
        track_dict = {track.id: track for track in tracks}

        # Fetch albums
        album_ids = [track.album_id for track in tracks if track.album_id]
        logger.info(f"Album IDs: {album_ids}")

        albums = db.query(Album).filter(Album.id.in_(album_ids)).all()
        logger.info(f"Retrieved {len(albums)} albums.")

        # Build a mapping from album_id to album
        album_dict = {album.id: album for album in albums}

        # Build the result list with visibility filtering
        in_progress_tracks = []
        for progress in in_progress:
            track = track_dict.get(progress.track_id)
            if not track:
                logger.warning(f"Track not found for track_id: {progress.track_id}")
                continue  # Skip if track not found

            # Apply visibility filtering based on user role
            visibility = getattr(track, 'visibility_status', 'visible')
            should_include = False
            if current_user.is_creator:
                # Creator can see all tracks
                should_include = True
            elif current_user.is_team:
                # Team can see all except hidden_from_all
                should_include = (visibility != "hidden_from_all")
            else:
                # Regular users can only see visible tracks
                should_include = (visibility == "visible")

            if not should_include:
                logger.info(f"Filtered out track due to visibility - ID: {track.id}, Title: {track.title}, Visibility: {visibility}")
                continue

            album = album_dict.get(track.album_id)
            cover_path = album.cover_path if album else '/static/images/default-cover.jpg'

            track_info = {
                "id": str(track.id),
                "title": track.title,
                "cover_path": cover_path,
                "album_title": album.title if album else 'Unknown Album',
                "progress": (progress.position / progress.duration * 100) if progress.duration > 0 else 0,
                "position": float(progress.position),
                "duration": float(progress.duration),
                "completion_rate": progress.completion_rate,
                "last_played": progress.last_played.isoformat() if progress.last_played else None,
                "device_info": progress.device_info
            }
            in_progress_tracks.append(track_info)
            logger.info(f"Added track to in-progress: {track_info}")

        logger.info(f"Returning {len(in_progress_tracks)} in-progress tracks")
        return in_progress_tracks

    except Exception as e:
        logger.error(f"Error getting in-progress tracks: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error retrieving tracks")
