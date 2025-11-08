# storage.py - Non-blocking filesystem operations with unified locking

from pathlib import Path
import uuid
from uuid import uuid4
import logging
import inspect
import shutil
import aiofiles
import asyncio
from fastapi import HTTPException, UploadFile
from datetime import datetime, timezone
import os
from typing import Optional, Dict, List, Tuple
import json
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import anyio
import functools
from models import Track, TTSTextSegment, TTSWordTiming, TTSTrackMeta
from background_preparation import BackgroundPreparationManager
from duration_manager import duration_manager
from upload_queue import upload_queue, WriteStatus
from metadata_extraction import metadata_queue
from mega_upload_manager import mega_upload_manager
from text_storage_service import text_storage_service

logger = logging.getLogger(__name__)

# Async/sync DB compatibility helpers
def _is_async(db) -> bool:
    """Check if database session is async"""
    return isinstance(db, AsyncSession)

async def _exec(db, stmt):
    """Execute a SQLAlchemy statement on either AsyncSession or sync Session without blocking"""
    if _is_async(db):
        return await db.execute(stmt)
    return await anyio.to_thread.run_sync(db.execute, stmt)

async def _commit(db):
    """Commit transaction without blocking"""
    if _is_async(db):
        await db.commit()
    else:
        await anyio.to_thread.run_sync(db.commit)

async def _rollback(db):
    """Rollback transaction without blocking"""
    if _is_async(db):
        await db.rollback()
    else:
        await anyio.to_thread.run_sync(db.rollback)

# Async filesystem helpers - prevents blocking the event loop
async def aio_copy2(src: Path, dst: Path):
    """Non-blocking file copy"""
    return await anyio.to_thread.run_sync(shutil.copy2, src, dst)

async def aio_move(src: Path, dst: Path):
    """Non-blocking file move"""
    return await anyio.to_thread.run_sync(shutil.move, str(src), str(dst))

async def aio_rmtree(path: Path):
    """Non-blocking directory tree removal"""
    return await anyio.to_thread.run_sync(shutil.rmtree, str(path), False)

async def aio_unlink(path: Path):
    """Non-blocking file deletion"""
    return await anyio.to_thread.run_sync(os.unlink, str(path))

async def aio_exists(path: Path) -> bool:
    """Non-blocking path existence check"""
    return await anyio.to_thread.run_sync(path.exists)

async def aio_stat(path: Path):
    """Non-blocking file stat"""
    return await anyio.to_thread.run_sync(path.stat)

async def aio_glob(path: Path, pattern: str):
    """Non-blocking glob pattern matching"""
    return await anyio.to_thread.run_sync(lambda: list(path.glob(pattern)))

async def aio_mkdir(path: Path, parents: bool = True, exist_ok: bool = True):
    """Non-blocking directory creation"""
    func = functools.partial(path.mkdir, parents=parents, exist_ok=exist_ok)
    return await anyio.to_thread.run_sync(func)

async def _maybe_await(func, *args, **kwargs):
    """Handle both async and sync functions without blocking"""
    if inspect.iscoroutinefunction(func):
        return await func(*args, **kwargs)
    return await anyio.to_thread.run_sync(lambda: func(*args, **kwargs))

def _strip_url(url: str) -> str:
    """Remove query strings and fragments from URLs"""
    return url.split('?', 1)[0].split('#', 1)[0]

_stream_manager = None
def get_stream_manager():
    global _stream_manager
    if _stream_manager is None:
        from hls_streaming import stream_manager
        _stream_manager = stream_manager
    return _stream_manager


class TTSPackageManager:
    def __init__(self, mega_base_path: str):
        self.mega_base_path = mega_base_path
        self.mega_tts_tracks_path = f"{mega_base_path}/tts-tracks"
        
    def get_track_package_path(self, track_id: str) -> str:
        return f"{self.mega_tts_tracks_path}/{track_id}"
    
    def get_track_metadata_path(self, track_id: str) -> str:
        return f"{self.get_track_package_path(track_id)}/metadata.json"
    
    def get_source_text_path(self, track_id: str) -> str:
        return f"{self.get_track_package_path(track_id)}/source.txt.zst"
    
    def get_voice_audio_path(self, track_id: str, voice_id: str) -> str:
        return f"{self.get_track_package_path(track_id)}/voice-{voice_id}/audio.mp3"
    
    def get_voice_timings_path(self, track_id: str, voice_id: str) -> str:
        return f"{self.get_track_package_path(track_id)}/voice-{voice_id}/timings.zst"
    
    def get_voice_metadata_path(self, track_id: str, voice_id: str) -> str:
        return f"{self.get_track_package_path(track_id)}/voice-{voice_id}/voice_meta.json"


class MediaStorage:
    def __init__(self):
        self.mega_base_path = "audio-streaming-app/media"
        self.mega_audio_path = f"{self.mega_base_path}/audio"
        self.mega_images_path = f"{self.mega_base_path}/images"
        self.tts_package_manager = TTSPackageManager(self.mega_base_path)
        self.media_url = "/media"
        self.audio_url = f"{self.media_url}/audio"
        self.images_url = f"{self.media_url}/images"
        self.temp_dir = Path("/tmp/media_storage")
        self.image_cache_dir = Path("/tmp/image_cache")
        
        self.preparation_manager = BackgroundPreparationManager()
        self.download_progress: Dict[str, Dict] = {}
        
        # HLS preparation locks to prevent duplicate processing
        self._prep_locks: Dict[str, asyncio.Lock] = {}
        self._prep_locks_lock = asyncio.Lock()
        
        # Download progress tracking (with size limit to prevent unbounded growth)
        self._max_progress_entries = 100
        
        self._initialize_directories()

    def _initialize_directories(self):
        """Sync initialization - only called once at startup"""
        try:
            for directory in [self.temp_dir, self.image_cache_dir]:
                directory.mkdir(parents=True, exist_ok=True)
                os.chmod(str(directory), 0o755)
        except Exception as e:
            logger.error(f"Failed to initialize directories: {e}")
            raise

    async def _determine_priority(self, file_size: int) -> str:
        SMALL_FILE = 10 * 1024 * 1024
        LARGE_FILE = 100 * 1024 * 1024
        
        if file_size < SMALL_FILE:
            return 'high'
        elif file_size > LARGE_FILE:
            return 'low'
        return 'normal'
    
    async def _acquire_prep_lock(self, key: str) -> asyncio.Lock:
        """Acquire a preparation lock to prevent duplicate HLS processing"""
        async with self._prep_locks_lock:
            lock = self._prep_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._prep_locks[key] = lock
        await lock.acquire()
        return lock

    async def shutdown(self):
        try:
            await self.preparation_manager.stop()
            # Ensure the shared S4 HTTP client is closed cleanly
            try:
                from mega_s4_client import mega_s4_client
                if getattr(mega_s4_client, "_started", False):
                    await mega_s4_client.stop()
                    logger.info("mega_s4_client stopped cleanly")
            except Exception as e:
                logger.warning(f"Failed to stop mega_s4_client: {e}")
            if await aio_exists(self.temp_dir):
                await aio_rmtree(self.temp_dir)
                await aio_mkdir(self.temp_dir)
        except Exception as e:
            logger.error(f"Error during MediaStorage shutdown: {e}")
            raise

    async def get_processing_status(self, track_id: str, voice_id: Optional[str] = None, db = None) -> Dict:
        """Get processing status using simple_track_lock only"""
        
        # Check if track is locked
        if voice_id:
            try:
                from status_lock import status_lock

                should_close = False
                if not db:
                    from database import get_db
                    db = next(get_db())
                    should_close = True

                try:
                    is_locked, lock_type = await status_lock.is_voice_locked(track_id, voice_id, db)
                    
                    if is_locked and 'voice' in lock_type.lower():
                        logger.info(f"Voice processing locked: {track_id}/{voice_id}")
                        return {
                            'status': 'processing',
                            'progress': 50,
                            'phase': 'voice_switching',
                            'message': f'Voice switching in progress: {lock_type}',
                            'voice_info': {
                                'voice_id': voice_id,
                                'is_voice_switch': True,
                                'lock_type': lock_type
                            }
                        }
                finally:
                    if should_close:
                        db.close()
            except Exception as e:
                logger.warning(f"Lock check failed: {e}")
        
        # Check background preparation status
        if voice_id:
            job_id = f"{track_id}_voice_{voice_id}"
            status = self.preparation_manager.get_status(job_id)
            if status:
                return {
                    'status': status['status'],
                    'progress': status.get('progress', 0),
                    'error': status.get('error'),
                    'phase': status.get('phase'),
                    'voice_info': {
                        'is_voice_switch': True,
                        'voice_id': voice_id,
                        'worker_id': status.get('worker_id'),
                        'job_id': job_id
                    }
                }
        
        prep_status = self.preparation_manager.get_status(track_id)
        
        status_response = {
            "processing_status": "unknown",
            "processing_progress": 0,
            "is_queued": False,
            "queue_position": None,
            "error": None,
            "voice_info": {}
        }
        
        if prep_status:
            from background_preparation import PreparationStatus
            
            is_queued = prep_status.get('status') == PreparationStatus.QUEUED.value
            queue_position = None
            
            if is_queued:
                priority = prep_status.get('priority', 'normal')
                try:
                    queue = self.preparation_manager._queues.get(priority)
                    if queue:
                        queue_position = queue.qsize()
                except Exception:
                    queue_position = None
            
            status_mapping = {
                PreparationStatus.QUEUED.value: "queued",
                PreparationStatus.DOWNLOADING.value: "processing",
                PreparationStatus.PROCESSING.value: "processing",
                PreparationStatus.SEGMENTING.value: "processing",
                PreparationStatus.COMPLETED.value: "completed",
                PreparationStatus.FAILED.value: "failed"
            }
            
            status_response.update({
                "processing_status": status_mapping.get(prep_status.get('status'), "unknown"),
                "processing_progress": prep_status.get('progress', 0),
                "is_queued": is_queued,
                "queue_position": queue_position if is_queued else None,
                "error": prep_status.get('error')
            })
        else:
            stream_manager = get_stream_manager()
            stream_dir = stream_manager.segment_dir / track_id
            
            if await aio_exists(stream_dir):
                if await aio_exists(stream_dir / "master.m3u8"):
                    status_response.update({
                        "processing_status": "completed",
                        "processing_progress": 100
                    })
                
                voice_dirs = await aio_glob(stream_dir, "voice-*")
                if voice_dirs:
                    available_voices = []
                    for voice_dir in voice_dirs:
                        voice_name = voice_dir.name.replace("voice-", "")
                        if await aio_exists(voice_dir / "master.m3u8"):
                            available_voices.append(voice_name)
                    
                    status_response["voice_info"] = {
                        "available_voices": available_voices,
                        "is_voice_track": True,
                        "voice_count": len(available_voices)
                    }
                    
                    if available_voices:
                        status_response.update({
                            "processing_status": "completed",
                            "processing_progress": 100
                        })
        
        return status_response

    async def regenerate_voice_efficiently(self, track_id: str, voice: str, creator_id: int, db) -> bool:
        """Smart voice regeneration - S4 first, then generate if needed"""
        try:
            logger.info(f"📦 REGEN-1: Starting regeneration for {track_id}/{voice}")

            # Try S4 first
            logger.info(f"📦 REGEN-2: Attempting S4 download for {track_id}/{voice}")
            try:
                voice_package = await self.download_complete_voice_package(track_id, voice, db)
                logger.info(f"📦 REGEN-3: Package download result: {bool(voice_package)}")
                if voice_package:
                    logger.info(f"Found voice package in S4: {track_id}/{voice}")
                    
                    # Store source text if not in filesystem
                    if voice_package.get('source_text'):
                        try:
                            existing_text = await text_storage_service.get_source_text(track_id)
                            if not existing_text:
                                logger.info(f"Source text not in filesystem, storing from S4: {track_id}")
                                await text_storage_service.store_source_text(
                                    track_id, voice_package['source_text'], db
                                )
                                logger.info(f"Source text restored from S4: {track_id}")
                        except Exception as text_error:
                            logger.warning(f"Could not store source text: {text_error}")
                    
                    # Update DB metadata and timing file on disk
                    await self._store_regenerated_voice_data(
                        track_id=track_id,
                        voice=voice,
                        word_timings=voice_package['word_timings'],
                        duration=voice_package['duration'],
                        db=db
                    )
                    logger.info(f"📦 REGEN-4: Voice metadata stored for {track_id}/{voice}")

                    # Queue HLS only (no S4 re-upload, no JSONL round-trip)
                    # Lock is already held by HTTP handler (enhanced_app_routes_voice.py)
                    await self.upload_tts_media_with_voice(
                        audio_file_path=voice_package['audio_file_path'],
                        track_id=track_id,
                        voice=voice,
                        creator_id=creator_id,
                        db=db,
                        word_timings=None,
                        word_timings_path=None,
                        is_voice_switch=True,
                        use_upsert=True,
                        skip_s4_upload=True,
                        pre_extracted_metadata={"duration": voice_package["duration"], "is_tts": True},
                        lock_already_held=True  # HTTP handler holds lock, worker will unlock
                    )
                    logger.info(f"📦 REGEN-5: HLS queued with lock_already_held=True for {track_id}/{voice}")

                    logger.info(f"Voice restored from S4 and HLS queued: {track_id}/{voice}")
                    return True
                    
            except Exception as s4_error:
                logger.info(f"S4 download failed or voice not in S4: {s4_error}")
                # Fall through to generation

            # S4 failed - Generate new TTS from source text
            logger.info(f"📦 REGEN-FALLBACK: S4 failed, generating new TTS for {track_id}/{voice}")
            
            source_text = await text_storage_service.get_source_text(track_id)
            if not source_text:
                logger.error(f"No source text available for generation: {track_id}")
                return False
            
            res = await _exec(db, select(Track).where(Track.id == track_id))
            track = res.scalar_one_or_none()
            if not track:
                logger.error(f"Track not found: {track_id}")
                return False
                
            from models import User
            res = await _exec(db, select(User).where(User.id == track.created_by_id))
            user = res.scalar_one_or_none()
            if not user:
                logger.error(f"User not found for track: {track_id}")
                return False
            
            from enhanced_tts_voice_service import enhanced_voice_tts_service
            tts_result = await enhanced_voice_tts_service.create_tts_track_with_voice(
                track_id=track_id, 
                title=track.title, 
                text_content=source_text,
                voice=voice, 
                db=db, 
                user=user
            )
            
            if tts_result['status'] != 'success':
                logger.error(f"TTS generation failed: {track_id}/{voice}")
                return False
            
            # Extract streaming file path from result (if provided by generator)
            word_timings_data = tts_result.get('word_timings')
            word_timings_file = None

            if isinstance(word_timings_data, dict):
                word_timings_file = word_timings_data.get('timings_file_path')
                if word_timings_file:
                    word_timings_file = Path(word_timings_file)

            # ✅ FIX: Pass pre-extracted metadata to avoid redundant duration extraction
            pre_extracted = {
                'duration': tts_result.get('duration', 0),
                'is_tts': True,
                'word_count': tts_result.get('word_count', 0)
            }

            # Upload new TTS (needs S4 upload - it's new)
            # Lock is already held by HTTP handler (enhanced_app_routes_voice.py)
            await self.upload_tts_media_with_voice(
                audio_file_path=Path(tts_result['audio_file_path']),
                track_id=track_id,
                voice=voice,
                creator_id=creator_id,
                db=db,
                word_timings=None,  # rely on timings_file if provided
                word_timings_path=word_timings_file,
                is_voice_switch=True,
                use_upsert=True,
                skip_s4_upload=False,
                pre_extracted_metadata=pre_extracted,  # ✅ Avoid redundant ffprobe call
                lock_already_held=True  # HTTP handler holds lock, worker will unlock
            )
            logger.info(f"📦 REGEN-6: New TTS generated and queued with lock_already_held=True for {track_id}/{voice}")

            logger.info(f"New TTS generated and uploaded: {track_id}/{voice}")
            return True
            
        except Exception as e:
            logger.error(f"Voice regeneration failed: {track_id}/{voice} - {e}")
            return False


    async def download_complete_voice_package(self, track_id: str, voice: str, db) -> Optional[Dict]:
        """Download complete voice package from S4 (audio + timings + metadata + source text)"""
        try:
            from mega_s4_client import mega_s4_client
            if not mega_s4_client._started:
                await mega_s4_client.start()
            
            logger.info(f"Downloading complete voice package: {track_id}/{voice}")
            
            # 1. Download audio
            audio_path = await self.download_voice_audio_file(track_id, voice)
            if not audio_path:
                logger.warning(f"Audio not found in S4: {track_id}/{voice}")
                # Try to download source text even if audio missing
                await self._download_and_store_source_text(track_id, db)
                return None
            
            # 2. Download timings (required)
            voice_timings_path = self.tts_package_manager.get_voice_timings_path(track_id, voice)
            timings_temp_path = self.temp_dir / f"timings_{track_id}_{voice}.zst"
            
            word_timings = []
            
            timings_response = await mega_s4_client.download_file_stream(voice_timings_path)
            if not timings_response:
                logger.warning(f"Timings not found in S4: {track_id}/{voice}")
                if await aio_exists(audio_path):
                    await aio_unlink(audio_path)
                await self._download_and_store_source_text(track_id, db)
                return None
            
            try:
                async with aiofiles.open(timings_temp_path, 'wb') as f:
                    async for chunk in timings_response.content.iter_chunked(65536):
                        await f.write(chunk)
            finally:
                if timings_response and not timings_response.closed:
                    try:
                        close_result = timings_response.close()
                        if hasattr(close_result, '__await__'):
                            await close_result
                    except Exception:
                        pass
            
            if not await aio_exists(timings_temp_path) or (await aio_stat(timings_temp_path)).st_size == 0:
                logger.warning(f"Timings download failed or empty: {track_id}/{voice}")
                if await aio_exists(audio_path):
                    await aio_unlink(audio_path)
                await self._download_and_store_source_text(track_id, db)
                return None
            
            try:
                async with aiofiles.open(timings_temp_path, 'rb') as f:
                    compressed_data = await f.read()
                
                decompressed_data = await anyio.to_thread.run_sync(
                    text_storage_service._decompress_data, compressed_data
                )
                word_timings = await anyio.to_thread.run_sync(
                    text_storage_service._unpack_word_timings_v4, decompressed_data
                )
                await aio_unlink(timings_temp_path)
                
                if not word_timings:
                    logger.warning(f"Timings unpacked but empty: {track_id}/{voice}")
                    if await aio_exists(audio_path):
                        await aio_unlink(audio_path)
                    await self._download_and_store_source_text(track_id, db)
                    return None
                    
            except Exception as e:
                logger.error(f"Error parsing timings: {e}")
                if await aio_exists(audio_path):
                    await aio_unlink(audio_path)
                if await aio_exists(timings_temp_path):
                    await aio_unlink(timings_temp_path)
                await self._download_and_store_source_text(track_id, db)
                return None
            
            # 3. Download source text (optional)
            source_text = await self._download_and_store_source_text(track_id, db)
            
            duration = await self._get_audio_duration(audio_path)
            
            logger.info(f"✅ Downloaded COMPLETE voice package: {track_id}/{voice} - {duration:.2f}s, {len(word_timings)} words, source_text: {bool(source_text)}")
            
            return {
                'audio_file_path': audio_path,
                'word_timings': word_timings,
                'source_text': source_text,
                'duration': duration,
                'voice': voice,
                'track_id': track_id,
                'source': 's4_backup'
            }
            
        except Exception as e:
            logger.error(f"Error downloading voice package {track_id}/{voice}: {e}")
            try:
                await self._download_and_store_source_text(track_id, db)
            except Exception:
                pass
            return None

    async def _download_and_store_source_text(self, track_id: str, db) -> Optional[str]:
        """Download source text compressed file from S4 directly to final destination"""
        try:
            from mega_s4_client import mega_s4_client
            if not mega_s4_client._started:
                await mega_s4_client.start()

            # Get S4 path and final destination path
            source_s4_path = self.tts_package_manager.get_source_text_path(track_id)
            final_path = text_storage_service._get_text_file_path(track_id)

            # Ensure directory exists
            final_dir = final_path.parent
            await aio_mkdir(final_dir)

            # Download directly to temp in same directory (for atomic move)
            temp_path = final_dir / f"source_{track_id}_temp.txt.zst"

            source_response = await mega_s4_client.download_file_stream(source_s4_path)
            if not source_response:
                logger.warning(f"Source text not found in S4: {track_id}")
                return None

            try:
                async with aiofiles.open(temp_path, 'wb') as f:
                    async for chunk in source_response.content.iter_chunked(65536):
                        await f.write(chunk)
            finally:
                if source_response and not source_response.closed:
                    try:
                        close_result = source_response.close()
                        if hasattr(close_result, '__await__'):
                            await close_result
                    except Exception:
                        pass

            if not await aio_exists(temp_path):
                logger.warning(f"Source text download failed: {track_id}")
                return None

            # Move temp to final location (atomic)
            await anyio.to_thread.run_sync(shutil.move, str(temp_path), str(final_path))
            logger.info(f"✅ Downloaded source text from S4 to {final_path}: {track_id}")

            # Read back to return the text content
            source_text = await text_storage_service.get_source_text(track_id)
            if source_text:
                logger.info(f"Source text verified: {track_id} ({len(source_text)} chars)")

            return source_text

        except Exception as e:
            logger.warning(f"Failed to download source text from S4: {track_id} - {e}")
            # Cleanup temp file if exists
            try:
                temp_path = text_storage_service._get_text_file_path(track_id).parent / f"source_{track_id}_temp.txt.zst"
                if await aio_exists(temp_path):
                    await aio_unlink(temp_path)
            except Exception:
                pass
            return None

    async def download_voice_audio_file(self, track_id: str, voice: str) -> Optional[Path]:
        """Download voice-specific audio file from TTS package with unique temp path"""
        try:
            from mega_s4_client import mega_s4_client
            if not mega_s4_client._started:
                await mega_s4_client.start()
            
            await aio_mkdir(self.temp_dir)
            
            voice_audio_path = self.tts_package_manager.get_voice_audio_path(track_id, voice)
            unique = uuid4().hex[:6]
            temp_path = self.temp_dir / f"{unique}_tts_{track_id}_{voice}.mp3"
            
            response = await mega_s4_client.download_file_stream(voice_audio_path)
            if not response:
                logger.error(f"Failed to download TTS package audio: {track_id}/{voice}")
                return None
            
            try:
                async with aiofiles.open(temp_path, 'wb') as f:
                    async for chunk in response.content.iter_chunked(65536):
                        await f.write(chunk)
            finally:
                if response and not response.closed:
                    try:
                        close_result = response.close()
                        if hasattr(close_result, '__await__'):
                            await close_result
                    except Exception:
                        pass
            
            if await aio_exists(temp_path) and (await aio_stat(temp_path)).st_size > 0:
                logger.info(f"Downloaded TTS package audio: {track_id}/{voice}")
                return temp_path
            else:
                logger.error(f"TTS package audio download failed: {track_id}/{voice}")
                return None
                
        except Exception as e:
            logger.error(f"Error downloading TTS package audio: {e}")
            return None

    async def _store_regenerated_voice_data(self, track_id: str, voice: str, word_timings: List[Dict], duration: float, db):
        """Store voice data for regenerated voices using UPSERT logic"""
        try:
            logger.info(f"Storing regenerated voice data: {track_id}/{voice}")
            
            try:
                timing_result = await text_storage_service.store_word_timings(track_id, voice, word_timings, db)
                logger.info(f"Word timings stored: {track_id}:{voice}: {timing_result['word_count']} words")
            except Exception as storage_error:
                error_msg = str(storage_error).lower()
                if 'uniqueviolation' in error_msg or 'duplicate key' in error_msg or 'already exists' in error_msg:
                    logger.info(f"Word timings already exist for {track_id}:{voice}, verifying existing data")
                    try:
                        existing_timings = await text_storage_service.get_word_timings(track_id, voice, db)
                        if existing_timings:
                            logger.info(f"Verified existing word timings: {track_id}:{voice}: {len(existing_timings)} words")
                        else:
                            logger.error(f"Database metadata exists but timing file is missing: {track_id}:{voice}")
                            raise storage_error
                    except Exception as verify_error:
                        logger.error(f"Failed to verify existing timings: {verify_error}")
                        raise storage_error
                else:
                    logger.error(f"Non-constraint storage error: {storage_error}")
                    raise storage_error

            if not db:
                return
            
            res = await _exec(db, select(TTSTrackMeta).where(TTSTrackMeta.track_id == track_id))
            track_meta = res.scalar_one_or_none()
            
            if track_meta:
                if voice not in track_meta.available_voices:
                    track_meta.available_voices.append(voice)
                if not track_meta.default_voice:
                    track_meta.default_voice = voice
                track_meta.total_duration = max(track_meta.total_duration, duration)
                track_meta.processing_status = 'ready'
                track_meta.completed_at = datetime.now(timezone.utc)
                track_meta.progress_percentage = 100.0
            
            res = await _exec(
                db,
                select(TTSTextSegment)
                .where(TTSTextSegment.track_id == track_id)
                .order_by(TTSTextSegment.segment_index)
            )
            text_segments = res.scalars().all()
            
            if text_segments:
                first_segment = text_segments[0]
                res = await _exec(
                    db,
                    select(TTSWordTiming).where(
                        TTSWordTiming.segment_id == first_segment.id,
                        TTSWordTiming.voice_id == voice
                    )
                )
                existing_timing = res.scalar_one_or_none()
                
                if existing_timing:
                    existing_timing.word_count = len(word_timings)
                    existing_timing.first_word_time = word_timings[0]['start_time'] if word_timings else 0
                    existing_timing.last_word_time = word_timings[-1]['end_time'] if word_timings else 0
                    logger.info(f"Updated existing timing record: {track_id}:{voice}")
                else:
                    word_timing_record = TTSWordTiming(
                        segment_id=first_segment.id,
                        voice_id=voice,
                        word_count=len(word_timings),
                        first_word_time=word_timings[0]['start_time'] if word_timings else 0,
                        last_word_time=word_timings[-1]['end_time'] if word_timings else 0,
                        timing_data_packed=b'',
                        compressed_timings=None
                    )
                    db.add(word_timing_record)
                    logger.info(f"Created new timing record: {track_id}:{voice}")
            
            await _commit(db)
            logger.info(f"Regenerated voice data stored successfully: {track_id}:{voice}")
            
        except Exception as e:
            logger.error(f"Error storing regenerated voice data: {track_id}:{voice} - {e}")
            if db:
                await _rollback(db)
            raise

    async def upload_tts_media_with_voice(
        self,
        audio_file_path: Path,
        track_id: str,
        voice: str,
        creator_id: int,
        db,
        word_timings: List[Dict] = None,
        is_voice_switch: bool = False,
        use_upsert: bool = False,
        skip_s4_upload: bool = False,
        session_dir: Path = None,
        word_timings_path: Optional[Path] = None,
        pre_extracted_metadata: Optional[Dict] = None,
        lock_already_held: bool = False,
    ) -> Tuple[str, Dict]:
        """
        TTS audio upload - PURE FILE OPERATIONS.

        Does:
        - Copy/move files (or reuse if skipping S4)
        - Upload to S4 (if not skipped)
        - Queue HLS preparation
        - Return URLs

        Does NOT:
        - Update track status (worker does this)
        - Update available_voices (worker does this)
        - Manage locks (simple_track_lock handles all locking)
        - Mark generating/segmenting/complete
        """
        temp_path: Optional[Path] = None

        try:
            # If we’re not uploading to S4, we don’t need timings materialized here
            if skip_s4_upload:
                word_timings = None
                word_timings_path = None

            # Only convert JSONL → list when we will actually upload timings to S4
            if (not skip_s4_upload) and word_timings_path and await aio_exists(word_timings_path) and word_timings is None:
                logger.info(f"Converting streaming timings to list format: {word_timings_path}")
                word_timings = []
                async with aiofiles.open(word_timings_path, 'r') as f:
                    async for line in f:
                        if line.strip():
                            try:
                                word_timings.append(json.loads(line))
                            except json.JSONDecodeError:
                                logger.warning(f"Invalid JSON line: {line[:100]}")
                logger.info(f"Converted {len(word_timings)} word timings from streaming file")

            # Validate word_timings if present
            if word_timings is not None:
                logger.info(f"WORD_TIMINGS-CHECK: Validating word_timings for {track_id}/{voice}")
                if not isinstance(word_timings, list):
                    raise TypeError(f"word_timings must be a list, got {type(word_timings).__name__}.")
                if word_timings and not isinstance(word_timings[0], dict):
                    raise TypeError(f"word_timings items must be dicts, got {type(word_timings[0]).__name__}")
                if word_timings:
                    required_keys = {'word', 'start_time', 'end_time'}
                    missing = required_keys - set(word_timings[0].keys())
                    if missing:
                        raise ValueError(f"word_timings items missing required keys: {missing}")

            logger.info(f"Storage: Processing {track_id} ({voice})")

            # File preparation (reuse or move/copy as needed)
            filename = f"tts_{track_id}_{voice}.mp3"
            dest_path = self.temp_dir / filename
            source_path = Path(audio_file_path)

            if skip_s4_upload:
                # Reuse source audio directly; no extra copy/rename
                temp_path = source_path
                logger.info(f"Reusing source audio without copy: {temp_path}")
            else:
                if source_path.parent == self.temp_dir:
                    await aio_move(source_path, dest_path)  # cheap rename in same FS
                    temp_path = dest_path
                    logger.info(f"Moved (rename) {source_path} -> {dest_path}")
                else:
                    await aio_copy2(source_path, dest_path)
                    temp_path = dest_path
                    logger.info(f"Copied: {source_path} -> {dest_path}")

            file_url = f"{self.audio_url}/{filename}"

            # Extract metadata (or reuse pre-extracted)
            if pre_extracted_metadata is not None:
                extracted_metadata = dict(pre_extracted_metadata)
            else:
                extracted_metadata = await self._extract_tts_metadata(temp_path, word_timings)

            # Upload to S4 (if not skipped)
            if not skip_s4_upload:
                await self._upload_tts_package(
                    track_id, voice, temp_path, word_timings,
                    creator_id, is_voice_switch, db, use_upsert
                )
            else:
                logger.info(f"Skipping S4 upload: {track_id}/{voice}")

            # Queue HLS preparation (lock already held by worker in this flow)
            logger.info(f"Queuing HLS: {track_id} ({voice})")

            stream_manager = get_stream_manager()
            file_size = (await aio_stat(temp_path)).st_size
            priority = await self._determine_priority(file_size)
            job_id = f"{track_id}_voice_{voice}"

            await self.preparation_manager.queue_preparation(
                stream_id=job_id,
                filename=filename,
                prepare_func=lambda f, db=None, task_info=None:
                    stream_manager.prepare_hls(
                        file_path=Path(task_info['temp_path']),
                        filename=f,
                        track_id=task_info['track_id'],
                        db=db,
                        voice=task_info['voice']
                    ),
                file_size=file_size,
                priority=priority,
                db_session=db,
                task_info={
                    'temp_path': str(temp_path),
                    'track_id': track_id,
                    'voice': voice,
                    'file_url': file_url,
                    'metadata': extracted_metadata,
                    'is_voice_switch': is_voice_switch,
                    'skip_s4_upload': skip_s4_upload,
                    'session_dir': str(session_dir) if session_dir else None,
                    'lock_already_held': lock_already_held  # Pass through from caller
                }
            )

            logger.info(f"Storage: Queued HLS for {track_id} ({voice})")

            return file_url, {
                'voice': voice,
                'voice_directory': f'voice-{voice}',
                'is_voice_switch': is_voice_switch,
                'skip_s4_upload': skip_s4_upload,
                'tts_package_path': self.tts_package_manager.get_track_package_path(track_id),
                'job_id': job_id
            }

        except Exception as e:
            logger.error(f"Storage: Upload failed {track_id}/{voice}: {str(e)}")
            # Only unlink our own copy/move; do not delete caller’s original file
            try:
                if temp_path and await aio_exists(temp_path) and temp_path != Path(audio_file_path):
                    await aio_unlink(temp_path)
            except Exception:
                pass
            raise HTTPException(status_code=500, detail=f"TTS upload failed: {str(e)}")


    async def get_media_path(self, file_url: str, voice: Optional[str] = None) -> Path:
        """Get media path with voice support"""
        try:
            clean_url = _strip_url(file_url)
            path = Path(clean_url)
            filename = path.name
            
            if "audio" in file_url.lower():
                stream_manager = get_stream_manager()
                
                base_segments = getattr(
                    stream_manager, 
                    "segment_dir", 
                    Path(os.path.expanduser("~")) / ".hls_streaming/segments"
                )
                
                file_hash = self._get_file_hash(filename)
                
                if voice:
                    segment_path = base_segments / file_hash / f"voice-{voice}"
                else:
                    segment_path = base_segments / file_hash
                
                prep_key = f"{file_hash}:{voice or 'base'}"
                lock = await self._acquire_prep_lock(prep_key)
                
                try:
                    if not await aio_exists(segment_path):
                        downloaded_path = await self.download_audio_file(file_url)
                        if downloaded_path:
                            working_path = downloaded_path
                            try:
                                if voice and hasattr(stream_manager, 'prepare_hls_stream_with_voice'):
                                    await _maybe_await(
                                        stream_manager.prepare_hls_stream_with_voice,
                                        file_path=working_path,
                                        filename=filename,
                                        track_id=file_hash,
                                        voice=voice
                                    )
                                else:
                                    await _maybe_await(
                                        stream_manager.prepare_hls_stream,
                                        file_path=working_path,
                                        filename=filename,
                                        track_id=file_hash
                                    )
                            finally:
                                if await aio_exists(working_path):
                                    await aio_unlink(working_path)
                finally:
                    lock.release()
                    async with self._prep_locks_lock:
                        if not lock.locked():
                            self._prep_locks.pop(prep_key, None)
                            
                return segment_path
            
            cache_path = self.image_cache_dir / filename
            if not await aio_exists(cache_path):
                from mega_s4_client import mega_s4_client
                if not mega_s4_client._started:
                    await mega_s4_client.start()
                object_key = mega_s4_client.generate_object_key(filename, prefix="images")
                response = await mega_s4_client.download_file_stream(object_key)
                if response:
                    try:
                        await aio_mkdir(cache_path.parent)
                        async with aiofiles.open(cache_path, 'wb') as f:
                            async for chunk in response.content.iter_chunked(65536):
                                await f.write(chunk)
                    except Exception as e:
                        logger.error(f"Error caching image: {e}")
                        if await aio_exists(cache_path):
                            await aio_unlink(cache_path)
                        raise
                    finally:
                        try:
                            if response and not response.closed:
                                close_result = response.close()
                                if hasattr(close_result, '__await__'):
                                    await close_result
                        except Exception:
                            pass
                else:
                    logger.error(f"Failed to download image from S4: {filename}")
                    raise FileNotFoundError(f"Image not found in S4: {filename}")
                        
            return cache_path
            
        except Exception as e:
            logger.error(f"Error resolving media path: {e}")
            raise

    def _get_media_type(self, file_path) -> str:
        try:
            if isinstance(file_path, str):
                file_path = Path(file_path)
                
            media_types = {
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.png': 'image/png',
                '.gif': 'image/gif',
                '.webp': 'image/webp',
                '.mp3': 'audio/mpeg',
                '.m4a': 'audio/mp4',
                '.wav': 'audio/wav'
            }
            return media_types.get(file_path.suffix.lower(), 'application/octet-stream')
        except Exception as e:
            logger.error(f"Error determining media type: {e}")
            return 'application/octet-stream'

    async def validate_tts_package_integrity(self, track_id: str, db = None) -> Dict:
        """S4 package integrity validation - database-first approach"""
        try:
            result = {
                'can_regenerate': False,
                'has_source_text': False,
                'voices_complete': {}
            }
            
            try:
                source_text = await text_storage_service.get_source_text(track_id)
                result['has_source_text'] = bool(source_text and source_text.strip())
            except Exception:
                result['has_source_text'] = False
            
            if db:
                try:
                    res = await _exec(
                        db,
                        select(TTSWordTiming)
                        .join(TTSTextSegment, TTSWordTiming.segment_id == TTSTextSegment.id)
                        .where(TTSTextSegment.track_id == track_id)
                    )
                    voice_timings = res.scalars().all()
                    
                    for timing in voice_timings:
                        result['voices_complete'][timing.voice_id] = True
                    
                    result['can_regenerate'] = bool(result['voices_complete']) or result['has_source_text']
                except Exception as db_error:
                    logger.warning(f"Database validation failed: {db_error}")
                    result['can_regenerate'] = result['has_source_text']
            else:
                result['can_regenerate'] = result['has_source_text']
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to validate TTS package integrity: {track_id} - {e}")
            return {
                'can_regenerate': False,
                'has_source_text': False,
                'voices_complete': {},
                'error': str(e)
            }

    async def upload_media(self, file: UploadFile, media_type: str, creator_id: int, db, track_id: Optional[str] = None,
                           lock_preacquired: bool = False) -> Tuple[str, Dict]:
        """
        Audio/image upload pipeline using simple_track_lock.

        For audio with track_id:
          - If lock_preacquired=True: assume caller already holds the DB lock (e.g., finalize path). Do NOT lock here.
          - If lock_preacquired=False: acquire the lock here (writer) and keep it until the background worker releases it.
          - On any early failure (before worker queued), unlock here.

        For images: direct upload, no locking.
        """
        temp_path: Optional[Path] = None
        file_url: Optional[str] = None
        extracted_metadata: Optional[Dict] = None
        metadata_event = asyncio.Event()

        async def _unlock_on_error():
            # Only relevant for audio with a track_id; used when we fail before queuing background worker
            if media_type != "audio" or not track_id:
                return
            try:
                from status_lock import status_lock
                from database import get_db
                s2 = next(get_db())
                try:
                    await status_lock.unlock_voice(track_id, None, success=False, db=s2)
                    logger.info(f"Writer UNLOCKED track {track_id} (error path)")
                finally:
                    try:
                        s2.close()
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"Writer unlock failed (error path): {e}")

        try:
            self._initialize_directories()

            current_time = datetime.now(timezone.utc)
            unique_id = uuid.uuid4().hex[:8]
            extension = Path(file.filename).suffix
            filename = f"{media_type}_{creator_id}_{int(current_time.timestamp() * 1000)}_{unique_id}{extension}"

            temp_path = self.temp_dir / filename

            remote_dir = self.mega_audio_path if media_type == "audio" else self.mega_images_path
            url_prefix = self.audio_url if media_type == "audio" else self.images_url
            file_url = f"{url_prefix}/{filename}"

            if media_type == "audio":
                logger.info(f"Upload started: {filename}")

                # Acquire writer-side lock unless caller already holds it
                if not lock_preacquired:
                    try:
                        from status_lock import status_lock
                        from database import get_db
                        _s = next(get_db())
                        try:
                            locked, reason = await status_lock.try_lock_voice(
                                track_id=track_id,
                                voice_id=None,
                                process_type="initial",
                                db=_s,
                            )
                            if not locked:
                                raise HTTPException(status_code=409, detail=f"Track is busy: {reason}")
                            logger.info(f"Writer ✅ LOCKED track {track_id} (storage.upload_media)")
                        finally:
                            try:
                                _s.close()
                            except Exception:
                                pass
                    except HTTPException:
                        raise
                    except Exception as e:
                        logger.error(f"Writer failed to lock track {track_id}: {e}")
                        raise HTTPException(status_code=500, detail="Failed to lock track")

                async def write_callback(file_id: str, file_info: Dict):
                    logger.info(f"Disk write complete: {track_id}")
                    try:
                        stats = upload_queue.get_stats(file_id)
                        if stats and stats.status == WriteStatus.COMPLETED.value:
                            await metadata_queue.queue_extraction(
                                file_path=temp_path,
                                file_id=track_id,
                                completion_callback=metadata_callback,
                                db=db
                            )
                            return
                        raise Exception("Disk write failed")
                    except Exception as e:
                        logger.error(f"Error in write callback: {e}")
                        await _unlock_on_error()
                        raise

                async def metadata_callback(file_id: str, metadata: Dict, db):
                    logger.info(f"Metadata extracted: {track_id} ({metadata.get('duration', 0):.1f}s)")
                    try:
                        res = await _exec(db, select(Track).where(Track.id == file_id))
                        track = res.scalar_one_or_none()
                        if track:
                            track.duration = metadata.get('duration', 0)
                            await _commit(db)
                        nonlocal extracted_metadata
                        extracted_metadata = metadata
                        metadata_event.set()

                        await mega_upload_manager.queue_upload_with_callback(
                            file_id=track_id,
                            temp_path=temp_path,
                            remote_dir=remote_dir,
                            filename=filename,
                            completion_callback=mega_callback,
                            db=db,
                            track_id=track_id
                        )
                    except Exception as e:
                        logger.error(f"Error in metadata callback: {e}")
                        await _unlock_on_error()
                        raise

                async def mega_callback(file_id: str, mega_info: Dict):
                    logger.info(f"S4 upload complete: {track_id} → queuing HLS")
                    try:
                        stream_manager = get_stream_manager()
                        file_size = (await aio_stat(temp_path)).st_size
                        priority = await self._determine_priority(file_size)

                        await self.preparation_manager.queue_preparation(
                            stream_id=track_id,
                            filename=filename,
                            prepare_func=lambda f, db=None, task_info=None:
                                stream_manager.prepare_hls(
                                    file_path=Path(task_info['temp_path']),
                                    filename=f,
                                    track_id=task_info['track_id'],
                                    db=db
                                ),
                            file_size=file_size,
                            priority=priority,
                            db_session=db,
                            task_info={
                                'temp_path': str(temp_path),
                                'track_id': track_id,
                                'file_url': file_url,
                                'metadata': extracted_metadata,
                                'lock_already_held': True,   # worker will unlock
                            }
                        )
                    except Exception as e:
                        logger.error(f"Error in S4 callback: {e}")
                        await _unlock_on_error()
                        raise

                # Stream file to temp path via upload_queue → metadata → S4 → queue worker
                await upload_queue.queue_upload_with_callback(
                    file=file,
                    path=temp_path,
                    completion_callback=write_callback,
                    file_id=track_id
                )
                return file_url, {}

            else:
                # Image upload (no locking needed)
                logger.info(f"Image upload: {filename}")
                async with aiofiles.open(temp_path, 'wb') as buffer:
                    while True:
                        chunk = await file.read(8192)
                        if not chunk:
                            break
                        await buffer.write(chunk)

                from mega_s4_client import mega_s4_client
                object_key = mega_s4_client.generate_object_key(filename, prefix="images")

                success = await mega_s4_client.upload_file(
                    local_path=temp_path,
                    object_key=object_key,
                    content_type="image/jpeg" if filename.endswith('.jpg') else "image/png"
                )

                if not success:
                    raise RuntimeError("S4 image upload failed")

                cache_path = self.image_cache_dir / filename
                await aio_copy2(temp_path, cache_path)

                logger.info(f"Image upload complete: {filename}")
                return file_url, {}

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Upload failed: {str(e)}")
            if temp_path and await aio_exists(temp_path):
                try:
                    await aio_unlink(temp_path)
                except Exception:
                    pass
            try:
                await _unlock_on_error()
            except Exception:
                pass
            raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


    async def _get_audio_duration(self, audio_path: Path) -> float:
        """Get audio duration"""
        try:
            metadata = await duration_manager._extract_metadata(audio_path)
            return metadata.get('duration', 0.0)
        except Exception:
            return 0.0

    async def _extract_tts_metadata(self, file_path: Path, word_timings: List[Dict] = None) -> Dict:
        """Extract metadata from TTS audio file"""
        try:
            metadata = await duration_manager._extract_metadata(file_path)
            if word_timings:
                metadata['word_timings'] = word_timings
                metadata['word_count'] = len(word_timings)
            metadata['is_tts'] = True
            return metadata
        except Exception as e:
            logger.error(f"TTS metadata extraction failed: {e}")
            return {'duration': 0, 'is_tts': True}

    async def _upload_tts_package(self, track_id: str, voice: str, audio_path: Path, word_timings: List[Dict], creator_id: int, is_voice_switch: bool, db, use_upsert: bool = False):
        """Upload TTS package structure to S4 - streaming only, no memory fallback"""
        logger.info(f"TTS-UPLOAD: Starting upload for {track_id}/{voice} (is_voice_switch={is_voice_switch}, use_upsert={use_upsert})")
        
        try:
            from mega_s4_client import mega_s4_client
            if not mega_s4_client._started:
                logger.info(f"TTS-UPLOAD: Starting mega_s4_client for {track_id}/{voice}")
                await mega_s4_client.start()

            # Create track package if not a voice switch
            if not is_voice_switch:
                logger.info(f"TTS-UPLOAD: Creating track package for {track_id}")
                await self._create_track_package(track_id, creator_id, db)
                logger.info(f"TTS-UPLOAD: Track package created for {track_id}")
            else:
                logger.info(f"TTS-UPLOAD: Skipping track package creation (voice switch)")

            # Handle audio upload
            voice_audio_path = self.tts_package_manager.get_voice_audio_path(track_id, voice)
            logger.info(f"TTS-UPLOAD: Audio S4 path: {voice_audio_path}")
            
            should_upload_audio = True
            if use_upsert:
                logger.info(f"TTS-UPLOAD: Checking if audio already exists in S4: {voice_audio_path}")
                try:
                    audio_exists = await mega_s4_client.object_exists(voice_audio_path)
                    if audio_exists:
                        logger.info(f"TTS-UPLOAD: Audio already exists in S4, skipping upload")
                        should_upload_audio = False
                except Exception as check_error:
                    logger.warning(f"TTS-UPLOAD: Could not check audio existence: {check_error}")
            
            if should_upload_audio:
                logger.info(f"TTS-UPLOAD: Uploading audio file to S4: {audio_path} -> {voice_audio_path}")
                audio_success = await mega_s4_client.upload_file(
                    local_path=audio_path,
                    object_key=voice_audio_path,
                    content_type="audio/mpeg"
                )
                if not audio_success:
                    raise RuntimeError(f"Failed to upload voice audio to {voice_audio_path}")
                logger.info(f"TTS-UPLOAD: Audio upload successful for {track_id}/{voice}")
            else:
                logger.info(f"TTS-UPLOAD: Skipped audio upload (already exists)")

            # Handle timings upload
            if word_timings:
                logger.info(f"TTS-UPLOAD: Processing {len(word_timings)} word timings for {track_id}/{voice}")
                voice_timings_path = self.tts_package_manager.get_voice_timings_path(track_id, voice)
                
                should_upload_timings = True
                if use_upsert:
                    logger.info(f"TTS-UPLOAD: Checking if timings already exist in S4: {voice_timings_path}")
                    try:
                        timings_exists = await mega_s4_client.object_exists(voice_timings_path)
                        if timings_exists:
                            logger.info(f"TTS-UPLOAD: Timings already exist in S4, skipping upload")
                            should_upload_timings = False
                    except Exception as check_error:
                        logger.warning(f"TTS-UPLOAD: Could not check timings existence: {check_error}")
                
                if should_upload_timings:
                    # Write timings to disk via text_storage_service, then upload the file
                    logger.info(f"TTS-UPLOAD: Storing word timings to disk via text_storage_service")
                    try:
                        result = await text_storage_service.store_word_timings(
                            track_id=track_id,
                            voice_id=voice,
                            word_timings=word_timings,
                            db=db
                        )
                        logger.info(f"TTS-UPLOAD: Word timings stored to disk: {result}")
                    except Exception as store_error:
                        logger.error(f"TTS-UPLOAD: Failed to store word timings to disk: {store_error}", exc_info=True)
                        raise RuntimeError(f"Failed to write timings to disk: {store_error}")
                    
                    local_timings_file = text_storage_service._get_timing_file_path(track_id, voice)
                    if not await text_storage_service._exists(local_timings_file):
                        raise RuntimeError(f"Timings file not created: {local_timings_file}")
                    
                    file_size = (await text_storage_service._stat(local_timings_file)).st_size
                    logger.info(f"TTS-UPLOAD: Uploading timings file to S4 ({file_size} bytes) -> {voice_timings_path}")
                    
                    timings_success = await mega_s4_client.upload_file(
                        local_path=local_timings_file,
                        object_key=voice_timings_path,
                        content_type="application/octet-stream"
                    )
                    if not timings_success:
                        raise RuntimeError(f"Failed to upload voice timings to {voice_timings_path}")
                    logger.info(f"TTS-UPLOAD: Timings uploaded successfully")
                else:
                    logger.info(f"TTS-UPLOAD: Skipped timings upload (already exists)")
            else:
                logger.info(f"TTS-UPLOAD: No word timings to upload for {track_id}/{voice}")

            logger.info(f"TTS-UPLOAD: Package upload complete: {track_id}/{voice}")

        except Exception as e:
            logger.error(f"TTS-UPLOAD: Failed to upload TTS package for {track_id}/{voice}: {str(e)}", exc_info=True)
            raise RuntimeError(f"TTS package upload failed for {track_id}/{voice}: {str(e)}")


    async def _create_track_package(self, track_id: str, creator_id: int, db):
        """Create track-level package files"""
        try:
            from mega_s4_client import mega_s4_client
            if not mega_s4_client._started:
                await mega_s4_client.start()
            
            try:
                source_text = await text_storage_service.get_source_text(track_id)
            except Exception:
                source_text = f"TTS Track {track_id}"

            source_text_bytes = source_text.encode('utf-8')
            compressed_source = await anyio.to_thread.run_sync(
                text_storage_service._compress_data, source_text_bytes
            )
            
            source_temp_path = self.temp_dir / f"source_{track_id}.txt.zst"
            async with aiofiles.open(source_temp_path, 'wb') as f:
                await f.write(compressed_source)
            
            source_s4_path = self.tts_package_manager.get_source_text_path(track_id)
            source_success = await mega_s4_client.upload_file(
                local_path=source_temp_path,
                object_key=source_s4_path,
                content_type="application/octet-stream"
            )
            if not source_success:
                raise RuntimeError("Failed to upload source text")

            res = await _exec(db, select(Track).where(Track.id == track_id))
            track = res.scalar_one_or_none()
            track_metadata = {
                'track_id': track_id,
                'title': track.title if track else f"TTS Track {track_id}",
                'creator_id': creator_id,
                'created_at': datetime.now(timezone.utc).isoformat(),
                'track_type': 'tts',
                'source_text_size': len(source_text_bytes),
                'source_text_compressed_size': len(compressed_source)
            }
            
            metadata_temp_path = self.temp_dir / f"metadata_{track_id}.json"
            async with aiofiles.open(metadata_temp_path, 'w') as f:
                await f.write(json.dumps(track_metadata, indent=2))
            
            metadata_s4_path = self.tts_package_manager.get_track_metadata_path(track_id)
            metadata_success = await mega_s4_client.upload_file(
                local_path=metadata_temp_path,
                object_key=metadata_s4_path,
                content_type="application/json"
            )
            if not metadata_success:
                raise RuntimeError("Failed to upload track metadata")

            await aio_unlink(source_temp_path)
            await aio_unlink(metadata_temp_path)
            
            logger.info(f"Track package created: {track_id}")

        except Exception as e:
            logger.error(f"Failed to create track package: {e}")
            raise

    async def download_audio_file(self, file_url: str, track_progress: bool = False) -> Optional[Path]:
        """Download audio file from S4 with unique temp path to avoid collisions"""
        file_url = _strip_url(file_url)
        filename = Path(file_url).name
        unique = uuid4().hex[:6]
        temp_path = self.temp_dir / f"{unique}_{filename}"
        file_hash = self._get_file_hash(filename)

        try:
            await aio_mkdir(self.temp_dir)

            logger.info(f"Downloading from S4: {filename}")
            
            if track_progress:
                if len(self.download_progress) >= self._max_progress_entries:
                    oldest_keys = list(self.download_progress.keys())[:10]
                    for key in oldest_keys:
                        self.download_progress.pop(key, None)
                
                self.download_progress[file_hash] = {
                    "status": "starting",
                    "percentage": 0,
                    "current_size": 0,
                    "total_size": 0
                }

            from mega_s4_client import mega_s4_client
            if not mega_s4_client._started:
                await mega_s4_client.start()
            
            object_key = mega_s4_client.generate_object_key(filename, prefix="audio")
            response = await mega_s4_client.download_file_stream(object_key)
            if not response:
                logger.error(f"Failed to start S4 download for {filename}")
                if track_progress:
                    self.download_progress.pop(file_hash, None)
                return None

            try:
                cl = None
                try:
                    cl = response.headers.get('Content-Length') or response.headers.get('content-length')
                except Exception:
                    pass
                total = int(cl) if cl else 0
                if track_progress:
                    self.download_progress[file_hash]['total_size'] = total
                
                current_size = 0
                async with aiofiles.open(temp_path, 'wb') as f:
                    try:
                        async for chunk in response.content.iter_chunked(65536):
                            await f.write(chunk)
                            current_size += len(chunk)
                            if track_progress:
                                self.download_progress[file_hash].update({
                                    "current_size": current_size,
                                    "status": "downloading"
                                })
                                if total > 0:
                                    self.download_progress[file_hash]["percentage"] = int(100 * current_size / total)
                    except Exception as read_error:
                        logger.error(f"Error reading chunk: {read_error}")
                        raise
            except Exception as download_error:
                logger.error(f"Download error: {download_error}")
                if track_progress:
                    self.download_progress.pop(file_hash, None)
                return None
            finally:
                try:
                    if response and not response.closed:
                        close_result = response.close()
                        if hasattr(close_result, '__await__'):
                            await close_result
                except Exception as close_error:
                    logger.warning(f"Error closing response: {close_error}")

            if not await aio_exists(temp_path) or (await aio_stat(temp_path)).st_size == 0:
                logger.error(f"S4 download failed for {filename}")
                if track_progress:
                    self.download_progress.pop(file_hash, None)
                return None

            if track_progress:
                self.download_progress.pop(file_hash, None)

            logger.info(f"Download complete: {filename} ({(await aio_stat(temp_path)).st_size} bytes)")
            return temp_path

        except Exception as e:
            logger.error(f"Error downloading audio file: {str(e)}")
            if track_progress and file_hash in self.download_progress:
                self.download_progress.pop(file_hash, None)
            return None

    def _get_file_hash(self, filename: str) -> str:
        import hashlib
        return hashlib.sha256(filename.encode()).hexdigest()

    async def delete_media(self, file_url: str, track_id: str = None) -> bool:
        try:
            path = Path(file_url)
            filename = path.name
            is_image = "images" in file_url.lower()
            
            if filename.startswith("tts_") and track_id:
                logger.info(f"Deleting TTS package for track: {track_id}")
                return await self.delete_all_tts_voices_for_track(track_id)

            from mega_s4_client import mega_s4_client
            prefix = "images" if is_image else "audio"
            object_key = mega_s4_client.generate_object_key(filename, prefix=prefix)

            success = await mega_s4_client.delete_object(object_key)
            if not success:
                logger.error(f"Failed to delete {object_key} from S4")
                return False

            if is_image:
                cache_path = self.image_cache_dir / filename
                if await aio_exists(cache_path):
                    try:
                        await aio_unlink(cache_path)
                    except Exception as e:
                        logger.error(f"Error deleting from cache: {e}")

            return True

        except Exception as e:
            logger.error(f"Delete operation failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def delete_all_tts_voices_for_track(self, track_id: str, track: Track = None, db = None) -> bool:
        """Delete complete TTS package from S4 + cancel active TTS generation"""
        try:
            from mega_s4_client import mega_s4_client
            if not mega_s4_client._started:
                await mega_s4_client.start()
            
            # Cancel active TTS generation
            try:
                from enhanced_tts_voice_service import enhanced_voice_tts_service
                for user_id, limiter in enhanced_voice_tts_service.user_job_manager._user_limiters.items():
                    jobs_to_cancel = []
                    async with limiter._condition:
                        for job_id in limiter.desired.keys():
                            job_info = enhanced_voice_tts_service.user_job_manager._user_jobs.get(user_id, {}).get(job_id)
                            if job_info and job_info.get('track_id') == track_id:
                                jobs_to_cancel.append((user_id, job_id))
                    for user_id, job_id in jobs_to_cancel:
                        try:
                            logger.info(f"Cancelling TTS generation for deleted track: {track_id}, job: {job_id}")
                            await enhanced_voice_tts_service.cancel_job(job_id, track_id)
                        except Exception as job_cancel_error:
                            logger.warning(f"Could not cancel TTS job {job_id} for track {track_id}: {job_cancel_error}")
            except Exception as cancel_error:
                logger.warning(f"Could not cancel TTS jobs for {track_id}: {cancel_error}")
            
            # Delete S4 storage
            logger.info(f"Deleting complete TTS package folder: {track_id}")
            deleted_files = []
            errors = []
            package_path = self.tts_package_manager.get_track_package_path(track_id)
            
            try:
                objects = await mega_s4_client.list_objects(prefix=package_path)
                if objects:
                    logger.info(f"Found {len(objects)} objects to delete in {package_path}")
                    for obj in objects:
                        try:
                            obj_key = obj['key'] if isinstance(obj, dict) else obj
                            if await mega_s4_client.delete_object(obj_key):
                                deleted_files.append(obj_key)
                        except Exception as obj_error:
                            obj_key_str = obj.get('key', str(obj)) if isinstance(obj, dict) else str(obj)
                            errors.append(f"Failed to delete {obj_key_str}: {str(obj_error)}")
                else:
                    logger.warning(f"No objects found in TTS package folder: {package_path}")
            except Exception as list_error:
                logger.error(f"Failed to list objects in {package_path}: {list_error}")
                errors.append(f"Listing error: {str(list_error)}")
            
            # Delete regular audio file
            if track and track.file_path:
                try:
                    await self.delete_media(track.file_path)
                    logger.info(f"Deleted regular audio file: {track.file_path}")
                    deleted_files.append(track.file_path)
                except Exception as e:
                    errors.append(f"Regular audio file deletion error: {str(e)}")
            
            logger.info(f"TTS package deletion for {track_id}: {len(deleted_files)} files deleted, {len(errors)} errors")
            if errors:
                logger.warning(f"TTS package deletion errors for {track_id}: {errors}")
            return len(deleted_files) > 0
            
        except Exception as e:
            logger.error(f"Failed to delete TTS package for {track_id}: {str(e)}")
            return False
    
    async def cleanup(self):
        try:
            if await aio_exists(self.temp_dir):
                await aio_rmtree(self.temp_dir)
                await aio_mkdir(self.temp_dir)
        except Exception as e:
            logger.error(f"Error during MediaStorage cleanup: {e}")

    async def check_upload_lock(self, track_id: str, voice_id: Optional[str] = None) -> Optional[Dict]:
        """
        Check if the specified track has an upload lock

        Returns:
            Dict with lock info if locked, None otherwise
        """
        try:
            from status_lock import status_lock
            from database import get_db

            db = next(get_db())
            try:
                is_locked, lock_type = await status_lock.is_voice_locked(track_id, voice_id, db)
                if is_locked:
                    return {
                        'status': 'locked',
                        'phase': lock_type or 'processing',
                        'lock_type': lock_type
                    }
                return None
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Error checking upload lock for {track_id}: {e}")
            return None

storage = MediaStorage()

