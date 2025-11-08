import asyncio
import logging
import os
from pathlib import Path
from typing import Optional, Dict, Union, List
import json
from datetime import datetime
from sqlalchemy import update, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from models import Track

logger = logging.getLogger(__name__)

class DurationManager:
    """Manages audio duration extraction with voice-aware support - NO REDIS"""

    def __init__(self):
        self.metadata_key_prefix = "audio:metadata:"
        self.extraction_locks = {}
        logger.info("DurationManager initialized without Redis")

    async def close(self):
        """Cleanup method for graceful shutdown"""
        try:
            # Clear any remaining extraction locks
            self.extraction_locks.clear()
            logger.info("DurationManager cleanup completed")
        except Exception as e:
            logger.error(f"Error during DurationManager cleanup: {str(e)}")

    # ========================================
    # ✅ NEW: Voice-Aware Duration Methods
    # ========================================

    async def get_voice_duration(
        self,
        track_id: str,
        voice_id: str,
        db_session: Union[Session, AsyncSession]
    ) -> float:
        """Get total duration for a specific voice from TTSVoiceSegment.actual_duration"""
        try:
            # Query database to aggregate actual_duration from all segments for this voice
            if isinstance(db_session, AsyncSession):
                stmt = text("""
                    SELECT COALESCE(SUM(tvs.actual_duration), 0) as total_duration
                    FROM tts_voice_segments tvs
                    JOIN tts_text_segments tts ON tvs.text_segment_id = tts.id
                    WHERE tts.track_id = :track_id 
                    AND tvs.voice_id = :voice_id 
                    AND tvs.status = 'ready'
                """)
                result = await db_session.execute(stmt, {'track_id': track_id, 'voice_id': voice_id})
                duration = result.scalar_one_or_none()
            else:
                stmt = text("""
                    SELECT COALESCE(SUM(tvs.actual_duration), 0) as total_duration
                    FROM tts_voice_segments tvs
                    JOIN tts_text_segments tts ON tvs.text_segment_id = tts.id
                    WHERE tts.track_id = :track_id 
                    AND tvs.voice_id = :voice_id 
                    AND tvs.status = 'ready'
                """)
                result = db_session.execute(stmt, {'track_id': track_id, 'voice_id': voice_id})
                duration = result.scalar_one_or_none()

            duration = float(duration) if duration is not None else 0.0

            if duration > 0:
                logger.info(f"Voice duration from DB: {track_id}/{voice_id} = {duration}s")
            
            return duration

        except Exception as e:
            logger.error(f"Error getting voice duration for {track_id}/{voice_id}: {str(e)}")
            return 0.0

    async def get_duration(
        self,
        track_id: str,
        db_session: Union[Session, AsyncSession],
        voice_id: Optional[str] = None
    ) -> float:
        """Get track duration - voice-aware for TTS tracks, regular for audio tracks"""
        try:
            # First, check if this is a TTS track and if voice_id is provided
            if voice_id:
                # Check if track is TTS type
                is_tts_track = await self._is_tts_track(track_id, db_session)
                if is_tts_track:
                    # Get voice-specific duration
                    voice_duration = await self.get_voice_duration(track_id, voice_id, db_session)
                    if voice_duration > 0:
                        return voice_duration

            # Fallback to original track duration logic (works for both TTS and regular)
            if isinstance(db_session, AsyncSession):
                stmt = text("SELECT duration FROM tracks WHERE id = :track_id")
                result = await db_session.execute(stmt, {'track_id': track_id})
                duration = result.scalar_one_or_none()
            else:
                stmt = text("SELECT duration FROM tracks WHERE id = :track_id")
                result = db_session.execute(stmt, {'track_id': track_id})
                duration = result.scalar_one_or_none()

            return float(duration) if duration is not None else 0.0

        except Exception as e:
            logger.error(f"Error getting duration: {str(e)}")
            return 0.0

    async def get_all_voice_durations(
        self,
        track_id: str,
        db_session: Union[Session, AsyncSession]
    ) -> Dict[str, float]:
        """Get durations for all voices of a TTS track"""
        try:
            # Query all voices and their total durations for this track
            if isinstance(db_session, AsyncSession):
                stmt = text("""
                    SELECT 
                        tvs.voice_id,
                        COALESCE(SUM(tvs.actual_duration), 0) as total_duration
                    FROM tts_voice_segments tvs
                    JOIN tts_text_segments tts ON tvs.text_segment_id = tts.id
                    WHERE tts.track_id = :track_id 
                    AND tvs.status = 'ready'
                    GROUP BY tvs.voice_id
                """)
                result = await db_session.execute(stmt, {'track_id': track_id})
                rows = result.fetchall()
            else:
                stmt = text("""
                    SELECT 
                        tvs.voice_id,
                        COALESCE(SUM(tvs.actual_duration), 0) as total_duration
                    FROM tts_voice_segments tvs
                    JOIN tts_text_segments tts ON tvs.text_segment_id = tts.id
                    WHERE tts.track_id = :track_id 
                    AND tvs.status = 'ready'
                    GROUP BY tvs.voice_id
                """)
                result = db_session.execute(stmt, {'track_id': track_id})
                rows = result.fetchall()

            voice_durations = {}
            for voice_id, duration in rows:
                voice_durations[voice_id] = float(duration) if duration else 0.0

            logger.info(f"All voice durations for {track_id}: {voice_durations}")
            return voice_durations

        except Exception as e:
            logger.error(f"Error getting all voice durations for {track_id}: {str(e)}")
            return {}

    async def store_voice_duration(
        self,
        track_id: str,
        voice_id: str,
        segments_data: List[Dict],
        db_session: Union[Session, AsyncSession]
    ) -> bool:
        """Store voice segment durations in TTSVoiceSegment table"""
        try:
            logger.info(f"Storing voice duration data for {track_id}/{voice_id}: {len(segments_data)} segments")
            
            # This stores actual_duration in TTSVoiceSegment table
            if isinstance(db_session, AsyncSession):
                for segment in segments_data:
                    stmt = text("""
                        UPDATE tts_voice_segments 
                        SET actual_duration = :duration, status = 'ready'
                        WHERE text_segment_id = :segment_id AND voice_id = :voice_id
                    """)
                    await db_session.execute(stmt, {
                        'duration': segment['actual_duration'],
                        'segment_id': segment['segment_id'],
                        'voice_id': voice_id
                    })
                await db_session.commit()
            else:
                for segment in segments_data:
                    stmt = text("""
                        UPDATE tts_voice_segments 
                        SET actual_duration = :duration, status = 'ready'
                        WHERE text_segment_id = :segment_id AND voice_id = :voice_id
                    """)
                    db_session.execute(stmt, {
                        'duration': segment['actual_duration'],
                        'segment_id': segment['segment_id'],
                        'voice_id': voice_id
                    })
                db_session.commit()

            logger.info(f"Successfully stored voice duration data for {track_id}/{voice_id}")
            return True

        except Exception as e:
            logger.error(f"Error storing voice duration for {track_id}/{voice_id}: {str(e)}")
            if hasattr(db_session, 'rollback'):
                db_session.rollback()
            return False

    # ========================================
    # ✅ EXISTING METHODS - Enhanced with voice support
    # ========================================

    async def process_upload(
        self,
        file_path: Path,
        track_id: str,
        db_session: Union[Session, AsyncSession],
        is_voice_switch: bool = False,  # ✅ NEW: Flag to prevent track.duration updates
        voice_id: Optional[str] = None  # ✅ NEW: Voice context for TTS
    ) -> Dict:
        """Extract duration during upload - Enhanced with voice-aware support"""
        try:
            # Extract metadata first
            metadata = await self._extract_metadata(file_path)
            if not metadata:
                logger.error(f"[{track_id}] Could not extract metadata")
                raise ValueError("Could not extract metadata")

            logger.info(f"[{track_id}] Successfully extracted metadata: {json.dumps(metadata, indent=2)}")

            # ✅ NEW: Skip track.duration update for voice switches to avoid lock contention
            if not is_voice_switch:
                try:
                    # Prepare the update data
                    update_data = {
                        'duration': float(metadata['duration']),
                        'bit_rate': int(metadata['bit_rate']),
                        'sample_rate': int(metadata['sample_rate']),
                        'channels': int(metadata['channels']),
                        'codec': metadata['codec'],
                        'format': metadata['format'],
                        'audio_metadata': json.dumps({
                            'size': metadata['size'],
                            'extracted_at': metadata['extracted_at']
                        }),
                        'updated_at': datetime.utcnow()
                    }

                    # Log current track state before update
                    current_track = db_session.query(Track).filter(Track.id == track_id).first()
                    logger.info(f"[{track_id}] Current track state: {current_track.__dict__ if current_track else 'Not found'}")

                    # Check if we're already in a transaction
                    was_active = db_session.is_active

                    try:
                        # If not in a transaction, start one
                        if not was_active:
                            db_session.begin()

                        # Perform the update
                        result = db_session.query(Track).filter(Track.id == track_id).update(update_data)
                        logger.info(f"[{track_id}] Database update result: {result} rows affected")

                        # Only commit if we started the transaction
                        if not was_active:
                            db_session.commit()
                        
                    except Exception as e:
                        # Only rollback if we started the transaction
                        if not was_active and db_session.is_active:
                            db_session.rollback()
                        raise

                    # Verify the update was successful
                    updated_track = db_session.query(Track).filter(Track.id == track_id).first()
                    if updated_track:
                        logger.info(
                            f"[{track_id}] Track update verified:\n"
                            f"Duration: {updated_track.duration}\n"
                            f"Bit Rate: {updated_track.bit_rate}\n"
                            f"Sample Rate: {updated_track.sample_rate}\n"
                            f"Channels: {updated_track.channels}\n"
                            f"Codec: {updated_track.codec}\n"
                            f"Format: {updated_track.format}\n"
                            f"Size: {json.loads(updated_track.audio_metadata).get('size') if updated_track.audio_metadata else 'Not set'}"
                        )
                    else:
                        logger.error(f"[{track_id}] Track not found after update!")

                except Exception as db_error:
                    logger.error(
                        f"[{track_id}] Database error:\n"
                        f"Error type: {type(db_error)}\n"
                        f"Error message: {str(db_error)}\n",
                        exc_info=True
                    )
                    raise
            else:
                logger.info(f"[{track_id}] Skipping track.duration update (voice switch mode)")

            # Return the extracted metadata for use in callbacks
            return metadata

        except Exception as e:
            logger.error(
                f"[{track_id}] Error in process_upload:\n"
                f"Error type: {type(e)}\n"
                f"Error message: {str(e)}\n"
                f"File path: {file_path}\n",
                exc_info=True
            )
            raise
        finally:
            # Ensure any remaining transactions are cleaned up
            if hasattr(db_session, 'rollback') and db_session.is_active:
                db_session.rollback()

    async def bulk_get_durations(
        self,
        track_ids: List[str],
        db_session: Union[Session, AsyncSession]
    ) -> Dict[str, float]:
        """Get durations for multiple tracks efficiently - NO CACHE"""
        try:
            durations = {}
            
            # Get durations directly from database
            if isinstance(db_session, AsyncSession):
                stmt = text("SELECT id, duration FROM tracks WHERE id = ANY(:track_ids)")
                result = await db_session.execute(stmt, {'track_ids': track_ids})
                rows = result.fetchall()
            else:
                stmt = text("SELECT id, duration FROM tracks WHERE id = ANY(:track_ids)")
                result = db_session.execute(stmt, {'track_ids': track_ids})
                rows = result.fetchall()

            # Process results
            for track_id, duration in rows:
                if duration is not None:
                    durations[track_id] = float(duration)

            return durations

        except Exception as e:
            logger.error(f"Error in bulk duration fetch: {str(e)}")
            return {track_id: 0 for track_id in track_ids}

    # ========================================
    # ✅ HELPER METHODS
    # ========================================

    async def _is_tts_track(
        self,
        track_id: str,
        db_session: Union[Session, AsyncSession]
    ) -> bool:
        """Check if track is TTS type"""
        try:
            if isinstance(db_session, AsyncSession):
                stmt = text("SELECT track_type FROM tracks WHERE id = :track_id")
                result = await db_session.execute(stmt, {'track_id': track_id})
                track_type = result.scalar_one_or_none()
            else:
                stmt = text("SELECT track_type FROM tracks WHERE id = :track_id")
                result = db_session.execute(stmt, {'track_id': track_id})
                track_type = result.scalar_one_or_none()

            return track_type == 'tts'

        except Exception as e:
            logger.error(f"Error checking track type: {str(e)}")
            return False

    async def _extract_metadata(self, file_path: Union[str, Path]) -> Optional[Dict]:
        """Extract audio metadata using ffprobe - UNCHANGED"""
        try:
            if isinstance(file_path, str):
                file_path = Path(file_path)

            if not file_path.exists() or not file_path.is_file():
                logger.error(f"Path invalid: {file_path}")
                raise FileNotFoundError(f"File not found: {file_path}")

            # Get file size
            file_size = file_path.stat().st_size
            logger.info(f"File size from stat: {file_size} bytes")

            file_key = str(file_path)
            if file_key not in self.extraction_locks:
                self.extraction_locks[file_key] = asyncio.Lock()

            async with self.extraction_locks[file_key]:
                cmd = [
                    'ffprobe',
                    '-v', 'error',
                    '-select_streams', 'a:0',    # First audio stream only
                    '-show_entries', 
                    'format=duration,bit_rate,format_name,format_long_name',  # Format info
                    '-show_streams',  # Stream info for codec, sample rate, channels
                    '-of', 'json',
                    str(file_path)
                ]

                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )

                stdout, stderr = await process.communicate()

                if process.returncode != 0:
                    logger.error(f"FFprobe failed: {stderr.decode()}")
                    raise RuntimeError(f"FFprobe failed: {stderr.decode()}")

                # Parse FFprobe output
                probe_data = json.loads(stdout.decode())
                streams = probe_data.get('streams', [])
                format_data = probe_data.get('format', {})

                # Get audio stream data (first audio stream)
                audio_stream = next(
                    (s for s in streams if s.get('codec_type') == 'audio'), 
                    streams[0] if streams else {}
                )

                # Extract bit rate with fallback
                bit_rate = format_data.get('bit_rate') or audio_stream.get('bit_rate', '0')
                bit_rate = int(bit_rate) if str(bit_rate).isdigit() else 0

                metadata = {
                    'duration': float(format_data.get('duration', 0)),
                    'size': file_size,
                    'format': format_data.get('format_name', 'unknown'),
                    'codec': audio_stream.get('codec_name', 'unknown'),
                    'sample_rate': int(audio_stream.get('sample_rate', 44100)),
                    'channels': int(audio_stream.get('channels', 2)),
                    'bit_rate': bit_rate,
                    'extracted_at': datetime.utcnow().isoformat()
                }

                logger.info(f"Successfully extracted metadata: {json.dumps(metadata, indent=2)}")
                return metadata

        except Exception as e:
            logger.error(f"Error in _extract_metadata: {str(e)}", exc_info=True)
            return None

    def format_duration(self, seconds: float) -> str:
        """Format duration in seconds to MM:SS or HH:MM:SS - UNCHANGED"""
        try:
            if not seconds:
                return '0:00'
                
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = int(seconds % 60)
            
            if h > 0:
                return f"{h}:{m:02d}:{s:02d}"
            else:
                return f"{m}:{s:02d}"
        except:
            return "0:00"

    # ========================================
    # ✅ REMOVED: Redis methods (no longer needed)
    # ========================================
    
    # Removed: init_redis, _get_available_redis, _set_in_all_redis, clear_duration, close

# Initialize global instance
duration_manager = DurationManager()