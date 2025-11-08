# ========================================
# MINIMAL SENTENCE-BASED CHUNKING FIX
# streaming_tts_processor.py
# ========================================

import asyncio
import aiofiles
import edge_tts
import time
import tempfile
import os
import io
import json
import numpy as np
import re  # Added for sentence processing
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from sqlalchemy.orm import Session
from database import SessionLocal, get_db
from models import TTSTrackMeta, TTSTextSegment, TTSVoiceSegment, TTSWordTiming, Track, SegmentMetadata
import logging
from datetime import datetime, timezone
from sqlalchemy import text as sql_text

logger = logging.getLogger(__name__)

# ========================================
# CONFIGURATION - UNCHANGED
# ========================================

BASE_HLS_DIR = Path(os.path.expanduser("~")) / ".hls_streaming"
SEGMENTS_DIR = BASE_HLS_DIR / "segments"
TTS_TEMP_DIR = BASE_HLS_DIR / "tts" / "temp"
TTS_COMPLETE_DIR = BASE_HLS_DIR / "tts" / "complete"

for path in [TTS_TEMP_DIR, TTS_COMPLETE_DIR]:
    path.mkdir(parents=True, exist_ok=True)

DEFAULT_VOICE = 'en-US-AvaNeural'
SEGMENT_DURATION = 30.0
CHUNK_SIZE = 8000

AVAILABLE_VOICES = [
    'en-US-AvaNeural', 'en-US-AriaNeural', 'en-US-GuyNeural',
    'en-US-JennyNeural', 'en-US-ChristopherNeural', 'en-US-EricNeural',
    'en-US-MichelleNeural', 'en-US-RogerNeural', 'en-US-SteffanNeural'
]

# ========================================
# EXISTING PROGRESS TRACKER - UNCHANGED
# ========================================

class RealTTSProgressTracker:
    """🔧 FIXED: Real percentage progress tracking with proper numeric conversion"""
    
    def __init__(self, track_id: str, db):
        self.track_id = track_id
        self.db = db
        
        self.phases = {
            'initializing': {
                'current_progress': 0,
                'status': 'pending',
                'start_time': None,
                'end_time': None
            },
            'text_processing': {
                'current_progress': 0,
                'status': 'pending', 
                'total_operations': 0,
                'completed_operations': 0,
                'start_time': None,
                'end_time': None
            },
            'audio_generation': {
                'current_progress': 0,
                'status': 'pending',
                'total_operations': 0,
                'completed_operations': 0,
                'start_time': None,
                'end_time': None
            },
            'hls_processing': {
                'current_progress': 0,
                'status': 'pending',
                'total_operations': 0,
                'completed_operations': 0,
                'start_time': None,
                'end_time': None
            },
            'word_mapping': {
                'current_progress': 0,
                'status': 'pending',
                'total_operations': 0,
                'completed_operations': 0,
                'start_time': None,
                'end_time': None
            }
        }
        
        self.current_phase = 'initializing'
        self.overall_start_time = datetime.now()
        
        self.chunk_info = {
            'characters_per_chunk': 8000,
            'max_characters_per_chunk': 30000,
            'estimated_words_per_chunk': 1200,
            'words_per_minute': 150,
            'target_chunk_minutes': 8
        }
        
    def _serialize_datetime(self, obj):
        """🔧 FIXED: Convert datetime objects to ISO format strings for JSON serialization"""
        if isinstance(obj, datetime):
            return obj.isoformat()
        elif isinstance(obj, dict):
            return {key: self._serialize_datetime(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [self._serialize_datetime(item) for item in obj]
        else:
            return obj
    
    def _safe_numeric_conversion(self, value, target_type=float):
        """🔧 NEW: Safely convert numeric values, handling edge cases"""
        if value is None:
            return None
        
        try:
            if target_type == int:
                return int(round(float(value)))
            else:
                return float(value)
        except (ValueError, TypeError, OverflowError):
            logger.warning(f"Could not convert {value} to {target_type.__name__}")
            return None
        
    async def start_phase(self, phase_name: str, total_operations: int = 0):
        """Start a new phase with known total operations"""
        if phase_name in self.phases:
            self.current_phase = phase_name
            self.phases[phase_name]['status'] = 'in_progress'
            self.phases[phase_name]['start_time'] = datetime.now()
            self.phases[phase_name]['total_operations'] = total_operations
            self.phases[phase_name]['completed_operations'] = 0
            self.phases[phase_name]['current_progress'] = 0
            
            await self.save_progress_to_db()
            logger.info(f"📊 Started phase {phase_name} with {total_operations} operations")
    
    async def update_phase_progress(self, phase_name: str, completed_operations: int, status_message: str = ''):
        """Update progress for current phase with real percentage"""
        if phase_name in self.phases:
            phase = self.phases[phase_name]
            phase['completed_operations'] = completed_operations
            
            if phase['total_operations'] > 0:
                real_percentage = (completed_operations / phase['total_operations']) * 100
                phase['current_progress'] = min(100, real_percentage)
            else:
                phase['current_progress'] = 100 if completed_operations > 0 else 0
            
            if not status_message:
                status_message = f"{phase_name.replace('_', ' ').title()}: {completed_operations}/{phase['total_operations']}"
            
            await self.save_progress_to_db(status_message)
            logger.info(f"📊 {phase_name}: {phase['current_progress']:.1f}% ({completed_operations}/{phase['total_operations']})")
    
    async def complete_phase(self, phase_name: str):
        """Mark a phase as completed"""
        if phase_name in self.phases:
            self.phases[phase_name]['status'] = 'completed'
            self.phases[phase_name]['current_progress'] = 100
            self.phases[phase_name]['end_time'] = datetime.now()
            
            await self.save_progress_to_db(f"{phase_name.replace('_', ' ').title()} completed")
            logger.info(f"✅ Completed phase: {phase_name}")
    
    def get_overall_progress(self) -> float:
        """Calculate overall progress based on completed phases (not weighted)"""
        total_phases = len(self.phases)
        completed_phases = sum(1 for phase in self.phases.values() if phase['status'] == 'completed')
        
        current_phase_progress = 0
        if self.current_phase in self.phases:
            current_phase_progress = self.phases[self.current_phase]['current_progress'] / 100
        
        overall = ((completed_phases + current_phase_progress) / total_phases) * 100
        return min(100, overall)
    
    async def save_progress_to_db(self, status_message: str = ''):
        """🔧 FIXED: Save real progress to database with proper numeric conversion"""
        try:
            elapsed_time = (datetime.now() - self.overall_start_time).total_seconds()
            overall_progress = self.get_overall_progress()
            
            estimated_remaining = None
            if overall_progress > 0:
                estimated_total_time = elapsed_time / (overall_progress / 100)
                estimated_remaining_seconds = max(0, estimated_total_time - elapsed_time)
                estimated_remaining = self._safe_numeric_conversion(estimated_remaining_seconds, int)
            
            serialized_phases = self._serialize_datetime(self.phases)
            
            progress_metadata = {
                'overall_progress': overall_progress,
                'current_phase': self.current_phase,
                'phases': serialized_phases,
                'elapsed_seconds': elapsed_time,
                'estimated_remaining_seconds': estimated_remaining,
                'status_message': status_message,
                'last_updated': datetime.now(timezone.utc).isoformat(),
                'chunk_info': self.chunk_info,
                'progress_type': 'real_percentages'
            }
            
            existing_metadata = {}
            track_sql = sql_text("SELECT audio_metadata FROM tracks WHERE id = :track_id")
            result = self.db.execute(track_sql, {'track_id': self.track_id})
            track_row = result.first()
            
            if track_row and track_row.audio_metadata:
                try:
                    if isinstance(track_row.audio_metadata, str):
                        existing_metadata = json.loads(track_row.audio_metadata)
                    else:
                        existing_metadata = track_row.audio_metadata
                except Exception as parse_error:
                    logger.warning(f"Could not parse existing metadata: {parse_error}")
                    existing_metadata = {}
            
            existing_metadata.update({
                'tts_progress': progress_metadata,
                'current_progress': overall_progress,
                'current_status': status_message
            })
            
            serialized_metadata = json.dumps(existing_metadata)
            
            update_sql = sql_text("""
                UPDATE tracks 
                SET tts_progress = :progress, 
                    tts_status = :status,
                    audio_metadata = :metadata,
                    updated_at = :updated_at
                WHERE id = :track_id
            """)
            
            self.db.execute(update_sql, {
                'track_id': self.track_id,
                'progress': overall_progress,
                'status': 'processing' if overall_progress < 100 else 'ready',
                'metadata': serialized_metadata,
                'updated_at': datetime.now(timezone.utc)
            })
            self.db.commit()
            
            logger.debug(f"✅ Progress saved successfully: {overall_progress:.1f}%")
            
        except Exception as e:
            logger.error(f"❌ Error saving real TTS progress: {e}")

# ========================================
# ENHANCED TTS SERVICE WITH SENTENCE CHUNKING
# ========================================

class VoiceNativeTTSService:
    """🔧 FIXED: Voice-Native TTS Service with corrected HLS Pipeline Integration"""
    
    def __init__(self):
        self.active_generations = {}
        self.background_tasks: set = set()
        self.voice_generation_locks = {}

        self.words_per_minute = 150
        self.target_chunk_minutes = 8
        
        logger.info("VoiceNativeTTSService initialized - FIXED HLS Pipeline Integration")

    # ========================================
    # 🆕 PURE SENTENCE-BASED CHUNKING (REPLACES OLD METHOD)
    # ========================================

    async def _create_optimized_text_chunks(self, text_content: str) -> List[str]:
        """🔧 PURE SENTENCE-BASED: Create chunks respecting sentence boundaries only"""
        
        try:
            target_words_per_chunk = int(self.target_chunk_minutes * self.words_per_minute)
            max_chars_per_chunk = 28000  # Safe buffer under 30k limit
            min_words_per_chunk = int(target_words_per_chunk * 0.6)
            
            logger.info(f"📝 Sentence chunking: target={target_words_per_chunk} words, max={max_chars_per_chunk} chars")
            
            # Split into sentences
            sentences = self._split_into_sentences(text_content)
            logger.info(f"📄 Split into {len(sentences)} sentences")
            
            # Group sentences into chunks
            chunks = []
            current_chunk_sentences = []
            current_word_count = 0
            current_char_count = 0
            
            for sentence in sentences:
                sentence_words = len(sentence.split())
                sentence_chars = len(sentence)
                
                would_exceed_words = current_word_count + sentence_words > target_words_per_chunk
                would_exceed_chars = current_char_count + sentence_chars > max_chars_per_chunk
                
                if (would_exceed_words or would_exceed_chars) and current_chunk_sentences:
                    chunk_text = ' '.join(current_chunk_sentences)
                    chunks.append(chunk_text)
                    
                    current_chunk_sentences = [sentence]
                    current_word_count = sentence_words
                    current_char_count = sentence_chars
                else:
                    current_chunk_sentences.append(sentence)
                    current_word_count += sentence_words
                    current_char_count += sentence_chars
            
            if current_chunk_sentences:
                chunk_text = ' '.join(current_chunk_sentences)
                chunks.append(chunk_text)
            
            # Merge small chunks
            merged_chunks = []
            temp_chunk = []
            temp_word_count = 0
            
            for chunk in chunks:
                chunk_word_count = len(chunk.split())
                
                if temp_word_count + chunk_word_count <= target_words_per_chunk:
                    temp_chunk.append(chunk)
                    temp_word_count += chunk_word_count
                else:
                    if temp_word_count >= min_words_per_chunk:
                        merged_chunks.append(' '.join(temp_chunk))
                    else:
                        merged_chunks.extend(temp_chunk)
                    temp_chunk = [chunk]
                    temp_word_count = chunk_word_count
            
            if temp_chunk:
                if temp_word_count >= min_words_per_chunk:
                    merged_chunks.append(' '.join(temp_chunk))
                else:
                    merged_chunks.extend(temp_chunk)
            
            # Handle oversized chunks
            final_chunks = []
            for chunk in merged_chunks:
                if len(chunk.split()) > target_words_per_chunk or len(chunk) > max_chars_per_chunk:
                    split_chunks = self._split_oversized_chunk_by_sentences(chunk, target_words_per_chunk)
                    final_chunks.extend(split_chunks)
                else:
                    final_chunks.append(chunk)
            
            logger.info(f"✅ Sentence chunking complete: {len(final_chunks)} chunks created")
            return final_chunks
            
        except Exception as e:
            logger.error(f"❌ Error in sentence chunking: {e}")
            raise

    def _split_into_sentences(self, text: str) -> List[str]:
        """Split text into sentences using comprehensive patterns"""
        text = re.sub(r'\s+', ' ', text.strip())
        
        # Primary sentence patterns
        sentence_patterns = [
            r'(?<=[.!?])\s+(?=[A-Z])',
            r'(?<=[.!?])\s*\n+\s*',
            r'(?<=[.!?])\s*\r?\n\s*',
        ]
        
        for pattern in sentence_patterns:
            sentences = [s.strip() for s in re.split(pattern, text) if s.strip()]
            if len(sentences) > 1:
                valid_sentences = []
                for sentence in sentences:
                    sentence = sentence.strip()
                    if sentence and self._is_valid_sentence(sentence):
                        valid_sentences.append(sentence)
                    elif sentence:
                        fixed = self._fix_sentence_ending(sentence)
                        valid_sentences.append(fixed)
                
                if len(valid_sentences) > 1:
                    return valid_sentences
        
        # Backup patterns
        backup_patterns = [
            r'(?<=[.!?])\s+',
            r'(?<=\.)\s+(?=[A-Z][a-z])',
            r'(?<=[.!?]")\s+',
            r'(?<=[.!?]\')\s+',
        ]
        
        for pattern in backup_patterns:
            sentences = [s.strip() for s in re.split(pattern, text) if s.strip()]
            if len(sentences) > 1:
                cleaned_sentences = []
                for sentence in sentences:
                    sentence = self._clean_sentence(sentence)
                    if sentence:
                        cleaned_sentences.append(sentence)
                
                if len(cleaned_sentences) > 1:
                    return cleaned_sentences
        
        # Last resort: treat as single sentence
        cleaned_text = self._ensure_sentence_ending(text)
        return [cleaned_text]

    def _is_valid_sentence(self, sentence: str) -> bool:
        """Check if a sentence has proper ending punctuation"""
        sentence = sentence.strip()
        if not sentence:
            return False
        
        proper_endings = ['.', '!', '?', '...', '."', '!"', '?"', ".\'", "!\'", "?\'"]
        
        for ending in proper_endings:
            if sentence.endswith(ending):
                return True
        
        abbrev_patterns = [
            r'\b(?:Mr|Mrs|Dr|Prof|St|Ave|Blvd|Inc|Co|Ltd|Corp)$',
            r'\b(?:etc|vs|ie|eg)$',
            r'\b[A-Z]{2,}$',
        ]
        
        for pattern in abbrev_patterns:
            if re.search(pattern, sentence, re.IGNORECASE):
                return True
        
        return False

    def _fix_sentence_ending(self, sentence: str) -> str:
        """Fix a sentence that doesn't end properly"""
        sentence = sentence.strip()
        if not sentence:
            return sentence
        
        if self._is_valid_sentence(sentence):
            return sentence
        
        if sentence.endswith(('"', "'")):
            return sentence[:-1] + '."' if sentence.endswith('"') else sentence[:-1] + ".'"
        
        if not sentence[-1] in '.!?':
            return sentence + '.'
        
        return sentence

    def _clean_sentence(self, sentence: str) -> str:
        """Clean and normalize a sentence"""
        sentence = sentence.strip()
        if not sentence:
            return sentence
        
        sentence = re.sub(r'\s+', ' ', sentence)
        sentence = re.sub(r'\s+([.!?])', r'\1', sentence)
        sentence = re.sub(r'([.!?])\s*$', r'\1', sentence)
        sentence = self._ensure_sentence_ending(sentence)
        
        return sentence

    def _ensure_sentence_ending(self, text: str) -> str:
        """Ensure text ends with appropriate punctuation"""
        text = text.strip()
        if not text:
            return text
        
        if self._is_valid_sentence(text):
            return text
        
        if not text[-1] in '.!?':
            text += '.'
        
        return text

    def _split_oversized_chunk_by_sentences(self, chunk: str, max_words: int) -> List[str]:
        """Split oversized chunk by finding sentence boundaries within it"""
        sentences = self._split_into_sentences(chunk)
        
        if len(sentences) <= 1:
            # Single oversized sentence - split at natural breakpoints
            return self._split_single_oversized_sentence(chunk, max_words)
        
        # Multiple sentences - group them into smaller chunks
        sub_chunks = []
        current_sentences = []
        current_word_count = 0
        
        for sentence in sentences:
            sentence_words = len(sentence.split())
            
            if current_word_count + sentence_words > max_words and current_sentences:
                sub_chunks.append(' '.join(current_sentences))
                current_sentences = [sentence]
                current_word_count = sentence_words
            else:
                current_sentences.append(sentence)
                current_word_count += sentence_words
        
        if current_sentences:
            sub_chunks.append(' '.join(current_sentences))
        
        return sub_chunks

    def _split_single_oversized_sentence(self, sentence: str, max_words: int) -> List[str]:
        """Split a single oversized sentence at natural breakpoints"""
        clause_patterns = [
            r'(?<=,)\s+(?=and|but|or|so|yet|for|nor|however|therefore|moreover)',
            r'(?<=;)\s+',
            r'(?<=:)\s+',
            r'(?<=,)\s+(?=which|that|who|whom|whose|where|when)',
        ]
        
        for pattern in clause_patterns:
            parts = [p.strip() for p in re.split(pattern, sentence) if p.strip()]
            if len(parts) > 1:
                chunks = []
                current_part = []
                current_words = 0
                
                for part in parts:
                    part_words = len(part.split())
                    if current_words + part_words > max_words and current_part:
                        chunks.append(' '.join(current_part))
                        current_part = [part]
                        current_words = part_words
                    else:
                        current_part.append(part)
                        current_words += part_words
                
                if current_part:
                    chunks.append(' '.join(current_part))
                
                if len(chunks) > 1:
                    return chunks
        
        # Last resort: split into word chunks
        words = sentence.split()
        chunks = []
        current_words = []
        
        for i, word in enumerate(words):
            current_words.append(word)
            
            if len(current_words) >= max_words:
                chunk_text = ' '.join(current_words)
                if word.endswith((',', ';', ':')):
                    chunks.append(chunk_text)
                    current_words = []
                elif i < len(words) - 1:
                    chunks.append(chunk_text)
                    current_words = []
        
        if current_words:
            chunks.append(' '.join(current_words))
        
        return chunks

    # ========================================
    # ALL OTHER METHODS REMAIN THE SAME
    # ========================================

    async def _integrate_with_existing_hls(
        self,
        track_id: str,
        audio_info: Dict,
        voice: str,
        progress_tracker: RealTTSProgressTracker = None
    ):
        """🔧 FIXED: Route complete audio through existing HLS pipeline without progress_callback"""
        
        try:
            logger.info(f"🎬 Integrating with HLS system for track {track_id} with voice {voice}")
            
            if progress_tracker:
                await progress_tracker.update_phase_progress('hls_processing', 0, 'Queuing for HLS processing...')
            
            from storage import storage
            
            if not hasattr(storage, 'preparation_manager'):
                raise ValueError("Background preparation manager not available")
            
            file_size = audio_info['file_size']
            priority = 'high' if file_size < 50 * 1024 * 1024 else 'normal'
            
            task_info = {
                'audio_path': str(audio_info['audio_path']),
                'track_id': track_id,
                'voice': voice,
                'voice_id': voice,
                'output_directory': f"voice-{voice}",
                'duration': audio_info['duration'],
                'word_timings': audio_info['word_timings'],
                'estimated_segments': audio_info['estimated_segments'],
                'is_tts_track': True,
                'voice_specific': True,
                'progress_tracker': progress_tracker
            }
            
            if progress_tracker:
                await progress_tracker.update_phase_progress('hls_processing', 1, 'HLS processing started...')
            
            await storage.preparation_manager.queue_preparation(
                stream_id=track_id,
                filename=f"tts_{track_id}.mp3",
                prepare_func=self._hls_preparation_wrapper,
                file_size=file_size,
                priority=priority,
                db_session=None,
                task_info=task_info
            )
            
            logger.info(f"✅ Queued for voice-specific HLS processing (voice: {voice}, priority: {priority})")
            
        except Exception as e:
            logger.error(f"❌ Error integrating with HLS pipeline: {e}")
            raise

    async def _hls_preparation_wrapper(self, filename: str, db=None, task_info=None):
        """🔧 FIXED: Wrapper for existing HLS preparation without unsupported parameters"""
        
        if not task_info:
            raise ValueError("Task info required for HLS preparation")
        
        track_id = task_info['track_id']
        voice_id = task_info.get('voice_id') or task_info.get('voice')
        output_directory = task_info.get('output_directory')
        audio_path = Path(task_info['audio_path'])
        progress_tracker = task_info.get('progress_tracker')
        
        try:
            logger.info(f"🎬 Starting voice-specific HLS preparation:")
            logger.info(f"  - Track ID: {track_id}")
            logger.info(f"  - Voice ID: {voice_id}")
            logger.info(f"  - Output Directory: {output_directory}")
            logger.info(f"  - Audio Path: {audio_path}")
            
            from hls_streaming import stream_manager
            
            result = await stream_manager.prepare_hls(
                file_path=audio_path,
                filename=filename,
                track_id=track_id,
                db=db,
                voice_id=voice_id,
                output_directory=output_directory
            )
            
            if progress_tracker:
                total_segments = result.get('total_segments', 0)
                await progress_tracker.update_phase_progress('hls_processing', total_segments, f'HLS processing complete: {total_segments} segments')
            
            logger.info(f"✅ Voice-specific HLS preparation completed:")
            logger.info(f"  - Track ID: {track_id}")
            logger.info(f"  - Voice: {voice_id}")
            logger.info(f"  - Segments: {result.get('total_segments', 'unknown')}")
            logger.info(f"  - Duration: {result.get('duration', 'unknown')}s")
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Voice-specific HLS preparation failed:")
            logger.error(f"  - Track ID: {track_id}")
            logger.error(f"  - Voice: {voice_id}")
            logger.error(f"  - Error: {e}")
            raise
            
        finally:
            if audio_path.exists():
                try:
                    audio_path.unlink()
                    logger.info(f"🧹 Cleaned up temp audio: {audio_path}")
                except Exception as cleanup_error:
                    logger.warning(f"Error cleaning up temp audio: {cleanup_error}")

    async def create_tts_track(
        self,
        track_id: str,
        title: str,
        text_content: str,
        voice: str = DEFAULT_VOICE,
        db: Session = None
    ) -> Dict:
        """🔧 ENHANCED: Create TTS track with FIXED real progress tracking"""
        
        progress_tracker = RealTTSProgressTracker(track_id, db)
        
        try:
            logger.info(f"🎤 Creating TTS track with FIXED progress tracking:")
            logger.info(f"  - Track ID: {track_id}")
            logger.info(f"  - Title: {title}")
            logger.info(f"  - Voice: {voice}")
            logger.info(f"  - Text: {len(text_content):,} chars")
            
            # Phase 1: Initializing
            await progress_tracker.start_phase('initializing', 1)
            await progress_tracker.update_phase_progress('initializing', 0, 'Setting up TTS processing...')
            
            # Validate text content
            if not isinstance(text_content, str):
                if db is not None:
                    logger.info(f"🔄 Attempting to recover text from database...")
                    track = db.query(Track).filter(Track.id == track_id).first()
                    if track and hasattr(track, 'source_text') and isinstance(track.source_text, str):
                        text_content = track.source_text
                        logger.info(f"✅ Recovered text from database: {len(text_content)} chars")
                    else:
                        raise ValueError(f"Cannot recover text: Track not found or source_text is invalid")
                else:
                    raise ValueError(f"Expected string for text_content parameter, got {type(text_content)}: {repr(text_content)}")
            
            if not text_content or not text_content.strip():
                raise ValueError("Text content cannot be empty")
            
            logger.info(f"✅ Text validation passed: {len(text_content)} characters")
            
            await progress_tracker.update_phase_progress('initializing', 1, 'Initialization complete')
            await progress_tracker.complete_phase('initializing')
            
            # Phase 2: Text Processing 
            await progress_tracker.start_phase('text_processing', 1)
            await progress_tracker.update_phase_progress('text_processing', 0, 'Processing and chunking text...')
            
            logger.info("🔤 Starting text processing and chunking")
            chunks = await self._create_optimized_text_chunks(text_content)
            total_chunks = len(chunks)
            
            await progress_tracker.update_phase_progress('text_processing', 1, f'Created {total_chunks} chunks')
            await progress_tracker.complete_phase('text_processing')
            
            logger.info(f"✅ Text processing complete: {total_chunks} chunks created")
            
            # Phase 3: Audio Generation 
            await progress_tracker.start_phase('audio_generation', total_chunks)
            
            logger.info(f"🎵 Starting audio generation for {total_chunks} chunks")
            complete_audio_info = await self._generate_complete_audio_with_fixed_progress(
                text_content=text_content,
                voice=voice,
                track_id=track_id,
                progress_tracker=progress_tracker,
                total_chunks=total_chunks
            )
            
            await progress_tracker.complete_phase('audio_generation')
            logger.info(f"✅ Audio generation complete: {complete_audio_info['duration']:.1f}s")
            
            # Phase 4: HLS Processing 
            estimated_segments = int(np.ceil(complete_audio_info['duration'] / SEGMENT_DURATION))
            await progress_tracker.start_phase('hls_processing', estimated_segments)
            
            logger.info(f"🎬 Starting HLS processing for ~{estimated_segments} segments")
            await self._integrate_with_existing_hls(
                track_id=track_id,
                audio_info=complete_audio_info,
                voice=voice,
                progress_tracker=progress_tracker
            )
            
            await progress_tracker.complete_phase('hls_processing')
            logger.info(f"✅ HLS processing complete")
            
            # Phase 5: Word Mapping 
            await progress_tracker.start_phase('word_mapping', estimated_segments)
            
            logger.info(f"📍 Starting word mapping for {estimated_segments} segments")
            asyncio.create_task(self._delayed_word_mapping_with_fixed_progress(
                track_id, complete_audio_info, voice, progress_tracker, estimated_segments
            ))
            
            logger.info(f"✅ TTS track {track_id} created with voice-specific processing for {voice}")
            
            return {
                'status': 'success',
                'track_id': track_id,
                'voice_id': voice,
                'duration': complete_audio_info['duration'],
                'segments': complete_audio_info.get('estimated_segments', 0),
                'chunks_created': total_chunks,
                'characters_per_chunk': progress_tracker.chunk_info['characters_per_chunk'],
                'approach': 'voice_specific_hls_integration_with_FIXED_progress',
                'voice_folder': f"voice-{voice}",
                'output_directory': f"voice-{voice}",
                'no_default_folder': True,
                'voice_specific': True,
                'api_calls_used': complete_audio_info['api_calls'],
                'processing_efficiency': f"{complete_audio_info['api_calls']} API calls (vs ~{len(text_content.split())//75} in old method)"
            }
            
        except Exception as e:
            try:
                await progress_tracker.save_progress_to_db(f'Error: {str(e)}')
            except Exception as save_error:
                logger.error(f"Error saving error state: {save_error}")
            logger.error(f"❌ Error creating voice-specific TTS track:")
            logger.error(f"  - Track ID: {track_id}")
            logger.error(f"  - Voice: {voice}")
            logger.error(f"  - Error: {e}")
            raise

    async def _generate_complete_audio_with_fixed_progress(
        self,
        text_content: str,
        voice: str,
        track_id: str,
        progress_tracker: RealTTSProgressTracker,
        total_chunks: int
    ) -> Dict:
        """🔧 FIXED: Generate complete audio with proper progress tracking"""
        
        try:
            chunks = await self._create_optimized_text_chunks(text_content)
            
            logger.info(f"📊 Optimized chunking: {len(chunks)} API calls (vs ~{len(text_content.split())//75} in old method)")
            
            audio_segments = []
            all_word_timings = []
            cumulative_time = 0.0
            
            for i, chunk_text in enumerate(chunks):
                await progress_tracker.update_phase_progress(
                    'audio_generation', 
                    i, 
                    f'Generating audio chunk {i+1}/{total_chunks} ({len(chunk_text)} chars)'
                )
                
                logger.info(f"🎵 Generating chunk {i+1}/{len(chunks)} ({len(chunk_text):,} chars)")
                
                chunk_audio, chunk_duration, chunk_word_timings = await self._generate_chunk_audio(
                    chunk_text, voice
                )
                
                if chunk_audio:
                    audio_segments.append(chunk_audio)
                    
                    adjusted_timings = []
                    for word_timing in chunk_word_timings:
                        adjusted_timing = word_timing.copy()
                        adjusted_timing['start_time'] += cumulative_time
                        adjusted_timing['end_time'] += cumulative_time
                        adjusted_timings.append(adjusted_timing)
                    
                    all_word_timings.extend(adjusted_timings)
                    cumulative_time += chunk_duration
            
            await progress_tracker.update_phase_progress(
                'audio_generation', 
                total_chunks, 
                f'All {total_chunks} audio chunks generated'
            )
            
            complete_audio = b''.join(audio_segments)
            total_duration = cumulative_time
            
            complete_audio_path = TTS_TEMP_DIR / f"complete_{track_id}.mp3"
            
            async with aiofiles.open(complete_audio_path, 'wb') as f:
                await f.write(complete_audio)
            
            logger.info(f"✅ Complete audio file saved: {len(complete_audio):,} bytes, {total_duration:.2f}s")
            
            estimated_segments = int(np.ceil(total_duration / SEGMENT_DURATION))
            
            return {
                'audio_path': complete_audio_path,
                'duration': total_duration,
                'file_size': len(complete_audio),
                'api_calls': len(chunks),
                'word_timings': all_word_timings,
                'voice': voice,
                'estimated_segments': estimated_segments
            }
            
        except Exception as e:
            logger.error(f"❌ Error generating complete audio: {e}")
            raise

    async def _generate_chunk_audio(self, chunk_text: str, voice: str) -> Tuple[bytes, float, List]:
        """Generate audio for a single optimized chunk"""
        try:
            communicate = edge_tts.Communicate(chunk_text.strip(), voice)
            
            audio_chunks = []
            word_timings = []
            
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_chunks.append(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    word_timings.append({
                        'word': chunk["text"].strip(),
                        'start_time': chunk["offset"] / 10_000_000,
                        'end_time': 0,
                        'text_offset': chunk.get("text_offset", 0),
                        'length': chunk.get("length", len(chunk["text"]))
                    })
            
            for i in range(len(word_timings) - 1):
                word_timings[i]['end_time'] = word_timings[i + 1]['start_time']
            
            if word_timings:
                last_word = word_timings[-1]
                last_word['end_time'] = last_word['start_time'] + (len(last_word['word']) * 0.08)
                duration = last_word['end_time']
            else:
                word_count = len(chunk_text.split())
                duration = (word_count / self.words_per_minute) * 60
            
            audio_data = b''.join(audio_chunks) if audio_chunks else b''
            
            return audio_data, duration, word_timings
            
        except Exception as e:
            logger.error(f"Error generating chunk audio: {e}")
            raise

    async def _delayed_word_mapping_with_fixed_progress(
        self, 
        track_id: str, 
        audio_info: Dict, 
        voice: str, 
        progress_tracker: RealTTSProgressTracker,
        total_segments: int
    ):
        """🔧 FIXED: Word mapping with proper progress updates"""
        
        try:
            await asyncio.sleep(3)
            
            await progress_tracker.update_phase_progress('word_mapping', 0, 'Starting word mapping...')
            
            with SessionLocal() as db:
                await self._map_timings_to_hls_segments_fixed(track_id, audio_info, voice, db, progress_tracker)
                
            await progress_tracker.complete_phase('word_mapping')
            
            logger.info(f"✅ Word mapping completed with FIXED progress tracking for {track_id}")
            
        except Exception as e:
            logger.error(f"❌ Word mapping failed with FIXED progress for {track_id}: {e}")

    async def _map_timings_to_hls_segments_fixed(self, track_id: str, audio_info: Dict, voice: str, db: Session, progress_tracker: RealTTSProgressTracker = None):
        """🔧 ENHANCED: Map word timings to voice-specific HLS segments with progress"""
        
        try:
            logger.info(f"📍 Mapping word timings to voice-specific HLS segments:")
            logger.info(f"  - Track ID: {track_id}")
            logger.info(f"  - Voice: {voice}")
            
            word_timings = audio_info.get('word_timings', [])
            if not word_timings:
                logger.warning(f"No word timings available for track {track_id}")
                return
            
            boundaries = await self._load_segment_boundaries_from_playlist(track_id, voice)
            
            if not boundaries:
                raise RuntimeError(f"No segment boundaries found in voice-specific playlist for track {track_id}, voice {voice}")
            
            logger.info(f"📍 Mapping {len(word_timings)} words to {len(boundaries)} voice-specific HLS segments")
            
            if progress_tracker:
                await progress_tracker.update_phase_progress('word_mapping', 0, 'Starting word mapping...')
            
            segments_data = []
            
            for i, boundary in enumerate(boundaries):
                segment_index = boundary["index"]
                seg_start = boundary["start"] 
                seg_end = boundary["end"]
                seg_duration = boundary["duration"]
                
                segment_words = [
                    word for word in word_timings
                    if seg_start <= word['start_time'] < seg_end
                ]
                
                if segment_words:
                    adjusted_words = []
                    segment_text_parts = []
                    
                    for word in segment_words:
                        adjusted_word = word.copy()
                        adjusted_word['start_time'] -= seg_start
                        adjusted_word['end_time'] -= seg_start
                        adjusted_words.append(adjusted_word)
                        segment_text_parts.append(word['word'])
                    
                    segment_text = ' '.join(segment_text_parts)
                    
                    segments_data.append({
                        'segment_index': segment_index,
                        'text': segment_text,
                        'word_count': len(segment_words),
                        'char_count': len(segment_text),
                        'start_time': seg_start,
                        'end_time': seg_end,
                        'actual_duration': seg_duration,
                        'word_timings': adjusted_words
                    })
                
                if progress_tracker:
                    await progress_tracker.update_phase_progress('word_mapping', i + 1, f'Mapped segment {i+1}/{len(boundaries)}')
            
            await self._store_voice_native_metadata(
                track_id=track_id,
                voice_id=voice,
                segments_data=segments_data,
                total_duration=audio_info['duration'],
                db=db
            )
            
            logger.info(f"✅ Word timings mapped to {len(segments_data)} voice-specific HLS segments:")
            logger.info(f"  - Track ID: {track_id}")
            logger.info(f"  - Voice: {voice}")
            logger.info(f"  - Total Duration: {audio_info['duration']:.2f}s")
            
        except Exception as e:
            logger.error(f"❌ Error mapping word timings to voice-specific HLS segments:")
            logger.error(f"  - Track ID: {track_id}")
            logger.error(f"  - Voice: {voice}")
            logger.error(f"  - Error: {e}")
            raise

    async def _load_segment_boundaries_from_playlist(self, track_id: str, voice_id: str) -> List[Dict]:
        """Parse the HLS variant playlist - ALWAYS use voice-specific directories"""
        try:
            variant_dir = SEGMENTS_DIR / track_id / f"voice-{voice_id}"
            playlist_path = variant_dir / "playlist.m3u8"
            
            for attempt in range(20):
                if playlist_path.exists():
                    break
                await asyncio.sleep(1)
            else:
                raise FileNotFoundError(f"Voice-specific playlist not found after 20s: {playlist_path}")
            
            logger.info(f"📊 Reading segment boundaries from voice-specific playlist: {playlist_path}")
            
            async with aiofiles.open(playlist_path, "r") as f:
                content = await f.read()
            
            lines = content.splitlines()
            boundaries = []
            current_start = 0.0
            current_duration = None
            
            duration_re = re.compile(r"#EXTINF:([\d.]+)")
            
            for line in lines:
                line = line.strip()
                
                duration_match = duration_re.match(line)
                if duration_match:
                    current_duration = float(duration_match.group(1))
                    continue
                
                if line.startswith("segment_") and line.endswith(".ts"):
                    segment_parts = line.split("_")
                    if len(segment_parts) >= 2:
                        index_str = segment_parts[1].split(".")[0]
                        segment_index = int(index_str)
                        
                        if current_duration is not None:
                            boundaries.append({
                                "index": segment_index,
                                "start": current_start,
                                "duration": current_duration,
                                "end": current_start + current_duration
                            })
                            current_start += current_duration
                            current_duration = None
            
            logger.info(f"✅ Extracted {len(boundaries)} segment boundaries from voice-specific playlist for voice {voice_id}")
            return boundaries
            
        except Exception as e:
            logger.error(f"❌ Error loading segment boundaries for voice {voice_id}: {e}")
            raise

    async def _store_voice_native_metadata(
        self,
        track_id: str,
        voice_id: str,
        segments_data: List[Dict],
        total_duration: float,
        db: Session
    ):
        """Store metadata for voice-specific TTS track"""
        try:
            logger.info(f"💾 Storing voice-specific metadata for track {track_id}")
            
            existing_meta = db.query(TTSTrackMeta).filter(
                TTSTrackMeta.track_id == track_id
            ).first()
            
            total_words = sum(s.get('word_count', 0) for s in segments_data)
            total_segments = len(segments_data)
            
            if existing_meta:
                logger.info(f"🔄 Updating existing metadata for track {track_id}")
                existing_meta.total_segments = total_segments
                existing_meta.default_voice = voice_id
                existing_meta.available_voices = [voice_id]
                existing_meta.total_words = total_words
                existing_meta.total_characters = sum(s.get('char_count', 0) for s in segments_data)
                existing_meta.total_duration = total_duration
                existing_meta.processing_status = 'ready'
                existing_meta.words_per_segment = total_words / total_segments if total_segments > 0 else 0
                existing_meta.processed_segments = total_segments
                existing_meta.progress_percentage = 100.0
                existing_meta.failed_segments = 0
                existing_meta.completed_at = datetime.now(timezone.utc)
                track_meta = existing_meta
            else:
                logger.info(f"🆕 Creating new metadata for track {track_id}")
                track_meta = TTSTrackMeta(
                    track_id=track_id,
                    total_segments=total_segments,
                    default_voice=voice_id,
                    available_voices=[voice_id],
                    total_words=total_words,
                    total_characters=sum(s.get('char_count', 0) for s in segments_data),
                    total_duration=total_duration,
                    processing_status='ready',
                    words_per_segment=total_words / total_segments if total_segments > 0 else 0,
                    segment_duration=SEGMENT_DURATION,
                    compression_level=6,
                    processed_segments=total_segments,
                    progress_percentage=100.0,
                    failed_segments=0,
                    average_compression_ratio=0.0,
                    started_at=datetime.now(timezone.utc),
                    completed_at=datetime.now(timezone.utc)
                )
                db.add(track_meta)
            
            db.flush()
            
            for segment_data in segments_data:
                segment_index = segment_data['segment_index']
                segment_text = segment_data['text']
                start_time = segment_data['start_time']
                end_time = segment_data['end_time']
                actual_duration = segment_data['actual_duration']
                word_timings = segment_data.get('word_timings', [])
                
                text_segment = TTSTextSegment(
                    track_id=track_id,
                    track_meta_id=track_meta.id,
                    segment_index=segment_index,
                    start_time=start_time,
                    end_time=end_time,
                    duration=actual_duration,
                    status='ready'
                )
                
                text_segment.set_text_content(segment_text)
                text_segment._word_count = segment_data['word_count']
                
                db.add(text_segment)
                db.flush()
                
                if word_timings:
                    word_timing_record = TTSWordTiming(
                        segment_id=text_segment.id,
                        voice_id=voice_id
                    )
                    word_timing_record.pack_word_timings(word_timings)
                    db.add(word_timing_record)
            
            db.commit()
            logger.info(f"✅ Voice-specific metadata stored for {total_segments} segments")
            
        except Exception as e:
            logger.error(f"Error storing voice-specific metadata: {e}")
            db.rollback()
            raise

# ========================================
# VOICE SWITCHING - SAME INTERFACE
# ========================================

class VoiceNativeSwitcher:
    """Same interface, HLS-integrated processing"""
    
    def __init__(self, tts_service: VoiceNativeTTSService):
        self.tts_service = tts_service
        self.switch_locks = {}
    
    async def switch_voice(
        self,
        track_id: str,
        new_voice: str,
        db: Session
    ) -> Dict:
        """Switch voice using HLS integration"""
        try:
            logger.info(f"🎤 Voice switch: {track_id} → {new_voice} (HLS integration)")
            
            result = await self.tts_service.generate_additional_voice(
                track_id=track_id,
                new_voice_id=new_voice,
                db=db
            )
            
            return {
                'status': 'success',
                'track_id': track_id,
                'new_voice': new_voice,
                'duration': result['duration'],
                'segments': result['segments'],
                'cached': False,
                'approach': 'hls_integrated_voice_switch_with_real_progress'
            }
            
        except Exception as e:
            logger.error(f"❌ HLS-integrated voice switch error: {e}")
            raise

    async def generate_additional_voice(
        self,
        track_id: str,
        new_voice_id: str,
        db: Session
    ) -> Dict:
        """🔧 Generate additional voice with proper voice-specific handling"""
        try:
            logger.info(f"🎤 Generating additional voice:")
            logger.info(f"  - Track ID: {track_id}")
            logger.info(f"  - New Voice: {new_voice_id}")
            
            if db is None:
                with SessionLocal() as db:
                    track = db.query(Track).filter(Track.id == track_id).first()
            else:
                track = db.query(Track).filter(Track.id == track_id).first()
                    
            if not track or not track.source_text:
                raise ValueError("Track not found or has no source text")
            
            complete_audio_info = await self.tts_service._generate_complete_audio_efficient(
                text_content=track.source_text,
                voice=new_voice_id,
                track_id=f"{track_id}_{new_voice_id}"
            )
            
            await self.tts_service._integrate_with_existing_hls(
                track_id=track_id,
                audio_info=complete_audio_info,
                voice=new_voice_id
            )
            
            logger.info(f"✅ Additional voice {new_voice_id} queued for voice-specific processing")
            
            return {
                'status': 'success',
                'track_id': track_id,
                'new_voice_id': new_voice_id,
                'duration': complete_audio_info['duration'],
                'segments': complete_audio_info['estimated_segments'],
                'approach': 'voice_specific_hls_integration',
                'voice_folder': f"voice-{new_voice_id}",
                'output_directory': f"voice-{new_voice_id}",
                'voice_specific': True
            }
            
        except Exception as e:
            logger.error(f"❌ Error generating additional voice:")
            logger.error(f"  - Track ID: {track_id}")
            logger.error(f"  - Voice: {new_voice_id}")
            logger.error(f"  - Error: {e}")
            raise

# ========================================
# EXPORTS - SAME AS BEFORE
# ========================================

voice_native_tts_service = VoiceNativeTTSService()
voice_native_switcher = VoiceNativeSwitcher(voice_native_tts_service)

simplified_tts_service = voice_native_tts_service
simplified_voice_switcher = voice_native_switcher
tts_streaming_service = voice_native_tts_service

logger.info("🔧 MINIMAL SENTENCE-BASED TTS System initialized")

__all__ = [
    'voice_native_tts_service',
    'voice_native_switcher',
    'simplified_tts_service',
    'simplified_voice_switcher',
    'tts_streaming_service',
    'VoiceNativeTTSService',
    'VoiceNativeSwitcher',
    'RealTTSProgressTracker'
]