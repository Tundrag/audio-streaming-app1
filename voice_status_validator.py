"""
Voice Status Validator - Auto-healing service for false 'failed' statuses
Runs every 5 minutes to detect and repair status inconsistencies
"""
import asyncio
import logging
from pathlib import Path
from typing import List
from sqlalchemy.orm import Session
from database import SessionLocal
from models import VoiceGenerationStatus
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class VoiceStatusValidator:
    """Validates and repairs inconsistent voice generation statuses"""

    def __init__(self):
        self.running = False
        self.check_interval = 300  # 5 minutes

    async def start(self):
        """Start the background validation service"""
        if self.running:
            logger.warning("Voice status validator already running")
            return

        self.running = True
        logger.info("🔍 Voice status validator started (checking every 5 minutes)")

        while self.running:
            try:
                await self.validate_and_repair_failed_voices()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                logger.info("Voice status validator cancelled")
                break
            except Exception as e:
                logger.error(f"Voice status validator error: {e}", exc_info=True)
                await asyncio.sleep(self.check_interval)

    async def stop(self):
        """Stop the background validation service"""
        self.running = False
        logger.info("Voice status validator stopped")

    async def validate_and_repair_failed_voices(self):
        """Check all failed voices and repair false failures"""
        db = SessionLocal()
        try:
            # Query all failed voices
            failed_voices = db.query(VoiceGenerationStatus).filter(
                VoiceGenerationStatus.status == 'failed'
            ).all()

            if not failed_voices:
                logger.debug("No failed voices to validate")
                return

            logger.info(f"🔍 Validating {len(failed_voices)} failed voices")

            repaired_count = 0
            for record in failed_voices:
                is_valid = await self._validate_hls_segments(record.track_id, record.voice_id)

                if is_valid:
                    # False failure - auto-heal
                    record.status = 'complete'
                    record.error_message = None
                    record.completed_at = datetime.now(timezone.utc)

                    logger.info(f"✅ Auto-healed: {record.track_id}/{record.voice_id} (segments exist, status was incorrectly 'failed')")
                    repaired_count += 1

            if repaired_count > 0:
                db.commit()
                logger.info(f"🔧 Auto-healed {repaired_count} false failures")

        except Exception as e:
            db.rollback()
            logger.error(f"Error validating failed voices: {e}", exc_info=True)
        finally:
            db.close()

    async def _validate_hls_segments(self, track_id: str, voice_id: str) -> bool:
        """Check if HLS segments exist and are complete"""
        try:
            # Get segment directory
            home = Path.home()
            segment_base = home / ".hls_streaming" / "segments"

            if voice_id:
                segment_dir = segment_base / track_id / f"voice-{voice_id}"
            else:
                segment_dir = segment_base / track_id

            # Check master playlist
            master_playlist = segment_dir / "master.m3u8"
            if not master_playlist.exists():
                return False

            # Check variant playlist
            variant_dir = segment_dir / "default"
            variant_playlist = variant_dir / "playlist.m3u8"

            if not variant_dir.exists() or not variant_playlist.exists():
                return False

            # Check playlist completeness
            playlist_content = variant_playlist.read_text()
            if "#EXT-X-ENDLIST" not in playlist_content:
                return False

            # Check segments exist
            segment_files = list(variant_dir.glob("segment_*.ts"))
            if len(segment_files) == 0:
                return False

            return True

        except Exception as e:
            logger.debug(f"Validation check failed for {track_id}/{voice_id}: {e}")
            return False

# Singleton instance
voice_status_validator = VoiceStatusValidator()
