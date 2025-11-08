# track_status_manager.py
"""
Centralized Track Status Management with Voice Awareness

STATUS FLOW:
  generating → segmenting → complete
                         ↘ failed

CRITICAL: Always updates processing_voice field for voice-aware status tracking
UPDATED: Now also updates voice_generation_status table for per-voice tracking
"""

import logging
from enum import Enum
from datetime import datetime, timezone
from typing import Optional

import anyio
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

logger = logging.getLogger(__name__)


class TrackStatus(str, Enum):
    """Python enum for status values (NOT a database enum)"""
    GENERATING = "generating"
    SEGMENTING = "segmenting"  
    COMPLETE = "complete"
    FAILED = "failed"


def _is_async(db) -> bool:
    """Check if database session is async or sync"""
    return isinstance(db, AsyncSession)


async def _flush(db):
    """Flush changes immediately (handles both async and sync sessions)"""
    if _is_async(db):
        await db.flush()
    else:
        await anyio.to_thread.run_sync(db.flush)


async def _commit(db):
    """Commit transaction (handles both async and sync sessions)"""
    if _is_async(db):
        await db.commit()
    else:
        await anyio.to_thread.run_sync(db.commit)


async def _rollback(db):
    """Rollback transaction (handles both async and sync sessions)"""
    if _is_async(db):
        await db.rollback()
    else:
        await anyio.to_thread.run_sync(db.rollback)


class TrackStatusManager:
    """
    Centralized track status management with voice-aware tracking.
    
    Simple 4-state system: generating → segmenting → complete (or failed)
    """
    
    @staticmethod
    async def mark_generating(track, db, process_type: str = 'tts', voice: str = None):
        """
        Mark track as GENERATING with optional voice tracking.

        Args:
            track: Track model instance
            db: Database session (sync or async)
            process_type: Type of processing (tts, voice_generation, etc.)
            voice: Voice being generated (CRITICAL for voice-aware checks)
        """
        from models import VoiceGenerationStatus

        track.status = TrackStatus.GENERATING.value
        track.processing_type = process_type
        track.processing_voice = voice  # ALWAYS set this, even if None
        track.failed_at = None
        track.processing_locked_at = datetime.now(timezone.utc)
        track.upload_status = 'processing'

        # ✅ Update voice_generation_status table for per-voice tracking
        if voice:
            now = datetime.now(timezone.utc)

            # Check if entry exists
            if _is_async(db):
                result = await db.execute(
                    select(VoiceGenerationStatus).where(
                        VoiceGenerationStatus.track_id == track.id,
                        VoiceGenerationStatus.voice_id == voice
                    )
                )
                voice_status = result.scalar_one_or_none()
            else:
                voice_status = await anyio.to_thread.run_sync(
                    lambda: db.query(VoiceGenerationStatus).filter(
                        VoiceGenerationStatus.track_id == track.id,
                        VoiceGenerationStatus.voice_id == voice
                    ).first()
                )

            if voice_status:
                # Update existing entry
                voice_status.status = 'generating'
                voice_status.started_at = now
                voice_status.completed_at = None
                voice_status.error_message = None
            else:
                # Create new entry
                voice_status = VoiceGenerationStatus(
                    track_id=track.id,
                    voice_id=voice,
                    status='generating',
                    started_at=now
                )
                db.add(voice_status)

        # Flush immediately to ensure other requests see this status
        await _flush(db)
        await _commit(db)

        voice_info = f" (voice: {voice})" if voice else ""
        logger.info(f"ATOMIC: Track {track.id} -> GENERATING ({process_type}){voice_info}")
    
    @staticmethod
    async def mark_segmenting(track, db, voice: str = None):
        """
        Mark track as SEGMENTING.
        
        Args:
            track: Track model instance
            db: Database session (sync or async)
            voice: Voice being segmented
        """
        track.status = TrackStatus.SEGMENTING.value
        track.processing_type = 'segmentation'
        track.processing_voice = voice  # Keep voice tracking through segmentation
        
        await _flush(db)
        await _commit(db)
        
        voice_info = f" (voice: {voice})" if voice else ""
        logger.info(f"ATOMIC: Track {track.id} -> SEGMENTING{voice_info}")
    
    @staticmethod
    async def mark_complete(track, db, voice: str = None):
        """
        Mark track as COMPLETE (final success state).

        Args:
            track: Track model instance
            db: Database session (sync or async)
            voice: Voice that completed (if voice-specific)
        """
        from models import VoiceGenerationStatus

        # Capture voice before clearing
        completed_voice = voice or track.processing_voice

        track.status = TrackStatus.COMPLETE.value
        track.failed_at = None
        track.processing_locked_at = None
        track.processing_type = None
        track.processing_voice = None  # Clear voice when complete
        track.upload_status = 'complete'

        # ✅ Update voice_generation_status table
        if completed_voice:
            now = datetime.now(timezone.utc)

            # Check if entry exists
            if _is_async(db):
                result = await db.execute(
                    select(VoiceGenerationStatus).where(
                        VoiceGenerationStatus.track_id == track.id,
                        VoiceGenerationStatus.voice_id == completed_voice
                    )
                )
                voice_status = result.scalar_one_or_none()
            else:
                voice_status = await anyio.to_thread.run_sync(
                    lambda: db.query(VoiceGenerationStatus).filter(
                        VoiceGenerationStatus.track_id == track.id,
                        VoiceGenerationStatus.voice_id == completed_voice
                    ).first()
                )

            if voice_status:
                # Update existing entry
                voice_status.status = 'complete'
                voice_status.completed_at = now
                voice_status.error_message = None
            else:
                # Create new entry (for legacy tracks without voice tracking)
                voice_status = VoiceGenerationStatus(
                    track_id=track.id,
                    voice_id=completed_voice,
                    status='complete',
                    started_at=now,  # Best guess
                    completed_at=now
                )
                db.add(voice_status)

        await _flush(db)
        await _commit(db)

        voice_info = f" (voice: {completed_voice})" if completed_voice else ""
        logger.info(f"ATOMIC: Track {track.id} -> COMPLETE{voice_info}")
    
    @staticmethod
    async def mark_failed(track, db, error: Exception, pipeline: str, cleanup_func=None):
        """
        Mark track as FAILED (terminal state).

        Args:
            track: Track model instance
            db: Database session (sync or async)
            error: Exception that caused failure
            pipeline: Which pipeline failed (tts/voice_generation/segmentation)
            cleanup_func: Optional cleanup function to run
        """
        from models import VoiceGenerationStatus

        failed_voice = track.processing_voice  # Capture before clearing

        track.status = TrackStatus.FAILED.value
        track.failed_at = datetime.now(timezone.utc)
        track.processing_locked_at = None
        track.processing_type = None
        track.processing_voice = None  # Clear voice on failure
        track.upload_status = 'failed'

        # ✅ Update voice_generation_status table
        if failed_voice:
            now = datetime.now(timezone.utc)
            error_msg = str(error)[:500]  # Truncate long errors

            # Check if entry exists
            if _is_async(db):
                result = await db.execute(
                    select(VoiceGenerationStatus).where(
                        VoiceGenerationStatus.track_id == track.id,
                        VoiceGenerationStatus.voice_id == failed_voice
                    )
                )
                voice_status = result.scalar_one_or_none()
            else:
                voice_status = await anyio.to_thread.run_sync(
                    lambda: db.query(VoiceGenerationStatus).filter(
                        VoiceGenerationStatus.track_id == track.id,
                        VoiceGenerationStatus.voice_id == failed_voice
                    ).first()
                )

            if voice_status:
                # Update existing entry
                voice_status.status = 'failed'
                voice_status.completed_at = now
                voice_status.error_message = error_msg
            else:
                # Create new entry
                voice_status = VoiceGenerationStatus(
                    track_id=track.id,
                    voice_id=failed_voice,
                    status='failed',
                    started_at=now,  # Best guess
                    completed_at=now,
                    error_message=error_msg
                )
                db.add(voice_status)

        await _flush(db)
        await _commit(db)

        voice_info = f" (voice: {failed_voice})" if failed_voice else ""
        logger.error(
            f"ATOMIC: Track {track.id} -> FAILED{voice_info}\n"
            f"  Pipeline: {pipeline}\n"
            f"  Error: {str(error)}"
        )

        # Run cleanup if provided
        if cleanup_func:
            try:
                await cleanup_func()
            except Exception as cleanup_error:
                logger.error(f"Cleanup failed: {cleanup_error}")
    
    @staticmethod
    def is_failed(track) -> bool:
        """Check if track is in failed state"""
        return track.status == TrackStatus.FAILED.value
    
    @staticmethod
    def is_complete(track) -> bool:
        """Check if track is complete and ready"""
        return track.status == TrackStatus.COMPLETE.value
    
    @staticmethod
    def is_processing(track) -> bool:
        """Check if track is being processed (any voice)"""
        return track.status in (
            TrackStatus.GENERATING.value, 
            TrackStatus.SEGMENTING.value
        )
    
    @staticmethod
    def is_processing_voice(track, voice: str) -> bool:
        """Check if specific voice is being processed"""
        return (
            TrackStatusManager.is_processing(track) and 
            track.processing_voice == voice
        )
    
    @staticmethod
    def get_status_info(track) -> dict:
        """Get comprehensive status information"""
        return {
            'status': track.status,
            'is_failed': TrackStatusManager.is_failed(track),
            'is_complete': TrackStatusManager.is_complete(track),
            'is_processing': TrackStatusManager.is_processing(track),
            'failed_at': track.failed_at.isoformat() if track.failed_at else None,
            'processing_type': track.processing_type,
            'processing_voice': track.processing_voice,
            'default_voice': track.default_voice if hasattr(track, 'default_voice') else None
        }