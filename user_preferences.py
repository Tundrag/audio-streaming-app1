# user_preferences.py
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from typing import Dict, Any, Optional
from pydantic import BaseModel
import logging

from models import User, UserTrackVoicePreference
from database import get_db
from auth import login_required

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/user", tags=["user_preferences"])


class VoicePreferenceRequest(BaseModel):
    voice_id: str


class TrackVoicePreferenceRequest(BaseModel):
    voice_id: str
    is_favorite: bool = False


@router.get("/preferences")
async def get_user_preferences(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
) -> Dict[str, Any]:
    """Get user's preferences including preferred voice"""
    try:
        return {
            "preferred_voice": current_user.preferred_voice
        }
    except Exception as e:
        logger.error(f"Error fetching user preferences: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch user preferences")


@router.put("/voice-preference")
async def update_voice_preference(
    voice_preference: VoicePreferenceRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
) -> Dict[str, Any]:
    """Update user's preferred voice"""
    try:
        # Validate voice_id is not empty
        if not voice_preference.voice_id or not voice_preference.voice_id.strip():
            raise HTTPException(status_code=400, detail="voice_id cannot be empty")

        # Update user's preferred voice
        current_user.preferred_voice = voice_preference.voice_id.strip()
        db.commit()
        db.refresh(current_user)

        logger.info(f"Updated voice preference for user {current_user.id} to {voice_preference.voice_id}")

        return {
            "success": True,
            "preferred_voice": current_user.preferred_voice
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating voice preference: {e}")
        raise HTTPException(status_code=500, detail="Failed to update voice preference")


# ═══════════════════════════════════════════════════════════════════════════
# Heart-Based Voice Preference System
# ═══════════════════════════════════════════════════════════════════════════
# Priority: Favorite (hearted) voice if cached > Track-specific > Track default
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/voice-preference/{track_id}")
async def get_track_voice_preference(
    track_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
) -> Dict[str, Any]:
    """
    Get voice preference for a specific track.

    Priority:
    1. Track-specific preference (non-favorited selection) - HIGHEST PRIORITY
    2. User's favorite (hearted) voice if it's cached for this track
    3. None (frontend will use track default)
    """
    try:
        # ✅ FIX: Check track-specific preference FIRST (highest priority)
        track_pref = db.query(UserTrackVoicePreference).filter(
            UserTrackVoicePreference.user_id == current_user.id,
            UserTrackVoicePreference.track_id == track_id
        ).first()

        if track_pref:
            return {
                "voice_id": track_pref.voice_id,
                "is_favorite": False,
                "source": "track_specific"
            }

        # Check if favorite is cached for this track (fallback if no track-specific)
        favorite_voice = current_user.preferred_voice
        if favorite_voice:
            # Import here to avoid circular dependency
            from enhanced_tts_api_voice import get_generated_voices_for_track

            cached_voices = await get_generated_voices_for_track(track_id)

            if favorite_voice in cached_voices:
                return {
                    "voice_id": favorite_voice,
                    "is_favorite": True,
                    "source": "favorite"
                }

        # No preference
        return {
            "voice_id": None,
            "is_favorite": False,
            "source": "default"
        }

    except Exception as e:
        logger.error(f"Error fetching track voice preference: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch voice preference")


@router.put("/voice-preference/{track_id}")
async def update_track_voice_preference(
    track_id: str,
    preference: TrackVoicePreferenceRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
) -> Dict[str, Any]:
    """
    Update voice preference for a track.

    is_favorite=True  → Hearts the voice (sets as global favorite)
    is_favorite=False → Sets as track-specific only (no heart)
    """
    try:
        voice_id = preference.voice_id.strip()
        is_favorite = preference.is_favorite

        if not voice_id:
            raise HTTPException(status_code=400, detail="voice_id cannot be empty")

        old_favorite = current_user.preferred_voice

        if is_favorite:
            # ❤️ Heart this voice (global favorite)
            current_user.preferred_voice = voice_id

            # Remove any track-specific preference for this track (favorite overrides)
            db.query(UserTrackVoicePreference).filter(
                UserTrackVoicePreference.user_id == current_user.id,
                UserTrackVoicePreference.track_id == track_id
            ).delete(synchronize_session=False)

            db.commit()

            logger.info(f"User {current_user.id} set favorite voice: {voice_id} (was: {old_favorite})")

            return {
                "voice_id": voice_id,
                "is_favorite": True,
                "previous_favorite": old_favorite,
                "source": "favorite",
                "message": f"Set as favorite voice for all tracks"
            }
        else:
            # 🎵 Track-specific selection (no heart)
            # ✅ FIX: ALWAYS save track-specific preference, even if it matches global favorite
            # This allows explicit track-specific overrides that persist even if global favorite changes

            # Upsert track-specific preference
            track_pref = db.query(UserTrackVoicePreference).filter(
                UserTrackVoicePreference.user_id == current_user.id,
                UserTrackVoicePreference.track_id == track_id
            ).first()

            if track_pref:
                track_pref.voice_id = voice_id
                from datetime import datetime, timezone
                track_pref.updated_at = datetime.now(timezone.utc)
            else:
                track_pref = UserTrackVoicePreference(
                    user_id=current_user.id,
                    track_id=track_id,
                    voice_id=voice_id
                )
                db.add(track_pref)

            db.commit()

            logger.info(f"User {current_user.id} set track-specific voice for {track_id}: {voice_id}")

            # Note if it matches global favorite
            matches_favorite = (current_user.preferred_voice == voice_id)
            message = "Set for this track only" if not matches_favorite else "Set for this track only (same as your favorite)"

            return {
                "voice_id": voice_id,
                "is_favorite": False,
                "source": "track_specific",
                "message": message
            }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating track voice preference: {e}")
        raise HTTPException(status_code=500, detail="Failed to update voice preference")


@router.delete("/voice-preference/favorite")
async def remove_favorite_voice(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
) -> Dict[str, Any]:
    """
    Remove favorite voice (unheart).
    Track-specific preferences remain unchanged.
    """
    try:
        old_favorite = current_user.preferred_voice

        if not old_favorite:
            return {
                "success": True,
                "removed": None,
                "message": "No favorite voice was set"
            }

        current_user.preferred_voice = None
        db.commit()

        logger.info(f"User {current_user.id} removed favorite voice: {old_favorite}")

        return {
            "success": True,
            "removed": old_favorite,
            "message": "Favorite voice removed"
        }

    except Exception as e:
        db.rollback()
        logger.error(f"Error removing favorite voice: {e}")
        raise HTTPException(status_code=500, detail="Failed to remove favorite voice")
