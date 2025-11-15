# enhanced_app_routes_voice.py - FULLY ASYNC VERSION

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy import select, func, and_, desc
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, validator
from typing import Optional, List, Dict
import uuid
import asyncio
import aiofiles
import anyio
from enhanced_app_routes_voice import get_segment_progress as _segment_progress
import logging
import time
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from voice_cache_manager import voice_cache_manager
from text_storage_service import text_storage_service, TextStorageError
from database import get_async_db, async_engine
from models import (
    Album,
    Track,
    User,
    TTSTrackMeta,
    AvailableVoice,
    CampaignTier,
    TTSTextSegment,
    TTSVoiceSegment,
)
from auth import login_required
from enhanced_tts_voice_service import enhanced_voice_tts_service
from storage import storage
from hls_streaming import stream_manager
from read_along_cache import clear_track_cache, clear_old_versions
from activity_logs_router import log_activity_isolated
from models import AuditLogType

logger = logging.getLogger(__name__)
router = APIRouter()

async_session = async_sessionmaker(async_engine, expire_on_commit=False)

async def aexists(p: Path) -> bool:
    return await anyio.to_thread.run_sync(p.exists)

async def aglob(p: Path, pat: str) -> list:
    return await anyio.to_thread.run_sync(lambda: list(p.glob(pat)))

async def armtree(p: Path):
    import shutil
    return await anyio.to_thread.run_sync(shutil.rmtree, p, True)

async def aread_json(p: Path) -> dict:
    async with aiofiles.open(p, 'r') as f:
        content = await f.read()
        return json.loads(content)

async def acount_glob(p: Path, pat: str) -> int:
    def work():
        return sum(1 for _ in p.glob(pat))
    return await anyio.to_thread.run_sync(work)

async def analyze_text_stats(text: str) -> dict:
    def work():
        b = text.encode("utf-8")
        wc = len(re.findall(r"\w+", text))
        return {
            "size_bytes": len(b),
            "size_mb": round(len(b) / 1024 / 1024, 2),
            "word_count": wc,
            "character_count": len(text),
        }
    return await anyio.to_thread.run_sync(work)

async def get_available_voices(db: AsyncSession) -> List[str]:
    try:
        result = await db.execute(
            select(AvailableVoice.voice_id).where(AvailableVoice.is_active == True)
        )
        return list(result.scalars().all())
    except Exception:
        return []

async def get_first_available_voice(db: AsyncSession) -> Optional[str]:
    try:
        result = await db.execute(
            select(AvailableVoice.voice_id)
            .where(AvailableVoice.is_active == True)
            .limit(1)
        )
        return result.scalar_one_or_none()
    except Exception:
        return None

async def get_voice_details(db: AsyncSession) -> Dict[str, str]:
    try:
        result = await db.execute(
            select(AvailableVoice).where(AvailableVoice.is_active == True)
        )
        voices = result.scalars().all()
        
        descriptions = {}
        for voice in voices:
            gender_text = voice.gender.title() if voice.gender else "Voice"
            descriptions[voice.voice_id] = f"{gender_text}, {voice.language_code}"
        
        return descriptions
    except Exception:
        return {}

async def get_generated_voices_for_track(track_id: str) -> List[str]:
    """
    Return the list of generated voices for a track.

    Primary source: database (distinct voice IDs from ready segments).
    Fallback: filesystem scan (legacy behaviour) only if DB returns nothing.
    """
    try:
        async for db in get_async_db():
            result = await db.execute(
                select(TTSVoiceSegment.voice_id)
                .join(TTSTextSegment, TTSTextSegment.id == TTSVoiceSegment.text_segment_id)
                .where(
                    TTSTextSegment.track_id == track_id,
                    TTSVoiceSegment.status == 'ready'
                )
                .distinct()
            )
            voices = [row[0] for row in result.all()]
            if voices:
                return voices
    except Exception as e:
        logger.error(f"[Voices] DB lookup failed for {track_id}: {e}")

    # Fallback to filesystem (slower, but keeps legacy behaviour)
    if not (stream_manager and hasattr(stream_manager, 'segment_dir')):
        return []

    track_dir = stream_manager.segment_dir / track_id
    if not await aexists(track_dir):
        return []

    voices = []
    voice_dirs = await aglob(track_dir, "voice-*")
    for voice_dir in voice_dirs:
        if await aexists(voice_dir / "master.m3u8"):
            voices.append(voice_dir.name.replace("voice-", ""))

    return voices

def check_album_access(user: User, album: Album) -> bool:
    if user.is_creator and album.created_by_id == user.id:
        return True
    if user.is_team and album.created_by_id == user.created_by:
        return True
    return False

_VOICE_LOCKS: dict[str, asyncio.Lock] = {}

def _get_voice_lock(track_id: str, voice_id: str) -> asyncio.Lock:
    key = f"{track_id}:{voice_id}"
    lock = _VOICE_LOCKS.get(key)
    if not lock:
        lock = asyncio.Lock()
        _VOICE_LOCKS[key] = lock
    return lock

async def _acquire_generation_lock_atomic(
    track_id: str,
    process_type: str,
    voice_id: str | None = None,
) -> bool:
    from status_lock import status_lock
    from database import get_async_db  # Use async version

    async with async_session() as lock_db:  # Use async context manager
        try:
            locked, reason = await status_lock.try_lock_voice(
                track_id=track_id,
                voice_id=voice_id,
                process_type=process_type,
                db=lock_db,
            )
            return bool(locked)
        except Exception as e:
            logger.error(f"Error acquiring lock: {e}")
            return False

async def _release_lock(track_id: str, success: bool, *, voice_id: str | None = None):
    from status_lock import status_lock
    from database import get_async_db  # Use async version

    async with async_session() as unlock_db:  # Use async context manager
        try:
            await status_lock.unlock_voice(track_id, voice_id, success=success, db=unlock_db)
        except Exception as e:
            logger.error(f"Error releasing lock: {e}")

_TRACK_START_LOCKS: Dict[str, asyncio.Lock] = {}

def _get_track_lock(track_id: str) -> asyncio.Lock:
    lock = _TRACK_START_LOCKS.get(track_id)
    if not lock:
        lock = asyncio.Lock()
        _TRACK_START_LOCKS[track_id] = lock
    return lock

async def check_unified_track_access(
    track_id: str,
    current_user: User,
    db: AsyncSession,
    voice_id: Optional[str] = None,
    require_voice_access: bool = False
) -> tuple[bool, Optional[str], Optional[Track], Optional[Album]]:
    try:
        result = await db.execute(
            select(Track)
            .options(selectinload(Track.album))
            .where(Track.id == track_id)
        )
        track = result.scalar_one_or_none()
        
        if not track:
            return False, "Track not found", None, None
            
        album = track.album
        if not album:
            return False, "Album not found", None, None
            
        if current_user.is_creator or current_user.is_team:
            if require_voice_access and voice_id and getattr(track, 'track_type', 'audio') == 'tts':
                voice_has_access, voice_error = await check_voice_tier_access_async(
                    current_user, voice_id, db, track_id
                )
                if not voice_has_access:
                    return False, voice_error, track, album
            return True, None, track, album
            
        restrictions = album.tier_restrictions
        track_access_granted = True
        
        if restrictions and restrictions.get("is_restricted") is True:
            tier_data = current_user.patreon_tier_data if current_user.patreon_tier_data else {}
            user_amount = tier_data.get("amount_cents", 0)
            required_amount = restrictions.get("minimum_tier_amount", 0)
            
            if (current_user.is_patreon or current_user.is_kofi or current_user.is_guest_trial) and tier_data:
                if user_amount >= required_amount:
                    track_access_granted = True
                elif current_user.is_kofi and tier_data.get('has_donations', False):
                    donation_amount = tier_data.get('donation_amount_cents', 0)
                    total_amount = user_amount + donation_amount
                    track_access_granted = total_amount >= required_amount
                else:
                    track_access_granted = False
            else:
                track_access_granted = False
                
            if not track_access_granted:
                required_tier = restrictions.get("minimum_tier", "").strip()
                tier_message = f"the {required_tier} tier or above" if required_tier else "a higher tier subscription"
                return False, f"This content requires {tier_message}", track, album
        
        if require_voice_access and voice_id and getattr(track, 'track_type', 'audio') == 'tts':
            voice_has_access, voice_error = await check_voice_tier_access_async(
                current_user, voice_id, db, track_id
            )
            if not voice_has_access:
                return False, voice_error, track, album
                
        return True, None, track, album
        
    except Exception:
        return False, "Error checking access permissions", None, None

async def check_voice_access_async(user: User, voice_id: str, db: AsyncSession, track_id: str = None) -> bool:
    try:
        result = await db.execute(
            select(AvailableVoice).where(
                AvailableVoice.voice_id == voice_id,
                AvailableVoice.is_active == True
            )
        )
        voice = result.scalar_one_or_none()
        
        if not voice:
            return False
        
        if user.is_creator or user.is_team:
            return True
        
        creator_id = user.created_by if user.created_by else user.id
        
        if track_id:
            result = await db.execute(
                select(TTSTrackMeta).where(TTSTrackMeta.track_id == track_id)
            )
            track_meta = result.scalar_one_or_none()
            
            if track_meta and track_meta.default_voice == voice_id:
                return True
        
        tier_data = user.patreon_tier_data if user.patreon_tier_data else {}
        user_amount = tier_data.get("amount_cents", 0)
        
        result = await db.execute(
            select(CampaignTier).where(
                CampaignTier.creator_id == creator_id,
                CampaignTier.is_active == True,
                CampaignTier.voice_access.contains([voice_id])
            )
        )
        all_tiers_with_voice = list(result.scalars().all())
        
        if not all_tiers_with_voice:
            return False
        
        paid_tiers = [tier for tier in all_tiers_with_voice if tier.amount_cents > 0]
        
        if paid_tiers:
            required_tier = max(paid_tiers, key=lambda t: t.amount_cents)
            required_amount = required_tier.amount_cents
        else:
            required_amount = 0
        
        if (user.is_patreon or user.is_kofi or user.is_guest_trial) and tier_data:
            if user_amount >= required_amount:
                return True
            
            if user.is_kofi and tier_data.get('has_donations', False):
                donation_amount = tier_data.get('donation_amount_cents', 0)
                total_amount = user_amount + donation_amount
                
                if total_amount >= required_amount:
                    return True
        
        return False
        
    except Exception:
        return False

async def check_voice_tier_access_async(
    user: User, voice_id: str, db: AsyncSession, track_id: str = None
) -> tuple[bool, Optional[str]]:
    if user.is_creator or user.is_team:
        return True, None
    
    has_access = await check_voice_access_async(user, voice_id, db, track_id)
    
    if has_access:
        return True, None
    
    creator_id = user.created_by or user.id
    
    result = await db.execute(
        select(CampaignTier)
        .where(
            CampaignTier.creator_id == creator_id,
            CampaignTier.is_active == True,
            CampaignTier.voice_access.contains([voice_id])
        )
        .order_by(CampaignTier.amount_cents)
    )
    min_tier = result.scalars().first()
    
    if not min_tier:
        return False, f"Voice '{voice_id}' is not available in any subscription tier"
    
    if track_id:
        result = await db.execute(
            select(TTSTrackMeta).where(TTSTrackMeta.track_id == track_id)
        )
        track_meta = result.scalar_one_or_none()
        
        if track_meta and track_meta.default_voice == voice_id:
            return False, f"Voice '{voice_id}' should be accessible as default - check track configuration"
    
    return False, f"Voice '{voice_id}' requires '{min_tier.title}' subscription"

class TTSCreateRequest(BaseModel):
    title: str
    text: str
    voice: Optional[str] = None
    bulk_split_count: Optional[int] = 1
    bulk_series_title: Optional[str] = None
    visibility_status: Optional[str] = "visible"

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
        if len(v.strip()) > 5000000:
            raise ValueError('Text must be less than 5 million characters')
        return v.strip()
    
    @validator('bulk_split_count')
    def validate_bulk_split_count(cls, v):
        if v is not None and (v < 1 or v > 20):
            raise ValueError('Split count must be between 1 and 20')
        return v

class VoiceChangeRequest(BaseModel):
    new_voice: str

class TTSCreateResponse(BaseModel):
    track_id: str
    status: str
    message: str
    text_size_mb: float
    estimated_duration_minutes: float
    total_chunks: int
    approach: str
    voice: str
    voice_directory: str
    supports_voice_switching: bool
    bulk_queue_id: Optional[str] = None
    total_tracks: Optional[int] = None
    estimated_total_duration: Optional[float] = None
    series_title: Optional[str] = None
    track: Optional[Dict] = None  # ✅ ADD THIS
    tracks: Optional[List[Dict]] = None  # ✅ ADD THIS for bulk

class TTSProgressResponse(BaseModel):
    track_id: str
    status: str
    progress: float
    approach: str = "enhanced_voice_switching"
    chunks_processed: Optional[int] = None
    total_chunks: Optional[int] = None
    current_chunk: Optional[int] = None
    current_phase: Optional[str] = None
    estimated_time_remaining: Optional[int] = None
    voice: Optional[str] = None
    available_voices: Optional[List[str]] = None
    voice_switching_ready: bool = False

class VoiceSwitchResponse(BaseModel):
    track_id: str
    old_voice: str
    new_voice: str
    status: str
    message: str
    duration: Optional[float] = None
    voice_directory: str
    processing_time: Optional[float] = None
    cached: bool = False

@router.get("/api/tracks/{track_id}/check-access")
async def check_track_access(
    track_id: str,
    current_user: User = Depends(login_required),
    db: AsyncSession = Depends(get_async_db)
):
    try:
        has_access, error_message, track, album = await check_unified_track_access(
            track_id, current_user, db
        )
        
        if not has_access:
            return JSONResponse({
                "error": {
                    "type": "access_denied",
                    "message": error_message
                }
            }, status_code=403)
            
        reason = "creator_access" if current_user.is_creator or current_user.is_team else "public_access"
        if album.tier_restrictions and album.tier_restrictions.get("is_restricted") is True:
            reason = "tier_access"
            
        return JSONResponse({
            "status": "ok",
            "has_access": True,
            "reason": reason
        })
        
    except Exception:
        raise HTTPException(status_code=500, detail="Error checking track access")

@router.get("/api/tracks/{track_id}/word-timings/{voice_id}")
async def get_track_word_timings(
    track_id: str,
    voice_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(login_required),
    raw: bool = False
):
    try:
        has_access, error_message, track, album = await check_unified_track_access(
            track_id, current_user, db, voice_id, require_voice_access=True
        )
        
        if not has_access:
            raise HTTPException(status_code=403, detail=error_message)
        
        if getattr(track, 'track_type', 'audio') != 'tts':
            raise HTTPException(status_code=400, detail="Track is not a TTS track")
        
        words = await stream_manager.get_words_for_segment_precise(track_id, voice_id, None, db)
        
        if not words:
            return {
                "track_id": track_id,
                "voice_id": voice_id,
                "word_timings": [],
                "total_words": 0,
                "duration": 0,
                "segment_duration": 30,
                "plan_b_enabled": False,
                "calibration_applied": False,
                "status": "no_timings"
            }
        
        calibration_applied = False
        calibration_data = None
        
        if not raw:
            voice_stream_dir = stream_manager.segment_dir / track_id / f"voice-{voice_id}"
            index_path = voice_stream_dir / "index.json"
            
            if await aexists(index_path):
                try:
                    index_data = await aread_json(index_path)
                    
                    calibration = index_data.get('calibration', {})
                    if calibration.get('plan_b_enabled'):
                        k_samples = calibration.get('k_samples', 1.0)
                        b_samples = calibration.get('b_samples', 0)
                        sample_rate = calibration.get('sample_rate', 48000)
                        
                        if len(words) > 100000:
                            def apply_calibration():
                                calibrated = []
                                for word in words:
                                    w = word.copy()
                                    start_samples = word['start_time'] * sample_rate
                                    end_samples = word['end_time'] * sample_rate
                                    calibrated_start = k_samples * start_samples + b_samples
                                    calibrated_end = k_samples * end_samples + b_samples
                                    w.update({
                                        'start_time_original': word['start_time'],
                                        'end_time_original': word['end_time'],
                                        'start_time': max(0, calibrated_start / sample_rate),
                                        'end_time': max(0, calibrated_end / sample_rate),
                                        'calibration_applied': True
                                    })
                                    calibrated.append(w)
                                return calibrated
                            words = await anyio.to_thread.run_sync(apply_calibration)
                        else:
                            calibrated_words = []
                            for word in words:
                                calibrated_word = word.copy()
                                
                                start_samples = word['start_time'] * sample_rate
                                end_samples = word['end_time'] * sample_rate
                                
                                calibrated_start_samples = k_samples * start_samples + b_samples
                                calibrated_end_samples = k_samples * end_samples + b_samples
                                
                                calibrated_word.update({
                                    'start_time_original': word['start_time'],
                                    'end_time_original': word['end_time'],
                                    'start_time': max(0, calibrated_start_samples / sample_rate),
                                    'end_time': max(0, calibrated_end_samples / sample_rate),
                                    'calibration_applied': True
                                })
                                
                                calibrated_words.append(calibrated_word)
                            
                            words = calibrated_words
                        
                        calibration_applied = True
                        calibration_data = {
                            "k_samples": k_samples,
                            "b_samples": b_samples,
                            "sample_rate": sample_rate,
                            "priming_offset_ms": (b_samples / sample_rate) * 1000
                        }
                        
                except Exception:
                    pass
        
        max_end_time = max(word.get('end_time', 0) for word in words)
        
        response = {
            "track_id": track_id,
            "voice_id": voice_id,
            "word_timings": words,
            "total_words": len(words),
            "duration": max_end_time,
            "segment_duration": 30,
            "plan_b_enabled": calibration_applied,
            "calibration_applied": calibration_applied,
            "status": "success"
        }
        
        if calibration_data:
            response["calibration"] = calibration_data
            
        return response
        
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to get word timings")

@router.get("/api/tracks/{track_id}/word-at-time")
async def find_word_at_time(
    track_id: str,
    time: float,
    voice_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(login_required),
    raw: bool = False
):
    try:
        has_access, error_message, track, album = await check_unified_track_access(
            track_id, current_user, db, voice_id, require_voice_access=True
        )
        
        if not has_access:
            raise HTTPException(status_code=403, detail=error_message)
        
        timings_response = await get_track_word_timings(track_id, voice_id, db, current_user, raw=raw)
        words = timings_response.get('word_timings', [])
        
        if not words:
            return {
                "track_id": track_id,
                "voice_id": voice_id,
                "time": time,
                "word_index": -1,
                "plan_b_enabled": False,
                "calibration_applied": False,
                "status": "no_timings"
            }
        
        search_time = time
        calibration_info = {}
        
        if not raw and timings_response.get('calibration_applied'):
            calibration = timings_response.get('calibration', {})
            if calibration:
                k_samples = calibration.get('k_samples', 1.0)
                b_samples = calibration.get('b_samples', 0)
                sample_rate = calibration.get('sample_rate', 48000)
                
                search_time_samples = time * sample_rate
                calibrated_time_samples = k_samples * search_time_samples + b_samples
                search_time = max(0, calibrated_time_samples / sample_rate)
                
                calibration_info = {
                    "original_search_time": time,
                    "calibrated_search_time": search_time,
                    "calibration": calibration
                }
        
        for i, word in enumerate(words):
            if word['start_time'] <= search_time < word['end_time']:
                response = {
                    "track_id": track_id,
                    "voice_id": voice_id,
                    "time": time,
                    "search_time": search_time,
                    "word_index": i,
                    "word": word['word'],
                    "word_timing": word,
                    "plan_b_enabled": timings_response.get('plan_b_enabled', False),
                    "calibration_applied": timings_response.get('calibration_applied', False),
                    "status": "found"
                }
                
                if calibration_info:
                    response.update(calibration_info)
                
                return response
        
        if words:
            closest_index = min(
                range(len(words)),
                key=lambda i: min(
                    abs(search_time - words[i]['start_time']),
                    abs(search_time - words[i]['end_time'])
                )
            )
            
            response = {
                "track_id": track_id,
                "voice_id": voice_id,
                "time": time,
                "search_time": search_time,
                "word_index": closest_index,
                "word": words[closest_index]['word'],
                "word_timing": words[closest_index],
                "plan_b_enabled": timings_response.get('plan_b_enabled', False),
                "calibration_applied": timings_response.get('calibration_applied', False),
                "status": "closest"
            }
            
            if calibration_info:
                response.update(calibration_info)
            
            return response
        
        return {
            "track_id": track_id,
            "voice_id": voice_id,
            "time": time,
            "word_index": -1,
            "plan_b_enabled": timings_response.get('plan_b_enabled', False),
            "status": "not_found"
        }
        
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to find word at time")

@router.get("/api/tracks/{track_id}/time-for-word")
async def get_time_for_word(
    track_id: str,
    word_index: int,
    voice_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(login_required),
    raw: bool = False
):
    try:
        has_access, error_message, track, album = await check_unified_track_access(
            track_id, current_user, db, voice_id, require_voice_access=True
        )
        
        if not has_access:
            raise HTTPException(status_code=403, detail=error_message)
        
        timings_response = await get_track_word_timings(track_id, voice_id, db, current_user, raw=raw)
        words = timings_response.get('word_timings', [])
        
        if not words:
            return {
                "track_id": track_id,
                "voice_id": voice_id,
                "word_index": word_index,
                "time": None,
                "plan_b_enabled": False,
                "calibration_applied": False,
                "status": "no_timings"
            }
        
        if 0 <= word_index < len(words):
            word = words[word_index]
            
            response = {
                "track_id": track_id,
                "voice_id": voice_id,
                "word_index": word_index,
                "time": word['start_time'],
                "word": word['word'],
                "word_timing": word,
                "plan_b_enabled": timings_response.get('plan_b_enabled', False),
                "calibration_applied": timings_response.get('calibration_applied', False),
                "status": "found"
            }
            
            if word.get('start_time_original') is not None:
                response["time_original"] = word['start_time_original']
                response["priming_offset_ms"] = (response["time"] - response["time_original"]) * 1000
            
            if timings_response.get('calibration'):
                response["calibration"] = timings_response['calibration']
            
            return response
        
        return {
            "track_id": track_id,
            "voice_id": voice_id,
            "word_index": word_index,
            "time": None,
            "plan_b_enabled": timings_response.get('plan_b_enabled', False),
            "status": "invalid_index"
        }
        
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to get time for word")

@router.get("/api/tracks/{track_id}/tts-progress/{voice_id}")
async def get_tts_voice_progress(
    track_id: str,
    voice_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(login_required)
):
    try:
        has_access, error_message, track, album = await check_unified_track_access(
            track_id, current_user, db, voice_id, require_voice_access=True
        )
        
        if not has_access:
            raise HTTPException(status_code=403, detail=error_message)
        
        lock_key = f"{track_id}:{voice_id}"
        
        if lock_key in enhanced_voice_tts_service.voice_switch_progress:
            progress_data = enhanced_voice_tts_service.voice_switch_progress[lock_key]
            
            status = progress_data.get('status', 'processing')
            progress = min(100, max(0, progress_data.get('progress', 0)))
            phase = progress_data.get('phase', 'initializing')
            message = progress_data.get('message', 'Processing...')
            
            chunks_completed = progress_data.get('chunks_completed', 0)
            total_chunks = progress_data.get('total_chunks', 0)
            
            response = {
                "track_id": track_id,
                "voice_id": voice_id,
                "status": status,
                "progress": progress,
                "phase": phase,
                "message": message,
                "chunks_completed": chunks_completed,
                "total_chunks": total_chunks
            }
            
            if status == 'complete' and progress >= 99:
                response['status'] = 'segmentation_ready'
                response['message'] = 'TTS complete, preparing segments...'
            
            return response
        
        voice_stream_dir = stream_manager.hls_manager.segment_dir / track_id / f"voice-{voice_id}"
        if await aexists(voice_stream_dir) and await aexists(voice_stream_dir / "master.m3u8"):
            return {
                "track_id": track_id,
                "voice_id": voice_id,
                "status": "complete",
                "progress": 100,
                "phase": "complete",
                "message": "Voice generation complete",
                "chunks_completed": 0,
                "total_chunks": 0
            }
        
        return {
            "track_id": track_id,
            "voice_id": voice_id,
            "status": "not_found",
            "progress": 0,
            "phase": "unknown",
            "message": "No TTS generation in progress",
            "chunks_completed": 0,
            "total_chunks": 0
        }
        
    except HTTPException:
        raise
    except Exception:
        return {
            "track_id": track_id,
            "voice_id": voice_id,
            "status": "error",
            "progress": 0,
            "phase": "error",
            "message": "Error getting progress",
            "chunks_completed": 0,
            "total_chunks": 0
        }

@router.get("/api/voices/available")
async def get_available_voices_simplified(
    track_id: Optional[str] = None,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(login_required)
):
    try:
        result = await db.execute(
            select(AvailableVoice)
            .where(AvailableVoice.is_active == True)
            .order_by(AvailableVoice.id)
        )
        voices = result.scalars().all()
        
        creator_id = current_user.id if current_user.is_creator else current_user.created_by
        
        track_default_voice = None
        if track_id:
            result = await db.execute(
                select(TTSTrackMeta).where(TTSTrackMeta.track_id == track_id)
            )
            track_meta = result.scalar_one_or_none()
            
            if track_meta and track_meta.default_voice:
                track_default_voice = track_meta.default_voice
        
        if not track_default_voice:
            track_default_voice = voices[0].voice_id if voices else None
        
        result = await db.execute(
            select(CampaignTier).where(
                CampaignTier.creator_id == creator_id,
                CampaignTier.is_active == True
            )
        )
        tiers = list(result.scalars().all())
        
        voice_list = []
        
        for voice in voices:
            has_access = await check_voice_access_async(current_user, voice.voice_id, db, track_id)
            
            tiers_with_voice = [tier for tier in tiers if voice.voice_id in (tier.voice_access or [])]
            
            if track_id and voice.voice_id == track_default_voice:
                tier_label = "Default/Free"
                tier_class = "free"
                is_restricted = False
                tier_amount = 0
            else:
                paid_tiers_with_voice = [tier for tier in tiers_with_voice if tier.amount_cents > 0]
                
                if paid_tiers_with_voice:
                    restriction_tier = max(paid_tiers_with_voice, key=lambda t: t.amount_cents)
                    tier_label = f"{restriction_tier.title}"
                    tier_class = "restricted" if not has_access else "accessible"
                    is_restricted = not has_access
                    tier_amount = restriction_tier.amount_cents
                elif tiers_with_voice:
                    free_tier = min(tiers_with_voice, key=lambda t: t.amount_cents)
                    tier_label = f"{free_tier.title}" if free_tier.amount_cents == 0 else "Free"
                    tier_class = "free"
                    is_restricted = False
                    tier_amount = 0
                else:
                    tier_label = "Tier Unassigned"
                    tier_class = "not-assigned"
                    is_restricted = True
                    tier_amount = 0
            
            voice_info = {
                "voice_id": voice.voice_id,
                "display_name": voice.display_name,
                "language_code": voice.language_code,
                "gender": voice.gender,
                "has_access": has_access,
                "is_restricted": is_restricted,
                "assigned_tiers": [{"tier_title": tier.title, "tier_amount": tier.amount_cents} for tier in tiers_with_voice],
                "tier_label": tier_label,
                "tier_class": tier_class,
                "tier_amount": tier_amount,
                "amount_cents": tier_amount,
                "is_default": track_id and voice.voice_id == track_default_voice,
                "description": f"{voice.gender.title() if voice.gender else 'Voice'}, {voice.language_code}",
                "default_override": track_id and voice.voice_id == track_default_voice and has_access
            }
            voice_list.append(voice_info)
        
        accessible_voices = [v for v in voice_list if v["has_access"]]
        restricted_voices = [v for v in voice_list if not v["has_access"]]
        
        return {
            "voices": voice_list,
            "accessible_voices": accessible_voices,
            "restricted_voices": restricted_voices,
            "default_voice": track_default_voice,
            "total_count": len(voice_list),
            "accessible_count": len(accessible_voices),
            "restricted_count": len(restricted_voices),
            "track_specific_access": track_id is not None,
            "user_access_level": {
                "is_creator": current_user.is_creator,
                "is_team": current_user.is_team,
                "is_patron": current_user.is_patreon,
                "is_kofi": current_user.is_kofi,
                "is_guest_trial": current_user.is_guest_trial,
                "creator_id": creator_id,
                "user_amount_cents": (current_user.patreon_tier_data or {}).get("amount_cents", 0)
            }
        }
        
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to get available voices")

@router.post("/api/albums/{album_id}/tracks/create-tts", response_model=TTSCreateResponse)
async def create_enhanced_voice_tts_track(
    album_id: str,
    request: TTSCreateRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(login_required)
):
    try:
        result = await db.execute(
            select(Album)
            .options(selectinload(Album.tracks))
            .where(Album.id == album_id)
        )
        album = result.scalar_one_or_none()
        
        if not album:
            raise HTTPException(status_code=404, detail="Album not found")

        if not check_album_access(current_user, album):
            raise HTTPException(status_code=403, detail="Permission denied")

        # Validate visibility_status based on user role
        visibility_status = request.visibility_status or "visible"
        valid_statuses = ["visible", "hidden_from_users", "hidden_from_all"]
        if visibility_status not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid visibility_status. Must be one of: {', '.join(valid_statuses)}")

        # Team members cannot hide from team or all - only from users
        if current_user.is_team and not current_user.is_creator:
            if visibility_status == "hidden_from_all":
                raise HTTPException(status_code=403, detail="Team members cannot hide content from team. Only 'visible' or 'hidden_from_users' allowed.")

        available_voices = await get_available_voices(db)
        if not available_voices:
            raise HTTPException(status_code=500, detail="No voices available in database")
        
        if request.voice and request.voice in available_voices:
            voice_to_use = request.voice
        else:
            voice_to_use = available_voices[0]
        
        track_id = str(uuid.uuid4())
        
        bulk_split_count = getattr(request, 'bulk_split_count', 1)
        bulk_series_title = getattr(request, 'bulk_series_title', None)
        
        # Calculate next order ONCE for both single and bulk
        try:
            result = await db.execute(
                select(func.max(Track.order))
                .where(Track.album_id == album_id)
            )
            max_order = result.scalar()
            next_order = (max_order + 1) if max_order is not None else 0
        except Exception:
            raise HTTPException(status_code=500, detail="Database error")
        
        if bulk_split_count > 1:
            # ✅ BULK GENERATION - Call service directly
            if not bulk_series_title or not bulk_series_title.strip():
                raise HTTPException(status_code=400, detail="Series title required for bulk generation")
            
            if len(request.text) < 10000:
                raise HTTPException(status_code=400, detail="Text too short for bulk generation (minimum 10k characters)")
            
            try:
                # ✅ This creates tracks, stores text, queues processing, and returns data
                result = await enhanced_voice_tts_service.create_tts_track_with_voice(
                    track_id=track_id,
                    title=request.title,
                    text_content=request.text,
                    voice=voice_to_use,
                    db=db,
                    user=current_user,
                    album_id=album_id,
                    bulk_split_count=bulk_split_count,
                    bulk_series_title=bulk_series_title,
                    starting_order=next_order,
                    visibility_status=request.visibility_status or "visible"
                )
                
                # ✅ Extract data from result
                tracks_data = result.get("tracks", [])
                bulk_queue_id = result.get("bulk_queue_id")

                stats = await analyze_text_stats(request.text)
                estimated_duration = stats["word_count"] / 150 * 60

                # Log activity - Bulk TTS generation started (isolated session)
                try:
                    await log_activity_isolated(
                        user_id=current_user.id,
                        action_type=AuditLogType.CREATE,
                        table_name='tracks',
                        record_id=bulk_queue_id,
                        description=f"Started bulk TTS generation: '{bulk_series_title}' ({bulk_split_count} tracks, voice_id: {voice_to_use})"
                    )
                except Exception as log_err:
                    logger.warning(f"Failed to log bulk TTS creation activity: {log_err}")

                return TTSCreateResponse(
                    track_id=bulk_queue_id,
                    status="bulk_queued",
                    message=f"Bulk generation queued: {bulk_split_count} tracks",
                    text_size_mb=stats["size_mb"],
                    estimated_duration_minutes=round(estimated_duration / 60, 1),
                    total_chunks=bulk_split_count,
                    approach="bulk_generation",
                    voice=voice_to_use,
                    voice_directory=f"bulk-{bulk_queue_id}",
                    supports_voice_switching=False,
                    bulk_queue_id=bulk_queue_id,
                    total_tracks=bulk_split_count,
                    estimated_total_duration=estimated_duration,
                    series_title=bulk_series_title,
                    tracks=tracks_data  # ✅ HAS REAL DATA
                )
                
            except Exception as e:
                logger.error(f"Bulk TTS creation failed: {e}")
                raise HTTPException(status_code=500, detail=f"Bulk TTS creation failed: {str(e)}")
        
        else:
            # ✅ SINGLE TRACK CREATION (keep existing code - it works!)
            word_count = len(request.text.split())
            estimated_duration = (word_count / 150) * 60
            estimated_chunks = max(1, len(request.text) // 8000)
            
            text_metadata = {
                'size_bytes': len(request.text.encode('utf-8')),
                'size_mb': round(len(request.text.encode('utf-8')) / 1024 / 1024, 2),
                'word_count': word_count,
                'character_count': len(request.text),
                'estimated_chunks': estimated_chunks,
                'estimated_duration': estimated_duration,
                'approach': 'enhanced_voice_switching',
                'voice': voice_to_use,
                'voice_directory': f"voice-{voice_to_use}",
                'supports_voice_switching': True,
                'available_voices': [voice_to_use],
                'is_bulk_part': False,
                'segment_index': next_order,
                'creation_order': 'single_tts',
            }
            
            try:
                new_track = Track(
                    id=track_id,
                    title=request.title,
                    file_path=f"/tts/{track_id}/voice-{voice_to_use}/complete.mp3",
                    album_id=album_id,
                    created_by_id=current_user.id,
                    upload_status='processing',
                    track_type='tts',
                    source_text=request.text[:1000],
                    default_voice=voice_to_use,
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
                    visibility_status=request.visibility_status or "visible",
                    tier_requirements={
                        "is_public": True,
                        "minimum_cents": 0,
                        "allowed_tier_ids": []
                    },
                    access_count=0,
                    order=next_order
                )
                
                db.add(new_track)
                await db.commit()
                await db.refresh(new_track)
                
                if not await _acquire_generation_lock_atomic(track_id, 'tts_initial', voice_id=voice_to_use):
                    raise HTTPException(status_code=409, detail="Track processing conflict")
                
            except HTTPException:
                raise
            except Exception as db_error:
                await db.rollback()
                raise HTTPException(status_code=500, detail=f"Database error: {str(db_error)}")
            
            try:
                background_tasks.add_task(
                    process_enhanced_voice_tts_track,
                    track_id=track_id,
                    title=request.title,
                    text_content=request.text,
                    voice=voice_to_use,
                    user_id=current_user.id,
                    lock_already_held=True
                )

                # Log activity - TTS generation started (isolated session)
                await log_activity_isolated(
                    user_id=current_user.id,
                    action_type=AuditLogType.CREATE,
                    table_name='tracks',
                    record_id=track_id,
                    description=f"Started TTS generation for '{request.title}' (TTS, voice_id: {voice_to_use})"
                )

                init_lock_key = f"{track_id}:{voice_to_use}"
                # Database tracks generation status via voice_generation_status table
                # No need for Redis lock - already marked in-flight by enforce_voice_limit()

                enhanced_voice_tts_service.voice_switch_progress[init_lock_key] = {
                    'status': 'initializing',
                    'progress': 0,
                    'phase': 'starting',
                    'message': 'Preparing voice generation...',
                    'start_time': time.time(),
                    'user_id': current_user.id,
                    'chunks_completed': 0,
                    'total_chunks': 0
                }
                
                try:
                    enhanced_voice_tts_service.try_start_generation_atomic(
                        current_user, track_id, voice_to_use
                    )
                except Exception:
                    pass
                
            except Exception:
                try:
                    new_track.tts_status = 'error'
                    new_track.upload_status = 'error'
                    await db.commit()
                except Exception:
                    pass
                
                await _release_lock(track_id, success=False)
                    
                raise HTTPException(status_code=500, detail="Failed to queue TTS")
            
            # ✅ BUILD TRACK DATA FOR RESPONSE
            track_data = {
                "id": new_track.id,
                "title": new_track.title,
                "duration": new_track.duration or 0,
                "tts_status": new_track.tts_status,
                "upload_status": new_track.upload_status,
                "status": "processing",
                "track_type": new_track.track_type,
                "has_read_along": new_track.has_read_along,
                "default_voice": new_track.default_voice,
                "order": new_track.order,
                "file_path": new_track.file_path,
                "is_tts_track": True,
                "source_text": "",
            }
            
            return TTSCreateResponse(
                track_id=track_id,
                status="queued",
                message=f"TTS track creation queued",
                text_size_mb=text_metadata['size_mb'],
                estimated_duration_minutes=round(estimated_duration / 60, 1),
                total_chunks=estimated_chunks,
                approach="enhanced_voice_switching",
                voice=voice_to_use,
                voice_directory=f"voice-{voice_to_use}",
                supports_voice_switching=True,
                track=track_data  # ✅ TRACK DATA HERE
            )
        
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create TTS track: {str(e)}")

@router.post("/api/tracks/{track_id}/voice/switch", response_model=VoiceSwitchResponse)
async def switch_voice_simplified(
    track_id: str,
    request: VoiceChangeRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(login_required)
):
    new_voice = request.new_voice
    lock_key = f"{track_id}:{new_voice}"
    voice_guard = _get_voice_lock(track_id, new_voice)
    
    async with voice_guard:
        async with async_session() as db:
            has_access, error_message, track, album = await check_unified_track_access(
                track_id, current_user, db, new_voice, require_voice_access=True
            )
            if not has_access:
                raise HTTPException(status_code=403, detail=error_message)
            if getattr(track, 'track_type', 'audio') != 'tts':
                raise HTTPException(status_code=400, detail="Track is not a TTS track")
            
            available_voices = await get_available_voices(db)
            if new_voice not in available_voices:
                raise HTTPException(status_code=400, detail=f"Voice '{new_voice}' is not available")
            
            old_voice = getattr(track, 'default_voice', None) or await get_first_available_voice(db)
            
            generated_voices = await get_generated_voices_for_track(track_id)
            if new_voice in generated_voices:
                from duration_manager import duration_manager
                try:
                    voice_duration = await duration_manager.get_voice_duration(track_id, new_voice, db)
                    if voice_duration <= 0:
                        voice_duration = await duration_manager.get_duration(track_id, db)
                except Exception:
                    voice_duration = await duration_manager.get_duration(track_id, db)
                return VoiceSwitchResponse(
                    track_id=track_id, old_voice=old_voice, new_voice=new_voice,
                    status="success",
                    message=f"Switched to {new_voice} (cached)",
                    voice_directory=f"voice-{new_voice}",
                    duration=voice_duration,
                    cached=True
                )
            
            from enhanced_tts_voice_service import enhanced_voice_tts_service
            from voice_cache_manager import voice_cache_manager

            # Check database if voice is already generating (DB-driven lock check)
            is_generating = await voice_cache_manager.is_voice_generating(track_id, new_voice, db)
            if is_generating:
                prog = enhanced_voice_tts_service.voice_switch_progress.get(lock_key, {})
                return VoiceSwitchResponse(
                    track_id=track_id, old_voice=old_voice, new_voice=new_voice,
                    status="processing",
                    message=prog.get("message", "Processing voice..."),
                    voice_directory=f"voice-{new_voice}",
                    processing_time=(time.time() - prog.get("start_time", time.time())),
                    cached=False
                )

            # USE DATABASE-BASED VOICE LIMIT ENFORCEMENT
            creator_id = track.created_by_id

            # Check voice limits using database-based tracking
            can_proceed, error_msg = await voice_cache_manager.enforce_voice_limit(
                track_id, new_voice, creator_id, db
            )

            if not can_proceed:
                # Get current counts for detailed error response
                cached_voices = await voice_cache_manager.get_cached_voices(track_id, db)
                inflight_count = await voice_cache_manager.get_inflight_voice_count(track_id, db)
                is_popular = await voice_cache_manager.is_track_popular(track_id, creator_id, db)
                max_voices = voice_cache_manager.max_voices_popular if is_popular else voice_cache_manager.max_voices_regular

                def pretty(v): return v.replace('en-US-','').replace('en-GB-','').replace('Neural','')
                return JSONResponse(
                    status_code=429,
                    content={
                        "status": "limit_reached",
                        "message": error_msg or f"Voice limit reached.",
                        "limit": max_voices,
                        "cached_count": len(cached_voices),
                        "cached": sorted(pretty(v['voice_id']) for v in cached_voices),
                        "in_flight_count": inflight_count,
                        "requested": pretty(new_voice),
                        "can_start_more": False
                    }
                )
            
            can_start, concurrency_error = enhanced_voice_tts_service.try_start_generation_atomic(
                current_user, track_id, new_voice
            )
            if not can_start:
                return JSONResponse(
                    status_code=429,
                    content={
                        "status": "concurrency_limited",
                        "message": concurrency_error or "Concurrency limit reached.",
                        "requested": new_voice
                    }
                )
            
            if not await _acquire_generation_lock_atomic(track_id, 'voice_switch', voice_id=new_voice):
                prog = enhanced_voice_tts_service.voice_switch_progress.get(lock_key, {})
                return VoiceSwitchResponse(
                    track_id=track_id, old_voice=old_voice, new_voice=new_voice,
                    status="processing",
                    message=prog.get("message", "Processing voice..."),
                    voice_directory=f"voice-{new_voice}",
                    processing_time=(time.time() - prog.get("start_time", time.time())),
                    cached=False
                )

            # Database tracks generation status via voice_generation_status table
            # No need for Redis lock - already marked in-flight by enforce_voice_limit()

            enhanced_voice_tts_service.voice_switch_progress[lock_key] = {
                'status': 'initializing',
                'progress': 0,
                'phase': 'starting',
                'message': 'Preparing voice generation...',
                'start_time': time.time(),
                'user_id': current_user.id,
                'chunks_completed': 0,
                'total_chunks': 0
            }
    
    background_tasks.add_task(
        process_voice_switch_with_cleanup,
        track_id=track_id,
        old_voice=old_voice,
        new_voice=new_voice,
        user_id=current_user.id,
        lock_key=lock_key,
        user=current_user,
        lock_already_held=True
    )
    
    voiceName = new_voice.replace('en-US-','').replace('en-GB-','').replace('Neural','')
    return VoiceSwitchResponse(
        track_id=track_id, old_voice=old_voice, new_voice=new_voice,
        status="processing",
        message=f"Generating voice {voiceName}...",
        voice_directory=f"voice-{new_voice}",
        cached=False
    )

@router.get("/api/tracks/{track_id}/voices")
async def get_track_voices_simplified(
    track_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(login_required)
):
    try:
        has_access, error_message, track, album = await check_unified_track_access(
            track_id, current_user, db
        )
        
        if not has_access:
            raise HTTPException(status_code=403, detail=error_message)
        
        if getattr(track, 'track_type', 'audio') != 'tts':
            raise HTTPException(status_code=400, detail="Track is not a TTS track")
        
        available_voices = await get_available_voices(db)
        voice_details = await get_voice_details(db)
        
        default_voice = getattr(track, 'default_voice', None)
        if not default_voice:
            default_voice = await get_first_available_voice(db)
            
        generated_voices = await get_generated_voices_for_track(track_id)
        
        voice_access_info = {}
        accessible_voices = []
        
        for voice in available_voices:
            voice_has_access, voice_error = await check_voice_tier_access_async(current_user, voice, db, track_id)
            voice_access_info[voice] = {
                "has_usage_access": voice_has_access,
                "access_reason": "Available" if voice_has_access else voice_error
            }
            
            if voice_has_access:
                accessible_voices.append(voice)
        
        return {
            "track_id": track_id,
            "default_voice": default_voice,
            "generated_voices": generated_voices,
            "all_possible_voices": available_voices,
            "accessible_voices": accessible_voices,
            "voice_details": voice_details,
            "voice_access_info": voice_access_info,
            "can_add_voices": bool(getattr(track, 'source_text', None)) and len(accessible_voices) > 0,
            "can_switch_voices": len(accessible_voices) > 0,
            "has_text_chunks": bool(getattr(track, 'source_text', None)),
            "supports_voice_switching": True,
            "voice_directories": {
                voice: f"voice-{voice}" for voice in generated_voices
            },
            "approach": "unified_access_control"
        }
        
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to get track voices")

@router.get("/api/tracks/{track_id}/voice-status/{voice_id}")
async def get_voice_processing_status(
    track_id: str,
    voice_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(login_required)
):
    try:
        has_access, error_message, track, album = await check_unified_track_access(
            track_id, current_user, db, voice_id, require_voice_access=True
        )
        
        if not has_access:
            raise HTTPException(status_code=403, detail=error_message)
        
        if getattr(track, 'track_type', 'audio') != 'tts':
            raise HTTPException(status_code=400, detail="Track is not a TTS track")
        
        generated_voices = await get_generated_voices_for_track(track_id)
        voice_ready = voice_id in generated_voices
        
        if voice_ready:
            return {
                "track_id": track_id,
                "voice_id": voice_id,
                "status": "ready",
                "voice_ready": True,
                "progress": 100,
                "message": "Voice is ready"
            }
        
        lock_key = f"{track_id}:{voice_id}"
        is_processing = False
        progress_info = {}
        
        try:
            # Check database if voice is generating (DB-driven lock check)
            is_processing = await voice_cache_manager.is_voice_generating(track_id, voice_id, db)
            progress_info = enhanced_voice_tts_service.voice_switch_progress.get(lock_key, {})
        except Exception:
            pass
        
        if is_processing:
            return {
                "track_id": track_id,
                "voice_id": voice_id,
                "status": "processing",
                "voice_ready": False,
                "progress": progress_info.get('progress', 0),
                "phase": progress_info.get('phase', 'processing'),
                "message": progress_info.get('message', 'Processing voice...')
            }
        else:
            return {
                "track_id": track_id,
                "voice_id": voice_id,
                "status": "not_started",
                "voice_ready": False,
                "progress": 0,
                "message": "Voice not available"
            }
            
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to get voice status")

@router.get("/api/tts/progress/{track_id}")
async def get_tts_progress(
    track_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(login_required)
):
    """
    Progress endpoint for album icon status.
    DB-DEPENDENT VERSION: Returns status based solely on database fields.
    Status flow: processing -> complete
    """
    try:
        # Access check
        has_access, error_message, track, album = await check_unified_track_access(
            track_id, current_user, db
        )
        if not has_access:
            raise HTTPException(status_code=403, detail=error_message)

        # Only for TTS tracks
        track_type = getattr(track, 'track_type', 'audio')
        if track_type != 'tts':
            return {
                "track_id": track_id,
                "status": "not_applicable",
                "progress": 100,
                "message": "Not a TTS track"
            }

        # Get current voice
        current_voice = getattr(track, 'default_voice', None)
        if not current_voice:
            # If no default voice, we can't determine progress, assume complete if track is
            current_voice = "default"

        # Check in-memory progress first (for actively generating tracks)
        lock_key = f"{track_id}:{current_voice}"
        if lock_key in enhanced_voice_tts_service.voice_switch_progress:
            progress_data = enhanced_voice_tts_service.voice_switch_progress[lock_key]
            status = progress_data.get('status', 'processing')
            progress = progress_data.get('progress', 0)

            # If in-memory progress says it's done, trust it
            if status == 'complete' or progress >= 100:
                return {
                    "track_id": track_id,
                    "status": "complete",
                    "progress": 100,
                    "message": "Voice ready"
                }
            else:
                # Still generating
                return {
                    "track_id": track_id,
                    "status": "processing",
                    "progress": progress,
                    "message": "Processing..."
                }

        # --- START OF THE CHANGE ---
        # REMOVED THE FILESYSTEM CHECK. WE NOW TRUST THE DATABASE.
        
        # Check database status fields directly
        tts_status = getattr(track, 'tts_status', None)
        upload_status = getattr(track, 'upload_status', None)
        track_status = getattr(track, 'status', None)

        # Generation complete (all relevant fields are 'complete' or 'ready')
        if (tts_status == 'ready' and upload_status == 'complete') or track_status == 'complete':
            return {
                "track_id": track_id,
                "status": "complete",
                "progress": 100,
                "message": "Voice ready"
            }

        # Processing states
        elif tts_status == 'processing' or upload_status == 'processing' or track_status == 'generating':
            return {
                "track_id": track_id,
                "status": "processing",
                "progress": 50,
                "message": "Processing..."
            }

        # Error states
        elif tts_status == 'error' or upload_status == 'error' or track_status == 'failed':
            return {
                "track_id": track_id,
                "status": "error",
                "progress": 0,
                "message": getattr(track, 'processing_error', 'Processing failed')
            }

        # Default/unknown state (e.g., 'pending', 'queued')
        else:
            return {
                "track_id": track_id,
                "status": "processing",
                "progress": 25,
                "message": "Starting..."
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting TTS progress: {e}")
        return {
            "track_id": track_id,
            "status": "error",
            "progress": 0,
            "message": "Error checking progress"
        }

async def group_words_into_sentences(word_timings: List[Dict]) -> List[Dict]:
    if not word_timings:
        return []
    
    if len(word_timings) > 10000:
        return await anyio.to_thread.run_sync(lambda: _group_words_sync(word_timings))
    else:
        return _group_words_sync(word_timings)

def _group_words_sync(word_timings: List[Dict]) -> List[Dict]:
    sentences = []
    current_sentence = {
        "words": [],
        "text": "",
        "start_time": None,
        "end_time": None,
        "word_count": 0,
        "sentence_index": 0
    }
    
    sentence_enders = {'.', '!', '?', '...', '…'}
    strong_breaks = {'\n\n', '\n', '--', '—'}
    
    for i, word in enumerate(word_timings):
        word_text = word.get('word', '').strip()
        if not word_text:
            continue
        
        if current_sentence["start_time"] is None:
            current_sentence["start_time"] = word.get('start_time', 0)
        
        current_sentence["words"].append(word)
        
        if current_sentence["text"]:
            current_sentence["text"] += " " + word_text
        else:
            current_sentence["text"] = word_text
        
        current_sentence["word_count"] += 1
        current_sentence["end_time"] = word.get('end_time', word.get('start_time', 0))
        
        should_break = False
        
        if any(ender in word_text for ender in sentence_enders):
            should_break = True
        elif any(breaker in word_text for breaker in strong_breaks):
            should_break = True
        elif current_sentence["word_count"] > 25:
            if any(pause in word_text for pause in {',', ';', ':', '--', '—'}):
                should_break = True
        elif current_sentence["word_count"] > 35:
            should_break = True
        
        if i == len(word_timings) - 1:
            should_break = True
        
        if should_break and current_sentence["words"]:
            sentence_text = current_sentence["text"].strip()
            
            sentences.append({
                "sentence_index": len(sentences),
                "text": sentence_text,
                "start_time": current_sentence["start_time"],
                "end_time": current_sentence["end_time"],
                "duration": current_sentence["end_time"] - current_sentence["start_time"],
                "word_count": current_sentence["word_count"],
                "words": current_sentence["words"].copy(),
                "first_word_index": current_sentence["words"][0].get('word_index', 0) if current_sentence["words"] else 0,
                "last_word_index": current_sentence["words"][-1].get('word_index', 0) if current_sentence["words"] else 0
            })
            
            current_sentence = {
                "words": [],
                "text": "",
                "start_time": None,
                "end_time": None,
                "word_count": 0,
                "sentence_index": len(sentences)
            }
    
    return sentences

@router.get("/api/tracks/{track_id}/sentence-timings/{voice_id}")
async def get_track_sentence_timings(
    track_id: str,
    voice_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(login_required)
):
    try:
        has_access, error_message, track, album = await check_unified_track_access(
            track_id, current_user, db, voice_id, require_voice_access=True
        )
        
        if not has_access:
            raise HTTPException(status_code=403, detail=error_message)
        
        if getattr(track, 'track_type', 'audio') != 'tts':
            raise HTTPException(status_code=400, detail="Track is not a TTS track")
        
        try:
            words = await stream_manager.get_words_for_segment_precise(track_id, voice_id, None, db)
            
            if not words:
                return {
                    "track_id": track_id,
                    "voice_id": voice_id,
                    "sentence_timings": [],
                    "total_sentences": 0,
                    "total_words": 0,
                    "duration": 0,
                    "status": "no_timings"
                }
            
            for i, word in enumerate(words):
                word['word_index'] = i
            
            sentences = await group_words_into_sentences(words)
            
            max_end_time = max(sentence['end_time'] for sentence in sentences) if sentences else 0
            
            return {
                "track_id": track_id,
                "voice_id": voice_id,
                "sentence_timings": sentences,
                "total_sentences": len(sentences),
                "total_words": len(words),
                "duration": max_end_time,
                "average_sentence_duration": sum(s['duration'] for s in sentences) / len(sentences) if sentences else 0,
                "status": "success"
            }
            
        except Exception:
            raise HTTPException(status_code=500, detail="Failed to get sentence timings")
        
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to get sentence timings")

@router.get("/api/tracks/{track_id}/sentence-at-time")
async def find_sentence_at_time(
    track_id: str,
    time: float,
    voice_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(login_required)
):
    try:
        has_access, error_message, track, album = await check_unified_track_access(
            track_id, current_user, db, voice_id, require_voice_access=True
        )
        
        if not has_access:
            raise HTTPException(status_code=403, detail=error_message)
        
        sentences_response = await get_track_sentence_timings(track_id, voice_id, db, current_user)
        sentences = sentences_response.get('sentence_timings', [])
        
        if not sentences:
            return {
                "track_id": track_id,
                "voice_id": voice_id,
                "time": time,
                "sentence_index": -1,
                "status": "no_timings"
            }
        
        for i, sentence in enumerate(sentences):
            if sentence['start_time'] <= time < sentence['end_time']:
                return {
                    "track_id": track_id,
                    "voice_id": voice_id,
                    "time": time,
                    "sentence_index": i,
                    "sentence": sentence,
                    "status": "found"
                }
        
        if sentences:
            closest_index = min(
                range(len(sentences)),
                key=lambda i: min(
                    abs(time - sentences[i]['start_time']),
                    abs(time - sentences[i]['end_time'])
                )
            )
            
            return {
                "track_id": track_id,
                "voice_id": voice_id,
                "time": time,
                "sentence_index": closest_index,
                "sentence": sentences[closest_index],
                "status": "closest"
            }
        
        return {
            "track_id": track_id,
            "voice_id": voice_id,
            "time": time,
            "sentence_index": -1,
            "status": "not_found"
        }
        
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to find sentence at time")

@router.get("/api/tracks/{track_id}/time-for-sentence")
async def get_time_for_sentence(
    track_id: str,
    sentence_index: int,
    voice_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(login_required)
):
    try:
        has_access, error_message, track, album = await check_unified_track_access(
            track_id, current_user, db, voice_id, require_voice_access=True
        )
        
        if not has_access:
            raise HTTPException(status_code=403, detail=error_message)
        
        sentences_response = await get_track_sentence_timings(track_id, voice_id, db, current_user)
        sentences = sentences_response.get('sentence_timings', [])
        
        if not sentences:
            return {
                "track_id": track_id,
                "voice_id": voice_id,
                "sentence_index": sentence_index,
                "time": None,
                "status": "no_timings"
            }
        
        if 0 <= sentence_index < len(sentences):
            sentence = sentences[sentence_index]
            return {
                "track_id": track_id,
                "voice_id": voice_id,
                "sentence_index": sentence_index,
                "time": sentence['start_time'],
                "sentence": sentence,
                "status": "found"
            }
        
        return {
            "track_id": track_id,
            "voice_id": voice_id,
            "sentence_index": sentence_index,
            "time": None,
            "status": "invalid_index"
        }
        
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to get time for sentence")

@router.delete("/api/tracks/{track_id}/voice/{voice_id}/delete-cache")
async def delete_voice_cache(
    track_id: str,
    voice_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(login_required)
):
    try:
        if not current_user.is_creator:
            raise HTTPException(status_code=403, detail="Only creators can delete voice cache")
        
        has_access, error_message, track, album = await check_unified_track_access(
            track_id, current_user, db
        )
        
        if not has_access:
            raise HTTPException(status_code=403, detail=error_message)
        
        if getattr(track, 'track_type', 'audio') != 'tts':
            raise HTTPException(status_code=400, detail="Track is not a TTS track")
        
        if album.created_by_id != current_user.id:
            raise HTTPException(status_code=403, detail="You can only delete cache for your own tracks")
        
        default_voice = getattr(track, 'default_voice', None)
        if voice_id == default_voice:
            voice_display_name = voice_id.replace('en-US-', '').replace('en-GB-', '').replace('Neural', '')
            raise HTTPException(status_code=400, detail=f"Cannot delete the default voice '{voice_display_name}'. This is the primary voice for this track.")
        
        result = await db.execute(
            select(TTSTrackMeta).where(TTSTrackMeta.track_id == track_id)
        )
        track_meta = result.scalar_one_or_none()
        
        if track_meta and track_meta.default_voice == voice_id:
            voice_display_name = voice_id.replace('en-US-', '').replace('en-GB-', '').replace('Neural', '')
            raise HTTPException(status_code=400, detail=f"Cannot delete the default voice '{voice_display_name}'. This is the primary voice for this track.")
        
        voice_cache_dir = stream_manager.segment_dir / track_id / f"voice-{voice_id}"
        cache_existed = await aexists(voice_cache_dir)
        
        if not cache_existed:
            return {
                "status": "success",
                "message": f"Voice {voice_id} cache was already deleted",
                "track_id": track_id,
                "voice_id": voice_id,
                "was_cached": False,
                "state_reset": False
            }
        
        await armtree(voice_cache_dir)
        
        lock_key = f"{track_id}:{voice_id}"
        
        # Check if voice was generating (for logging)
        was_locked_in_memory = await voice_cache_manager.is_voice_generating(track_id, voice_id, db)
        # Remove progress tracking (ephemeral UI state)
        enhanced_voice_tts_service.voice_switch_progress.pop(lock_key, None)
        
        try:
            enhanced_voice_tts_service.complete_user_generation(current_user.id, track_id, voice_id)
        except Exception:
            pass
        
        state_was_reset = False
        
        await db.refresh(track)
        
        if track.processing_voice == voice_id:
            track.processing_voice = None
            track.processing_type = None
            track.processing_locked_at = None
            track.status = 'complete'
            track.processing_error = None
            
            await db.commit()
            state_was_reset = True
        
        from status_lock import status_lock
        # Use async session for lock check
        async with async_session() as lock_db:
            lock_was_released = False

            try:
                is_locked, lock_type = await status_lock.is_voice_locked(track_id, voice_id, lock_db)

                if is_locked:
                    await status_lock.unlock_voice(track_id, voice_id, success=False, db=lock_db)
                    lock_was_released = True

            except Exception:
                pass
        
        voice_display_name = voice_id.replace('en-US-', '').replace('en-GB-', '').replace('Neural', '')
        
        reset_summary = []
        if cache_existed:
            reset_summary.append("cache files deleted")
        if was_locked_in_memory:
            reset_summary.append("in-memory locks cleared")
        if state_was_reset:
            reset_summary.append("track state reset")
        if lock_was_released:
            reset_summary.append("DB lock released")
        
        summary_text = ", ".join(reset_summary) if reset_summary else "no active state found"
        
        return {
            "status": "success",
            "message": f"Voice {voice_display_name} cache deleted: {summary_text}",
            "track_id": track_id,
            "voice_id": voice_id,
            "voice_display_name": voice_display_name,
            "was_cached": cache_existed,
            "deleted_files": cache_existed,
            "state_reset": state_was_reset,
            "lock_released": lock_was_released,
            "in_memory_cleared": was_locked_in_memory,
            "note": "Voice will regenerate automatically when requested",
            "default_voice_protected": True,
            "ready_for_regeneration": True,
            "reset_details": {
                "cache_files": cache_existed,
                "in_memory_locks": was_locked_in_memory,
                "track_status_manager": state_was_reset,
                "status_lock": lock_was_released
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete voice cache: {str(e)}")

@router.get("/api/tracks/{track_id}/cache-status")
async def get_track_cache_status(
    track_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(login_required)
):
    try:
        has_access, error_message, track, album = await check_unified_track_access(
            track_id, current_user, db
        )
        
        if not has_access:
            raise HTTPException(status_code=403, detail=error_message)
        
        track_type = getattr(track, 'track_type', 'audio')
        
        if track_type != 'tts':
            return {
                "track_id": track_id,
                "track_type": track_type,
                "is_tts": False,
                "cached_voices": [],
                "cache_info": "Not a TTS track"
            }
        
        cached_voices = []
        track_cache_dir = stream_manager.segment_dir / track_id
        
        if await aexists(track_cache_dir):
            voice_dirs = await aglob(track_cache_dir, "voice-*")
            for voice_dir in voice_dirs:
                voice_id = voice_dir.name.replace("voice-", "")
                master_playlist = voice_dir / "master.m3u8"
                
                segment_count = 0
                default_dir = voice_dir / 'default'
                if await aexists(default_dir):
                    segment_count = await acount_glob(default_dir, 'segment_*.ts')
                
                voice_info = {
                    "voice_id": voice_id,
                    "directory": str(voice_dir),
                    "has_master_playlist": await aexists(master_playlist),
                    "segment_count": segment_count,
                    "is_complete": await aexists(master_playlist) and segment_count > 0,
                    "can_delete": voice_id != getattr(track, 'default_voice', None)
                }
                cached_voices.append(voice_info)
        
        cache_manager_status = {}
        try:
            creator_id = album.created_by_id
            cache_manager_status = await voice_cache_manager.get_voice_cache_status(track_id, creator_id, db)
        except Exception:
            cache_manager_status = {"error": "Cache manager unavailable"}
        
        return {
            "track_id": track_id,
            "track_type": track_type,
            "is_tts": True,
            "default_voice": getattr(track, 'default_voice', None),
            "cached_voices": cached_voices,
            "total_cached": len(cached_voices),
            "cache_manager_status": cache_manager_status,
            "user_permissions": {
                "can_delete_cache": current_user.is_creator and album.created_by_id == current_user.id
            }
        }
        
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to get cache status")

@router.get("/api/tracks/{track_id}/voice-cache-status")
async def get_voice_cache_status(
    track_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(login_required)
):
    try:
        has_access, error_message, track, album = await check_unified_track_access(
            track_id, current_user, db
        )
        
        if not has_access:
            raise HTTPException(status_code=403, detail=error_message)
        
        creator_id = current_user.id if current_user.is_creator else current_user.created_by
        status = await voice_cache_manager.get_voice_cache_status(track_id, creator_id, db)
        return status
        
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to get cache status")

@router.get("/api/tts/bulk-progress/{bulk_queue_id}")
async def get_bulk_tts_progress(
    bulk_queue_id: str, 
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(login_required)
):
    try:
        return await enhanced_voice_tts_service.get_bulk_job_status(bulk_queue_id, current_user)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to get bulk progress")

@router.get("/api/tracks/{track_id}/source-text")
async def get_track_source_text(
    track_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(login_required)
):
    try:
        has_access, error_message, track, album = await check_unified_track_access(
            track_id, current_user, db
        )
        
        if not has_access:
            raise HTTPException(status_code=403, detail=error_message)
        
        if getattr(track, 'track_type', 'audio') != 'tts':
            raise HTTPException(status_code=400, detail="Not a TTS track")
        
        if not check_album_access(current_user, album):
            raise HTTPException(status_code=403, detail="Only creators can edit their TTS tracks")
        
        full_text = await text_storage_service.get_source_text(track_id, db)
        if not full_text:
            full_text = getattr(track, 'source_text', '')
        
        if not full_text:
            raise HTTPException(status_code=404, detail="Source text not found")
        
        stats = await analyze_text_stats(full_text)
        
        return {
            "track_id": track_id,
            "title": track.title,
            "text": full_text,
            "voice": getattr(track, 'default_voice', None),
            "character_count": stats["character_count"],
            "word_count": stats["word_count"]
        }
        
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to retrieve source text")

@router.put("/api/tracks/{track_id}/update-tts-content")
async def update_tts_track_content(
    track_id: str,
    request: TTSCreateRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(login_required)
):
    try:
        # First, get track to determine the voice we'll be regenerating
        try:
            result = await db.execute(
                select(Track)
                .options(selectinload(Track.album))
                .where(Track.id == track_id)
            )
            track = result.scalar_one_or_none()

            if not track:
                raise HTTPException(status_code=404, detail="Track not found")

            if getattr(track, 'track_type', 'audio') != 'tts':
                raise HTTPException(status_code=400, detail="Not a TTS track")

            album = track.album

            if not album:
                raise HTTPException(status_code=404, detail="Album not found")

            # MODIFIED: Use check_album_access to allow both creators and team members
            if not check_album_access(current_user, album):
                raise HTTPException(status_code=403, detail="Only creators can edit their TTS tracks")

            try:
                stored_text = await text_storage_service.get_source_text(track_id, db)
            except:
                stored_text = getattr(track, 'source_text', '')

            stored_text = stored_text or ''

            text_changed = stored_text.strip() != request.text.strip()
            title_changed = track.title != request.title
            visibility_changed = (request.visibility_status is not None and
                                 track.visibility_status != request.visibility_status)

            # Determine voice for locking
            existing_voice = getattr(track, 'default_voice', None) or await get_first_available_voice(db)
            voice_to_use = request.voice if request.voice else existing_voice

            # Handle visibility-ONLY changes WITHOUT locking (no regeneration needed)
            if visibility_changed and not title_changed and not text_changed:
                # Validate visibility value
                valid_statuses = ["visible", "hidden_from_users", "hidden_from_all"]
                if request.visibility_status not in valid_statuses:
                    raise HTTPException(status_code=400, detail=f"Invalid visibility_status. Must be one of: {', '.join(valid_statuses)}")

                # Team members cannot hide from team or all - only from users
                if current_user.is_team and not current_user.is_creator:
                    if request.visibility_status == "hidden_from_all":
                        raise HTTPException(status_code=403, detail="Team members cannot hide content from team. Only 'visible' or 'hidden_from_users' allowed.")

                old_visibility = track.visibility_status
                track.visibility_status = request.visibility_status
                track.updated_at = datetime.now(timezone.utc)
                await db.commit()
                await db.refresh(track)

                return {
                    "status": "updated",
                    "message": "Visibility updated successfully",
                    "track_id": track_id,
                    "old_visibility": old_visibility,
                    "new_visibility": request.visibility_status,
                    "regeneration_required": False
                }

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        # ✅ FIX: Acquire VOICE-SPECIFIC lock, not full track lock (only for title/text changes)
        if not await _acquire_generation_lock_atomic(track_id, 'tts_regeneration', voice_id=voice_to_use):
            raise HTTPException(status_code=202, detail=f"Voice {voice_to_use} is currently being regenerated. Please wait.")

        # NOTE: content_version is incremented by background worker AFTER TTS completes successfully
        # (background_preparation.py:522) to prevent cache corruption from using new text with old timings

        try:
            if title_changed and not text_changed:
                old_title = track.title
                voice_id = voice_to_use

                track.title = request.title

                # Handle visibility_status update if provided
                if request.visibility_status is not None:
                    # Validate visibility value
                    valid_statuses = ["visible", "hidden_from_users", "hidden_from_all"]
                    if request.visibility_status not in valid_statuses:
                        await _release_lock(track_id, success=False, voice_id=voice_to_use)
                        raise HTTPException(status_code=400, detail=f"Invalid visibility_status. Must be one of: {', '.join(valid_statuses)}")

                    # Team members cannot hide from team or all - only from users
                    if current_user.is_team and not current_user.is_creator:
                        if request.visibility_status == "hidden_from_all":
                            await _release_lock(track_id, success=False, voice_id=voice_to_use)
                            raise HTTPException(status_code=403, detail="Team members cannot hide content from team. Only 'visible' or 'hidden_from_users' allowed.")

                    track.visibility_status = request.visibility_status

                track.updated_at = datetime.now(timezone.utc)
                await db.commit()
                await db.refresh(track)

                # Log activity - TTS track title update
                try:
                    from activity_logs_router import log_activity_isolated
                    from models import AuditLogType

                    description = f"Renamed TTS track from '{old_title}' to '{request.title}'"

                    await log_activity_isolated(
                        user_id=current_user.id,
                        action_type=AuditLogType.UPDATE,
                        table_name='tracks',
                        record_id=track_id,
                        description=description,
                        old_values={"title": old_title},
                        new_values={"title": request.title}
                    )
                except Exception as log_err:
                    logger.warning(f"Failed to log TTS track rename activity: {log_err}")

                await _release_lock(track_id, success=True, voice_id=voice_to_use)

                return {
                    "status": "updated",
                    "message": "Title updated successfully",
                    "track_id": track_id,
                    "title": request.title,
                    "regeneration_required": False
                }

            if text_changed:
                old_title = track.title

                try:
                    from mega_s4_client import mega_s4_client
                    if not mega_s4_client._started:
                        await mega_s4_client.start()

                    package_path = storage.tts_package_manager.get_track_package_path(track_id)

                    deleted_count = 0
                    try:
                        objects = await mega_s4_client.list_objects(prefix=package_path)

                        if objects:
                            for obj in objects:
                                try:
                                    obj_key = obj['key'] if isinstance(obj, dict) else obj
                                    if await mega_s4_client.delete_object(obj_key):
                                        deleted_count += 1
                                except Exception:
                                    pass

                    except Exception:
                        pass

                    try:
                        await stream_manager.cleanup_stream(track_id, db)
                    except Exception:
                        pass

                except Exception:
                    pass

                track.source_text = request.text[:1000]
                track.title = request.title
                track.tts_status = 'processing'
                track.upload_status = 'processing'
                track.processing_error = None
                track.available_voices = []

                # Handle visibility_status update if provided
                if request.visibility_status is not None:
                    # Validate visibility value
                    valid_statuses = ["visible", "hidden_from_users", "hidden_from_all"]
                    if request.visibility_status not in valid_statuses:
                        await _release_lock(track_id, success=False, voice_id=voice_to_use)
                        raise HTTPException(status_code=400, detail=f"Invalid visibility_status. Must be one of: {', '.join(valid_statuses)}")

                    # Team members cannot hide from team or all - only from users
                    if current_user.is_team and not current_user.is_creator:
                        if request.visibility_status == "hidden_from_all":
                            await _release_lock(track_id, success=False, voice_id=voice_to_use)
                            raise HTTPException(status_code=403, detail="Team members cannot hide content from team. Only 'visible' or 'hidden_from_users' allowed.")

                    track.visibility_status = request.visibility_status

                await db.commit()
                await db.refresh(track)

                # ✅ Invalidate authorization grants when content changes
                try:
                    from authorization_service import invalidate_on_content_change
                    await invalidate_on_content_change(track_id)
                except Exception as e:
                    logger.warning(f"Failed to invalidate grants for {track_id}: {e}")

                # Log activity - TTS track regeneration
                try:
                    from activity_logs_router import log_activity_isolated
                    from models import AuditLogType

                    description = f"Regenerated TTS track '{request.title}'"
                    if old_title != request.title:
                        description = f"Regenerated and renamed TTS track from '{old_title}' to '{request.title}'"

                    old_vals = {"title": old_title} if old_title != request.title else {}
                    new_vals = {"title": request.title} if old_title != request.title else {}

                    await log_activity_isolated(
                        user_id=current_user.id,
                        action_type=AuditLogType.UPDATE,
                        table_name='tracks',
                        record_id=track_id,
                        description=description,
                        old_values=old_vals or None,
                        new_values=new_vals or None
                    )
                except Exception as log_err:
                    logger.warning(f"Failed to log TTS track regeneration activity: {log_err}")

                background_tasks.add_task(
                    regenerate_tts_track_content,
                    track_id=track_id,
                    title=request.title,
                    text_content=request.text,
                    voice=voice_to_use,
                    user_id=current_user.id,
                    lock_already_held=True
                )

                voice_name = voice_to_use.replace('en-US-','').replace('en-GB-','').replace('Neural','')
                return {
                    "status": "queued",
                    "message": f"Regenerating audio with {voice_name}",
                    "track_id": track_id,
                    "voice": voice_to_use,
                    "regeneration_required": True
                }

            await _release_lock(track_id, success=True, voice_id=voice_to_use)

            return {
                "status": "unchanged",
                "message": "No changes detected",
                "track_id": track_id,
                "regeneration_required": False
            }

        except HTTPException:
            await _release_lock(track_id, success=False, voice_id=voice_to_use)
            raise
        except Exception as e:
            await db.rollback()
            await _release_lock(track_id, success=False, voice_id=voice_to_use)
            raise HTTPException(status_code=500, detail=str(e))

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/tts/config")
async def get_tts_config(
    current_user: User = Depends(login_required)
):
    return {
        "max_characters": 5000000,
        "min_characters": 10,
        "max_bulk_split": 20,
        "min_bulk_split": 2,
        "min_bulk_characters": 10000
    }

@router.get("/api/tracks/{track_id}/voices/{voice_id}/segments")
async def get_track_segments(
    track_id: str,
    voice_id: str,
    current_user: User = Depends(login_required),
    db: AsyncSession = Depends(get_async_db)
):
    try:
        has_access, error_message, track, album = await check_unified_track_access(
            track_id, current_user, db, voice_id, require_voice_access=True
        )
        
        if not has_access:
            raise HTTPException(status_code=403, detail=error_message)
        
        index_data = await stream_manager.get_segment_index(track_id, voice_id)
        
        if index_data.get("status") == "not_found" or not index_data.get("durations"):
            raise HTTPException(status_code=404, detail=f"Segment index not found for {track_id}/{voice_id}")
        
        return {
            "track_id": track_id,
            "voice_id": voice_id,
            "durations": index_data["durations"],
            "starts": index_data["starts"],
            "total_duration": index_data.get("total_duration", sum(index_data["durations"])),
            "segment_count": len(index_data["durations"]),
            "measured": index_data.get("measured", True)
        }
        
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Error retrieving segment index")

@router.get("/api/tracks/{track_id}/voices/{voice_id}/words")
async def get_track_words_range(
    track_id: str,
    voice_id: str,
    start: float = Query(0.0, ge=0.0, description="Start time in seconds"),
    end: float = Query(None, ge=0.0, description="End time in seconds (default: all)"),
    limit: int = Query(5000, ge=1, le=50000, description="Maximum words to return"),
    current_user: User = Depends(login_required),
    db: AsyncSession = Depends(get_async_db)
):
    try:
        has_access, error_message, track, album = await check_unified_track_access(
            track_id, current_user, db, voice_id, require_voice_access=True
        )
        
        if not has_access:
            raise HTTPException(status_code=403, detail=error_message)
        
        if end is None:
            end = 1e12
        
        from text_storage_service import text_storage_service
        words = await text_storage_service.get_word_timings_range(
            track_id, voice_id, start, end, limit, db
        )
        
        return {
            "track_id": track_id,
            "voice_id": voice_id,
            "start": start,
            "end": end if end < 1e12 else None,
            "limit": limit,
            "count": len(words),
            "words": words
        }
        
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Error retrieving words")

async def process_voice_switch_with_cleanup(
    track_id: str, 
    old_voice: str,
    new_voice: str, 
    user_id: int,
    lock_key: str,
    user: User,
    lock_already_held: bool = False
):
    from track_status_manager import TrackStatusManager
    
    try:
        async with async_session() as db:
            if not lock_already_held:
                raise RuntimeError("Lock must be held before calling worker")
            
            if lock_key in enhanced_voice_tts_service.voice_switch_progress:
                enhanced_voice_tts_service.voice_switch_progress[lock_key].update({
                    'status': 'processing',
                    'progress': 5,
                    'phase': 'checking_backup',
                    'message': 'Checking for existing data...'
                })
            
            track = await db.get(Track, track_id)
            if not track:
                raise ValueError(f"Track {track_id} not found")
            
            is_default_voice = getattr(track, 'default_voice', None) == new_voice

            if is_default_voice:
                await TrackStatusManager.mark_generating(track, db, process_type='voice_generation', voice=new_voice)
                # ✅ Update tts_status only for default voice
                track.tts_status = 'processing'
                await db.commit()
            
            result = await enhanced_voice_tts_service.switch_voice_efficiently(
                track_id=track_id,
                new_voice=new_voice,
                db=db,
                user=user,
                already_locked=True
            )
            
            if result['status'] != 'success':
                raise ValueError(f"Voice switch failed: {result}")
            
            is_s4_download = result.get('source') in ['s3_backup', 'db_triggered_s4_download']
            
            await db.refresh(track)
            if getattr(track, 'default_voice', None) == new_voice:
                await TrackStatusManager.mark_segmenting(track, db, voice=new_voice)
            await db.commit()
        
        async with async_session() as db:
            # Extract streaming file path from result
            word_timings_data = result.get('word_timings')
            word_timings_file = None
            
            if word_timings_data:
                if isinstance(word_timings_data, dict):
                    timings_file_path = word_timings_data.get('timings_file_path')
                    if timings_file_path:
                        word_timings_file = Path(timings_file_path)
                        logger.info(f"Using streaming timings: {word_timings_file}")
                elif isinstance(word_timings_data, list):
                    raise ValueError(
                        f"Streaming format required. "
                        f"Received list with {len(word_timings_data)} items. "
                        f"TTS service must return dict with timings_file_path."
                    )
            
            file_url, upload_metadata = await storage.upload_tts_media_with_voice(
                audio_file_path=Path(result['audio_file_path']),
                track_id=track_id,
                voice=new_voice,
                creator_id=user_id,
                db=db,
                word_timings=None,
                word_timings_path=word_timings_file,
                is_voice_switch=True,
                use_upsert=True,
                skip_s4_upload=is_s4_download,
                session_dir=result.get('session_dir'),
                lock_already_held=True
            )
            
            from duration_manager import duration_manager
            if 'segments' in result and result['segments']:
                segment_durations = []
                for i, segment in enumerate(result['segments']):
                    segment_durations.append({
                        'segment_id': segment.get('text_segment_id', i),
                        'actual_duration': segment.get('actual_duration', segment.get('duration', 0.0))
                    })
                
                await duration_manager.store_voice_duration(
                    track_id=track_id,
                    voice_id=new_voice,
                    segments_data=segment_durations,
                    db_session=db
                )
            
            track = await db.get(Track, track_id)
            is_default_voice = track and getattr(track, 'default_voice', None) == new_voice

            if is_default_voice:
                await TrackStatusManager.mark_complete(track, db)
                # ✅ Update tts_status only for default voice
                track.tts_status = 'ready'

            await db.commit()
        
        if lock_key in enhanced_voice_tts_service.voice_switch_progress:
            enhanced_voice_tts_service.voice_switch_progress[lock_key].update({
                'status': 'complete',
                'progress': 100,
                'phase': 'complete',
                'message': 'Voice generation complete'
            })
        
        return {
            'status': 'success',
            'track_id': track_id,
            'new_voice': new_voice,
            'file_url': file_url
        }
            
    except Exception as e:
        async with async_session() as db:
            track = await db.get(Track, track_id)
            if track:
                # ✅ Only mark track as failed if the default voice failed
                # Non-default voice failures should not affect track status
                is_default_voice = getattr(track, 'default_voice', None) == new_voice

                if is_default_voice:
                    # Default voice failed - mark track as failed
                    await TrackStatusManager.mark_failed(track, db, e, 'voice_generation')
                    # ✅ Update tts_status only for default voice
                    track.tts_status = 'error'
                    logger.error(f"❌ Default voice {new_voice} failed for track {track_id}: {str(e)}")
                else:
                    # Non-default voice failed - only log, don't fail the track
                    logger.warning(f"⚠️ Non-default voice {new_voice} failed for track {track_id}: {str(e)}")

            # ✅ FIX: Mark voice as failed in VoiceGenerationStatus
            # This ensures failed voices don't count against voice limits
            from voice_cache_manager import voice_cache_manager
            await voice_cache_manager.mark_voice_failed(
                track_id=track_id,
                voice_id=new_voice,
                error=str(e),
                db=db
            )
            logger.warning(f"⚠️ Voice generation failed for {new_voice}: {str(e)}")

        if lock_key in enhanced_voice_tts_service.voice_switch_progress:
            enhanced_voice_tts_service.voice_switch_progress[lock_key].update({
                'status': 'error',
                'progress': 0,
                'phase': 'error',
                'message': f'Error: {str(e)}'
            })

        if lock_already_held:
            await _release_lock(track_id, success=False, voice_id=new_voice)

        raise
    
    finally:
        try:
            enhanced_voice_tts_service.complete_user_generation(user_id, track_id, new_voice)
        except Exception:
            pass
        
        # Database automatically marks voice as failed via mark_voice_failed()
        # Remove progress tracking after delay (ephemeral UI state)
        async def cleanup_progress():
            await asyncio.sleep(60)
            enhanced_voice_tts_service.voice_switch_progress.pop(lock_key, None)
        
        asyncio.create_task(cleanup_progress())

async def regenerate_tts_track_content(
    track_id: str,
    title: str,
    text_content: str,
    voice: str,
    user_id: int,
    lock_already_held: bool = False
):
    from track_status_manager import TrackStatusManager
    
    session_dir = None
    
    async with async_session() as db:
        try:
            if not lock_already_held:
                raise RuntimeError("Lock must be held before calling worker")
            
            user = await db.get(User, user_id)
            track = await db.get(Track, track_id)
            
            if not user or not track:
                raise ValueError(f"User/Track not found")
            
            await TrackStatusManager.mark_generating(track, db, process_type='tts_regeneration', voice=voice)
            await db.commit()
            
            try:
                await stream_manager.cleanup_stream(track_id, db)
            except Exception:
                pass
            
            result = await enhanced_voice_tts_service.create_tts_track_with_voice(
                track_id=track_id,
                title=title,
                text_content=text_content,
                voice=voice,
                db=db,
                user=user
            )
            
            if result['status'] != 'success':
                raise ValueError(f"TTS generation failed: {result}")
            
            session_dir = result.get('session_dir')
            
            await TrackStatusManager.mark_segmenting(track, db, voice=voice)
            
            # ✅ HANDLE NEW STREAMING FORMAT
            word_timings_data = result.get('word_timings')
            word_timings_list = None
            word_timings_file = None
            
            if word_timings_data:
                if isinstance(word_timings_data, dict) and 'timings_file_path' in word_timings_data:
                    # New streaming format - timings already on disk
                    word_timings_file = Path(word_timings_data['timings_file_path'])
                    logger.info(f"Using streaming timings file: {word_timings_file}")
                elif isinstance(word_timings_data, list):
                    # Old format - timings in memory
                    word_timings_list = word_timings_data
                    logger.info(f"Using in-memory timings: {len(word_timings_list)} words")
                else:
                    logger.warning(f"Unexpected word_timings format: {type(word_timings_data)}")
            
            # ✅ FIX: Set duration in DB BEFORE queuing HLS (so HLS worker can read from DB)
            track.duration = result['duration']
            await db.commit()

            # ✅ FIX: Pass pre-extracted metadata to avoid redundant duration extraction
            pre_extracted = {
                'duration': result.get('duration', 0),
                'is_tts': True,
                'word_count': result.get('word_count', 0)
            }

            file_url, upload_metadata = await storage.upload_tts_media_with_voice(
                audio_file_path=Path(result['audio_file_path']),
                track_id=track_id,
                voice=voice,
                creator_id=user_id,
                db=db,
                word_timings=word_timings_list,  # None if using file
                word_timings_path=word_timings_file,  # File path if using streaming
                session_dir=session_dir,
                pre_extracted_metadata=pre_extracted,  # ✅ Avoid redundant ffprobe call
                lock_already_held=True  # ✅ TTS API handler already holds the lock
            )

            session_dir = None

            track.file_path = file_url
            track.tts_status = 'ready'
            track.upload_status = 'complete'
            track.updated_at = datetime.now(timezone.utc)
            track.processing_error = None
            
            if 'voice_directory' in upload_metadata:
                track.voice_directory = upload_metadata['voice_directory']
            
            await db.commit()

            cache_bust_value = int(track.updated_at.timestamp() * 1000) if track.updated_at else None

        except Exception as e:
            if session_dir:
                await armtree(Path(session_dir))
            
            track = await db.get(Track, track_id)
            if track:
                await TrackStatusManager.mark_failed(track, db, e, 'tts_regeneration')
                await db.commit()
            
            if lock_already_held:
                await _release_lock(track_id, success=False)
            
            raise

async def process_bulk_tts_creation(
    track_id: str,
    album_id: str,
    title: str,
    text_content: str,
    voice: str,
    user_id: int,
    bulk_split_count: int,
    bulk_series_title: str,
    bulk_queue_id: str,
    starting_order: int = 0
):
    async with async_session() as db:
        try:
            user = await db.get(User, user_id)
            if not user:
                raise ValueError("User not found")
            
            result = await enhanced_voice_tts_service.create_tts_track_with_voice(
                track_id=track_id,
                title=title,
                text_content=text_content,
                voice=voice,
                db=db,
                user=user,
                album_id=album_id,
                bulk_split_count=bulk_split_count,
                bulk_series_title=bulk_series_title,
                starting_order=starting_order
            )
            
        except Exception as e:
            async with enhanced_voice_tts_service._bulk_lock:
                if bulk_queue_id in enhanced_voice_tts_service.bulk_jobs:
                    enhanced_voice_tts_service.bulk_jobs[bulk_queue_id]['status'] = 'failed'
                    enhanced_voice_tts_service.bulk_jobs[bulk_queue_id]['error'] = str(e)
            
            raise

async def process_enhanced_voice_tts_track(
    track_id: str, 
    title: str, 
    text_content: str, 
    voice: str, 
    user_id: int,
    lock_already_held: bool = False
):
    from track_status_manager import TrackStatusManager
    from models import Track
    
    session_dir_to_cleanup = None
    lock_key = f"{track_id}:{voice}"
    
    async with async_session() as db:
        try:
            if not lock_already_held:
                raise RuntimeError("Lock must be held before calling worker")
            
            track = await db.get(Track, track_id)
            user = await db.get(User, user_id)
            
            if not user or not track:
                raise ValueError("User or Track not found")
            
            await TrackStatusManager.mark_generating(track, db, 'tts', voice=voice)
            await db.commit()
            
            result = await enhanced_voice_tts_service.create_tts_track_with_voice(
                track_id=track_id,
                title=title,
                text_content=text_content,
                voice=voice,
                db=db,
                user=user
            )
            
            if result['status'] != 'success':
                raise ValueError(f"TTS generation failed: {result}")
            
            session_dir_to_cleanup = result.get('session_dir')
            
            await TrackStatusManager.mark_segmenting(track, db, voice=voice)
            await db.commit()
            
            # Extract streaming file path from result
            word_timings_data = result.get('word_timings')
            word_timings_file = None
            
            if word_timings_data:
                if isinstance(word_timings_data, dict):
                    timings_file_path = word_timings_data.get('timings_file_path')
                    if timings_file_path:
                        word_timings_file = Path(timings_file_path)
                        logger.info(f"Using streaming timings: {word_timings_file}")
                elif isinstance(word_timings_data, list):
                    raise ValueError(
                        f"Streaming format required. "
                        f"Received list with {len(word_timings_data)} items. "
                        f"TTS service must return dict with timings_file_path."
                    )
            
            file_url, upload_metadata = await storage.upload_tts_media_with_voice(
                audio_file_path=Path(result['audio_file_path']),
                track_id=track_id,
                voice=voice,
                creator_id=user_id,
                db=db,
                word_timings=None,
                word_timings_path=word_timings_file,
                session_dir=result.get('session_dir'),
                lock_already_held=True  # ✅ TTS API handler already holds the lock
            )
            
            session_dir_to_cleanup = None
            
            track.file_path = file_url
            track.default_voice = voice
            track.track_type = 'tts'
            track.duration = result['duration']
            track.updated_at = datetime.now(timezone.utc)

            if not track.content_version:
                track.content_version = 1
            
            if 'voice_directory' in upload_metadata:
                track.voice_directory = upload_metadata['voice_directory']
            
            available_voices = getattr(track, 'available_voices', [])
            if voice not in available_voices:
                available_voices.append(voice)
                track.available_voices = available_voices

            await db.commit()
            
            if lock_key in enhanced_voice_tts_service.voice_switch_progress:
                enhanced_voice_tts_service.voice_switch_progress[lock_key].update({
                    'status': 'complete',
                    'progress': 100,
                    'phase': 'complete',
                    'message': 'Voice generation complete'
                })
            
            return {
                'status': 'success',
                'track_id': track_id,
                'file_url': file_url,
                'lock_held': True
            }
            
        except Exception as e:
            await db.rollback()
            
            if session_dir_to_cleanup:
                await armtree(Path(session_dir_to_cleanup))
            
            track = await db.get(Track, track_id)
            if track:
                await TrackStatusManager.mark_failed(track, db, e, 'tts_generation')
                await db.commit()
            
            if lock_already_held:
                await _release_lock(track_id, success=False)
            
            if lock_key in enhanced_voice_tts_service.voice_switch_progress:
                enhanced_voice_tts_service.voice_switch_progress[lock_key].update({
                    'status': 'error',
                    'progress': 0,
                    'phase': 'error',
                    'message': f'Error: {str(e)}'
                })
            
            raise
        
        finally:
            try:
                enhanced_voice_tts_service.complete_user_generation(user_id, track_id, voice)
            except Exception:
                pass
            
            # Database automatically handles lock state
            # Remove progress tracking after delay (ephemeral UI state)
            async def cleanup_progress():
                await asyncio.sleep(60)
                enhanced_voice_tts_service.voice_switch_progress.pop(lock_key, None)
            
            asyncio.create_task(cleanup_progress())

__all__ = ['router']
