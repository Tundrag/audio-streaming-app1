# hls_streaming.py - Voice-aware progress implementation

import asyncio
import logging
import shutil
import aiofiles
import json
import time
import os
import math

from pathlib import Path
from typing import Dict, Optional, List
from fastapi import HTTPException
from datetime import datetime
from sqlalchemy.orm import Session
from database import get_db
import numpy as np

from hls_core import (
    EnterpriseHLSManager, BaseHLSManager, DatabaseExecutor, SimpleFileCache,
    _get_track_by_id, _update_track_status, _delete_segment_metadata, _rollback_db,
    _get_file_hash, SEGMENT_DURATION, DEFAULT_BITRATE,
    BASE_DIR, SEGMENT_DIR, TEMP_DIR
)
from models import Track
from storage import storage as storage_manager
from text_storage_service import text_storage_service, TextStorageError

logger = logging.getLogger(__name__)

class StreamManager:
    def __init__(self):
        self.hls_manager = EnterpriseHLSManager()
        self.storage_manager = storage_manager
        self.track_regeneration_locks = {}
        self.track_lock_creation = asyncio.Lock()

    async def initialize(self):
        try:
            for path in [self.hls_manager.segment_dir, self.hls_manager.temp_dir]:
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda p=path: p.mkdir(parents=True, exist_ok=True)
                )
            logger.info("StreamManager initialized without Redis")
        except Exception as e:
            logger.error(f"Stream manager initialization error: {str(e)}")
            raise

    async def get_stream_response(
        self,
        filename: str,
        track_id: str,
        specific_segment_id: Optional[int] = None,
        voice: str = None,
        skip_lock_check: bool = False
    ):
        """
        Get stream response with deadlock prevention.
        
        Args:
            filename: Source audio filename
            track_id: Track ID
            specific_segment_id: Optional specific segment to regenerate
            voice: Optional voice ID for TTS
            skip_lock_check: If True, skip lock check (caller already holds lock)
        """
        # LOG: Entry point verification
        logger.info(f"🔄 get_stream_response: track={track_id}, voice={voice}, skip_lock_check={skip_lock_check}")
        
        async with self.track_lock_creation:
            self.track_regeneration_locks.setdefault(track_id, asyncio.Lock())

        async with self.track_regeneration_locks[track_id]:
            db = None
            try:
                db = next(get_db())

                track = await DatabaseExecutor.execute(lambda: _get_track_by_id(db, track_id))
                if not track:
                    raise HTTPException(status_code=404, detail="Track not found")

                worker_status = self.hls_manager.preparation_manager.get_status(track_id)
                if worker_status and worker_status["status"] in {"queued", "processing", "creating_segments"}:
                    raise HTTPException(
                        status_code=202,
                        detail="Track is being prepared",
                        headers={"Retry-After": "10"}
                    )

                track_type = getattr(track, 'track_type', 'audio')

                if track_type == 'tts' and voice:
                    return await self._handle_tts_stream_response(
                        track_id, voice, specific_segment_id, db, track, skip_lock_check
                    )
                else:
                    return await self._handle_regular_stream_response(
                        track_id, filename, specific_segment_id, db, track, skip_lock_check
                    )

            except HTTPException:
                raise
            except Exception as err:
                if db:
                    try:
                        await DatabaseExecutor.execute(lambda: db.rollback())
                    except Exception:
                        pass
                logger.error(f"Stream response error for {track_id}: {err}")
                raise HTTPException(status_code=500, detail="Streaming error")
            finally:
                if db:
                    try:
                        await DatabaseExecutor.execute(lambda: db.close())
                    except Exception:
                        pass


    async def _handle_tts_stream_response(
        self,
        track_id: str,
        voice: str,
        specific_segment_id: Optional[int],
        db: Session,
        track: Track,
        skip_lock_check: bool = False
    ):
        """Handle TTS stream response with proper locking to prevent concurrent regenerations."""
        logger.info(f"🎤 TTS handler: track={track_id}, voice={voice}, skip_lock_check={skip_lock_check}")

        voice_stream_dir = self.hls_manager.segment_dir / track_id / f"voice-{voice}"
        variant_dir = voice_stream_dir / "default"
        master_playlist = voice_stream_dir / "master.m3u8"
        variant_playlist = variant_dir / "playlist.m3u8"
        index_path = voice_stream_dir / "index.json"
        voice_cache_key = f"{track_id}:voice:{voice}"

        # -------- Determine if (re)generation is needed --------
        needs_regen = False
        master_ok, variant_ok, index_ok = await asyncio.get_event_loop().run_in_executor(
            None, lambda: (master_playlist.exists(), variant_playlist.exists(), index_path.exists())
        )
        logger.info(f"🔍 HLS-CHECK: Files exist - master={master_ok}, variant={variant_ok}, index={index_ok}")

        if not (master_ok and variant_ok and index_ok):
            needs_regen = True
            logger.info(f"TTS HLS files missing for {track_id}/{voice}")
        else:
            complete_master, complete_variant = await asyncio.gather(
                self._is_playlist_complete(master_playlist),
                self._is_playlist_complete(variant_playlist)
            )
            if not (complete_master and complete_variant):
                needs_regen = True
                logger.info(f"TTS HLS playlists incomplete for {track_id}/{voice}")

        if (
            not needs_regen
            and specific_segment_id is not None
            and not await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: (variant_dir / f"segment_{specific_segment_id:05d}.m4s").exists()
                     or (variant_dir / f"segment_{specific_segment_id:05d}.ts").exists()
            )
        ):
            needs_regen = True
            logger.info(f"TTS segment missing: {track_id}/{voice}#{specific_segment_id}")

        logger.info(f"🔍 HLS-CHECK: needs_regen={needs_regen} for {track_id}/{voice}")

        # -------- Fast path: already ready --------
        if not needs_regen:
            meta = await self.hls_manager.cache.get_metadata(voice_cache_key)
            if not meta:
                meta = {
                    'status': 'ready',
                    'voice': voice,
                    'voice_directory': f'voice-{voice}',
                }
            meta['cache_type'] = 'file_based'
            return meta

        # -------- Regeneration path --------
        from storage import storage
        if skip_lock_check:
            # Caller already holds the DB-backed track lock
            # Lock will be handed off to background worker via lock_already_held=True
            logger.info(f"⏩ Skipping lock acquisition for {track_id}/{voice} (caller holds lock)")
            logger.info(f"🔍 HLS-REGEN: Calling regenerate_voice_efficiently for {track_id}/{voice}")
            success = await storage.regenerate_voice_efficiently(
                track_id=track_id,
                voice=voice,
                creator_id=track.created_by_id,
                db=db
            )
            logger.info(f"🔍 HLS-REGEN: Regeneration queued, success={success} for {track_id}/{voice}")

            if not success:
                # Failed to queue - unlock since worker won't run
                from status_lock import status_lock
                await status_lock.unlock_voice(track_id, voice, success=False, db=db)

                # Mark voice generation failed
                from voice_cache_manager import voice_cache_manager
                await voice_cache_manager.mark_voice_failed(track_id, voice, "Failed to queue regeneration", db)

                raise HTTPException(status_code=500, detail=f"Voice {voice} regeneration failed to queue")

            # Work is queued with lock_already_held=True
            # Worker will process, set status, and unlock
            # Return HTTP 202 immediately - client will poll
            logger.info(f"✅ Voice regen queued for {track_id}/{voice} - returning 202")
            raise HTTPException(
                status_code=202,
                detail=f"Voice {voice} HLS regeneration queued",
                headers={"Retry-After": "5", "X-Voice-ID": voice}
            )

        # Acquire the lock here to serialize concurrent voice regenerations for this track
        from status_lock import status_lock
        locked, reason = await status_lock.try_lock_voice(
            track_id=track_id,
            voice_id=voice,
            process_type='voice_regeneration',
            db=db
        )
        if not locked:
            logger.info(f"🚧 Track {track_id} busy: {reason}")
            raise HTTPException(
                status_code=202,
                detail=f"Voice processing in progress: {reason}",
                headers={"Retry-After": "5", "X-Voice-ID": voice}
            )

        # Lock acquired - queue regeneration to background worker
        logger.info(f"🔒 Voice regen lock acquired for {track_id}/{voice}")
        try:
            logger.info(f"🔍 HLS-REGEN: Calling regenerate_voice_efficiently for {track_id}/{voice}")
            success = await storage.regenerate_voice_efficiently(
                track_id=track_id,
                voice=voice,
                creator_id=track.created_by_id,
                db=db
            )
            logger.info(f"🔍 HLS-REGEN: Regeneration queued, success={success} for {track_id}/{voice}")

            if not success:
                # Failed to queue - unlock since worker won't run
                await status_lock.unlock_voice(track_id, voice, success=False, db=db)

                # Mark voice generation failed
                from voice_cache_manager import voice_cache_manager
                await voice_cache_manager.mark_voice_failed(track_id, voice, "Failed to queue regeneration", db)

                raise HTTPException(status_code=500, detail=f"Voice {voice} regeneration failed to queue")

            # Work is queued with lock_already_held=True
            # Worker will process, set status, and unlock
            # Return HTTP 202 immediately - client will poll
            logger.info(f"✅ Voice regen queued for {track_id}/{voice} - returning 202")
            raise HTTPException(
                status_code=202,
                detail=f"Voice {voice} HLS regeneration queued",
                headers={"Retry-After": "5", "X-Voice-ID": voice}
            )

        except HTTPException:
            # HTTPException 202 is expected - propagate it
            raise
        except Exception as e:
            # Unexpected error - unlock since worker won't run
            logger.error(f"TTS stream response error for {track_id}/{voice}: {e}", exc_info=True)
            await status_lock.unlock_voice(track_id, voice, success=False, db=db)

            # Mark voice generation failed
            from voice_cache_manager import voice_cache_manager
            await voice_cache_manager.mark_voice_failed(track_id, voice, str(e), db)

            raise HTTPException(status_code=500, detail="TTS streaming error")




    async def _handle_regular_stream_response(
        self,
        track_id: str,
        filename: str,
        specific_segment_id: Optional[int],
        db: Session,
        track: Track,
        skip_lock_check: bool = False
    ):
        """Handle regular audio stream response with **proper locking** to avoid races."""
        logger.info(f"🎵 Regular audio handler: track={track_id}, skip_lock_check={skip_lock_check}")

        stream_dir = self.hls_manager.segment_dir / track_id
        variant_dir = stream_dir / 'default'
        master_playlist = stream_dir / "master.m3u8"
        variant_playlist = variant_dir / "playlist.m3u8"
        index_path = stream_dir / "index.json"

        # ---------- Determine if regeneration is needed ----------
        needs_regen = False
        master_ok, variant_ok, index_ok = await asyncio.get_event_loop().run_in_executor(
            None, lambda: (master_playlist.exists(), variant_playlist.exists(), index_path.exists())
        )

        if not (master_ok and variant_ok):
            needs_regen = True
        else:
            complete_master, complete_variant = await asyncio.gather(
                self._is_playlist_complete(master_playlist),
                self._is_playlist_complete(variant_playlist)
            )
            if not (complete_master and complete_variant):
                needs_regen = True

        if (
            not needs_regen
            and specific_segment_id is not None
            and not await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: (variant_dir / f"segment_{specific_segment_id:05d}.m4s").exists()
                        or (variant_dir / f"segment_{specific_segment_id:05d}.ts").exists()
            )
        ):
            needs_regen = True

        # ---------- If no regeneration needed, return cache/meta ----------
        if not needs_regen:
            meta = await self.hls_manager.cache.get_metadata(track_id)
            if not meta:
                meta = {'status': 'ready'}
            meta['cache_type'] = 'file_based'
            return meta

        # ---------- Regeneration path ----------
        # Both Case A and Case B now queue to background workers instead of processing synchronously

        from status_lock import status_lock

        # Case B: Acquire lock if caller doesn't hold it
        lock_acquired_here = False
        if not skip_lock_check:
            locked, reason = await status_lock.try_lock_voice(
                track_id=track_id,
                voice_id=None,
                process_type='regeneration',
                db=db
            )
            if not locked:
                logger.info(f"🚧 Track {track_id} busy: {reason}")
                raise HTTPException(
                    status_code=202,
                    detail=f"Track is being processed: {reason}",
                    headers={"Retry-After": "5"}
                )
            lock_acquired_here = True
            logger.info(f"🔒 Regeneration lock acquired for {track_id}")
        else:
            # Case A: Caller already holds lock
            logger.info(f"⏩ Using existing lock for {track_id} (caller holds lock)")

        # Queue regeneration to background workers (both cases)
        try:
            # Estimate file size for priority determination
            # Use track metadata if available, otherwise use default
            file_size = 10 * 1024 * 1024  # Default 10MB estimate
            if track and hasattr(track, 'duration') and track.duration:
                # Rough estimate: 128kbps = 16KB/s
                file_size = int(track.duration * 16 * 1024)

            # Determine priority based on file size
            if file_size < 5 * 1024 * 1024:  # < 5MB
                priority = 1
            elif file_size < 20 * 1024 * 1024:  # < 20MB
                priority = 2
            else:
                priority = 3

            # Create prepare_func that downloads from S4 and prepares HLS
            # Note: This is a closure that captures self
            stream_manager_self = self  # Capture self for use in closure

            async def regeneration_prepare_func(f, db=None, task_info=None):
                """Downloads from S4 and prepares HLS stream for regeneration"""
                regen_filename = task_info.get('filename')
                regen_track_id = task_info.get('track_id')

                logger.info(f"Background worker: Starting regeneration for {regen_track_id}")

                # Download from S4
                from mega_s4_client import mega_s4_client
                if not mega_s4_client._started:
                    await mega_s4_client.start()
                    await asyncio.sleep(1)

                temp_path = await stream_manager_self.storage_manager.download_audio_file(regen_filename)
                if not temp_path or not await asyncio.get_event_loop().run_in_executor(None, lambda: temp_path.exists()):
                    raise ValueError(f"Failed to download audio file from storage: {regen_filename}")

                logger.info(f"Background worker: Downloaded {regen_filename} to {temp_path}")

                # Prepare HLS stream
                result = await stream_manager_self.hls_manager.prepare_hls_stream(
                    file_path=temp_path,
                    filename=regen_filename,
                    track_id=regen_track_id,
                    db=db
                )

                logger.info(f"Background worker: HLS preparation complete for {regen_track_id}")
                return result

            # Queue to background workers
            await self.hls_manager.preparation_manager.queue_preparation(
                stream_id=track_id,
                filename=filename,
                prepare_func=regeneration_prepare_func,
                file_size=file_size,
                priority=priority,
                db_session=db,
                task_info={
                    'track_id': track_id,
                    'filename': filename,
                    'lock_already_held': True,  # Worker will unlock after completion
                    'is_regeneration': True
                }
            )

            logger.info(f"✅ Queued regeneration for {track_id} (priority={priority}, lock_held={'caller' if skip_lock_check else 'self'})")

            # Return HTTP 202 to indicate processing has been queued
            raise HTTPException(
                status_code=202,
                detail="Track regeneration queued",
                headers={
                    "Retry-After": "10",
                    "X-Processing": "regeneration"
                }
            )

        except HTTPException:
            # HTTPException 202 is expected - propagate it
            raise
        except Exception as e:
            # Queueing failed - unlock if we acquired the lock
            logger.error(f"Failed to queue regeneration for {track_id}: {e}", exc_info=True)
            if lock_acquired_here:
                try:
                    await status_lock.unlock_voice(track_id, None, success=False, db=db)
                    logger.info(f"🔓 Released lock after queueing failure for {track_id}")
                except Exception as unlock_err:
                    logger.warning(f"Unlock failed for {track_id}: {unlock_err}")
            raise HTTPException(status_code=500, detail="Failed to queue regeneration")


    async def get_segment_index(self, track_id: str, voice_id: Optional[str] = None) -> Dict:
        base = self.hls_manager.segment_dir / track_id
        if voice_id:
            base = base / f"voice-{voice_id}"
        idx_path = base / "index.json"
        
        try:
            async with aiofiles.open(idx_path, "r") as f:
                return json.loads(await f.read())
        except Exception:
            variant = base / "default" / "playlist.m3u8"
            if not variant.exists():
                return {"durations": [], "starts": [], "nominal": self.hls_manager.default_bitrate["segment_duration"], "measured": False}
            
            try:
                async with aiofiles.open(variant, "r") as f:
                    content = await f.read()
                
                durs = []
                for line in content.splitlines():
                    if line.startswith("#EXTINF:"):
                        try:
                            val = line.split(":")[1].split(",")[0]
                            durs.append(float(val))
                        except Exception:
                            pass
                
                starts, acc = [], 0.0
                for d in durs:
                    starts.append(acc)
                    acc += d
                
                return {"durations": durs, "starts": starts, "nominal": self.hls_manager.default_bitrate["segment_duration"], "total_duration": acc, "measured": True}
            except Exception:
                return {"durations": [], "starts": [], "nominal": self.hls_manager.default_bitrate["segment_duration"], "measured": False}

    async def get_words_for_segment_precise(self, track_id: str, voice_id: str, segment_index: Optional[int], db: Session) -> List[Dict]:
        try:
            try:
                all_word_timings = await text_storage_service.get_word_timings(track_id, voice_id, db)
                if not all_word_timings:
                    return []
                
                if segment_index is not None:
                    segment_words = [
                        word for word in all_word_timings 
                        if word.get('segment_index') == segment_index
                    ]
                    return segment_words
                else:
                    return all_word_timings
                    
            except TextStorageError as e:
                logger.error(f"File storage error getting word timings for {track_id}:{voice_id}: {e}")
                return []
            
        except Exception as e:
            logger.error(f"Error getting word timings from file storage: {str(e)}")
            return []

    async def get_word_switching_quality(self, track_id: str, voice_id: str) -> Dict:
        try:
            word_timings = await text_storage_service.get_word_timings(track_id, voice_id)
            
            if not word_timings:
                return {
                    'track_id': track_id,
                    'voice_id': voice_id,
                    'supports_precision_switching': False,
                    'mapping_coverage': 0,
                    'quality_score': 0.0,
                    'measured_durations_used': False,
                    'precision_processing': False,
                    'error': 'No word timings found in file storage',
                    'storage_type': 'file_based'
                }
            
            total_words = len(word_timings)
            words_with_segments = len([w for w in word_timings if 'segment_index' in w])
            mapping_coverage = (words_with_segments / total_words * 100) if total_words > 0 else 0
            
            has_segment_offsets = any('segment_offset' in w for w in word_timings)
            
            return {
                'track_id': track_id,
                'voice_id': voice_id,
                'supports_precision_switching': mapping_coverage > 80,
                'mapping_coverage': mapping_coverage,
                'quality_score': mapping_coverage / 100.0,
                'measured_durations_used': has_segment_offsets,
                'precision_processing': True,
                'total_words': total_words,
                'mapped_words': words_with_segments,
                'storage_type': 'file_based'
            }
            
        except Exception as e:
            logger.error(f"Error assessing word switching quality: {str(e)}")
            return {
                'track_id': track_id,
                'voice_id': voice_id,
                'supports_precision_switching': False,
                'mapping_coverage': 0,
                'quality_score': 0.0,
                'measured_durations_used': False,
                'precision_processing': False,
                'error': str(e),
                'storage_type': 'file_based'
            }

    async def supports_word_level_switching(self, track_id: str, voice_id: str) -> bool:
        try:
            quality = await self.get_word_switching_quality(track_id, voice_id)
            return quality.get('supports_precision_switching', False)
        except Exception:
            return False

    async def _get_duration_from_database(self, track_id: str, db: Session, voice_id: Optional[str] = None) -> float:
        try:
            from duration_manager import duration_manager
            
            if voice_id:
                duration = await duration_manager.get_duration(track_id, db, voice_id=voice_id)
                if duration > 0:
                    return duration
            
            duration = await duration_manager.get_duration(track_id, db)
            return duration if duration > 0 else 0.0
                
        except Exception as e:
            logger.error(f"Error getting duration from database: {str(e)}")
            return 0.0

    async def _extract_duration_with_fallback(self, file_path: Path, track_id: str, db: Session, voice_id: Optional[str] = None) -> float:
        try:
            if db:
                duration = await self._get_duration_from_database(track_id, db, voice_id)
                if duration > 0:
                    return duration
                    
            from duration_manager import duration_manager
            metadata = await duration_manager._extract_metadata(file_path)
            if metadata:
                return float(metadata['duration'])
            else:
                raise ValueError("Could not extract duration from file")
                
        except Exception as e:
            logger.error(f"Error getting duration: {str(e)}")
            raise ValueError(f"Failed to get duration for track {track_id}")

    async def prepare_hls_stream_with_voice(
            self,
            file_path: Path,
            filename: str,
            track_id: str,
            voice: str,
            db: Optional[Session] = None
        ) -> Dict:
            """
            Create HLS for a specific TTS voice with sharded word timing storage.
            Uses Phase 3 append-only shards for memory-efficient word mapping.
            """
            try:
                logger.info(f"Voice HLS preparation: {track_id} ({voice})")

                progress_key = self.hls_manager._get_progress_key(track_id, voice)
                await self.hls_manager.clear_segment_progress(progress_key)

                track_dir = self.hls_manager.segment_dir / track_id
                voice_stream_dir = track_dir / f"voice-{voice}"
                variant_dir = voice_stream_dir / self.hls_manager.default_bitrate["name"]
                master_playlist_path = voice_stream_dir / "master.m3u8"
                index_path = voice_stream_dir / "index.json"

                master_ok, index_ok = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: (master_playlist_path.exists(), index_path.exists())
                )
                
                if master_ok and index_ok:
                    playlist_complete = await self._is_playlist_complete(master_playlist_path)
                    segments_exist = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: list(variant_dir.glob("segment_*.ts"))
                    )
                    
                    if playlist_complete and segments_exist:
                        logger.info(f"Using existing voice stream: {track_id} ({voice})")

                        initial_duration = await self.hls_manager._extract_duration_with_fallback(
                            file_path, track_id, db, voice
                        )

                        segment_index = await self.get_segment_index(track_id, voice)
                        measured_durations = segment_index.get("durations", [])
                        total_duration = segment_index.get("total_duration", initial_duration)

                        supports_word_switching = await self.supports_word_level_switching(track_id, voice)
                        quality_info = await self.get_word_switching_quality(track_id, voice)

                        return {
                            "stream_id": track_id,
                            "voice": voice,
                            "voice_directory": f"voice-{voice}",
                            "duration": total_duration,
                            "initial_duration": initial_duration,
                            "ready": True,
                            "segment_duration": self.hls_manager.default_bitrate["segment_duration"],
                            "total_segments": len(segments_exist),
                            "measured_segment_durations": measured_durations,
                            "has_measured_durations": bool(measured_durations),
                            "optimized_single_pass": True,
                            "segments_found": len(segments_exist),
                            "status": "existing_stream_used",
                            "supports_word_level_switching": supports_word_switching,
                            "word_switching_quality": quality_info,
                            "precision_switching_ready": quality_info.get("supports_precision_switching", False),
                            "cache_type": "file_based",
                        }

                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: voice_stream_dir.mkdir(parents=True, exist_ok=True)
                )
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: variant_dir.mkdir(parents=True, exist_ok=True)
                )

                initial_duration = await self.hls_manager._extract_duration_with_fallback(
                    file_path, track_id, db, voice
                )
                segment_duration = self.hls_manager.default_bitrate["segment_duration"]
                expected_segments = int(math.ceil(initial_duration / segment_duration))

                self.hls_manager.segment_progress[progress_key] = {
                    "total": expected_segments,
                    "current": 0,
                    "status": "initializing",
                    "voice": voice,
                    "optimized_single_pass": True,
                    "precision_mapping_enabled": self.hls_manager.enable_word_level_mapping,
                    "cache_type": "file_based",
                }

                await self.hls_manager._create_master_playlist(
                    voice_stream_dir, [self.hls_manager.default_bitrate]
                )

                logger.info(f"Direct single-pass voice segmentation: {voice}")
                playlist_path = await self.hls_manager._hls_from_source_direct_with_progress(
                    source_path=file_path,
                    variant_dir=variant_dir,
                    playlist_name="playlist.m3u8",
                    segment_duration=segment_duration,
                    total_duration=initial_duration,
                    progress_key=progress_key,
                )

                measured_durations = self.hls_manager._parse_m3u8_durations(playlist_path)
                logger.info(f"Voice playlist: {len(measured_durations)} measured durations")

                boundaries, t = [], 0.0
                for i, d in enumerate(measured_durations):
                    boundaries.append({"index": i, "start": t, "end": t + d, "duration": d})
                    t += d

                words_mapped = 0
                if self.hls_manager.enable_word_level_mapping and db and track_id:
                    logger.info(f"[Word Mapping] Starting: {track_id}/{voice}")
                    
                    word_timings = await self.hls_manager._get_word_timings_for_segmentation(track_id, voice, db)
                    
                    if not word_timings:
                        logger.warning(f"[Word Mapping] No timings found in file storage: {track_id}/{voice}")
                        timing_path = self.hls_manager.segment_dir / track_id / f"voice-{voice}" / "timings.zst"
                        logger.warning(f"[Word Mapping] Expected path: {timing_path}")
                    
                    if word_timings:
                        logger.info(f"[Word Mapping] Found {len(word_timings)} words to map")
                        
                        try:
                            mapped_count = await self.hls_manager._map_words_to_segments_precise(
                                track_id=track_id,
                                voice_id=voice,
                                word_timings=word_timings,
                                segment_boundaries=boundaries,
                                db=db,
                                total_duration=initial_duration,
                            )
                            words_mapped = mapped_count
                            logger.info(f"[Word Mapping] Complete: {track_id} ({mapped_count}/{len(word_timings)} words)")
                            
                            from text_storage_service import text_storage_service
                            if hasattr(text_storage_service, "consolidate_timing_shards"):
                                logger.info(f"[Word Mapping] Consolidating shards: {track_id}/{voice}")
                                consolidated = await text_storage_service.consolidate_timing_shards(track_id, voice, db)
                                if consolidated:
                                    logger.info(f"[Word Mapping] Shards consolidated successfully")
                            
                        except Exception as mapping_error:
                            logger.error(f"[Word Mapping] Failed: {track_id}/{voice} - {mapping_error}")
                            words_mapped = 0
                    else:
                        logger.warning(f"[Word Mapping] Skipped - no word timings available: {track_id}/{voice}")

                await self.hls_manager._save_segment_index(
                    variant_dir, measured_durations, start_number=0
                )

                if db and track_id:
                    try:
                        track = await DatabaseExecutor.execute(lambda: _get_track_by_id(db, track_id))
                        if track:
                            await DatabaseExecutor.execute(
                                lambda: _update_track_status(db, track, "complete", True)
                            )
                    except Exception as track_error:
                        logger.error(f"Error updating track status: {track_error}")

                supports_word_switching = await self.supports_word_level_switching(track_id, voice)
                quality_info = await self.get_word_switching_quality(track_id, voice)

                stream_info = {
                    "stream_id": track_id,
                    "voice": voice,
                    "voice_directory": f"voice-{voice}",
                    "duration": initial_duration,
                    "ready": True,
                    "segment_duration": segment_duration,
                    "total_segments": len(measured_durations),
                    "measured_segment_durations": measured_durations,
                    "has_measured_durations": bool(measured_durations),
                    "optimized_single_pass": True,
                    "segments_created": len(measured_durations),
                    "words_mapped": int(words_mapped),
                    "synchronous_word_mapping": True,
                    "variants": [
                        {
                            "name": self.hls_manager.default_bitrate["name"],
                            "bitrate": self.hls_manager.default_bitrate["bitrate"],
                            "codec": self.hls_manager.default_bitrate["codec"],
                            "segment_duration": self.hls_manager.default_bitrate["segment_duration"],
                            "url": f"{self.hls_manager.default_bitrate['name']}/playlist.m3u8",
                            "voice_specific": True,
                        }
                    ],
                    "status": "voice_stream_created",
                    "supports_word_level_switching": supports_word_switching,
                    "word_switching_quality": quality_info,
                    "precision_switching_ready": quality_info.get("supports_precision_switching", False),
                    "precision_mapping_enabled": self.hls_manager.enable_word_level_mapping,
                    "cache_type": "file_based",
                }

                voice_cache_key = f"{track_id}:voice:{voice}"
                await self.hls_manager.cache.set_upload_time(voice_cache_key)
                await self.hls_manager.cache.set_metadata(voice_cache_key, stream_info)

                if progress_key in self.hls_manager.segment_progress:
                    word_status = f"{words_mapped} words mapped" if words_mapped > 0 else "no word mapping"
                    self.hls_manager.segment_progress[progress_key].update({
                        "status": "complete",
                        "words_mapped": int(words_mapped),
                        "synchronous_mapping": True,
                        "message": f"Voice HLS complete: {len(measured_durations)} segments, {word_status}",
                        "percentage": 100.0,
                        "current_duration": initial_duration,
                        "formatted": {
                            "current": self.hls_manager._format_duration(initial_duration),
                            "total": self.hls_manager._format_duration(initial_duration),
                            "percent": "100%",
                        },
                    })
                    stream_info["generation_status"] = self.hls_manager.segment_progress[progress_key]

                word_log = f"{words_mapped} words mapped" if words_mapped > 0 else "no word mapping"
                logger.info(f"Voice HLS complete: {track_id} ({voice}) - {word_log}")
                return stream_info

            except Exception as e:
                logger.error(f"Voice HLS preparation failed: {track_id} ({voice}): {str(e)}")
                progress_key = self.hls_manager._get_progress_key(track_id, voice)
                if progress_key in self.hls_manager.segment_progress:
                    self.hls_manager.segment_progress[progress_key]["status"] = "error"
                    self.hls_manager.segment_progress[progress_key]["error"] = str(e)
                raise


    async def prepare_hls_stream(self, file_path: Path, filename: str, track_id: str, db: Optional[Session] = None) -> Dict:
        return await self.hls_manager.prepare_hls_stream(file_path, filename, track_id, db=db)

    async def prepare_hls(self, file_path: Path, filename: str, track_id: Optional[str] = None, db: Optional[Session] = None, **kwargs) -> Dict:
        voice = kwargs.get('voice')
        task_info = kwargs.get('task_info', {})
        
        if not voice and task_info:
            voice = task_info.get('voice')
        
        is_voice_stream = bool(voice) or (track_id and '_voice_' in str(track_id))
        
        if is_voice_stream and voice:
            return await self.prepare_hls_stream_with_voice(
                file_path=file_path,
                filename=filename,
                track_id=track_id,
                voice=voice,
                db=db
            )
        else:
            return await self.prepare_hls_stream(file_path, filename, track_id, db=db)

    async def _is_playlist_complete(self, playlist_path: Path) -> bool:
        try:
            async with aiofiles.open(playlist_path, 'r') as f:
                content = await f.read()
            
            required_tags = {
                'master': ['#EXTM3U', '#EXT-X-VERSION', '#EXT-X-STREAM-INF'],
                'variant': ['#EXTM3U', '#EXT-X-VERSION', '#EXT-X-TARGETDURATION', '#EXT-X-MEDIA-SEQUENCE', '#EXT-X-ENDLIST']
            }
            
            playlist_type = 'master' if playlist_path.name == 'master.m3u8' else 'variant'
            is_complete = all(tag in content for tag in required_tags[playlist_type])
            
            return is_complete
                
        except Exception as e:
            logger.error(f"Error checking playlist completion: {str(e)}")
            return False

    async def get_segment_progress(
        self, 
        stream_id: str, 
        voice_id: Optional[str] = None,
        db: Optional[Session] = None
    ) -> dict:
        try:
            progress_key = self.hls_manager._get_progress_key(stream_id, voice_id)
            
            # DEBUG: Log what we're looking for
            logger.info(f"🔍 Progress lookup: stream_id={stream_id}, voice_id={voice_id}")
            logger.info(f"🔍 Progress key: {progress_key}")
            logger.info(f"🔍 Available progress keys: {list(self.hls_manager.segment_progress.keys())}")
            
            if progress_key in self.hls_manager.segment_progress:
                progress = self.hls_manager.segment_progress[progress_key]
                logger.info(f"🔍 Found progress: {progress}")
                if voice_id:
                    progress['voice_id'] = voice_id
                    progress['voice_specific'] = True
                progress['cache_type'] = 'file_based'
                return progress
            else:
                logger.info(f"🔍 Progress key NOT FOUND in memory")

            # 2. Enhanced fallback for TTS voices with existing streams
            if voice_id:
                voice_stream_dir = self.hls_manager.segment_dir / stream_id / f"voice-{voice_id}"
                variant_dir = voice_stream_dir / 'default'
                master_playlist = voice_stream_dir / "master.m3u8"
                variant_playlist = variant_dir / "playlist.m3u8"
                
                if master_playlist.exists() and variant_playlist.exists():
                    # Get duration from database if available
                    total_duration = 0.0
                    if db:
                        total_duration = await self.hls_manager._get_duration_from_database(stream_id, db, voice_id)
                    
                    # Get segment information
                    segment_index = await self.get_segment_index(stream_id, voice_id)
                    measured_durations = segment_index.get('durations', [])
                    segments = list(variant_dir.glob("segment_*.*"))
                    
                    # Use segment index duration if database duration not available
                    if total_duration <= 0:
                        total_duration = segment_index.get('total_duration', sum(measured_durations) if measured_durations else 0)
                    
                    if segments and total_duration > 0:
                        # Check if playlist is complete
                        try:
                            async with aiofiles.open(variant_playlist, 'r') as f:
                                content = await f.read()
                            has_endlist = '#EXT-X-ENDLIST' in content
                        except Exception:
                            has_endlist = False
                        
                        if has_endlist:
                            # Complete stream
                            newest_segment = max(segments, key=lambda p: p.stat().st_mtime)
                            return {
                                'status': 'complete',
                                'percentage': 100.0,
                                'current_duration': total_duration,
                                'total_duration': total_duration,
                                'message': f'Voice {self.hls_manager.getVoiceDisplayName(voice_id) if hasattr(self.hls_manager, "getVoiceDisplayName") else voice_id} ready',
                                'formatted': {
                                    'current': self.hls_manager._format_duration(total_duration),
                                    'total': self.hls_manager._format_duration(total_duration),
                                    'percent': '100%'
                                },
                                'voice_id': voice_id,
                                'voice_specific': True,
                                'voice_directory': f'voice-{voice_id}',
                                'total_segments': len(segments),
                                'measured_durations': measured_durations,
                                'has_measured_durations': bool(measured_durations),
                                'last_modified': datetime.fromtimestamp(newest_segment.stat().st_mtime).isoformat(),
                                'fallback_detection': True,
                                'cache_type': 'file_based'
                            }
                        else:
                            # Stream in progress - parse current EXTINF entries for live progress
                            try:
                                durations = []
                                for line in content.splitlines():
                                    line = line.strip()
                                    if line.startswith("#EXTINF:"):
                                        try:
                                            duration_str = line.split(":", 1)[1].split(",", 1)[0]
                                            durations.append(float(duration_str))
                                        except Exception:
                                            pass
                                
                                current_duration = sum(durations)
                                if total_duration > 0:
                                    percentage = min(99.0, max(0.1, (current_duration / total_duration) * 100.0))
                                else:
                                    percentage = 50.0  # Unknown total, show some progress
                                
                                return {
                                    'status': 'creating_segments',
                                    'percentage': percentage,
                                    'current_duration': current_duration,
                                    'total_duration': total_duration,
                                    'message': f'Preparing {self.hls_manager.getVoiceDisplayName(voice_id) if hasattr(self.hls_manager, "getVoiceDisplayName") else voice_id}... {percentage:.1f}%',
                                    'formatted': {
                                        'current': self.hls_manager._format_duration(current_duration),
                                        'total': self.hls_manager._format_duration(total_duration),
                                        'percent': f'{percentage:.1f}%'
                                    },
                                    'voice_id': voice_id,
                                    'voice_specific': True,
                                    'voice_directory': f'voice-{voice_id}',
                                    'segments_so_far': len(durations),
                                    'progress_type': 'inferred_from_playlist',
                                    'cache_type': 'file_based'
                                }
                            except Exception as parse_error:
                                logger.debug(f"Could not parse in-progress playlist: {parse_error}")

            # 3. Regular track fallback (existing logic preserved)
            segment_index = await self.get_segment_index(stream_id, voice_id)
            measured_durations = segment_index.get('durations', [])
            
            if voice_id:
                voice_stream_dir = self.hls_manager.segment_dir / stream_id / f"voice-{voice_id}"
                segment_dir = voice_stream_dir / 'default'
            else:
                segment_dir = self.hls_manager.segment_dir / stream_id / 'default'
            
            if segment_dir.exists():
                segments = list(segment_dir.glob('segment_*.*'))
                if segments:
                    segment_count = len(segments)
                    newest_segment = max(segments, key=lambda p: p.stat().st_mtime)
                    segment_numbers = [int(s.stem.split('_')[1]) for s in segments]
                    min_num, max_num = min(segment_numbers), max(segment_numbers)
                    
                    status = {
                        'total': segment_count,
                        'current': segment_count,
                        'percent': 100,
                        'status': 'complete',
                        'formatted': {
                            'current': segment_count,
                            'total': segment_count,
                            'percent': '100%'
                        },
                        'last_modified': datetime.fromtimestamp(newest_segment.stat().st_mtime).isoformat(),
                        'segments_info': {
                            'first': min_num,
                            'last': max_num,
                            'total': segment_count
                        },
                        'measured_durations': measured_durations,
                        'has_measured_durations': bool(measured_durations),
                        'uses_measured_durations': segment_index.get('measured', False),
                        'precision_processing': segment_index.get('precision_processing', True),
                        'pipeline_type': segment_index.get('pipeline', 'single_pass_ts'),
                        'cache_type': 'file_based'
                    }
                    
                    if voice_id:
                        status.update({
                            'voice_id': voice_id,
                            'voice_specific': True,
                            'voice_directory': f'voice-{voice_id}'
                        })
                    
                    return status

            # 4. Nothing found - return appropriate not_found response
            response = {
                'total': 0,
                'current': 0,
                'percent': 0,
                'status': 'not_found',
                'formatted': {
                    'current': 0,
                    'total': 0,
                    'percent': '0%'
                },
                'measured_durations': [],
                'has_measured_durations': False,
                'precision_processing': False,
                'cache_type': 'file_based'
            }
            
            if voice_id:
                response.update({
                    'voice_id': voice_id,
                    'voice_specific': True,
                    'message': f'No segments found for voice {voice_id}',
                    'supports_word_level_switching': False
                })
            
            return response

        except Exception as e:
            logger.error(f"Error getting progress for {stream_id}/{voice_id}: {str(e)}")
            
            response = {
                'total': 0,
                'current': 0,
                'percent': 0,
                'status': 'error',
                'message': str(e),
                'formatted': {
                    'current': 0,
                    'total': 0,
                    'percent': '0%'
                },
                'measured_durations': [],
                'has_measured_durations': False,
                'precision_processing': False,
                'cache_type': 'file_based'
            }
            
            if voice_id:
                response.update({
                    'voice_id': voice_id,
                    'voice_specific': True,
                    'supports_word_level_switching': False
                })
                
            return response

    async def redownload_and_prepare_stream(self, filename: str, track_id: str):
        temp_path: Optional[Path] = None
        db = None
        try:
            db = next(get_db())
            cleanup_status = await self.cleanup_stream(track_id, db)

            file_path = Path(filename)
            actual_filename = file_path.name
            
            max_retries = 3
            retry_delay = 2
            
            for attempt in range(max_retries):
                try:
                    from mega_s4_client import mega_s4_client
                    if not mega_s4_client._started:
                        await mega_s4_client.start()
                        await asyncio.sleep(1)
                    
                    temp_path = await self.storage_manager.download_audio_file(actual_filename)
                    if temp_path and await asyncio.get_event_loop().run_in_executor(None, lambda: temp_path.exists()):
                        break
                    else:
                        pass
                        
                except Exception:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2
                    else:
                        raise ValueError("Failed to download audio file from storage after retries")
            
            if not temp_path:
                raise ValueError("Failed to download audio file from storage")

            track = await DatabaseExecutor.execute(lambda: _get_track_by_id(db, track_id))
            if track:
                await DatabaseExecutor.execute(lambda: _update_track_status(db, track, 'incomplete', False))

            stream_info = await self.hls_manager.prepare_hls_stream(
                file_path=temp_path, 
                filename=actual_filename,
                track_id=track_id,
                db=db
            )
            
            stream_dir = self.hls_manager.segment_dir / track_id
            variant_dir = stream_dir / 'default'
            master_playlist = stream_dir / "master.m3u8"
            variant_playlist = variant_dir / "playlist.m3u8"
            index_path = stream_dir / "index.json"
            
            verification_result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: {
                    'master_exists': master_playlist.exists(),
                    'variant_exists': variant_playlist.exists(),
                    'index_exists': index_path.exists(),
                    'segments': list(variant_dir.glob("segment_*.*"))
                }
            )
            
            segments = verification_result['segments']
            expected_segments = stream_info.get('total_segments', 0)
            
            if (verification_result['master_exists'] and 
                verification_result['variant_exists'] and 
                verification_result['index_exists'] and
                len(segments) >= expected_segments):
                
                if track:
                    await DatabaseExecutor.execute(lambda: _update_track_status(db, track, 'complete', True))
            else:
                raise ValueError(f"Regeneration verification failed: segments={len(segments)}, expected={expected_segments}")

            await self.hls_manager.cache.set_upload_time(track_id)

        except Exception as e:
            logger.error(f"Error during regeneration for {track_id}: {str(e)}")
            
            if db:
                try:
                    track = await DatabaseExecutor.execute(lambda: _get_track_by_id(db, track_id))
                    if track:
                        await DatabaseExecutor.execute(
                            lambda: _update_track_status(db, track, 'incomplete', False, str(e))
                        )
                except Exception:
                    try:
                        await DatabaseExecutor.execute(lambda: db.rollback())
                    except Exception:
                        pass
            
            raise
            
        finally:
            if temp_path:
                temp_exists = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: os.path.exists(temp_path)
                )
                if temp_exists:
                    try:
                        await asyncio.get_event_loop().run_in_executor(
                            None, lambda: os.unlink(temp_path)
                        )
                    except Exception:
                        pass
            
            if db:
                try:
                    await DatabaseExecutor.execute(lambda: db.close())
                except Exception:
                    pass

    async def cleanup_stream(self, track_id: str, db: Optional[Session] = None) -> Dict[str, bool]:
        cleanup_status = {
            "segments_removed": False,
            "playlists_removed": False,
            "directories_removed": False,
            "cache_cleared": False,
            "metadata_cleared": False,
            "database_cleaned": False,
            "index_files_removed": False,
            "cache_type": "file_based",
            "errors": []
        }
        
        try:
            segment_path = self.hls_manager.segment_dir / str(track_id)
            
            try:
                cache_cleared = await self.hls_manager.cache.clear_track_data(track_id)
                
                track_dir = self.hls_manager.segment_dir / track_id
                if track_dir.exists():
                    voice_dirs = [d for d in track_dir.iterdir() if d.is_dir() and d.name.startswith('voice-')]
                    for voice_dir in voice_dirs:
                        voice_id = voice_dir.name[6:]
                        voice_cache_key = f"{track_id}:voice:{voice_id}"
                        await self.hls_manager.cache.clear_track_data(voice_cache_key)
                
                cleanup_status.update({
                    "cache_cleared": cache_cleared,
                    "metadata_cleared": cache_cleared
                })
                
            except Exception as e:
                cleanup_status["errors"].append(f"File cache cleanup error: {str(e)}")

            segment_path_exists = await asyncio.get_event_loop().run_in_executor(
                None, lambda: segment_path.exists()
            )
            
            if segment_path_exists:
                try:
                    def remove_directory_with_indexes(path: Path):
                        if path.exists():
                            index_files = list(path.rglob("index.json"))
                            metadata_files = list(path.rglob("metadata.json"))
                            index_count = len(index_files) + len(metadata_files)
                            
                            shutil.rmtree(str(path), ignore_errors=True)
                            success = not path.exists()
                            return success, index_count

                    removal_success, index_files_removed = await asyncio.get_event_loop().run_in_executor(
                        None, 
                        remove_directory_with_indexes,
                        segment_path
                    )
                    
                    cleanup_status.update({
                        "segments_removed": removal_success,
                        "playlists_removed": removal_success,
                        "directories_removed": removal_success,
                        "index_files_removed": index_files_removed > 0
                    })
                    
                except Exception as e:
                    cleanup_status["errors"].append(f"Directory removal error: {str(e)}")

            if db is not None:
                try:
                    deleted_count = await DatabaseExecutor.execute(lambda: _delete_segment_metadata(db, track_id))
                    cleanup_status["database_cleaned"] = True
                    
                except Exception as db_error:
                    cleanup_status["errors"].append(f"Database cleanup error: {str(db_error)}")

            return cleanup_status

        except Exception as e:
            error_msg = f"Critical error during stream cleanup: {str(e)}"
            logger.error(error_msg)
            cleanup_status["errors"].append(error_msg)
            return cleanup_status

    async def cleanup(self):
        await self.hls_manager.cleanup()

    @property
    def segment_dir(self) -> Path:
        return self.hls_manager.segment_dir

stream_manager = StreamManager()

__all__ = [
    'stream_manager',
    'StreamManager', 
    'BaseHLSManager',
    'EnterpriseHLSManager',
    'DatabaseExecutor', 
    'SimpleFileCache',
    'SEGMENT_DURATION',
    'BASE_DIR', 
    'SEGMENT_DIR',
    'TEMP_DIR',
    'DEFAULT_BITRATE',
    '_get_track_by_id',
    '_update_track_status',
    '_delete_segment_metadata', 
    '_rollback_db',
    '_get_file_hash',
    'logger'
]