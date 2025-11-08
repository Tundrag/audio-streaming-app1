#chunked_upload
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request, BackgroundTasks
from sqlalchemy.orm import Session
from typing import Dict, Optional
import os
import uuid
import time
import shutil
from pathlib import Path
import logging
import asyncio
import json
from datetime import datetime, timezone
from sqlalchemy import and_, or_

from database import get_db
from models import Track, Album
from auth import login_required

router = APIRouter()
logger = logging.getLogger(__name__)

# REPLACED: In-memory dict with Redis-backed state for multi-container support
# active_uploads: Dict[str, Dict] = {}
# active_uploads_lock = asyncio.Lock()

# Import Redis upload state manager V2 (using generic RedisStateManager)
# OLD: from redis_state.state.upload_legacy import get_redis_upload_state
from redis_state.state.upload import get_redis_upload_state

# Get Redis state manager (will be initialized on first use)
# Now uses generic RedisStateManager("upload") for consistency
redis_upload_state = get_redis_upload_state()

# Task to periodically clean up cancelled uploads
cleanup_task = None

async def cleanup_failed_track(
    track_id: str, 
    db: Session,
    file_path: Optional[str] = None,
    error_message: str = "Upload failed",
    background_tasks: Optional[BackgroundTasks] = None
):
    """Thoroughly clean up a failed track - delete from DB, MEGA, and all temp files"""
    from storage import storage
    
    # If background tasks provided, run cleanup in background
    if background_tasks:
        background_tasks.add_task(
            cleanup_failed_track, 
            track_id=track_id,
            db=db,
            file_path=file_path,
            error_message=error_message
        )
        logger.info(f"Scheduled background cleanup for track {track_id}: {error_message}")
        return
        
    try:
        logger.info(f"Starting comprehensive cleanup for track {track_id}: {error_message}")
        
        # Get track from DB
        track = db.query(Track).filter(Track.id == track_id).first()
        if not track:
            logger.warning(f"Track {track_id} not found in database, nothing to clean up")
            return
            
        # Get file path if not provided
        if not file_path and track:
            file_path = track.file_path
        
        # Log track details for debugging
        logger.info(f"Cleaning up track: {track_id}, title: {track.title}, status: {track.upload_status}, duration: {track.duration}, file: {file_path}")

        # 1. Delete from MEGA if file_path exists and isn't a temp path
        # (temp paths wouldn't have been uploaded to MEGA yet)
        if file_path and not "temp_" in file_path:
            try:
                await storage.delete_media(file_path)
                logger.info(f"Deleted file from MEGA: {file_path}")
            except Exception as e:
                logger.error(f"Error deleting file from MEGA: {e}")
                
        # 2. Clean up HLS segments if any
        try:
            from hls_streaming import stream_manager
            await stream_manager.cleanup_stream(track_id)
            logger.info(f"Cleaned up HLS segments for track {track_id}")
        except Exception as e:
            logger.error(f"Error cleaning up HLS segments: {e}")
            
        # 3. Clean up duration cache
        try:
            from duration_manager import duration_manager
            await duration_manager.clear_duration(track_id)
            logger.info(f"Cleared duration cache for track {track_id}")
        except Exception as e:
            logger.error(f"Error clearing duration cache: {e}")
            
        # 4. Clean up any files in background preparation queue
        try:
            if hasattr(storage, 'preparation_manager'):
                if hasattr(storage.preparation_manager, 'cancel_task'):
                    storage.preparation_manager.cancel_task(track_id)
                    logger.info(f"Cancelled any pending HLS preparation for track {track_id}")
                else:
                    logger.info(f"No cancel_task method available for track {track_id}")
        except Exception as e:
            logger.error(f"Error cancelling background tasks: {e}")
            
        # 5. Delete track from DB
        try:
            db.delete(track)
            db.commit()
            logger.info(f"Deleted track {track_id} from database")
        except Exception as e:
            db.rollback()
            logger.error(f"Database error during track deletion: {e}")
            
        # 6. Clean up temp files and upload locks
        try:
            # Remove upload lock
            await storage.remove_upload_lock(track_id) if hasattr(storage, 'remove_upload_lock') else None
            
            # Clean up temp files
            import glob
            
            # Clean all possible temp file patterns
            temp_patterns = [
                f"/tmp/media_storage/temp_{track_id}_*",
                f"/tmp/media_storage/{track_id}_*",
                f"/tmp/media_storage/chunks/{track_id}*",
                f"/tmp/media_storage/chunks/*_{track_id}*"
            ]
            
            for pattern in temp_patterns:
                for path in glob.glob(pattern):
                    if os.path.isdir(path):
                        shutil.rmtree(path)
                        logger.info(f"Removed directory: {path}")
                    else:
                        os.unlink(path)
                        logger.info(f"Removed file: {path}")
                        
            logger.info(f"Cleaned up temporary files for track {track_id}")
        except Exception as e:
            logger.error(f"Error cleaning up temp files: {e}")
            
        logger.info(f"Comprehensive cleanup completed for track {track_id}")
            
    except Exception as e:
        logger.error(f"Error during cleanup for track {track_id}: {e}")

async def scan_for_incomplete_uploads(db: Session, stall_threshold_minutes: int = 30):
    """
    Scan for and clean up uploads that are truly incomplete:
    - Processing status with duration of 0 (NEVER clean up tracks with duration > 0)
    - Minimum age requirement to avoid race conditions
    """
    try:
        from datetime import datetime, timezone, timedelta
        
        cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=stall_threshold_minutes)
        
        # Find incomplete tracks - ONLY ones with zero duration that are old enough
        incomplete_tracks = db.query(Track).filter(
            and_(
                Track.upload_status == 'processing',
                Track.created_at < cutoff_time,  # Track must be older than threshold
                Track.duration == 0,  # ONLY zero duration tracks
                or_(
                    Track.updated_at < cutoff_time,  # Not updated recently
                    Track.file_path.like('%temp_%')  # Has temp path
                )
            )
        ).all()
        
        if not incomplete_tracks:
            logger.info("No incomplete uploads found")
            return
            
        logger.info(f"Found {len(incomplete_tracks)} incomplete uploads to clean up")
        
        cleaned_count = 0
        for track in incomplete_tracks:
            track_id = str(track.id)
            
            # Log details before cleanup
            logger.info(f"Cleaning up stalled incomplete upload: {track_id}, title: {track.title}, duration: {track.duration}, path: {track.file_path}, age: {datetime.now(timezone.utc) - track.created_at}")
            await cleanup_failed_track(
                track_id=track_id,
                db=db,
                file_path=track.file_path,
                error_message=f"Stalled incomplete upload with zero duration (age={int((datetime.now(timezone.utc) - track.created_at).total_seconds() / 60)}min)"
            )
            cleaned_count += 1
            
        if cleaned_count > 0:
            logger.info(f"Cleaned up {cleaned_count} incomplete uploads")
                
    except Exception as e:
        logger.error(f"Error scanning for incomplete uploads: {e}")

async def start_cleanup_task():
    """Start the background task to clean up cancelled uploads"""
    global cleanup_task
    if cleanup_task is None:
        cleanup_task = asyncio.create_task(cleanup_cancelled_uploads())
        logger.info("Started cleanup task for cancelled uploads")

async def cleanup_cancelled_uploads():
    """Periodically clean up cancelled uploads (now using Redis)"""
    while True:
        try:
            await asyncio.sleep(300)  # Check every 5 minutes

            # Get current time for comparison
            now = time.time()
            to_remove = []

            # Get all active uploads from Redis
            all_uploads = redis_upload_state.get_all_active_uploads()

            for session in all_uploads:
                upload_id = session.get("upload_id")
                if not upload_id:
                    continue

                # If upload is cancelled or more than 30 minutes old, clean it up
                if session.get("status") == "cancelled" or \
                   (session.get("last_updated", 0) and (now - session.get("last_updated", 0)) > 1800):
                    to_remove.append(upload_id)

            # Remove the identified uploads
            for upload_id in to_remove:
                session = redis_upload_state.get_session(upload_id)
                if not session:
                    continue

                logger.info(f"Cleaning up upload {upload_id} (status: {session.get('status')})")

                # Extra cleanup for chunk directory if it exists
                chunks_dir = session.get("chunks_dir")
                if chunks_dir and Path(chunks_dir).exists():
                    try:
                        shutil.rmtree(chunks_dir)
                    except Exception as e:
                        logger.error(f"Error removing chunks directory: {e}")

                # Remove from Redis
                redis_upload_state.delete_session(upload_id)

            if to_remove:
                logger.info(f"Cleaned up {len(to_remove)} stale uploads")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in upload cleanup task: {e}")
            await asyncio.sleep(60)  # Wait a minute before trying again

@router.post("/albums/{album_id}/tracks/init-upload")
async def init_chunked_upload(
    album_id: str, 
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user = Depends(login_required)
):
    """Initialize a new chunked upload session without creating a DB record yet"""
    try:
        # Start cleanup task if not already running
        background_tasks.add_task(start_cleanup_task)
        
        # Parse request body
        request_data = await request.json()
        
        # Get creator ID based on user type
        creator_id = current_user.id if hasattr(current_user, 'is_creator') and current_user.is_creator else current_user.created_by
        
        # Validate album exists
        album = db.query(Album).filter(Album.id == album_id).first()
        if not album:
            raise HTTPException(status_code=404, detail="Album not found")
        
        upload_id = request_data.get("uploadId")
        filename = request_data.get("filename")
        file_size = request_data.get("fileSize")
        visibility_status = request_data.get("visibility_status", "visible")

        # Validate visibility_status based on user role
        valid_statuses = ["visible", "hidden_from_users", "hidden_from_all"]
        if visibility_status not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid visibility_status. Must be one of: {', '.join(valid_statuses)}")

        # Team members cannot hide from team or all - only from users
        if current_user.is_team and not current_user.is_creator:
            if visibility_status == "hidden_from_all":
                raise HTTPException(status_code=403, detail="Team members cannot hide content from team. Only 'visible' or 'hidden_from_users' allowed.")

        if not all([upload_id, filename, file_size]):
            raise HTTPException(status_code=400, detail="Missing required fields")
        
        # Get track count for order - will use this later when creating the track
        track_count = db.query(Track).filter(Track.album_id == album.id).count()
        
        # Generate track ID - will use this to identify the upload
        track_id = str(uuid.uuid4())
        
        # Create a temporary file_path for when the track eventually gets created
        temp_file_path = f"/media/audio/temp_{track_id}_{filename}"
        
        # Extract the real title
        real_title = os.path.splitext(filename)[0]
        
        # Set up chunks directory in shared media storage
        chunks_dir = Path(f"/tmp/media_storage/chunks/{upload_id}")
        logger.warning(f"🔥 DEBUG: About to create chunks dir: {chunks_dir}")
        try:
            chunks_dir.mkdir(parents=True, exist_ok=True)
            logger.warning(f"🔥 DEBUG: Successfully created chunks directory: {chunks_dir}")
        except Exception as e:
            logger.error(f"Failed to create chunks directory {chunks_dir}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to create upload directory: {str(e)}")

        # Store all information in Redis instead of in-memory dict
        upload_data = {
            "upload_id": upload_id,
            "track_id": track_id,
            "album_id": album_id,
            "filename": filename,
            "title": real_title,
            "creator_id": creator_id,
            "file_path": temp_file_path,
            "file_size": file_size,
            "track_order": track_count + 1,
            "chunks_dir": str(chunks_dir),
            "total_chunks": 0,
            "received_chunks": 0,
            "status": "initialized",
            "track_created": False,  # Flag to indicate if track has been created in DB
            "visibility_status": visibility_status
        }

        # Create session in Redis
        success = redis_upload_state.create_session(upload_data)
        if not success:
            logger.error(f"Failed to create Redis session for upload {upload_id}")
            raise HTTPException(status_code=500, detail="Failed to initialize upload session")

        logger.info(f"Initialized chunked upload {upload_id} for track {track_id} in Redis (no DB record yet)")
        
        return {
            "trackId": track_id,
            "uploadId": upload_id,
            "message": "Upload initialized successfully"
        }
        
    except Exception as e:
        logger.error(f"Error initializing upload: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/albums/{album_id}/tracks/upload-chunk")
async def upload_chunk(
    album_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user = Depends(login_required)
):
    """Handle individual chunk upload, creating DB record only after all chunks received.
       As soon as the Track row is created, acquire the simple_track_lock to prevent regeneration."""
    try:
        # Get form data
        form = await request.form()

        # Extract values with client-side parameter names
        uploadId = form.get("uploadId")
        chunkIndex = int(form.get("chunkIndex"))
        totalChunks = int(form.get("totalChunks"))
        chunk = form.get("chunk")

        logger.info(f"Processing chunk upload for uploadId: {uploadId}, chunkIndex: {chunkIndex}/{totalChunks}")

        if not all([uploadId, chunk, chunkIndex is not None, totalChunks is not None]):
            raise HTTPException(status_code=400, detail="Missing required fields")

        # Validate the upload exists in Redis
        upload_info = redis_upload_state.get_session(uploadId)
        if not upload_info:
            logger.error(f"Upload not found in Redis: {uploadId}")
            raise HTTPException(status_code=404, detail="Upload not found")

        track_id = upload_info.get("track_id")

        # Check if upload was cancelled - return early with cancelled flag
        if upload_info["status"] == "cancelled":
            logger.info(f"Skipping chunk {chunkIndex} for cancelled upload {uploadId}")
            return {"message": "Upload cancelled by user", "cancelled": True}

        if upload_info["album_id"] != album_id:
            logger.error(f"Album ID mismatch: expected {upload_info['album_id']}, got {album_id}")
            raise HTTPException(status_code=400, detail="Album ID mismatch")

        # Update total chunks if necessary
        if upload_info["total_chunks"] == 0:
            redis_upload_state.update_session(uploadId, {"total_chunks": totalChunks})

        # Save chunk to temporary location
        chunks_dir_str = upload_info["chunks_dir"]

        # Fix old paths from before migration to shared storage
        if chunks_dir_str.startswith("/tmp/chunks/"):
            upload_id_from_path = chunks_dir_str.split("/")[-1]
            chunks_dir_str = f"/tmp/media_storage/chunks/{upload_id_from_path}"
            logger.info(f"Migrated old chunk path to shared storage: {chunks_dir_str}")
            # Update Redis with new path
            redis_upload_state.update_session(uploadId, {"chunks_dir": chunks_dir_str})

        chunks_dir = Path(chunks_dir_str)

        # Ensure directory exists (important for multi-container setups)
        chunks_dir.mkdir(parents=True, exist_ok=True)

        chunk_path = chunks_dir / f"chunk_{chunkIndex}"

        try:
            with open(chunk_path, "wb") as f:
                content = await chunk.read()
                f.write(content)
        except Exception as chunk_error:
            logger.error(f"Error saving chunk {chunkIndex}: {chunk_error}")
            raise HTTPException(status_code=500, detail=f"Error saving chunk: {str(chunk_error)}")

        # Update tracking in Redis
        # Register this chunk as received
        redis_upload_state.register_chunk(uploadId, chunkIndex)

        # Get updated session info
        upload_info = redis_upload_state.get_session(uploadId)
        if not upload_info:
            raise HTTPException(status_code=404, detail="Upload not found")

        if upload_info["status"] == "cancelled":
            logger.info(f"Upload {uploadId} was cancelled after chunk {chunkIndex} was saved")
            return {"message": "Upload cancelled by user", "cancelled": True}

        # Get received chunk count from Redis
        received_chunks = redis_upload_state.get_received_chunks_count(uploadId)

        logger.info(f"Received chunk {chunkIndex+1}/{totalChunks} for upload {uploadId} (total received: {received_chunks})")

        # If all chunks received and no Track yet, create Track + LOCK IT immediately
        if received_chunks == upload_info["total_chunks"] and not upload_info.get("track_created", False):
            logger.info(f"All chunks received for upload {uploadId}, creating track in database")

            # Update status to chunks_complete in Redis
            redis_upload_state.update_session(uploadId, {
                "status": "chunks_complete",
                "received_chunks": received_chunks
            })

            new_track = Track(
                    id=track_id,
                    title=upload_info["title"],
                    file_path=upload_info["file_path"],
                    album_id=album_id,
                    created_by_id=upload_info["creator_id"],
                    order=upload_info["track_order"],
                    duration=0,  # Will be updated after metadata extraction
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                    upload_status='processing',
                    codec=None,
                    bit_rate=None,
                    sample_rate=None,
                    channels=None,
                    format=None,
                    audio_metadata=None,
                    access_count=0,
                    segmentation_status='incomplete',
                    visibility_status=upload_info.get("visibility_status", "visible"),
                    tier_requirements={
                        "is_public": True,
                        "minimum_cents": 0,
                        "allowed_tier_ids": []
                    }
                )

            db.add(new_track)
            db.commit()

            # Update Redis to mark track as created
            redis_upload_state.update_session(uploadId, {"track_created": True})
            logger.info(f"Created track in database: {track_id}")

            # 🔒 Acquire lock IMMEDIATELY so user cannot trigger regeneration after row exists
            try:
                from status_lock import status_lock
                locked, reason = await status_lock.try_lock_voice(
                    track_id=track_id,
                    voice_id=None,
                    process_type="initial",   # uploading/initial ingestion
                    db=db,
                )
                if not locked:
                    # If we cannot lock right after creation, roll back the row to be safe
                    logger.warning(f"Track {track_id} created but busy: {reason}. Deleting row.")
                    try:
                        # Reload in case of stale state
                        tr = db.query(Track).filter(Track.id == track_id).first()
                        if tr:
                            db.delete(tr)
                            db.commit()
                    except Exception as de:
                        db.rollback()
                        logger.error(f"Failed to delete newly created track {track_id}: {de}")
                    raise HTTPException(status_code=409, detail=f"Track is busy: {reason}")

                logger.info(f"✅ LOCKED track {track_id} right after creation (chunk phase)")

            except HTTPException:
                # Re-raise HTTP errors
                raise
            except Exception as e:
                logger.error(f"Lock acquisition failed for new track {track_id}: {e}")
                # Clean up the row on failure to lock
                try:
                    tr = db.query(Track).filter(Track.id == track_id).first()
                    if tr:
                        db.delete(tr)
                        db.commit()
                except Exception:
                    db.rollback()
                raise HTTPException(status_code=500, detail="Failed to lock track after creation")

        return {"message": "Chunk uploaded successfully"}

    except Exception as e:
        logger.error(f"Error uploading chunk: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/albums/{album_id}/tracks/finalize-upload")
async def finalize_upload(
    album_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user = Depends(login_required)
):
    """Combine chunks, update/create track if needed, and pass the already-held lock through to storage → worker.
       Worker will release the lock on success/failure. If we fail before queueing the worker, unlock here."""
    from storage import storage

    async def _unlock_on_early_failure(tid: str):
        # Only used if we fail before the background worker picks it up
        try:
            from status_lock import status_lock
            s = next(get_db())
            try:
                await status_lock.unlock_voice(tid, None, success=False, db=s)
                logger.info(f"Finalize early-failure — UNLOCKED track {tid}")
            finally:
                try: s.close()
                except Exception: pass
        except Exception as e:
            logger.warning(f"Early-failure unlock failed for {tid}: {e}")

    try:
        # Parse request body
        request_data = await request.json()
        upload_id = request_data.get("uploadId")
        track_id = request_data.get("trackId")

        if not upload_id or not track_id:
            raise HTTPException(status_code=400, detail="Missing uploadId or trackId")

        # Get upload info from Redis
        upload_info = redis_upload_state.get_session(upload_id)
        if not upload_info:
            raise HTTPException(status_code=404, detail="Upload not found")

        if upload_info["status"] != "chunks_complete":
            received = redis_upload_state.get_received_chunks_count(upload_id)
            raise HTTPException(
                status_code=400,
                detail=f"Upload not ready for finalization ({received}/{upload_info['total_chunks']} chunks)"
            )

        # Get creator ID
        creator_id = current_user.id if getattr(current_user, 'is_creator', False) else current_user.created_by

        # Ensure Track exists (if chunk-phase failed right before creation)
        track = db.query(Track).filter(Track.id == track_id).first()
        if not track and not upload_info.get("track_created", False):
            logger.info(f"Creating track during finalization for upload {upload_id}")
            track = Track(
                id=track_id,
                title=upload_info["title"],
                file_path=upload_info["file_path"],
                album_id=album_id,
                created_by_id=creator_id,
                order=upload_info["track_order"],
                duration=0,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
                upload_status='processing',
                codec=None,
                bit_rate=None,
                sample_rate=None,
                channels=None,
                format=None,
                audio_metadata=None,
                access_count=0,
                segmentation_status='incomplete',
                visibility_status=upload_info.get("visibility_status", "visible"),
                tier_requirements={
                    "is_public": True,
                    "minimum_cents": 0,
                    "allowed_tier_ids": []
                }
            )
            db.add(track)
            db.commit()
            upload_info["track_created"] = True

            # If we had to create the Track here (rare), lock it now (to keep invariant)
            try:
                from status_lock import status_lock
                locked, reason = await status_lock.try_lock_voice(
                    track_id=track_id, voice_id=None, process_type="initial", db=db
                )
                if not locked:
                    logger.warning(f"Track {track_id} created in finalize but busy: {reason}")
                    # Clean the row so we don't leave a dangling busy entry
                    try:
                        tr = db.query(Track).filter(Track.id == track_id).first()
                        if tr: db.delete(tr); db.commit()
                    except Exception:
                        db.rollback()
                    raise HTTPException(status_code=409, detail=f"Track is busy: {reason}")
                logger.info(f"✅ LOCKED track {track_id} (finalize fallback path)")
            except Exception as e:
                logger.error(f"Lock acquisition failed in finalize for {track_id}: {e}")
                try:
                    tr = db.query(Track).filter(Track.id == track_id).first()
                    if tr: db.delete(tr); db.commit()
                except Exception:
                    db.rollback()
                raise HTTPException(status_code=500, detail="Failed to lock track after creation (finalize)")

        elif not track:
            raise HTTPException(status_code=404, detail="Track not found")

        # Combine chunks into final file
        chunks_dir = Path(upload_info["chunks_dir"])
        temp_file_path = Path(f"/tmp/media_storage/{track_id}_{upload_info['filename']}")
        temp_file_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(temp_file_path, "wb") as outfile:
                for i in range(upload_info["total_chunks"]):
                    chunk_path = chunks_dir / f"chunk_{i}"
                    if not chunk_path.exists():
                        logger.error(f"Missing chunk {i} during finalization for track {track_id}")
                        await cleanup_failed_track(
                            track_id=track_id,
                            db=db,
                            error_message=f"Missing chunks during finalization",
                            background_tasks=background_tasks
                        )
                        # unlock because worker won't be queued
                        await _unlock_on_early_failure(track_id)
                        raise HTTPException(status_code=400, detail="Missing chunks during finalization")

                    with open(chunk_path, "rb") as infile:
                        shutil.copyfileobj(infile, outfile)

            # Clean up chunks after successful combination
            shutil.rmtree(chunks_dir)

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error combining chunks: {e}")
            await cleanup_failed_track(
                track_id=track_id,
                db=db,
                error_message=f"Error combining chunks: {str(e)}",
                background_tasks=background_tasks
            )
            # unlock because worker won't be queued
            await _unlock_on_early_failure(track_id)
            raise HTTPException(status_code=500, detail=f"Error combining chunks: {str(e)}")

        # Update track status
        track.upload_status = "processing"
        track.updated_at = datetime.now(timezone.utc)
        db.commit()

        # Minimal file-like wrapper used by storage.upload_media
        class FileWrapper:
            def __init__(self, file_path, filename):
                self.file_path = file_path
                self.file = None
                self.filename = filename
            async def read(self, size=-1):
                if self.file is None:
                    self.file = open(self.file_path, "rb")
                return await asyncio.to_thread(self.file.read, size)
            def close(self):
                if self.file:
                    self.file.close()

        file_obj = FileWrapper(temp_file_path, upload_info["filename"])

        try:
            # ✅ Tell storage we ALREADY HOLD the lock
            file_url, metadata = await storage.upload_media(
                file=file_obj,
                media_type="audio",
                creator_id=creator_id,
                db=db,
                track_id=track_id,
                lock_preacquired=True      # <<< IMPORTANT
            )

            # Update track record with URL + any metadata extracted by early stage (if returned)
            track.file_path = file_url
            if metadata:
                track.duration = metadata.get('duration', track.duration or 0)
                track.codec = metadata.get('codec', track.codec)
                track.bit_rate = metadata.get('bit_rate', track.bit_rate)
                track.sample_rate = metadata.get('sample_rate', track.sample_rate)
                track.channels = metadata.get('channels', track.channels)
                track.format = metadata.get('format', track.format)
                track.audio_metadata = metadata
            track.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(track)

            # Clean Redis upload session entry
            redis_upload_state.delete_session(upload_id)

            return {
                "id": track_id,
                "title": track.title,
                "file_path": file_url,
                "album_id": str(album_id),
                "duration": track.duration,
                "formatted_duration": format_duration(track.duration),
                "order": track.order,
                "created_at": track.created_at.isoformat() if track.created_at else None,
                "updated_at": track.updated_at.isoformat() if track.updated_at else None,
                "codec": track.codec,
                "bit_rate": track.bit_rate,
                "sample_rate": track.sample_rate,
                "channels": track.channels,
                "format": track.format
            }

        except HTTPException as he:
            logger.error(f"Storage upload failed in finalize: {he.detail}")
            await cleanup_failed_track(
                track_id=track_id,
                db=db,
                error_message=f"Storage upload failed: {he.detail}",
                background_tasks=background_tasks
            )
            # unlock because worker won't be queued (storage aborted)
            await _unlock_on_early_failure(track_id)
            raise
        except Exception as e:
            logger.error(f"Error in storage upload during finalization: {e}")
            await cleanup_failed_track(
                track_id=track_id,
                db=db,
                error_message=f"Storage upload failed: {str(e)}",
                background_tasks=background_tasks
            )
            await _unlock_on_early_failure(track_id)
            raise HTTPException(status_code=500, detail=f"Storage upload failed: {str(e)}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error finalizing upload: {str(e)}")
        # Try to unlock just in case we failed before worker queue
        try:
            tid = request_data.get("trackId") if 'request_data' in locals() else None
            if tid:
                await _unlock_on_early_failure(tid)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))




@router.post("/albums/{album_id}/tracks/cancel-upload")
async def cancel_upload(
    album_id: str,
    request: Request,
    current_user = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Cancel an in-progress upload and clean up all resources"""
    try:
        request_data = await request.json()
        upload_id = request_data.get("uploadId")
        
        if not upload_id:
            raise HTTPException(status_code=400, detail="Missing uploadId")
        
        logger.info(f"Cancelling upload: {upload_id}")
        
        # Make sure album exists
        album = db.query(Album).filter(Album.id == album_id).first()
        if not album:
            raise HTTPException(status_code=404, detail="Album not found")

        # Get upload info from Redis
        upload_info = redis_upload_state.get_session(upload_id)
        if not upload_info:
            logger.info(f"Upload not found for cancellation: {upload_id}")
            return {"message": "Upload not found"}

        # Verify upload belongs to this album
        if upload_info["album_id"] != album_id:
            raise HTTPException(status_code=403, detail="Upload does not belong to this album")

        # Update status to cancelled in Redis
        redis_upload_state.update_session(upload_id, {"status": "cancelled"})

        # If a track was created, delete it from the database
        if upload_info.get("track_created", False) and "track_id" in upload_info:
            track_id = upload_info["track_id"]
            # Use the comprehensive cleanup function
            await cleanup_failed_track(
                track_id=track_id,
                db=db,
                error_message="Upload cancelled by user"
            )
            logger.info(f"Cleaned up track {track_id} due to cancelled upload")
        else:
            # Clean up chunks even if no track was created
            chunks_dir = Path(upload_info["chunks_dir"])
            if chunks_dir.exists():
                try:
                    shutil.rmtree(chunks_dir)
                    logger.info(f"Removed chunks directory for upload {upload_id}")
                except Exception as e:
                    logger.error(f"Error removing chunks directory: {e}")

        # Session will be cleaned up by periodic cleanup task
        logger.info(f"Upload {upload_id} cancelled successfully")
        
        return {"message": "Upload cancelled successfully", "cancelled": True}
        
    except Exception as e:
        logger.error(f"Error cancelling upload: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

async def start_cleanup_background_task(app=None):
    """Start background task to periodically clean up incomplete uploads"""
    async def run_periodic_cleanup():
        logger.info("Periodic cleanup task is now running")
        while True:
            try:
                # Sleep first to allow app to start
                await asyncio.sleep(300)  # 5 minutes delay on startup
                
                # Get DB session
                db = next(get_db())
                try:
                    logger.info("Running scheduled periodic upload cleanup scan")
                    await scan_for_incomplete_uploads(db)
                    logger.info("Periodic cleanup scan completed")
                finally:
                    db.close()
                    
                # Wait until next run
                await asyncio.sleep(3600)  # Run every hour
                
            except asyncio.CancelledError:
                logger.info("Periodic cleanup task was cancelled")
                break
            except Exception as e:
                logger.error(f"Error in periodic cleanup task: {e}")
                await asyncio.sleep(300)  # Wait 5 minutes on error
    
    # Create the task
    task = asyncio.create_task(run_periodic_cleanup())
    logger.info("Started periodic cleanup task for incomplete uploads")
    return task  # Return the task for reference

# Helper function for formatting duration
def format_duration(seconds):
    """Format duration in seconds to MM:SS format"""
    if seconds is None:
        return "00:00"
    
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins:02d}:{secs:02d}"