# ========================================
# SMART VOICE SEGMENT SERVICE - PURE VOICE-SPECIFIC WITH ADAPTIVE BUFFERING
# smart_voice_segments.py - Enhanced Implementation with Smart Buffering
# ========================================

import asyncio
import aiofiles
import edge_tts
import os
import time
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple, Set, List
from sqlalchemy.orm import Session
from models import TTSTextSegment, TTSWordTiming, TTSTrackMeta, Track
from datetime import datetime, timezone
import numpy as np
import logging

logger = logging.getLogger(__name__)

# ========================================
# CONFIGURATION - PURE VOICE-SPECIFIC
# ========================================

BASE_HLS_DIR = Path(os.path.expanduser("~")) / ".hls_streaming"
SEGMENTS_DIR = BASE_HLS_DIR / "segments"
DEFAULT_VOICE_ID = "en-US-AvaNeural"

# Simple in-memory processing locks
generation_locks = {}
generation_results = {}

# ========================================
# PURE VOICE-SPECIFIC SEGMENT SERVICE WITH SMART BUFFERING
# ========================================

class SmartVoiceSegmentService:
    """Pure voice-specific segment service with adaptive buffering - No legacy/default folder support"""
    
    def __init__(self):
        self.active_generations = {}
        self.background_tasks: Set[asyncio.Task] = set()
        self.voice_generation_locks = {}
        
        # 🆕 Smart buffering properties
        self.buffer_size = 5  # Default buffer size
        self.concurrent_limit = 3  # Max concurrent generations
        self.active_buffers = {}  # Track active buffering per voice
        self.user_behavior = {}  # Track user seeking patterns
        self.buffer_tasks = {}  # Track buffer generation tasks

    async def get_voice_segment(
        self,
        track_id: str,
        segment_index: int,
        voice_id: str,
        db: Session
    ) -> Tuple[bytes, Dict[str, str]]:
        """Get voice segment - Pure voice-specific implementation with smart buffering"""
        
        try:
            logger.info(f"🎤 Voice segment request: {track_id}/segment_{segment_index:05d} for voice {voice_id}")
            
            # 🆕 Track user behavior for smart buffering
            self._update_user_behavior(track_id, voice_id, segment_index)
            
            # Step 0: Check segment boundary
            max_segment_index = await self._get_max_segment_index(track_id, db)
            if segment_index > max_segment_index:
                logger.warning(f"📊 Segment {segment_index} out of bounds (max: {max_segment_index})")
                empty_audio = b''
                headers = self._build_response_headers(
                    voice_id=voice_id,
                    track_id=track_id,
                    segment_index=segment_index,
                    duration=0.0,
                    source="out_of_bounds"
                )
                headers['X-Error'] = 'segment_out_of_bounds'
                headers['X-Max-Segment'] = str(max_segment_index)
                return empty_audio, headers
            
            # Step 1: Check voice-specific segment (ONLY voice-specific paths)
            voice_segment_path = self._get_voice_segment_path(track_id, voice_id, segment_index)
            
            if voice_segment_path.exists():
                logger.info(f"🚀 CACHE HIT: Serving voice segment from {voice_segment_path}")
                async with aiofiles.open(voice_segment_path, 'rb') as f:
                    audio_data = await f.read()
                
                duration = await self._get_segment_duration(voice_segment_path, audio_data)
                headers = self._build_response_headers(
                    voice_id=voice_id,
                    track_id=track_id,
                    segment_index=segment_index,
                    duration=duration,
                    source="voice_cache"
                )
                
                # 🆕 Smart buffer strategy for cached hits
                await self._smart_buffer_strategy(track_id, voice_id, segment_index, db, max_segment_index)
                
                # Original background generation trigger (kept for compatibility)
                await self._trigger_background_voice_generation(track_id, voice_id, db, max_segment_index)
                
                return audio_data, headers
            
            # Step 2: Check processing lock
            lock_key = f"{track_id}:{voice_id}:{segment_index}"
            
            if self._is_generating(lock_key):
                logger.info(f"⏳ GENERATION IN PROGRESS: Waiting for existing generation")
                audio_data, duration = await self._wait_for_generation(lock_key)
                headers = self._build_response_headers(
                    voice_id=voice_id,
                    track_id=track_id,
                    segment_index=segment_index,
                    duration=duration,
                    source="concurrent_generation"
                )
                return audio_data, headers
            
            # Step 3: Generate segment with smart buffering
            logger.info(f"💨 CACHE MISS: Generating segment for voice {voice_id} with smart buffering")
            
            async with self._generation_lock(lock_key):
                text_segment = self._get_text_segment_from_db(track_id, segment_index, db)
                
                if not text_segment:
                    logger.error(f"❌ Text segment {segment_index} not found after boundary check")
                    raise Exception(f"Text segment {segment_index} not found for track {track_id}")
                
                chunk_text = self._extract_text_content(text_segment)
                if not chunk_text:
                    raise Exception(f"No text content in segment {segment_index}")
                
                # Generate audio from text with specified voice
                audio_data, duration, word_timings = await self._generate_chunk_audio(chunk_text, voice_id)
                
                # Save directly to voice-specific path
                await self._save_voice_segment(voice_segment_path, audio_data)
                
                # Store result for concurrent requests
                self._store_generation_result(lock_key, audio_data, duration)
                
                # Store word timings for this voice
                await self._store_word_timings(text_segment.id, voice_id, word_timings, db)
                
                # Ensure voice playlist exists
                await self._ensure_voice_playlist_exists(track_id, voice_id, db)
                
                # 🆕 Enhanced headers with buffer info
                headers = self._build_response_headers(
                    voice_id=voice_id,
                    track_id=track_id,
                    segment_index=segment_index,
                    duration=duration,
                    source="live_generation"
                )
                
                # 🆕 Add smart buffer information to headers
                behavior = self.user_behavior.get(f"{track_id}:{voice_id}", {})
                buffer_strategy = self._determine_buffer_strategy(behavior, segment_index, max_segment_index)
                headers['X-Buffer-Strategy'] = buffer_strategy['name']
                headers['X-Buffer-Count'] = str(buffer_strategy['size'])
                
                logger.info(f"✅ Generated segment {segment_index} for voice {voice_id} in {duration:.2f}s")
                
                # 🆕 Start smart buffering for upcoming segments
                await self._start_smart_buffering(track_id, voice_id, segment_index, db, max_segment_index)
                
                # Original background generation trigger (kept for compatibility)
                await self._trigger_background_voice_generation(track_id, voice_id, db, max_segment_index)
                
                return audio_data, headers
                
        except Exception as e:
            logger.error(f"❌ Error getting voice segment: {str(e)}", exc_info=True)
            raise

    # ========================================
    # 🆕 SMART BUFFERING METHODS
    # ========================================
    
    def _update_user_behavior(self, track_id: str, voice_id: str, segment_index: int):
        """Track user seeking patterns for smart buffering"""
        
        behavior_key = f"{track_id}:{voice_id}"
        current_time = time.time()
        
        if behavior_key not in self.user_behavior:
            self.user_behavior[behavior_key] = {
                'last_segment': segment_index,
                'last_time': current_time,
                'seek_pattern': 'linear',
                'jump_history': [],
                'total_requests': 1,
                'created_at': current_time
            }
            return
        
        behavior = self.user_behavior[behavior_key]
        last_segment = behavior['last_segment']
        time_diff = current_time - behavior['last_time']
        
        # Calculate jump distance
        jump_distance = abs(segment_index - last_segment)
        
        # Update behavior tracking
        behavior['jump_history'].append({
            'from': last_segment,
            'to': segment_index,
            'distance': jump_distance,
            'time_diff': time_diff,
            'timestamp': current_time
        })
        
        # Keep only recent history (last 10 requests)
        if len(behavior['jump_history']) > 10:
            behavior['jump_history'] = behavior['jump_history'][-10:]
        
        # Detect seeking pattern
        recent_jumps = [j['distance'] for j in behavior['jump_history'][-5:]]
        avg_jump = sum(recent_jumps) / len(recent_jumps) if recent_jumps else 1
        
        # Pattern detection
        if avg_jump > 20:
            behavior['seek_pattern'] = 'random'
        elif segment_index > last_segment + 10:
            behavior['seek_pattern'] = 'forward_seeking'
        elif time_diff < 2 and jump_distance > 5:
            behavior['seek_pattern'] = 'scrubbing'
        elif jump_distance <= 2:
            behavior['seek_pattern'] = 'linear'
        else:
            behavior['seek_pattern'] = 'mixed'
        
        behavior['last_segment'] = segment_index
        behavior['last_time'] = current_time
        behavior['total_requests'] += 1
        
        logger.debug(f"🧠 User behavior: {behavior['seek_pattern']}, jump: {jump_distance}, avg: {avg_jump:.1f}")

    def _determine_buffer_strategy(self, behavior: dict, segment_index: int, max_segment_index: int) -> dict:
        """Determine optimal buffer strategy based on user behavior"""
        
        seek_pattern = behavior.get('seek_pattern', 'linear')
        remaining_segments = max_segment_index - segment_index
        
        if seek_pattern == 'random':
            return {'name': 'minimal', 'size': min(2, remaining_segments), 'reason': 'random_seeking'}
        elif seek_pattern == 'forward_seeking':
            return {'name': 'extended', 'size': min(8, remaining_segments), 'reason': 'forward_seeking'}
        elif seek_pattern == 'scrubbing':
            return {'name': 'bidirectional', 'size': min(6, remaining_segments), 'reason': 'scrubbing'}
        elif remaining_segments <= 3:
            return {'name': 'end_track', 'size': remaining_segments, 'reason': 'near_end'}
        else:
            return {'name': 'standard', 'size': min(self.buffer_size, remaining_segments), 'reason': 'linear_playback'}

    async def _smart_buffer_strategy(self, track_id: str, voice_id: str, segment_index: int, db: Session, max_segment_index: int):
        """Adaptive buffering strategy based on user behavior"""
        
        behavior_key = f"{track_id}:{voice_id}"
        behavior = self.user_behavior.get(behavior_key, {})
        
        # Avoid duplicate buffering
        if behavior_key in self.active_buffers:
            return
        
        strategy = self._determine_buffer_strategy(behavior, segment_index, max_segment_index)
        buffer_size = strategy['size']
        
        if buffer_size <= 0:
            return
        
        # Cancel previous buffering if user jumped far
        await self._cancel_irrelevant_buffering(track_id, voice_id, segment_index, behavior)
        
        # Determine buffer range based on strategy
        if strategy['name'] == 'bidirectional':
            # Buffer both directions for scrubbing
            start = max(0, segment_index - 2)
            end = min(max_segment_index + 1, segment_index + buffer_size - 1)
            buffer_range = list(range(start, end))
        else:
            # Forward buffering
            start = segment_index + 1
            end = min(segment_index + buffer_size + 1, max_segment_index + 1)
            buffer_range = list(range(start, end))
        
        if buffer_range:
            # Mark as active
            self.active_buffers[behavior_key] = {
                'started_at': time.time(),
                'strategy': strategy,
                'segments': buffer_range
            }
            
            # Start background buffer generation
            asyncio.create_task(
                self._background_smart_buffering(track_id, voice_id, buffer_range, db, strategy, behavior_key)
            )
            
            logger.info(f"🔄 Started {strategy['name']} buffering: {len(buffer_range)} segments, reason: {strategy['reason']}")

    async def _cancel_irrelevant_buffering(self, track_id: str, voice_id: str, current_segment: int, behavior: dict):
        """Cancel buffering tasks that are no longer relevant"""
        
        if not behavior.get('jump_history'):
            return
        
        last_jump = behavior['jump_history'][-1]
        jump_distance = last_jump.get('distance', 0)
        
        # Cancel if user jumped more than 10 segments
        if jump_distance > 10:
            buffer_key = f"{track_id}:{voice_id}"
            
            if buffer_key in self.active_buffers:
                logger.info(f"❌ Cancelling previous buffering due to large jump ({jump_distance} segments)")
                del self.active_buffers[buffer_key]
            
            # Cancel buffer tasks
            if buffer_key in self.buffer_tasks:
                for task in self.buffer_tasks[buffer_key]:
                    if not task.done():
                        task.cancel()
                del self.buffer_tasks[buffer_key]

    async def _start_smart_buffering(self, track_id: str, voice_id: str, segment_index: int, db: Session, max_segment_index: int):
        """Start smart buffering for upcoming segments"""
        
        behavior_key = f"{track_id}:{voice_id}"
        behavior = self.user_behavior.get(behavior_key, {})
        strategy = self._determine_buffer_strategy(behavior, segment_index, max_segment_index)
        
        buffer_size = strategy['size']
        if buffer_size <= 0:
            return
        
        # Generate buffer segments list
        if strategy['name'] == 'bidirectional':
            start = max(0, segment_index - 2)
            end = min(max_segment_index + 1, segment_index + buffer_size - 1)
        else:
            start = segment_index + 1
            end = min(segment_index + buffer_size + 1, max_segment_index + 1)
        
        buffer_segments = []
        for seg_idx in range(start, end):
            voice_path = self._get_voice_segment_path(track_id, voice_id, seg_idx)
            if not voice_path.exists():
                buffer_segments.append(seg_idx)
        
        if buffer_segments:
            # Start buffer generation task
            task = asyncio.create_task(
                self._generate_buffer_segments(track_id, voice_id, buffer_segments, db, strategy)
            )
            
            # Track buffer task
            if behavior_key not in self.buffer_tasks:
                self.buffer_tasks[behavior_key] = []
            self.buffer_tasks[behavior_key].append(task)
            
            # Clean up completed tasks
            task.add_done_callback(lambda t: self._cleanup_buffer_task(behavior_key, t))
            
            logger.info(f"🚀 Started smart buffer generation: {len(buffer_segments)} segments, strategy: {strategy['name']}")

    async def _background_smart_buffering(self, track_id: str, voice_id: str, segments: List[int], db: Session, strategy: dict, buffer_key: str):
        """Background smart buffering with pattern awareness"""
        
        try:
            # Prioritize segments based on strategy
            if strategy['name'] == 'forward_seeking':
                # Generate in forward order with higher priority
                priority_segments = segments[:3]
                normal_segments = segments[3:]
            elif strategy['name'] == 'bidirectional':
                # Generate closest segments first
                priority_segments = segments[:2]
                normal_segments = segments[2:]
            else:
                priority_segments = segments[:2]
                normal_segments = segments[2:]
            
            # Generate priority segments first
            if priority_segments:
                await self._generate_segments_batch(track_id, voice_id, priority_segments, db)
                logger.info(f"✅ Priority buffering complete: {len(priority_segments)} segments")
            
            # Generate remaining segments
            if normal_segments:
                limited_segments = normal_segments[:self.concurrent_limit]
                await self._generate_segments_batch(track_id, voice_id, limited_segments, db)
                logger.info(f"✅ Normal buffering complete: {len(limited_segments)} segments")
            
        except Exception as e:
            logger.error(f"❌ Smart buffering error: {e}")
        finally:
            # Clean up active buffer tracking
            if buffer_key in self.active_buffers:
                del self.active_buffers[buffer_key]

    async def _generate_buffer_segments(self, track_id: str, voice_id: str, segments: List[int], db: Session, strategy: dict):
        """Generate buffer segments with strategy-aware prioritization"""
        
        try:
            if strategy['name'] == 'extended' and len(segments) > self.concurrent_limit:
                # For forward seeking, generate in batches
                for i in range(0, len(segments), self.concurrent_limit):
                    batch = segments[i:i + self.concurrent_limit]
                    await self._generate_segments_batch(track_id, voice_id, batch, db)
                    await asyncio.sleep(0.2)  # Small delay between batches
            else:
                # Generate all at once for smaller buffers
                limited_segments = segments[:self.concurrent_limit * 2]
                await self._generate_segments_batch(track_id, voice_id, limited_segments, db)
                
        except Exception as e:
            logger.error(f"❌ Buffer generation error: {e}")

    async def _generate_segments_batch(self, track_id: str, voice_id: str, segments: List[int], db: Session):
        """Generate a batch of segments concurrently"""
        
        tasks = []
        for seg_idx in segments:
            # Skip if already exists
            voice_path = self._get_voice_segment_path(track_id, voice_id, seg_idx)
            if voice_path.exists():
                continue
                
            task = asyncio.create_task(
                self._generate_single_segment(track_id, voice_id, seg_idx, db)
            )
            tasks.append((seg_idx, task))
            
            # Limit concurrent tasks
            if len(tasks) >= self.concurrent_limit:
                break
        
        if tasks:
            # Wait for all tasks in batch
            results = await asyncio.gather(*[task for _, task in tasks], return_exceptions=True)
            
            success_count = sum(1 for result in results if not isinstance(result, Exception))
            logger.info(f"✅ Batch generation: {success_count}/{len(tasks)} segments completed")

    async def _generate_single_segment(self, track_id: str, voice_id: str, segment_index: int, db: Session) -> bytes:
        """Generate a single segment and save to cache"""
        
        try:
            # Get text segment
            text_segment = self._get_text_segment_from_db(track_id, segment_index, db)
            if not text_segment:
                raise Exception(f"Text segment {segment_index} not found")
            
            chunk_text = self._extract_text_content(text_segment)
            if not chunk_text:
                raise Exception(f"No text content in segment {segment_index}")
            
            # Generate audio
            audio_data, duration, word_timings = await self._generate_chunk_audio(chunk_text, voice_id)
            
            # Save to voice-specific cache
            voice_path = self._get_voice_segment_path(track_id, voice_id, segment_index)
            await self._save_voice_segment(voice_path, audio_data)
            
            # Store word timings
            await self._store_word_timings(text_segment.id, voice_id, word_timings, db)
            
            logger.debug(f"✅ Generated and cached segment {segment_index} for voice {voice_id}")
            return audio_data
            
        except Exception as e:
            logger.error(f"❌ Error generating segment {segment_index}: {e}")
            raise

    def _cleanup_buffer_task(self, buffer_key: str, task: asyncio.Task):
        """Clean up completed buffer task"""
        if buffer_key in self.buffer_tasks:
            if task in self.buffer_tasks[buffer_key]:
                self.buffer_tasks[buffer_key].remove(task)
            if not self.buffer_tasks[buffer_key]:
                del self.buffer_tasks[buffer_key]

    # ========================================
    # 🆕 BUFFER STATUS AND MANAGEMENT
    # ========================================

    async def get_buffer_status(self, track_id: str, voice_id: str) -> Dict:
        """Get current buffer status for a voice"""
        
        behavior_key = f"{track_id}:{voice_id}"
        
        # Get user behavior info
        behavior = self.user_behavior.get(behavior_key, {})
        
        # Check active buffering
        active_buffer = self.active_buffers.get(behavior_key)
        
        # Count existing segments
        try:
            from database import SessionLocal
            with SessionLocal() as db:
                max_segment_index = await self._get_max_segment_index(track_id, db)
                
            existing_segments = 0
            for seg_idx in range(max_segment_index + 1):
                voice_path = self._get_voice_segment_path(track_id, voice_id, seg_idx)
                if voice_path.exists():
                    existing_segments += 1
            
            total_segments = max_segment_index + 1
            cache_percent = (existing_segments / total_segments) * 100 if total_segments > 0 else 100
            
        except Exception as e:
            logger.error(f"❌ Error getting buffer status: {e}")
            return {"status": "error", "error": str(e)}
        
        return {
            "track_id": track_id,
            "voice_id": voice_id,
            "cache_completion": cache_percent,
            "cached_segments": existing_segments,
            "total_segments": total_segments,
            "seek_pattern": behavior.get('seek_pattern', 'unknown'),
            "total_requests": behavior.get('total_requests', 0),
            "active_buffering": active_buffer is not None,
            "buffer_strategy": active_buffer['strategy']['name'] if active_buffer else None,
            "architecture": "smart_buffering_enabled"
        }

    # ========================================
    # ORIGINAL METHODS (PRESERVED FOR COMPATIBILITY)
    # ========================================

    def _get_voice_segment_path(self, track_id: str, voice_id: str, segment_index: int) -> Path:
        """Get voice-specific segment path - PURE voice-specific, no legacy support"""
        voice_dir = SEGMENTS_DIR / track_id / f"voice-{voice_id}"
        voice_dir.mkdir(parents=True, exist_ok=True)
        return voice_dir / f"segment_{segment_index:05d}.ts"

    async def _save_voice_segment(self, segment_path: Path, audio_data: bytes):
        """Save audio segment to voice-specific path"""
        try:
            segment_path.parent.mkdir(parents=True, exist_ok=True)
            
            async with aiofiles.open(segment_path, 'wb') as f:
                await f.write(audio_data)
                
            logger.debug(f"💾 Saved voice segment: {segment_path}")
            
        except Exception as e:
            logger.error(f"❌ Error saving voice segment: {e}")
            raise

    async def _ensure_voice_playlist_exists(self, track_id: str, voice_id: str, db: Session):
        """Ensure voice-specific playlist exists"""
        try:
            from voice_playlist_manager import create_voice_playlist_if_needed
            await create_voice_playlist_if_needed(track_id, voice_id, db)
        except Exception as e:
            logger.error(f"❌ Error ensuring voice playlist: {e}")

    async def _trigger_background_voice_generation(
        self,
        track_id: str,
        voice_id: str,
        db: Session,
        max_segment_index: int
    ):
        """Trigger background generation of all segments for a voice"""
        
        try:
            # Check if already generating this voice in background
            voice_key = f"{track_id}:{voice_id}"
            if voice_key in self.voice_generation_locks:
                logger.debug(f"🔄 Background generation already running for voice {voice_id}")
                return
            
            # Create lock for this voice
            self.voice_generation_locks[voice_key] = asyncio.Lock()
            
            # Start background task
            task = asyncio.create_task(
                self._background_generate_voice_segments(track_id, voice_id, db, max_segment_index)
            )
            
            self.background_tasks.add(task)
            task.add_done_callback(self.background_tasks.discard)
            
            logger.debug(f"🚀 Started background generation for voice {voice_id} ({max_segment_index + 1} segments)")
            
        except Exception as e:
            logger.error(f"❌ Error triggering background generation: {e}")

    async def _background_generate_voice_segments(
        self,
        track_id: str,
        voice_id: str,
        db: Session,
        max_segment_index: int
    ):
        """Background worker to generate all missing segments for a voice"""
        
        voice_key = f"{track_id}:{voice_id}"
        
        try:
            async with self.voice_generation_locks[voice_key]:
                logger.debug(f"🔄 Background: Generating missing segments for voice {voice_id}")
                
                # Find missing segments (pure voice-specific check)
                missing_segments = []
                for seg_idx in range(max_segment_index + 1):
                    voice_path = self._get_voice_segment_path(track_id, voice_id, seg_idx)
                    if not voice_path.exists():
                        missing_segments.append(seg_idx)
                
                if not missing_segments:
                    logger.debug(f"✅ All segments exist for voice {voice_id}")
                    # Still ensure playlist exists
                    await self._ensure_voice_playlist_exists(track_id, voice_id, db)
                    return
                
                logger.info(f"🔄 Background: Generating {len(missing_segments)} missing segments for voice {voice_id}")
                
                # Generate missing segments in batches
                for i in range(0, len(missing_segments), self.concurrent_limit):
                    batch = missing_segments[i:i + self.concurrent_limit]
                    await self._generate_segments_batch(track_id, voice_id, batch, db)
                    
                    # Small delay between batches
                    if i + self.concurrent_limit < len(missing_segments):
                        await asyncio.sleep(0.5)
                
                # Ensure playlist exists after generation
                await self._ensure_voice_playlist_exists(track_id, voice_id, db)
                
                logger.info(f"🎯 Background: Completed generation for voice {voice_id}")
                
        except Exception as e:
            logger.error(f"❌ Background generation error for voice {voice_id}: {e}")
            
        finally:
            # Clean up lock
            if voice_key in self.voice_generation_locks:
                del self.voice_generation_locks[voice_key]

    async def get_voice_generation_status(self, track_id: str, voice_id: str) -> Dict:
        """Get status of background generation for a voice"""
        
        try:
            voice_key = f"{track_id}:{voice_id}"
            
            # Check if generation is active
            is_generating = voice_key in self.voice_generation_locks
            
            # Count existing segments (voice-specific only)
            from database import SessionLocal
            with SessionLocal() as db:
                max_segment_index = await self._get_max_segment_index(track_id, db)
                if max_segment_index < 0:
                    return {"status": "no_segments", "progress": 0, "voice_id": voice_id}
            
            existing_segments = 0
            for seg_idx in range(max_segment_index + 1):
                voice_path = self._get_voice_segment_path(track_id, voice_id, seg_idx)
                if voice_path.exists():
                    existing_segments += 1
            
            total_segments = max_segment_index + 1
            progress_percent = (existing_segments / total_segments) * 100 if total_segments > 0 else 100
            
            # Add buffer status info
            buffer_status = await self.get_buffer_status(track_id, voice_id)
            
            return {
                "status": "generating" if is_generating else "completed" if existing_segments == total_segments else "partial",
                "progress": progress_percent,
                "existing_segments": existing_segments,
                "total_segments": total_segments,
                "missing_segments": total_segments - existing_segments,
                "voice_id": voice_id,
                "architecture": "pure_voice_specific_with_smart_buffering",
                "seek_pattern": buffer_status.get('seek_pattern'),
                "buffer_strategy": buffer_status.get('buffer_strategy')
            }
            
        except Exception as e:
            logger.error(f"❌ Error getting generation status: {e}")
            return {"status": "error", "error": str(e), "voice_id": voice_id}

    async def cleanup_background_tasks(self):
        """Cleanup all background generation tasks"""
        
        try:
            if self.background_tasks:
                logger.info(f"🧹 Cleaning up {len(self.background_tasks)} background tasks")
                
                # Cancel all background tasks
                for task in list(self.background_tasks):
                    if not task.done():
                        task.cancel()
                
                # Wait for tasks to complete or cancel
                if self.background_tasks:
                    await asyncio.gather(*self.background_tasks, return_exceptions=True)
                
                self.background_tasks.clear()
                logger.info("✅ Background tasks cleaned up")
            
            # Clean up generation locks
            self.voice_generation_locks.clear()
            
            # 🆕 Clean up buffer tasks
            for buffer_key, tasks in self.buffer_tasks.items():
                for task in tasks:
                    if not task.done():
                        task.cancel()
            self.buffer_tasks.clear()
            self.active_buffers.clear()
            
        except Exception as e:
            logger.error(f"❌ Error cleaning up background tasks: {e}")

    # ========================================
    # CORE GENERATION METHODS (PRESERVED)
    # ========================================
    
    async def _get_max_segment_index(self, track_id: str, db: Session) -> int:
        """Get the maximum segment index for a track"""
        
        try:
            result = db.query(TTSTextSegment.segment_index).filter(
                TTSTextSegment.track_id == track_id
            ).order_by(TTSTextSegment.segment_index.desc()).first()
            
            if result:
                max_index = result[0]
                logger.debug(f"📊 Track {track_id} has segments 0-{max_index}")
                return max_index
            else:
                logger.warning(f"📊 Track {track_id} has no segments")
                return -1
                
        except Exception as e:
            logger.error(f"❌ Error getting max segment index: {e}")
            return -1
    
    def _get_text_segment_from_db(self, track_id: str, segment_index: int, db: Session) -> Optional[TTSTextSegment]:
        """Get text segment from database"""
        
        try:
            segment = db.query(TTSTextSegment).filter(
                TTSTextSegment.track_id == track_id,
                TTSTextSegment.segment_index == segment_index
            ).first()
            
            if segment:
                logger.debug(f"📝 Found text segment {segment_index} for track {track_id}")
                return segment
            else:
                logger.warning(f"📝 No text segment found: track={track_id}, segment={segment_index}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Database error getting text segment: {e}")
            return None
    
    def _extract_text_content(self, text_segment: TTSTextSegment) -> str:
        """Extract text content from segment"""
        
        try:
            # Try get_text_content() method first
            if hasattr(text_segment, 'get_text_content'):
                content = text_segment.get_text_content()
                if content and isinstance(content, str) and content.strip():
                    logger.debug(f"📝 Extracted text via get_text_content(): {len(content)} chars")
                    return content.strip()
            
            # Try direct text_content attribute
            if hasattr(text_segment, 'text_content') and text_segment.text_content:
                content = text_segment.text_content
                if isinstance(content, str) and content.strip():
                    logger.debug(f"📝 Extracted text via text_content: {len(content)} chars")
                    return content.strip()
            
            # Try other common text fields
            for field_name in ['text', 'content', 'segment_text', 'original_text']:
                if hasattr(text_segment, field_name):
                    content = getattr(text_segment, field_name)
                    if content and isinstance(content, str) and content.strip():
                        logger.debug(f"📝 Extracted text via {field_name}: {len(content)} chars")
                        return content.strip()
            
            logger.error(f"❌ Could not extract text content from segment")
            return ""
            
        except Exception as e:
            logger.error(f"❌ Error extracting text content: {e}")
            return ""
    
    async def _generate_chunk_audio(self, chunk_text: str, voice_id: str) -> Tuple[bytes, float, list]:
        """Generate audio for a single chunk with word boundaries"""
        
        try:
            logger.debug(f"🎵 Generating audio: {len(chunk_text)} chars with voice {voice_id}")
            
            communicate = edge_tts.Communicate(chunk_text.strip(), voice_id)
            
            audio_chunks = []
            word_timings = []
            
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_chunks.append(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    word_timings.append({
                        'word': chunk["text"].strip(),
                        'start_time': chunk["offset"] / 10_000_000,
                        'end_time': 0,  # Will be calculated
                        'text_offset': chunk.get("text_offset", 0),
                        'length': chunk.get("length", len(chunk["text"]))
                    })
            
            # Calculate end times
            for i in range(len(word_timings) - 1):
                word_timings[i]['end_time'] = word_timings[i + 1]['start_time']
            
            if word_timings:
                last_word = word_timings[-1]
                last_word['end_time'] = last_word['start_time'] + (len(last_word['word']) * 0.08)
                duration = last_word['end_time']
            else:
                # Fallback duration calculation
                word_count = len(chunk_text.split())
                duration = (word_count / 150) * 60  # 150 WPM
            
            audio_data = b''.join(audio_chunks) if audio_chunks else b''
            
            logger.debug(f"✅ Generated audio: {len(audio_data)} bytes, {duration:.2f}s, {len(word_timings)} words for voice {voice_id}")
            
            return audio_data, duration, word_timings
            
        except Exception as e:
            logger.error(f"❌ Error generating chunk audio for voice {voice_id}: {e}")
            raise
    
    async def _store_word_timings(self, segment_id: int, voice_id: str, word_timings: list, db: Session):
        """Store word timings for this segment and voice"""
        
        try:
            if not word_timings:
                logger.debug(f"💾 No word timings to store for segment {segment_id}, voice {voice_id}")
                return
            
            # Check if word timing already exists
            existing = db.query(TTSWordTiming).filter(
                TTSWordTiming.segment_id == segment_id,
                TTSWordTiming.voice_id == voice_id
            ).first()
            
            if existing:
                # Update existing
                existing.pack_word_timings(word_timings)
                logger.debug(f"💾 Updated existing word timings for segment {segment_id}, voice {voice_id}")
            else:
                # Create new
                word_timing_record = TTSWordTiming(
                    segment_id=segment_id,
                    voice_id=voice_id
                )
                word_timing_record.pack_word_timings(word_timings)
                db.add(word_timing_record)
                logger.debug(f"💾 Created new word timings for segment {segment_id}, voice {voice_id}")
            
            db.commit()
            logger.debug(f"💾 Stored {len(word_timings)} word timings for segment {segment_id}, voice {voice_id}")
            
        except Exception as e:
            logger.error(f"❌ Error storing word timings: {e}")
            db.rollback()
    
    async def _get_segment_duration(self, segment_path: Path, audio_data: bytes) -> float:
        """Get segment duration from file or estimate"""
        
        try:
            # Try to get duration using existing duration manager
            from duration_manager import duration_manager
            metadata = await duration_manager._extract_metadata(segment_path)
            duration = metadata.get('duration', 30.0)
            logger.debug(f"📊 Duration from metadata: {duration}s")
            return duration
            
        except Exception as e:
            logger.debug(f"⚠️ Could not get metadata duration: {e}")
            # Fallback: estimate from audio data size
            estimated_duration = len(audio_data) / 16000  # Rough estimate
            estimated_duration = min(max(estimated_duration, 1.0), 30.0)  # Clamp 1-30 seconds
            logger.debug(f"📊 Estimated duration: {estimated_duration}s")
            return estimated_duration
    
    def _build_response_headers(
        self,
        voice_id: str,
        track_id: str,
        segment_index: int,
        duration: float,
        source: str
    ) -> Dict[str, str]:
        """Build response headers for frontend"""
        
        return {
            'Content-Type': 'audio/mpeg',
            'Cache-Control': 'public, max-age=3600',
            'X-Voice-ID': voice_id,
            'X-Track-ID': track_id,
            'X-Segment-Index': str(segment_index),
            'X-Segment-Duration': str(duration),
            'X-Real-Duration': str(duration),
            'X-Source': source,
            'X-Generation-Method': 'pure_voice_specific_with_smart_buffering',
            'X-Architecture': 'voice_native_v3_smart',
            'X-Default-Voice-ID': DEFAULT_VOICE_ID,
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Expose-Headers': 'X-Voice-ID,X-Track-ID,X-Segment-Index,X-Segment-Duration,X-Real-Duration,X-Source,X-Architecture,X-Buffer-Strategy,X-Buffer-Count'
        }
    
    # ========================================
    # PROCESSING LOCK MANAGEMENT (PRESERVED)
    # ========================================
    
    def _is_generating(self, lock_key: str) -> bool:
        """Check if segment is currently being generated"""
        return lock_key in generation_locks
    
    def _generation_lock(self, lock_key: str):
        """Returns async context manager for generation locks"""
        
        class GenerationLock:
            def __init__(self, service, key):
                self.service = service
                self.key = key
                self.lock = asyncio.Lock()
            
            async def __aenter__(self):
                await self.lock.acquire()
                generation_locks[self.key] = {
                    'started_at': time.time(),
                    'lock': self.lock
                }
                return self
            
            async def __aexit__(self, exc_type, exc_val, exc_tb):
                if self.key in generation_locks:
                    del generation_locks[self.key]
                self.lock.release()
        
        return GenerationLock(self, lock_key)
    
    async def _wait_for_generation(self, lock_key: str, timeout: float = 300.0) -> Tuple[bytes, float]:
        """Wait for concurrent generation to complete"""
        
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            # Check if generation completed
            if lock_key in generation_results:
                result = generation_results[lock_key]
                del generation_results[lock_key]  # Clean up result
                return result['audio_data'], result['duration']
            
            # Check if generation is still active
            if lock_key not in generation_locks:
                break
            
            await asyncio.sleep(0.1)
        
        logger.warning(f"⏰ Concurrent generation timeout after {timeout}s")
        raise Exception("Concurrent generation timeout - try again")
    
    def _store_generation_result(self, lock_key: str, audio_data: bytes, duration: float):
        """Store generation result for concurrent requests"""
        
        generation_results[lock_key] = {
            'audio_data': audio_data,
            'duration': duration,
            'generated_at': time.time()
        }
        
        # Clean up old results after 30 seconds
        async def cleanup_result():
            await asyncio.sleep(30)
            if lock_key in generation_results:
                del generation_results[lock_key]
        
        asyncio.create_task(cleanup_result())
    
    # ========================================
    # UTILITY METHODS (PRESERVED)
    # ========================================
    
    async def get_track_segment_count(self, track_id: str, db: Session) -> int:
        """Get total number of segments for a track"""
        return db.query(TTSTextSegment).filter(
            TTSTextSegment.track_id == track_id
        ).count()
    
    async def pregenerate_voice_segments(
        self,
        track_id: str,
        voice_id: str,
        db: Session,
        progress_callback=None
    ):
        """Pre-generate all segments for a voice"""
        
        try:
            segments = db.query(TTSTextSegment).filter(
                TTSTextSegment.track_id == track_id
            ).order_by(TTSTextSegment.segment_index).all()
            
            total_segments = len(segments)
            logger.info(f"🔄 Pre-generating {total_segments} segments for voice {voice_id}")
            
            for i, segment in enumerate(segments):
                try:
                    # Check if segment already exists
                    voice_path = self._get_voice_segment_path(track_id, voice_id, segment.segment_index)
                    if voice_path.exists():
                        if progress_callback:
                            progress_callback(i + 1, total_segments, f"Segment {segment.segment_index} already exists")
                        continue
                    
                    # Generate segment
                    chunk_text = self._extract_text_content(segment)
                    if chunk_text:
                        audio_data, duration, word_timings = await self._generate_chunk_audio(chunk_text, voice_id)
                        await self._save_voice_segment(voice_path, audio_data)
                        await self._store_word_timings(segment.id, voice_id, word_timings, db)
                        
                        if progress_callback:
                            progress_callback(i + 1, total_segments, f"Generated segment {segment.segment_index}")
                        
                        logger.info(f"✅ Pre-generated segment {segment.segment_index}/{total_segments} for voice {voice_id}")
                    
                except Exception as e:
                    logger.error(f"❌ Error pre-generating segment {segment.segment_index} for voice {voice_id}: {e}")
                    if progress_callback:
                        progress_callback(i + 1, total_segments, f"Failed segment {segment.segment_index}")
                
                # Small delay to avoid overwhelming the system
                await asyncio.sleep(0.1)
            
            # Ensure playlist exists after pre-generation
            await self._ensure_voice_playlist_exists(track_id, voice_id, db)
            
            logger.info(f"✅ Pre-generation complete for voice {voice_id}: {total_segments} segments")
            
        except Exception as e:
            logger.error(f"❌ Error in pre-generation for voice {voice_id}: {e}")
            raise

# ========================================
# GLOBAL SERVICE INSTANCE
# ========================================

smart_voice_segment_service = SmartVoiceSegmentService()

__all__ = ['smart_voice_segment_service', 'SmartVoiceSegmentService', 'DEFAULT_VOICE_ID']