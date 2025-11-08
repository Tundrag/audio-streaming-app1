from redis_state.config import redis_client, REDIS_HOST, REDIS_PORT, REDIS_PASSWORD


from sync.sync_service import PatreonSyncService
from sync.sync_worker import PatreonSyncWorker
from models import Campaign  # Import Campaign model
from platform_tiers import platform_router
from patreon_routes import router as patreon_router
from functools import wraps
from core.download_workers import DownloadStage, download_manager
from core.track_download_workers import track_download_manager  # For tracks
from upload_queue import upload_queue
from mega_upload_manager import mega_upload_manager
from metadata_extraction import metadata_queue
from pydantic import BaseModel
from fastapi.responses import StreamingResponse
import random
import time
from fastapi import APIRouter, Depends, Request, HTTPException
from typing import List, Optional
from starlette.requests import ClientDisconnect
from contextvars import ContextVar
import psutil
import psutil
import functools
from contextlib import contextmanager
from time import perf_counter
from worker_config import worker_config
from fastapi.responses import FileResponse
from pathlib import Path
from dateutil.relativedelta import relativedelta
from sqlalchemy import and_, exists
from typing import Union 
from fastapi import FastAPI
from starlette.middleware import Middleware
from fastapi.middleware.cors import CORSMiddleware
from chunked_upload import router as chunked_upload_router
from discord_integration import discord  # Import only discord instance
from discord_routes import router as discord_router
from core.download_cleanup_service import download_cleanup_service
from core.my_downloads import router as my_downloads_router
from chunked_upload import start_cleanup_background_task
from book_request import get_user_book_requests, get_all_book_requests, get_user_book_request_quota
from book_request import add_pending_request_count
from book_request import book_request_pages_router, book_request_router, book_request_ws_manager
from activity_logs_router import add_activity_logs_count
from comment_routes import comment_router
from sync.kofi_sync_service import KofiSyncService, KofiSyncWorker
from notifications import notifications_router
from broadcast_router import broadcast_router
from forum_settings_routes import forum_settings_router
from mega_s4_client import mega_s4_client
from document_extraction_service import router as document_router
from cache_busting import cache_busted_url_for, APP_VERSION
from read_along_cache import start_cache_cleanup_task
from activity_logs_router import router as activity_logs_router
from user_preferences import router as user_preferences_router
from scheduled_visibility_routes import router as scheduled_visibility_router
from progress import router as progress_router, get_in_progress_tracks as get_in_progress_tracks_from_router

from credit_reservation import CreditReservationService
from guest_trial_routes import router as guest_trial_router
from status_lock import status_lock
from enhanced_tts_api_voice import router as enhanced_tts_router
from voice_sample_api import router as sample_router
from enhanced_read_along_api import router as read_along_router
from tts_websocket import tts_websocket_router

from enhanced_app_routes_voice import (
    serve_hls_master, 
    serve_variant_playlist, 
    serve_segment,
    player,
    get_segment_progress,
    get_track_metadata,
    serve_media,
    get_available_voices_for_track,
    update_session_activity,
    get_available_voices_from_db,
    read_along_player

)
# app.py


from fastapi import (
    FastAPI, 
    UploadFile, 
    File, 
    Form, 
    HTTPException, 
    Request, 
    Depends,
    Response,
    Header,
    Query,
    BackgroundTasks,
    APIRouter
)
from fastapi.security import (
    HTTPBearer, 
    HTTPAuthorizationCredentials,
    OAuth2PasswordBearer
)
from fastapi.responses import (
    JSONResponse, 
    RedirectResponse, 
    FileResponse
)
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from urllib.parse import unquote
# SQLAlchemy imports
from sqlalchemy import (
    func, 
    distinct,
    text,
    and_, 
    or_, 
    desc, 
    Boolean, 
    Integer,
    String,
    Column,
    DateTime,
    ForeignKey,
    Enum,
    JSON,
    cast,
    select
)
from sqlalchemy.orm import (
    Session,
    relationship,
    joinedload
)
from sqlalchemy.ext.asyncio import AsyncSession  # Changed this line
from sqlalchemy.exc import SQLAlchemyError


# Model imports
from models import (
    BookRequest, 
    BookRequestStatus,
    User, 
    UserRole, 
    Permission, 
    PatreonTier,
    CampaignTier,
    UserAlbumManagement,
    Album,
    Track,
    TrackPlays,
    Comment,
    CommentLike,
    CommentReport,
    PlaybackProgress,
    UserSession,
    Notification,
    AuditLog,
    AuditLogType,
    NotificationType,
    UserTier,
    SegmentMetadata,
    SegmentStatus,
    UserDownload,
    DownloadType,
    PlatformType,
    AvailableVoice,
    TTSTrackMeta,      # ← Add this for bulk_update_tier_voices
    TTSTextSegment,    # ← Add this if using text segments  
    TTSWordTiming      # ← Add this if using word timings
)
# Local application imports
from auth import login_required
from kofi_routes import router as kofi_router
from models import KofiSettings, KofiWebhook
from kofi_service import kofi_service
from pin_management import PinManagementRouter, get_pin_history, datetime, timezone, and_, ScheduledTask
from session_manager import SessionManager
from album_service import AlbumService
from sqlalchemy.orm import joinedload
from storage import storage
from hls_streaming import (
    stream_manager,
    _get_file_hash,
    SEGMENT_DIR,
    DEFAULT_BITRATE
)
from stream_limiter import SessionStreamLimiter
from background_preparation import (
    BackgroundPreparationManager,
    PreparationStatus,
    WorkerStatus
)
from database import get_db, Base, SessionLocal
from redis_state.config import (
    redis_client,
    REDIS_HOST,
    REDIS_PORT,
    REDIS_PASSWORD
)
from duration_manager import duration_manager
from patreon_client import patreon_client

# Standard library imports
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from enum import Flag, auto, Enum as PyEnum
from functools import wraps
from pathlib import Path
from typing import Dict, Optional, List, Any
from uuid import UUID, uuid4
import asyncio
import aiofiles
import httpx
import json
import logging
import numpy as np
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
import re
from dotenv import load_dotenv
from typing import List, Tuple, Set
from typing import Optional, List, Dict, Any
import math

from concurrent.futures import ThreadPoolExecutor
# Security imports
from jose import JWTError, jwt
from passlib.context import CryptContext
load_dotenv()


logging.basicConfig(
    level=logging.WARNING,  # Changed from DEBUG to WARNING for less noise
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
async def verify_cloud_setup():
    """Verify cloud storage setup on startup"""
    try:
        # Check MEGA connection
        logger.info("Testing MEGA authentication...")
        result = subprocess.run(['mega-whoami'], capture_output=True, text=True)
        if "Not logged in" in result.stdout:
            logger.error("Not logged in to MEGA. Please run setup_mega.sh")
            return False
        else:
            logger.info(f"MEGA auth successful: {result.stdout.strip()}")
            
        # Create required directories first
        paths = [
            "/audio-streaming-app1/media",
            "/audio-streaming-app1/media/audio",
            "/audio-streaming-app1/media/images"
        ]
        
        logger.info("Creating/checking MEGA directories...")
        for path in paths:
            # Try to create directory with -p flag
            create_result = subprocess.run(
                ['mega-mkdir', '-p', path], 
                capture_output=True, 
                text=True
            )
            if create_result.returncode == 0:
                logger.info(f"Directory created/verified: {path}")
            else:
                logger.error(f"Failed to create directory: {path}")
                logger.error(f"Error: {create_result.stderr}")
        
        # Test upload
        test_path = "/audio-streaming-app1/media/.test"
        logger.info("Testing MEGA upload capability...")
        
        with open("/tmp/.test", "w") as f:
            f.write("test content")
            
        upload_result = subprocess.run(
            ['mega-put', '/tmp/.test', test_path],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if upload_result.returncode == 0:
            logger.info("Test upload successful")
            # Clean up
            subprocess.run(['mega-rm', test_path], capture_output=True)
            if os.path.exists("/tmp/.test"):
                os.remove("/tmp/.test")
        else:
            logger.error(f"Upload failed: {upload_result.stderr}")
            
        return True
        
    except Exception as e:
        logger.error(f"Cloud storage verification failed: {str(e)}")
        return True  # Still return True to allow app to continue



        
async def periodic_sync_task():
    """Background task for periodic tier sync"""
    while True:
        try:
            db = next(get_db())
            # Remove campaign_id filter for creators
            creators = db.query(User).filter(
                User.role == UserRole.CREATOR,
                User.is_active == True
            ).all()

            logger.info(f"🔍 Found {len(creators)} creators for periodic sync.")

            if not creators:
                logger.warning("⚠️ No active creators found for periodic sync! Check database.")
            else:
                for creator in creators:
                    try:
                        logger.info(f"🔄 Running periodic sync for creator: {creator.email} (ID: {creator.id})")
                        if patreon_sync_service._should_sync():
                            await patreon_sync_service.sync_campaign_tiers(creator.id)
                            logger.info(f"✅ Sync complete for {creator.email}")
                        else:
                            logger.info(f"⏩ Skipping sync for {creator.email} - not due yet")
                    except Exception as e:
                        logger.error(f"❌ Error in periodic sync for creator {creator.email}: {str(e)}")
                        continue
        except Exception as e:
            logger.error(f"❌ Error in periodic tier sync: {str(e)}")
        finally:
            # Wait for 7 days before next sync
            await asyncio.sleep(60 * 60 * 24 * 7)
        
        
  
SECRET_KEY = os.getenv("SECRET_KEY", "").encode()
SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY")
if not SESSION_SECRET_KEY:
    logger.warning("SESSION_SECRET_KEY not set, using a random key")
    SESSION_SECRET_KEY = secrets.token_urlsafe(32)

import secrets

# Replace the section around line 324-331 with this:

session_manager = SessionManager(
    secret_key=SESSION_SECRET_KEY,
    cookie_settings={
        "httponly": True,
        "secure": False,  # Set to True in production
        "samesite": "lax",
        "path": "/"
    }
)

# Initialize WebSocket auth BEFORE importing forum routes
logger.info("Initializing WebSocket authentication...")
import websocket_auth
websocket_auth.init_websocket_auth(session_manager)
logger.info("WebSocket authentication initialized successfully")

# NOW import forum routes (after websocket auth is initialized)
from forum_routes import forum_router

redis = redis_client


async def _cleanup_cache_task(cache_cleanup_task):
    """Stop cache cleanup task gracefully."""
    if cache_cleanup_task and not cache_cleanup_task.done():
        cache_cleanup_task.cancel()
        try:
            await cache_cleanup_task
        except asyncio.CancelledError:
            pass

async def _cleanup_document_extraction():
    """Clean up document extraction service and files."""
    logger.info("Cleaning up document extraction service...")
    
    # Try cleanup endpoint first
    try:
        response = requests.delete("http://localhost:8000/api/documents/cleanup", timeout=5)
        if response.status_code == 200:
            logger.info(f"✅ Document cleanup: {response.json().get('message', 'completed')}")
            return
    except Exception as e:
        logger.debug(f"Cleanup endpoint unavailable: {e}")
    
    # Fallback to direct cleanup
    document_temp_folder = "/tmp/media_storage/document_extraction"
    if os.path.exists(document_temp_folder):
        shutil.rmtree(document_temp_folder)
        logger.info("✅ Document extraction folder cleaned up")

async def _cleanup_text_storage():
    """Clean up track-centric text storage service."""
    logger.info("Cleaning up track-centric text storage service...")
    from text_storage_service import text_storage_service
    await text_storage_service.cleanup()
    logger.info("✅ Track-centric text storage cleanup completed")

async def _cleanup_discord(get_db):
    """Clean up Discord integration."""
    logger.info("Cleaning up Discord integration...")
    db = next(get_db())
    try:
        await discord.cleanup(db)
    except Exception:
        await discord.cleanup()
    finally:
        db.close()

async def _cleanup_sync_services(patreon_sync_service, kofi_sync_service, enable_patreon, enable_kofi):
    """Stop sync services if enabled."""
    if enable_patreon and patreon_sync_service and hasattr(patreon_sync_service, "_worker"):
        logger.info("Stopping Patreon sync service...")
        await patreon_sync_service._worker.stop()
        await patreon_sync_service.stop_periodic_task()
    
    if enable_kofi and kofi_sync_service and hasattr(kofi_sync_service, "_worker"):
        logger.info("Stopping Ko-fi sync service...")
        await kofi_sync_service._worker.stop()
        await kofi_sync_service.stop_periodic_task()

async def _cleanup_download_managers(download_manager, track_download_manager):
    """Stop download managers."""
    logger.info("Stopping download managers...")
    for worker in download_manager.workers:
        worker._is_running = False
    await download_manager.stop()
    await track_download_manager.stop()

async def _cleanup_temp_directories(temp_dirs):
    """Remove temporary directories."""
    logger.info("Cleaning up temporary directories...")
    for dir_path in temp_dirs:
        if Path(dir_path).exists():
            shutil.rmtree(dir_path)
            logger.info(f"Cleaned up directory: {dir_path}")

async def cleanup_stale_voice_generations():
    """Periodic cleanup of voice generation records stuck in 'generating' state > 90 minutes"""
    from database import get_async_db
    from models import VoiceGenerationStatus
    from sqlalchemy import delete
    from datetime import datetime, timezone, timedelta

    while True:
        try:
            await asyncio.sleep(300)  # Every 5 minutes

            async for db in get_async_db():
                try:
                    cutoff = datetime.now(timezone.utc) - timedelta(minutes=90)

                    result = await db.execute(
                        delete(VoiceGenerationStatus)
                        .where(VoiceGenerationStatus.status == 'generating')
                        .where(VoiceGenerationStatus.started_at < cutoff)
                    )

                    if result.rowcount > 0:
                        await db.commit()
                        logger.warning(
                            f"🧹 Cleaned up {result.rowcount} stale voice generation records "
                            f"(older than 90 minutes)"
                        )
                    break
                except Exception as e:
                    logger.error(f"Error in cleanup iteration: {e}")
                    await db.rollback()
                    break

        except asyncio.CancelledError:
            logger.info("Voice generation cleanup task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in cleanup_stale_voice_generations: {e}")
            await asyncio.sleep(60)  # Wait 1 minute before retry on error

@asynccontextmanager
async def lifespan(app: FastAPI):
    global patreon_sync_service, kofi_sync_service, stream_limiter

    ENABLE_PATREON_SYNC = False
    ENABLE_KOFI_SYNC = False
    temp_dirs = [
        "/tmp/mega_upload",
        "/tmp/mega_stream",
        "/tmp/mega_downloads/tracks",
        "/tmp/media_storage",
        "/tmp/media_storage/document_extraction"
    ]

    # Initialize service references
    patreon_sync_service = None
    kofi_sync_service = None
    cache_cleanup_task = None
    orphan_cleanup_task = None
    voice_cleanup_task = None
    voice_status_validator_task = None

    try:
        # ============================================================
        # STARTUP PHASE
        # ============================================================
        
        # Start cache cleanup task
        logger.info("Starting read-along cache cleanup background task...")
        from read_along_cache import start_cache_cleanup_task
        cache_cleanup_task = asyncio.create_task(start_cache_cleanup_task())
        if not hasattr(app.state, "background_tasks"):
            app.state.background_tasks = []
        app.state.background_tasks.append(cache_cleanup_task)
        
        # Initialize worker configuration
        logger.info("Initializing worker configuration...")
        from worker_config import worker_config
        
        # Cleanup orphaned track locks
        logger.info("🔧 Cleaning up orphaned track locks...")
        from status_lock import status_lock
        with SessionLocal() as startup_db:
            cleared_count = await status_lock.clear_all_on_startup(startup_db)
        if cleared_count > 0:
            logger.warning(f"⚠️ Cleared {cleared_count} orphaned track locks from previous session")

        logger.info("🔧 Starting periodic lock cleanup...")
        await status_lock.start_periodic_cleanup()

        # Start voice generation cleanup task
        logger.info("🧹 Starting stale voice generation cleanup task...")
        voice_cleanup_task = asyncio.create_task(cleanup_stale_voice_generations())
        app.state.background_tasks.append(voice_cleanup_task)

        # Start voice status validator
        logger.info("🔍 Starting voice status validator (auto-heals false failures every 5 minutes)...")
        from voice_status_validator import voice_status_validator
        voice_status_validator_task = asyncio.create_task(voice_status_validator.start())
        app.state.background_tasks.append(voice_status_validator_task)

        # Start visibility scheduler worker
        logger.info("🕐 Starting scheduled visibility worker...")
        from visibility_scheduler_worker import start_worker as start_visibility_worker
        await start_visibility_worker()

        # Start MEGA S4 client
        logger.info("🚀 Starting MEGA S4 client...")
        await mega_s4_client.start()
        
        # Run system recovery check
        logger.info("🔧 Running system recovery check...")
        from core.download_cleanup_service import download_recovery_service
        download_recovery_service.get_db_func = get_db
        await download_recovery_service.full_recovery_on_startup()
        
        # Start queues and managers
        logger.info("Starting metadata extraction queue...")
        await metadata_queue.start()
        
        logger.info("Starting upload queue...")
        await upload_queue.start()
        
        logger.info("Starting MEGA upload manager...")
        await mega_upload_manager.start()
        
        # Initialize storage system
        logger.info("Initializing storage system...")
        storage._initialize_directories()
        
        # Start background preparation system
        logger.info(f"Starting background preparation system with {worker_config.worker_configs['background']['max_workers']} workers")
        await storage.preparation_manager.start()
        
        # Start download managers
        logger.info(f"Starting download manager with {worker_config.worker_configs['mega_upload']['max_workers']} workers...")
        from core.download_workers import download_manager
        await download_manager.start()
        
        from core.track_download_workers import track_download_manager
        await track_download_manager.start()
        
        # Start download cleanup service
        logger.info("Starting download cleanup service...")
        download_cleanup_service.get_db_func = get_db
        download_cleanup_service.start()
        
        # Start chunked upload cleanup
        logger.info("Starting chunked upload cleanup background task...")
        cleanup_task = await start_cleanup_background_task(app)
        app.state.background_tasks.append(cleanup_task)
        app.state.cleanup_task = cleanup_task
        
        # Initialize Discord integration
        logger.info("Initializing Discord integration...")
        await discord.initialize()
        
        # Start orphan folder cleanup
        logger.info("🧹 Starting orphan folder cleanup service...")
        await cleanup_orphaned_folders()
        orphan_cleanup_task = asyncio.create_task(periodic_orphan_cleanup())
        
        # Verify cloud storage
        logger.info("Verifying cloud storage...")
        cloud_status = await verify_cloud_setup()
        if not cloud_status:
            logger.warning("Cloud storage verification failed but continuing...")
        
        # Initialize stream manager
        await stream_manager.initialize()
        
        # Initialize track-centric text storage
        logger.info("Initializing track-centric text storage service with 15GB TTL cache...")
        from text_storage_service import initialize_text_storage
        await initialize_text_storage()
        
        # Initialize document extraction service
        logger.info("Initializing document extraction service...")
        from document_extraction_service import init_document_service
        init_document_service()
        asyncio.create_task(periodic_document_cleanup())
        
        # Initialize sync services
        logger.info("Initializing sync services...")
        creators = []
        with SessionLocal() as startup_db:
            db_creators = startup_db.query(User).filter(
            and_(
                User.role == UserRole.CREATOR,
                User.is_active == True,
            )
            ).all()
            creators = [
                {"id": creator.id, "email": creator.email}
                for creator in db_creators
            ]
        
        if not creators:
            logger.warning("No active creators found for initial sync!")
        
        # Initialize Patreon sync service
        if ENABLE_PATREON_SYNC:
            logger.info("Initializing Patreon sync service...")
            patreon_sync_service = PatreonSyncService(db_factory=SessionLocal)
            patreon_sync_worker = PatreonSyncWorker(storage.preparation_manager, get_db)
            patreon_sync_worker._enabled = ENABLE_PATREON_SYNC
            await patreon_sync_worker.start()

            await patreon_sync_service.initialize(
                background_manager=storage.preparation_manager,
                db_factory=get_db,
                enabled=ENABLE_PATREON_SYNC,
                sync_worker=patreon_sync_worker
            )
            
            await patreon_sync_service.start_periodic_task()
            
            for creator in creators:
                try:
                    patreon_sync_result = await patreon_sync_worker.queue_sync(creator["id"], initial=True)
                    logger.info(f"✅ Queued initial Patreon sync for creator {creator['id']}: {patreon_sync_result}")
                except Exception as e:
                    logger.error(f"❌ Error queueing Patreon sync for creator {creator['id']}: {str(e)}")
        
        # Initialize Ko-fi sync service
        if ENABLE_KOFI_SYNC:
            logger.info("Initializing Ko-fi sync service...")
            from sync.kofi_sync_service import KofiSyncService, KofiSyncWorker
            kofi_sync_service = KofiSyncService(db_factory=SessionLocal)
            kofi_sync_worker = KofiSyncWorker(storage.preparation_manager, get_db)
            kofi_sync_worker._enabled = ENABLE_KOFI_SYNC
            await kofi_sync_worker.start()
            
            await kofi_sync_service.initialize(
                background_manager=storage.preparation_manager,
                db_factory=get_db,
                enabled=ENABLE_KOFI_SYNC,
                sync_worker=kofi_sync_worker
            )
            
            await kofi_sync_service.start_periodic_task()
            
            for creator in creators:
                try:
                    kofi_sync_result = await kofi_sync_worker.queue_sync(creator["id"], initial=True)
                    logger.info(f"✅ Queued initial Ko-fi sync for creator {creator['id']}: {kofi_sync_result}")
                except Exception as e:
                    logger.error(f"❌ Error queueing Ko-fi sync for creator {creator['id']}: {str(e)}")
        
        # Create temporary working directories
        for dir_path in temp_dirs:
            Path(dir_path).mkdir(parents=True, exist_ok=True)

        # Note: WebSocketManager now handles Redis pub/sub automatically
        # No need to manually start Redis subscriber

        logger.info("🎯 Application initialization complete - Document extraction and text storage active")

        yield

        logger.info("🔧 Stopping periodic lock cleanup...")
        await status_lock.stop_periodic_cleanup()
        
        # ============================================================
        # SHUTDOWN PHASE
        # ============================================================
        logger.info("Beginning application shutdown...")
        
        # Stop cache cleanup task
        logger.info("Stopping read-along cache cleanup task...")
        await _cleanup_cache_task(cache_cleanup_task)

        # Stop visibility scheduler worker
        logger.info("Stopping visibility scheduler worker...")
        from visibility_scheduler_worker import stop_worker as stop_visibility_worker
        await stop_visibility_worker()

        # Stop voice status validator
        if voice_status_validator_task:
            logger.info("Stopping voice status validator...")
            from voice_status_validator import voice_status_validator
            await voice_status_validator.stop()
            voice_status_validator_task.cancel()
            try:
                await voice_status_validator_task
            except asyncio.CancelledError:
                pass

        # Stop orphan cleanup task
        if orphan_cleanup_task:
            orphan_cleanup_task.cancel()
            await orphan_cleanup_task
        
        # Clean up document extraction
        await _cleanup_document_extraction()
        
        # Clean up text storage
        await _cleanup_text_storage()
        
        # Clean up Discord
        await _cleanup_discord(get_db)
        
        # Stop queues
        logger.info("Stopping metadata extraction queue...")
        await metadata_queue.stop()
        
        logger.info("Stopping upload queue...")
        await upload_queue.stop()
        
        # Stop sync services
        await _cleanup_sync_services(patreon_sync_service, kofi_sync_service, ENABLE_PATREON_SYNC, ENABLE_KOFI_SYNC)
        
        # Stop background preparation
        logger.info("Stopping background preparation...")
        await storage.preparation_manager.stop()
        
        # Stop download managers
        await _cleanup_download_managers(download_manager, track_download_manager)
        
        # Stop download cleanup service
        logger.info("Stopping download cleanup service...")
        download_cleanup_service.stop()
        
        # Stop MEGA upload manager
        logger.info("Stopping MEGA upload manager...")
        await mega_upload_manager.stop()
        
        # Close MEGA S4 client
        logger.info("🛑 Closing MEGA S4 client...")
        await mega_s4_client.close()
        
        # Clean up stream services
        logger.info("Cleaning up stream services...")
        await stream_manager.cleanup()
        await duration_manager.close()

        # Clean up broadcast WebSocket manager
        logger.info("Cleaning up broadcast WebSocket manager...")
        from broadcast_router import broadcast_ws_manager
        await broadcast_ws_manager.close()
        
        # Clean up storage
        await storage.cleanup()
        
        # Clean up temporary directories
        await _cleanup_temp_directories(temp_dirs)
        
        logger.info("🔒 Application shutdown complete - Document extraction and text storage cleanup completed")
        
    except Exception as e:
        logger.error(f"Error during application lifecycle: {str(e)}")
        
        # Run cleanup using same helper functions
        await _cleanup_cache_task(cache_cleanup_task)
        await _cleanup_document_extraction()
        await _cleanup_text_storage()
        await _cleanup_discord(get_db)
        await _cleanup_sync_services(patreon_sync_service, kofi_sync_service, ENABLE_PATREON_SYNC, ENABLE_KOFI_SYNC)
        
        await storage.preparation_manager.stop()
        await _cleanup_download_managers(download_manager, track_download_manager)
        download_cleanup_service.stop()
        
        await mega_upload_manager.stop()
        await mega_s4_client.close()
        await stream_manager.cleanup()
        await duration_manager.close()

        # Clean up broadcast WebSocket manager
        try:
            from broadcast_router import broadcast_ws_manager
            await broadcast_ws_manager.close()
        except Exception:
            pass

        await storage.cleanup()
        await _cleanup_temp_directories(temp_dirs)
        
        raise
app = FastAPI(
    lifespan=lifespan,
    middleware=[
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    ]
)
request_start_time = ContextVar('request_start_time', default=None)
last_periodic_log = ContextVar('last_periodic_log', default=None)
session_manager.schedule_cleanup(app)
                                     
class TrackRename(BaseModel):
    title: Optional[str] = None
    visibility_status: Optional[str] = None


ROLE_PERMISSIONS = {
    UserRole.CREATOR: Permission.ALL,  # Creators have all permissions including download
    UserRole.TEAM: Permission.TEAM_ACCESS,  # Team members have team access including download
    UserRole.PATREON: Permission.VIEW | Permission.DOWNLOAD  # Patreon members can view and download
}


TIER_PERMISSIONS = {
    PatreonTier.BRONZE: Permission.VIEW,
    PatreonTier.SILVER: Permission.VIEW,
    PatreonTier.GOLD: Permission.VIEW
}

def is_ajax_request(request: Request) -> bool:
    """Check if the request is an AJAX request"""
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest" or
        request.headers.get("X-Ajax-Navigation") == "true"
    )

def get_template_name(base_name: str, is_ajax: bool) -> str:
    """Get the appropriate template name based on request type"""
    if is_ajax:
        return f"{base_name}_content.html"
    return f"{base_name}.html"

# Enhanced Timing Middleware with AJAX Support
@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    """Enhanced timing middleware with interval tracking and AJAX support"""
    current_time = time.time()
    
    # Track AJAX requests
    is_ajax = is_ajax_request(request)
    if is_ajax:
        logger.info(f"🌐 AJAX Request: {request.method} {request.url.path}")
    
    # Only track session check endpoints
    if request.url.path == "/api/session/check":
        # Store last check time in app state if not exists
        if not hasattr(app.state, "last_session_check"):
            app.state.last_session_check = current_time
        else:
            # Calculate time since last check
            time_since_last = current_time - app.state.last_session_check
            logger.info(f"[SessionTiming] Time since last check: {time_since_last:.2f}s")
            
            # Log warning if interval is too long
            if time_since_last > 35:  # Expected ~30s
                logger.warning(
                    f"[SessionTiming] Long interval detected: {time_since_last:.2f}s - "
                    f"Possible blocking or performance issue"
                )
            
            app.state.last_session_check = current_time
    
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time
    
    # Enhanced timing log for session checks
    if request.url.path == "/api/session/check":
        logger.info(
            f"[SessionTiming] Check completed - "
            f"Duration: {duration:.3f}s, "
            f"Memory: {psutil.Process().memory_percent():.1f}%, "
            f"CPU: {psutil.cpu_percent(interval=None):.1f}%"
        )
    
    # Add AJAX-specific headers
    if is_ajax:
        response.headers["X-Navigation-Type"] = "ajax"
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["X-Current-URL"] = str(request.url)
        response.headers["X-Ajax-Success"] = "true"
        response.headers["X-Response-Time"] = f"{duration:.3f}s"

        # Log AJAX response timing
        logger.info(f"🚀 AJAX Response: {request.url.path} - {duration:.3f}s")

    # ✅ Prevent HTML page caching (so cache-busted asset URLs are always fresh)
    # Only cache static assets (JS, CSS, images), NOT HTML pages
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

    return response

# Enhanced Error Handling Middleware with AJAX Support
@app.middleware("http")
async def error_handling_middleware(request: Request, call_next):
    """Global error handling and logging with AJAX support"""
    try:
        return await call_next(request)
    except Exception as e:
        logger.error(f"Unhandled error: {str(e)}", exc_info=True)
        
        # Session clearing removed - we use PostgreSQL sessions
        # Sessions are managed via session_manager, not request.session
        
        # Return appropriate response based on request type
        if is_ajax_request(request):
            return JSONResponse(
                status_code=500,
                content={
                    "error": "An internal server error occurred",
                    "ajax": True,
                    "reload_required": isinstance(e, (RuntimeError, SQLAlchemyError))
                }
            )
        else:
            return JSONResponse(
                status_code=500,
                content={"detail": "An internal server error occurred"}
            )

# Enhanced HTTP Exception Handler with AJAX Support      
@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    """Handle HTTP exceptions with proper redirection and AJAX support"""
    
    # Handle redirects
    if exc.status_code == 303:
        return RedirectResponse(
            url=exc.headers.get("Location", "/"),
            status_code=303
        )
    
    # Handle authentication errors
    if exc.status_code == 401:
        if is_ajax_request(request):
            # For AJAX requests, return JSON with redirect instruction
            return JSONResponse(
                status_code=401,
                content={
                    "error": "Authentication required",
                    "ajax": True,
                    "redirect": "/login"
                }
            )
        elif not request.url.path.startswith('/api/'):
            return RedirectResponse(url="/login", status_code=303)
    
    # Handle 404 errors
    if exc.status_code == 404:
        if is_ajax_request(request):
            return JSONResponse(
                status_code=404,
                content={
                    "error": "Page not found",
                    "ajax": True,
                    "message": exc.detail
                }
            )
    
    # Handle 403 errors (access denied)
    if exc.status_code == 403:
        if is_ajax_request(request):
            return JSONResponse(
                status_code=403,
                content={
                    "error": "Access denied",
                    "ajax": True,
                    "message": exc.detail
                }
            )
    
    # Default JSON response
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "ajax": is_ajax_request(request)
        }
    )

# Clear Invalid Session Cookie Middleware
@app.middleware("http")
async def clear_invalid_session_middleware(request: Request, call_next):
    """Automatically clear invalid session cookies to prevent persistent authentication loops"""
    from fastapi.responses import RedirectResponse, JSONResponse

    try:
        response = await call_next(request)
        return response
    except HTTPException as exc:
        # Check if this is a session-related authentication failure
        session_error = (
            exc.status_code in [401, 303] and
            exc.detail and
            isinstance(exc.detail, str) and
            ("session" in exc.detail.lower() or "authentication" in exc.detail.lower())
        )

        if session_error:
            session_id = request.cookies.get("session_id")
            if session_id:
                logger.info(f"Clearing invalid session cookie: {session_id[:8]}...")

                # Create appropriate response based on status code
                if exc.status_code == 303 and exc.headers and "Location" in exc.headers:
                    # Redirect response (for page requests)
                    response = RedirectResponse(
                        url=exc.headers["Location"],
                        status_code=303
                    )
                elif request.url.path.startswith('/api/'):
                    # JSON response for API endpoints
                    response = JSONResponse(
                        status_code=401,
                        content={"detail": exc.detail}
                    )
                else:
                    # HTML page requests - redirect to login
                    response = RedirectResponse(url="/login", status_code=303)

                # Delete the invalid session cookie
                response.delete_cookie(
                    key="session_id",
                    path="/",
                    secure=True,
                    httponly=True,
                    samesite="lax"
                )

                return response

        # Re-raise if not a session error
        raise

# New: AJAX Navigation Performance Middleware
@app.middleware("http")
async def ajax_performance_middleware(request: Request, call_next):
    """Track AJAX navigation performance"""
    if is_ajax_request(request):
        # Add request start time for performance tracking
        request.state.ajax_start_time = time.time()
        
        # Log AJAX navigation pattern
        referer = request.headers.get("referer", "")
        logger.info(
            f"[AJAX Performance] Navigation: {referer} → {request.url.path}"
        )
    
    response = await call_next(request)
    
    if is_ajax_request(request) and hasattr(request.state, 'ajax_start_time'):
        total_time = time.time() - request.state.ajax_start_time
        
        # Log performance metrics
        logger.info(
            f"[AJAX Performance] {request.url.path}: "
            f"Total={total_time:.3f}s, "
            f"Status={response.status_code}"
        )
        
        # Add performance headers
        response.headers["X-Ajax-Total-Time"] = f"{total_time:.3f}s"
        response.headers["X-Ajax-Optimized"] = "true"
        
        # Log slow AJAX requests
        if total_time > 1.0:
            logger.warning(
                f"[AJAX Performance] Slow request detected: "
                f"{request.url.path} took {total_time:.3f}s"
            )
    
    return response


@app.middleware("http")
async def admin_badge_counts_middleware(request: Request, call_next):
    """Add badge counts to request.state for all admin/team users on HTML pages"""
    # Only add counts for HTML responses (not API endpoints or static files)
    if not request.url.path.startswith("/api/") and not request.url.path.startswith("/static/"):
        # Check if user is authenticated and is admin/team
        try:
            # Get user from session cookie (PostgreSQL session)
            session_id = request.cookies.get("session_id")
            if session_id:
                db = next(get_db())
                try:
                    # Get session from PostgreSQL
                    from models import UserSession
                    session = db.query(UserSession).filter(
                        and_(
                            UserSession.session_id == session_id,
                            UserSession.is_active == True,
                            UserSession.expires_at > datetime.now(timezone.utc)
                        )
                    ).first()

                    if session:
                        user = db.query(User).filter(User.id == session.user_id).first()
                        if user and (user.is_creator or user.is_team):
                            # Add pending book requests count
                            from book_request import get_pending_book_request_count
                            pending_count = await get_pending_book_request_count(user, db)
                            request.state.pending_book_requests = pending_count

                            # Add unread activity logs count
                            from activity_logs_router import get_unread_activity_logs_count
                            activity_logs_count = await get_unread_activity_logs_count(user.id, db)
                            request.state.unread_activity_logs = activity_logs_count

                            logger.debug(f"Added badge counts to request.state: pending={pending_count}, activity_logs={activity_logs_count}")
                finally:
                    db.close()
        except Exception as e:
            logger.warning(f"Failed to add badge counts: {e}")

    # Process the request
    response = await call_next(request)
    return response


@app.get("/privacy")
async def privacy_policy():
    return {"message": "Privacy Policy"}

@app.get("/terms")
async def terms_of_service():
    return {"message": "Terms of Service"}


async def periodic_document_cleanup():
    """Simple periodic cleanup for orphaned document extraction files"""
    while True:
        try:
            await asyncio.sleep(600)  # Every 10 minutes (reduced from 1800)
            
            document_temp_folder = "/tmp/media_storage/document_extraction"
            if os.path.exists(document_temp_folder):
                import shutil
                from datetime import datetime, timedelta
                
                cutoff = datetime.now() - timedelta(minutes=15)  # 15 minutes (reduced from hours=1)
                cleaned = 0
                
                for item_name in os.listdir(document_temp_folder):
                    item_path = os.path.join(document_temp_folder, item_name)
                    
                    if os.path.isdir(item_path) and item_name.startswith("session_"):
                        try:
                            mod_time = datetime.fromtimestamp(os.path.getmtime(item_path))
                            if mod_time < cutoff:
                                shutil.rmtree(item_path)
                                cleaned += 1
                        except Exception:
                            pass
                
                if cleaned > 0:
                    logger.info(f"Cleaned up {cleaned} orphaned document extraction sessions")
                    
        except Exception as e:
            logger.error(f"Error in periodic document cleanup: {str(e)}")
            await asyncio.sleep(300)


def get_user_permissions(user: User) -> Dict:
    """
    Get detailed permissions based on user role - FIXED: Proper Guest Trial handling
    """
    from models import UserTier, CampaignTier  # Import here to avoid circular imports
    from datetime import datetime, timezone  # Import datetime
    
    logger.info(f"Getting permissions for user {user.email} (creator: {user.is_creator}, team: {user.is_team}, patreon: {user.is_patreon}, kofi: {user.is_kofi}, guest_trial: {user.is_guest_trial})")
    
    # Base permissions structure
    permissions = {
        "can_view": False,
        "can_create": False,
        "can_rename": False,
        "can_delete": False,
        "can_delete_albums": False,
        "can_delete_tracks": False,
        "can_download": False,
        "downloads_blocked": False,
        "is_creator": user.is_creator,
        "is_team": user.is_team,
        "is_patreon": user.is_patreon,
        "is_kofi": user.is_kofi,
        "is_guest_trial": user.is_guest_trial
    }
    
    # Creator permissions
    if user.is_creator:
        permissions.update({
            "can_view": True,
            "can_create": True,
            "can_rename": True,
            "can_delete": True,
            "can_delete_albums": True,
            "can_delete_tracks": True,
            "can_manage_team": True,
            "can_download": True,
            "role_type": "creator"
        })
        logger.info(f"Set creator permissions for {user.email}")
        return permissions
    
    # ✅ FIXED: Guest Trial permissions - Read from tier association
    if user.is_guest_trial and user.role == UserRole.GUEST:
        logger.info(f"Processing guest trial user: {user.email}")
        
        # Check if trial is still active
        if not user.trial_active:
            logger.info(f"Guest trial expired for {user.email}")
            permissions.update({
                "can_view": True,
                "can_create": False,
                "can_rename": False,
                "can_delete": False,
                "can_delete_albums": False,
                "can_delete_tracks": False,
                "can_download": False,
                "downloads_blocked": True,
                "role_type": "expired_guest_trial",
                "trial_expired": True,
                "album_downloads_allowed": 0,
                "track_downloads_allowed": 0,
                "book_requests_allowed": 0,
                "max_sessions": 1
            })
            return permissions
        
        # ✅ Active trial - get benefits from tier association (NOT stored data)
        from sqlalchemy.orm import sessionmaker
        from database import engine
        
        # Get database session (this is a hack but needed for the query)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db = SessionLocal()
        
        try:
            # Debug: Log what we're looking for
            logger.info(f"Looking for UserTier associations for guest trial user {user.id}")
            
            # Find any active UserTier association for this user
            user_tier = db.query(UserTier).filter(
                and_(
                    UserTier.user_id == user.id,
                    UserTier.is_active == True
                )
            ).first()
            
            tier = None
            if user_tier:
                logger.info(f"Found UserTier association: user_id={user_tier.user_id}, tier_id={user_tier.tier_id}")
                
                # Get the actual tier using tier_id
                tier = db.query(CampaignTier).filter(
                    CampaignTier.id == user_tier.tier_id
                ).first()
                
                if tier:
                    logger.info(f"Found associated tier: {tier.title} (ID: {tier.id}, platform: {tier.platform_type})")
                    
                    # Check if this is the Guest Trial tier
                    if tier.title == "Guest Trial" and tier.platform_type == "KOFI":
                        logger.info(f"Confirmed Guest Trial tier for user {user.email}")
                    else:
                        logger.warning(f"User {user.email} has different tier: {tier.title} (platform: {tier.platform_type})")
                else:
                    logger.error(f"Could not find CampaignTier with ID {user_tier.tier_id}")
                    tier = None
            else:
                logger.warning(f"No active UserTier association found for guest trial user {user.email} (ID: {user.id})")
                
                # Try to find Guest Trial tier and create association
                guest_tier = db.query(CampaignTier).filter(
                    and_(
                        CampaignTier.creator_id == user.created_by,
                        CampaignTier.title == "Guest Trial",
                        CampaignTier.platform_type == "KOFI",
                        CampaignTier.is_active == True
                    )
                ).first()
                
                if guest_tier:
                    logger.info(f"Found Guest Trial tier {guest_tier.id}, creating association for user {user.email}")
                    
                    # Create missing UserTier association
                    user_tier_association = UserTier(
                        user_id=user.id,
                        tier_id=guest_tier.id,
                        joined_at=user.trial_started_at or datetime.now(timezone.utc),
                        expires_at=user.trial_expires_at,
                        is_active=True,
                        payment_status='guest_trial'
                    )
                    db.add(user_tier_association)
                    db.commit()
                    
                    tier = guest_tier
                    logger.info(f"✅ Created missing UserTier association for guest trial user {user.email}")
                else:
                    logger.error(f"No Guest Trial tier found for creator {user.created_by}")
                    tier = None
            
            if tier:
                # Get usage from user data (reset monthly)
                tier_data = user.patreon_tier_data or {}
                album_used = tier_data.get('album_downloads_used', 0)
                track_used = tier_data.get('track_downloads_used', 0)
                book_used = tier_data.get('book_requests_used', 0)
                
                permissions.update({
                    "can_view": True,
                    "can_create": False,
                    "can_rename": False,
                    "can_delete": False,
                    "can_delete_albums": False,
                    "can_delete_tracks": False,
                    "can_download": (tier.album_downloads_allowed > 0 or tier.track_downloads_allowed > 0),
                    "downloads_blocked": False,
                    "role_type": "guest_trial",
                    "trial_active": True,
                    "trial_expires_at": user.trial_expires_at.isoformat() if user.trial_expires_at else None,
                    "trial_hours_remaining": user.trial_hours_remaining,
                    
                    # ✅ Benefits from tier (NOT stored in user data)
                    "album_downloads_allowed": tier.album_downloads_allowed,
                    "track_downloads_allowed": tier.track_downloads_allowed,
                    "book_requests_allowed": tier.book_requests_allowed,
                    "chapters_allowed_per_book_request": getattr(tier, 'chapters_allowed_per_book_request', 0),
                    "max_sessions": tier.max_sessions,
                    
                    # Usage tracking (from user data)
                    "album_downloads_used": album_used,
                    "track_downloads_used": track_used,
                    "book_requests_used": book_used,
                    "album_downloads_remaining": max(0, tier.album_downloads_allowed - album_used),
                    "track_downloads_remaining": max(0, tier.track_downloads_allowed - track_used),
                    "book_requests_remaining": max(0, tier.book_requests_allowed - book_used)
                })
                
                logger.info(f"✅ Guest trial permissions set from tier: {tier.title} (ID: {tier.id}) - "
                           f"Albums: {tier.album_downloads_allowed} (used: {album_used}), "
                           f"Tracks: {tier.track_downloads_allowed} (used: {track_used}), "
                           f"Books: {tier.book_requests_allowed} (used: {book_used})")
                
                return permissions
            else:
                logger.warning(f"No Guest Trial tier found for user {user.email}")
                # Fallback to minimal permissions
                permissions.update({
                    "can_view": True,
                    "role_type": "guest_trial_no_tier",
                    "album_downloads_allowed": 0,
                    "track_downloads_allowed": 0,
                    "book_requests_allowed": 0,
                    "max_sessions": 1
                })
                return permissions
                
        finally:
            db.close()
        
    # Team member permissions
    if user.is_team:
        tier_data = user.patreon_tier_data or {}
        album_downloads = tier_data.get('album_downloads_allowed', 0)
        track_downloads = tier_data.get('track_downloads_allowed', 0)
        
        # INDIVIDUAL DELETION PERMISSIONS FOR ALBUMS
        album_deletions_allowed = tier_data.get('album_deletions_allowed', 0)
        album_deletions_used = tier_data.get('album_deletions_used', 0)
        
        # INDIVIDUAL DELETION PERMISSIONS FOR TRACKS
        track_deletions_allowed = tier_data.get('track_deletions_allowed', 0)
        track_deletions_used = tier_data.get('track_deletions_used', 0)
        
        # Check if 24-hour period has reset
        deletion_start = tier_data.get('deletion_period_start')
        if deletion_start and (album_deletions_allowed > 0 or track_deletions_allowed > 0):
            try:
                start_time = datetime.fromisoformat(deletion_start.replace('Z', '+00:00'))
                if datetime.now(timezone.utc) >= start_time + timedelta(hours=24):
                    pass
            except (ValueError, TypeError):
                pass
        
        can_delete_albums = album_deletions_allowed > 0 and album_deletions_used < album_deletions_allowed
        can_delete_tracks = track_deletions_allowed > 0 and track_deletions_used < track_deletions_allowed
        
        permissions.update({
            "can_view": True,
            "can_create": True,
            "can_rename": True,
            "can_delete": can_delete_albums or can_delete_tracks,
            "can_delete_albums": can_delete_albums,
            "can_delete_tracks": can_delete_tracks,
            "can_download": album_downloads >= 0 or track_downloads >= 0,
            "creator_id": user.created_by,
            "role_type": "team",
            "album_downloads": album_downloads,
            "track_downloads": track_downloads,
            "album_deletions_allowed": album_deletions_allowed,
            "album_deletions_used": album_deletions_used,
            "album_deletions_remaining": max(0, album_deletions_allowed - album_deletions_used),
            "track_deletions_allowed": track_deletions_allowed,
            "track_deletions_used": track_deletions_used,
            "track_deletions_remaining": max(0, track_deletions_allowed - track_deletions_used)
        })
        return permissions
        
    # Patreon member permissions
    if user.is_patreon:
        if user.patreon_tier_data:
            logger.info(f"Patreon tier data for {user.email}: {user.patreon_tier_data}")
            
        album_downloads = user.patreon_tier_data.get('album_downloads_allowed', 0) if user.patreon_tier_data else 0
        track_downloads = user.patreon_tier_data.get('track_downloads_allowed', 0) if user.patreon_tier_data else 0
        
        logger.info(
            f"Patron {user.email} download limits: "
            f"Albums={album_downloads}, Tracks={track_downloads}"
        )
        
        permissions.update({
            "can_view": True,
            "can_create": False,
            "can_rename": False,
            "can_delete": False,
            "can_delete_albums": False,
            "can_delete_tracks": False,
            "can_download": album_downloads > 0 or track_downloads > 0,
            "tier_info": user.get_tier_info(),
            "role_type": "patreon",
            "album_downloads": album_downloads,
            "track_downloads": track_downloads
        })
        logger.info(f"Set patreon permissions for {user.email}: {permissions}")
        return permissions
    
    # Ko‑fi member permissions
    if user.is_kofi:
        if user.patreon_tier_data:
            logger.info(f"Ko-fi tier data for {user.email}: {user.patreon_tier_data}")
            
        album_downloads = user.patreon_tier_data.get('album_downloads_allowed', 0) if user.patreon_tier_data else 0
        track_downloads = user.patreon_tier_data.get('track_downloads_allowed', 0) if user.patreon_tier_data else 0
        book_requests = user.patreon_tier_data.get('book_requests_allowed', 0) if user.patreon_tier_data else 0

        logger.info(
            f"Ko-fi user {user.email} download limits: "
            f"Albums={album_downloads}, Tracks={track_downloads}"
        )
        
        permissions.update({
            "can_view": True,
            "can_create": False,
            "can_rename": False,
            "can_delete": False,
            "can_delete_albums": False,
            "can_delete_tracks": False,
            "can_download": album_downloads > 0 or track_downloads > 0,
            "tier_info": user.get_tier_info(),
            "role_type": "kofi",
            "album_downloads": album_downloads,
            "track_downloads": track_downloads,
            "book_requests": book_requests
        })
        logger.info(f"Set Ko-fi permissions for {user.email}: {permissions}")
        return permissions
    
    # If no special role is detected, return base permissions with role_type 'unknown'
    permissions["role_type"] = "unknown"
    logger.info(f"No special permissions for {user.email}, using base permissions: {permissions}")
    return permissions

def verify_role_permission(allowed_roles: List[str]):
    def decorator(func):
        @wraps(func)
        async def wrapper(
            *args,
            current_user: User = Depends(login_required),
            **kwargs
        ):
            permissions = get_user_permissions(current_user)

            if permissions["role_type"] not in allowed_roles:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "message": "Access denied",
                        "required_roles": allowed_roles,
                        "current_role": permissions["role_type"]
                    }
                )

            return await func(*args, current_user=current_user, **kwargs)
        return wrapper
    return decorator





class PatreonService:
    def __init__(self, db: Session):
        self.db = db
        self._cache = {}
        self._cache_expiry = None
        self._cache_duration = timedelta(minutes=15)

# SessionMiddleware removed - we use PostgreSQL-backed sessions via SessionManager
# This ensures sessions work across all containers (shared database)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

# URL paths for client-side access
STATIC_URL = "/static"              # Points to local static files (CSS, JS)
MEDIA_URL = "/media"                # This is just a URL prefix, not a directory
DEFAULT_COVER_URL = f"{MEDIA_URL}/images/default-album.jpg"  # URL pattern for browser
COVER_URL_PREFIX = f"{MEDIA_URL}/images"     # URL pattern for images
AUDIO_URL_PREFIX = f"{MEDIA_URL}/audio"      # URL pattern for audio

# Create directories
STATIC_DIR.mkdir(exist_ok=True)
(STATIC_DIR / "css").mkdir(exist_ok=True)
(STATIC_DIR / "js").mkdir(exist_ok=True)
TEMPLATES_DIR.mkdir(exist_ok=True)

# Mount directories
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# CACHE BUSTING - ADD THIS BEFORE TEMPLATES SETUP
import time

# Setup templates
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals['url_for'] = cache_busted_url_for
templates.env.filters['url_for'] = cache_busted_url_for
templates.env.globals['APP_VERSION'] = APP_VERSION  # Make APP_VERSION available in templates

def get_user_permissions_func(user: User) -> Permission:
    """Get permissions for a given user based on role and tier"""
    # First check role-based permissions
    base_permissions = ROLE_PERMISSIONS.get(user.role, Permission.NONE)

    # For Patreon members, add tier-based permissions
    if user.is_patreon and hasattr(user, 'patreon_tier_id'):
        tier = PatreonTier(user.patreon_tier_id)
        base_permissions |= TIER_PERMISSIONS.get(tier, Permission.NONE)

    return base_permissions





def verify_patreon_access(user: User, required_tier: PatreonTier) -> bool:
    """Verify if a Patreon user has access to content requiring a specific tier"""
    if not user.is_patreon or not user.patreon_tier_id:
        return False

    try:
        user_tier = PatreonTier(user.patreon_tier_id)
        return list(PatreonTier).index(user_tier) >= list(PatreonTier).index(required_tier)
    except ValueError:
        return False


def check_permission(user: User, required_permission: Permission):
    """Check if user has the required permission"""
    if not user:
        raise HTTPException(
            status_code=403,
            detail="Authentication required"
        )

    # Get permissions
    permissions = get_user_permissions_func(user)

    if not (permissions & required_permission):
        raise HTTPException(
            status_code=403,
            detail="You don't have permission to perform this action"
        )

class EventLoopMonitor:
    def __init__(self):
        self.start_time = time.time()
        self.last_check = time.time()
        self._monitor_task = None
        self.blocked_time = 0
        self.check_interval = 0.05  # 50ms
        
    async def start(self):
        """Start monitoring the event loop"""
        if self._monitor_task is None:
            self._monitor_task = asyncio.create_task(self._monitor())
            logger.info("Event loop monitoring started")
            
    async def stop(self):
        """Stop monitoring the event loop"""
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None
            logger.info("Event loop monitoring stopped")
            
    async def _monitor(self):
        """Monitor event loop for blocking operations"""
        while True:
            try:
                loop_time = time.time()
                interval = loop_time - self.last_check
                
                if interval > 0.1:  # 100ms threshold
                    self.blocked_time += interval
                    logger.warning(
                        f"Event loop blocked for {interval:.3f}s "
                        f"(Total blocked: {self.blocked_time:.3f}s) "
                        f"CPU: {psutil.cpu_percent()}% "
                        f"Memory: {psutil.Process().memory_percent()}%"
                    )
                    
                self.last_check = loop_time
                await asyncio.sleep(self.check_interval)
                
            except Exception as e:
                logger.error(f"Error in event loop monitor: {e}")
                await asyncio.sleep(1)  # Back off on error

# Create monitor instance
event_loop_monitor = EventLoopMonitor()

# Add performance tracking decorator
def track_operation(name: str = None):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            operation = name or func.__name__
            start = perf_counter()
            try:
                result = await func(*args, **kwargs)
                duration = perf_counter() - start
                
                # Log if operation takes longer than 100ms
                if duration > 0.1:
                    logger.warning(
                        f"[Timing] Long operation detected - {operation}: {duration:.3f}s "
                        f"CPU: {psutil.cpu_percent()}% "
                        f"Memory: {psutil.Process().memory_percent()}%"
                    )
                else:
                    logger.info(f"[Timing] {operation}: {duration:.3f}s")
                    
                return result
            except Exception as e:
                duration = perf_counter() - start
                logger.error(
                    f"[Timing] Operation {operation} failed after {duration:.3f}s: {str(e)}"
                )
                raise
        return wrapper
    return decorator

# Authentication helper functions
async def get_current_user(
    request: Request,
    db: Session = Depends(get_db)
) -> Optional[User]:
    return await session_manager.verify_session(request, db)

def verify_team_permissions(user: User) -> bool:
    """Verify that a user has proper team permissions"""
    return (
        user.role == UserRole.TEAM and
        user.is_active and
        user.created_by is not None
    )

async def check_access(user: User, album: Album) -> bool:
    """Check if user has access to album based on their supporter status - platform agnostic"""
    try:
        # Log the check for debugging
        logger.info(f"Checking access for user {user.email}")
        logger.info(f"User tier data: {user.patreon_tier_data}")
        
        # Creators and team always have access
        if user.is_creator or user.is_team:
            logger.info("User is creator/team - granted access")
            return True
            
        # If album is not restricted, all users have access
        restrictions = album.tier_restrictions
        if not restrictions or not restrictions.get("is_restricted"):
            logger.info("Album not restricted - granted access")
            return True
            
        # Check if user has valid tier data, regardless of platform
        has_tier_data = user.patreon_tier_data is not None and len(user.patreon_tier_data) > 0
        is_supporter = any([
            user.is_patreon,
            user.is_kofi,
            # Add any future platform checks here
            getattr(user, 'is_stripe', False),  # Example for future platform
            getattr(user, 'is_memberful', False),  # Example for future platform
        ])
        
        if not (is_supporter and has_tier_data):
            logger.info("User not an active supporter - denied access")
            return False
            
        # Get user's actual tier amount
        user_amount = user.patreon_tier_data.get("amount_cents", 0)
        required_amount = restrictions.get("minimum_tier_amount", 0)
        
        logger.info(f"User amount: {user_amount}, Required: {required_amount}")
        
        # Alternatively, check for specific platform handling
        if hasattr(user, 'override_tier_access') and user.override_tier_access:
            logger.info(f"User has override_tier_access - granted access")
            return True
        
        # Do the comparison for tier-based access
        has_access = user_amount >= required_amount
        logger.info(f"Access {'granted' if has_access else 'denied'} based on amount")
        return has_access
        
    except Exception as e:
        logger.error(f"Error checking access: {str(e)}", exc_info=True)
        # Default to deny on error
        return False

async def get_user_downloads(user: User, db: Session) -> Dict[str, Any]:
    """
    Get user's download count and limit prioritizing individual user settings
    """
    try:
        # Initialize response structure
        response = {
            "albums": {
                "downloads_allowed": 0,
                "downloads_used": 0,
                "downloads_remaining": 0
            },
            "tracks": {
                "downloads_allowed": 0,
                "downloads_used": 0,
                "downloads_remaining": 0
            },
            "books": {
                "requests_allowed": 0,
                "requests_used": 0,
                "requests_remaining": 0
            }
        }
        
        # Creators have unlimited downloads
        if user.is_creator:
            response["albums"]["downloads_allowed"] = float('inf')
            response["albums"]["downloads_remaining"] = float('inf')
            response["tracks"]["downloads_allowed"] = float('inf')
            response["tracks"]["downloads_remaining"] = float('inf')
            response["books"]["requests_allowed"] = float('inf')
            response["books"]["requests_remaining"] = float('inf')
            return response
            
        # For patrons, team members and Ko-fi users
        if user.patreon_tier_data:
            tier_data = user.patreon_tier_data
            
            # ALWAYS prioritize the user's individual settings stored in patreon_tier_data
            album_downloads_allowed = tier_data.get('album_downloads_allowed', 0)
            track_downloads_allowed = tier_data.get('track_downloads_allowed', 0)
            book_requests_allowed = tier_data.get('book_requests_allowed', 0)
            
            # Get usage counts from stored tier data
            album_downloads_used = tier_data.get('album_downloads_used', 0)
            track_downloads_used = tier_data.get('track_downloads_used', 0)
            book_requests_used = tier_data.get('book_requests_used', 0)
            
            # Log what we're using
            if user.is_kofi:
                logger.info(f"Ko-fi user {user.email} download limits: Albums={album_downloads_allowed}, Tracks={track_downloads_allowed}")
            elif user.is_patreon:
                logger.info(f"Patreon user {user.email} download limits: Albums={album_downloads_allowed}, Tracks={track_downloads_allowed}")
            elif user.is_team:
                logger.info(f"Team member {user.email} download limits: Albums={album_downloads_allowed}, Tracks={track_downloads_allowed}")
            
            # Calculate remaining downloads
            album_downloads_remaining = max(0, album_downloads_allowed - album_downloads_used)
            track_downloads_remaining = max(0, track_downloads_allowed - track_downloads_used)
            book_requests_remaining = max(0, book_requests_allowed - book_requests_used)
            
            # Set response values
            response["albums"]["downloads_allowed"] = album_downloads_allowed
            response["albums"]["downloads_used"] = album_downloads_used
            response["albums"]["downloads_remaining"] = album_downloads_remaining
            
            response["tracks"]["downloads_allowed"] = track_downloads_allowed
            response["tracks"]["downloads_used"] = track_downloads_used
            response["tracks"]["downloads_remaining"] = track_downloads_remaining
            
            response["books"]["requests_allowed"] = book_requests_allowed
            response["books"]["requests_used"] = book_requests_used
            response["books"]["requests_remaining"] = book_requests_remaining
        
        return response
    except Exception as e:
        logger.error(f"Error getting user downloads: {str(e)}")
        # Return empty response on error
        return {
            "albums": {"downloads_allowed": 0, "downloads_used": 0, "downloads_remaining": 0},
            "tracks": {"downloads_allowed": 0, "downloads_used": 0, "downloads_remaining": 0},
            "books": {"requests_allowed": 0, "requests_used": 0, "requests_remaining": 0}
        }
async def initialize_patron_period(user: User, db: Session):
    """Initialize patron's billing period from Patreon"""
    try:
        patron_data = await patreon_client.verify_patron(email)
        current_data = user.patreon_tier_data or {}
        
        current_data.update({
            'album_downloads_used': 0,
            'track_downloads_used': 0,
            'current_period_start': patron_data.get('current_period_start'),
            'current_period_end': patron_data.get('current_period_end')
        })
        
        user.patreon_tier_data = current_data
        db.commit()
        db.refresh(user)
        
    except Exception as e:
        logger.error(f"Error initializing patron period: {str(e)}")
        # Fallback to 30-day period
        now = datetime.now(timezone.utc)
        current_data = user.patreon_tier_data or {}
        current_data.update({
            'album_downloads_used': 0,
            'track_downloads_used': 0,
            'current_period_start': now.isoformat(),
            'current_period_end': (now + timedelta(days=30)).isoformat()
        })
        user.patreon_tier_data = current_data
        db.commit()
        db.refresh(user)



async def get_or_create_team_tier(creator_id: int, db: Session) -> CampaignTier:
    """Get existing team tier or create new one with book request defaults"""
    try:
        # Check if team tier exists
        team_tier = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == creator_id,
                CampaignTier.title == "Team Members",
                CampaignTier.is_active == True
            )
        ).first()
        
        if not team_tier:
            # Get default values from highest tier as reference
            default_tier = db.query(CampaignTier).filter(
                CampaignTier.creator_id == creator_id,
                CampaignTier.is_active == True
            ).order_by(CampaignTier.amount_cents.desc()).first()
            
            # Use defaults or fallback values
            album_downloads = 4
            track_downloads = 2
            book_requests = 2  # ✅ Default to 2 book requests for team members
            max_sessions = 1
            
            if default_tier:
                album_downloads = getattr(default_tier, 'album_downloads_allowed', 4)
                track_downloads = getattr(default_tier, 'track_downloads_allowed', 2)
                book_requests = getattr(default_tier, 'book_requests_allowed', 2)  # ✅ Get from existing tier
                max_sessions = getattr(default_tier, 'max_sessions', 1)
            
            # Create new team tier with book request support
            team_tier = CampaignTier(
                creator_id=creator_id,
                title="Team Members",
                description="Team Member Access",
                amount_cents=0,
                patron_count=0,
                is_active=True,
                album_downloads_allowed=album_downloads,
                track_downloads_allowed=track_downloads,
                book_requests_allowed=book_requests,  # ✅ ADD THIS
                max_sessions=max_sessions,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            db.add(team_tier)
            db.flush()
            logger.info(f"Created new team tier for creator {creator_id} with book requests: {book_requests}")
            
        return team_tier
        
    except Exception as e:
        logger.error(f"Error getting/creating team tier: {str(e)}")
        raise
async def update_team_tier_count(creator_id: int, db: Session):
    """Update team member count and manage tier existence"""
    try:
        # Start a new transaction
        logger.info(f"Starting team tier update for creator {creator_id}")
        
        # Get team member count
        team_count = db.query(User).filter(
            and_(
                User.created_by == creator_id,
                User.role == UserRole.TEAM,
                User.is_active == True
            )
        ).count()
        
        logger.info(f"Found {team_count} active team members")

        # Get team tier in same transaction
        team_tier = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == creator_id,
                CampaignTier.title == "Team Members"
            )
        ).first()
        
        logger.info(f"Found team tier: {team_tier is not None}")
        
        if team_count == 0:
            if team_tier:
                logger.info("No active members and tier exists - deleting tier")
                # Force a refresh of the tier to ensure latest state
                db.refresh(team_tier)
                # Explicitly delete and flush
                db.delete(team_tier)
                db.flush()
                logger.info("Tier deleted, performing commit")
                db.commit()
                logger.info("Commit successful - tier deleted")
            else:
                logger.info("No active members and no tier exists - nothing to do")
            return
            
        logger.info(f"Transaction completed for creator {creator_id}")
        
    except Exception as e:
        logger.error(f"Error in update_team_tier_count: {str(e)}")
        logger.error(f"Stack trace: {traceback.format_exc()}")
        db.rollback()
        raise

    finally:
        logger.info(f"Final team count status - creator: {creator_id}, count: {team_count}")

async def initialize_team_downloads(user: User, db: Session):
    """Initialize/reset download and deletion settings for team members"""
    try:
        logger.info(f"Initializing download settings for team member: {user.email}")
        
        # Get the team tier (will be created if it doesn't exist)
        team_tier = await get_or_create_team_tier(user.created_by, db)
            
        logger.info(
            f"Using team tier settings - "
            f"Albums: {team_tier.album_downloads_allowed}, "
            f"Tracks: {team_tier.track_downloads_allowed}, "
            f"Book Requests: {team_tier.book_requests_allowed}"
        )
        
        now = datetime.now(timezone.utc)
        current_data = user.patreon_tier_data or {}
        
        # First time initialization if period_start is missing:
        if 'period_start' not in current_data:
            logger.info(f"First time initialization for team member {user.email}")
            current_data = {
                'title': 'Team Member',
                'album_downloads_allowed': team_tier.album_downloads_allowed,
                'track_downloads_allowed': team_tier.track_downloads_allowed,
                'book_requests_allowed': team_tier.book_requests_allowed,
                # 🔥 FIX: Only set deletion allowances if not already set individually
                'track_deletions_allowed': current_data.get('track_deletions_allowed', 0),
                'album_deletions_allowed': current_data.get('album_deletions_allowed', 0),  # NEW
                'album_downloads_used': 0,
                'track_downloads_used': 0,
                'book_requests_used': 0,
                'track_deletions_used': 0,
                'album_deletions_used': 0,  # NEW
                'max_sessions': team_tier.max_sessions,
                'period_start': now.isoformat(),
                'deletion_period_start': now.isoformat(),
                'platform': 'team',
                'patron_status': 'active_patron',
                'amount_cents': 0,
                'tier_description': "Special access for team members"
            }
        else:
            # 🔥 FIX: Preserve existing individual settings
            # Only update download settings from team tier, NOT deletion settings
            existing_track_deletions_allowed = current_data.get('track_deletions_allowed', 0)
            existing_album_deletions_allowed = current_data.get('album_deletions_allowed', 0)  # NEW
            
            current_data.update({
                'title': 'Team Member',
                'album_downloads_allowed': team_tier.album_downloads_allowed,
                'track_downloads_allowed': team_tier.track_downloads_allowed,
                'book_requests_allowed': team_tier.book_requests_allowed,
                # 🔥 FIX: PRESERVE the individual deletion settings
                'track_deletions_allowed': existing_track_deletions_allowed,
                'album_deletions_allowed': existing_album_deletions_allowed,  # NEW
                'max_sessions': team_tier.max_sessions,
                'platform': 'team',
                'tier_description': "Special access for team members"
            })
            
            # Check if we need to reset usage counts for a new period
            if 'period_start' in current_data:
                try:
                    period_start = datetime.fromisoformat(current_data['period_start'].replace('Z', '+00:00'))
                    next_reset = period_start + relativedelta(months=1)
                    
                    if now >= next_reset:
                        logger.info(f"Monthly reset for team member {user.email}")
                        current_data.update({
                            'album_downloads_used': 0,
                            'track_downloads_used': 0,
                            'book_requests_used': 0,
                            'period_start': now.isoformat()
                        })
                except (ValueError, TypeError) as e:
                    logger.error(f"Error parsing period_start date: {str(e)} - resetting period")
                    current_data.update({
                        'album_downloads_used': 0,
                        'track_downloads_used': 0,
                        'book_requests_used': 0,
                        'period_start': now.isoformat()
                    })
            else:
                # Add missing period start
                current_data['period_start'] = now.isoformat()
            
            # 🔥 FIX: Check for 24-hour deletion reset separately
            if 'deletion_period_start' in current_data:
                try:
                    deletion_start = datetime.fromisoformat(current_data['deletion_period_start'].replace('Z', '+00:00'))
                    next_deletion_reset = deletion_start + timedelta(hours=24)
                    
                    if now >= next_deletion_reset:
                        logger.info(f"24-hour deletion reset for team member {user.email}")
                        current_data.update({
                            'track_deletions_used': 0,  # Reset USAGE, not allowance
                            'album_deletions_used': 0,  # Reset USAGE, not allowance - NEW
                            'deletion_period_start': now.isoformat()
                        })
                except (ValueError, TypeError) as e:
                    logger.error(f"Error parsing deletion_period_start: {str(e)} - resetting")
                    current_data.update({
                        'track_deletions_used': 0,  # Reset USAGE, not allowance
                        'album_deletions_used': 0,  # Reset USAGE, not allowance - NEW
                        'deletion_period_start': now.isoformat()
                    })
            else:
                # Add missing deletion tracking
                current_data.update({
                    'track_deletions_used': 0,
                    'album_deletions_used': 0,  # NEW
                    'deletion_period_start': now.isoformat()
                })
        
        # Make sure the user has the team platform marked
        if 'platform' not in current_data:
            current_data['platform'] = 'team'
            
        # Add any missing fields
        if 'track_deletions_used' not in current_data:
            current_data['track_deletions_used'] = 0
        if 'album_deletions_used' not in current_data:  # NEW
            current_data['album_deletions_used'] = 0
        if 'deletion_period_start' not in current_data:
            current_data['deletion_period_start'] = now.isoformat()
        
        # Ensure campaign ID is set for the team member
        if not user.campaign_id:
            campaign = db.query(Campaign).filter(
                and_(
                    Campaign.creator_id == user.created_by,
                    Campaign.is_primary == True
                )
            ).first()
            
            if campaign:
                user.campaign_id = str(campaign.id)
                
        # Save the updated data to the user
        user.patreon_tier_data = current_data
        db.commit()
        db.refresh(user)
        
        logger.info(f"Team member {user.email} deletion settings preserved: "
                   f"tracks={current_data.get('track_deletions_allowed', 0)}, "
                   f"albums={current_data.get('album_deletions_allowed', 0)}")
        return user
    except Exception as e:
        logger.error(f"Error initializing team downloads: {str(e)}")
        db.rollback()
        raise


async def initialize_patron_downloads(user: User, patron_data: dict, creator: User, db: Session):
    """Initialize/update download settings based on patron's tier and handle period resets"""
    try:
        logger.info(f"===== INITIALIZING PATRON: {user.email} =====")
        
        # Get attributes directly from patron_data
        attributes = patron_data.get("attributes", {})
        logger.info(f"Patron attributes: {json.dumps(attributes, indent=2)}")
        
        # Extract data from attributes
        current_amount = attributes.get("currently_entitled_amount_cents")
        last_charge_date = attributes.get("last_charge_date")
        next_charge_date = attributes.get("next_charge_date")
        last_charge_status = attributes.get("last_charge_status")
        patron_status = attributes.get("patron_status")
        will_pay_amount = attributes.get("will_pay_amount_cents")

        logger.info(
            f"Extracted patron data from attributes:\n"
            f"Status: {patron_status}\n"
            f"Last charge status: {last_charge_status}\n"
            f"Last charge date: {last_charge_date}\n"
            f"Next charge date: {next_charge_date}\n"
            f"Current amount: {current_amount}\n"
            f"Will pay amount: {will_pay_amount}"
        )

        # Get tier info
        tier_data = patron_data.get("tier_data", {})
        tier_title = tier_data.get("title")

        # Get CampaignTier settings
        campaign_tier = await get_tier_settings(creator.id, tier_title, db)
        if campaign_tier:
            logger.info(f"Found campaign tier: {tier_title}")
            album_downloads = campaign_tier.album_downloads_allowed
            track_downloads = campaign_tier.track_downloads_allowed
        else:
            logger.warning(f"No campaign tier found for {tier_title}, defaulting to 0 downloads")
            album_downloads = 0
            track_downloads = 0

        # Get current stored data
        current_data = user.patreon_tier_data or {}
        
        # Build new data structure preserving all Patreon information
        new_data = {
            'title': tier_title,
            'amount_cents': current_amount,
            'will_pay_amount_cents': will_pay_amount,
            'patron_status': patron_status,
            'last_charge_status': last_charge_status,
            'last_charge_date': last_charge_date,  # Keep original format
            'next_charge_date': next_charge_date,  # Keep original format
            'album_downloads_allowed': album_downloads,
            'track_downloads_allowed': track_downloads,
            'tier_amount_cents': campaign_tier.amount_cents if campaign_tier else 0,
            'tier_description': campaign_tier.description if campaign_tier else "",
            'max_sessions': 1
        }

        # Initialize or preserve download counts
        new_data['album_downloads_used'] = current_data.get('album_downloads_used', 0)
        new_data['track_downloads_used'] = current_data.get('track_downloads_used', 0)

        # Set period_start from last_charge_date
        if last_charge_date:
            new_data['period_start'] = last_charge_date
        elif 'period_start' not in current_data:
            new_data['period_start'] = datetime.now(timezone.utc).isoformat()

        logger.info(f"New data to save: {json.dumps(new_data, indent=2)}")

        # Update user data
        user.patreon_tier_data = new_data
        db.commit()
        db.refresh(user)
        
        # Verify saved data
        logger.info(f"Verified saved data: {json.dumps(user.patreon_tier_data, indent=2)}")
        
        return user
        
    except Exception as e:
        logger.error(f"Error initializing patron downloads: {str(e)}", exc_info=True)
        db.rollback()
        raise

async def get_tier_settings(creator_id: int, tier_title: str, db: Session) -> Optional[CampaignTier]:
    """Get tier settings - handles case sensitivity and validation"""
    try:
        # First try exact match
        campaign_tier = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == creator_id,
                CampaignTier.title == tier_title,
                CampaignTier.is_active == True
            )
        ).first()
        
        # If not found, try case-insensitive match
        if not campaign_tier:
            campaign_tier = db.query(CampaignTier).filter(
                and_(
                    CampaignTier.creator_id == creator_id,
                    func.lower(CampaignTier.title) == func.lower(tier_title),
                    CampaignTier.is_active == True
                )
            ).first()
            
        if campaign_tier:
            logger.info(f"Found campaign tier: {campaign_tier.title}")
            # Update log to show both album and track downloads
            logger.info(
                f"Downloads allowed - Albums: {campaign_tier.album_downloads_allowed}, "
                f"Tracks: {campaign_tier.track_downloads_allowed}"
            )
        else:
            logger.warning(f"No campaign tier found for title: {tier_title}")
            # Update debug logging for existing tiers
            existing_tiers = db.query(CampaignTier).filter(
                CampaignTier.creator_id == creator_id
            ).all()
            logger.info("Existing campaign tiers:")
            for tier in existing_tiers:
                logger.info(
                    f"- {tier.title}: Albums={tier.album_downloads_allowed}, "
                    f"Tracks={tier.track_downloads_allowed}"
                )
                
        return campaign_tier
        
    except Exception as e:
        logger.error(f"Error getting tier settings: {str(e)}")
        return None

 
def get_album_service(db: Session = Depends(get_db)) -> AlbumService:
    return AlbumService(db)

def get_album_track_counts(db: Session, album_ids: List[str]) -> Dict[str, int]:
    """Get track counts for multiple albums efficiently"""
    try:
        results = (
            db.query(Track.album_id, func.count(Track.id).label('count'))
            .filter(Track.album_id.in_(album_ids))
            .group_by(Track.album_id)
            .all()
        )
        return {str(album_id): count for album_id, count in results}
    except Exception as e:
        logger.error(f"Error getting track counts: {str(e)}")
        return {}
def get_download_type_value(is_track: bool) -> str:
    """Return the correct string value for the DownloadType enum in database"""
    # Looking at your models.py, DownloadType maps to lowercase strings in the DB
    return "track" if is_track else "album"
async def create_user_download(
    db: Session,
    user_id: int,
    is_track: bool,
    track_id: str = None,
    album_id: str = None,
    download_path: str = "",
    original_filename: str = "",
    expires_hours: int = 24
) -> int:
    """
    Create a user download record using raw SQL to avoid enum issues.
    Returns the ID of the created download.
    """
    try:
        # Calculate expiry time
        expiry = datetime.now(timezone.utc) + timedelta(hours=expires_hours)
        
        # The enum values in the database are lowercase strings
        download_type = "track" if is_track else "album"
        
        # Convert album_id to string if it's a UUID
        if album_id and not isinstance(album_id, str):
            album_id = str(album_id)
        
        logger.info(f"Creating user download: type={download_type}, user={user_id}, track={track_id}, album={album_id}")
        
        # Insert with raw SQL to avoid enum conversion issues - FIXED for type casting
        from sqlalchemy import text
        
        if is_track:
            # Track-specific query (no UUID casting needed)
            query = text("""
                INSERT INTO user_downloads 
                (user_id, download_type, track_id, album_id, download_path, original_filename, 
                 is_available, expires_at, downloaded_at)
                VALUES 
                (:user_id, 'track', :track_id, NULL, :path, :filename, 
                 true, :expires, :now)
                RETURNING id
            """)
            
            params = {
                "user_id": user_id,
                "track_id": track_id,
                "path": download_path,
                "filename": original_filename,
                "expires": expiry,
                "now": datetime.now(timezone.utc)
            }
        else:
            # Album-specific query with proper UUID handling
            query = text("""
                INSERT INTO user_downloads 
                (user_id, download_type, track_id, album_id, download_path, original_filename, 
                 is_available, expires_at, downloaded_at)
                VALUES 
                (:user_id, 'album', NULL, CAST(:album_id AS uuid), :path, :filename, 
                 true, :expires, :now)
                RETURNING id
            """)
            
            params = {
                "user_id": user_id,
                "album_id": album_id,
                "path": download_path,
                "filename": original_filename,
                "expires": expiry,
                "now": datetime.now(timezone.utc)
            }
        
        # Execute the query with appropriate parameters
        result = db.execute(query, params)
        
        # Get the ID of the newly created download
        inserted_id = result.scalar()
        db.commit()
        
        logger.info(f"Created new download: ID={inserted_id}, Type={download_type}, User={user_id}")
        return inserted_id
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating user download: {str(e)}", exc_info=True)
        return None


ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
stream_router = APIRouter(prefix="/api/streams")
app.include_router(stream_router)
pin_router = PinManagementRouter(
    login_required=login_required,
    verify_role_permission=verify_role_permission,
    get_user_permissions=get_user_permissions
).router

app.include_router(pin_router)
logger.info("Including Patreon router...")
app.include_router(patreon_router)
logger.info("Patreon router included")
app.include_router(
    chunked_upload_router,
    prefix="/api"
)
app.include_router(discord_router)
app.include_router(my_downloads_router)
app.include_router(book_request_router)
app.include_router(book_request_pages_router)
app.include_router(kofi_router)
app.include_router(platform_router)
app.include_router(comment_router)
app.include_router(notifications_router)
app.include_router(broadcast_router)
app.include_router(forum_router)
app.include_router(forum_settings_router)
app.include_router(progress_router)

# ✅ Forum SPA route - serves forum HTML for client-side routing
@app.get("/forum", response_class=HTMLResponse)
async def forum_spa_route(request: Request, current_user: User = Depends(login_required)):
    """Forum SPA route - returns forum HTML for SPA mode"""
    return templates.TemplateResponse("forum.html", {
        "request": request,
        "user": current_user,
        "page_title": "Community Forum"
    })
app.include_router(guest_trial_router)
app.include_router(enhanced_tts_router)
app.include_router(tts_websocket_router)
app.include_router(sample_router)
app.include_router(read_along_router)
app.include_router(document_router)
app.include_router(activity_logs_router)
app.include_router(user_preferences_router)
app.include_router(scheduled_visibility_router)























     
# Routes


@app.on_event("startup")
async def startup_event():
    # Print registered routes
    print("=== Registered Routes ===")
    for route in app.routes:
        print(f"Path: {route.path}, Methods: {route.methods}")
    
    # Start the background task to clean up incomplete uploads
    logger.info("About to start cleanup background task...")
    try:
        await start_cleanup_background_task()
        logger.info("Cleanup background task started successfully")
    except Exception as e:
        logger.error(f"Failed to start cleanup background task: {e}")
    
    # Start periodic session cleanup
    asyncio.create_task(periodic_session_cleanup())

async def periodic_session_cleanup():
    while True:
        try:
            async with get_async_session() as db:
                cleaned = await session_manager.cleanup_stale_sessions(db)
                if cleaned > 0:
                    logger.info(f"Cleaned up {cleaned} stale sessions")
        except Exception as e:
            logger.error(f"Session cleanup error: {str(e)}")
        await asyncio.sleep(300) 

@app.on_event("shutdown") 
async def shutdown_event():
    await event_loop_monitor.stop()






@app.get("/api/permissions")
async def get_permissions(current_user: User = Depends(login_required)):
    """Get permissions for the current user"""
    try:
        # Create base permissions dictionary
        permissions = {
            "can_view": True,  # All authenticated users can view
            "can_create": current_user.is_creator or current_user.is_team,
            "can_rename": current_user.is_creator or current_user.is_team,
            "can_delete": current_user.is_creator,
            "can_download": False  # Default to false
        }
        
        # Add download permission for creators and team members
        if current_user.is_creator or current_user.is_team:
            permissions["can_download"] = True
            
        # Check patron download permissions
        elif current_user.is_patreon and current_user.patreon_tier_data:
            album_downloads = current_user.patreon_tier_data.get('album_downloads_allowed', 0)
            track_downloads = current_user.patreon_tier_data.get('track_downloads_allowed', 0)
            permissions["can_download"] = album_downloads > 0 or track_downloads > 0
            
        return permissions
        
    except Exception as e:
        logger.error(f"Error getting permissions: {str(e)}")
        raise HTTPException(status_code=500, detail="Error retrieving permissions")



@app.get("/")
async def root(request: Request, db: Session = Depends(get_db)):  # Add db parameter
    try:
        user = await session_manager.verify_session(request, db)  # Use the db parameter
        if user:
            return RedirectResponse(url="/home", status_code=303)
        return RedirectResponse(url="/login", status_code=303)
    except Exception as e:
        logger.error(f"Root route error: {str(e)}")
        return RedirectResponse(url="/login", status_code=303)

@app.get("/home")
async def home(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Home page with session-based auth and proper album track counts"""
    try:
        logger.info(f"Loading home page for user: {current_user.email}")
        
        # Find current session
        session = db.query(UserSession).filter(
            and_(
                UserSession.user_id == current_user.id,
                UserSession.session_id == request.cookies.get("session_id"),
                UserSession.is_active == True
            )
        ).first()
        
        if not session:
            logger.warning("No active session found")
            return RedirectResponse(url="/login", status_code=303)
            
        # Refresh session if needed
        if current_user.is_creator:
            session.extend_session(hours=48)
            db.commit()

        # Get user's album management records with limit for recent albums
        user_albums = db.query(UserAlbumManagement).filter(
            UserAlbumManagement.user_id == current_user.id
        ).order_by(
            UserAlbumManagement.created_at.desc()
        ).limit(25).all()
        
        logger.info(f"Found {len(user_albums)} user album relationships")
        
        recent_albums = []
        if user_albums:
            # Extract album IDs
            album_ids = [ua.album_id for ua in user_albums]
            logger.info(f"Fetching album details for album IDs: {album_ids}")
            
            # Fetch albums with tracks in one efficient query
            albums = (
                db.query(Album)
                .options(joinedload(Album.tracks))  # Eager load tracks
                .filter(Album.id.in_(album_ids))
                .all()
            )
            
            logger.info(f"Loaded {len(albums)} albums from the database")
            
            # Create lookup dictionary
            album_dict = {str(album.id): album for album in albums}
            
            # Map user albums to their details
            for ua in user_albums:
                album = album_dict.get(str(ua.album_id))
                if album:
                    album_info = {
                        'id': str(album.id),
                        'title': album.title,
                        'cover_path': album.cover_path or DEFAULT_COVER_URL,
                        'tracks': [  # Include full track information
                            {
                                'id': str(track.id),
                                'title': track.title,
                                'duration': track.duration,
                                'file_path': track.file_path,
                                'created_at': track.created_at.isoformat() if track.created_at else None
                            } 
                            for track in album.tracks
                        ] if album.tracks else [],
                        'track_count': len(album.tracks) if album.tracks else 0,
                        'added_at': ua.created_at.isoformat() if ua.created_at else None,
                        'last_viewed': ua.last_viewed.isoformat() if ua.last_viewed else None,
                        'view_count': ua.view_count,
                        'is_favorite': ua.is_favorite,
                        'in_collection': True
                    }
                    recent_albums.append(album_info)
                    logger.info(f"Added album to recent_albums: {album.title} with {len(album.tracks)} tracks")
                else:
                    logger.warning(f"No matching album found for album_id: {ua.album_id}")
        else:
            logger.info("No albums found for the user")

        # Get total count of in-progress tracks
        total_in_progress = db.query(PlaybackProgress).filter(
            PlaybackProgress.user_id == current_user.id,
            PlaybackProgress.completed == False,
            PlaybackProgress.position > 0
        ).count()

        # Get in-progress tracks (only 2 for home page preview)
        recent_tracks = await get_in_progress_tracks_from_router(limit=2, current_user=current_user, db=db)

        # Add flag to show all sections
        show_all_sections = True

        # Add activity logs count for badge (admin/team only)
        request = await add_activity_logs_count(request, current_user, db)

        return templates.TemplateResponse(
            "home.html",
            {
                "request": request,
                "user": current_user,
                "albums": recent_albums,
                "recent_tracks": recent_tracks,
                "total_in_progress": total_in_progress,
                "popular_tracks": [],  # Popular tracks are loaded via API
                "show_all_sections": show_all_sections,  # New flag
                "permissions": get_user_permissions(current_user)
            }
        )
    except Exception as e:
        logger.error(f"Error loading home page: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error loading home page")


@app.get("/api/support/tiers")
async def get_support_tiers_api(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """API endpoint for support tiers data - mirrors SSR endpoint exactly"""
    try:
        # ✅ REMOVED: Guest trial blocking - now accessible to all users
        
        # Get creator_id
        creator_id = current_user.id if current_user.is_creator else current_user.created_by
        logger.info(f"Loading support page API for creator_id: {creator_id}")
        
        # Get all active tiers
        tiers = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == creator_id,
                CampaignTier.is_active == True,
                CampaignTier.title != "Team Members",
                ~CampaignTier.title.ilike("%development%")
            )
        ).order_by(CampaignTier.amount_cents.asc()).all()
        
        # Create a mapping of tier names to their position in hierarchy
        tier_hierarchy = {tier.title: index for index, tier in enumerate(tiers)}
        logger.info(f"Tier hierarchy: {tier_hierarchy}")
        
        # Get all albums
        albums = db.query(Album).filter(
            Album.created_by_id == creator_id
        ).all()
        
        total_albums = len(albums)
        logger.info(f"Found {total_albums} total albums")
        
        # Process tiers
        processed_tiers = []
        for tier in tiers:
            # Skip Team Members tier
            if tier.title == "Team Members":
                continue
                
            logger.info(f"Processing tier: {tier.title} (amount: {tier.amount_cents})")
            
            # Determine if tier is Kofi
            if hasattr(tier, 'platform_type') and tier.platform_type:
                is_kofi_tier = str(tier.platform_type) == "KOFI"
            else:
                is_kofi_tier = "kofi" in tier.title.lower()
            
            # Clean description
            description = getattr(tier, 'description', None)
            if description:
                import re
                description = re.sub(r'<[^>]*>', '', description)
                if len(description) > 80:
                    description = description[:77] + '...'
            
            # Calculate accessible albums based on tier hierarchy
            tier_position = tier_hierarchy.get(tier.title, -1)
            
            # Initialize counters
            accessible_albums = 0
            restricted_albums = 0
            public_albums = 0
            
            for album in albums:
                # Check if album has restrictions
                restrictions = album.tier_restrictions
                
                if not restrictions or not restrictions.get('is_restricted', False):
                    accessible_albums += 1
                    public_albums += 1
                else:
                    # Get minimum tier name from restrictions
                    min_tier_name = restrictions.get('minimum_tier')
                    
                    if min_tier_name and min_tier_name in tier_hierarchy:
                        min_tier_position = tier_hierarchy.get(min_tier_name, float('inf'))
                        
                        # If current tier position >= minimum tier position, album is accessible
                        if tier_position >= min_tier_position:
                            accessible_albums += 1
                        else:
                            restricted_albums += 1
                    else:
                        # Fallback to amount comparison if tier name is not found
                        min_amount = restrictions.get('minimum_tier_amount', 0)
                        if tier.amount_cents >= min_amount:
                            accessible_albums += 1
                        else:
                            restricted_albums += 1
            
            # Calculate percentage
            books_percentage = round((accessible_albums / total_albums) * 100) if total_albums > 0 else 0
            
            logger.info(
                f"Tier {tier.title}: {accessible_albums}/{total_albums} albums accessible "
                f"({books_percentage}%, public: {public_albums}, restricted: {restricted_albums})"
            )
            
            tier_data = {
                "title": tier.title,
                "amount_cents": tier.amount_cents,
                "album_downloads_allowed": tier.album_downloads_allowed,
                "track_downloads_allowed": tier.track_downloads_allowed,
                'book_requests_allowed': getattr(tier, 'book_requests_allowed', 0),
                'chapters_allowed_per_book_request': getattr(tier, 'chapters_allowed_per_book_request', 0),
                "max_sessions": getattr(tier, 'max_sessions', 1),
                "is_active": tier.is_active,
                "is_kofi": is_kofi_tier,
                "description": description,
                "books_percentage": books_percentage,
                "voice_access": tier.voice_access or [],
                "read_along_access": getattr(tier, 'read_along_access', False)
            }
            
            processed_tiers.append(tier_data)
        
        return {
            "tiers": processed_tiers,
            "user": {
                "email": current_user.email,
                "is_guest_trial": current_user.is_guest_trial,
                "role": current_user.role.value if current_user.role else "unknown"
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error loading support page API: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/support")
async def support_page(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Support page showing platform options and tier benefits"""
    try:
        # ✅ REMOVED: Guest trial blocking - now accessible to all users
        
        # Get creator_id
        creator_id = current_user.id if current_user.is_creator else current_user.created_by
        logger.info(f"Loading support page for creator_id: {creator_id}")
        
        # Get all active tiers
        tiers = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == creator_id,
                CampaignTier.is_active == True,
                CampaignTier.title != "Team Members",
                ~CampaignTier.title.ilike("%development%")
            )
        ).order_by(CampaignTier.amount_cents.asc()).all()
        
        # Create a mapping of tier names to their position in hierarchy
        tier_hierarchy = {tier.title: index for index, tier in enumerate(tiers)}
        logger.info(f"Tier hierarchy: {tier_hierarchy}")
        
        # Get all albums
        albums = db.query(Album).filter(
            Album.created_by_id == creator_id
        ).all()
        
        total_albums = len(albums)
        logger.info(f"Found {total_albums} total albums")
        
        # Process tiers
        processed_tiers = []
        for tier in tiers:
            # Skip Team Members tier
            if tier.title == "Team Members":
                continue
                
            logger.info(f"Processing tier: {tier.title} (amount: {tier.amount_cents})")
            
            # Determine if tier is Kofi
            if hasattr(tier, 'platform_type') and tier.platform_type:
                is_kofi_tier = str(tier.platform_type) == "KOFI"
            else:
                is_kofi_tier = "kofi" in tier.title.lower()
            
            # Clean description
            description = getattr(tier, 'description', None)
            if description:
                import re
                description = re.sub(r'<[^>]*>', '', description)
                if len(description) > 80:
                    description = description[:77] + '...'
            
            # Calculate accessible albums based on tier hierarchy
            tier_position = tier_hierarchy.get(tier.title, -1)
            
            # Initialize counters
            accessible_albums = 0
            restricted_albums = 0
            public_albums = 0
            
            for album in albums:
                # Check if album has restrictions
                restrictions = album.tier_restrictions
                
                if not restrictions or not restrictions.get('is_restricted', False):
                    accessible_albums += 1
                    public_albums += 1
                else:
                    # Get minimum tier name from restrictions
                    min_tier_name = restrictions.get('minimum_tier')
                    
                    if min_tier_name and min_tier_name in tier_hierarchy:
                        min_tier_position = tier_hierarchy.get(min_tier_name, float('inf'))
                        
                        # If current tier position >= minimum tier position, album is accessible
                        if tier_position >= min_tier_position:
                            accessible_albums += 1
                        else:
                            restricted_albums += 1
                    else:
                        # Fallback to amount comparison if tier name is not found
                        min_amount = restrictions.get('minimum_tier_amount', 0)
                        if tier.amount_cents >= min_amount:
                            accessible_albums += 1
                        else:
                            restricted_albums += 1
            
            # Calculate percentage
            books_percentage = round((accessible_albums / total_albums) * 100) if total_albums > 0 else 0
            
            logger.info(
                f"Tier {tier.title}: {accessible_albums}/{total_albums} albums accessible "
                f"({books_percentage}%, public: {public_albums}, restricted: {restricted_albums})"
            )
            
            tier_data = {
                "title": tier.title,
                "amount_cents": tier.amount_cents,
                "album_downloads_allowed": tier.album_downloads_allowed,
                "track_downloads_allowed": tier.track_downloads_allowed,
                'book_requests_allowed': getattr(tier, 'book_requests_allowed', 0),
                'chapters_allowed_per_book_request': getattr(tier, 'chapters_allowed_per_book_request', 0),
                "max_sessions": getattr(tier, 'max_sessions', 1),
                "is_active": tier.is_active,
                "is_kofi": is_kofi_tier,
                "description": description,
                "books_percentage": books_percentage,
                "voice_access": getattr(tier, 'voice_access', []) or [],
                "read_along_access": getattr(tier, 'read_along_access', False)
            }
            
            processed_tiers.append(tier_data)
        
        return templates.TemplateResponse(
            "support.html",
            {
                "request": request,
                "user": current_user,
                "tiers": processed_tiers,
                "permissions": get_user_permissions(current_user)
            }
        )
        
    except Exception as e:
        logger.error(f"Error loading support page: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/login")
async def login_page(
    request: Request,
    db: Session = Depends(get_db)
):
    try:
        session_id = request.cookies.get("session_id")
        if session_id:
            try:
                # Pass the db session properly
                user = await session_manager.verify_session(request, Response(), db)
                if user:
                    return RedirectResponse(url="/home", status_code=303)
            except Exception as e:
                logger.error(f"Session verification error: {str(e)}")

        # Get flash message from SessionManager (works across all containers)
        flash = session_manager.get_flash(request, db)
        error_message = flash["message"] if flash else ""

        # Return login page with flash message if any
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": error_message
            }
        )
    except Exception as e:
        logger.error(f"Login page error: {str(e)}")
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": ""
            }
        )

async def handle_patreon_login(
    email: str,
    creator_pin: str, 
    db: Session,
    request: Request
) -> tuple[User, JSONResponse | None]:
    try:
        logger.info(f"Processing Patreon login for {email}")

        # Find creator by PIN
        creator = db.query(User).filter(
            and_(
                User.creator_pin == creator_pin,
                User.role == UserRole.CREATOR,
                User.is_active == True
            )
        ).first()

        if not creator:
            return None, JSONResponse(
                status_code=400,
                content={"error": "Invalid creator PIN", "step": "pin"}
            )

        # Get campaign - but don't require it
        campaign = db.query(Campaign).filter(
            and_(
                Campaign.creator_id == creator.id,
                Campaign.is_primary == True
            )
        ).first()
        
        # Instead of rejecting, just log a warning
        if not campaign:
            logger.warning(f"Creator {creator.id} has no active campaign - proceeding with legacy login")

        # Try to verify patron through Patreon API
        try:
            patron_data = await patreon_client.verify_patron(email)
            
            # If API call successful but no patron data, use fallback
            if not patron_data:
                logger.info(f"Email not found in Patreon API: {email} - using fallback")
                return await fallback_patreon_login(email, creator, campaign, db)
                
            patron_id = patron_data.get("patron_id")
            if not patron_id:
                logger.info("Invalid patron data received - using fallback")
                return await fallback_patreon_login(email, creator, campaign, db)
                
            # Continue with normal patron login flow if API call successful
            # Find or create user
            user = db.query(User).filter(
                or_(
                    User.patreon_id == patron_id,
                    User.email == email
                )
            ).first()

            if not user:
                user = User(
                    email=email,
                    username=patron_data.get("full_name") or email.split('@')[0],
                    role=UserRole.PATREON,
                    patreon_id=patron_id,
                    created_by=creator.id,
                    campaign_id=str(campaign.id) if campaign else None,
                    is_active=True
                )
                db.add(user)
                db.flush()
                logger.info(f"Created new Patreon user: {email}")
            
            # Check if this is a team member trying to login with PIN - they should use password
            if user.role == UserRole.TEAM:
                logger.warning(f"Team member {email} tried to login with PIN - redirecting to creator login")
                return None, JSONResponse(
                    status_code=400,
                    content={"error": "Team members must use the Creator Login with password", "step": "team"}
                )

            # Update user's basic info
            user.username = patron_data.get("full_name") or user.username or email.split('@')[0]
            user.email = email
            user.patreon_id = patron_id
            user.created_by = creator.id
            user.campaign_id = str(campaign.id) if campaign else None
            user.is_active = True
            user.role = UserRole.PATREON  # Ensure role is set to PATREON
            
            # Get payment amount in cents
            tier_data = patron_data.get("tier_data", {})
            amount_cents = tier_data.get("amount_cents", 0)
            
            # Find matching tier by amount
            matching_tier = await find_matching_tier(
                db=db,
                creator_id=creator.id,
                platform_type="PATREON",
                amount_cents=amount_cents
            )
            
            # If we found a matching tier by amount, use it
            if matching_tier:
                logger.info(f"Using matching tier by amount: {matching_tier.title}")
                campaign_tier = matching_tier
                tier_title = matching_tier.title
            else:
                # Fallback to the original title-based lookup
                tier_title = tier_data.get("title")
                
                if tier_title:
                    # Get the actual CampaignTier record
                    campaign_tier = db.query(CampaignTier).filter(
                        and_(
                            CampaignTier.creator_id == creator.id,
                            func.lower(CampaignTier.title) == func.lower(tier_title),
                            CampaignTier.platform_type == "PATREON",
                            CampaignTier.is_active == True
                        )
                    ).first()
                else:
                    campaign_tier = None
            
            if campaign_tier:
                logger.info(f"Found campaign tier: {tier_title} with album_downloads_allowed={campaign_tier.album_downloads_allowed}, track_downloads_allowed={campaign_tier.track_downloads_allowed}")
                
                # Update or initialize patron's tier data using actual tier limits
                current_data = user.patreon_tier_data or {}
                
                # Keep existing download counts if within new period
                album_downloads_used = 0
                track_downloads_used = 0
                book_requests_used = 0
                
                # Preserve download counts if we have period data and it's still valid
                if current_data and 'period_start' in current_data:
                    try:
                        period_start = datetime.fromisoformat(current_data['period_start'].replace('Z', '+00:00'))
                        if period_start + timedelta(days=30) > datetime.now(timezone.utc):
                            album_downloads_used = current_data.get('album_downloads_used', 0)
                            track_downloads_used = current_data.get('track_downloads_used', 0)
                            book_requests_used = current_data.get('book_requests_used', 0)
                    except (ValueError, TypeError) as e:
                        logger.error(f"Error parsing period_start date: {str(e)}")

                # Set the tier data with actual limits from CampaignTier
                user.patreon_tier_data = {
                    'title': campaign_tier.title,
                    'amount_cents': amount_cents,
                    'patron_status': patron_data.get('patron_status'),
                    'last_charge_status': patron_data.get('last_charge_status'),
                    'last_charge_date': patron_data.get('last_charge_date'),
                    'next_charge_date': patron_data.get('next_charge_date'),
                    'album_downloads_allowed': campaign_tier.album_downloads_allowed,
                    'album_downloads_used': album_downloads_used,
                    'track_downloads_allowed': campaign_tier.track_downloads_allowed,
                    'track_downloads_used': track_downloads_used,
                    'book_requests_allowed': campaign_tier.book_requests_allowed,
                    'book_requests_used': book_requests_used,
                    'period_start': datetime.now(timezone.utc).isoformat(),
                    'max_sessions': campaign_tier.max_sessions,
                    'tier_description': campaign_tier.description,
                    'platform': 'patreon'  # Explicitly set platform for consistency
                }
                
                logger.info(f"Updated tier data for {user.email}: {json.dumps(user.patreon_tier_data, indent=2)}")
            else:
                logger.warning(f"No campaign tier found for title: {tier_title} - checking for free tier")
                
                # Find or create a free tier for this creator
                free_tier = db.query(CampaignTier).filter(
                    and_(
                        CampaignTier.creator_id == creator.id,
                        CampaignTier.platform_type == "PATREON",
                        CampaignTier.is_active == True,
                        or_(
                            func.lower(CampaignTier.title).contains("free"),
                            CampaignTier.amount_cents == 0
                        )
                    )
                ).first()
                
                if not free_tier:
                    # Create a new free tier
                    free_tier = CampaignTier(
                        creator_id=creator.id,
                        title="Free Patreon",
                        description="Free access for Patreon subscribers",
                        amount_cents=0,
                        patron_count=0,
                        platform_type="PATREON",
                        is_active=True,
                        album_downloads_allowed=0,
                        track_downloads_allowed=0,
                        book_requests_allowed=0,
                        max_sessions=1,
                        created_at=datetime.now(timezone.utc),
                        updated_at=datetime.now(timezone.utc)
                    )
                    db.add(free_tier)
                    db.flush()
                    logger.info(f"Created new free Patreon tier for creator {creator.id}")
                
                # Use the free tier settings
                user.patreon_tier_data = {
                    'title': free_tier.title,
                    'amount_cents': amount_cents,
                    'patron_status': patron_data.get('patron_status'),
                    'last_charge_status': patron_data.get('last_charge_status'),
                    'last_charge_date': patron_data.get('last_charge_date'),
                    'next_charge_date': patron_data.get('next_charge_date'),
                    'album_downloads_allowed': free_tier.album_downloads_allowed,
                    'album_downloads_used': 0,
                    'track_downloads_allowed': free_tier.track_downloads_allowed,
                    'track_downloads_used': 0,
                    'book_requests_allowed': free_tier.book_requests_allowed,
                    'book_requests_used': 0,
                    'period_start': datetime.now(timezone.utc).isoformat(),
                    'max_sessions': free_tier.max_sessions,
                    'tier_description': free_tier.description,
                    'platform': 'patreon'  # Explicitly set platform for consistency
                }
                
                logger.info(f"Assigned free tier to patron {user.email} due to no matching tier")

            db.commit()
            return user, None
            
        except Exception as api_error:
            # If API call fails for any reason, use fallback
            logger.error(f"Patreon API error: {str(api_error)} - using fallback login")
            return await fallback_patreon_login(email, creator, campaign, db)

    except Exception as e:
        logger.error(f"Error in handle_patreon_login: {str(e)}", exc_info=True)
        db.rollback()
        return None, JSONResponse(
            status_code=500,
            content={"error": "Internal server error during login", "step": "server"}
        )

async def fallback_patreon_login(email: str, creator: User, campaign: Campaign, db: Session):
    """
    Fallback login for Patreon when API is unavailable or returns no data
    """
    logger.info(f"Using fallback login for Patreon user: {email}")

    # Check if the user exists in the database
    user = db.query(User).filter(
        and_(
            func.lower(User.email) == email.lower(),
            User.created_by == creator.id
        )
    ).first()

    # If user doesn't exist at all, reject the login
    if not user:
        logger.info(f"User {email} not found in database - rejecting login")
        return None, JSONResponse(
            status_code=400,
            content={"error": "Email not found or patron is inactive", "step": "email"}
        )

    # Check if this is a team member trying to login with PIN - they should use password
    if user.role == UserRole.TEAM:
        logger.warning(f"Team member {email} tried to login with PIN - redirecting to creator login")
        return None, JSONResponse(
            status_code=400,
            content={"error": "Team members must use the Creator Login with password", "step": "team"}
        )

    # For regular Patreon users
    if user.role != UserRole.PATREON:
        logger.info(f"Setting user {email} role to PATREON (was {user.role})")
        user.role = UserRole.PATREON

    # Extract key information safely
    tier_data = user.patreon_tier_data or {}
    has_payment_data = False
    last_payment_date = None
    amount_cents = tier_data.get('amount_cents', 0)
    is_gift = tier_data.get('is_gift', False)
    tier_title = tier_data.get('title')

    # Safely try to get payment date
    if tier_data and 'last_charge_date' in tier_data and tier_data['last_charge_date']:
        try:
            last_charge_date_str = tier_data['last_charge_date']
            if isinstance(last_charge_date_str, str):
                if 'Z' in last_charge_date_str:
                    last_charge_date_str = last_charge_date_str.replace('Z', '+00:00')
                last_payment_date = datetime.fromisoformat(last_charge_date_str)
                has_payment_data = True
                logger.info(f"Found last payment date: {last_payment_date.isoformat()}")
        except (ValueError, TypeError) as e:
            logger.error(f"Error parsing last_charge_date: {str(e)}")

    # Determine if active based on payment, gifts, or free tier
    is_active = False

    # Case 1: Has payment data - check expiry
    if has_payment_data and last_payment_date:
        now = datetime.now(timezone.utc)
        expiry_date = last_payment_date + relativedelta(months=1)
        is_active = now < expiry_date
        logger.info(f"User subscription status: {'active' if is_active else 'expired'}")

    # Case 2: Gift membership
    elif is_gift:
        is_active = True
        logger.info(f"User has gift membership with amount {amount_cents}")

    # Case 3: Free tier
    elif amount_cents == 0 and tier_title:
        is_active = True
        logger.info(f"User is on free tier: {tier_title}")

    # Get appropriate tier
    matching_tier = None

    # Find the right tier
    if is_active:
        # Try to match by title first
        if tier_title:
            matching_tier = db.query(CampaignTier).filter(
                and_(
                    CampaignTier.creator_id == creator.id,
                    CampaignTier.platform_type == "PATREON",
                    func.lower(CampaignTier.title) == func.lower(tier_title),
                    CampaignTier.is_active == True
                )
            ).first()

        # If gift, try to match by amount
        if not matching_tier and is_gift and amount_cents > 0:
            matching_tier = db.query(CampaignTier).filter(
                and_(
                    CampaignTier.creator_id == creator.id,
                    CampaignTier.platform_type == "PATREON",
                    CampaignTier.amount_cents <= amount_cents,
                    CampaignTier.is_active == True
                )
            ).order_by(CampaignTier.amount_cents.desc()).first()

        # If regular member, match by amount
        elif not matching_tier and amount_cents > 0:
            matching_tier = db.query(CampaignTier).filter(
                and_(
                    CampaignTier.creator_id == creator.id,
                    CampaignTier.platform_type == "PATREON",
                    CampaignTier.amount_cents <= amount_cents,
                    CampaignTier.is_active == True
                )
            ).order_by(CampaignTier.amount_cents.desc()).first()

    # If no tier found or not active, use free tier
    if not matching_tier:
        # Find free tier
        matching_tier = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == creator.id,
                CampaignTier.platform_type == "PATREON",
                CampaignTier.amount_cents == 0,
                CampaignTier.is_active == True
            )
        ).first()

        # If no free tier, create one
        if not matching_tier:
            matching_tier = CampaignTier(
                creator_id=creator.id,
                title="Free Patreon",
                description="Free access for Patreon subscribers",
                amount_cents=0,
                patron_count=0,
                platform_type="PATREON",
                is_active=True,
                album_downloads_allowed=0,
                track_downloads_allowed=0,
                book_requests_allowed=0,
                max_sessions=1,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            db.add(matching_tier)
            db.flush()
            logger.info(f"Created free tier for creator {creator.id}")

        # Set status for non-active users
        is_active = True  # Always allow login but with free tier
        logger.info(f"User assigned to free tier: {matching_tier.title}")

    # Update user tier data
    if matching_tier:
        # Preserve existing download counts if within current period
        album_downloads_used = 0
        track_downloads_used = 0
        book_requests_used = 0
        
        # Check if we have period data and if it's current
        if 'period_start' in tier_data:
            try:
                period_start = datetime.fromisoformat(tier_data['period_start'].replace('Z', '+00:00'))
                now = datetime.now(timezone.utc)
                next_reset = period_start + relativedelta(months=1)
                
                if now < next_reset:
                    # Still in current period, preserve usage counts
                    album_downloads_used = tier_data.get('album_downloads_used', 0)
                    track_downloads_used = tier_data.get('track_downloads_used', 0)
                    book_requests_used = tier_data.get('book_requests_used', 0)
                    logger.info(f"Preserving download counts for {user.email} within current period")
            except (ValueError, TypeError) as e:
                logger.error(f"Error parsing period_start: {str(e)} - resetting download counts")

        # Generate updated tier data
        updated_tier_data = {
            'title': matching_tier.title,
            'amount_cents': amount_cents,
            'patron_status': 'active_patron' if is_active else 'expired_patron',
            'album_downloads_allowed': matching_tier.album_downloads_allowed,
            'album_downloads_used': album_downloads_used,
            'track_downloads_allowed': matching_tier.track_downloads_allowed,
            'track_downloads_used': track_downloads_used,
            'book_requests_allowed': matching_tier.book_requests_allowed,
            'book_requests_used': book_requests_used,
            'period_start': datetime.now(timezone.utc).isoformat(),
            'max_sessions': matching_tier.max_sessions,
            'is_gift': is_gift,
            'platform': 'patreon'  # Explicitly set the platform
        }

        # Preserve payment history if available
        if has_payment_data:
            updated_tier_data['last_charge_date'] = tier_data.get('last_charge_date')
            updated_tier_data['next_charge_date'] = tier_data.get('next_charge_date')
            updated_tier_data['last_charge_status'] = tier_data.get('last_charge_status')

        # Update user
        user.patreon_tier_data = updated_tier_data
        user.is_active = True

        # Ensure campaign ID
        if campaign and not user.campaign_id:
            user.campaign_id = str(campaign.id)

        # Update tier patron count
        matching_tier.patron_count += 1

        db.commit()
        logger.info(f"Updated tier data for user {email} using tier {matching_tier.title}")
        return user, None

    # If we somehow got here without a tier, return an error
    logger.error(f"No suitable tier found for user {email}")
    return None, JSONResponse(
        status_code=500,
        content={"error": "No suitable tier found", "step": "tiers"}
    )

async def find_matching_tier(
    db: Session, 
    creator_id: int, 
    platform_type: str, 
    amount_cents: int
) -> Optional[CampaignTier]:
    """
    Find the highest tier that matches the payment amount for a given platform.
    
    Args:
        db: Database session
        creator_id: ID of the creator
        platform_type: Platform type (PATREON or KOFI) as string
        amount_cents: Payment amount in cents
        
    Returns:
        The highest matching tier or None if no match found
    """
    try:
        logger.info(f"Finding matching tier for amount {amount_cents} cents on platform {platform_type}")
        
        # Query tiers in descending order by amount
        tiers = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == creator_id,
                CampaignTier.platform_type == platform_type,
                CampaignTier.amount_cents <= amount_cents,  # Find tiers less than or equal to payment amount
                CampaignTier.is_active == True
            )
        ).order_by(CampaignTier.amount_cents.desc()).all()
        
        if not tiers:
            logger.info(f"No matching tiers found for amount {amount_cents} cents")
            return None
            
        # Return the highest tier that matches the amount
        matching_tier = tiers[0]
        logger.info(f"Found matching tier: {matching_tier.title} with amount {matching_tier.amount_cents} cents")
        return matching_tier
        
    except Exception as e:
        logger.error(f"Error finding matching tier: {str(e)}")
        return None


@app.post("/login")
async def login(
    request: Request,
    response: Response,
    email: str = Form(...),
    password: str = Form(None),
    creator_pin: str = Form(None),
    login_type: str = Form("kofi"),  # ✅ CHANGED: Default to kofi instead of patreon
    remember_me: bool = Form(False),
    db: Session = Depends(get_db)
):
    # 1) Validate the email
    validate_forbidden_email(email)

    try:
        logger.info(f"Processing login request for: {email} (type: {login_type})")

        # -------------------------------------------------------
        #  CREATOR/TEAM LOGIN (via Password)
        # -------------------------------------------------------
        if password and not creator_pin:
            user = db.query(User).filter(
                User.email == email,
                User.is_active == True
            ).first()

            if not user:
                session_manager.set_flash(request, db, "Invalid credentials (No active user found)")
                return RedirectResponse(url="/login", status_code=303)

            # Must be a creator or team user
            if not (user.is_creator or user.is_team):
                session_manager.set_flash(request, db, "Invalid credentials (Expected Creator/Team)")
                return RedirectResponse(url="/login", status_code=303)

            if not user.verify_password(password):
                session_manager.set_flash(request, db, "Invalid credentials (Wrong password)")
                return RedirectResponse(url="/login", status_code=303)

            # For team members
            if user.is_team:
                try:
                    # Initialize team member downloads - this will create/update the team tier
                    await initialize_team_downloads(user, db)
                except Exception as e:
                    logger.error(f"Error updating team member data: {str(e)}")
                    db.rollback()
                    session_manager.set_flash(request, db, "Error updating team member data")
                    return RedirectResponse(url="/login", status_code=303)

        # -------------------------------------------------------
        #  PATRON/KOFI/GUEST LOGIN (via creator_pin)
        # -------------------------------------------------------
        elif creator_pin and not password:
            # Determine which login handler to use based on login_type
            if login_type == "kofi":
                # Call Ko-fi login handler (handles both Ko-fi users and guest trial users)
                from kofi_routes import handle_kofi_login
                user, error_response = await handle_kofi_login(email, creator_pin, db, request)
            else:
                # Default to Patreon login
                user, error_response = await handle_patreon_login(email, creator_pin, db, request)

            # Handle error response if any
            if error_response:
                return error_response

        else:
            session_manager.set_flash(request, db, "Invalid login attempt")
            return RedirectResponse(url="/login", status_code=303)

        # -------------------------------------------------------
        #  CHECK SESSION LIMITS
        # -------------------------------------------------------
        limits = await session_manager.check_session_limits(
            user.id,
            db,
            current_session_id=request.cookies.get("session_id")
        )

        if not limits["allowed"]:
            session_manager.set_flash(request, db, limits["reason"])
            return RedirectResponse(url="/login", status_code=303)

        # -------------------------------------------------------
        #  CREATE NEW SESSION
        # -------------------------------------------------------
        try:
            session_result = await session_manager.create_session(
                db=db,
                user=user,
                request=request,
                response=response,
                remember_me=remember_me
            )

            if not session_result:
                session_manager.set_flash(request, db, "Failed to create session")
                return RedirectResponse(url="/login", status_code=303)

            # Update user's last login time
            user.last_login = datetime.now(timezone.utc)
            db.commit()

            # Create redirect response with session cookie
            redirect = RedirectResponse(url="/home", status_code=303)
            session_id = session_result["session_id"]
            max_age = session_manager.extended_session_expire if remember_me else session_manager.session_expire

            # Set session cookie
            redirect.set_cookie(
                key="session_id",
                value=session_id,
                max_age=max_age,
                **session_manager.cookie_settings
            )

            # Add campaign IDs to session data
            campaign_ids = []
            primary_campaign_id = None
            
            if user.is_creator:
                # Get all campaigns for creator
                campaigns = db.query(Campaign).filter(
                    Campaign.creator_id == user.id
                ).all()
                campaign_ids = [str(c.id) for c in campaigns]
                
                # Get primary campaign
                primary_campaign = next(
                    (c for c in campaigns if c.is_primary), 
                    campaigns[0] if campaigns else None
                )
                if primary_campaign:
                    primary_campaign_id = str(primary_campaign.id)
            else:
                # For patrons, Ko-fi users, guests, and team members, use their assigned campaign
                if user.campaign_id:
                    campaign_ids = [str(user.campaign_id)] 
                    primary_campaign_id = user.campaign_id

            # ✅ FIXED: Store platform type in session with proper guest user handling
            platform_type = None
            if user.role == UserRole.CREATOR:
                platform_type = "creator"
            elif user.role == UserRole.TEAM:
                platform_type = "team"  # Team members are marked with platform_type="team"
            elif user.role == UserRole.KOFI:
                platform_type = "kofi"
            elif user.role == UserRole.GUEST:  # ✅ ADD: Handle guest users
                platform_type = "kofi"  # ✅ Guest users are treated as kofi platform
            else:
                platform_type = "patreon"  # Default to patreon for all other roles

            # Use platform from tier data if available (for patrons/kofi users)
            if user.patreon_tier_data and 'platform' in user.patreon_tier_data:
                tier_platform = user.patreon_tier_data['platform'].lower()
                if tier_platform in ["patreon", "kofi", "team"]:
                    platform_type = tier_platform

            # ✅ SPECIAL: For guest trial users, always force kofi platform
            if user.is_guest_trial:
                platform_type = "kofi"
                logger.info(f"Guest trial user {user.email} assigned kofi platform")

            # Session data is already in PostgreSQL (UserSession.session_data)
            # No need to update request.session - using PostgreSQL sessions only

            logger.info(f"✅ Login successful for {user.email} - Role: {user.role.value}, Platform: {platform_type}")
            return redirect

        except Exception as e:
            logger.error(f"Session creation error: {str(e)}")
            session_manager.set_flash(request, db, "Error creating session")
            return RedirectResponse(url="/login", status_code=303)

    except Exception as e:
        logger.error(f"Login error: {str(e)}", exc_info=True)
        session_manager.set_flash(request, db, "An error occurred during login")
        return RedirectResponse(url="/login", status_code=303)



def validate_forbidden_email(email: str) -> None:
    """
    Raises an HTTPException if `email` matches any forbidden domain/pattern.
    This list blocks various domains often associated with 'official' publishing
    or large distribution sites for books, audiobooks, web novels, manga, etc.
    """

    forbidden_patterns = [
        # Original patterns
        r"\.official",
        r"@wuxia",
        r"@webnovel",
        r"@amazon",
        r"@pocketfm",
        r"@novelupdates",
        r"@novelpub",
        r"@royalroad",
        r"@tapread",
        r"@qidian",
        r"@jjwxc",
        r"@syosetu",
        r"@kindle",
        r"@goodnovel",
        r"@moonquill",

        # Additional major eBook & audiobook platforms
        r"@barnesandnoble",
        r"@bn\.",          # matches @bn.com
        r"@kobo",
        r"@audible",
        r"@audiobooks",
        r"@scribd",
        r"@libro",
        r"@bookwalker",    # BookWalker (light novels & manga)

        # Additional web novel / light novel platforms
        r"@wuxiaworld",    # If you also want to block @wuxiaworld
        r"@wattpad",
        r"@honeyfeed",
        r"@scribblehub",

        # Manga / comics sites
        r"@mangaplus",
        r"@shueisha",
        r"@viz",
        r"@kodansha",
        r"@comixology",
        r"@crunchyroll",
        r"@tapas",
        r"@webtoons",

        # Other reading/book platforms
        r"@goodreads",
        r"@bookbub",
        r"@overdrive",
        r"@libbyapp",
        r"@bookdepository",
    ]

    for pattern in forbidden_patterns:
        if re.search(pattern, email, re.IGNORECASE):
            raise HTTPException(
                status_code=403,
                detail="Email address not allowed. Please use a different email."
            )

@app.get("/logout")
async def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db)
):
    """Enhanced logout handler with session cleanup"""
    try:
        session_id = request.cookies.get("session_id")
        if session_id:
            session = db.query(UserSession).filter(
                and_(
                    UserSession.session_id == session_id,
                    UserSession.is_active == True
                )
            ).first()
            
            if session:
                session.end_session()
                db.commit()
                logger.info(f"Session {session_id} ended successfully")

        # Clear all cookies
        response = RedirectResponse(url="/login", status_code=303)
        for key in ["session_id", "session", "audio_session"]:
            response.delete_cookie(
                key=key,
                path="/",
                secure=session_manager.cookie_settings["secure"],
                httponly=True,
                samesite="lax"
            )

        # Session already marked inactive in PostgreSQL
        logger.info("Session marked inactive and cookies cleared")

        return response

    except Exception as e:
        logger.error(f"Logout error: {str(e)}")
        # Even if there's an error, try to clear cookies and redirect
        response = RedirectResponse(url="/login", status_code=303)
        for key in ["session_id", "session", "audio_session"]:
            response.delete_cookie(
                key=key, 
                path="/",
                secure=session_manager.cookie_settings["secure"],
                httponly=True,
                samesite="lax"
            )
        return response












@stream_router.get("/{stream_id}/status")
async def get_preparation_status(
    stream_id: str,
    current_user: User = Depends(login_required)
):
    """Get current status of stream preparation"""
    try:
        status = stream_manager.background_manager.get_status(stream_id)
        if not status:
            raise HTTPException(status_code=404, detail="Stream not found")
        return status
    except Exception as e:
        logger.error(f"Error getting stream status: {str(e)}")
        raise HTTPException(status_code=500, detail="Error checking stream status")

@app.post("/api/streams/check/{track_id}")
async def check_stream(
    track_id: str,
    request: Request,
    current_user: User = Depends(login_required)
):
    # Get session_id from cookie (PostgreSQL session)
    session_id = request.cookies.get("session_id")
    result = await stream_limiter.check_stream_limit(current_user, session_id, track_id)
    if result["can_stream"]:
        return {
            "can_stream": True,
            "stream_id": result["stream_id"],
            "heartbeat_url": f"/api/streams/heartbeat/{result['stream_id']}"
        }
    return result

@app.post("/api/streams/heartbeat/{stream_id}")
async def update_stream_heartbeat(
    stream_id: str,
    request: Request,
    current_user: User = Depends(login_required),
):
    try:
        from urllib.parse import unquote
        stream_id = unquote(stream_id)
        stream_key = f"{stream_limiter.key_prefix}stream:{stream_id}"
        
        stream_data = await stream_limiter.redis.hgetall(stream_key)
        if not stream_data:
            raise HTTPException(status_code=404)
            
        if stream_data.get('user_id') != str(current_user.id):
            raise HTTPException(status_code=403)
            
        old_ttl = await stream_limiter.redis.ttl(stream_key)
        await stream_limiter.redis.expire(stream_key, stream_limiter.stream_timeout)
        new_ttl = await stream_limiter.redis.ttl(stream_key)
        
        logger.info(f"Stream heartbeat updated - Track: {stream_data.get('track_id')}, "
                   f"Old TTL: {old_ttl}s, New TTL: {new_ttl}s")
        return {"status": "ok"}
        
    except Exception as e:
        logger.error(f"Heartbeat error: {str(e)}")
        raise HTTPException(status_code=500)

@app.post("/api/stream/{stream_id}/end")  
async def end_stream(
    stream_id: str,
    current_user: User = Depends(login_required)
):
    await stream_limiter.decrease_stream_count(current_user.id, stream_id)
    return {"status": "success"}
    
        
           
# Route for master playlist


@app.get("/api/tracks/{track_id}/check-access")
async def check_track_access(
    track_id: str,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Check access for a specific track, considering the parent album's restrictions"""
    try:
        # Get track with its album using join
        track = db.query(Track).options(
            joinedload(Track.album)
        ).filter(Track.id == track_id).first()
        
        if not track:
            raise HTTPException(status_code=404, detail="Track not found")
            
        # Creators and team members always have access
        if current_user.is_creator or current_user.is_team:
            return JSONResponse({
                "status": "ok",
                "has_access": True,
                "reason": "creator_access"
            })
            
        # Get album restrictions
        album = track.album
        restrictions = album.tier_restrictions
        
        # If no restrictions or not explicitly restricted, grant access
        if not restrictions or restrictions.get("is_restricted") is not True:
            return JSONResponse({
                "status": "ok",
                "has_access": True,
                "reason": "public_access"
            })
        
        # Get required tier name for message
        tier_message = "a higher tier subscription"
        required_tier = restrictions.get("minimum_tier", "").strip()
        if required_tier:
            tier_message = f"the {required_tier} tier or above"
        
        # Get user's amount and required amount    
        tier_data = current_user.patreon_tier_data if current_user.patreon_tier_data else {}
        user_amount = tier_data.get("amount_cents", 0)
        required_amount = restrictions.get("minimum_tier_amount", 0)
        
        logger.info(f"Track access check: User amount={user_amount}, Required amount={required_amount}, User role={current_user.role}, is_guest_trial={current_user.is_guest_trial}")
        
        # ✅ FIXED: Check for Patreon, Ko-fi, AND guest trial users
        if (current_user.is_patreon or current_user.is_kofi or current_user.is_guest_trial) and tier_data:
            # Simple amount check for all platforms (including guest trials)
            if user_amount >= required_amount:
                logger.info(f"User {current_user.email} meets tier amount criteria for track - granted access")
                return JSONResponse({
                    "status": "ok",
                    "has_access": True,
                    "reason": "tier_access"
                })
            
            # Special case for Ko-fi users with donations (not applicable to guest trials)
            if current_user.is_kofi and tier_data.get('has_donations', False):
                donation_amount = tier_data.get('donation_amount_cents', 0)
                total_amount = user_amount + donation_amount
                
                if total_amount >= required_amount:
                    logger.info(f"Ko-fi user {current_user.email} meets criteria with donations for track - granted access")
                    return JSONResponse({
                        "status": "ok",
                        "has_access": True,
                        "reason": "kofi_donation_access"
                    })
        
        # Access denied with specific tier message
        logger.info(f"User {current_user.email} does not meet tier criteria for track - denied access")
        return JSONResponse({
            "error": {
                "type": "tier_restricted",
                "message": f"This content requires {tier_message}"
            }
        }, status_code=403)
    except Exception as e:
        logger.error(f"Error checking track access: {str(e)}")
        raise HTTPException(status_code=500, detail="Error checking track access")


@app.get("/hls/{track_id}/master.m3u8")
async def serve_hls_master_route(
    track_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user = Depends(login_required)
):
    """Regular audio tracks - master playlist"""
    return await serve_hls_master(track_id, request, db, current_user)


@app.get("/hls/{track_id}/voice/{voice_id}/master.m3u8")
async def serve_hls_master_voice_route(
    track_id: str,
    voice_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user = Depends(login_required)
):
    """TTS tracks with specific voice - master playlist"""
    return await serve_hls_master(track_id, request, db, current_user, voice_id=voice_id)


@app.get("/hls/{track_id}/{quality}/playlist.m3u8")
async def serve_variant_playlist_route(
    track_id: str,
    quality: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user = Depends(login_required)
):
    """Regular audio tracks - variant playlist"""
    return await serve_variant_playlist(track_id, quality, request, db, current_user)

@app.get("/hls/{track_id}/voice/{voice_id}/{quality}/playlist.m3u8")
async def serve_variant_playlist_voice_route(
    track_id: str,
    voice_id: str,
    quality: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user = Depends(login_required)
):
    """TTS tracks with specific voice - variant playlist"""
    return await serve_variant_playlist(track_id, quality, request, db, current_user, voice_id=voice_id)


@app.get("/hls/{track_id}/{quality}/segment_{segment_id}.ts")
async def serve_segment_route(
    track_id: str,
    quality: str,
    segment_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user = Depends(login_required)
):
    """Regular audio tracks - segments"""
    return await serve_segment(track_id, quality, segment_id, request, db, current_user)

@app.get("/hls/{track_id}/voice/{voice_id}/{quality}/segment_{segment_id}.ts")
async def serve_segment_voice_route(
    track_id: str,
    voice_id: str,
    quality: str,
    segment_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user = Depends(login_required)
):
    """TTS tracks with specific voice - segments"""
    return await serve_segment(track_id, quality, segment_id, request, db, current_user, voice_id=voice_id)

async def update_session_activity(request: Request, db: Session):
    """Non-blocking session activity update"""
    try:
        session_id = request.cookies.get("session_id")
        if session_id:
            user_session = db.query(UserSession).filter(
                UserSession.session_id == session_id
            ).first()
            if user_session:
                user_session.last_active = datetime.now(timezone.utc)
                db.commit()
    except Exception as e:
        logger.error(f"Failed to update session activity: {e}")
        db.rollback()


@app.get("/api/segment-progress/{track_id}")
async def get_segment_progress_route(
    track_id: str, 
    voice_id: Optional[str] = Query(None),
    voice: Optional[str] = Query(None),  # Accept the old parameter name too
    db: Session = Depends(get_db)
):
    try:
        # Use whichever parameter was provided
        voice_param = voice_id or voice
        return await stream_manager.get_segment_progress(track_id, voice_param)
    except Exception as e:
        logger.error(f"Error getting segment progress for {track_id}: {str(e)}")
        return {
            'total': 0,
            'current': 0,
            'percent': 0,
            'status': 'error',
            'message': str(e)
        }



@app.get("/api/tracks/{track_id}/metadata")
async def get_track_metadata_route(
    track_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user = Depends(login_required)
):
    """Regular audio tracks - metadata"""
    return await get_track_metadata(track_id, request, db, current_user)

@app.get("/api/tracks/{track_id}/voice/{voice_id}/metadata")
async def get_track_metadata_voice_route(
    track_id: str,
    voice_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user = Depends(login_required)
):
    """TTS tracks with specific voice - metadata"""
    return await get_track_metadata(track_id, request, db, current_user, voice_id=voice_id)




@app.get("/api/tracks/{track_id}/voices")
async def get_track_voices_route(
    track_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(login_required)
):
    """Get available voices for a track"""
    return await get_available_voices_for_track(track_id, db, current_user)


async def _validate_hls_quick(track_id: str, voice_id: str) -> bool:
    """Quick HLS validation - checks if segments exist and playlist is complete"""
    try:
        from pathlib import Path
        import asyncio

        # Get segment directory
        home = Path.home()
        segment_base = home / ".hls_streaming" / "segments"

        if voice_id:
            segment_dir = segment_base / track_id / f"voice-{voice_id}"
        else:
            segment_dir = segment_base / track_id

        # Check in thread to avoid blocking
        def check_files():
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
            try:
                playlist_content = variant_playlist.read_text()
                if "#EXT-X-ENDLIST" not in playlist_content:
                    return False
            except:
                return False

            # Check segments exist
            segment_files = list(variant_dir.glob("segment_*.ts"))
            return len(segment_files) > 0

        return await asyncio.to_thread(check_files)

    except Exception as e:
        logger.debug(f"Quick validation failed for {track_id}/{voice_id}: {e}")
        return False


@app.get("/api/tracks/{track_id}/default-voice-status")
async def get_default_voice_status(
    track_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(login_required)
):
    """
    Get the generation status of the track's default voice from voice_generation_status table.
    Used by frontend badge logic to determine if track is playable.
    """
    from sqlalchemy import select
    from models import Track, VoiceGenerationStatus

    # Get track to find default voice
    track = db.query(Track).filter(Track.id == track_id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    # Determine default voice
    default_voice = track.default_voice or track.voice or track.tts_voice
    if not default_voice:
        # No TTS track, return ready status
        return {
            "default_voice": None,
            "status": "complete",
            "is_tts": False,
            "has_file": bool(track.file_path)
        }

    # Query voice_generation_status table
    voice_status = db.query(VoiceGenerationStatus).filter(
        VoiceGenerationStatus.track_id == track_id,
        VoiceGenerationStatus.voice_id == default_voice
    ).first()

    if not voice_status:
        # No entry yet - check if file exists (legacy tracks)
        if track.voice_directory or track.file_path:
            return {
                "default_voice": default_voice,
                "status": "complete",
                "is_tts": True,
                "has_file": True,
                "completed_at": track.tts_completed_at
            }
        else:
            return {
                "default_voice": default_voice,
                "status": "pending",
                "is_tts": True,
                "has_file": False
            }

    # Return voice generation status
    response_data = {
        "default_voice": default_voice,
        "status": voice_status.status,
        "is_tts": True,
        "started_at": voice_status.started_at.isoformat() if voice_status.started_at else None,
        "completed_at": voice_status.completed_at.isoformat() if voice_status.completed_at else None,
        "error_message": voice_status.error_message
    }

    # NEW: Auto-heal false failures on-demand
    if voice_status.status == 'failed':
        # Validate if HLS segments actually exist
        hls_valid = await _validate_hls_quick(track_id, default_voice)

        if hls_valid:
            # False failure - update status immediately
            from datetime import datetime, timezone
            voice_status.status = 'complete'
            voice_status.error_message = None
            voice_status.completed_at = datetime.now(timezone.utc)
            db.commit()

            response_data["status"] = "complete"
            response_data["completed_at"] = voice_status.completed_at.isoformat()
            response_data["error_message"] = None

            logger.info(f"✅ Auto-healed on API access: {track_id}/{default_voice}")

    return response_data


@app.get("/media/{media_type}/{filename}")
async def serve_media_route(
    media_type: str,
    filename: str,
    request: Request,
    current_user = Depends(login_required),
    range: Optional[str] = Header(None)
):
    """Media serving - works for both regular and TTS tracks"""
    return await serve_media(media_type, filename, request, current_user, range)

@app.get("/player/{track_id}")
async def player_route(
    request: Request,
    track_id: str,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Enhanced player using database - supports both regular and TTS tracks"""
    return await player(request, track_id, current_user, db)

@app.get("/player/{track_id}/voice/{voice_id}")
async def player_voice_route(
    request: Request,
    track_id: str,
    voice_id: str,
    current_user = Depends(login_required),
    db: Session = Depends(get_db)
):
    """TTS tracks with specific voice - player"""
    return await player(request, track_id, current_user, db, voice_id=voice_id)

@app.get("/api/tracks/{track_id}/tts-progress/{voice_id}")
async def get_tts_progress_route(
    track_id: str, 
    voice_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(login_required)
):
    """Get TTS generation progress for a specific voice"""
    try:
        return await get_tts_generation_progress(track_id, voice_id)
    except Exception as e:
        logger.error(f"Error getting TTS progress for {track_id}/{voice_id}: {str(e)}")
        return {
            'status': 'error',
            'progress': 0,
            'phase': 'error',
            'message': str(e)
        }








# Team Management Routes

@app.post("/creator/add-team")
@verify_role_permission(["creator"])
async def add_team_member(
    request: Request,
    email: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Add team member with new permission system including book requests"""
    try:
        # Check if email already exists
        if db.query(User).filter(User.email == email).first():
            raise HTTPException(status_code=400, detail="Email already registered")

        # Check if username already exists
        if db.query(User).filter(User.username == username).first():
            raise HTTPException(status_code=400, detail="Username already taken")

        # Get or create team tier first
        team_tier = await get_or_create_team_tier(current_user.id, db)

        # Create new team member
        team_member = User(
            email=email,
            username=username,
            password_hash=pwd_context.hash(password),
            role=UserRole.TEAM,
            is_active=True,
            created_by=current_user.id,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            patreon_tier_data={
                'title': 'Team Member',
                'album_downloads_allowed': team_tier.album_downloads_allowed,
                'track_downloads_allowed': team_tier.track_downloads_allowed,
                'book_requests_allowed': team_tier.book_requests_allowed,  # ✅ ADD THIS
                'album_downloads_used': 0,
                'track_downloads_used': 0,
                'max_sessions': team_tier.max_sessions,
                'period_start': datetime.now(timezone.utc).isoformat()
            }
        )

        db.add(team_member)
        
        # Update team tier count
        team_tier.patron_count = team_tier.patron_count + 1
        
        db.commit()
        db.refresh(team_member)

        return {
            "status": "success",
            "member": {
                "id": team_member.id,
                "email": team_member.email,
                "username": team_member.username,
                "is_active": team_member.is_active
            }
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/creator/team")
@verify_role_permission(["creator"])
async def team_management_page(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    permissions = get_user_permissions(current_user)
    
    # ✅ Fetch team members for SSR hydration (instant data on load)
    try:
        team_members = db.query(User).filter(
            and_(
                User.created_by == current_user.id,
                User.role == UserRole.TEAM
            )
        ).all()
        
        members_data = []
        for member in team_members:
            tier_data = member.patreon_tier_data or {}
            
            # Get download info
            download_info = await get_user_downloads(member, db)
            
            # Check deletion period resets
            deletion_start = tier_data.get('deletion_period_start')
            track_deletions_used = tier_data.get('track_deletions_used', 0)
            album_deletions_used = tier_data.get('album_deletions_used', 0)
            
            if deletion_start:
                try:
                    start_time = datetime.fromisoformat(deletion_start.replace('Z', '+00:00'))
                    if datetime.now(timezone.utc) >= start_time + timedelta(hours=24):
                        tier_data['track_deletions_used'] = 0
                        tier_data['album_deletions_used'] = 0
                        tier_data['deletion_period_start'] = datetime.now(timezone.utc).isoformat()
                        member.patreon_tier_data = tier_data
                        db.commit()
                        db.refresh(member)
                        track_deletions_used = 0
                        album_deletions_used = 0
                except (ValueError, TypeError):
                    tier_data['track_deletions_used'] = 0
                    tier_data['album_deletions_used'] = 0
                    tier_data['deletion_period_start'] = datetime.now(timezone.utc).isoformat()
                    member.patreon_tier_data = tier_data
                    db.commit()
                    db.refresh(member)
                    track_deletions_used = 0
                    album_deletions_used = 0

            # Get book request quota
            quota = await get_user_book_request_quota(member, db)
            
            # Refresh tier_data after potential updates
            tier_data = member.patreon_tier_data or {}
            
            member_data = {
                "id": member.id,
                "username": member.username,
                "email": member.email,
                "last_login": member.last_login.isoformat() if member.last_login else None,
                "is_active": member.is_active,
                "created_at": member.created_at.isoformat() if member.created_at else None,
                
                # Download permissions
                "album_downloads_allowed": download_info['albums']['downloads_allowed'],
                "track_downloads_allowed": download_info['tracks']['downloads_allowed'],
                "album_downloads_used": download_info['albums']['downloads_used'],
                "track_downloads_used": download_info['tracks']['downloads_used'],
                "album_downloads_remaining": download_info['albums']['downloads_remaining'],
                "track_downloads_remaining": download_info['tracks']['downloads_remaining'],
                
                # Book request permissions
                "book_requests_allowed": quota["requests_allowed"],
                "book_requests_used": quota["requests_used"],
                "book_requests_remaining": quota["requests_remaining"],
                
                # Deletion permissions
                "track_deletions_allowed": tier_data.get('track_deletions_allowed', 0),
                "track_deletions_used": track_deletions_used,
                "track_deletions_remaining": max(0, tier_data.get('track_deletions_allowed', 0) - track_deletions_used),
                "album_deletions_allowed": tier_data.get('album_deletions_allowed', 0),
                "album_deletions_used": album_deletions_used,
                "album_deletions_remaining": max(0, tier_data.get('album_deletions_allowed', 0) - album_deletions_used),
                
                # Default to offline for SSR (JS will update with real-time status)
                "is_online": False
            }
            members_data.append(member_data)
        
        team_count = len(members_data)
        
    except Exception as e:
        logger.error(f"Error fetching team members for SSR: {str(e)}")
        members_data = []
        team_count = 0

    return templates.TemplateResponse("team.html", {
        "request": request,
        "user": current_user,
        "permissions": permissions,
        "team_count": team_count,
        "team_members": members_data  # ✅ This fixes the error!
    })

@app.get("/api/team/members")
@verify_role_permission(["creator"])
async def list_team_members(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """List team members with individual permission settings, book requests, AND online status"""
    try:
        team_members = db.query(User).filter(
            and_(
                User.created_by == current_user.id,
                User.role == UserRole.TEAM
            )
        ).all()

        # ✅ GET ONLINE STATUS (combined in one query)
        now = datetime.now(timezone.utc)
        five_minutes_ago = now - timedelta(minutes=5)
        
        team_member_ids = [member.id for member in team_members]
        
        active_sessions = db.query(UserSession).filter(
            and_(
                UserSession.user_id.in_(team_member_ids),
                UserSession.is_active == True,
                UserSession.last_active > five_minutes_ago
            )
        ).all()
        
        online_member_ids = {session.user_id for session in active_sessions}

        members_data = []
        for member in team_members:
            tier_data = member.patreon_tier_data or {}
            
            # Use existing download info function
            download_info = await get_user_downloads(member, db)
            
            # Check deletion period resets
            deletion_start = tier_data.get('deletion_period_start')
            track_deletions_used = tier_data.get('track_deletions_used', 0)
            album_deletions_used = tier_data.get('album_deletions_used', 0)
            
            if deletion_start:
                try:
                    start_time = datetime.fromisoformat(deletion_start.replace('Z', '+00:00'))
                    if datetime.now(timezone.utc) >= start_time + timedelta(hours=24):
                        logger.info(f"🔄 Resetting 24hr deletion period for team member {member.email}")
                        tier_data['track_deletions_used'] = 0
                        tier_data['album_deletions_used'] = 0
                        tier_data['deletion_period_start'] = datetime.now(timezone.utc).isoformat()
                        member.patreon_tier_data = tier_data
                        db.commit()
                        db.refresh(member)
                        track_deletions_used = 0
                        album_deletions_used = 0
                except (ValueError, TypeError) as e:
                    logger.warning(f"Error parsing deletion period for {member.email}: {str(e)} - resetting")
                    tier_data['track_deletions_used'] = 0
                    tier_data['album_deletions_used'] = 0
                    tier_data['deletion_period_start'] = datetime.now(timezone.utc).isoformat()
                    member.patreon_tier_data = tier_data
                    db.commit()
                    db.refresh(member)
                    track_deletions_used = 0
                    album_deletions_used = 0

            # Get book request usage
            quota = await get_user_book_request_quota(member, db)

            # Refresh tier_data after potential updates
            tier_data = member.patreon_tier_data or {}
            
            member_data = {
                "id": member.id,
                "username": member.username,
                "email": member.email,
                "last_login": member.last_login.isoformat() if member.last_login else None,
                "is_active": member.is_active,
                "created_at": member.created_at.isoformat() if member.created_at else None,
                
                # DOWNLOAD PERMISSIONS
                "album_downloads_allowed": download_info['albums']['downloads_allowed'],
                "track_downloads_allowed": download_info['tracks']['downloads_allowed'],
                "album_downloads_used": download_info['albums']['downloads_used'],
                "track_downloads_used": download_info['tracks']['downloads_used'],
                "album_downloads_remaining": download_info['albums']['downloads_remaining'],
                "track_downloads_remaining": download_info['tracks']['downloads_remaining'],
                
                # BOOK REQUEST PERMISSIONS
                "book_requests_allowed": quota["requests_allowed"],
                "book_requests_used": quota["requests_used"],
                "book_requests_remaining": quota["requests_remaining"],
                
                # DELETION PERMISSIONS
                "track_deletions_allowed": tier_data.get('track_deletions_allowed', 0),
                "track_deletions_used": track_deletions_used,
                "track_deletions_remaining": max(0, tier_data.get('track_deletions_allowed', 0) - track_deletions_used),
                "album_deletions_allowed": tier_data.get('album_deletions_allowed', 0),
                "album_deletions_used": album_deletions_used,
                "album_deletions_remaining": max(0, tier_data.get('album_deletions_allowed', 0) - album_deletions_used),
                
                # ✅ ONLINE STATUS (included in same response)
                "is_online": member.id in online_member_ids
            }
            members_data.append(member_data)

        return members_data
    except Exception as e:
        logger.error(f"Error in list_team_members: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/team/members/{member_id}/details")
@verify_role_permission(["creator"])
async def update_team_member_details(
    member_id: int,
    details_data: dict,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Update team member details (email, username, password)"""
    try:
        team_member = db.query(User).filter(
            and_(
                User.id == member_id,
                User.created_by == current_user.id,
                User.role == UserRole.TEAM
            )
        ).first()

        if not team_member:
            raise HTTPException(status_code=404, detail="Team member not found")

        updated_fields = []
        
        # Update email if provided
        if 'email' in details_data and details_data['email']:
            new_email = details_data['email'].strip().lower()
            
            # Check if email already exists (excluding current user)
            existing_user = db.query(User).filter(
                and_(
                    User.email == new_email,
                    User.id != member_id
                )
            ).first()
            
            if existing_user:
                raise HTTPException(status_code=400, detail="Email already in use")
            
            team_member.email = new_email
            updated_fields.append("email")
        
        # Update username if provided
        if 'username' in details_data and details_data['username']:
            new_username = details_data['username'].strip()
            
            # Check if username already exists (excluding current user)
            existing_user = db.query(User).filter(
                and_(
                    User.username == new_username,
                    User.id != member_id
                )
            ).first()
            
            if existing_user:
                raise HTTPException(status_code=400, detail="Username already taken")
            
            team_member.username = new_username
            updated_fields.append("username")
        
        # Update password if provided
        if 'password' in details_data and details_data['password']:
            new_password = details_data['password'].strip()
            
            if len(new_password) < 6:
                raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
            
            team_member.password_hash = pwd_context.hash(new_password)
            updated_fields.append("password")
        
        # Update timestamps
        team_member.updated_at = datetime.now(timezone.utc)
        
        db.commit()
        db.refresh(team_member)
        
        return {
            "status": "success",
            "message": f"Updated {', '.join(updated_fields)} for {team_member.username}",
            "member": {
                "id": team_member.id,
                "username": team_member.username,
                "email": team_member.email,
                "updated_fields": updated_fields
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating team member details: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/team/members/{member_id}/permissions")
@verify_role_permission(["creator"])
async def update_individual_team_permissions(
    member_id: int,
    permission_data: dict,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Update individual permissions for a specific team member including book requests"""
    try:
        team_member = db.query(User).filter(
            and_(
                User.id == member_id,
                User.created_by == current_user.id,
                User.role == UserRole.TEAM
            )
        ).first()

        if not team_member:
            raise HTTPException(status_code=404, detail="Team member not found")

        # Get current tier data
        tier_data = team_member.patreon_tier_data or {}
        
        # Update download permissions (existing)
        if 'album_downloads_allowed' in permission_data:
            tier_data['album_downloads_allowed'] = permission_data['album_downloads_allowed']
        
        if 'track_downloads_allowed' in permission_data:
            tier_data['track_downloads_allowed'] = permission_data['track_downloads_allowed']
        
        # ✅ ADD: Update book request permissions
        if 'book_requests_allowed' in permission_data:
            book_requests_allowed = permission_data['book_requests_allowed']
            tier_data['book_requests_allowed'] = book_requests_allowed
            logger.info(f"✅ Updated book request limit for {team_member.email}: {book_requests_allowed}")
        
        # Update deletion permissions (existing)
        if 'track_deletions_allowed' in permission_data:
            new_track_deletions_allowed = permission_data['track_deletions_allowed']
            tier_data['track_deletions_allowed'] = new_track_deletions_allowed
            
            if new_track_deletions_allowed > 0 and 'deletion_period_start' not in tier_data:
                tier_data['track_deletions_used'] = 0
                tier_data['deletion_period_start'] = datetime.now(timezone.utc).isoformat()
            elif new_track_deletions_allowed == 0:
                tier_data['track_deletions_used'] = 0
        
        if 'album_deletions_allowed' in permission_data:
            new_album_deletions_allowed = permission_data['album_deletions_allowed']
            tier_data['album_deletions_allowed'] = new_album_deletions_allowed
            
            if new_album_deletions_allowed > 0 and 'deletion_period_start' not in tier_data:
                tier_data['album_deletions_used'] = 0
                tier_data['deletion_period_start'] = datetime.now(timezone.utc).isoformat()
            elif new_album_deletions_allowed == 0:
                tier_data['album_deletions_used'] = 0
        
        # Save updated permissions
        team_member.patreon_tier_data = tier_data
        team_member.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(team_member)
        
        return {
            "status": "success",
            "message": f"Permissions updated for {team_member.username}",
            "member": {
                "id": team_member.id,
                "username": team_member.username,
                "album_downloads_allowed": tier_data.get('album_downloads_allowed', 0),
                "track_downloads_allowed": tier_data.get('track_downloads_allowed', 0),
                "book_requests_allowed": tier_data.get('book_requests_allowed', 0),  # ✅ ADD THIS
                "track_deletions_allowed": tier_data.get('track_deletions_allowed', 0),
                "track_deletions_used": tier_data.get('track_deletions_used', 0),
                "album_deletions_allowed": tier_data.get('album_deletions_allowed', 0),
                "album_deletions_used": tier_data.get('album_deletions_used', 0)
            }
        }

    except Exception as e:
        logger.error(f"Error updating individual permissions: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/team/members/{member_id}")
@verify_role_permission(["creator"])
async def delete_team_member(
    member_id: int,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Delete team member with new permission system"""
    try:
        logger.info(f"Starting deletion of team member {member_id}")
        
        team_member = db.query(User).filter(
            and_(
                User.id == member_id,
                User.created_by == current_user.id,
                User.role == UserRole.TEAM
            )
        ).first()

        if not team_member:
            raise HTTPException(status_code=404, detail="Team member not found")

        logger.info(f"Team member before deletion: {team_member.email}")
        if hasattr(team_member, 'patreon_tier_data'):
            logger.info(f"Team member patreon_tier_data: {team_member.patreon_tier_data}")

        # Use raw SQL to delete all possible related records that might have enum issues
        try:
            # First delete downloads
            db.execute(text(f"DELETE FROM user_downloads WHERE user_id = {member_id}"))
            logger.info(f"Successfully deleted downloads for user {member_id}")
            
            # Delete notifications
            db.execute(text(f"DELETE FROM notifications WHERE user_id = {member_id} OR sender_id = {member_id}"))
            logger.info(f"Successfully deleted notifications for user {member_id}")
            
            # Delete comments
            db.execute(text(f"DELETE FROM comments WHERE user_id = {member_id}"))
            logger.info(f"Successfully deleted comments for user {member_id}")
            
            # Delete comment likes
            db.execute(text(f"DELETE FROM comment_likes WHERE user_id = {member_id}"))
            logger.info(f"Successfully deleted comment likes for user {member_id}")
            
            # Delete comment reports
            db.execute(text(f"DELETE FROM comment_reports WHERE user_id = {member_id}"))
            logger.info(f"Successfully deleted comment reports for user {member_id}")
            
            # Delete playback progress
            db.execute(text(f"DELETE FROM playback_progress WHERE user_id = {member_id}"))
            logger.info(f"Successfully deleted playback progress for user {member_id}")
            
            # Delete user sessions
            db.execute(text(f"DELETE FROM user_sessions WHERE user_id = {member_id}"))
            logger.info(f"Successfully deleted user sessions for user {member_id}")
            
            # Delete track plays
            db.execute(text(f"DELETE FROM track_plays WHERE user_id = {member_id}"))
            logger.info(f"Successfully deleted track plays for user {member_id}")
            
            # Delete book requests
            db.execute(text(f"DELETE FROM book_requests WHERE user_id = {member_id}"))
            logger.info(f"Successfully deleted book requests for user {member_id}")
            
            # DELETE FORUM USER SETTINGS - THIS WAS MISSING
            db.execute(text(f"DELETE FROM forum_user_settings WHERE user_id = {member_id}"))
            logger.info(f"Successfully deleted forum user settings for user {member_id}")
            
            # Flush to ensure all deletes are processed
            db.flush()
            
        except Exception as delete_err:
            logger.error(f"Error deleting related records: {str(delete_err)}")
            # We'll continue anyway
        
        try:
            # Now delete the team member
            db.delete(team_member)
            db.flush()
            logger.info(f"Successfully deleted team member {member_id}")
        except Exception as user_delete_err:
            logger.error(f"Error during user deletion: {str(user_delete_err)}")
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Error deleting user: {str(user_delete_err)}")
        
        # Verify deletion
        deleted_check = db.query(User).get(member_id)
        logger.info(f"Member deletion status - exists: {deleted_check is not None}")
        
        # Now update the tier count
        try:
            await update_team_tier_count(current_user.id, db)
        except Exception as tier_err:
            logger.error(f"Error updating team tier count: {str(tier_err)}")
            # Continue despite error
        
        # Final verification
        final_count = db.query(User).filter(
            and_(
                User.created_by == current_user.id,
                User.role == UserRole.TEAM,
                User.is_active == True
            )
        ).count()
        
        logger.info(f"Final team member count: {final_count}")
        
        db.commit()
        
        return {
            "status": "success",
            "message": "Team member deleted successfully",
            "final_count": final_count
        }

    except Exception as e:
        logger.error(f"Error in delete_team_member: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))







# Album and Track Routes

@app.get("/collection")
async def collection(
    request: Request,
    page: int = 1,
    per_page: int = None,
    search: str = None,  # ✅ ADD THIS
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Collection route with pagination and search - no track loading"""
    try:
        # Get creator ID
        creator_id = current_user.id if current_user.is_creator else current_user.created_by
        logger.info(f"Loading collection for user {current_user.email} (creator_id: {creator_id})")
        
        # Determine page size based on user agent (mobile vs desktop)
        if per_page is None:
            user_agent = request.headers.get('user-agent', '').lower()
            is_mobile = any(device in user_agent for device in ['mobile', 'android', 'iphone', 'ipad'])
            per_page = 12 if is_mobile else 24  # Smaller pages for mobile
        
        # Get user's tier data for access checks
        user_tier_data = current_user.patreon_tier_data or {}
        user_tier_amount = user_tier_data.get('amount_cents', 0)
        logger.info(f"User tier amount: {user_tier_amount}")
        
        # Get track counts for albums in one efficient query (only for current page albums)
        offset = (page - 1) * per_page
        
        # ✅ Build albums query with optional search filtering
        albums_query = db.query(Album).options(
            joinedload(Album.user_management)  # Keep this for collection status
        ).filter(
            Album.created_by_id == creator_id
        )

        # Apply visibility filtering based on user role
        if not current_user.is_creator:
            if current_user.is_team:
                # Team members can see all except hidden_from_all
                albums_query = albums_query.filter(Album.visibility_status != "hidden_from_all")
            else:
                # Regular users (Patreon/Ko-fi/guests) can only see visible albums
                albums_query = albums_query.filter(Album.visibility_status == "visible")

        # ✅ ADD SEARCH FILTERING
        if search and search.strip():
            search_term = f"%{search.strip()}%"
            albums_query = albums_query.filter(
                Album.title.ilike(search_term)
            )
            logger.info(f"Applying search filter: '{search.strip()}'")

        albums_query = albums_query.order_by(Album.created_at.desc())
        
        # Get total count for pagination
        total_albums = albums_query.count()
        
        # Get albums for current page
        albums = albums_query.offset(offset).limit(per_page).all()
        
        logger.info(f"Found {len(albums)} albums for page {page} (total: {total_albums})")
        
        # Get track counts only for albums on current page
        album_ids = [album.id for album in albums]
        track_counts = {}
        if album_ids:
            track_count_results = db.query(
                Track.album_id,
                func.count(Track.id).label('track_count')
            ).filter(Track.album_id.in_(album_ids)).group_by(Track.album_id).all()
            track_counts = dict(track_count_results)
        
        logger.info(f"Retrieved track counts for {len(track_counts)} albums")
        
        # Get user's album management records for current page
        user_albums = set()
        user_favorites = set()
        if album_ids:
            user_album_mgmt = db.query(UserAlbumManagement).filter(
                and_(
                    UserAlbumManagement.user_id == current_user.id,
                    UserAlbumManagement.album_id.in_([str(aid) for aid in album_ids])
                )
            ).all()
            
            user_albums = {str(ua.album_id) for ua in user_album_mgmt}
            user_favorites = {str(ua.album_id) for ua in user_album_mgmt if ua.is_favorite}
        
        logger.info(f"User has {len(user_albums)} albums in collection, {len(user_favorites)} favorites")

        # Process albums (same logic as before)
        processed_albums = []
        restricted_count = 0
        for album in albums:
            track_count = track_counts.get(album.id, 0)
            
            # Check tier access
            has_access = True
            if album.tier_restrictions and album.tier_restrictions.get('is_restricted') is True:
                restricted_count += 1
                if not (current_user.is_creator or current_user.is_team):
                    minimum_amount = album.tier_restrictions.get('minimum_tier_amount', 0)
                    has_access = user_tier_amount >= minimum_amount
                    
                    # Special case for Ko-fi users with donations
                    if not has_access and current_user.is_kofi and user_tier_data.get('has_donations', False):
                        donation_amount = user_tier_data.get('donation_amount_cents', 0)
                        total_amount = user_tier_amount + donation_amount
                        has_access = total_amount >= minimum_amount
            
            album_dict = {
                "id": str(album.id),
                "title": album.title,
                "cover_path": album.cover_path or DEFAULT_COVER_URL,
                "created_at": album.created_at.isoformat() if album.created_at else None,
                "updated_at": album.updated_at.isoformat() if album.updated_at else None,
                "tier_restrictions": album.tier_restrictions,
                "visibility_status": album.visibility_status,
                "in_collection": str(album.id) in user_albums,
                "is_favorite": str(album.id) in user_favorites,
                "track_count": track_count,
                "creator_id": album.created_by_id,
                "has_access": has_access
            }
            processed_albums.append(album_dict)
        
        logger.info(f"Processed {len(processed_albums)} albums ({restricted_count} restricted)")

        # Calculate pagination info
        total_pages = (total_albums + per_page - 1) // per_page
        has_next = page < total_pages
        has_prev = page > 1

        # ✅ ADD SEARCH METADATA FOR AJAX REQUESTS
        search_metadata = {}
        if search and search.strip():
            search_metadata = {
                "query": search.strip(),
                "result_count": total_albums
            }

        # For AJAX requests, return JSON
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            response_data = {
                "albums": processed_albums,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": total_albums,
                    "total_pages": total_pages,
                    "has_next": has_next,
                    "has_prev": has_prev
                }
            }
            
            # ✅ ADD SEARCH METADATA TO RESPONSE
            if search_metadata:
                response_data["search"] = search_metadata
                
            return response_data

        # Get other data for initial page load
        available_tiers = []
        if current_user.is_creator or current_user.is_team:
            tiers = db.query(CampaignTier).filter(
                and_(
                    CampaignTier.creator_id == creator_id,
                    CampaignTier.is_active == True
                )
            ).order_by(CampaignTier.amount_cents).all()
            
            available_tiers = [
                {
                    "id": tier.id,
                    "uuid": tier.uuid,
                    "title": tier.title,
                    "amount_cents": tier.amount_cents,
                    "patron_count": tier.patron_count,
                    "description": tier.description,
                    "album_downloads_allowed": tier.album_downloads_allowed,
                    "track_downloads_allowed": tier.track_downloads_allowed
                }
                for tier in tiers
            ]

        # Get download info
        download_info = None
        if current_user.is_creator:
            download_info = {
                "albums": {"downloads_allowed": float('inf'), "downloads_used": 0, "downloads_remaining": float('inf')},
                "tracks": {"downloads_allowed": float('inf'), "downloads_used": 0, "downloads_remaining": float('inf')}
            }
        elif current_user.is_team or current_user.is_patreon or current_user.is_kofi:
            download_info = await get_user_downloads(current_user, db)

        if download_info:
            current_user.download_info = download_info

        return templates.TemplateResponse(
            "collection.html",
            {
                "request": request,
                "user": current_user,
                "albums": processed_albums,
                "permissions": get_user_permissions(current_user),
                "available_tiers": available_tiers,
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": total_albums,
                    "total_pages": total_pages,
                    "has_next": has_next,
                    "has_prev": has_prev
                }
            }
        )

    except Exception as e:
        logger.error(f"Error in collection route: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error loading collection")


@app.get("/api/collection/albums")
async def get_collection_albums(
    request: Request,
    page: int = 1,
    per_page: int = 24,
    search: str = None,  # ✅ ADD THIS
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """
    API endpoint for loading albums with pagination and search (AJAX support)
    Returns JSON response for infinite scroll
    """
    try:
        # Get creator ID
        creator_id = current_user.id if current_user.is_creator else current_user.created_by
        
        # Determine page size based on user agent if not specified
        user_agent = request.headers.get('user-agent', '').lower()
        is_mobile = any(device in user_agent for device in ['mobile', 'android', 'iphone', 'ipad'])
        
        if per_page > 50:  # Limit max page size
            per_page = 50
        elif per_page <= 0:
            per_page = 12 if is_mobile else 24
            
        # Get user's tier data for access checks
        user_tier_data = current_user.patreon_tier_data or {}
        user_tier_amount = user_tier_data.get('amount_cents', 0)
        
        # Calculate offset
        offset = (page - 1) * per_page
        
        # ✅ Build albums query with optional search filtering
        albums_query = db.query(Album).filter(
            Album.created_by_id == creator_id
        )

        # Apply visibility filtering based on user role
        if not current_user.is_creator:
            if current_user.is_team:
                # Team members can see all except hidden_from_all
                albums_query = albums_query.filter(
                    Album.visibility_status != "hidden_from_all"
                )
            else:
                # Regular users (Patreon/Ko-fi guests) can only see visible albums
                albums_query = albums_query.filter(
                    Album.visibility_status == "visible"
                )

        # ✅ ADD SEARCH FILTERING
        if search and search.strip():
            search_term = f"%{search.strip()}%"
            albums_query = albums_query.filter(
                Album.title.ilike(search_term)
            )
            logger.info(f"API search filter applied: '{search.strip()}'")
        
        albums_query = albums_query.order_by(Album.created_at.desc())
        
        # Get total count
        total_albums = albums_query.count()
        
        # Get albums for current page
        albums = albums_query.offset(offset).limit(per_page).all()
        
        # Early return if no albums
        if not albums:
            response_data = {
                "albums": [],
                "pagination": {
                    "page": page,
                    "per_page": per_page,
                    "total": total_albums,
                    "total_pages": 0,
                    "has_next": False,
                    "has_prev": False
                }
            }
            
            # ✅ ADD SEARCH METADATA EVEN FOR EMPTY RESULTS
            if search and search.strip():
                response_data["search"] = {
                    "query": search.strip(),
                    "result_count": 0
                }
            
            return response_data
        
        # Get track counts for current page albums
        album_ids = [album.id for album in albums]
        track_count_results = db.query(
            Track.album_id,
            func.count(Track.id).label('track_count')
        ).filter(Track.album_id.in_(album_ids)).group_by(Track.album_id).all()
        track_counts = dict(track_count_results)
        
        # Get user's album management records
        user_album_mgmt = db.query(UserAlbumManagement).filter(
            and_(
                UserAlbumManagement.user_id == current_user.id,
                UserAlbumManagement.album_id.in_([str(aid) for aid in album_ids])
            )
        ).all()
        
        user_albums = {str(ua.album_id) for ua in user_album_mgmt}
        user_favorites = {str(ua.album_id) for ua in user_album_mgmt if ua.is_favorite}
        
        # Process albums
        processed_albums = []
        for album in albums:
            track_count = track_counts.get(album.id, 0)
            
            # Check tier access
            has_access = True
            if album.tier_restrictions and album.tier_restrictions.get('is_restricted') is True:
                if not (current_user.is_creator or current_user.is_team):
                    minimum_amount = album.tier_restrictions.get('minimum_tier_amount', 0)
                    has_access = user_tier_amount >= minimum_amount
                    
                    # Special case for Ko-fi users with donations
                    if not has_access and current_user.is_kofi and user_tier_data.get('has_donations', False):
                        donation_amount = user_tier_data.get('donation_amount_cents', 0)
                        total_amount = user_tier_amount + donation_amount
                        has_access = total_amount >= minimum_amount
            
            album_dict = {
                "id": str(album.id),
                "title": album.title,
                "cover_path": album.cover_path or DEFAULT_COVER_URL,
                "created_at": album.created_at.isoformat() if album.created_at else None,
                "updated_at": album.updated_at.isoformat() if album.updated_at else None,
                "tier_restrictions": album.tier_restrictions,
                "visibility_status": album.visibility_status,
                "in_collection": str(album.id) in user_albums,
                "is_favorite": str(album.id) in user_favorites,
                "track_count": track_count,
                "creator_id": album.created_by_id,
                "has_access": has_access
            }
            processed_albums.append(album_dict)
        
        # Calculate pagination info
        total_pages = (total_albums + per_page - 1) // per_page
        has_next = page < total_pages
        has_prev = page > 1
        
        response_data = {
            "albums": processed_albums,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total_albums,
                "total_pages": total_pages,
                "has_next": has_next,
                "has_prev": has_prev
            },
            "user_permissions": {
                "can_download": bool(current_user.is_creator or current_user.is_team or current_user.is_patreon or current_user.is_kofi),
                "can_create": bool(current_user.is_creator),
                "can_rename": bool(current_user.is_creator or current_user.is_team),
                "can_delete": bool(current_user.is_creator)
            }
        }
        
        # ✅ ADD SEARCH METADATA TO RESPONSE
        if search and search.strip():
            response_data["search"] = {
                "query": search.strip(),
                "result_count": total_albums
            }
        
        return response_data
        
    except Exception as e:
        logger.error(f"Error in AJAX collection endpoint: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error loading albums")


async def load_creator_albums(user_id: int, db: Session) -> list:
    """Load albums from database instead of JSON file"""
    try:
        # Get creator ID based on user type
        creator_id = user_id
        if db:
            user = db.query(User).filter(User.id == user_id).first()
            if user and user.is_team and user.created_by:
                creator_id = user.created_by
            elif not user:
                logger.error(f"User not found: {user_id}")
                return []

        # Query albums from database
        albums = db.query(Album).filter(Album.created_by_id == creator_id).all()
        logger.info(f"Found {len(albums)} albums in database")

        # Convert to dictionary format
        return [album.to_dict() for album in albums]

    except Exception as e:
        logger.error(f"Error loading albums from database: {str(e)}")
        return []

@app.get("/album/{album_id}")
async def view_album(
    request: Request,
    album_id: str,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db),
    album_service: AlbumService = Depends(get_album_service)
):
    """
    View album route
    """
    try:
        # Get album without access check since it was already verified
        album = await album_service.get_album(
            album_id=album_id,
            user_id=current_user.id,
            check_access=False
        )

        if not album:
            raise HTTPException(status_code=404, detail="Album not found")

        # ✅ Use proper SQL ordering instead of Python sorting
        tracks = db.query(Track).filter(
            Track.album_id == album_id
        ).order_by(
            Track.order.asc().nulls_last(),
            Track.created_at.asc()
        ).all()

        tracks_data = []
        track_ids_in_order = []

        logger.info(f"========== ALBUM DEBUG: Processing {len(tracks)} tracks ==========")
        for track in tracks:
            track_data = track.to_dict()
            # Debug: Log visibility_status for each track
            logger.info(f"Track '{track.title}' (id={track.id})")
            logger.info(f"  - DB object visibility_status: {track.visibility_status}")
            logger.info(f"  - Dict has visibility_status key: {'visibility_status' in track_data}")
            logger.info(f"  - Dict visibility_status value: {track_data.get('visibility_status')}")
            tracks_data.append(track_data)
            track_ids_in_order.append(track.id)

        # Calculate total plays across all tracks in the album
        total_plays = db.query(func.sum(TrackPlays.play_count)).join(
            Track, TrackPlays.track_id == Track.id
        ).filter(
            Track.album_id == album_id
        ).scalar() or 0

        logger.info(f"Album {album_id} total plays: {total_plays}")

        # Prepare album data
        album_data = {
            "id": str(album.id),
            "title": album.title,
            "cover_path": album.cover_path or "/static/images/default-album.jpg",
            "created_by_id": album.created_by_id,
            "created_at": album.created_at.isoformat() if album.created_at else None,
            "updated_at": album.updated_at.isoformat() if album.updated_at else None,
            "tier_restrictions": album.tier_restrictions,
            "tracks": tracks_data,
            "ordered_track_ids": track_ids_in_order,
            "total_plays": int(total_plays)
        }

        # Debug: Log what's in album_data.tracks
        logger.info(f"========== ALBUM DATA: Checking tracks in album_data ==========")
        for idx, track in enumerate(album_data['tracks']):
            logger.info(f"Track {idx+1}: '{track.get('title')}' - visibility_status: {track.get('visibility_status')}")

        # Get user's album management info
        user_album = db.query(UserAlbumManagement).filter(
            and_(
                UserAlbumManagement.user_id == current_user.id,
                UserAlbumManagement.album_id == album_id
            )
        ).first()

        if user_album:
            user_album.increment_view()
            db.commit()

        # Add collection status to album data
        album_data["in_collection"] = user_album is not None
        album_data["is_favorite"] = user_album.is_favorite if user_album else False

        context = {
            "request": request,
            "album": album_data,
            "user": current_user,
            "permissions": get_user_permissions(current_user)
        }

        # Final check before rendering
        logger.info(f"========== FINAL CHECK: album_data['tracks'] count: {len(album_data['tracks'])} ==========")
        logger.info(f"First track visibility_status: {album_data['tracks'][0].get('visibility_status') if album_data['tracks'] else 'NO TRACKS'}")
        logger.info(f"Rendering album view: album_detail.html with {len(album_data['tracks'])} tracks")
        return templates.TemplateResponse("album_detail.html", context)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error viewing album: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error loading album details")
@app.get("/api/collection/data")
async def get_collection_data(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """
    Get collection page configuration for SPA
    Returns all necessary config without albums (albums loaded separately)
    """
    try:
        # Get creator ID
        creator_id = current_user.id if current_user.is_creator else current_user.created_by
        
        # Get available tiers
        available_tiers = []
        if current_user.is_creator or current_user.is_team:
            tiers = db.query(CampaignTier).filter(
                and_(
                    CampaignTier.creator_id == creator_id,
                    CampaignTier.is_active == True
                )
            ).order_by(CampaignTier.amount_cents).all()
            
            available_tiers = [
                {
                    "id": tier.id,
                    "uuid": tier.uuid,
                    "title": tier.title,
                    "amount_cents": tier.amount_cents,
                    "patron_count": tier.patron_count,
                    "description": tier.description,
                    "album_downloads_allowed": tier.album_downloads_allowed,
                    "track_downloads_allowed": tier.track_downloads_allowed
                }
                for tier in tiers
            ]
        
        # Get download info - FIX: Use "unlimited" string instead of infinity
        download_info = None
        if current_user.is_creator:
            download_info = {
                "albums": {
                    "downloads_allowed": "unlimited",  # Changed from float('inf')
                    "downloads_used": 0, 
                    "downloads_remaining": "unlimited"  # Changed from float('inf')
                },
                "tracks": {
                    "downloads_allowed": "unlimited",  # Changed from float('inf')
                    "downloads_used": 0, 
                    "downloads_remaining": "unlimited"  # Changed from float('inf')
                }
            }
        elif current_user.is_team or current_user.is_patreon or current_user.is_kofi:
            download_info = await get_user_downloads(current_user, db)
        
        return {
            "success": True,
            "config": {
                "permissions": get_user_permissions(current_user),
                "available_tiers": available_tiers,
                "user": {
                    "id": current_user.id,
                    "username": current_user.username,
                    "is_creator": current_user.is_creator,
                    "is_team": current_user.is_team,
                    "download_info": download_info
                }
            }
        }
        
    except Exception as e:
        logger.error(f"Error loading collection data: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error loading collection data")

@app.get("/api/albums/{album_id}/check-access")
async def check_album_access(
    album_id: str,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    try:
        # Get the album
        album = db.query(Album).filter(Album.id == album_id).first()
        if not album:
            raise HTTPException(status_code=404, detail="Album not found")
            
        # Creators and team members always have access
        if current_user.is_creator or current_user.is_team:
            logger.info(f"Creator/team access granted to album {album_id} for {current_user.email}")
            return JSONResponse({"status": "ok", "has_access": True, "reason": "creator_access"})
        
        # Check if album has tier restrictions
        restrictions = album.tier_restrictions or {}
        
        # Check if is_restricted is explicitly True
        is_restricted = restrictions.get("is_restricted")
        if is_restricted is not True:  # Only restrict if explicitly True
            logger.info(f"Album {album_id} is not restricted - granted access to {current_user.email}")
            return JSONResponse({"status": "ok", "has_access": True, "reason": "public_access"})
        
        # At this point, we know the album is restricted
        logger.info(f"Album {album_id} is restricted - checking tier criteria for {current_user.email}")
        
        # Get required tier name for message
        tier_message = "a higher tier subscription"
        required_tier = restrictions.get("minimum_tier", "").strip()
        if required_tier:
            tier_message = f"the {required_tier} tier or above"
        
        # Get user's tier data
        tier_data = current_user.patreon_tier_data or {}
        
        # Get user amount and required amount
        user_amount = tier_data.get("amount_cents", 0)
        
        # Check both field names for compatibility
        required_amount = restrictions.get("minimum_tier_amount", 0)
        if required_amount == 0:  # Try the other field name if first one is zero
            required_amount = restrictions.get("minimum_cents", 0)
            
        logger.info(f"Access check: User amount={user_amount}, Required amount={required_amount}")
        
        # Check if user meets the amount criteria
        if user_amount >= required_amount:
            logger.info(f"User {current_user.email} meets tier amount criteria - granted access")
            return JSONResponse({"status": "ok", "has_access": True, "reason": "tier_access"})
        
        # Special case for Ko-fi users with donations
        if current_user.is_kofi and tier_data.get('has_donations', False):
            donation_amount = tier_data.get('donation_amount_cents', 0)
            total_amount = user_amount + donation_amount
            
            if total_amount >= required_amount:
                logger.info(f"Ko-fi user {current_user.email} meets criteria with donations - granted access")
                return JSONResponse({"status": "ok", "has_access": True, "reason": "kofi_donation_access"})
        
        # Access denied
        logger.info(f"User {current_user.email} does not meet tier criteria - denied access")
        return JSONResponse({
            "error": {
                "type": "tier_restricted",
                "message": f"This content requires {tier_message}"
            }
        }, status_code=403)
            
    except Exception as e:
        logger.error(f"Error checking album access: {str(e)}")
        raise HTTPException(status_code=500, detail="Error checking album access")


@app.post("/api/albums/")
async def create_album(
    title: str = Form(...),
    cover: UploadFile = File(...),
    tier_data: str = Form(None),
    visibility_status: str = Form("visible"),
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db),
    album_service: AlbumService = Depends(get_album_service)
):
    try:
        creator_id = current_user.id if current_user.is_creator else current_user.created_by
        
        # Validate and upload cover
        file_ext = Path(cover.filename).suffix.lower()
        if file_ext not in ['.jpg', '.jpeg', '.png']:
            raise HTTPException(status_code=400, detail="Invalid file type")

        # Upload cover to cloud storage, passing db
        cover_url, _ = await storage.upload_media(
            file=cover,
            media_type="image",
            creator_id=creator_id,
            db=db  # Add this parameter
        )

        # Process tier data
        tier_info = json.loads(tier_data) if tier_data else None

        # Validate visibility_status based on user role
        valid_statuses = ["visible", "hidden_from_users", "hidden_from_all"]
        if visibility_status not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid visibility_status. Must be one of: {', '.join(valid_statuses)}")

        # Team members cannot hide from team or all - only from users
        if current_user.is_team and not current_user.is_creator:
            if visibility_status == "hidden_from_all":
                raise HTTPException(status_code=403, detail="Team members cannot hide content from team. Only 'visible' or 'hidden_from_users' allowed.")

        # Create album using service
        album = await album_service.create_album(
            title=title,
            cover_path=cover_url,
            creator_id=creator_id,
            tier_data=tier_info,
            visibility_status=visibility_status
        )

        return album.to_dict()
        
    except Exception as e:
        logger.error(f"Error creating album: {str(e)}")
        raise HTTPException(status_code=500, detail="Error creating album")



@app.post("/api/albums/{album_id}/tracks/reorder")
async def reorder_tracks(
    album_id: UUID,
    track_orders: dict,
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Update track order within an album"""
    try:
        # Verify album exists and user has permission
        album = db.query(Album).filter(Album.id == album_id).first()
        if not album:
            raise HTTPException(status_code=404, detail="Album not found")

        creator_id = current_user.id if current_user.is_creator else current_user.created_by
        if album.created_by_id != creator_id:
            raise HTTPException(status_code=403, detail="Not authorized")

        # Store album title for logging
        album_title = album.title

        # ✅ Update track orders AND build new ordered list
        ordered_track_ids = []

        for track_order in track_orders['tracks']:
            track = db.query(Track).filter(
                Track.id == track_order['id'],
                Track.album_id == album_id
            ).first()

            if track:
                track.order = track_order['order']
                ordered_track_ids.append((track_order['order'], track_order['id']))

        # ✅ Sort by order and update album's ordered_track_ids
        ordered_track_ids.sort(key=lambda x: x[0])
        album.ordered_track_ids = [track_id for _, track_id in ordered_track_ids]

        # ✅ Update album's updated_at timestamp
        album.updated_at = datetime.now(timezone.utc)

        db.commit()

        logger.info(f"✅ Reordered {len(ordered_track_ids)} tracks in album {album_id}")
        logger.info(f"✅ New order: {album.ordered_track_ids}")

        # Log activity after successful reorder
        try:
            from activity_logs_router import log_activity_isolated
            from models import AuditLogType

            await log_activity_isolated(
                user_id=current_user.id,
                action_type=AuditLogType.UPDATE,
                table_name='albums',
                record_id=str(album_id),
                description=f"Reordered {len(ordered_track_ids)} tracks in album '{album_title}'",
                ip_address=request.client.host if hasattr(request, 'client') else None
            )
        except Exception as e:
            logger.warning(f"Failed to log track reorder activity: {e}")

        return {
            "status": "success",
            "message": "Track order updated",
            "ordered_track_ids": album.ordered_track_ids
        }

    except Exception as e:
        db.rollback()
        logger.error(f"Error reordering tracks: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


        
@app.post("/api/albums/bulk-tier-update")
async def bulk_update_tiers(
    request: Request,
    data: dict,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db),
    album_service: AlbumService = Depends(get_album_service)
):
    """Bulk update tier restrictions for multiple albums"""
    if not current_user.is_creator and not current_user.is_team:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    try:
        creator_id = current_user.id if current_user.is_creator else current_user.created_by
        
        result = await album_service.bulk_update_tiers(
            album_ids=data.get('album_ids', []),
            tier_data=data.get('tier_data', {}),
            creator_id=creator_id
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Error in bulk tier update: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
               
def update_media_paths():
    """Update existing paths to new media structure"""
    try:
        albums_dir = BASE_DIR / "data" / "albums"
        if not albums_dir.exists():
            print("Albums directory not found")
            return
            
        for album_file in albums_dir.glob("albums_*.json"):
            print(f"Processing {album_file}")
            modified = False
            
            try:
                with open(album_file) as f:
                    albums = json.load(f)
                
                for album in albums:
                    # Update cover path
                    if "cover_path" in album:
                        if "/static/covers/" in album["cover_path"]:
                            album["cover_path"] = album["cover_path"].replace("/static/covers/", "/media/images/")
                        elif "/static/" in album["cover_path"]:
                            album["cover_path"] = album["cover_path"].replace("/static/", "/media/images/")
                        modified = True
                    
                    # Update track paths
                    for track in album.get("tracks", []):
                        if "file_path" in track:
                            if "/static/audio/" in track["file_path"]:
                                track["file_path"] = track["file_path"].replace("/static/audio/", "/media/audio/")
                            elif "/static/" in track["file_path"]:
                                track["file_path"] = track["file_path"].replace("/static/", "/media/audio/")
                            modified = True
                
                if modified:
                    with open(album_file, "w") as f:
                        json.dump(albums, f, indent=2)
                    print(f"Updated paths in {album_file}")
                
            except Exception as e:
                print(f"Error processing {album_file}: {str(e)}")
                
    except Exception as e:
        print(f"Error updating media paths: {str(e)}")


@app.patch("/api/albums/{album_id}/tracks/{track_id}")
async def rename_track(
    album_id: UUID,
    track_id: str,  # Changed from UUID to str to match Track.id type
    rename_data: TrackRename,
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Rename a track within an album"""
    try:
        # Get creator ID
        creator_id = current_user.id if current_user.is_creator else current_user.created_by

        # Fetch the track with album ID check
        track = db.query(Track).filter(
            Track.id == track_id,
            Track.album_id == album_id
        ).first()

        if not track:
            logger.warning(f"Track not found: {track_id} in Album: {album_id}")
            raise HTTPException(status_code=404, detail="Track not found")

        # Verify album belongs to creator
        album = db.query(Album).filter(
            Album.id == album_id,
            Album.created_by_id == creator_id
        ).first()

        if not album:
            logger.warning(f"Album not authorized for User: {current_user.id}")
            raise HTTPException(status_code=403, detail="Not authorized to modify this album")

        # Check if at least one field is provided
        if rename_data.title is None and rename_data.visibility_status is None:
            raise HTTPException(status_code=400, detail="At least one field (title or visibility_status) must be provided")

        # Store old values for logging
        old_title = track.title
        old_visibility = track.visibility_status
        changes = []

        # Update track title if provided
        if rename_data.title is not None:
            new_title = rename_data.title.strip()
            if not new_title:
                logger.warning("Attempted to rename track with empty title")
                raise HTTPException(status_code=400, detail="Track title cannot be empty")

            if new_title != track.title:
                track.title = new_title
                changes.append(f"title: '{old_title}' → '{new_title}'")

        # Update visibility_status if provided
        if rename_data.visibility_status is not None:
            # Validate visibility value
            valid_statuses = ["visible", "hidden_from_users", "hidden_from_all"]
            if rename_data.visibility_status not in valid_statuses:
                raise HTTPException(status_code=400, detail=f"Invalid visibility_status. Must be one of: {', '.join(valid_statuses)}")

            # Team members cannot hide from team or all - only from users
            if current_user.is_team and not current_user.is_creator:
                if rename_data.visibility_status == "hidden_from_all":
                    raise HTTPException(status_code=403, detail="Team members cannot hide content from team. Only 'visible' or 'hidden_from_users' allowed.")

            if rename_data.visibility_status != track.visibility_status:
                track.visibility_status = rename_data.visibility_status
                changes.append(f"visibility: '{old_visibility}' → '{rename_data.visibility_status}'")

        track.updated_at = datetime.now(timezone.utc)

        # Update album's updated_at timestamp
        album.updated_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(track)

        logger.info(f"Track updated successfully: {track_id} - {', '.join(changes)} by User: {current_user.id}")

        # Log activity after successful update
        if changes:
            try:
                from activity_logs_router import log_activity_isolated
                from models import AuditLogType

                description = f"Updated track '{track.title}': {', '.join(changes)}"

                # Build old_values and new_values based on what changed
                old_vals = {}
                new_vals = {}
                if rename_data.title is not None and rename_data.title.strip() != old_title:
                    old_vals["title"] = old_title
                    new_vals["title"] = track.title
                if rename_data.visibility_status is not None and rename_data.visibility_status != old_visibility:
                    old_vals["visibility_status"] = old_visibility
                    new_vals["visibility_status"] = track.visibility_status

                await log_activity_isolated(
                    user_id=current_user.id,
                    action_type=AuditLogType.UPDATE,
                    table_name='tracks',
                    record_id=track_id,
                    description=description,
                    old_values=old_vals if old_vals else None,
                    new_values=new_vals if new_vals else None,
                    ip_address=request.client.host if hasattr(request, 'client') else None
                )
            except Exception as e:
                logger.warning(f"Failed to log track update activity: {e}")

        return {
            "status": "success",
            "track": {
                "id": track_id,
                "title": track.title,
                "album_id": str(track.album_id),
                "visibility_status": track.visibility_status,
                "updated_at": track.updated_at.isoformat() if track.updated_at else None
            }
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        db.rollback()
        logger.error(f"Error renaming track: {str(e)} | album_id: {album_id} | track_id: {track_id} | user_id: {current_user.id}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/session/check")
async def check_session(
    request: Request,
    db: Session = Depends(get_db)
):
    """API endpoint to check session validity and limits"""
    try:
        session_id = request.cookies.get("session_id")
        logger.info(f"Checking session: {session_id}")

        if not session_id:
            return JSONResponse(
                status_code=401,
                content={
                    "status": "error",
                    "message": "No session found",
                    "code": "NO_SESSION"
                }
            )

        # Find active session
        session = db.query(UserSession).filter(
            and_(
                UserSession.session_id == session_id,
                UserSession.is_active == True,
                UserSession.expires_at > datetime.now(timezone.utc)
            )
        ).first()

        if not session:
            return JSONResponse(
                status_code=401,
                content={
                    "status": "error",
                    "message": "Session expired",
                    "code": "SESSION_EXPIRED"
                }
            )

        # Check session limits
        limits_check = await session_manager.check_session_limits(
            user_id=session.user_id,
            db=db,
            current_session_id=session_id
        )
        if not limits_check["allowed"]:
            # End this session if we're over limit
            session.is_active = False
            session.ended_at = datetime.now(timezone.utc)
            db.commit()
            
            return JSONResponse(
                status_code=401,
                content={
                    "status": "error",
                    "message": limits_check["reason"],
                    "code": "SESSION_LIMIT_EXCEEDED",
                    "details": {
                        "max_sessions": limits_check["max_sessions"],
                        "active_sessions": limits_check["active_sessions"]
                    }
                }
            )

        # Get user
        user = db.query(User).filter(User.id == session.user_id).first()
        if not user or not user.is_active:
            return JSONResponse(
                status_code=401,
                content={
                    "status": "error",
                    "message": "User not found or inactive",
                    "code": "USER_INVALID"
                }
            )

        # Update session activity
        session.last_active = datetime.now(timezone.utc)
        if user.is_creator:
            session.extend_session(hours=48)
        db.commit()

        # Return successful response with session info
        return JSONResponse({
            "status": "success",
            "session": {
                "id": session_id,
                "expires_at": session.expires_at.isoformat(),
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "role": user.role.value,
                    "is_creator": user.is_creator
                }
            },
            "limits": {
                "max_sessions": limits_check["max_sessions"],
                "active_sessions": limits_check["active_sessions"],
                "warning": limits_check.get("warning")
            }
        })

    except Exception as e:
        logger.error(f"Session check error: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": "Internal server error",
                "code": "SERVER_ERROR"
            }
        )
                

@app.delete("/api/albums/{album_id}")
async def delete_album(
    album_id: UUID,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db),
    album_service: AlbumService = Depends(get_album_service)
):
    """Delete album with individual permission checking for team members"""
    try:
        logger.info(f"🗑️ Album deletion request - User: {current_user.email}, Album: {album_id}")
        logger.info(f"User role: {current_user.role}, is_creator: {current_user.is_creator}, is_team: {current_user.is_team}")
        
        # Get user's tier data for debugging
        tier_data = current_user.patreon_tier_data or {}
        logger.info(f"User tier data: {json.dumps(tier_data, indent=2)}")

        # Permission check
        is_creator = current_user.is_creator or (current_user.role == UserRole.CREATOR)
        is_team = current_user.is_team or (current_user.role == UserRole.TEAM)
        
        if is_creator:
            logger.info(f"✅ Creator {current_user.email} granted album deletion access")
            creator_id = current_user.id
            
        elif is_team:
            logger.info(f"🔍 Checking team member {current_user.email} album deletion permissions...")
            
            # Check INDIVIDUAL album deletion limits for team members
            album_deletions_allowed = tier_data.get('album_deletions_allowed', 0)
            album_deletions_used = tier_data.get('album_deletions_used', 0)
            
            logger.info(f"Team permissions - allowed: {album_deletions_allowed}, used: {album_deletions_used}")
            
            if album_deletions_allowed <= 0:
                logger.warning(f"❌ Team member {current_user.email} has NO album deletion permissions (allowed: {album_deletions_allowed})")
                raise HTTPException(status_code=403, detail="You don't have album deletion permissions")
            
            # Check if deletion period needs reset (24 hours)
            deletion_start = tier_data.get('deletion_period_start')
            now = datetime.now(timezone.utc)
            
            if deletion_start:
                try:
                    start_time = datetime.fromisoformat(deletion_start.replace('Z', '+00:00'))
                    hours_since_start = (now - start_time).total_seconds() / 3600
                    
                    logger.info(f"Deletion period check - started: {deletion_start}, hours ago: {hours_since_start:.1f}")
                    
                    if hours_since_start >= 24:
                        # Reset deletion count for this individual user
                        logger.info(f"🔄 Resetting 24hr deletion period for {current_user.email}")
                        tier_data['album_deletions_used'] = 0
                        tier_data['track_deletions_used'] = 0  # Reset both
                        tier_data['deletion_period_start'] = now.isoformat()
                        current_user.patreon_tier_data = tier_data
                        db.commit()
                        db.refresh(current_user)
                        album_deletions_used = 0
                        logger.info(f"✅ Reset deletion count for team member {current_user.email}")
                except (ValueError, TypeError) as e:
                    # Reset on error
                    logger.warning(f"⚠️ Error parsing deletion period for {current_user.email}: {str(e)} - resetting")
                    tier_data['album_deletions_used'] = 0
                    tier_data['track_deletions_used'] = 0  # Reset both
                    tier_data['deletion_period_start'] = now.isoformat()
                    current_user.patreon_tier_data = tier_data
                    db.commit()
                    db.refresh(current_user)
                    album_deletions_used = 0
            
            # Final permission check
            if album_deletions_used >= album_deletions_allowed:
                try:
                    next_reset = datetime.fromisoformat(deletion_start.replace('Z', '+00:00')) + timedelta(hours=24)
                    next_reset_str = next_reset.strftime('%H:%M UTC')
                except:
                    next_reset_str = "next 24-hour period"
                
                logger.warning(f"❌ Team member {current_user.email} exceeded daily album deletion limit ({album_deletions_used}/{album_deletions_allowed})")
                raise HTTPException(
                    status_code=403, 
                    detail=f"Daily album deletion limit reached ({album_deletions_used}/{album_deletions_allowed}). Resets at {next_reset_str}"
                )
            
            logger.info(f"✅ Team member {current_user.email} has {album_deletions_allowed - album_deletions_used} album deletions remaining")
            creator_id = current_user.created_by
            
        else:
            logger.warning(f"❌ User {current_user.email} attempted album deletion without proper permissions")
            raise HTTPException(status_code=403, detail="Only creators and authorized team members can delete albums")

        logger.info(f"Using creator_id: {creator_id} for album deletion")

        # Perform the album deletion with requesting user context
        result = await album_service.delete_album(
            album_id=str(album_id), 
            creator_id=creator_id, 
            requesting_user=current_user
        )
        
        if not result:
            raise HTTPException(status_code=404, detail="Album not found")

        # AFTER SUCCESSFUL DELETION - INCREMENT INDIVIDUAL USAGE FOR TEAM MEMBERS
        if is_team and not is_creator:
            try:
                tier_data = current_user.patreon_tier_data or {}
                current_used = tier_data.get('album_deletions_used', 0)
                tier_data['album_deletions_used'] = current_used + 1
                
                # Ensure deletion period start is set
                if 'deletion_period_start' not in tier_data:
                    tier_data['deletion_period_start'] = datetime.now(timezone.utc).isoformat()
                
                current_user.patreon_tier_data = tier_data
                db.commit()
                db.refresh(current_user)
                
                remaining = tier_data.get('album_deletions_allowed', 0) - (current_used + 1)
                logger.info(f"📊 Incremented album deletion count for {current_user.email}: {current_used + 1}/{tier_data.get('album_deletions_allowed', 0)} (remaining: {remaining})")
            except Exception as e:
                logger.error(f"❌ Error incrementing album deletion count for {current_user.email}: {str(e)}")

        success_message = "Album deleted successfully"
        if is_team and not is_creator:
            tier_data = current_user.patreon_tier_data or {}
            remaining = max(0, tier_data.get('album_deletions_allowed', 0) - tier_data.get('album_deletions_used', 0))
            success_message += f" ({remaining} album deletions remaining today)"

        logger.info(f"✅ Album {album_id} successfully deleted by {current_user.email}")
        return {
            "status": "success", 
            "message": success_message,
            "deletion_report": result.get("deletion_report", {})
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error deleting album: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# Individual track deletion route


BASE_DIR = Path(os.path.expanduser("~")) / ".hls_streaming"
SEGMENT_DIR = BASE_DIR / "segments"
cleanup_task = None

# Add this after your imports in paste.txt


def _delete_folder_sync(folder_path):
    """Synchronous folder deletion function (for thread executor)"""
    try:
        # Calculate size first
        size_bytes = sum(f.stat().st_size for f in folder_path.rglob('*') if f.is_file())
        size_mb = size_bytes / (1024*1024)
        
        # Delete folder
        try:
            shutil.rmtree(str(folder_path))
            return {"success": True, "size_mb": size_mb, "method": "normal"}
        except Exception:
            # Try force delete
            def handle_readonly(func, path, exc):
                try:
                    os.chmod(path, 0o777)
                    func(path)
                except Exception:
                    pass
            
            shutil.rmtree(str(folder_path), onerror=handle_readonly)
            return {"success": True, "size_mb": size_mb, "method": "force"}
            
    except Exception as e:
        return {"success": False, "error": str(e), "size_mb": 0}

def _scan_directory_sync(hls_dir):
    """Synchronous directory scanning (for thread executor)"""
    try:
        folders = []
        for item in hls_dir.iterdir():
            if item.is_dir():
                folder_name = item.name
                # Only include UUID-format folders
                if len(folder_name) == 36 and folder_name.count('-') == 4:
                    folders.append({"path": item, "name": folder_name})
        return {"success": True, "folders": folders}
    except Exception as e:
        return {"success": False, "error": str(e), "folders": []}

async def cleanup_orphaned_folders():
    """NON-BLOCKING orphan cleanup - won't freeze your app"""
    try:
        logger.info("🧹 Starting NON-BLOCKING orphan cleanup...")
        logger.info(f"🧹 HLS directory: {SEGMENT_DIR}")
        
        if not SEGMENT_DIR.exists():
            logger.warning(f"🧹 HLS directory not found: {SEGMENT_DIR}")
            return
        
        # STEP 1: Get valid IDs from database (non-blocking)
        logger.info("🧹 STEP 1: Getting valid IDs from database...")
        
        valid_ids = set()
        try:
            # Run database queries in thread executor to avoid blocking
            loop = asyncio.get_event_loop()
            
            def get_valid_ids():
                db = next(get_db())
                try:
                    track_result = db.execute(text("SELECT id FROM tracks"))
                    track_ids = {str(row[0]) for row in track_result.fetchall()}
                    
                    album_result = db.execute(text("SELECT id FROM albums"))
                    album_ids = {str(row[0]) for row in album_result.fetchall()}
                    
                    return track_ids | album_ids
                finally:
                    db.close()
            
            valid_ids = await loop.run_in_executor(None, get_valid_ids)
            logger.info(f"🧹   Found {len(valid_ids)} valid IDs in database")
            
        except Exception as db_error:
            logger.error(f"🧹 Database error: {db_error}")
            return
        
        if not valid_ids:
            logger.warning("🧹 No valid IDs found in database")
            return
        
        # Yield control to other tasks
        await asyncio.sleep(0)
        
        # STEP 2: Scan HLS directory (non-blocking)
        logger.info("🧹 STEP 2: Scanning HLS directory...")
        
        loop = asyncio.get_event_loop()
        scan_result = await loop.run_in_executor(None, _scan_directory_sync, SEGMENT_DIR)
        
        if not scan_result["success"]:
            logger.error(f"🧹 Directory scan failed: {scan_result['error']}")
            return
        
        all_folders = scan_result["folders"]
        logger.info(f"🧹   Found {len(all_folders)} UUID-format folders")
        
        # Yield control
        await asyncio.sleep(0)
        
        # STEP 3: Identify orphaned folders
        logger.info("🧹 STEP 3: Identifying orphaned folders...")
        
        orphaned_folders = []
        valid_folders = []
        
        for folder_info in all_folders:
            folder_name = folder_info["name"]
            
            if folder_name in valid_ids:
                valid_folders.append(folder_name)
            else:
                orphaned_folders.append(folder_info)
                logger.info(f"🧹   🗑️  ORPHANED: {folder_name}")
            
            # Yield control every 10 folders to keep app responsive
            if len(orphaned_folders + valid_folders) % 10 == 0:
                await asyncio.sleep(0)
        
        logger.info(f"🧹   Valid folders: {len(valid_folders)}")
        logger.info(f"🧹   Orphaned folders: {len(orphaned_folders)}")
        
        if not orphaned_folders:
            logger.info("🧹 ✅ No orphaned folders found")
            return
        
        # STEP 4: Delete orphaned folders (non-blocking, in batches)
        logger.info(f"🧹 STEP 4: Deleting {len(orphaned_folders)} orphaned folders...")
        
        deleted_count = 0
        failed_count = 0
        total_space_mb = 0
        
        # Process in batches to avoid blocking for too long
        batch_size = 3  # Process 3 folders at a time
        
        for i in range(0, len(orphaned_folders), batch_size):
            batch = orphaned_folders[i:i + batch_size]
            
            logger.info(f"🧹   Processing batch {i//batch_size + 1}/{(len(orphaned_folders) + batch_size - 1)//batch_size}")
            
            # Delete batch in parallel using thread executor
            loop = asyncio.get_event_loop()
            
            with ThreadPoolExecutor(max_workers=batch_size) as executor:
                # Submit all deletions in the batch
                futures = []
                for folder_info in batch:
                    future = loop.run_in_executor(executor, _delete_folder_sync, folder_info["path"])
                    futures.append((folder_info["name"], future))
                
                # Wait for batch to complete
                for folder_name, future in futures:
                    try:
                        result = await future
                        
                        if result["success"]:
                            deleted_count += 1
                            total_space_mb += result["size_mb"]
                            method = result.get("method", "normal")
                            logger.info(f"🧹     ✅ Deleted: {folder_name} ({result['size_mb']:.2f} MB) [{method}]")
                        else:
                            failed_count += 1
                            logger.error(f"🧹     ❌ Failed: {folder_name} - {result['error']}")
                            
                    except Exception as e:
                        failed_count += 1
                        logger.error(f"🧹     ❌ Error deleting {folder_name}: {e}")
            
            # Yield control between batches to keep app responsive
            await asyncio.sleep(0.1)  # Small pause between batches
        
        # STEP 5: Final summary
        logger.info(f"🧹 NON-BLOCKING CLEANUP COMPLETE:")
        logger.info(f"🧹   Orphaned folders found: {len(orphaned_folders)}")
        logger.info(f"🧹   Successfully deleted: {deleted_count}")
        logger.info(f"🧹   Failed to delete: {failed_count}")
        logger.info(f"🧹   Space freed: {total_space_mb:.2f} MB")
        
    except Exception as e:
        logger.error(f"🧹 Non-blocking cleanup error: {e}")

# Non-blocking periodic cleanup
async def periodic_orphan_cleanup():
    """Non-blocking periodic cleanup - runs every 6 hours"""
    while True:
        try:
            await asyncio.sleep(6 * 3600)  # 6 hours
            
            logger.info("🧹 Starting scheduled non-blocking orphan cleanup...")
            await cleanup_orphaned_folders()
            logger.info("🧹 Scheduled cleanup completed")
            
        except asyncio.CancelledError:
            logger.info("🧹 Periodic cleanup cancelled")
            break
        except Exception as e:
            logger.error(f"🧹 Periodic cleanup error: {e}")

@app.get("/api/albums/{album_id}/plays-count")
async def get_album_plays_count(
    album_id: str,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get the total play count for an album"""
    try:
        total_plays = db.query(func.sum(TrackPlays.play_count)).join(
            Track, TrackPlays.track_id == Track.id
        ).filter(
            Track.album_id == album_id
        ).scalar() or 0

        return {"total_plays": int(total_plays)}
    except Exception as e:
        logger.error(f"Error fetching album plays count: {str(e)}")
        return {"total_plays": 0}

@app.delete("/api/albums/{album_id}/tracks/{track_id}")
async def delete_track(
    album_id: str,
    track_id: str,
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Delete a single track with complete cleanup and individual permission checking"""
    cleanup_lock = asyncio.Lock()
    
    async with cleanup_lock:
        try:
            # INDIVIDUAL PERMISSION CHECK
            if not current_user.is_creator:
                if not current_user.is_team:
                    raise HTTPException(status_code=403, detail="Only creators and authorized team members can delete tracks")
                
                # Check INDIVIDUAL deletion limits for team members
                tier_data = current_user.patreon_tier_data or {}
                deletions_allowed = tier_data.get('track_deletions_allowed', 0)
                deletions_used = tier_data.get('track_deletions_used', 0)
                
                if deletions_allowed <= 0:
                    raise HTTPException(status_code=403, detail="You don't have track deletion permissions")
                
                # Check if deletion period needs reset (24 hours)
                deletion_start = tier_data.get('deletion_period_start')
                if deletion_start:
                    try:
                        start_time = datetime.fromisoformat(deletion_start.replace('Z', '+00:00'))
                        if datetime.now(timezone.utc) >= start_time + timedelta(hours=24):
                            # Reset deletion count for this individual user
                            tier_data['track_deletions_used'] = 0
                            tier_data['deletion_period_start'] = datetime.now(timezone.utc).isoformat()
                            current_user.patreon_tier_data = tier_data
                            db.commit()
                            db.refresh(current_user)
                            deletions_used = 0
                            logger.info(f"Reset deletion count for team member {current_user.email}")
                    except (ValueError, TypeError):
                        # Reset on error
                        tier_data['track_deletions_used'] = 0
                        tier_data['deletion_period_start'] = datetime.now(timezone.utc).isoformat()
                        current_user.patreon_tier_data = tier_data
                        db.commit()
                        db.refresh(current_user)
                        deletions_used = 0
                        logger.warning(f"Error parsing deletion period for {current_user.email}, resetting")
                
                if deletions_used >= deletions_allowed:
                    try:
                        next_reset = datetime.fromisoformat(deletion_start.replace('Z', '+00:00')) + timedelta(hours=24)
                        next_reset_str = next_reset.strftime('%H:%M UTC')
                    except:
                        next_reset_str = "next 24-hour period"
                    
                    raise HTTPException(
                        status_code=403, 
                        detail=f"Daily track deletion limit reached ({deletions_used}/{deletions_allowed}). Resets at {next_reset_str}"
                    )

            # Get track data before deletion
            track = db.query(Track).filter(
                Track.id == track_id,
                Track.album_id == album_id
            ).first()

            if not track:
                raise HTTPException(status_code=404, detail="Track not found")

            # Verify album ownership
            album = db.query(Album).filter(Album.id == album_id).first()
            if not album:
                raise HTTPException(status_code=404, detail="Album not found")
            
            # Check if user has access to this album (creator or team member of the creator)
            if current_user.is_creator:
                if album.created_by_id != current_user.id:
                    raise HTTPException(status_code=403, detail="Not authorized to modify this album")
            elif current_user.is_team:
                if album.created_by_id != current_user.created_by:
                    raise HTTPException(status_code=403, detail="Not authorized to modify this album")

            deletion_report = {
                "track_id": track_id,
                "steps_completed": [],
                "errors": []
            }

            # 1. Clean up HLS segments
            try:
                cleanup_result = await stream_manager.cleanup_stream(track_id, db=db)
                if cleanup_result.get("segments_removed"):
                    deletion_report["steps_completed"].append("HLS segments cleanup")
                if cleanup_result.get("errors"):
                    deletion_report["errors"].extend(cleanup_result["errors"])
            except Exception as e:
                deletion_report["errors"].append(f"HLS cleanup error: {str(e)}")

            # 2. Clear duration cache
            try:
                await duration_manager.clear_duration(track_id)
                deletion_report["steps_completed"].append("Duration cache cleanup")
            except Exception as e:
                deletion_report["errors"].append(f"Duration cache error: {str(e)}")

            # 3. Clean up playback progress
            try:
                progress_count = db.query(PlaybackProgress).filter(
                    PlaybackProgress.track_id == track_id
                ).delete(synchronize_session=False)
                deletion_report["steps_completed"].append(f"Deleted {progress_count} progress records")
            except Exception as e:
                deletion_report["errors"].append(f"Progress cleanup error: {str(e)}")

            # 4. Delete user downloads using raw SQL with proper enum casting
            try:
                download_delete_query = text("""
                    DELETE FROM user_downloads 
                    WHERE track_id = :track_id 
                    AND download_type = 'track'::downloadtype
                """)
                download_count = db.execute(download_delete_query, {"track_id": track_id}).rowcount
                deletion_report["steps_completed"].append(f"Deleted {download_count} download records")
            except Exception as e:
                deletion_report["errors"].append(f"Downloads cleanup error: {str(e)}")

            # 5. Delete from storage if file exists - ENHANCED FOR TTS MULTI-VOICE DELETION
            if track.file_path:
                try:
                    track_type = getattr(track, 'track_type', 'audio')
                    
                    if track_type == 'tts':
                        # ✅ TTS TRACK: Delete ALL voices for this track
                        logger.info(f"🎤 TTS track deletion: deleting ALL voices for track {track_id}")
                        
                        await storage.delete_all_tts_voices_for_track(
                            track_id=track_id,
                            track=track,
                            db=db
                        )
                        if deletion_success:
                            deletion_report["steps_completed"].append("TTS multi-voice S4 storage cleanup")
                        else:
                            deletion_report["errors"].append("TTS voice files deletion attempted (may not have existed)")
                    else:
                        # ✅ REGULAR AUDIO: Use existing logic
                        logger.info(f"🎵 Regular audio deletion: using file_path: {track.file_path}")
                        await storage.delete_media(track.file_path)
                        deletion_report["steps_completed"].append("Storage cleanup")
                        
                except Exception as e:
                    deletion_report["errors"].append(f"Storage cleanup error: {str(e)}")
                    
            # 6. Delete all comments and related data for this track
            try:
                # First get all comment IDs for this track
                comment_ids = [comment.id for comment in db.query(Comment.id).filter(Comment.track_id == track_id).all()]
                
                # Delete comment likes for these comments
                if comment_ids:
                    like_count = db.query(CommentLike).filter(
                        CommentLike.comment_id.in_(comment_ids)
                    ).delete(synchronize_session=False)
                    
                    # Delete comment reports - using raw SQL to avoid schema mismatch
                    if comment_ids:
                        report_delete_query = text("""
                            DELETE FROM comment_reports 
                            WHERE comment_id IN :comment_ids
                        """)
                        report_count = db.execute(report_delete_query, {"comment_ids": tuple(comment_ids) if len(comment_ids) > 1 else (comment_ids[0],)}).rowcount
                
                # Delete the comments themselves
                comment_count = db.query(Comment).filter(
                    Comment.track_id == track_id
                ).delete(synchronize_session=False)
                
                deletion_report["steps_completed"].append(f"Deleted {comment_count} comments and related data")
            except Exception as e:
                deletion_report["errors"].append(f"Comments cleanup error: {str(e)}")
                
            # 7. Delete track from database
            # Store track info for activity logging before deletion
            track_title = track.title
            track_type = getattr(track, 'track_type', 'audio')
            voice_id = getattr(track, 'voice_id', None) if track_type == 'tts' else None

            try:
                db.delete(track)
                db.commit()
                deletion_report["steps_completed"].append("Database cleanup")
                logger.info(f"Successfully deleted track {track_id} from database")

                # ✅ Invalidate authorization grants when track is deleted
                try:
                    from authorization_service import invalidate_on_content_change
                    await invalidate_on_content_change(track_id)
                except Exception as e:
                    logger.warning(f"Failed to invalidate grants for deleted track {track_id}: {e}")
            except Exception as e:
                db.rollback()
                deletion_report["errors"].append(f"Database error: {str(e)}")
                logger.error(f"Database deletion failed for track {track_id}: {str(e)}")
                raise

            # 7b. Log activity after successful deletion
            try:
                from activity_logs_router import log_activity_isolated
                from models import AuditLogType

                description = f"Deleted track '{track_title}'"
                if track_type == 'tts' and voice_id:
                    description += f" (TTS, voice_id: {voice_id})"

                await log_activity_isolated(
                    user_id=current_user.id,
                    action_type=AuditLogType.DELETE,
                    table_name='tracks',
                    record_id=track_id,
                    description=description,
                    ip_address=request.client.host if hasattr(request, 'client') else None
                )
            except Exception as e:
                logger.warning(f"Failed to log track deletion activity: {e}")

            # 8. AFTER SUCCESSFUL DELETION - INCREMENT INDIVIDUAL USAGE FOR TEAM MEMBERS
            if current_user.is_team and not current_user.is_creator:
                try:
                    tier_data = current_user.patreon_tier_data or {}
                    current_used = tier_data.get('track_deletions_used', 0)
                    tier_data['track_deletions_used'] = current_used + 1
                    
                    # Ensure deletion period start is set
                    if 'deletion_period_start' not in tier_data:
                        tier_data['deletion_period_start'] = datetime.now(timezone.utc).isoformat()
                    
                    current_user.patreon_tier_data = tier_data
                    db.commit()
                    db.refresh(current_user)
                    
                    remaining = tier_data.get('track_deletions_allowed', 0) - (current_used + 1)
                    logger.info(f"Incremented deletion count for {current_user.email}: {current_used + 1}/{tier_data.get('track_deletions_allowed', 0)} (remaining: {remaining})")
                except Exception as e:
                    logger.error(f"Error incrementing deletion count for {current_user.email}: {str(e)}")
                    # Don't fail the deletion if this step fails
                    deletion_report["errors"].append(f"Failed to update deletion count: {str(e)}")

            success_message = "Track deleted successfully"
            if current_user.is_team and not current_user.is_creator:
                remaining = max(0, tier_data.get('track_deletions_allowed', 0) - tier_data.get('track_deletions_used', 0))
                success_message += f" ({remaining} deletions remaining today)"

            return {
                "status": "success",
                "message": success_message,
                "deletion_report": deletion_report
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Track deletion error: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Deletion failed: {str(e)}")


# Bulk deletion route - FIXED VERSION
@app.post("/api/albums/bulk-delete")
async def bulk_delete_tracks(
    tracks: List[Dict[str, str]],
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Bulk delete multiple tracks with complete cleanup and TTS support"""
    
    # INDIVIDUAL PERMISSION CHECK FOR TEAM MEMBERS
    deletions_allowed = float('inf')  # Unlimited for creators
    deletions_used = 0
    tier_data = {}
    
    if not current_user.is_creator:
        if not current_user.is_team:
            raise HTTPException(status_code=403, detail="Only creators and authorized team members can delete tracks")
        
        # Check INDIVIDUAL deletion limits for team members
        tier_data = current_user.patreon_tier_data or {}
        deletions_allowed = tier_data.get('track_deletions_allowed', 0)
        deletions_used = tier_data.get('track_deletions_used', 0)
        
        if deletions_allowed <= 0:
            raise HTTPException(status_code=403, detail="You don't have track deletion permissions")
        
        # Check if deletion period needs reset (24 hours)
        deletion_start = tier_data.get('deletion_period_start')
        if deletion_start:
            try:
                start_time = datetime.fromisoformat(deletion_start.replace('Z', '+00:00'))
                if datetime.now(timezone.utc) >= start_time + timedelta(hours=24):
                    tier_data['track_deletions_used'] = 0
                    tier_data['deletion_period_start'] = datetime.now(timezone.utc).isoformat()
                    current_user.patreon_tier_data = tier_data
                    db.commit()
                    db.refresh(current_user)
                    deletions_used = 0
                    logger.info(f"Reset deletion count for team member {current_user.email}")
            except (ValueError, TypeError):
                tier_data['track_deletions_used'] = 0
                tier_data['deletion_period_start'] = datetime.now(timezone.utc).isoformat()
                current_user.patreon_tier_data = tier_data
                db.commit()
                db.refresh(current_user)
                deletions_used = 0
                logger.warning(f"Error parsing deletion period for {current_user.email}, resetting")
        
        if deletions_used >= deletions_allowed:
            try:
                next_reset = datetime.fromisoformat(deletion_start.replace('Z', '+00:00')) + timedelta(hours=24)
                next_reset_str = next_reset.strftime('%H:%M UTC')
            except:
                next_reset_str = "next 24-hour period"
            
            raise HTTPException(
                status_code=403, 
                detail=f"Daily track deletion limit reached ({deletions_used}/{deletions_allowed}). Resets at {next_reset_str}"
            )

    # VALIDATE: Check if user is trying to delete more tracks than they have remaining
    if current_user.is_team and not current_user.is_creator:
        remaining_deletions = deletions_allowed - deletions_used
        tracks_to_delete = len(tracks)
        
        if tracks_to_delete > remaining_deletions:
            raise HTTPException(
                status_code=403,
                detail=f"Cannot delete {tracks_to_delete} tracks. You have {remaining_deletions} deletion{'s' if remaining_deletions != 1 else ''} remaining today."
            )

    deletion_report = {
        "successful_deletions": [],
        "failed_deletions": [],
        "limit_reached": False,
        "details": {}
    }

    cleanup_lock = asyncio.Lock()
    running_deletion_count = deletions_used

    try:
        for track_info in tracks:
            album_id = track_info.get("album_id")
            track_id = track_info.get("track_id")

            if not album_id or not track_id:
                deletion_report["failed_deletions"].append({
                    "track_id": track_id,
                    "album_id": album_id,
                    "error": "Invalid track information"
                })
                continue

            # Check if team member has reached their limit
            if current_user.is_team and not current_user.is_creator:
                if running_deletion_count >= deletions_allowed:
                    deletion_report["limit_reached"] = True
                    deletion_report["failed_deletions"].append({
                        "track_id": track_id,
                        "album_id": album_id,
                        "error": f"Deletion limit reached ({deletions_allowed} per day)"
                    })
                    continue

            track_report = {
                "completed_steps": [],
                "errors": []
            }

            async with cleanup_lock:
                try:
                    track = db.query(Track).filter(
                        Track.id == track_id,
                        Track.album_id == album_id
                    ).first()

                    if not track:
                        raise ValueError("Track not found")

                    # Verify album ownership
                    album = db.query(Album).filter(Album.id == album_id).first()
                    if not album:
                        raise ValueError("Album not found")
                    
                    # Check if user has access to this album
                    if current_user.is_creator:
                        if album.created_by_id != current_user.id:
                            raise ValueError("Not authorized to modify this album")
                    elif current_user.is_team:
                        if album.created_by_id != current_user.created_by:
                            raise ValueError("Not authorized to modify this album")

                    # 1. HLS segments and playlists cleanup
                    try:
                        cleanup_result = await stream_manager.cleanup_stream(track_id, db=db)
                        if cleanup_result.get("segments_removed"):
                            track_report["completed_steps"].append("HLS segments")
                        if cleanup_result.get("errors"):
                            track_report["errors"].extend(cleanup_result["errors"])
                    except Exception as e:
                        track_report["errors"].append(f"HLS cleanup error: {str(e)}")

                    # 2. Redis cache cleanup
                    try:
                        await duration_manager.clear_duration(track_id)
                        track_report["completed_steps"].append("Redis cache")
                    except Exception as e:
                        track_report["errors"].append(f"Redis cleanup error: {str(e)}")

                    # 3. User downloads cleanup with proper enum casting
                    try:
                        download_delete_query = text("""
                            DELETE FROM user_downloads 
                            WHERE track_id = :track_id 
                            AND download_type = 'track'::downloadtype
                        """)
                        download_count = db.execute(download_delete_query, {"track_id": track_id}).rowcount
                        track_report["completed_steps"].append(f"Deleted {download_count} download records")
                    except Exception as e:
                        track_report["errors"].append(f"Downloads cleanup error: {str(e)}")

                    # 4. Enhanced storage cleanup for TTS support
                    if track.file_path:
                        try:
                            track_type = getattr(track, 'track_type', 'audio')
                            
                            if track_type == 'tts':
                                logger.info(f"🎤 Bulk deletion - TTS track: deleting ALL voices for track {track_id}")
                                
                                await storage.delete_all_tts_voices_for_track(
                                    track_id=track_id,
                                    track=track,
                                    db=db
                                )
                                
                                if tts_deletion_success:
                                    track_report["completed_steps"].append("TTS multi-voice storage")
                                else:
                                    track_report["errors"].append("TTS voice deletion attempted")
                            else:
                                await storage.delete_media(track.file_path)
                                track_report["completed_steps"].append("Storage cleanup")
                                
                        except Exception as e:
                            track_report["errors"].append(f"Storage cleanup error: {str(e)}")

                    # 5. Progress data cleanup
                    try:
                        progress_count = db.query(PlaybackProgress).filter(
                            PlaybackProgress.track_id == track_id
                        ).delete(synchronize_session=False)
                        track_report["completed_steps"].append(f"Deleted {progress_count} progress records")
                    except Exception as e:
                        track_report["errors"].append(f"Progress cleanup error: {str(e)}")

                    # 6. Database deletion
                    # Store track info for activity logging before deletion
                    track_title = track.title
                    track_type_val = getattr(track, 'track_type', 'audio')
                    voice_id_val = getattr(track, 'voice_id', None) if track_type_val == 'tts' else None

                    try:
                        db.delete(track)
                        track_report["completed_steps"].append("Database deletion")

                        # INCREMENT USAGE FOR TEAM MEMBERS AFTER SUCCESSFUL DELETION
                        if current_user.is_team and not current_user.is_creator:
                            running_deletion_count += 1

                    except Exception as e:
                        track_report["errors"].append(f"Database deletion error: {str(e)}")
                        raise

                    # 6b. Log activity after successful deletion
                    try:
                        from activity_logs_router import log_activity_isolated
                        from models import AuditLogType

                        description = f"Deleted track '{track_title}' (bulk delete)"
                        if track_type_val == 'tts' and voice_id_val:
                            description += f" (TTS, voice_id: {voice_id_val})"

                        await log_activity_isolated(
                            user_id=current_user.id,
                            action_type=AuditLogType.DELETE,
                            table_name='tracks',
                            record_id=track_id,
                            description=description,
                            ip_address=request.client.host if hasattr(request, 'client') else None
                        )
                    except Exception as e:
                        logger.warning(f"Failed to log track deletion activity for {track_id}: {e}")

                    if not track_report["errors"]:
                        deletion_report["successful_deletions"].append({
                            "track_id": track_id,
                            "album_id": album_id
                        })
                    else:
                        deletion_report["failed_deletions"].append({
                            "track_id": track_id,
                            "album_id": album_id,
                            "errors": track_report["errors"]
                        })

                    deletion_report["details"][track_id] = track_report

                except Exception as e:
                    logger.error(f"Error deleting track {track_id}: {str(e)}")
                    deletion_report["failed_deletions"].append({
                        "track_id": track_id,
                        "album_id": album_id,
                        "error": str(e)
                    })

        # Final database commit after all operations
        db.commit()

        # UPDATE DELETION COUNT FOR TEAM MEMBERS
        if current_user.is_team and not current_user.is_creator:
            try:
                tier_data = current_user.patreon_tier_data or {}
                tier_data['track_deletions_used'] = running_deletion_count
                
                # Ensure deletion period start is set
                if 'deletion_period_start' not in tier_data:
                    tier_data['deletion_period_start'] = datetime.now(timezone.utc).isoformat()
                
                current_user.patreon_tier_data = tier_data
                db.commit()
                db.refresh(current_user)
                
                logger.info(f"Updated deletion count for {current_user.email}: {running_deletion_count}/{deletions_allowed}")
            except Exception as e:
                logger.error(f"Error updating deletion count for {current_user.email}: {str(e)}")

        response_data = {
            "status": "completed",
            "total_tracks": len(tracks),
            "successful": len(deletion_report["successful_deletions"]),
            "failed": len(deletion_report["failed_deletions"]),
            "deletion_report": deletion_report
        }

        # Add remaining deletions info for team members
        if current_user.is_team and not current_user.is_creator:
            remaining = max(0, deletions_allowed - running_deletion_count)
            response_data["deletions_remaining"] = remaining
            response_data["deletions_used"] = running_deletion_count
            response_data["deletions_allowed"] = deletions_allowed

        return response_data

    except Exception as e:
        db.rollback()
        logger.error(f"Bulk deletion error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/popular-tracks")
async def get_popular_tracks(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get popular albums based on aggregated track plays - uses centralized popular_tracks_service"""
    try:
        from popular_tracks_service import get_popular_albums

        # Get creator ID
        creator_id = current_user.id if current_user.is_creator else current_user.created_by
        logger.info(f"Getting popular tracks for creator: {creator_id}")

        # Use centralized service
        popular_tracks = await get_popular_albums(creator_id, db)

        # Apply visibility filtering based on user role
        if not current_user.is_creator:
            if current_user.is_team:
                # Team members can see all except hidden_from_all
                popular_tracks = [
                    album for album in popular_tracks
                    if album.get('visibility_status') != 'hidden_from_all'
                ]
            else:
                # Regular users (Patreon/Ko-fi/guests) can only see visible albums
                popular_tracks = [
                    album for album in popular_tracks
                    if album.get('visibility_status') == 'visible'
                ]

        logger.info(f"Found {len(popular_tracks)} popular albums after visibility filtering")
        return popular_tracks

    except Exception as e:
        logger.error(f"Error getting popular tracks: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error getting popular tracks")


@app.get("/api/albums/recent-updates")
async def get_updated_albums(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db),
    limit: int = 25
):
    """Get most recently updated albums (based on track additions)"""
    try:
        # Add debug logging
        logger.info(f"Recent Updates API called by user: {current_user.id}")

        # Build query with visibility filtering
        query = (
            db.query(Album)
            .join(Track)
            .filter(
                Album.created_at < func.now() - timedelta(days=7)  # Keep the 7-day filter
            )
        )

        # Apply visibility filtering based on user role
        if not current_user.is_creator:
            if current_user.is_team:
                # Team members can see all except hidden_from_all
                query = query.filter(Album.visibility_status != "hidden_from_all")
            else:
                # Regular users (Patreon/Ko-fi/guests) can only see visible albums
                query = query.filter(Album.visibility_status == "visible")

        albums = (
            query
            .group_by(Album.id)
            .order_by(func.max(Track.created_at).desc())
            .limit(limit)
            .all()
        )
        
        # Add debug logging
        logger.info(f"Recent Updates query returned {len(albums)} albums")
        
        albums_data = []
        for album in albums:
            latest_track = max(album.tracks, key=lambda t: t.created_at) if album.tracks else None
            
            album_data = {
                'id': str(album.id),
                'title': album.title,
                'cover_path': album.cover_path or '/static/images/default-album.jpg',
                'visibility_status': album.visibility_status,
                'track_count': len(album.tracks) if album.tracks else 0,
                'latest_update': latest_track.created_at.isoformat() if latest_track else None,
                'latest_track': {
                    'title': latest_track.title,
                    'created_at': latest_track.created_at.isoformat()
                } if latest_track else None
            }
            albums_data.append(album_data)
        
        # If no data, return default example
        if not albums_data:
            logger.warning("No updated albums found, returning default data")
            return [{
                'id': "default",
                'title': "Example Album",
                'cover_path': '/static/images/default-album.jpg',
                'track_count': 12,
                'latest_update': datetime.now().isoformat(),
                'latest_track': {
                    'title': "Example Track",
                    'created_at': datetime.now().isoformat()
                }
            }]
            
        return albums_data
    except Exception as e:
        logger.error(f"Error getting updated albums: {str(e)}", exc_info=True)
        # Return default data on error
        return [{
            'id': "default-error",
            'title': "Featured Album",
            'cover_path': '/static/images/default-album.jpg',
            'track_count': 10,
            'latest_update': datetime.now().isoformat(),
            'latest_track': {
                'title': "Featured Track",
                'created_at': datetime.now().isoformat()
            }
        }]

@app.get("/api/albums/recent-additions")
async def get_recent_albums(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db),
    limit: int = 25
):
    """Get most recently added albums"""
    try:
        # Add debug logging
        logger.info(f"Recent Additions API called by user: {current_user.id}")

        # Build query with visibility filtering
        query = db.query(Album)

        # Apply visibility filtering based on user role
        if not current_user.is_creator:
            if current_user.is_team:
                # Team members can see all except hidden_from_all
                query = query.filter(Album.visibility_status != "hidden_from_all")
            else:
                # Regular users (Patreon/Ko-fi/guests) can only see visible albums
                query = query.filter(Album.visibility_status == "visible")

        albums = (
            query
            .order_by(desc(Album.created_at))
            .limit(limit)
            .all()
        )
        
        # Add debug logging
        logger.info(f"Recent Additions query returned {len(albums)} albums")
        
        albums_data = []
        for album in albums:
            album_data = {
                'id': str(album.id),
                'title': album.title,
                'cover_path': album.cover_path or '/static/images/default-album.jpg',
                'visibility_status': album.visibility_status,
                'track_count': len(album.tracks) if album.tracks else 0,
                'created_at': album.created_at.isoformat() if album.created_at else None
            }
            albums_data.append(album_data)
        
        # If no data, return default example
        if not albums_data:
            logger.warning("No recent albums found, returning default data")
            return [{
                'id': "default",
                'title': "New Release",
                'cover_path': '/static/images/default-album.jpg',
                'track_count': 8,
                'created_at': datetime.now().isoformat()
            }]
            
        return albums_data
    except Exception as e:
        logger.error(f"Error getting recent albums: {str(e)}", exc_info=True)
        # Return default data on error
        return [{
            'id': "default-error",
            'title': "New Release",
            'cover_path': '/static/images/default-album.jpg',
            'track_count': 8,
            'created_at': datetime.now().isoformat()
        }]

@app.get("/api/albums/")
async def get_albums(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get all albums with proper data structure for API consumption"""
    check_permission(current_user, Permission.VIEW)
    try:
        creator_id = current_user.id if current_user.is_creator else current_user.created_by
        logger.info(f"Fetching albums for creator_id: {creator_id}")
        
        # Get user's saved albums for reference
        user_albums = {
            str(ua.album_id): ua 
            for ua in db.query(UserAlbumManagement).filter(
                UserAlbumManagement.user_id == current_user.id
            ).all()
        }
        
        # Load all creator's albums
        all_albums = load_creator_albums(creator_id, db)
        
        # Process albums with user-specific data and visibility filtering
        processed_albums = []
        for album in all_albums:
            # Apply visibility filtering based on user role
            visibility_status = album.get('visibility_status', 'visible')

            # Skip albums based on visibility rules
            if not current_user.is_creator:
                if current_user.is_team:
                    # Team members can see all except hidden_from_all
                    if visibility_status == "hidden_from_all":
                        continue
                else:
                    # Regular users (Patreon/Ko-fi guests) can only see visible albums
                    if visibility_status != "visible":
                        continue

            # Get user's relationship with this album if it exists
            user_album = user_albums.get(str(album.get('id')))

            album_data = {
                "id": str(album.get('id')),
                "title": album.get('title'),
                "cover_path": album.get('cover_path') or '/media/images/default-album.jpg',
                "created_at": album.get('created_at'),
                "updated_at": album.get('updated_at'),
                "created_by_id": str(album.get('created_by_id')),
                "tier_restrictions": album.get('tier_restrictions', {}),
                "visibility_status": visibility_status,
                "track_count": len(album.get('tracks', [])),
                "in_collection": bool(user_album),

                # Add user-specific data if album is in collection
                "is_favorite": user_album.is_favorite if user_album else False,
                "last_viewed": user_album.last_viewed.isoformat() if user_album and user_album.last_viewed else None,
                "view_count": user_album.view_count if user_album else 0
            }
            processed_albums.append(album_data)

        # Return paginated response
        return {
            "items": processed_albums,
            "total": len(processed_albums),
            "page": 1,
            "per_page": len(processed_albums),
            "pages": 1
        }

    except Exception as e:
        logger.error(f"Error getting albums: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error retrieving albums")


@app.get("/api/albums/{album_id}")
async def get_album(
    album_id: str,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get album details from database with tier access enforcement"""
    try:
        # Get album with tracks using eager loading
        album = db.query(Album).options(
            joinedload(Album.tracks)
        ).filter(Album.id == album_id).first()
        
        if not album:
            raise HTTPException(status_code=404, detail="Album not found")

        # Check user access to creator's content
        creator_id = current_user.id if current_user.is_creator else current_user.created_by
        if album.created_by_id != creator_id:
            raise HTTPException(status_code=403, detail="Not authorized")

        # Add tier restriction check before accessing album content
        if not current_user.is_creator and not current_user.is_team:
            restrictions = album.tier_restrictions
            if restrictions and restrictions.get("is_restricted"):
                if current_user.is_patreon and current_user.patreon_tier_data:
                    # Get user's tier amount and minimum required amount
                    user_amount = current_user.patreon_tier_data.get("amount_cents", 0)
                    minimum_tier_amount = restrictions.get("minimum_cents", 0)
                    minimum_tier = restrictions.get("minimum_tier", "Unknown")
                    
                    if user_amount < minimum_tier_amount:
                        raise HTTPException(
                            status_code=403,
                            detail={
                                "message": f"This album requires {minimum_tier} tier or higher",
                                "type": "error",
                                "title": "Access Restricted",
                                "current_tier": current_user.patreon_tier_data.get("title"),
                                "required_tier": minimum_tier
                            }
                        )
                else:
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "message": "This content requires a Patreon subscription",
                            "type": "error",
                            "title": "Patron-Only Content"
                        }
                    )

        # Get user's collection status - keep existing functionality
        user_album = db.query(UserAlbumManagement).filter(
            and_(
                UserAlbumManagement.user_id == current_user.id,
                UserAlbumManagement.album_id == album_id
            )
        ).first()

        # Apply cascading visibility filtering
        tracks = sorted(album.tracks, key=lambda x: x.order or 0)

        # Check album-level visibility first
        album_visibility = getattr(album, 'visibility_status', 'visible')

        if album_visibility != "visible":
            # Album-level hiding takes precedence
            if album_visibility == "hidden_from_all" and not current_user.is_creator:
                # Hide all tracks from everyone except creator
                tracks = []
            elif album_visibility == "hidden_from_users" and not (current_user.is_creator or current_user.is_team):
                # Hide all tracks from regular users (non-creator, non-team)
                tracks = []
        else:
            # Album is visible, apply track-level filtering
            if not current_user.is_creator:
                if current_user.is_team:
                    # Team members can see all tracks except "hidden_from_all"
                    tracks = [t for t in tracks if getattr(t, 'visibility_status', 'visible') != "hidden_from_all"]
                else:
                    # Regular users can only see "visible" tracks
                    tracks = [t for t in tracks if getattr(t, 'visibility_status', 'visible') == "visible"]

        # Build tracks list with TTS metadata
        tracks_data = []
        for track in tracks:
            track_data = {
                "id": str(track.id),
                "title": track.title,
                "file_path": track.file_path,
                "duration": track.duration,
                "order": track.order,
                "visibility_status": getattr(track, 'visibility_status', 'visible')
            }

            # Add TTS-specific metadata
            track_type = getattr(track, 'track_type', 'audio')
            track_data["track_type"] = track_type

            if track_type == 'tts':
                # Add default voice
                default_voice = getattr(track, 'default_voice', None)
                track_data["default_voice"] = default_voice

                # Add TTS status fields
                track_data["tts_status"] = getattr(track, 'tts_status', None)
                track_data["upload_status"] = getattr(track, 'upload_status', None)
                track_data["status"] = getattr(track, 'status', None)
                track_data["voice_directory"] = getattr(track, 'voice_directory', None)

                # Get generated voices from filesystem
                generated_voices = []
                if stream_manager and hasattr(stream_manager, 'segment_dir'):
                    try:
                        track_id = str(track.id)
                        track_dir = stream_manager.segment_dir / track_id

                        # Check if track directory exists
                        if await asyncio.to_thread(track_dir.exists):
                            # Find all voice-* directories
                            voice_dirs = await asyncio.to_thread(lambda: list(track_dir.glob("voice-*")))
                            for voice_dir in voice_dirs:
                                # Check if master.m3u8 exists
                                master_m3u8 = voice_dir / "master.m3u8"
                                if await asyncio.to_thread(master_m3u8.exists):
                                    voice_id = voice_dir.name.replace("voice-", "")
                                    generated_voices.append(voice_id)
                    except Exception as e:
                        logger.warning(f"Error checking generated voices for track {track.id}: {e}")

                track_data["generated_voices"] = generated_voices

            tracks_data.append(track_data)

        # Keep the existing response format but add tier access info
        response = {
            "id": str(album.id),
            "title": album.title,
            "cover_path": album.cover_path,
            "created_at": album.created_at.isoformat() if album.created_at else None,
            "updated_at": album.updated_at.isoformat() if album.updated_at else None,
            "tier_restrictions": album.tier_restrictions,
            "visibility_status": album_visibility,
            "tracks": tracks_data,
            "in_collection": bool(user_album),
            "is_favorite": user_album.is_favorite if user_album else False,
            "creator_id": album.created_by_id,
            # Add user's tier access information
            "user_access": {
                "has_access": True,  # We'll only reach here if access is granted
                "user_tier": current_user.patreon_tier_data.get("title") if current_user.is_patreon else None,
                "user_amount": current_user.patreon_tier_data.get("amount_cents", 0) if current_user.is_patreon else 0,
                "is_creator": current_user.is_creator,
                "is_team": current_user.is_team
            }
        }

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting album: {str(e)}")
        raise HTTPException(status_code=500, detail="Error retrieving album")        
        
def check_tier_access(user_tier_amount: int, required_tier_amount: int) -> bool:
    """Check if user's tier amount meets or exceeds the required amount"""
    return user_tier_amount >= required_tier_amount
    
    
@app.patch("/api/albums/{album_id}")
async def update_album(
    album_id: UUID,
    title: str = Form(None),
    cover: Optional[UploadFile] = File(None),
    tier_data: str = Form(None),
    visibility_status: str = Form(None),
    request: Request = None,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db),
    album_service: AlbumService = Depends(get_album_service)
):
    """Update album title, cover and tier restrictions"""
    try:
        # Get existing album first
        album = await album_service.get_album(
            album_id=str(album_id),
            user_id=current_user.id,
            check_access=False
        )

        if not album:
            raise HTTPException(status_code=404, detail="Album not found")

        # Verify ownership
        creator_id = current_user.id if current_user.is_creator else current_user.created_by
        if album.created_by_id != creator_id:
            raise HTTPException(status_code=403, detail="Not authorized to modify this album")

        # Track changes for logging
        changes = []
        old_values = {}
        new_values = {}
        old_title = album.title

        if title and title != album.title:
            changes.append(f"title: '{album.title}' → '{title}'")
            old_values["title"] = album.title
            new_values["title"] = title

        # Handle cover upload if provided
        cover_path = None
        if cover:
            file_ext = Path(cover.filename).suffix.lower()
            if file_ext not in ['.jpg', '.jpeg', '.png']:
                raise HTTPException(status_code=400, detail="Invalid file type")

            cover_path = await storage.upload_media(
                file=cover,
                media_type="image",
                creator_id=creator_id
            )
            changes.append("cover updated")
            old_values["cover_path"] = album.cover_path
            new_values["cover_path"] = cover_path

        # Process tier data
        tier_info = json.loads(tier_data) if tier_data else None
        if tier_info:
            changes.append("tier restrictions updated")
            old_values["tier_restrictions"] = album.tier_restrictions
            new_values["tier_restrictions"] = tier_info

        # Validate and track visibility_status changes
        if visibility_status is not None:
            valid_statuses = ["visible", "hidden_from_users", "hidden_from_all"]
            if visibility_status not in valid_statuses:
                raise HTTPException(status_code=400, detail=f"Invalid visibility_status. Must be one of: {', '.join(valid_statuses)}")

            # Team members cannot hide from team or all - only from users
            if current_user.is_team and not current_user.is_creator:
                if visibility_status == "hidden_from_all":
                    raise HTTPException(status_code=403, detail="Team members cannot hide content from team. Only 'visible' or 'hidden_from_users' allowed.")
            if visibility_status != album.visibility_status:
                changes.append(f"visibility: '{album.visibility_status}' → '{visibility_status}'")
                old_values["visibility_status"] = album.visibility_status
                new_values["visibility_status"] = visibility_status

        # Update album using service
        updated_album = await album_service.update_album(
            album_id=str(album_id),
            title=title,
            cover_path=cover_path,
            tier_data=tier_info,
            visibility_status=visibility_status,
            creator_id=creator_id
        )

        # Log activity after successful update
        if changes:
            try:
                from activity_logs_router import log_activity_isolated
                from models import AuditLogType

                description = f"Updated album '{old_title}': {', '.join(changes)}"

                await log_activity_isolated(
                    user_id=current_user.id,
                    action_type=AuditLogType.UPDATE,
                    table_name='albums',
                    record_id=str(album_id),
                    description=description,
                    old_values=old_values if old_values else None,
                    new_values=new_values if new_values else None,
                    ip_address=request.client.host if request and hasattr(request, 'client') else None
                )
            except Exception as e:
                logger.warning(f"Failed to log album update activity: {e}")

        return updated_album.to_dict()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating album: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Utility Functions

def get_creator_albums_file(creator_id: int) -> Path:
    """Get the path to a creator's albums JSON file"""
    albums_dir = BASE_DIR / "data" / "albums"
    albums_dir.mkdir(parents=True, exist_ok=True)
    print(f"Looking for albums file: albums_{creator_id}.json")
    return albums_dir / f"albums_{creator_id}.json"


def load_creator_albums(user_id: int, db: Session = None) -> list:
    """Load albums for a creator, team member, or patron"""
    try:
        creator_id = user_id
        if db:
            # Get the user
            user = db.query(User).filter(User.id == user_id).first()
            if user:
                if user.is_team or user.is_patreon:
                    if user.created_by:
                        creator_id = user.created_by
                        print(f"Loading albums for creator: {creator_id} (accessed by {user.role} user {user.id})")
                    else:
                        print(f"Warning: {user.role} user {user.id} has no creator_id")
                        return []

        albums_file = get_creator_albums_file(creator_id)
        print(f"Loading albums from: {albums_file}")

        if not albums_file.exists():
            print(f"No albums file found for creator {creator_id}")
            return []

        with albums_file.open("r") as f:
            albums = json.load(f)
            if not isinstance(albums, list):
                print("Warning: Albums data is not a list")
                return []

            # Ensure all albums have creator_id
            for album in albums:
                if "creator_id" not in album:
                    album["creator_id"] = creator_id
                if "tracks" not in album:
                    album["tracks"] = []

            print(f"Successfully loaded {len(albums)} albums for creator {creator_id}")
            return albums

    except Exception as e:
        print(f"Error loading albums: {str(e)}")
        return []


def save_creator_albums(creator_id: int, albums: list):
    """Save albums for a specific creator"""
    albums_file = get_creator_albums_file(creator_id)
    try:
        with open(albums_file, "w") as f:
            json.dump(albums, f, indent=2)
    except Exception as e:
        print(f"Error saving albums: {str(e)}")
        raise


def get_creator_album_by_id(user_id: int, album_id: str, db: Session = None) -> dict:
    """Get a specific album with UUID support"""
    try:
        creator_id = user_id
        if db:
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                logger.error(f"User not found: {user_id}")
                return None
                
            if user.is_team or user.is_patreon:
                creator_id = user.created_by
                if not creator_id:
                    logger.error(f"Created_by not found for user: {user.id}")
                    return None
            elif user.is_creator:
                creator_id = user.id
            else:
                logger.error(f"Invalid user role for user: {user.id}")
                return None

        logger.info(f"Looking for album {album_id} from creator {creator_id}")
        albums = load_creator_albums(creator_id, db)
        album = next((album for album in albums if str(album["id"]) == str(album_id)), None)
        
        if album:
            logger.info(f"Found album: {album['title']} (ID: {album['id']}) for creator {creator_id}")
        else:
            logger.warning(f"Album not found with ID: {album_id} for creator {creator_id}")

        return album

    except Exception as e:
        logger.error(f"Error in get_creator_album_by_id: {str(e)}")
        return None


def get_formatted_user_role(user: User) -> str:
    """Get a formatted user role string"""
    if user.is_creator:
        return "Creator"
    elif user.is_team:
        return "Team Member"
    elif user.is_patreon:
        return f"Patron ({user.patreon_tier_id if user.patreon_tier_id else 'No Tier'})"
    else:
        return user.role.value.capitalize()



@app.get("/api/user/tier")
async def get_user_tier(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get current user's tier information"""
    try:
        # Get creator ID (either current user or their creator)
        creator_id = current_user.id if current_user.is_creator else current_user.created_by

        # Get tier information
        tier_info = current_user.get_tier_info()

        # Add role information
        return {
            "tier": tier_info,
            "role": current_user.role.value,
            "is_creator": current_user.is_creator,
            "is_team": current_user.is_team,
            "is_patreon": current_user.is_patreon
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error getting tier info: {str(e)}"
        )

def get_track_ids_from_zip(zip_path: str) -> Tuple[Set[str], int]:
    """
    Extract track IDs from filenames and count audio files in a ZIP archive.
    
    Args:
        zip_path: Path to the ZIP file
        
    Returns:
        Tuple of (track_ids, audio_file_count)
    """
    track_ids = set()
    audio_file_count = 0
    
    if not os.path.exists(zip_path):
        logger.warning(f"ZIP file does not exist: {zip_path}")
        return track_ids, audio_file_count
        
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            file_list = zip_ref.namelist()
            
            # Count audio files
            audio_extensions = ('.mp3', '.wav', '.flac', '.aac', '.m4a', '.ogg')
            audio_files = [f for f in file_list if f.lower().endswith(audio_extensions)]
            audio_file_count = len(audio_files)
            
            # Log some sample files for debugging
            if file_list:
                sample_files = file_list[:3]
                logger.info(f"Sample files in ZIP: {sample_files}")
            
            # Still try to extract track IDs using regex pattern
            # Assuming filenames contain track IDs in standard UUID format
            uuid_pattern = r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'
            
            for filename in file_list:
                matches = re.findall(uuid_pattern, filename)
                track_ids.update(matches)
                
            if track_ids:
                logger.info(f"Found {len(track_ids)} unique track IDs in ZIP")
            else:
                logger.warning(f"No track IDs found in ZIP files! Will use file count instead.")
            
    except zipfile.BadZipFile:
        logger.error(f"Invalid ZIP file: {zip_path}")
    except Exception as e:
        logger.error(f"Error reading ZIP file {zip_path}: {str(e)}")
        
    return track_ids, audio_file_count

def has_album_content_changed(db, album_id, existing_download_id) -> Tuple[bool, str]:
    """
    Check if album content has changed since the last download using multiple methods:
    1. File count comparison (primary method)
    2. Track IDs comparison (if IDs are in filenames)
    3. Track metadata update checks
    
    Args:
        db: Database session
        album_id: Album ID (UUID)
        existing_download_id: ID of existing download record
        
    Returns:
        Tuple of (has_changed, reason)
    """
    try:
        from sqlalchemy import text
        album_id_str = str(album_id)
        
        # Get download record to find the ZIP file
        download_query = text("""
            SELECT download_path, downloaded_at
            FROM user_downloads
            WHERE id = :download_id
        """)
        
        download_record = db.execute(download_query, {"download_id": existing_download_id}).fetchone()
        if not download_record or not download_record.download_path:
            return True, "Download record not found or missing path"
            
        zip_path = download_record.download_path
        download_date = download_record.downloaded_at
        
        # Get current track IDs from database
        current_tracks_query = text("""
            SELECT id
            FROM tracks
            WHERE album_id = :album_id
        """)
        
        db_tracks = db.execute(current_tracks_query, {"album_id": album_id_str}).fetchall()
        current_track_ids = {str(track.id) for track in db_tracks}
        current_track_count = len(current_track_ids)
        
        # Get track IDs and audio file count from ZIP file
        zip_track_ids, audio_file_count = get_track_ids_from_zip(zip_path)
        
        # Log detailed information for debugging
        logger.info(f"Album ID: {album_id_str}")
        logger.info(f"ZIP path: {zip_path}")
        logger.info(f"Current tracks in DB: {current_track_count}")
        logger.info(f"Audio files in ZIP: {audio_file_count}")
        
        # PRIMARY CHECK: Compare audio file count to track count
        # This works even if track IDs aren't in filenames
        if audio_file_count != current_track_count:
            return True, f"Track count changed: ZIP has {audio_file_count} audio files, DB has {current_track_count} tracks"
        
        # SECONDARY CHECK: If we found track IDs, use them for more detailed comparison
        if zip_track_ids:
            # Check for missing tracks (in ZIP but not in DB)
            missing_tracks = zip_track_ids - current_track_ids
            if missing_tracks:
                return True, f"Tracks removed: {len(missing_tracks)} tracks no longer exist ({', '.join(list(missing_tracks)[:3])}...)"
                
            # Check for new tracks (in DB but not in ZIP)
            new_tracks = current_track_ids - zip_track_ids
            if new_tracks:
                return True, f"Tracks added: {len(new_tracks)} new tracks added"
        
        # ADDITIONAL CHECKS: Check if album or tracks were updated after the download
        album_query = text("""
            SELECT updated_at
            FROM albums
            WHERE id = :album_id
        """)
        
        album_record = db.execute(album_query, {"album_id": album_id_str}).fetchone()
        if album_record and album_record.updated_at and album_record.updated_at > download_date:
            return True, f"Album metadata updated at {album_record.updated_at.isoformat()}"
            
        # Check if any tracks were updated after the download
        updated_tracks_query = text("""
            SELECT id, title, updated_at
            FROM tracks
            WHERE album_id = :album_id
            AND updated_at IS NOT NULL
            AND updated_at > :download_date
        """)
        
        updated_tracks = db.execute(
            updated_tracks_query, 
            {"album_id": album_id_str, "download_date": download_date}
        ).fetchall()
        
        if updated_tracks:
            track_titles = [t.title for t in updated_tracks[:3]]
            return True, f"Tracks updated: {len(updated_tracks)} tracks modified ({', '.join(track_titles)}...)"
        
        return False, "No changes detected"
        
    except Exception as e:
        logger.error(f"Error in has_album_content_changed: {str(e)}", exc_info=True)
        return True, f"Error checking for changes: {str(e)}"
       
        
@app.get("/api/albums/{album_id}/download")
async def download_album(
    album_id: UUID,
    voice: Optional[str] = None,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    from sqlalchemy import text
    album = db.query(Album).options(joinedload(Album.tracks)).filter(Album.id == album_id).first()
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")
    if not album.tracks:
        raise HTTPException(status_code=400, detail="Album has no tracks")

    tts_tracks = [t for t in album.tracks if getattr(t, "track_type", "audio") == "tts"]
    album_voice = voice or (getattr(tts_tracks[0], "default_voice", "en-US-AvaNeural") if tts_tracks else None)
    album_id_str = str(album_id)

    # Check for valid existing download in user_downloads
    if album_voice:
        r = db.execute(text("""
            SELECT id, download_path FROM user_downloads
            WHERE user_id=:uid AND album_id=CAST(:aid AS uuid)
              AND download_type='album'::downloadtype
              AND voice_id=:v AND is_available=true
              AND expires_at > :now
            ORDER BY downloaded_at DESC LIMIT 1
        """), {"uid": current_user.id, "aid": album_id_str, "v": album_voice, "now": datetime.now(timezone.utc)}).fetchone()
    else:
        r = db.execute(text("""
            SELECT id, download_path FROM user_downloads
            WHERE user_id=:uid AND album_id=CAST(:aid AS uuid)
              AND download_type='album'::downloadtype
              AND (voice_id IS NULL OR voice_id='')
              AND is_available=true
              AND expires_at > :now
            ORDER BY downloaded_at DESC LIMIT 1
        """), {"uid": current_user.id, "aid": album_id_str, "now": datetime.now(timezone.utc)}).fetchone()

    if r:
        path = Path(r.download_path)
        if _valid_zip(path):
            return {
                "status": "COMPLETED",
                "download_path": f"/api/my-downloads/{r.id}/file",
                "progress": 100,
                "from_my_downloads": True,
                "download_id": r.id,
                "user_id": current_user.id,
                "voice": album_voice,
                "has_tts_tracks": bool(tts_tracks),
            }
        else:
            # Invalid cache - mark as unavailable and remove file
            db.execute(text("UPDATE user_downloads SET is_available=false WHERE id=:id"), {"id": r.id})
            db.commit()
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass

    # No valid cache - proceed with new download
    # Permission checks
    should_charge = True
    if current_user.is_creator:
        should_charge = False
    elif current_user.is_team or current_user.is_patreon or current_user.is_kofi:
        should_charge = True
    else:
        raise HTTPException(status_code=403, detail="Download permission denied")

    # Build track list
    items = []
    for t in album.tracks:
        ttype = getattr(t, "track_type", "audio")
        if ttype == "tts" and album_voice:
            mega_path = storage.tts_package_manager.get_voice_audio_path(t.id, album_voice)
        else:
            mega_path = f"{storage.mega_audio_path}/{Path(t.file_path).name}"
        items.append({
            "track": {
                "id": str(t.id),
                "title": t.title,
                "mega_path": mega_path,
                "file_path": str(t.file_path),
                "order": t.order,
                "track_type": ttype,
                "voice": album_voice if ttype == "tts" else None,
            },
            "mega_path": mega_path,
        })
    items.sort(key=lambda x: x["track"].get("order") or 0)

    # Queue download
    info = await download_manager.queue_download(
        user_id=current_user.id,
        album_id=album_id_str,
        tracks=items,
        should_charge=should_charge,
        reservation_id=None,
        is_creator=current_user.is_creator,
    )
    info["user_id"] = current_user.id
    info["voice"] = album_voice
    info["has_tts_tracks"] = bool(tts_tracks)
    return info
def _valid_zip(path: Path) -> bool:
    """Check if ZIP file exists and is valid."""
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        import zipfile
        with zipfile.ZipFile(path, 'r') as zf:
            return len(zf.namelist()) > 0
    except Exception:
        return False

@app.get("/api/downloads/status")
async def get_download_status(
    album_id: str,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Check status of a particular album download."""
    request_id = f"status_{datetime.now(timezone.utc).timestamp()}"
    
    try:
        # First check if it's an existing completed download in the database
        from sqlalchemy import text
        from pathlib import Path
        
        existing_download_query = text("""
            SELECT id, download_path 
            FROM user_downloads 
            WHERE user_id = :user_id 
            AND album_id = :album_id 
            AND download_type = 'album'::downloadtype
            AND is_available = true
            AND expires_at > :now
        """)
        
        existing_result = db.execute(
            existing_download_query, 
            {
                "user_id": current_user.id,
                "album_id": album_id,
                "now": datetime.now(timezone.utc)
            }
        ).fetchone()
        
        # If it exists in the database AND the file exists, return as completed
        if existing_result and Path(existing_result.download_path).exists():
            logger.info(f"[{request_id}] Found existing download in database with ID {existing_result.id}")
            return {
                "status": "COMPLETED",
                "stage": "COMPLETED",
                "download_path": f"/api/my-downloads/{existing_result.id}/file",
                "progress": 100,
                "from_my_downloads": True,
                "download_id": existing_result.id,
                "user_id": current_user.id,
                "queue_position": None,
                "current_track": 0,
                "total_tracks": 0,
                "can_retry": False
            }
        elif existing_result:
            logger.warning(f"[{request_id}] Download record exists but file is missing: {existing_result.download_path}")
            # Continue to check download manager

        # If not in database or file missing, check the download manager
        download_id = f"{current_user.id}_{album_id}"
        status = await download_manager.get_download_status(download_id)
        
        if not status:
            return {
                "status": "NOT_FOUND",
                "error": "Download not found",
                "can_retry": True,
                "progress": 0,
                "file_progress": 0,
                "stage": "NOT_FOUND",
                "current_track": 0,
                "total_tracks": 0,
                "queue_position": None,
                "user_id": current_user.id,
                "album_id": album_id
            }

        def safe_convert(value, default=0):
            if value is None:
                return default
            try:
                return int(float(value))
            except (ValueError, TypeError):
                return default

        # Get the raw stage without DOWNLOADSTAGE prefix
        raw_stage = str(status.get('stage', 'queued')).replace('DOWNLOADSTAGE.', '')
        
        # Calculate queue position for any active download (not just QUEUED)
        queue_position = None
        active_stages = ["QUEUED", "INITIALIZATION", "PREPARATION", "DOWNLOADING", "COMPRESSION"]
        if raw_stage.upper() in active_stages:
            if 'queue_position' in status:
                queue_position = safe_convert(status['queue_position'])
            else:
                # Manually calculate queue position if not in status
                try:
                    async with download_queue_lock:
                        queue_list = list(download_manager.download_queue._queue)
                        for i, task in enumerate(queue_list, start=1):
                            if isinstance(task, dict) and task.get("download_id") == download_id:
                                queue_position = i
                                break
                        if queue_position is None and raw_stage.upper() != "QUEUED":
                            # If not in queue but still active, it's currently being processed
                            queue_position = 0  # Being processed now
                except Exception as e:
                    logger.error(f"[{request_id}] Queue position calculation error: {e}")
                    queue_position = None
        
        # Build response with all status fields
        response = {
            "status": raw_stage.upper(),
            "stage": raw_stage.upper(),
            "stage_detail": str(status.get('stage_detail', '')),
            "progress": safe_convert(status.get('progress', 0)),
            "file_progress": safe_convert(status.get('file_progress', 0)),
            "current_track": safe_convert(status.get('track_number', 0)),
            "total_tracks": safe_convert(status.get('total_tracks', 0)),
            "can_retry": (raw_stage.upper() == "ERROR"),
            "processed_size": safe_convert(status.get('processed_size', 0)),
            "total_size": safe_convert(status.get('total_size', 0)),
            "last_updated": status.get('last_updated', datetime.now(timezone.utc).isoformat()),
            "queue_position": queue_position,
            "user_id": current_user.id,
            "album_id": album_id
        }

        # Add rate info for active downloads or compression
        if 'rate' in status:
            try:
                response["rate"] = float(status['rate']) if status['rate'] else 0.0
            except (ValueError, TypeError):
                response["rate"] = 0.0

        # Add completion time for completed downloads
        if raw_stage.upper() == "COMPLETED":
            response["download_path"] = str(status.get('download_path', ''))
            if 'completed_at' in status:
                response["completed_at"] = status['completed_at'].isoformat()

        # Add error info
        if raw_stage.upper() == "ERROR":
            response["error"] = str(status.get('error', 'Unknown error'))

        logger.info(f"[{request_id}] Sending status response: {response}")
        return response

    except Exception as e:
        logger.error(f"[{request_id}] Error: {str(e)}")
        return {
            "status": "ERROR",
            "error": str(e),
            "can_retry": True,
            "progress": 0,
            "file_progress": 0,
            "stage": "ERROR",
            "current_track": 0,
            "total_tracks": 0,
            "queue_position": None,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "user_id": current_user.id,
            "album_id": album_id
        }
@app.get("/api/files/{download_id}")
async def get_download_file(
    download_id: str,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    from sqlalchemy import text
    parts = download_id.split("_", 1)
    if len(parts) != 2:
        raise HTTPException(status_code=400, detail="Invalid download ID format")
    
    entity_id = parts[1]
    is_track = entity_id.startswith("track_")

    if is_track:
        # Track handling (unchanged)
        track_id = entity_id.split("_", 1)[1]
        track = db.query(Track).filter(Track.id == track_id).first()
        if not track:
            raise HTTPException(status_code=404, detail="Track not found")
        if current_user.is_team and track.created_by_id != current_user.created_by:
            raise HTTPException(status_code=403, detail="Access denied")

        rec = db.execute(text("""
            SELECT id, download_path
            FROM user_downloads
            WHERE user_id=:uid
              AND track_id=:tid
              AND download_type='track'::downloadtype
              AND is_available=true
            ORDER BY downloaded_at DESC
            LIMIT 1
        """), {"uid": current_user.id, "tid": track_id}).fetchone()

        if not rec or not Path(rec.download_path).exists():
            raise HTTPException(status_code=404, detail="Download file not found or not completed")

        safe_title = "".join(c for c in track.title if c.isalnum() or c in (" ", "-", "_")).strip()
        return FileResponse(
            path=str(rec.download_path),
            media_type="application/zip",
            filename=f"{safe_title}.zip",
            headers={"Cache-Control": "no-cache", "Content-Disposition": f'attachment; filename="{safe_title}.zip"'}
        )

    # Album handling - ONLY serve from user_downloads, no temp file fallback
    album_id_str = entity_id
    album = db.query(Album).filter(Album.id == album_id_str).first()
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")
    if current_user.is_team and album.created_by_id != current_user.created_by:
        raise HTTPException(status_code=403, detail="Access denied")

    # Find latest available download record
    rec = db.execute(text("""
        SELECT id, download_path
        FROM user_downloads
        WHERE user_id=:uid
          AND album_id=CAST(:aid AS uuid)
          AND download_type='album'::downloadtype
          AND is_available=true
        ORDER BY downloaded_at DESC
        LIMIT 1
    """), {"uid": current_user.id, "aid": album_id_str}).fetchone()

    if not rec:
        raise HTTPException(status_code=404, detail="Download file not found or not completed")

    path = Path(rec.download_path)
    if not _valid_zip(path):
        # Invalid file - mark as unavailable and remove
        db.execute(text("UPDATE user_downloads SET is_available=false WHERE id=:id"), {"id": rec.id})
        db.commit()
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass
        raise HTTPException(status_code=404, detail="Download file not found or not completed")

    safe_title = "".join(c for c in album.title if c.isalnum() or c in (" ", "-", "_")).strip()
    return FileResponse(
        path=str(path),
        media_type="application/zip",
        filename=f"{safe_title}.zip",
        headers={"Cache-Control": "no-cache", "Content-Disposition": f'attachment; filename="{safe_title}.zip"'}
    )
@app.get("/api/tracks/{track_id}/status")
async def get_track_download_status(
    track_id: str,
    voice: Optional[str] = None,  # ✅ ADD: Voice parameter
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Check track download status with voice support."""
    try:
        # ✅ VOICE-AWARE: Get track info to determine voice-specific ID
        track = db.query(Track).filter(Track.id == track_id).first()
        if not track:
            return {'status': 'error', 'error': 'Track not found'}
        
        track_type = getattr(track, 'track_type', 'audio')
        target_voice = None
        
        if track_type == 'tts':
            if voice:
                target_voice = voice
            else:
                target_voice = getattr(track, 'default_voice', 'en-US-AvaNeural')
        
        # ✅ VOICE-AWARE: Create voice-specific download ID
        if track_type == 'tts' and target_voice:
            download_id = f"track_{current_user.id}_{track_id}_{target_voice}"
        else:
            download_id = f"track_{current_user.id}_{track_id}"
        
        logger.info(f"Checking status for download: {download_id}")
        
        # Get status from track download manager
        status = await track_download_manager.get_download_status(download_id)
        
        # If no status found in memory, check the database with voice consideration
        if not status or status.get('status') == 'not_found':
            from sqlalchemy import text
            from pathlib import Path
            
            if track_type == 'tts' and target_voice:
                existing_query = text("""
                    SELECT id, download_path FROM user_downloads 
                    WHERE user_id = :user_id 
                    AND track_id = :track_id 
                    AND voice_id = :voice_id
                    AND download_type = 'track'::downloadtype
                    AND is_available = true
                """)
                existing_record = db.execute(existing_query, {
                    "user_id": current_user.id,
                    "track_id": track_id,
                    "voice_id": target_voice
                }).fetchone()
            else:
                existing_query = text("""
                    SELECT id, download_path FROM user_downloads 
                    WHERE user_id = :user_id 
                    AND track_id = :track_id 
                    AND (voice_id IS NULL OR voice_id = '')
                    AND download_type = 'track'::downloadtype
                    AND is_available = true
                """)
                existing_record = db.execute(existing_query, {
                    "user_id": current_user.id,
                    "track_id": track_id
                }).fetchone()
            
            if existing_record and Path(existing_record.download_path).exists():
                logger.info(f"Found completed download in database with ID {existing_record.id}")
                return {
                    'status': 'completed',
                    'progress': 100,
                    'error': None,
                    'download_path': f"/api/my-downloads/{existing_record.id}/file",
                    'speed': "0 MB/s",
                    'message': 'Download ready (from database)',
                    'voice': target_voice,
                    'track_type': track_type
                }
            
            logger.info(f"No status found for download: {download_id}")
            return {
                'status': 'not_found',
                'progress': 0,
                'error': None,
                'download_path': None,
                'speed': "0 MB/s",
                'voice': target_voice,
                'track_type': track_type
            }

        # Build response with voice info
        response = {
            'status': status.get('status', 'processing'),
            'progress': status.get('progress', 0),
            'error': status.get('error'),
            'download_path': status.get('file_path'),
            'speed': status.get('speed', '0 MB/s'),
            'voice': target_voice,
            'track_type': track_type
        }
        
        # Handle different statuses...
        if status.get('status') == 'queued':
            queue_position = status.get('queue_position', 'Unknown')
            response.update({
                'queue_position': queue_position,
                'message': f"Waiting in queue (Position: {queue_position})"
            })
        elif status.get('status') == 'processing':
            if status.get('downloaded') and status.get('total_size'):
                downloaded_mb = status.get('downloaded', 0) / (1024 * 1024)
                total_mb = status.get('total_size', 0) / (1024 * 1024)
                message = (
                    f"Downloading: {status.get('progress', 0):.1f}% "
                    f"({downloaded_mb:.1f}MB / {total_mb:.1f}MB) "
                    f"@ {status.get('speed', '0 MB/s')}"
                )
            else:
                message = f"Downloading: {status.get('progress', 0):.1f}%"
            response['message'] = message
        elif status.get('status') == 'completed':
            response['message'] = 'Download ready'
        elif status.get('status') == 'error':
            error_msg = status.get('error', 'Unknown error')
            response['message'] = f"Error: {error_msg}"

        return response

    except Exception as e:
        logger.error(f"Error checking track status for {track_id}: {str(e)}", exc_info=True)
        return {
            'status': 'error',
            'progress': 0,
            'message': f"Error checking status: {str(e)}",
            'error': str(e)
        }

@app.get("/api/user/downloads")
async def get_user_downloads_endpoint(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get user's download count and limit"""
    try:
        if not current_user.is_patreon:
            raise HTTPException(
                status_code=403,
                detail="Only patrons can access download information"
            )

        return await get_user_downloads(current_user, db)
    except Exception as e:
        logger.error(f"Error getting download info: {str(e)}")
        raise HTTPException(status_code=500, detail="Error retrieving download information")


@app.get("/api/tracks/{track_id}/download")
async def init_track_download(
    track_id: str,
    voice: Optional[str] = None,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Initialize track download with voice support for TTS tracks."""
    try:
        logger.info(f"Download request for track {track_id} by user {current_user.email} (voice: {voice})")
        
        # Get track info first to check if it's TTS
        track = db.query(Track).filter(Track.id == track_id).first()
        if not track:
            logger.error(f"Track {track_id} not found")
            raise HTTPException(status_code=404, detail="Track not found")

        # Determine the voice to use for TTS tracks
        track_type = getattr(track, 'track_type', 'audio')
        target_voice = None
        
        if track_type == 'tts':
            if voice:
                target_voice = voice
                logger.info(f"Using requested voice: {voice}")
            else:
                target_voice = getattr(track, 'default_voice', 'en-US-AvaNeural')
                logger.info(f"Using track default voice: {target_voice}")
        
        # Create download ID with voice suffix for TTS
        if track_type == 'tts' and target_voice:
            download_id = f"track_{current_user.id}_{track_id}_{target_voice}"
            logger.info(f"TTS download ID with voice: {download_id}")
        else:
            download_id = f"track_{current_user.id}_{track_id}"
            logger.info(f"Regular download ID: {download_id}")
        
        # Check for ACTIVE download record (within 24 hours)
        from sqlalchemy import text
        
        if track_type == 'tts' and target_voice:
            active_download_query = db.execute(
                text("""
                    SELECT id, download_path FROM user_downloads 
                    WHERE user_id = :user_id 
                    AND track_id = :track_id 
                    AND download_type = 'track'::downloadtype
                    AND voice_id = :voice_id
                    AND is_available = true
                    AND expires_at > :now
                """),
                {
                    "user_id": current_user.id,
                    "track_id": track_id,
                    "voice_id": target_voice,
                    "now": datetime.now(timezone.utc)
                }
            )
        else:
            active_download_query = db.execute(
                text("""
                    SELECT id, download_path FROM user_downloads 
                    WHERE user_id = :user_id 
                    AND track_id = :track_id 
                    AND download_type = 'track'::downloadtype
                    AND (voice_id IS NULL OR voice_id = '')
                    AND is_available = true
                    AND expires_at > :now
                """),
                {
                    "user_id": current_user.id,
                    "track_id": track_id,
                    "now": datetime.now(timezone.utc)
                }
            )
        
        active_record = active_download_query.fetchone()
        active_id = active_record.id if active_record else None
        active_path = active_record.download_path if active_record else None
        
        # Check if the file for an active download actually exists
        db_file_exists = False
        if active_path and Path(active_path).exists():
            db_file_exists = True
            logger.info(f"Active download file found in DB at: {active_path}")
        
        # Check for temporary download file with voice suffix
        if track_type == 'tts' and target_voice:
            temp_file_path = Path("/tmp/mega_downloads/tracks") / f"{download_id}.mp3"
        else:
            temp_file_path = Path("/tmp/mega_downloads/tracks") / f"{download_id}.mp3"
        temp_file_exists = temp_file_path.exists()
        
        # Check status from track download manager
        download_status = await track_download_manager.get_download_status(download_id)
        
        # CASE 1: We have a valid ACTIVE download in the database and the file exists
        if active_id and db_file_exists:
            logger.info(f"Using existing active download from database with ID {active_id}")
            return {
                "success": True,
                "download_id": download_id,
                "status": "completed",
                "download_path": f"/api/my-downloads/{active_id}/file",
                "from_my_downloads": True,
                "voice": target_voice,
                "track_type": track_type
            }
        
        # CASE 2: Manager says it's completed but need to verify file exists
        if download_status and download_status.get('status') == 'completed':
            if temp_file_exists:
                logger.info(f"Using completed download from track manager")
                return {
                    "success": True,
                    "download_id": download_id,
                    "status": "completed",
                    "downloaded": True,
                    "voice": target_voice,
                    "track_type": track_type
                }
            else:
                logger.warning(f"Track manager says completed but file is missing - will redownload")
                if download_id in track_download_manager.completed_downloads:
                    del track_download_manager.completed_downloads[download_id]
        
        # CASE 3: Download is in progress
        if download_status and download_status.get('status') == 'processing':
            logger.info(f"Download already in progress: {download_status.get('progress')}%")
            return {
                "success": True,
                "download_id": download_id,
                "status": "processing",
                "progress": download_status.get('progress', 0),
                "voice": target_voice,
                "track_type": track_type
            }
            
        # CASE 4: Need to start a new download
        should_charge = True
        
        if current_user.is_creator:
            should_charge = False
            logger.info(f"Creator access granted for {current_user.email} - unlimited track downloads")
        elif current_user.is_team or current_user.is_patreon or current_user.is_kofi:
            if active_id:
                should_charge = False
                logger.info(f"Re-download of active content for {current_user.email} - not charging credits")
            else:
                if track_type == 'tts':
                    ever_downloaded_query = db.execute(
                        text("""
                            SELECT id FROM user_downloads 
                            WHERE user_id = :user_id 
                            AND track_id = :track_id 
                            AND voice_id = :voice_id
                            AND download_type = 'track'::downloadtype
                            LIMIT 1
                        """),
                        {
                            "user_id": current_user.id,
                            "track_id": track_id,
                            "voice_id": target_voice
                        }
                    )
                else:
                    ever_downloaded_query = db.execute(
                        text("""
                            SELECT id FROM user_downloads 
                            WHERE user_id = :user_id 
                            AND track_id = :track_id 
                            AND download_type = 'track'::downloadtype
                            LIMIT 1
                        """),
                        {
                            "user_id": current_user.id,
                            "track_id": track_id
                        }
                    )
                
                ever_downloaded = ever_downloaded_query.fetchone()
                
                if ever_downloaded:
                    should_charge = True
                    logger.info(f"Re-download of expired content for {current_user.email} - will charge credits")
                else:
                    should_charge = True
                    logger.info(f"New download for {current_user.email} - will charge credits")
        else:
            logger.warning(f"User {current_user.email} does not have track download permission")
            raise HTTPException(status_code=403, detail="Track download permission denied")

        # RESERVE CREDIT
        reservation_id = f"{download_id}_{int(datetime.now(timezone.utc).timestamp())}"
        
        if should_charge:
            from credit_reservation import CreditReservationService
            
            success, message = CreditReservationService.reserve_credit(
                db, current_user, reservation_id, 'track'
            )
            
            if not success:
                logger.warning(f"Credit reservation failed for track {track_id}: {message}")
                
                if "Insufficient" in message:
                    download_info = await get_user_downloads(current_user, db)
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "message": "No track downloads remaining",
                            "downloads_used": download_info["tracks"]["downloads_used"],
                            "downloads_allowed": download_info["tracks"]["downloads_allowed"],
                            "reserved": download_info["tracks"].get("reserved", 0),
                            "reason": message
                        }
                    )
                else:
                    raise HTTPException(status_code=403, detail={"message": message})
            
            logger.info(f"Credit reserved successfully for track {track_id}: {message}")

        # ✅ FIXED: Use correct TTS package path structure
        if track_type == 'tts' and target_voice:
            # Use TTS package manager to get correct path
            mega_path = storage.tts_package_manager.get_voice_audio_path(track_id, target_voice)
            logger.info(f"TTS voice-specific MEGA path: {mega_path}")
        else:
            # Regular audio track
            mega_path = f"{storage.mega_audio_path}/{Path(track.file_path).name}"
            logger.info(f"Regular MEGA path: {mega_path}")
        
        # Create track info with correct path
        track_info = {
            'title': track.title,
            'mega_path': mega_path,
            'file_path': str(track.file_path),
            'voice': target_voice,
            'track_type': track_type
        }

        # ✅ NEW: Queue download with concurrency limit handling
        try:
            # Import the exception from the track worker
            from core.track_download_workers import ConcurrentLimitExceeded
            
            queued_download_id = await track_download_manager.queue_download(
                user_id=current_user.id,
                track_id=track_id,
                track_info=track_info,
                should_charge=should_charge,
                reservation_id=reservation_id if should_charge else None,
                voice=target_voice,
                is_creator=current_user.is_creator  # ✅ NEW: Pass creator flag
            )
            
        except ConcurrentLimitExceeded as e:
            # ✅ NEW: Release credit reservation when concurrent limit is reached
            if should_charge and reservation_id:
                try:
                    from credit_reservation import CreditReservationService
                    CreditReservationService.release_reservation(db, reservation_id, "concurrent_limit")
                    logger.info(f"Released track reservation due to concurrent limit for user {current_user.email}")
                except Exception as cleanup_error:
                    logger.error(f"Error releasing track reservation on concurrent limit: {cleanup_error}")

            # Return structured 429 response
            raise HTTPException(
                status_code=429,
                detail={
                    "message": f"Maximum {e.limit} downloads allowed at once. You currently have {e.active} active downloads.",
                    "error_type": "concurrent_limit_exceeded",
                    "current_count": e.active,
                    "max_allowed": e.limit
                }
            )
        
        logger.info(f"Download queued successfully. Download ID: {queued_download_id}, will charge: {should_charge}, voice: {target_voice}")
        return {
            "success": True, 
            "download_id": queued_download_id,
            "voice": target_voice,
            "track_type": track_type
        }

    except HTTPException as he:
        # Clean up credit reservation on any HTTP exception
        if 'should_charge' in locals() and should_charge and 'reservation_id' in locals():
            try:
                from credit_reservation import CreditReservationService
                CreditReservationService.release_reservation(db, reservation_id, "endpoint_error")
                logger.info(f"Released track reservation due to endpoint error")
            except Exception as cleanup_error:
                logger.error(f"Error releasing track reservation on failure: {cleanup_error}")
        raise he
        
    except Exception as e:
        logger.error(f"Error initializing track download: {str(e)}", exc_info=True)
        
        # Clean up credit reservation on any unexpected exception
        if 'should_charge' in locals() and should_charge and 'reservation_id' in locals():
            try:
                from credit_reservation import CreditReservationService
                CreditReservationService.release_reservation(db, reservation_id, "endpoint_error")
                logger.info(f"Released track reservation due to endpoint error")
            except Exception as cleanup_error:
                logger.error(f"Error releasing track reservation on failure: {cleanup_error}")
        
        raise HTTPException(status_code=500, detail="Internal server error")



@app.get("/api/tracks/{track_id}/file")
async def download_track_file(
    track_id: str,
    background_tasks: BackgroundTasks,
    voice: Optional[str] = None,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Serve the downloaded track file with voice support and better error handling."""
    try:
        logger.info(f"File download request for track {track_id} by user {current_user.email} (voice: {voice})")
        
        # Get track info to determine type and voice
        track = db.query(Track).filter(Track.id == track_id).first()
        if not track:
            logger.error(f"Track {track_id} not found")
            raise HTTPException(status_code=404, detail="Track not found")

        # ✅ VOICE-AWARE: Determine target voice for TTS tracks
        track_type = getattr(track, 'track_type', 'audio')
        target_voice = None
        
        if track_type == 'tts':
            if voice:
                target_voice = voice
            else:
                target_voice = getattr(track, 'default_voice', 'en-US-AvaNeural')
            logger.info(f"🎤 TTS track using voice: {target_voice}")
        
        # ✅ VOICE-AWARE: Create download ID with voice suffix for TTS tracks
        if track_type == 'tts' and target_voice:
            download_id = f"track_{current_user.id}_{track_id}_{target_voice}"
        else:
            download_id = f"track_{current_user.id}_{track_id}"

        # ✅ PRIORITY 1: Check for existing download in my-downloads FIRST
        from sqlalchemy import text
        if track_type == 'tts' and target_voice:
            existing_download_query = db.execute(
                text("""
                    SELECT id, download_path, original_filename FROM user_downloads 
                    WHERE user_id = :user_id 
                    AND track_id = :track_id 
                    AND voice_id = :voice_id
                    AND download_type = 'track'::downloadtype
                    AND is_available = true
                    ORDER BY downloaded_at DESC
                    LIMIT 1
                """),
                {
                    "user_id": current_user.id,
                    "track_id": track_id,
                    "voice_id": target_voice
                }
            )
        else:
            existing_download_query = db.execute(
                text("""
                    SELECT id, download_path, original_filename FROM user_downloads 
                    WHERE user_id = :user_id 
                    AND track_id = :track_id 
                    AND (voice_id IS NULL OR voice_id = '')
                    AND download_type = 'track'::downloadtype
                    AND is_available = true
                    ORDER BY downloaded_at DESC
                    LIMIT 1
                """),
                {
                    "user_id": current_user.id,
                    "track_id": track_id
                }
            )
        
        existing_record = existing_download_query.fetchone()
        
        if existing_record and Path(existing_record.download_path).exists():
            logger.info(f"✅ Serving file from my-downloads: {existing_record.download_path}")
            
            download_filename = existing_record.original_filename
            if not download_filename:
                if track_type == 'tts' and target_voice:
                    voice_name = target_voice.replace('en-US-', '').replace('Neural', '')
                    download_filename = f"{voice_name} - {track.title}.mp3"
                else:
                    clean_title = "".join(c for c in track.title if c.isalnum() or c in (' ', '-', '_')).strip()
                    download_filename = f"{clean_title}.mp3"
            
            return FileResponse(
                path=str(existing_record.download_path),
                media_type='audio/mpeg',
                filename=download_filename,
                headers={
                    'Cache-Control': 'no-cache',
                    'Content-Disposition': f'attachment; filename="{download_filename}"'
                }
            )

        # ✅ PRIORITY 2: Check temp file with wait logic
        file_path = Path("/tmp/mega_downloads/tracks") / f"{download_id}.mp3"
        
        # ✅ NEW: Wait for file to appear if download is in progress
        max_wait_time = 30  # Wait up to 30 seconds
        wait_interval = 1   # Check every 1 second
        waited_time = 0
        
        # Check if download is actively processing
        from core.track_download_workers import track_download_manager
        download_status = await track_download_manager.get_download_status(download_id)
        
        if download_status and download_status.get('status') in ['processing', 'queued']:
            logger.info(f"⏳ Download in progress, waiting for file: {download_id}")
            
            while waited_time < max_wait_time and not file_path.exists():
                await asyncio.sleep(wait_interval)
                waited_time += wait_interval
                
                # Check if download completed or failed
                current_status = await track_download_manager.get_download_status(download_id)
                if current_status:
                    if current_status.get('status') == 'completed':
                        logger.info(f"✅ Download completed while waiting")
                        break
                    elif current_status.get('status') == 'error':
                        logger.error(f"❌ Download failed while waiting: {current_status.get('error')}")
                        raise HTTPException(status_code=500, detail=f"Download failed: {current_status.get('error')}")
                
                logger.info(f"⏳ Still waiting for file... ({waited_time}s/{max_wait_time}s)")
        
        if not file_path.exists():
            logger.error(f"❌ File not found after waiting: {file_path}")
            
            # Provide more helpful error messages
            if download_status:
                status_info = download_status.get('status', 'unknown')
                if status_info == 'completed':
                    error_msg = "Download shows as completed but file is missing. Please try downloading again."
                elif status_info == 'error':
                    error_msg = f"Download failed: {download_status.get('error', 'Unknown error')}"
                elif status_info in ['processing', 'queued']:
                    error_msg = f"Download is still {status_info}. Please wait and try again."
                else:
                    error_msg = "Download not found. Please start a new download."
            else:
                error_msg = "Download not found. Please start a new download."
            
            raise HTTPException(status_code=404, detail=error_msg)

        # ✅ SUCCESS: File exists, prepare for download
        if track_type == 'tts' and target_voice:
            voice_name = target_voice.replace('en-US-', '').replace('Neural', '')
            download_filename = f"{voice_name} - {track.title}.mp3"
        else:
            clean_title = "".join(c for c in track.title if c.isalnum() or c in (' ', '-', '_')).strip()
            download_filename = f"{clean_title}.mp3"
        
        logger.info(f"✅ Serving temp file: {download_filename}")

        # Cleanup function remains the same
        def cleanup_temp_file():
            try:
                if file_path.exists():
                    file_path.unlink()
                    logger.info(f"✅ Cleaned up temp file: {file_path}")
                    
                    if download_id in track_download_manager.completed_downloads:
                        del track_download_manager.completed_downloads[download_id]
                        logger.info(f"✅ Removed download tracking: {download_id}")
                        
            except Exception as e:
                logger.error(f"❌ Error cleaning up temp file {file_path}: {e}")
        
        background_tasks.add_task(cleanup_temp_file)

        return FileResponse(
            path=str(file_path),
            media_type='audio/mpeg',
            filename=download_filename,
            headers={
                'Cache-Control': 'no-cache',
                'Content-Disposition': f'attachment; filename="{download_filename}"'
            }
        )

    except HTTPException:
        raise  # Re-raise HTTP exceptions as-is
    except Exception as e:
        logger.error(f"❌ Error serving track file: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while serving file")

@app.patch("/api/albums/{album_id}/tier-access")
async def update_album_tier_access(
    album_id: UUID,
    tier_access: dict,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db),
    album_service: AlbumService = Depends(get_album_service)
):
    """Update album tier access using database"""
    if not current_user.is_creator and not current_user.is_team:
        raise HTTPException(status_code=403, detail="Only creators and team members can update tier access")
    
    try:
        creator_id = current_user.id if current_user.is_creator else current_user.created_by
        
        # Use the album service to update tier restrictions
        updated_album = await album_service.update_tier_restrictions(
            album_id=str(album_id),
            tier_data={
                "minimum_tier": tier_access.get("minimum_tier"),
                "amount_cents": tier_access.get("amount_cents", 0)
            },
            creator_id=creator_id
        )
        
        return {
            "status": "success",
            "album_id": str(album_id),
            "tier_restrictions": updated_album.tier_restrictions
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating tier access: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
        
def check_album_access(album: dict, user: User) -> bool:
    """Check if user has access to an album based on their tier level"""
    # Creators and team members always have access
    if user.is_creator or user.is_team:
        return True
    
    restrictions = album.get("tier_restrictions", {})
    
    # If album is not restricted, everyone has access
    if not restrictions or not restrictions.get("is_restricted", False):
        return True
    
    # Check patron tier access
    if user.is_patreon and user.patreon_tier_data:
        user_amount = user.patreon_tier_data.get("amount_cents", 0)
        minimum_tier_amount = restrictions.get("minimum_tier_amount", 0)
        
        # User has access if their tier amount is >= the minimum required
        return user_amount >= minimum_tier_amount
    
    return False    
from auth import router as auth_router
app.include_router(auth_router)

def get_media_file_path(file_path: str, media_type: str) -> Path:
    """
    Get the correct file path for media files.
    
    Args:
        file_path (str): The stored file path from the album data
        media_type (str): Type of media ('audio' or 'cover')
        
    Returns:
        Path: The correct Path object for the file
    """
    # Extract just the filename from the path
    filename = Path(file_path).name
    
    # Determine the correct directory
    if media_type == 'audio':
        return AUDIO_DIR / filename
    elif media_type == 'cover':
        return COVERS_DIR / filename
    else:
        raise ValueError(f"Invalid media type: {media_type}")






@app.post("/api/creator/sync/manual")
@verify_role_permission(["creator", "team"])
async def manual_sync(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    try:
        creator_id = current_user.id if current_user.is_creator else current_user.created_by
        logger.info(f"Starting manual sync for creator_id: {creator_id}")
        
        updated_tiers = await patreon_sync_service.perform_manual_sync(creator_id, db)
        
        return {
            "status": "success",
            "message": "Manual sync completed successfully",
            "sync_time": datetime.now(timezone.utc).isoformat(),
            "tiers": updated_tiers
        }
        
    except Exception as e:
        logger.error(f"Manual sync error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error during manual sync: {str(e)}"
        )

        
async def add_to_my_albums(
    album_id: str,  # Changed to str for UUID
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Add an album to user's collection"""
    try:
        # Check if already added
        existing = db.query(UserAlbumManagement).filter(
            UserAlbumManagement.user_id == current_user.id,
            UserAlbumManagement.album_id == album_id
        ).first()

        if existing:
            raise HTTPException(status_code=400, detail="Album already in your collection")

        # Verify album exists
        album = db.query(Album).filter(Album.id == album_id).first()
        if not album:
            raise HTTPException(status_code=404, detail="Album not found")

        # Add album to collection
        user_album = UserAlbumManagement(
            user_id=current_user.id,
            album_id=album_id,
            is_favorite=False,
            view_count=0,
            last_viewed=datetime.now(timezone.utc)
        )
        db.add(user_album)
        
        # Create audit log
        AuditLog.log_change(
            db=db,
            user_id=current_user.id,
            action_type=AuditLogType.CREATE,
            table_name="user_album_management",
            record_id=album_id,
            description=f"Added album '{album.title}' to collection"
        )
        
        db.commit()

        return {
            "status": "success", 
            "message": "Album added to your collection",
            "album_id": album_id
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error adding album to collection: {str(e)}")
        raise HTTPException(status_code=500, detail="Error adding album")

@app.get("/my-albums")
async def my_albums_page(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Show user's collected albums with track information"""
    try:
        # Get user's album management records first
        user_albums = (
            db.query(UserAlbumManagement)
            .filter(UserAlbumManagement.user_id == current_user.id)
            .order_by(
                UserAlbumManagement.is_favorite.desc(),
                UserAlbumManagement.last_viewed.desc()
            )
            .all()
        )
        
        # Get album IDs
        album_ids = [str(ua.album_id) for ua in user_albums]
        
        # Get albums with tracks in one efficient query using joinedload
        albums = (
            db.query(Album)
            .options(joinedload(Album.tracks))  # Eager load tracks
            .filter(Album.id.in_([UUID(aid) for aid in album_ids]))
            .all()
        )
        
        # Create album lookup
        album_lookup = {str(album.id): album for album in albums}
        
        # Build collection
        collection = []
        for user_album in user_albums:
            album = album_lookup.get(str(user_album.album_id))
            if album:
                album_data = {
                    "id": str(album.id),
                    "title": album.title,
                    "cover_path": album.cover_path or '/static/images/default-album.jpg',
                    "created_at": album.created_at,
                    "created_by": str(album.created_by_id),
                    "is_favorite": user_album.is_favorite,
                    "last_viewed": user_album.last_viewed,
                    "view_count": user_album.view_count or 0,
                    "added_at": user_album.created_at,
                    "tracks": [  # Add track information
                        {
                            "id": str(track.id),
                            "title": track.title,
                        } 
                        for track in album.tracks
                    ] if album.tracks else []
                }
                collection.append(album_data)
                
        # Get favorite count
        favorite_count = sum(1 for album in collection if album["is_favorite"])
        
        return templates.TemplateResponse(
            "my_albums.html",
            {
                "request": request,
                "user": current_user,
                "albums": collection,
                "total_count": len(collection),
                "favorite_count": favorite_count,
                "permissions": get_user_permissions(current_user)
            }
        )
    except Exception as e:
        logger.error(f"Error loading my albums: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error loading my albums")

@app.get("/continue-listening")
async def continue_listening_page(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Show user's in-progress tracks (continue listening page)"""
    try:
        from datetime import datetime

        # Get in-progress tracks (limit 10)
        tracks = await get_in_progress_tracks_from_router(limit=10, current_user=current_user, db=db)

        # Format last_played for display
        for track in tracks:
            if 'last_played' in track and track['last_played']:
                try:
                    last_played_dt = datetime.fromisoformat(track['last_played'])
                    # Format as "Month Day, Year at HH:MM AM/PM"
                    track['last_played_formatted'] = last_played_dt.strftime('%B %d, %Y at %I:%M %p')
                except:
                    track['last_played_formatted'] = 'Recently'
            else:
                track['last_played_formatted'] = 'Recently'

        return templates.TemplateResponse(
            "continue_listening.html",
            {
                "request": request,
                "user": current_user,
                "tracks": tracks,
                "permissions": get_user_permissions(current_user)
            }
        )
    except Exception as e:
        logger.error(f"Error loading continue listening page: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error loading continue listening page")

@app.get("/api/my-albums")
async def get_my_albums_api(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """API endpoint to return user's albums as JSON for SPA"""
    try:
        # Get user's album management records
        user_albums = (
            db.query(UserAlbumManagement)
            .filter(UserAlbumManagement.user_id == current_user.id)
            .order_by(
                UserAlbumManagement.is_favorite.desc(),
                UserAlbumManagement.last_viewed.desc()
            )
            .all()
        )
        
        # Get album IDs
        album_ids = [str(ua.album_id) for ua in user_albums]
        
        # Get albums with tracks
        albums = (
            db.query(Album)
            .options(joinedload(Album.tracks))
            .filter(Album.id.in_([UUID(aid) for aid in album_ids]))
            .all()
        )
        
        # Create album lookup
        album_lookup = {str(album.id): album for album in albums}
        
        # Build collection
        collection = []
        for user_album in user_albums:
            album = album_lookup.get(str(user_album.album_id))
            if album:
                album_data = {
                    "id": str(album.id),
                    "title": album.title,
                    "cover_path": album.cover_path or '/static/images/default-album.jpg',
                    "is_favorite": user_album.is_favorite,
                    "last_viewed": user_album.last_viewed.isoformat() if user_album.last_viewed else None,
                    "view_count": user_album.view_count or 0,
                    "added_at": user_album.created_at.isoformat() if user_album.created_at else None,
                    "track_count": len(album.tracks) if album.tracks else 0
                }
                collection.append(album_data)
        
        return collection
        
    except Exception as e:
        logger.error(f"Error loading my albums API: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error loading albums")

@app.get("/api/continue-listening")
async def get_continue_listening_api(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """API endpoint to return user's in-progress tracks as JSON for SPA"""
    try:
        from datetime import datetime

        # Get in-progress tracks (limit 10)
        tracks = await get_in_progress_tracks_from_router(limit=10, current_user=current_user, db=db)

        # Format last_played for display
        for track in tracks:
            if 'last_played' in track and track['last_played']:
                try:
                    last_played_dt = datetime.fromisoformat(track['last_played'])
                    track['last_played_formatted'] = last_played_dt.strftime('%B %d, %Y at %I:%M %p')
                except:
                    track['last_played_formatted'] = 'Recently'
            else:
                track['last_played_formatted'] = 'Recently'

        return tracks

    except Exception as e:
        logger.error(f"Error loading continue listening API: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error loading continue listening")

@app.post("/api/my-albums/{album_id}/favorite")
async def toggle_favorite_album(
    album_id: str,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Toggle album favorite status using database"""
    try:
        # Convert string to UUID
        album_uuid = UUID(album_id)
        
        # Verify album exists
        album = db.query(Album).filter(Album.id == album_uuid).first()
        if not album:
            raise HTTPException(status_code=404, detail="Album not found")

        # Get existing user album record
        user_album = db.query(UserAlbumManagement).filter(
            and_(
                UserAlbumManagement.user_id == current_user.id,
                UserAlbumManagement.album_id == str(album_uuid)
            )
        ).first()

        now = datetime.now(timezone.utc)

        if not user_album:
            # Add new album to collection as favorite
            user_album = UserAlbumManagement(
                user_id=current_user.id,
                album_id=str(album_uuid),
                is_favorite=True,
                view_count=0,
                last_viewed=now,
                created_at=now,
                updated_at=now
            )
            db.add(user_album)
            action = "added to"
            is_favorite = True
        else:
            # Toggle existing album's favorite status
            user_album.is_favorite = not user_album.is_favorite
            user_album.updated_at = now
            action = "added to" if user_album.is_favorite else "removed from"
            is_favorite = user_album.is_favorite

            # If unfavorited and has no other data, remove the record entirely
            if not is_favorite and user_album.view_count == 0:
                db.delete(user_album)
                logger.info(f"Removed album management record for {album_id}")

        db.commit()
        
        logger.info(f"Album {album_id} {action} favorites for user {current_user.id}")

        return {
            "status": "success",
            "message": f"Album {action} favorites",
            "album_id": album_id,
            "is_favorite": is_favorite
        }

    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid album ID format")
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error toggling favorite status: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error updating favorites: {str(e)}"
        )
        
        
@app.delete("/api/my-albums/{album_id}")
async def remove_from_my_albums(
    album_id: str,  # Using str for UUID
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Remove an album from user's collection"""
    try:
        # Explicitly define the conditions for the query
        user_album = db.query(UserAlbumManagement).filter(
            and_(
                UserAlbumManagement.user_id == current_user.id,
                UserAlbumManagement.album_id == str(album_id)  # Ensure album_id is string
            )
        ).first()

        if not user_album:
            raise HTTPException(
                status_code=404, 
                detail="Album not found in your collection"
            )

        # Perform the deletion
        db.delete(user_album)
        db.commit()

        return {
            "status": "success",
            "message": "Album removed from your collection",
            "album_id": str(album_id)
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error removing album from collection: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Error removing album from collection"
        )

        
@app.get("/api/home/data")
async def get_home_data(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """
    Get all home page data in one API call for SPA
    Returns user's albums and continue listening tracks
    """
    try:
        logger.info(f"Loading home data for user: {current_user.email}")
        
        # Verify active session
        session = db.query(UserSession).filter(
            and_(
                UserSession.user_id == current_user.id,
                UserSession.session_id == request.cookies.get("session_id"),
                UserSession.is_active == True
            )
        ).first()
        
        if not session:
            logger.warning("No active session found")
            raise HTTPException(status_code=401, detail="No active session")
        
        # Get user's albums with tracks (eager loading for performance)
        user_albums = db.query(UserAlbumManagement).filter(
            UserAlbumManagement.user_id == current_user.id
        ).order_by(
            UserAlbumManagement.created_at.desc()
        ).limit(25).all()
        
        my_albums = []
        if user_albums:
            album_ids = [ua.album_id for ua in user_albums]
            
            # Fetch albums with tracks in one efficient query
            albums = (
                db.query(Album)
                .options(joinedload(Album.tracks))  # Eager load tracks
                .filter(Album.id.in_(album_ids))
                .all()
            )
            
            # Create lookup dictionary for O(1) access
            album_dict = {str(album.id): album for album in albums}
            
            # Build response maintaining user's order
            for ua in user_albums:
                album = album_dict.get(str(ua.album_id))
                if album:
                    my_albums.append({
                        'id': str(album.id),
                        'title': album.title,
                        'cover_path': album.cover_path or DEFAULT_COVER_URL,
                        'track_count': len(album.tracks) if album.tracks else 0,
                        'added_at': ua.created_at.isoformat() if ua.created_at else None,
                        'last_viewed': ua.last_viewed.isoformat() if ua.last_viewed else None,
                        'view_count': ua.view_count,
                        'is_favorite': ua.is_favorite,
                        'in_collection': True
                    })

        # Get total count of in-progress tracks
        total_in_progress = db.query(PlaybackProgress).filter(
            PlaybackProgress.user_id == current_user.id,
            PlaybackProgress.completed == False,
            PlaybackProgress.position > 0
        ).count()

        # Get in-progress tracks (only 2 for home page preview)
        continue_listening = await get_in_progress_tracks_from_router(limit=2, current_user=current_user, db=db)

        return {
            "success": True,
            "data": {
                "my_albums": my_albums,
                "continue_listening": continue_listening,
                "total_in_progress": total_in_progress,
                "show_all_sections": True,
                "user": {
                    "id": current_user.id,
                    "username": current_user.username,
                    "email": current_user.email,
                    "is_creator": current_user.is_creator,
                    "is_team": current_user.is_team,
                    "is_patreon": current_user.is_patreon
                }
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error loading home data: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error loading home data")


@app.get("/catalog")
async def album_catalog(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Album directory page with alphabetical sorting and filtering"""
    try:
        # Get creator ID based on user type
        creator_id = current_user.id if current_user.is_creator else current_user.created_by
        
        # Get all albums from database
        albums = db.query(Album).filter(
            Album.created_by_id == creator_id
        ).order_by(Album.title).all()
        
        # Group albums by first letter - include # for numbers/symbols
        albums_by_letter = {}
        # Add # category first for numbers and symbols
        albums_by_letter['#'] = []
        # Then add A-Z categories
        for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
            albums_by_letter[letter] = []
            
        for album in albums:
            if album.title:  # Skip albums without titles
                first_char = album.title[0].upper()
                
                # Determine which category this album belongs to
                if first_char.isalpha() and first_char in albums_by_letter:
                    # Regular A-Z letter
                    category = first_char
                else:
                    # Numbers, symbols, or any non-A-Z characters go to #
                    category = '#'
                
                album_dict = {
                    "id": str(album.id),
                    "title": album.title,
                    "cover_path": album.cover_path or '/media/images/default-album.jpg',
                    "tier_restrictions": album.tier_restrictions,
                    "created_at": album.created_at.isoformat() if album.created_at else None,
                    "updated_at": album.updated_at.isoformat() if album.updated_at else None
                }
                albums_by_letter[category].append(album_dict)
            
        # Sort albums within each letter/category
        for letter in albums_by_letter:
            albums_by_letter[letter].sort(key=lambda x: x['title'].upper())
            
        return templates.TemplateResponse(
            "catalog.html",
            {
                "request": request,
                "user": current_user,
                "albums_by_letter": albums_by_letter,
                "permissions": get_user_permissions(current_user)
            }
        )
        
    except Exception as e:
        logger.error(f"Error loading album catalog: {str(e)}")
        raise HTTPException(status_code=500, detail="Error loading album catalog")
@app.get("/api/catalog")
async def api_album_catalog(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """API endpoint for catalog data to be used by SPA"""
    try:
        # Get creator ID based on user type
        creator_id = current_user.id if current_user.is_creator else current_user.created_by
        
        # Get all albums from database
        albums = db.query(Album).filter(
            Album.created_by_id == creator_id
        ).order_by(Album.title).all()
        
        # Group albums by first letter - include # for numbers/symbols
        albums_by_letter = {}
        # Add # category first for numbers and symbols
        albums_by_letter['#'] = []
        # Then add A-Z categories
        for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
            albums_by_letter[letter] = []
            
        for album in albums:
            if album.title:  # Skip albums without titles
                first_char = album.title[0].upper()
                
                # Determine which category this album belongs to
                if first_char.isalpha() and first_char in albums_by_letter:
                    # Regular A-Z letter
                    category = first_char
                else:
                    # Numbers, symbols, or any non-A-Z characters go to #
                    category = '#'
                
                album_dict = {
                    "id": str(album.id),
                    "title": album.title,
                    "cover_path": album.cover_path or '/media/images/default-album.jpg',
                    "tier_restrictions": album.tier_restrictions,
                    "created_at": album.created_at.isoformat() if album.created_at else None,
                    "updated_at": album.updated_at.isoformat() if album.updated_at else None
                }
                albums_by_letter[category].append(album_dict)
            
        # Sort albums within each letter/category
        for letter in albums_by_letter:
            albums_by_letter[letter].sort(key=lambda x: x['title'].upper())
            
        return {
            "albums_by_letter": albums_by_letter,
            "user": {
                "id": current_user.id,
                "username": current_user.username,
                "is_creator": current_user.is_creator,
                "is_team": current_user.is_team
            },
            "permissions": get_user_permissions(current_user)
        }
        
    except Exception as e:
        logger.error(f"Error loading album catalog API: {str(e)}")
        raise HTTPException(status_code=500, detail="Error loading album catalog")
@app.post("/api/sessions/active")
async def track_active_session(
    request: Request,
    data: dict,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Track user activity without affecting session expiration"""
    try:
        now = datetime.now(timezone.utc)
        session_id = request.cookies.get("session_id")
        
        if not session_id:
            raise HTTPException(status_code=401, detail="No session found")
        
        # Get current session
        user_session = db.query(UserSession).filter(
            UserSession.session_id == session_id
        ).first()
        
        if not user_session:
            raise HTTPException(status_code=401, detail="Invalid session")
        
        # Only update last_active, don't touch is_active or expires_at
        user_session.last_active = now
        db.commit()
        
        # Get currently online users (active in last 5 minutes)
        online_sessions = db.query(UserSession).join(User).filter(
            UserSession.last_active > now - timedelta(minutes=5)
        ).all()
        
        # Count unique users per role
        stats = {
            'creator': {'total': 0},
            'team': {'total': 0},
            'patron': {'total': 0},
            'kofi': {'total': 0},
            'guest': {'total': 0, 'active': 0, 'expired': 0}  # ✅ Added guest tracking
        }
        seen_users = set()
        
        for session in online_sessions:
            if session.user_id not in seen_users:
                if session.user.is_creator:
                    stats['creator']['total'] += 1
                elif session.user.is_team:
                    stats['team']['total'] += 1
                elif session.user.is_patreon:
                    stats['patron']['total'] += 1
                elif session.user.is_kofi:
                    stats['kofi']['total'] += 1
                elif session.user.role == UserRole.GUEST and session.user.is_guest_trial:  # ✅ Added guest condition
                    stats['guest']['total'] += 1
                    # Check if trial is active or expired
                    if session.user.trial_active:
                        stats['guest']['active'] += 1
                    else:
                        stats['guest']['expired'] += 1
                seen_users.add(session.user_id)
        
        return {
            'status': 'success',
            'stats': stats,
            'total_active': len(seen_users),
            'message': "Activity tracked successfully"
        }
    except Exception as e:
        logger.error(f"Error tracking activity: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sessions/stats")
async def get_session_stats(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get currently online users based on recent activity"""
    try:
        now = datetime.now(timezone.utc)
        
        # Get active sessions with recent activity
        online_sessions = db.query(UserSession).join(User).filter(
            and_(
                UserSession.last_active > now - timedelta(minutes=5),
                UserSession.is_active == True  # ✅ This was the key fix
            )
        ).all()
        
        stats = {
            'creator': {'total': 0},
            'team': {'total': 0},
            'patron': {'total': 0},
            'kofi': {'total': 0},
            'guest': {'total': 0, 'active': 0, 'expired': 0}
        }
        seen_users = set()
        
        for session in online_sessions:
            if session.user_id not in seen_users:
                if session.user.is_creator:
                    stats['creator']['total'] += 1
                elif session.user.is_team:
                    stats['team']['total'] += 1
                elif session.user.is_patreon:
                    stats['patron']['total'] += 1
                elif session.user.is_kofi:
                    stats['kofi']['total'] += 1
                elif session.user.role == UserRole.GUEST and session.user.is_guest_trial:
                    stats['guest']['total'] += 1
                    if session.user.trial_active:
                        stats['guest']['active'] += 1
                    else:
                        stats['guest']['expired'] += 1
                seen_users.add(session.user_id)
        
        return {
            'status': 'success',
            'stats': stats,
            'total_active': len(seen_users)
        }
    except Exception as e:
        logger.error(f"Error getting session stats: {str(e)}")
        return {
            'status': 'error',
            'message': str(e)
        }


@app.get("/api/my-benefits")
async def api_my_benefits(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """API endpoint that returns benefits data as JSON for SPA"""
    try:
        logger.info(f"===== API: LOADING BENEFITS FOR: {current_user.email} =====")
        
        # ✅ Define UNLIMITED_VALUE constant
        UNLIMITED_VALUE = 999999999  # or float('inf') but that's harder to serialize
        
        # Get download info
        download_info = await get_user_downloads(current_user, db)
        logger.info(f"Download info: {json.dumps(download_info, indent=2)}")
        
        # Get book request info (now includes chapters)
        book_request_info = await get_user_book_request_quota(current_user, db)
        logger.info(f"Book request info: {json.dumps(book_request_info, indent=2)}")
        
        # Handle creator benefits
        if current_user.is_creator:
            logger.info("Processing benefits for Creator user")
            benefits_info = {
                'tier_title': 'Creator',
                'album_downloads': {
                    'downloads_allowed': UNLIMITED_VALUE,
                    'downloads_used': download_info['albums']['downloads_used'],
                    'downloads_remaining': UNLIMITED_VALUE
                },
                'track_downloads': {
                    'downloads_allowed': UNLIMITED_VALUE,
                    'downloads_used': download_info['tracks']['downloads_used'],
                    'downloads_remaining': UNLIMITED_VALUE
                },
                'book_requests': {
                    'requests_allowed': UNLIMITED_VALUE,
                    'requests_used': book_request_info['requests_used'],
                    'requests_remaining': UNLIMITED_VALUE,
                    'chapters_allowed_per_book_request': UNLIMITED_VALUE
                },
                'chapters_allowed': UNLIMITED_VALUE,
                'is_unlimited': True,
                'tier_info': {
                    'description': 'Creator account with unlimited access'
                }
            }
        
        # Handle Patreon or Ko-fi users
        elif current_user.is_patreon or current_user.is_kofi:
            user_type = "Patreon" if current_user.is_patreon else "Ko-fi"
            logger.info(f"Processing benefits for {user_type} user")
            period_start = None
            next_reset = None
            
            if current_user.patreon_tier_data:
                logger.info(f"User tier data: {json.dumps(current_user.patreon_tier_data, indent=2)}")
                
                # Handle next reset date
                if current_user.patreon_tier_data.get('next_charge_date'):
                    try:
                        next_charge_str = current_user.patreon_tier_data['next_charge_date']
                        next_reset = datetime.fromisoformat(next_charge_str.replace('Z', '+00:00'))
                        logger.info(f"Using next_charge_date for reset: {next_reset}")
                    except Exception as e:
                        logger.error(f"Error parsing next_charge_date: {e}")
                elif current_user.patreon_tier_data.get('expires_at'):
                    try:
                        expires_str = current_user.patreon_tier_data['expires_at']
                        next_reset = datetime.fromisoformat(expires_str.replace('Z', '+00:00'))
                        logger.info(f"Using expires_at for Ko-fi reset: {next_reset}")
                    except Exception as e:
                        logger.error(f"Error parsing expires_at: {e}")
                
                # Handle period start date
                if current_user.patreon_tier_data.get('last_charge_date'):
                    try:
                        last_charge_str = current_user.patreon_tier_data['last_charge_date']
                        period_start = datetime.fromisoformat(last_charge_str.replace('Z', '+00:00'))
                        logger.info(f"Using last_charge_date for period_start: {period_start}")
                    except Exception as e:
                        logger.error(f"Error parsing last_charge_date: {e}")
                elif current_user.patreon_tier_data.get('last_payment_date'):
                    try:
                        payment_str = current_user.patreon_tier_data['last_payment_date']
                        period_start = datetime.fromisoformat(payment_str.replace('Z', '+00:00'))
                        logger.info(f"Using last_payment_date for period_start: {period_start}")
                    except Exception as e:
                        logger.error(f"Error parsing last_payment_date: {e}")
                elif current_user.patreon_tier_data.get('period_start'):
                    try:
                        period_start_str = current_user.patreon_tier_data['period_start']
                        period_start = datetime.fromisoformat(period_start_str.replace('Z', '+00:00'))
                        logger.info(f"Using period_start as fallback: {period_start}")
                        if not next_reset:
                            next_reset = period_start + relativedelta(months=1)
                            logger.info(f"Calculated next_reset from period_start: {next_reset}")
                    except Exception as e:
                        logger.error(f"Error parsing period_start: {e}")
            
            service_type = "Ko-fi" if current_user.is_kofi else "Patreon"
            benefits_info = {
                'tier_title': current_user.patreon_tier_data.get('title', f'Unknown {service_type} Tier'),
                'album_downloads': download_info['albums'],
                'track_downloads': download_info['tracks'],
                'book_requests': {
                    'requests_allowed': book_request_info['requests_allowed'],
                    'requests_used': book_request_info['requests_used'],
                    'requests_remaining': book_request_info['requests_remaining'],
                    'chapters_allowed_per_book_request': book_request_info.get('chapters_allowed_per_book_request', 0)
                },
                'chapters_allowed': book_request_info.get('chapters_allowed_per_book_request', 0),
                'period_start': period_start.isoformat() if period_start else None,
                'next_reset': next_reset.isoformat() if next_reset else None,
                'is_unlimited': False,
                'tier_info': {
                    'amount_cents': current_user.patreon_tier_data.get('amount_cents', 0),
                    'patron_status': current_user.patreon_tier_data.get('patron_status'),
                    'last_charge_status': current_user.patreon_tier_data.get('last_charge_status'),
                    'description': current_user.patreon_tier_data.get('tier_description', ''),
                    'service_type': service_type
                },
                'grace_period_active': False
            }
            
            # Add grace period message based on current time and grace_period_ends_at
            now = datetime.now(timezone.utc)
            if current_user.grace_period_ends_at and current_user.patreon_tier_data.get('patron_status') != 'active_patron':
                if now <= current_user.grace_period_ends_at:
                    # Calculate time remaining
                    time_diff = current_user.grace_period_ends_at - now
                    days_remaining = time_diff.days
                    hours_remaining = int(time_diff.total_seconds() // 3600)
                    
                    if days_remaining > 0:
                        time_remaining = f"{days_remaining} day{'s' if days_remaining != 1 else ''}"
                    else:
                        time_remaining = f"{hours_remaining} hour{'s' if hours_remaining != 1 else ''}"
                    
                    grace_msg = (
                        f"Your subscription expired, but you're on a grace period until "
                        f"{current_user.grace_period_ends_at.strftime('%B %d, %Y %I:%M %p')}. "
                        f"You have {time_remaining} remaining. Please renew soon to avoid interruption."
                    )
                    benefits_info["grace_period_message"] = grace_msg
                    benefits_info["grace_period_active"] = True
                    benefits_info["grace_period_ends_at"] = current_user.grace_period_ends_at.isoformat()
                    benefits_info["grace_period_time_remaining"] = time_remaining
                else:
                    grace_msg = (
                        "Your grace period has expired. Please renew your subscription to maintain access."
                    )
                    benefits_info["grace_period_message"] = grace_msg
                    benefits_info["grace_period_active"] = False
        
        # Handle team members
        elif current_user.is_team:
            logger.info("Processing benefits for Team member")
            period_start = None
            next_reset = None
            if current_user.patreon_tier_data:
                logger.info(f"Team member tier data: {json.dumps(current_user.patreon_tier_data, indent=2)}")
                if current_user.patreon_tier_data.get('period_start'):
                    try:
                        period_start_str = current_user.patreon_tier_data['period_start']
                        period_start = datetime.fromisoformat(period_start_str.replace('Z', '+00:00'))
                        logger.info(f"Using period_start for team member: {period_start}")
                        next_reset = period_start + relativedelta(months=1)
                        logger.info(f"Calculated next_reset for team member: {next_reset}")
                    except Exception as e:
                        logger.error(f"Error parsing team member period_start: {e}")
            
            benefits_info = {
                'tier_title': 'Team Member',
                'album_downloads': download_info['albums'],
                'track_downloads': download_info['tracks'],
                'book_requests': {
                    'requests_allowed': book_request_info['requests_allowed'],
                    'requests_used': book_request_info['requests_used'],
                    'requests_remaining': book_request_info['requests_remaining'],
                    'chapters_allowed_per_book_request': book_request_info.get('chapters_allowed_per_book_request', 0)
                },
                'chapters_allowed': book_request_info.get('chapters_allowed_per_book_request', 0),
                'period_start': period_start.isoformat() if period_start else None,
                'next_reset': next_reset.isoformat() if next_reset else None,
                'is_unlimited': False,
                'tier_info': {
                    'description': 'Team member account',
                    'service_type': 'Team'
                }
            }
        
        # Handle guest trial users
        elif current_user.is_guest_trial and current_user.role == UserRole.GUEST:
            logger.info("Processing benefits for GUEST TRIAL user")
            
            # Check if trial is still active
            if not current_user.trial_active:
                logger.info(f"Guest trial expired for {current_user.email}")
                benefits_info = {
                    'tier_title': 'Expired Guest Trial',
                    'album_downloads': {
                        'downloads_allowed': 0,
                        'downloads_used': download_info['albums']['downloads_used'],
                        'downloads_remaining': 0
                    },
                    'track_downloads': {
                        'downloads_allowed': 0,
                        'downloads_used': download_info['tracks']['downloads_used'],
                        'downloads_remaining': 0
                    },
                    'book_requests': {
                        'requests_allowed': 0,
                        'requests_used': book_request_info['requests_used'],
                        'requests_remaining': 0,
                        'chapters_allowed_per_book_request': 0
                    },
                    'chapters_allowed': 0,
                    'trial_expired': True,
                    'trial_started_at': current_user.trial_started_at.isoformat() if current_user.trial_started_at else None,
                    'trial_expires_at': current_user.trial_expires_at.isoformat() if current_user.trial_expires_at else None,
                    'is_unlimited': False,
                    'tier_info': {
                        'description': 'Guest trial has expired',
                        'service_type': 'Guest Trial'
                    }
                }
            else:
                # Active trial
                from models import UserTier, CampaignTier
                
                user_tier = db.query(UserTier).filter(
                    and_(
                        UserTier.user_id == current_user.id,
                        UserTier.is_active == True
                    )
                ).first()
                
                if user_tier:
                    tier = db.query(CampaignTier).filter(
                        CampaignTier.id == user_tier.tier_id
                    ).first()
                    
                    if tier and tier.title == "Guest Trial":
                        logger.info(f"Found Guest Trial tier: {tier.title}")
                        
                        # Calculate time remaining
                        time_remaining = current_user.trial_expires_at - datetime.now(timezone.utc)
                        hours_remaining = int(time_remaining.total_seconds() / 3600)
                        
                        benefits_info = {
                            'tier_title': 'Guest Trial',
                            'album_downloads': {
                                'downloads_allowed': tier.album_downloads_allowed,
                                'downloads_used': download_info['albums']['downloads_used'],
                                'downloads_remaining': tier.album_downloads_allowed - download_info['albums']['downloads_used']
                            },
                            'track_downloads': {
                                'downloads_allowed': tier.track_downloads_allowed,
                                'downloads_used': download_info['tracks']['downloads_used'],
                                'downloads_remaining': tier.track_downloads_allowed - download_info['tracks']['downloads_used']
                            },
                            'book_requests': {
                                'requests_allowed': tier.book_requests_allowed,
                                'requests_used': book_request_info['requests_used'],
                                'requests_remaining': tier.book_requests_allowed - book_request_info['requests_used'],
                                'chapters_allowed_per_book_request': getattr(tier, 'chapters_allowed_per_book_request', 0)
                            },
                            'chapters_allowed': getattr(tier, 'chapters_allowed_per_book_request', 0),
                            'trial_active': True,
                            'trial_started_at': current_user.trial_started_at.isoformat() if current_user.trial_started_at else None,
                            'trial_expires_at': current_user.trial_expires_at.isoformat() if current_user.trial_expires_at else None,
                            'trial_hours_remaining': max(0, hours_remaining),
                            'is_unlimited': False,
                            'tier_info': {
                                'description': f'Guest trial - {hours_remaining} hours remaining',
                                'service_type': 'Guest Trial'
                            }
                        }
                    else:
                        # Fallback
                        benefits_info = {
                            'tier_title': 'Guest Trial (No Tier)',
                            'album_downloads': download_info['albums'],
                            'track_downloads': download_info['tracks'],
                            'book_requests': {
                                'requests_allowed': 0,
                                'requests_used': 0,
                                'requests_remaining': 0,
                                'chapters_allowed_per_book_request': 0
                            },
                            'chapters_allowed': 0,
                            'trial_started_at': current_user.trial_started_at.isoformat() if current_user.trial_started_at else None,
                            'is_unlimited': False,
                            'tier_info': {
                                'description': 'Guest trial - no tier association found',
                                'service_type': 'Guest Trial'
                            }
                        }
                else:
                    # No UserTier association
                    benefits_info = {
                        'tier_title': 'Guest Trial (No Association)',
                        'album_downloads': download_info['albums'],
                        'track_downloads': download_info['tracks'],
                        'book_requests': {
                            'requests_allowed': 0,
                            'requests_used': 0,
                            'requests_remaining': 0,
                            'chapters_allowed_per_book_request': 0
                        },
                        'chapters_allowed': 0,
                        'trial_started_at': current_user.trial_started_at.isoformat() if current_user.trial_started_at else None,
                        'is_unlimited': False,
                        'tier_info': {
                            'description': 'Guest trial - no tier association',
                            'service_type': 'Guest Trial'
                        }
                    }
        
        # Handle regular users
        else:
            logger.info("Processing benefits for regular user")
            benefits_info = {
                'tier_title': 'Free User',
                'album_downloads': download_info['albums'],
                'track_downloads': download_info['tracks'],
                'book_requests': {
                    'requests_allowed': 0,
                    'requests_used': 0,
                    'requests_remaining': 0,
                    'chapters_allowed_per_book_request': 0
                },
                'chapters_allowed': 0,
                'is_unlimited': False,
                'tier_info': {
                    'description': 'Free user account'
                }
            }
        
        logger.info(f"Final benefits info: {json.dumps(benefits_info, default=str)}")
        
        # ✅ Return JSON response
        return JSONResponse(content=benefits_info)
        
    except Exception as e:
        logger.error(f"Error in API my-benefits: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error loading benefits")
@app.get("/my-benefits")
async def my_benefits_page(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Display user benefits and download allowances with complete tier information"""
    try:
        logger.info(f"===== LOADING BENEFITS FOR: {current_user.email} =====")
        
        # Get download info
        download_info = await get_user_downloads(current_user, db)
        logger.info(f"Download info: {json.dumps(download_info, indent=2)}")
        
        # Get book request info (now includes chapters)
        book_request_info = await get_user_book_request_quota(current_user, db)
        logger.info(f"Book request info: {json.dumps(book_request_info, indent=2)}")
        
        # Handle creator benefits
        if current_user.is_creator:
            logger.info("Processing benefits for Creator user")
            benefits_info = {
                'tier_title': 'Creator',
                'album_downloads': {
                    'downloads_allowed': float('inf'),
                    'downloads_used': download_info['albums']['downloads_used'],
                    'downloads_remaining': float('inf')
                },
                'track_downloads': {
                    'downloads_allowed': float('inf'),
                    'downloads_used': download_info['tracks']['downloads_used'],
                    'downloads_remaining': float('inf')
                },
                'book_requests': {
                    'requests_allowed': float('inf'),
                    'requests_used': book_request_info['requests_used'],
                    'requests_remaining': float('inf'),
                    'chapters_allowed_per_book_request': float('inf')
                },
                'chapters_allowed': float('inf'),  # Add this line
                'is_unlimited': True,
                'tier_info': {
                    'description': 'Creator account with unlimited access'
                }
            }
        
        # Handle Patreon or Ko-fi users
        elif current_user.is_patreon or current_user.is_kofi:
            user_type = "Patreon" if current_user.is_patreon else "Ko-fi"
            logger.info(f"Processing benefits for {user_type} user")
            period_start = None
            next_reset = None
            
            if current_user.patreon_tier_data:
                logger.info(f"User tier data: {json.dumps(current_user.patreon_tier_data, indent=2)}")
                
                # Handle next reset date
                if current_user.patreon_tier_data.get('next_charge_date'):
                    try:
                        next_charge_str = current_user.patreon_tier_data['next_charge_date']
                        next_reset = datetime.fromisoformat(next_charge_str.replace('Z', '+00:00'))
                        logger.info(f"Using next_charge_date for reset: {next_reset}")
                    except Exception as e:
                        logger.error(f"Error parsing next_charge_date: {e}")
                elif current_user.patreon_tier_data.get('expires_at'):
                    try:
                        expires_str = current_user.patreon_tier_data['expires_at']
                        next_reset = datetime.fromisoformat(expires_str.replace('Z', '+00:00'))
                        logger.info(f"Using expires_at for Ko-fi reset: {next_reset}")
                    except Exception as e:
                        logger.error(f"Error parsing expires_at: {e}")
                
                # Handle period start date
                if current_user.patreon_tier_data.get('last_charge_date'):
                    try:
                        last_charge_str = current_user.patreon_tier_data['last_charge_date']
                        period_start = datetime.fromisoformat(last_charge_str.replace('Z', '+00:00'))
                        logger.info(f"Using last_charge_date for period_start: {period_start}")
                    except Exception as e:
                        logger.error(f"Error parsing last_charge_date: {e}")
                elif current_user.patreon_tier_data.get('last_payment_date'):
                    try:
                        payment_str = current_user.patreon_tier_data['last_payment_date']
                        period_start = datetime.fromisoformat(payment_str.replace('Z', '+00:00'))
                        logger.info(f"Using last_payment_date for period_start: {period_start}")
                    except Exception as e:
                        logger.error(f"Error parsing last_payment_date: {e}")
                elif current_user.patreon_tier_data.get('period_start'):
                    try:
                        period_start_str = current_user.patreon_tier_data['period_start']
                        period_start = datetime.fromisoformat(period_start_str.replace('Z', '+00:00'))
                        logger.info(f"Using period_start as fallback: {period_start}")
                        if not next_reset:
                            next_reset = period_start + relativedelta(months=1)
                            logger.info(f"Calculated next_reset from period_start: {next_reset}")
                    except Exception as e:
                        logger.error(f"Error parsing period_start: {e}")
            
            service_type = "Ko-fi" if current_user.is_kofi else "Patreon"
            benefits_info = {
                'tier_title': current_user.patreon_tier_data.get('title', f'Unknown {service_type} Tier'),
                'album_downloads': download_info['albums'],
                'track_downloads': download_info['tracks'],
                'book_requests': {
                    'requests_allowed': book_request_info['requests_allowed'],
                    'requests_used': book_request_info['requests_used'],
                    'requests_remaining': book_request_info['requests_remaining'],
                    'chapters_allowed_per_book_request': book_request_info.get('chapters_allowed_per_book_request', 0)
                },
                'chapters_allowed': book_request_info.get('chapters_allowed_per_book_request', 0),  # Add this line
                'period_start': period_start,
                'next_reset': next_reset,
                'is_unlimited': False,
                'tier_info': {
                    'amount_cents': current_user.patreon_tier_data.get('amount_cents', 0),
                    'patron_status': current_user.patreon_tier_data.get('patron_status'),
                    'last_charge_status': current_user.patreon_tier_data.get('last_charge_status'),
                    'description': current_user.patreon_tier_data.get('tier_description', ''),
                    'service_type': service_type
                },
                'grace_period_active': False  # Default value
            }
            
            # Add grace period message based on current time and grace_period_ends_at
            now = datetime.now(timezone.utc)
            if current_user.grace_period_ends_at and current_user.patreon_tier_data.get('patron_status') != 'active_patron':
                if now <= current_user.grace_period_ends_at:
                    # Calculate time remaining in a user-friendly format
                    time_diff = current_user.grace_period_ends_at - now
                    days_remaining = time_diff.days
                    hours_remaining = int(time_diff.total_seconds() // 3600)
                    
                    if days_remaining > 0:
                        time_remaining = f"{days_remaining} day{'s' if days_remaining != 1 else ''}"
                    else:
                        time_remaining = f"{hours_remaining} hour{'s' if hours_remaining != 1 else ''}"
                    
                    grace_msg = (
                        f"Your subscription expired, but you're on a grace period until "
                        f"{current_user.grace_period_ends_at.strftime('%B %d, %Y %I:%M %p')}. "
                        f"You have {time_remaining} remaining. Please renew soon to avoid interruption."
                    )
                    benefits_info["grace_period_message"] = grace_msg
                    benefits_info["grace_period_active"] = True
                    benefits_info["grace_period_ends_at"] = current_user.grace_period_ends_at
                    benefits_info["grace_period_time_remaining"] = time_remaining
                else:
                    grace_msg = (
                        "Your grace period has expired. Please renew your subscription to maintain access."
                    )
                    benefits_info["grace_period_message"] = grace_msg
                    benefits_info["grace_period_active"] = False
        
        # Handle team members
        elif current_user.is_team:
            logger.info("Processing benefits for Team member")
            period_start = None
            next_reset = None
            if current_user.patreon_tier_data:
                logger.info(f"Team member tier data: {json.dumps(current_user.patreon_tier_data, indent=2)}")
                if current_user.patreon_tier_data.get('period_start'):
                    try:
                        period_start_str = current_user.patreon_tier_data['period_start']
                        period_start = datetime.fromisoformat(period_start_str.replace('Z', '+00:00'))
                        logger.info(f"Using period_start for team member: {period_start}")
                        next_reset = period_start + relativedelta(months=1)
                        logger.info(f"Calculated next_reset for team member: {next_reset}")
                    except Exception as e:
                        logger.error(f"Error parsing team member period_start: {e}")
            
            benefits_info = {
                'tier_title': 'Team Member',
                'album_downloads': download_info['albums'],
                'track_downloads': download_info['tracks'],
                'book_requests': {
                    'requests_allowed': book_request_info['requests_allowed'],
                    'requests_used': book_request_info['requests_used'],
                    'requests_remaining': book_request_info['requests_remaining'],
                    'chapters_allowed_per_book_request': book_request_info.get('chapters_allowed_per_book_request', 0)
                },
                'chapters_allowed': book_request_info.get('chapters_allowed_per_book_request', 0),  # Add this line
                'period_start': period_start,
                'next_reset': next_reset,
                'is_unlimited': False,
                'tier_info': {
                    'description': 'Team member account',
                    'service_type': 'Team'
                }
            }
        
        # ✅ ADD THIS: Handle guest trial users BEFORE regular users
        elif current_user.is_guest_trial and current_user.role == UserRole.GUEST:
            logger.info("Processing benefits for GUEST TRIAL user")
            
            # Check if trial is still active
            if not current_user.trial_active:
                logger.info(f"Guest trial expired for {current_user.email}")
                benefits_info = {
                    'tier_title': 'Expired Guest Trial',
                    'album_downloads': {
                        'downloads_allowed': 0,
                        'downloads_used': download_info['albums']['downloads_used'],
                        'downloads_remaining': 0
                    },
                    'track_downloads': {
                        'downloads_allowed': 0,
                        'downloads_used': download_info['tracks']['downloads_used'],
                        'downloads_remaining': 0
                    },
                    'book_requests': {
                        'requests_allowed': 0,
                        'requests_used': book_request_info['requests_used'],
                        'requests_remaining': 0,
                        'chapters_allowed_per_book_request': 0
                    },
                    'chapters_allowed': 0,
                    'trial_expired': True,
                    'trial_started_at': current_user.trial_started_at,
                    'trial_expires_at': current_user.trial_expires_at,
                    'is_unlimited': False,
                    'tier_info': {
                        'description': 'Guest trial has expired'
                    }
                }
            else:
                # Active trial - get benefits from Guest Trial tier association
                from models import UserTier, CampaignTier
                
                user_tier = db.query(UserTier).filter(
                    and_(
                        UserTier.user_id == current_user.id,
                        UserTier.is_active == True
                    )
                ).first()
                
                if user_tier:
                    tier = db.query(CampaignTier).filter(
                        CampaignTier.id == user_tier.tier_id
                    ).first()
                    
                    if tier and tier.title == "Guest Trial":
                        logger.info(f"Found Guest Trial tier: {tier.title} - Albums: {tier.album_downloads_allowed}, Tracks: {tier.track_downloads_allowed}, Books: {tier.book_requests_allowed}")
                        
                        # Calculate time remaining
                        time_remaining = current_user.trial_expires_at - datetime.now(timezone.utc)
                        hours_remaining = int(time_remaining.total_seconds() / 3600)
                        
                        benefits_info = {
                            'tier_title': 'Guest Trial',
                            'album_downloads': {
                                'downloads_allowed': tier.album_downloads_allowed,
                                'downloads_used': download_info['albums']['downloads_used'],
                                'downloads_remaining': tier.album_downloads_allowed - download_info['albums']['downloads_used']
                            },
                            'track_downloads': {
                                'downloads_allowed': tier.track_downloads_allowed,
                                'downloads_used': download_info['tracks']['downloads_used'],
                                'downloads_remaining': tier.track_downloads_allowed - download_info['tracks']['downloads_used']
                            },
                            'book_requests': {
                                'requests_allowed': tier.book_requests_allowed,
                                'requests_used': book_request_info['requests_used'],
                                'requests_remaining': tier.book_requests_allowed - book_request_info['requests_used'],
                                'chapters_allowed_per_book_request': getattr(tier, 'chapters_allowed_per_book_request', 0)
                            },
                            'chapters_allowed': getattr(tier, 'chapters_allowed_per_book_request', 0),
                            'trial_active': True,
                            'trial_started_at': current_user.trial_started_at,
                            'trial_expires_at': current_user.trial_expires_at,
                            'trial_hours_remaining': max(0, hours_remaining),
                            'is_unlimited': False,
                            'tier_info': {
                                'description': f'Guest trial - {hours_remaining} hours remaining',
                                'service_type': 'Guest Trial'
                            }
                        }
                    else:
                        logger.error(f"Could not find Guest Trial tier for user {current_user.email}")
                        # Fallback to minimal trial benefits
                        benefits_info = {
                            'tier_title': 'Guest Trial (No Tier)',
                            'album_downloads': download_info['albums'],
                            'track_downloads': download_info['tracks'],
                            'book_requests': {
                                'requests_allowed': 0,
                                'requests_used': 0,
                                'requests_remaining': 0,
                                'chapters_allowed_per_book_request': 0
                            },
                            'chapters_allowed': 0,
                            'trial_started_at': current_user.trial_started_at,  # ✅ FIXED: Added missing field
                            'is_unlimited': False,
                            'tier_info': {
                                'description': 'Guest trial - no tier association found'
                            }
                        }
                else:
                    logger.error(f"No UserTier association found for guest trial user {current_user.email}")
                    # Fallback to minimal trial benefits
                    benefits_info = {
                        'tier_title': 'Guest Trial (No Association)',
                        'album_downloads': download_info['albums'],
                        'track_downloads': download_info['tracks'],
                        'book_requests': {
                            'requests_allowed': 0,
                            'requests_used': 0,
                            'requests_remaining': 0,
                            'chapters_allowed_per_book_request': 0
                        },
                        'chapters_allowed': 0,
                        'trial_started_at': current_user.trial_started_at,  # ✅ FIXED: Added missing field
                        'is_unlimited': False,
                        'tier_info': {
                            'description': 'Guest trial - no tier association'
                        }
                    }
        
        # Handle regular users
        else:
            logger.info("Processing benefits for regular user")
            benefits_info = {
                'tier_title': 'Free User',
                'album_downloads': download_info['albums'],
                'track_downloads': download_info['tracks'],
                'book_requests': {
                    'requests_allowed': 0,
                    'requests_used': 0,
                    'requests_remaining': 0,
                    'chapters_allowed_per_book_request': 0
                },
                'chapters_allowed': 0,  # Add this line
                'is_unlimited': False,
                'tier_info': {
                    'description': 'Free user account'
                }
            }
            
        logger.info(f"Final benefits info: {json.dumps(benefits_info, default=str)}")
        
        return templates.TemplateResponse(
            "my_benefits.html",
            {
                "request": request,
                "user": current_user,
                "benefits": benefits_info,
                "permissions": get_user_permissions(current_user)
            }
        )
    except Exception as e:
        logger.error(f"Error loading benefits page: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error loading benefits page")


# Add a new API endpoint to check grace period status
@app.get("/api/user/grace-period-status")
async def get_grace_period_status(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get user's grace period status"""
    try:
        now = datetime.now(timezone.utc)
        is_in_grace_period = False
        grace_period_ends_at = None
        time_remaining = None
        
        if current_user.grace_period_ends_at and current_user.patreon_tier_data and current_user.patreon_tier_data.get('patron_status') != 'active_patron':
            if now <= current_user.grace_period_ends_at:
                is_in_grace_period = True
                grace_period_ends_at = current_user.grace_period_ends_at
                
                # Calculate time remaining
                time_diff = current_user.grace_period_ends_at - now
                days_remaining = time_diff.days
                hours_remaining = int(time_diff.total_seconds() // 3600)
                
                if days_remaining > 0:
                    time_remaining = f"{days_remaining} day{'s' if days_remaining != 1 else ''}"
                else:
                    time_remaining = f"{hours_remaining} hour{'s' if hours_remaining != 1 else ''}"
        
        return {
            "is_in_grace_period": is_in_grace_period,
            "grace_period_ends_at": grace_period_ends_at.isoformat() if grace_period_ends_at else None,
            "time_remaining": time_remaining,
            "service_type": "Ko-fi" if current_user.is_kofi else "Patreon" if current_user.is_patreon else None
        }
    except Exception as e:
        logger.error(f"Error getting grace period status: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching grace period status")

@app.get("/api/user/book-requests-status")
async def get_user_book_requests_status(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get user's book request status and quota"""
    try:
        quota = await get_user_book_request_quota(current_user, db)
        
        # Get requested, approved, rejected, and fulfilled counts
        statuses = {}
        for status in BookRequestStatus:
            count = db.query(BookRequest).filter(
                BookRequest.user_id == current_user.id,
                BookRequest.status == status
            ).count()
            statuses[status.value] = count
        
        # Add counts to the response
        response = {
            **quota,
            "statuses": statuses
        }
        
        return response
    except Exception as e:
        logger.error(f"Error getting book request status: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching book request status")


@app.get("/api/creator/patrons/search")
@verify_role_permission(["creator"])
async def search_patrons(
    q: str,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Search patrons, team members, Ko-fi supporters, and guest trial users"""
    try:
        # ✅ FIXED: Include guest trial users in the search
        users_query = db.query(User).filter(
            and_(
                User.created_by == current_user.id,
                or_(
                    User.role == UserRole.PATREON,
                    User.role == UserRole.TEAM,
                    User.role == UserRole.KOFI,
                    and_(  # ✅ ADD: Include guest trial users
                        User.role == UserRole.GUEST,
                        User.is_guest_trial == True
                    )
                ),
                User.is_active == True,
                or_(
                    User.email.ilike(f"%{q}%"),
                    User.username.ilike(f"%{q}%")
                )
            )
        )
        
        logger.info(f"Searching for users (including guest trials) with query: {q}")
        users = users_query.all()
        logger.info(f"Found {len(users)} matching users")
        
        results = []
        for user in users:
            # Get the stored tier data
            tier_data = user.patreon_tier_data or {}
            
            # Get download information 
            downloads = {
                'albums': {
                    'allowed': tier_data.get('album_downloads_allowed', 0),
                    'used': tier_data.get('album_downloads_used', 0)
                },
                'tracks': {
                    'allowed': tier_data.get('track_downloads_allowed', 0),
                    'used': tier_data.get('track_downloads_used', 0)
                }
            }
            
            # Get book request information
            book_requests = {
                'allowed': tier_data.get('book_requests_allowed', 0)
            }
            
            # Get chapters information
            chapters_allowed_per_book_request = tier_data.get('chapters_allowed_per_book_request', 0)
            
            # Count actual book requests used
            current_month = datetime.now(timezone.utc).strftime("%Y-%m")
            
            from sqlalchemy import text
            book_request_query = text("""
                SELECT COUNT(*) 
                FROM book_requests 
                WHERE user_id = :user_id 
                AND month_year = :month_year 
                AND status != 'rejected'
            """)
            
            result = db.execute(book_request_query, {
                'user_id': user.id, 
                'month_year': current_month
            }).scalar()
            
            book_requests['used'] = result if result else 0
            
            # ✅ FIXED: Set role-specific info including guest trial users
            if user.role == UserRole.TEAM:
                title = "Team Member"
                role_type = "team"
            elif user.role == UserRole.KOFI:
                title = tier_data.get('title', 'Ko-fi Supporter')
                role_type = "kofi"
            elif user.role == UserRole.GUEST and user.is_guest_trial:  # ✅ ADD: Handle guest trial users
                title = tier_data.get('title', 'Guest Trial')
                role_type = "kofi"  # ✅ Treat as kofi platform for management
            else:
                title = tier_data.get('title', 'No Tier')
                role_type = "patreon"
            
            results.append({
                'id': user.id,
                'name': user.username,
                'email': user.email,
                'role': user.role.value,
                'role_type': role_type,
                'tier_title': title,
                'downloads': downloads,
                'book_requests': book_requests,
                'chapters_allowed_per_book_request': chapters_allowed_per_book_request,
                'max_sessions': tier_data.get('max_sessions', 1),
                'is_guest_trial': user.role == UserRole.GUEST and user.is_guest_trial  # ✅ ADD: Flag for UI
            })
            
            logger.info(f"Added user to results: {user.username} ({user.role}) with tier {title}")
            
        return results
        
    except Exception as e:
        logger.error(f"Error searching users: {str(e)}")
        raise HTTPException(status_code=500, detail="Error searching users")


# Fix 2: Update get_patron_benefits to include guest trial users
@app.get("/api/creator/patrons/{patron_id}/benefits")
@verify_role_permission(["creator"])
async def get_patron_benefits(
    patron_id: int,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    try:
        logger.info(f"Fetching benefits for patron ID {patron_id}")
        
        # ✅ FIXED: Include guest trial users in the query
        user = db.query(User).filter(
            and_(
                User.id == patron_id,
                User.created_by == current_user.id,
                User.is_active == True,
                or_(
                    User.role == UserRole.PATREON,
                    User.role == UserRole.KOFI,
                    User.role == UserRole.TEAM,
                    and_(  # ✅ ADD: Include guest trial users
                        User.role == UserRole.GUEST,
                        User.is_guest_trial == True
                    )
                )
            )
        ).first()

        if not user:
            logger.error(f"User with ID {patron_id} not found or not associated with this creator")
            raise HTTPException(status_code=404, detail="User not found")

        tier_data = user.patreon_tier_data or {}
        logger.info(f"Retrieved tier data: {json.dumps(tier_data)}")
        
        # Get book request quota using raw SQL to avoid enum issues
        book_requests_allowed = tier_data.get('book_requests_allowed', 0)
        chapters_allowed_per_book_request = tier_data.get('chapters_allowed_per_book_request', 0)
        current_month = datetime.now(timezone.utc).strftime("%Y-%m")
        
        from sqlalchemy import text
        book_request_query = text("""
            SELECT COUNT(*) 
            FROM book_requests 
            WHERE user_id = :user_id 
            AND month_year = :month_year 
            AND status != 'rejected'
        """)
        
        result = db.execute(book_request_query, {
            'user_id': user.id, 
            'month_year': current_month
        }).scalar()
        
        book_requests_used = result if result else 0

        # ✅ FIXED: Handle guest trial users in role_type determination
        if user.role == UserRole.TEAM:
            tier_title = 'Team Member'
            role_type = 'team'
        elif user.role == UserRole.GUEST and user.is_guest_trial:  # ✅ ADD: Handle guest trial users
            tier_title = tier_data.get('title', 'Guest Trial')
            role_type = 'kofi'  # ✅ Treat as kofi platform
        elif user.role == UserRole.KOFI:
            tier_title = tier_data.get('title', 'Ko-fi Supporter')
            role_type = 'kofi'
        else:
            tier_title = tier_data.get('title', 'No Tier')
            role_type = 'patreon'

        return {
            'id': user.id,
            'name': user.username,
            'email': user.email,
            'tier_title': tier_title,
            'role_type': role_type,
            'album_downloads_allowed': tier_data.get('album_downloads_allowed', 0),
            'album_downloads_used': tier_data.get('album_downloads_used', 0),
            'track_downloads_allowed': tier_data.get('track_downloads_allowed', 0),
            'track_downloads_used': tier_data.get('track_downloads_used', 0),
            'book_requests_allowed': tier_data.get('book_requests_allowed', 0),
            'book_requests_used': tier_data.get('book_requests_used', 0),
            'chapters_allowed_per_book_request': chapters_allowed_per_book_request,
            'max_sessions': tier_data.get('max_sessions', 1),
            'is_guest_trial': user.role == UserRole.GUEST and user.is_guest_trial  # ✅ ADD: Flag for UI
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error getting patron benefits: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Error getting patron benefits")


# Fix 3: Update patron benefits endpoint to include guest trial users
@app.post("/api/creator/patrons/{patron_id}/benefits")
@verify_role_permission(["creator"])
async def update_patron_benefits(
    request: Request,
    patron_id: int,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    try:
        body = await request.json()
        logger.info(f"Updating benefits for patron ID {patron_id}")
        
        # ✅ FIXED: Include guest trial users in the query
        user = db.query(User).filter(
            and_(
                User.id == patron_id,
                User.created_by == current_user.id,
                User.is_active == True,
                or_(
                    User.role == UserRole.PATREON,
                    User.role == UserRole.KOFI,
                    User.role == UserRole.TEAM,
                    and_(  # ✅ ADD: Include guest trial users
                        User.role == UserRole.GUEST,
                        User.is_guest_trial == True
                    )
                )
            )
        ).first()

        if not user:
            logger.error(f"User with ID {patron_id} not found")
            raise HTTPException(status_code=404, detail="User not found")
            
        logger.info(f"Found user: {user.email}")
        logger.info(f"Current tier data: {user.patreon_tier_data}")
        logger.info(f"Requested updates: {body}")

        # Get current tier data
        tier_data = dict(user.patreon_tier_data or {})
        
        # Update the values
        new_tier_data = {
            **tier_data,  # Keep all existing data
            'album_downloads_allowed': body.get('album_downloads_allowed'),
            'track_downloads_allowed': body.get('track_downloads_allowed'),
            'book_requests_allowed': body.get('book_requests_allowed'),
            'chapters_allowed_per_book_request': body.get('chapters_allowed_per_book_request', 0),
            'max_sessions': min(5, max(1, body.get('max_sessions', 1)))
        }
        
        logger.info(f"New tier data before save: {new_tier_data}")

        # Save the updates
        user.patreon_tier_data = new_tier_data
        db.flush()
        
        logger.info(f"Data after flush: {user.patreon_tier_data}")
        
        db.commit()
        db.refresh(user)
        
        logger.info(f"Final user data: {user.patreon_tier_data}")

        return {
            "status": "success",
            "message": "Benefits updated successfully",
            "updated_benefits": user.patreon_tier_data
        }

    except Exception as e:
        db.rollback()
        logger.error(f"Error updating patron benefits: {str(e)}")
        raise HTTPException(status_code=500, detail="Error updating patron benefits")

@app.get("/api/creator/patrons/{patron_id}/benefits")
@verify_role_permission(["creator"])
async def get_patron_benefits(
    patron_id: int,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    try:
        logger.info(f"Fetching benefits for patron ID {patron_id}")
        
        # Get the patron - check for ALL user types (Patreon, Ko-fi, Team)
        user = db.query(User).filter(
            and_(
                User.id == patron_id,
                User.created_by == current_user.id,
                User.is_active == True,
                or_(
                    User.role == UserRole.PATREON,
                    User.role == UserRole.KOFI,
                    User.role == UserRole.TEAM
                )
            )
        ).first()

        if not user:
            logger.error(f"Patron with ID {patron_id} not found or not associated with this creator")
            raise HTTPException(status_code=404, detail="Patron not found")

        tier_data = user.patreon_tier_data or {}
        logger.info(f"Retrieved tier data: {json.dumps(tier_data)}")
        
        # Get book request quota using raw SQL to avoid enum issues
        book_requests_allowed = tier_data.get('book_requests_allowed', 0)
        chapters_allowed_per_book_request = tier_data.get('chapters_allowed_per_book_request', 0)
        current_month = datetime.now(timezone.utc).strftime("%Y-%m")
        
        # Use raw SQL to get book request counts - AVOID ENUM ISSUES
        from sqlalchemy import text
        book_request_query = text("""
            SELECT COUNT(*) 
            FROM book_requests 
            WHERE user_id = :user_id 
            AND month_year = :month_year 
            AND status != 'rejected'
        """)
        
        result = db.execute(book_request_query, {
            'user_id': user.id, 
            'month_year': current_month
        }).scalar()
        
        book_requests_used = result if result else 0

        return {
            'id': user.id,
            'name': user.username,
            'email': user.email,
            'tier_title': tier_data.get('title', 'No Tier') if user.role != UserRole.TEAM else 'Team Member',
            'role_type': 'patreon' if user.role == UserRole.PATREON else 
                        'kofi' if user.role == UserRole.KOFI else 'team',
            'album_downloads_allowed': tier_data.get('album_downloads_allowed', 0),
            'album_downloads_used': tier_data.get('album_downloads_used', 0),
            'track_downloads_allowed': tier_data.get('track_downloads_allowed', 0),
            'track_downloads_used': tier_data.get('track_downloads_used', 0),
            'book_requests_allowed': tier_data.get('book_requests_allowed', 0),
            'book_requests_used': tier_data.get('book_requests_used', 0),
            'chapters_allowed_per_book_request': chapters_allowed_per_book_request,  # Add this line
            'max_sessions': tier_data.get('max_sessions', 1)
        }

    except HTTPException as he:
        # Pass through HTTP exceptions directly without wrapping
        raise he
    except Exception as e:
        logger.error(f"Error getting patron benefits: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Error getting patron benefits")


@app.post("/api/creator/patrons/{patron_id}/benefits")
@verify_role_permission(["creator"])
async def update_patron_benefits(
    request: Request,
    patron_id: int,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    try:
        body = await request.json()
        logger.info(f"Updating benefits for patron ID {patron_id}")
        
        # Get the patron or team member - check ALL user types
        user = db.query(User).filter(
            and_(
                User.id == patron_id,
                User.created_by == current_user.id,
                User.is_active == True,
                or_(
                    User.role == UserRole.PATREON,
                    User.role == UserRole.KOFI,
                    User.role == UserRole.TEAM
                )
            )
        ).first()

        if not user:
            logger.error(f"Patron with ID {patron_id} not found")
            raise HTTPException(status_code=404, detail="Patron not found")
            
        logger.info(f"Found user: {user.email}")
        logger.info(f"Current tier data: {user.patreon_tier_data}")
        logger.info(f"Requested updates: {body}")

        # Get current tier data
        tier_data = dict(user.patreon_tier_data or {})
        
        # Update the values
        new_tier_data = {
            **tier_data,  # Keep all existing data
            'album_downloads_allowed': body.get('album_downloads_allowed'),
            'track_downloads_allowed': body.get('track_downloads_allowed'),
            'book_requests_allowed': body.get('book_requests_allowed'),
            'chapters_allowed_per_book_request': body.get('chapters_allowed_per_book_request', 0),  # Add this line
            'max_sessions': min(5, max(1, body.get('max_sessions', 1)))
        }
        
        logger.info(f"New tier data before save: {new_tier_data}")

        # Save the updates
        user.patreon_tier_data = new_tier_data
        db.flush()
        
        logger.info(f"Data after flush: {user.patreon_tier_data}")
        
        db.commit()
        db.refresh(user)
        
        logger.info(f"Final patron data: {user.patreon_tier_data}")

        return {
            "status": "success",
            "message": "Benefits updated successfully",
            "updated_benefits": user.patreon_tier_data
        }

    except Exception as e:
        db.rollback()
        logger.error(f"Error updating patron benefits: {str(e)}")
        raise HTTPException(status_code=500, detail="Error updating patron benefits")
 

@app.post("/api/creator/downloads/reset")
@verify_role_permission(["creator"])
async def reset_all_downloads(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Reset all patron, team member, and Ko-fi user download counts back to their allowed values"""
    try:
        creator_id = current_user.id
        
        # Get all active patrons, team members, AND Ko-fi users
        users = db.query(User).filter(
            and_(
                User.created_by == creator_id,
                User.is_active == True,
                or_(
                    User.role == UserRole.PATREON,
                    User.role == UserRole.TEAM,
                    User.role == UserRole.KOFI  # Added Ko-fi users
                )
            )
        ).all()
        
        update_count = 0
        current_month = datetime.now(timezone.utc).strftime("%Y-%m")
        
        for user in users:
            try:
                if user.patreon_tier_data:
                    # Create a new dictionary to avoid reference issues
                    updated_data = dict(user.patreon_tier_data)
                    album_downloads_allowed = updated_data.get('album_downloads_allowed', 0)
                    track_downloads_allowed = updated_data.get('track_downloads_allowed', 0)
                    
                    # Explicitly set both download counts to 0
                    updated_data['album_downloads_used'] = 0
                    updated_data['track_downloads_used'] = 0
                    
                    # Also reset book requests if they exist
                    if 'book_requests_used' in updated_data:
                        updated_data['book_requests_used'] = 0
                    
                    # Update the last reset month to track manual resets
                    updated_data['last_reset_month'] = current_month
                    updated_data['manual_reset_date'] = datetime.now(timezone.utc).isoformat()
                    
                    # Update the user's tier data with the new dictionary
                    user.patreon_tier_data = updated_data
                    
                    update_count += 1
                    
                    logger.info(
                        f"Reset downloads for {user.role.value} {user.email}: "
                        f"restored album downloads: {album_downloads_allowed} (used: {updated_data['album_downloads_used']}), "
                        f"track downloads: {track_downloads_allowed} (used: {updated_data['track_downloads_used']}), "
                        f"book requests: {updated_data.get('book_requests_used', 0)}, "
                        f"period remains: {updated_data.get('period_start', 'Not set')}"
                    )
                    
                    # Explicitly flush changes for this user
                    db.flush()
                
            except Exception as e:
                logger.error(f"Error resetting {user.role.value} {user.email}: {str(e)}")
                continue
        
        # Commit all changes
        db.commit()
        
        # Verify changes were saved
        for user in users:
            db.refresh(user)
            if user.patreon_tier_data:
                logger.info(
                    f"Verified reset for {user.role.value} {user.email}: "
                    f"album_downloads_used now = {user.patreon_tier_data.get('album_downloads_used', 'Not set')}, "
                    f"track_downloads_used now = {user.patreon_tier_data.get('track_downloads_used', 'Not set')}, "
                    f"book_requests_used now = {user.patreon_tier_data.get('book_requests_used', 'Not set')}"
                )
        
        return {
            "status": "success",
            "message": f"Reset {update_count} users back to their allowed download counts",
            "updated_count": update_count,
            "details": {
                "patrons": len([u for u in users if u.role == UserRole.PATREON]),
                "team_members": len([u for u in users if u.role == UserRole.TEAM]),
                "kofi_users": len([u for u in users if u.role == UserRole.KOFI])  # Added Ko-fi count
            }
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error resetting downloads: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/creator/downloads/settings")
@verify_role_permission(["creator"])
async def update_download_settings(
    settings: Dict[str, Any],
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Update download allowances for a tier using CampaignTier"""
    try:
        creator_id = current_user.id
        tier_title = settings.get('tier_id')
        album_downloads = settings.get('album_downloads_allowed', 0)
        track_downloads = settings.get('track_downloads_allowed', 0)
        
        logger.info(f"Updating download settings:")
        logger.info(f"Tier: {tier_title}")
        logger.info(f"Album downloads allowed: {album_downloads}")
        logger.info(f"Track downloads allowed: {track_downloads}")

        # Handle team members differently
        is_team_tier = tier_title.lower() == 'team members'
        
        # Find existing campaign tier
        campaign_tier = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == creator_id,
                CampaignTier.title.ilike(tier_title)
            )
        ).first()
        
        if not campaign_tier:
            # Create new campaign tier
            campaign_tier = CampaignTier(
                creator_id=creator_id,
                title=tier_title,
                album_downloads_allowed=album_downloads,
                track_downloads_allowed=track_downloads,
                is_active=True,
                # For team tier, use special handling
                patreon_tier_id=None if is_team_tier else tier_title,
                amount_cents=0 if is_team_tier else None
            )
            db.add(campaign_tier)
            logger.info(f"Created new campaign tier: {tier_title} with album downloads: {album_downloads}, track downloads: {track_downloads}")
        else:
            campaign_tier.album_downloads_allowed = album_downloads
            campaign_tier.track_downloads_allowed = track_downloads
            logger.info(f"Updated existing campaign tier: {tier_title} with album downloads: {album_downloads}, track downloads: {track_downloads}")

        db.flush()  # Flush to get the campaign tier ID

        # Update team members or patrons
        if is_team_tier:
            users_to_update = db.query(User).filter(
                and_(
                    User.created_by == creator_id,
                    User.role == UserRole.TEAM,
                    User.is_active == True
                )
            ).all()
        else:
            # Update both Patreon and Ko-fi users with matching tier
            users_to_update = db.query(User).filter(
                and_(
                    User.created_by == creator_id,
                    or_(
                        User.role == UserRole.PATREON,
                        User.role == UserRole.KOFI
                    ),
                    User.is_active == True,
                    func.json_extract_path_text(User.patreon_tier_data, 'title').ilike(tier_title)
                )
            ).all()

        update_count = 0
        kofi_update_count = 0
        
        for user in users_to_update:
            try:
                # Here's the important part - force refresh tier data in the database
                # Get the latest user data directly from the database
                db.refresh(user)
                
                current_data = user.patreon_tier_data or {}
                updated_data = current_data.copy()
                
                # Update download allowances while preserving usage
                updated_data.update({
                    'album_downloads_allowed': album_downloads,
                    'track_downloads_allowed': track_downloads,
                })
                
                # Dump data to log before saving
                logger.info(f"User {user.email} before update: {json.dumps(user.patreon_tier_data)}")
                
                # Apply the update and explicitly flush to database
                user.patreon_tier_data = updated_data
                db.flush()
                
                # Re-fetch to confirm changes saved
                db.refresh(user)
                logger.info(f"User {user.email} after update: {json.dumps(user.patreon_tier_data)}")
                
                update_count += 1
                
                if user.role == UserRole.KOFI:
                    kofi_update_count += 1
                    
                logger.info(
                    f"Updated user {user.email} ({user.role}): "
                    f"album downloads: {album_downloads} (used: {updated_data.get('album_downloads_used', 0)}), "
                    f"track downloads: {track_downloads} (used: {updated_data.get('track_downloads_used', 0)})"
                )

            except Exception as e:
                logger.error(f"Error processing user {user.email}: {str(e)}")
                continue

        # Explicitly commit the transaction
        db.commit()
        
        return {
            "status": "success",
            "message": f"Successfully updated campaign tier and {update_count} users ({kofi_update_count} Ko-fi users)",
            "album_downloads_allowed": album_downloads,
            "track_downloads_allowed": track_downloads,
            "tier_title": tier_title
        }

    except Exception as e:
        db.rollback()
        logger.error(f"Error updating download settings: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/creator/tiers")
async def get_tiers(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    try:
        # Get base tier data
        tiers = db.query(CampaignTier).filter(
            CampaignTier.creator_id == current_user.id
        ).all()
        
        # Count active sessions per tier using a subquery
        active_sessions = (
            db.query(
                func.json_extract_path_text(User.patreon_tier_data, 'title').label('tier_title'),
                func.count(UserSession.id).label('session_count')
            )
            .join(UserSession, User.id == UserSession.user_id)
            .filter(
                and_(
                    User.created_by == current_user.id,
                    User.role == UserRole.PATREON,
                    UserSession.is_active == True
                )
            )
            .group_by(func.json_extract_path_text(User.patreon_tier_data, 'title'))
            .all()
        )
        
        session_counts = {
            result.tier_title: result.session_count 
            for result in active_sessions
        }
        
        return [{
            "id": str(tier.id),
            "title": tier.title,
            "amount_cents": tier.amount_cents,
            "patron_count": tier.patron_count,
            "is_active": tier.is_active,
            "album_downloads_allowed": tier.album_downloads_allowed,  # Updated field name
            "track_downloads_allowed": tier.track_downloads_allowed,  # Added field
            "max_sessions": tier.max_sessions,
            "active_sessions": session_counts.get(tier.title, 0)
        } for tier in tiers]
        
    except Exception as e:
        logger.error(f"Get tiers error: {str(e)}")
        raise HTTPException(status_code=500) 


 

@app.get("/api/creator/benefits/data")
@verify_role_permission(["creator"])
async def get_benefits_data(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get benefits data for SPA mode - mirrors SSR endpoint logic"""
    
    try:
        creator_id = current_user.id
        logger.info(f"Loading benefits data API for creator_id: {creator_id}")
        tiers = []

        # Check if platform_type column exists (database migration handling)
        has_platform_column = False
        try:
            db.query(CampaignTier.platform_type).limit(1).all()
            has_platform_column = True
            logger.info("Database has platform_type column")
        except Exception:
            logger.info("Database does not have platform_type column yet")

        # Get Patreon and Ko-fi tiers from database
        campaign_tiers = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == creator_id,
                CampaignTier.is_active == True
            )
        ).order_by(CampaignTier.amount_cents).all()

        logger.info(f"Found {len(campaign_tiers)} campaign tiers")

        # Process each tier (same logic as SSR endpoint)
        for tier in campaign_tiers:
            logger.info(f"Processing tier: {tier.title} (amount: {tier.amount_cents})")

            # Determine platform type
            if has_platform_column and hasattr(tier, 'platform_type') and tier.platform_type:
                is_kofi_tier = str(tier.platform_type) == "KOFI"
            else:
                is_kofi_tier = "kofi" in tier.title.lower()
                
            user_role = UserRole.KOFI if is_kofi_tier else UserRole.PATREON
            
            # Check if this is a guest trial tier
            is_guest_trial = "guest trial" in tier.title.lower()
            
            if is_guest_trial:
                # For guest trial tiers, count GUEST users
                patron_query = db.query(User).filter(
                    and_(
                        User.created_by == creator_id,
                        User.role == UserRole.GUEST,
                        User.is_guest_trial == True,
                        User.is_active == True
                    )
                )
            else:
                # For regular tiers, use existing logic
                patron_query = db.query(User).filter(
                    and_(
                        User.created_by == creator_id,
                        User.role == user_role,
                        User.is_active == True,
                        func.json_extract_path_text(User.patreon_tier_data, 'title').ilike(tier.title)
                    )
                )

            # Get patron count
            patrons = patron_query.all()
            actual_patron_count = len(patrons)

            if is_guest_trial:
                tier.patron_count = actual_patron_count

            # Get active sessions for this tier
            active_sessions_count = 0
            if not is_guest_trial:
                try:
                    active_sessions_count = db.query(UserSession).join(
                        User, UserSession.user_id == User.id
                    ).filter(
                        and_(
                            User.created_by == creator_id,
                            User.role == user_role,
                            UserSession.is_active == True,
                            UserSession.expires_at > datetime.now(timezone.utc),
                            func.json_extract_path_text(User.patreon_tier_data, 'title').ilike(tier.title)
                        )
                    ).count()
                except Exception as e:
                    logger.warning(f"Could not count active sessions for tier {tier.title}: {e}")
                    active_sessions_count = 0

            # Get all tier properties
            album_downloads = tier.album_downloads_allowed
            track_downloads = tier.track_downloads_allowed
            book_requests = getattr(tier, 'book_requests_allowed', 0)
            chapters_per_request = getattr(tier, 'chapters_allowed_per_book_request', 0)
            max_sessions = getattr(tier, 'max_sessions', 1)
            read_along_access = getattr(tier, 'read_along_access', False)

            logger.info(
                f"Tier {tier.title}: Albums={album_downloads}, Tracks={track_downloads}, "
                f"Book Requests={book_requests}, Chapters/Request={chapters_per_request}, "
                f"Max Sessions={max_sessions}, Read-Along={read_along_access}"
            )

            # Prepare tier data
            tier_data = {
                "id": tier.id,
                "title": tier.title,
                "patreon_tier_id": tier.title,
                "amount_cents": tier.amount_cents,
                "patron_count": tier.patron_count,
                "is_active": tier.is_active,
                "album_downloads_allowed": album_downloads,
                "track_downloads_allowed": track_downloads,
                "book_requests_allowed": book_requests,
                "chapters_allowed_per_book_request": chapters_per_request,
                "max_sessions": max_sessions,
                "active_sessions": active_sessions_count,
                "is_kofi": is_kofi_tier,
                "read_along_access": read_along_access
            }
            
            # Add platform type information if available
            if has_platform_column:
                tier_data["platform_type"] = str(getattr(tier, 'platform_type', "PATREON"))
            
            tiers.append(tier_data)

        # Get user counts (same as SSR endpoint)
        kofi_users_count = db.query(User).filter(
            and_(
                User.created_by == creator_id,
                User.role == UserRole.KOFI,
                User.is_active == True
            )
        ).count()

        patreon_users_count = db.query(User).filter(
            and_(
                User.created_by == creator_id,
                User.role == UserRole.PATREON,
                User.is_active == True
            )
        ).count()
        
        guest_trial_users_count = db.query(User).filter(
            and_(
                User.created_by == creator_id,
                User.role == UserRole.GUEST,
                User.is_guest_trial == True,
                User.is_active == True
            )
        ).count()
        
        team_users_count = db.query(User).filter(
            and_(
                User.created_by == creator_id,
                User.role == UserRole.TEAM,
                User.is_active == True
            )
        ).count()

        logger.info(
            f"User counts - Patreon: {patreon_users_count}, Ko-fi: {kofi_users_count}, "
            f"Guest Trial: {guest_trial_users_count}, Team: {team_users_count}"
        )
        logger.info(f"Returning {len(tiers)} total tiers")

        return {
            "tiers": tiers,
            "patreon_users_count": patreon_users_count,
            "kofi_users_count": kofi_users_count,
            "team_users_count": team_users_count,
            "guest_trial_users_count": guest_trial_users_count,
            "has_platform_column": has_platform_column
        }

    except Exception as e:
        logger.error(f"Error in benefits data API: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
 

@app.get("/creator/benefits")
@verify_role_permission(["creator"])
async def benefits_page(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    try:
        creator_id = current_user.id
        logger.info(f"Loading benefits for creator_id: {creator_id}")
        tiers = []

        # Add team tier first
        team_members = db.query(User).filter(
            and_(
                User.created_by == creator_id,
                User.role == UserRole.TEAM,
                User.is_active == True
            )
        ).all()

        logger.info(f"Found {len(team_members)} team members")
        # Debug team member tier data
        team_album_downloads = 0
        team_track_downloads = 0
        team_chapters_per_request = 0
        if team_members:
            for member in team_members:
                logger.info(f"Team member {member.email} tier data: {member.patreon_tier_data}")
                if member.patreon_tier_data:
                    team_album_downloads = member.patreon_tier_data.get('album_downloads_allowed', 0)
                    team_track_downloads = member.patreon_tier_data.get('track_downloads_allowed', 0)
                    team_chapters_per_request = member.patreon_tier_data.get('chapters_allowed_per_book_request', 0)
                    logger.info(f"Found team downloads - Albums: {team_album_downloads}, Tracks: {team_track_downloads}, Chapters: {team_chapters_per_request}")
                    break

        # Get Patreon and Ko-fi tiers from database
        tiers_query = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == creator_id,
                CampaignTier.is_active == True
            )
        ).order_by(CampaignTier.amount_cents)

        # Log the SQL query
        logger.info(f"Campaign tiers query: {str(tiers_query)}")

        # Check if platform_type column exists
        has_platform_column = False
        try:
            # This will throw an error if the column doesn't exist
            db.query(CampaignTier.platform_type).limit(1).all()
            has_platform_column = True
            logger.info("Database has platform_type column")
        except Exception:
            logger.info("Database does not have platform_type column yet")

        campaign_tiers = tiers_query.all()
        logger.info(f"Found {len(campaign_tiers)} campaign tiers")

        # Log each tier found
        for tier in campaign_tiers:
            logger.info(f"Processing tier: {tier.title} (amount: {tier.amount_cents})")

            # Determine platform type
            if has_platform_column and hasattr(tier, 'platform_type') and tier.platform_type:
                # Use the actual platform_type field
                is_kofi_tier = str(tier.platform_type) == "KOFI"
            else:
                # Fallback to title check
                is_kofi_tier = "kofi" in tier.title.lower()
                
            user_role = UserRole.KOFI if is_kofi_tier else UserRole.PATREON
            
            # ✅ MINIMAL FIX: Check if this is a guest trial tier
            is_guest_trial = "guest trial" in tier.title.lower()
            
            if is_guest_trial:
                # For guest trial tiers, count GUEST users instead of KOFI users
                patron_query = db.query(User).filter(
                    and_(
                        User.created_by == creator_id,
                        User.role == UserRole.GUEST,  # ✅ Use GUEST role for guest trials
                        User.is_guest_trial == True,
                        User.is_active == True
                    )
                )
            else:
                # For regular tiers, use existing logic
                patron_query = db.query(User).filter(
                    and_(
                        User.created_by == creator_id,
                        User.role == user_role,
                        User.is_active == True,
                        func.json_extract_path_text(User.patreon_tier_data, 'title').ilike(tier.title)
                    )
                )

            # Log patron query
            logger.info(f"Patron query for tier {tier.title}: {str(patron_query)}")

            # Get patron count and example
            patrons = patron_query.all()
            actual_patron_count = len(patrons)
            example_patron = patrons[0] if patrons else None

            # ✅ MINIMAL FIX: Use actual count for guest trials
            if is_guest_trial:
                tier.patron_count = actual_patron_count  # Update the tier's patron count

            patron_email = example_patron.email if example_patron else 'None'
            logger.info(f"Example patron found: {patron_email}")

            # Get downloads from CampaignTier model directly
            album_downloads = tier.album_downloads_allowed
            track_downloads = tier.track_downloads_allowed
            book_requests = getattr(tier, 'book_requests_allowed', 0)
            chapters_per_request = getattr(tier, 'chapters_allowed_per_book_request', 0)
            max_sessions = getattr(tier, 'max_sessions', 1)
            active_sessions = getattr(tier, 'active_sessions', 0)
            read_along_access = getattr(tier, 'read_along_access', False)

            logger.info(
                f"Downloads for tier {tier.title}: "
                f"Albums={album_downloads}, Tracks={track_downloads}, "
                f"Book Requests={book_requests}, Chapters/Request={chapters_per_request}, "
                f"Max Sessions={max_sessions}, Read-Along Access={read_along_access}" 
            )

            # Prepare tier data
            tier_data = {
                "id": tier.id, 
                "title": tier.title,
                "patreon_tier_id": tier.title,
                "amount_cents": tier.amount_cents,
                "patron_count": tier.patron_count,
                "is_active": tier.is_active,
                "album_downloads_allowed": album_downloads,
                "track_downloads_allowed": track_downloads,
                "book_requests_allowed": book_requests,
                "chapters_allowed_per_book_request": chapters_per_request,
                "max_sessions": max_sessions,
                "active_sessions": active_sessions,
                "is_kofi": is_kofi_tier,
                "read_along_access": read_along_access

            }
            
            # Add platform type information if available
            if has_platform_column:
                tier_data["platform_type"] = str(getattr(tier, 'platform_type', "PATREON"))
            
            tiers.append(tier_data)

        # Get Ko-fi users count (to show on the page)
        kofi_users_count = db.query(User).filter(
            and_(
                User.created_by == creator_id,
                User.role == UserRole.KOFI,
                User.is_active == True
            )
        ).count()

        # Get Patreon users count (to show on the page)
        patreon_users_count = db.query(User).filter(
            and_(
                User.created_by == creator_id,
                User.role == UserRole.PATREON,
                User.is_active == True
            )
        ).count()
        
        # ✅ NEW: Get Guest Trial users count
        guest_trial_users_count = db.query(User).filter(
            and_(
                User.created_by == creator_id,
                User.role == UserRole.GUEST,
                User.is_guest_trial == True,
                User.is_active == True
            )
        ).count()
        
        # Get Team members count
        team_users_count = len(team_members)

        logger.info(f"User counts - Patreon: {patreon_users_count}, Ko-fi: {kofi_users_count}, Guest Trial: {guest_trial_users_count}, Team: {team_users_count}")
        logger.info(f"Returning {len(tiers)} total tiers")

        return templates.TemplateResponse(
            "benefits.html",
            {
                "request": request,
                "user": current_user,
                "permissions": get_user_permissions(current_user),
                "tiers": tiers,
                "kofi_users_count": kofi_users_count,
                "patreon_users_count": patreon_users_count,
                "guest_trial_users_count": guest_trial_users_count,  # ✅ NEW: Add guest trial count
                "team_users_count": team_users_count,
                "has_platform_column": has_platform_column
            }
        )

    except Exception as e:
        logger.error(f"Error in benefits page: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
@app.post("/api/creator/sessions/reset")
async def reset_sessions(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    try:
        if not current_user.is_creator:
            raise HTTPException(status_code=403)

        # Get all patron users for this creator
        patron_users = db.query(User).filter(
            and_(
                User.created_by == current_user.id,
                User.role == UserRole.PATREON,
                User.is_active == True
            )
        ).all()

        logger.info(f"Found {len(patron_users)} active patrons")

        # Get all their active sessions
        active_sessions = db.query(UserSession).filter(
            and_(
                UserSession.user_id.in_([u.id for u in patron_users]),
                UserSession.is_active == True,
                UserSession.expires_at > datetime.now(timezone.utc)
            )
        ).all()

        logger.info(f"Found {len(active_sessions)} active sessions to reset")

        # Group sessions by tier for logging
        sessions_by_tier = {}
        for session in active_sessions:
            patron = next(u for u in patron_users if u.id == session.user_id)
            tier_title = patron.patreon_tier_data.get('title', 'Unknown')
            sessions_by_tier.setdefault(tier_title, []).append(session)

        # Log distribution
        for tier, sessions in sessions_by_tier.items():
            logger.info(f"Tier {tier}: {len(sessions)} active sessions")

        # Deactivate all sessions
        for session in active_sessions:
            session.is_active = False
            session.updated_at = datetime.now(timezone.utc)
            logger.info(f"Deactivating session {session.session_id} for user {session.user_id}")

        db.commit()

        # Verify reset
        remaining = db.query(UserSession).filter(
            and_(
                UserSession.user_id.in_([u.id for u in patron_users]),
                UserSession.is_active == True
            )
        ).count()

        logger.info(f"Reset complete. All sessions deactivated. Remaining active: {remaining}")

        return {
            "success": True,
            "sessions_reset": len(active_sessions),
            "tier_distribution": {tier: len(sessions) for tier, sessions in sessions_by_tier.items()}
        }

    except Exception as e:
        logger.error(f"Session reset error: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500)

@app.post("/api/creator/sessions/settings")
async def update_session_settings(
    request: Request,
    data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    try:
        tier_id = data.get('tier_id')
        max_sessions = data.get('max_sessions')

        # Validate input
        if not isinstance(max_sessions, int) or max_sessions < 1 or max_sessions > 5:
            raise HTTPException(
                status_code=400,
                detail="Max sessions must be between 1 and 5"
            )

        # Get tier and validate
        tier = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == current_user.id,
                CampaignTier.title == tier_id,
                CampaignTier.is_active == True
            )
        ).first()

        if not tier:
            raise HTTPException(status_code=404, detail="Tier not found")

        # Update tier settings
        tier.max_sessions = max_sessions
        
        # Get current active sessions for this tier
        active_sessions = (
            db.query(UserSession)
            .join(User, UserSession.user_id == User.id)
            .filter(
                and_(
                    User.created_by == current_user.id,
                    User.role == UserRole.PATREON,
                    UserSession.is_active == True,
                    func.json_extract_path_text(User.patreon_tier_data, 'title') == tier_id
                )
            )
            .order_by(UserSession.last_active.desc())
            .all()
        )

        # If there are more active sessions than new max, deactivate oldest
        if len(active_sessions) > max_sessions:
            logger.info(f"Need to deactivate {len(active_sessions) - max_sessions} sessions for tier {tier_id}")
            for session in active_sessions[max_sessions:]:
                session.is_active = False
                session.updated_at = datetime.now(timezone.utc)

        db.commit()

        return {
            "success": True,
            "tier": tier_id,
            "max_sessions": max_sessions,
            "sessions_deactivated": len(active_sessions) - max_sessions if len(active_sessions) > max_sessions else 0
        }

    except Exception as e:
        logger.error(f"Error updating session settings: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500)
     
@app.get("/api/statistics/data")
@verify_role_permission(["creator"])
async def get_statistics_data(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """
    API endpoint to fetch statistics data for SPA mode
    Returns the same data structure as the SSR template
    """
    try:
        # Get all active tiers for this creator
        tiers = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == current_user.id,
                CampaignTier.is_active == True
            )
        ).order_by(CampaignTier.amount_cents.desc()).all()

        # Get all users belonging to this creator (excluding creators themselves)
        all_users = db.query(User).filter(
            and_(
                User.created_by == current_user.id,
                User.role != UserRole.CREATOR,  # Exclude creator downloads
                User.is_active == True
            )
        ).all()

        logger.info(f"Found {len(all_users)} non-creator users and {len(tiers)} tiers for creator {current_user.id}")

        if not all_users:
            return {
                "monthly_stats": [],
                "total_stats": {
                    "albums": 0,
                    "tracks": 0,
                    "book_requests": 0,
                    "total_users": 0
                }
            }

        # Group users by tier and role
        user_tier_map = {}
        tier_users = {}
        role_users = {
            'TEAM': [],
            'KOFI': [],
            'GUEST': []
        }
        
        # Initialize tier groups
        for tier in tiers:
            tier_users[tier.title] = []
        
        # Categorize users
        for user in all_users:
            role_key = user.role.value if hasattr(user.role, 'value') else str(user.role)
            
            if role_key == 'PATREON' and user.patreon_tier_data:
                tier_title = user.patreon_tier_data.get('title', 'Unknown Tier')
                if tier_title in tier_users:
                    tier_users[tier_title].append(user)
                    user_tier_map[user.id] = tier_title
            else:
                if role_key in role_users:
                    role_users[role_key].append(user)
                    user_tier_map[user.id] = {
                        'TEAM': 'Team Members',
                        'KOFI': 'Ko-fi Supporters', 
                        'GUEST': 'Guest Users'
                    }.get(role_key, role_key)

        # Generate monthly statistics for last 6 months
        monthly_stats = []
        total_stats = {
            "albums": 0,
            "tracks": 0,
            "book_requests": 0,
            "total_users": len(all_users)
        }
        
        now = datetime.now(timezone.utc)
        
        for i in range(6):
            # Calculate month boundaries
            month_date = datetime(now.year, now.month, 1, tzinfo=timezone.utc) - timedelta(days=i*30)
            month_start = datetime(month_date.year, month_date.month, 1, tzinfo=timezone.utc)
            
            if month_date.month == 12:
                next_month = datetime(month_date.year + 1, 1, 1, tzinfo=timezone.utc)
            else:
                next_month = datetime(month_date.year, month_date.month + 1, 1, tzinfo=timezone.utc)
            
            month_end = next_month - timedelta(microseconds=1)
            month_name = month_date.strftime("%B %Y")
            month_key = month_date.strftime("%m/%y")

            # Get all downloads for this month with user info
            month_downloads_sql = """
            SELECT 
                dh.user_id,
                dh.download_type,
                dh.status,
                COUNT(*) as count
            FROM download_history dh
            WHERE dh.user_id IN :user_ids
            AND dh.downloaded_at >= :month_start 
            AND dh.downloaded_at <= :month_end
            GROUP BY dh.user_id, dh.download_type, dh.status
            """
            
            user_ids = [user.id for user in all_users]
            if not user_ids:
                continue
                
            month_results = db.execute(text(month_downloads_sql), {
                "user_ids": tuple(user_ids),
                "month_start": month_start,
                "month_end": month_end
            }).fetchall()

            # Process month data by tier
            month_data = {
                "month_name": month_name,
                "month_key": month_key,
                "total_downloads": 0,
                "successful_downloads": 0,
                "failed_downloads": 0,
                "album_downloads": {"success": 0, "failed": 0},
                "track_downloads": {"success": 0, "failed": 0},
                "tier_breakdown": {}
            }

            # Initialize tier breakdown
            for tier in tiers:
                month_data["tier_breakdown"][tier.title] = {
                    "albums": {"success": 0, "failed": 0},
                    "tracks": {"success": 0, "failed": 0},
                    "total": 0,
                    "amount_cents": tier.amount_cents
                }
            
            # Add role-based categories
            for role_name in ['Team Members', 'Ko-fi Supporters', 'Guest Users']:
                month_data["tier_breakdown"][role_name] = {
                    "albums": {"success": 0, "failed": 0},
                    "tracks": {"success": 0, "failed": 0},
                    "total": 0,
                    "amount_cents": 0
                }

            # Process each download result
            for result in month_results:
                user_id = result.user_id
                download_type = result.download_type
                status = result.status
                count = result.count
                
                # Get tier for this user
                tier_name = user_tier_map.get(user_id, 'Unknown')
                
                if tier_name not in month_data["tier_breakdown"]:
                    continue
                
                # Update overall month totals
                month_data["total_downloads"] += count
                if status == 'success':
                    month_data["successful_downloads"] += count
                    month_data[f"{download_type}_downloads"]["success"] += count
                else:
                    month_data["failed_downloads"] += count
                    month_data[f"{download_type}_downloads"]["failed"] += count

                # Update tier-specific counts
                tier_data = month_data["tier_breakdown"][tier_name]
                tier_data["total"] += count
                if status == 'success':
                    tier_data[download_type + "s"]["success"] += count
                else:
                    tier_data[download_type + "s"]["failed"] += count

            # Remove tiers with no activity in this month
            month_data["tier_breakdown"] = {
                tier: data for tier, data in month_data["tier_breakdown"].items() 
                if data["total"] > 0
            }

            # Update overall totals
            total_stats["albums"] += month_data["album_downloads"]["success"]
            total_stats["tracks"] += month_data["track_downloads"]["success"]

            # Only include months with activity
            if month_data["total_downloads"] > 0:
                monthly_stats.append(month_data)

        # Get total book requests
        user_ids = [user.id for user in all_users]
        if user_ids:
            if len(user_ids) == 1:
                book_sql = """
                SELECT COUNT(*) as count
                FROM book_requests
                WHERE user_id = :user_id AND status != 'rejected'
                """
                book_result = db.execute(text(book_sql), {"user_id": user_ids[0]}).first()
            else:
                book_sql = """
                SELECT COUNT(*) as count
                FROM book_requests
                WHERE user_id IN :user_ids AND status != 'rejected'
                """
                book_result = db.execute(text(book_sql), {"user_ids": tuple(user_ids)}).first()
            
            total_stats["book_requests"] = book_result.count

        # Sort by most recent first
        monthly_stats.sort(key=lambda x: datetime.strptime(x["month_name"], "%B %Y"), reverse=True)

        logger.info(f"Returning monthly statistics for {len(monthly_stats)} months with tier breakdowns")

        return {
            "monthly_stats": monthly_stats,
            "total_stats": total_stats
        }

    except Exception as e:
        logger.error(f"Error loading statistics: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/statistics")
@verify_role_permission(["creator"])
async def statistics_page(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Statistics page showing download totals by month and tier for all users except creators"""
    try:
        # Get all active tiers for this creator
        tiers = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == current_user.id,
                CampaignTier.is_active == True
            )
        ).order_by(CampaignTier.amount_cents.desc()).all()

        # Get all users belonging to this creator (excluding creators themselves)
        all_users = db.query(User).filter(
            and_(
                User.created_by == current_user.id,
                User.role != UserRole.CREATOR,  # Exclude creator downloads
                User.is_active == True
            )
        ).all()

        logger.info(f"Found {len(all_users)} non-creator users and {len(tiers)} tiers for creator {current_user.id}")

        if not all_users:
            return templates.TemplateResponse(
                "statistics.html",
                {
                    "request": request,
                    "user": current_user,
                    "monthly_stats": [],
                    "total_stats": {"albums": 0, "tracks": 0, "book_requests": 0, "total_users": 0},
                    "permissions": get_user_permissions(current_user)
                }
            )

        # Group users by tier and role
        user_tier_map = {}
        tier_users = {}
        role_users = {
            'TEAM': [],
            'KOFI': [],
            'GUEST': []
        }
        
        # Initialize tier groups
        for tier in tiers:
            tier_users[tier.title] = []
        
        # Categorize users
        for user in all_users:
            role_key = user.role.value if hasattr(user.role, 'value') else str(user.role)
            
            if role_key == 'PATREON' and user.patreon_tier_data:
                tier_title = user.patreon_tier_data.get('title', 'Unknown Tier')
                if tier_title in tier_users:
                    tier_users[tier_title].append(user)
                    user_tier_map[user.id] = tier_title
            else:
                if role_key in role_users:
                    role_users[role_key].append(user)
                    user_tier_map[user.id] = {
                        'TEAM': 'Team Members',
                        'KOFI': 'Ko-fi Supporters', 
                        'GUEST': 'Guest Users'
                    }.get(role_key, role_key)

        # Generate monthly statistics for last 6 months
        monthly_stats = []
        total_stats = {"albums": 0, "tracks": 0, "book_requests": 0, "total_users": len(all_users)}
        
        now = datetime.now(timezone.utc)
        
        for i in range(6):
            # Calculate month boundaries
            month_date = datetime(now.year, now.month, 1, tzinfo=timezone.utc) - timedelta(days=i*30)
            month_start = datetime(month_date.year, month_date.month, 1, tzinfo=timezone.utc)
            
            if month_date.month == 12:
                next_month = datetime(month_date.year + 1, 1, 1, tzinfo=timezone.utc)
            else:
                next_month = datetime(month_date.year, month_date.month + 1, 1, tzinfo=timezone.utc)
            
            month_end = next_month - timedelta(microseconds=1)
            month_name = month_date.strftime("%B %Y")
            month_key = month_date.strftime("%m/%y")

            # Get all downloads for this month with user info
            month_downloads_sql = """
            SELECT 
                dh.user_id,
                dh.download_type,
                dh.status,
                COUNT(*) as count
            FROM download_history dh
            WHERE dh.user_id IN :user_ids
            AND dh.downloaded_at >= :month_start 
            AND dh.downloaded_at <= :month_end
            GROUP BY dh.user_id, dh.download_type, dh.status
            """
            
            user_ids = [user.id for user in all_users]
            if not user_ids:
                continue
                
            month_results = db.execute(text(month_downloads_sql), {
                "user_ids": tuple(user_ids),
                "month_start": month_start,
                "month_end": month_end
            }).fetchall()

            # Process month data by tier
            month_data = {
                "month_name": month_name,
                "month_key": month_key,
                "total_downloads": 0,
                "successful_downloads": 0,
                "failed_downloads": 0,
                "album_downloads": {"success": 0, "failed": 0},
                "track_downloads": {"success": 0, "failed": 0},
                "tier_breakdown": {}
            }

            # Initialize tier breakdown
            for tier in tiers:
                month_data["tier_breakdown"][tier.title] = {
                    "albums": {"success": 0, "failed": 0},
                    "tracks": {"success": 0, "failed": 0},
                    "total": 0,
                    "amount_cents": tier.amount_cents
                }
            
            # Add role-based categories
            for role_name in ['Team Members', 'Ko-fi Supporters', 'Guest Users']:
                month_data["tier_breakdown"][role_name] = {
                    "albums": {"success": 0, "failed": 0},
                    "tracks": {"success": 0, "failed": 0},
                    "total": 0,
                    "amount_cents": 0
                }

            # Process each download result
            for result in month_results:
                user_id = result.user_id
                download_type = result.download_type
                status = result.status
                count = result.count
                
                # Get tier for this user
                tier_name = user_tier_map.get(user_id, 'Unknown')
                
                if tier_name not in month_data["tier_breakdown"]:
                    continue
                
                # Update overall month totals
                month_data["total_downloads"] += count
                if status == 'success':
                    month_data["successful_downloads"] += count
                    month_data[f"{download_type}_downloads"]["success"] += count
                else:
                    month_data["failed_downloads"] += count
                    month_data[f"{download_type}_downloads"]["failed"] += count

                # Update tier-specific counts
                tier_data = month_data["tier_breakdown"][tier_name]
                tier_data["total"] += count
                if status == 'success':
                    tier_data[download_type + "s"]["success"] += count
                else:
                    tier_data[download_type + "s"]["failed"] += count

            # Remove tiers with no activity in this month
            month_data["tier_breakdown"] = {
                tier: data for tier, data in month_data["tier_breakdown"].items() 
                if data["total"] > 0
            }

            # Update overall totals
            total_stats["albums"] += month_data["album_downloads"]["success"]
            total_stats["tracks"] += month_data["track_downloads"]["success"]

            # Only include months with activity
            if month_data["total_downloads"] > 0:
                monthly_stats.append(month_data)

        # Get total book requests
        user_ids = [user.id for user in all_users]
        if user_ids:
            if len(user_ids) == 1:
                book_sql = """
                SELECT COUNT(*) as count
                FROM book_requests
                WHERE user_id = :user_id AND status != 'rejected'
                """
                book_result = db.execute(text(book_sql), {"user_id": user_ids[0]}).first()
            else:
                book_sql = """
                SELECT COUNT(*) as count
                FROM book_requests
                WHERE user_id IN :user_ids AND status != 'rejected'
                """
                book_result = db.execute(text(book_sql), {"user_ids": tuple(user_ids)}).first()
            
            total_stats["book_requests"] = book_result.count

        # Sort by most recent first
        monthly_stats.sort(key=lambda x: datetime.strptime(x["month_name"], "%B %Y"), reverse=True)

        logger.info(f"Returning monthly statistics for {len(monthly_stats)} months with tier breakdowns")

        return templates.TemplateResponse(
            "statistics.html",
            {
                "request": request,
                "user": current_user,
                "monthly_stats": monthly_stats,
                "total_stats": total_stats,
                "permissions": get_user_permissions(current_user)
            }
        )

    except Exception as e:
        logger.error(f"Error loading statistics: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/creator/book-requests/reset")
@verify_role_permission(["creator"])
async def reset_all_book_requests(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Reset all monthly book request counts back to zero (admin function)"""
    try:
        creator_id = current_user.id
        
        # Find the current month
        current_month = datetime.now(timezone.utc).strftime("%Y-%m")
        
        # Find all non-rejected book requests in the current month
        book_requests = db.query(BookRequest).join(
            User, BookRequest.user_id == User.id
        ).filter(
            and_(
                User.created_by == creator_id,
                BookRequest.month_year == current_month,
                BookRequest.status != BookRequestStatus.REJECTED.value
            )
        ).all()
        
        # Mark all as rejected with a system message
        update_count = 0
        for br in book_requests:
            if br.status == BookRequestStatus.PENDING:
                br.status = BookRequestStatus.REJECTED
                br.response_message = "Request reset by system administrator"
                br.responded_by_id = current_user.id
                br.response_date = datetime.now(timezone.utc)
                update_count += 1
        
        db.commit()
        
        return {
            "status": "success",
            "message": f"Reset {update_count} pending book requests for the current month",
            "reset_count": update_count
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Error resetting book requests: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/creator/pin-management")
async def creator_pin_management_page(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """PIN management page at user-friendly URL"""
    # Verify creator role
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Creator access required")
        
    try:
        # Same implementation as in the original router
        history = await get_pin_history(db, current_user.id)
        
        scheduled_rotation = db.query(ScheduledTask).filter(
            and_(
                ScheduledTask.user_id == current_user.id,
                ScheduledTask.task_type == "pin_rotation",
                ScheduledTask.status == "pending",
                ScheduledTask.scheduled_for > datetime.now(timezone.utc)
            )
        ).first()
        
        return templates.TemplateResponse(
            "creator_management.html",
            {
                "request": request,
                "user": current_user,
                "permissions": get_user_permissions(current_user),
                "current_pin": current_user.creator_pin,
                "pin_history": history,
                "scheduled_rotation": scheduled_rotation.scheduled_for.isoformat() if scheduled_rotation else None
            }
        )
    except Exception as e:
        logger.error(f"Error loading PIN management page: {str(e)}")
        raise HTTPException(status_code=500, detail="Error loading PIN management page")


@app.get("/api/creator/tiers/{tier_id}/voices")
@verify_role_permission(["creator"])
async def get_tier_voices(
    tier_id: str,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get voices for a specific tier"""
    if not tier_id or tier_id.strip() == "":
        raise HTTPException(status_code=400, detail="Tier ID cannot be empty")
    
    tier_id = unquote(tier_id)
    
    tier = db.query(CampaignTier).filter(
        and_(
            CampaignTier.creator_id == current_user.id,
            CampaignTier.title == tier_id,
            CampaignTier.is_active == True
        )
    ).first()
    
    if not tier:
        logger.info(f"Tier not found for creator {current_user.id} with title: '{tier_id}'")
        available_tiers = db.query(CampaignTier.title).filter(
            CampaignTier.creator_id == current_user.id,
            CampaignTier.is_active == True
        ).all()
        logger.info(f"Available tiers: {[t.title for t in available_tiers]}")
        raise HTTPException(status_code=404, detail=f"Tier '{tier_id}' not found")
    
    all_voices = db.query(AvailableVoice).filter(
        AvailableVoice.is_active == True
    ).order_by(AvailableVoice.language_code, AvailableVoice.display_name).all()
    
    return {
        "tier_id": tier_id,
        "voices": tier.voice_access or [],  # ✅ FIXED: No hardcoded default
        "all_available_voices": [voice.voice_id for voice in all_voices],
        "voice_details": [
            {
                "voice_id": voice.voice_id,
                "display_name": voice.display_name,
                "language": voice.language_code,
                "gender": voice.gender,
                "is_premium": voice.is_premium
            }
            for voice in all_voices
        ]
    }
@app.patch("/api/creator/tiers/{tier_id}/voices")
@verify_role_permission(["creator"])
async def bulk_update_tier_voices(
    tier_id: str,
    voice_data: dict,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Bulk update voices for a tier (atomic operation with exclusive assignment)"""
    # Handle empty tier_id
    if not tier_id or tier_id.strip() == "":
        raise HTTPException(status_code=400, detail="Tier ID cannot be empty")
    
    # URL decode the tier_id
    tier_id = unquote(tier_id)
    
    # Validate request data
    action = voice_data.get('action')  # 'add' or 'remove'
    voice_ids = voice_data.get('voice_ids', [])
    
    if action not in ['add', 'remove']:
        raise HTTPException(status_code=400, detail="Action must be 'add' or 'remove'")
    
    if not voice_ids or not isinstance(voice_ids, list):
        raise HTTPException(status_code=400, detail="voice_ids must be a non-empty list")
    
    # Get the tier
    tier = db.query(CampaignTier).filter(
        and_(
            CampaignTier.creator_id == current_user.id,
            CampaignTier.title == tier_id,
            CampaignTier.is_active == True
        )
    ).first()
    
    if not tier:
        raise HTTPException(status_code=404, detail=f"Tier '{tier_id}' not found")
    
    # Validate all voice IDs exist and are active
    valid_voices = db.query(AvailableVoice.voice_id).filter(
        and_(
            AvailableVoice.voice_id.in_(voice_ids),
            AvailableVoice.is_active == True
        )
    ).all()
    valid_voice_ids = [v.voice_id for v in valid_voices]
    
    # Check for invalid voices
    invalid_voices = [v for v in voice_ids if v not in valid_voice_ids]
    if invalid_voices:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid or inactive voices: {', '.join(invalid_voices)}"
        )
    
    # Get current voices
    current_voices = list(tier.voice_access or [])
    
    # ✅ REMOVED: All the "default voice" special handling logic
    # Now ALL voices get exclusive assignment, including Ava
    
    # Perform the bulk operation
    updated_voices = current_voices.copy()
    
    if action == 'add':
        # ✅ FIXED: Exclusive assignment for ALL voices (no exceptions)
        voices_to_add = [v for v in voice_ids if v not in current_voices]
        
        if voices_to_add:
            # Remove these voices from all other tiers (exclusive assignment)
            other_tiers = db.query(CampaignTier).filter(
                and_(
                    CampaignTier.creator_id == current_user.id,
                    CampaignTier.is_active == True,
                    CampaignTier.title != tier_id
                )
            ).all()
            
            removed_from_tiers = []
            for other_tier in other_tiers:
                if other_tier.voice_access:
                    original_voices = other_tier.voice_access.copy()
                    
                    # Remove ALL voices being added from other tiers
                    other_tier.voice_access = [
                        v for v in other_tier.voice_access 
                        if v not in voices_to_add
                    ]
                    
                    # Track removals for logging
                    removed_voices = [v for v in voices_to_add if v in original_voices]
                    if removed_voices:
                        removed_from_tiers.append({
                            'tier': other_tier.title,
                            'voices': removed_voices
                        })
            
            if removed_from_tiers:
                logger.info(f"Exclusive assignment: Removed voices from other tiers: {removed_from_tiers}")
        
        # Add voices to current tier
        for voice_id in voice_ids:
            if voice_id not in updated_voices:
                updated_voices.append(voice_id)
        
        operation_count = len(voices_to_add)
        
    elif action == 'remove':
        # ✅ FIXED: Allow removal of ANY voice (no special protection)
        voices_to_remove = voice_ids
        updated_voices = [v for v in updated_voices if v not in voices_to_remove]
        operation_count = len([v for v in voices_to_remove if v in current_voices])
    
    # Update the tier (atomic operation)
    tier.voice_access = updated_voices
    
    try:
        db.commit()
        logger.info(f"Bulk {action} operation completed for tier '{tier_id}': {operation_count} voices affected")
        logger.info(f"Tier '{tier_id}' now has {len(updated_voices)} voices: {updated_voices}")
        
    except Exception as e:
        db.rollback()
        logger.error(f"Database error during bulk voice {action}: {str(e)}")
        raise HTTPException(status_code=500, detail="Database error occurred")
    
    return {
        "status": "success",
        "tier_id": tier_id,
        "action": action,
        "voices_affected": operation_count,
        "total_voices": len(updated_voices),
        "current_voices": updated_voices,
        "exclusive_assignment": action == 'add'
    }
@app.post("/api/creator/tiers/{tier_id}/voices/{voice_id}")
@verify_role_permission(["creator"])
async def add_voice_to_tier(
    tier_id: str,
    voice_id: str,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Add a single voice to a tier (legacy endpoint)"""
    # Use the bulk endpoint internally for consistency
    voice_data = {
        "action": "add",
        "voice_ids": [voice_id]
    }
    
    try:
        result = await bulk_update_tier_voices(tier_id, voice_data, current_user, db)
        return {
            "status": "success",
            "tier_id": tier_id,
            "voice_added": voice_id,
            "voices": result["current_voices"]
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in legacy add_voice_to_tier: {str(e)}")
        raise HTTPException(status_code=500, detail="Error adding voice to tier")


@app.delete("/api/creator/tiers/{tier_id}/voices/{voice_id}")
@verify_role_permission(["creator"])
async def remove_voice_from_tier(
    tier_id: str,
    voice_id: str,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Remove a single voice from a tier (legacy endpoint)"""
    # ✅ FIXED: Remove hardcoded Ava protection - let the bulk function handle default protection
    
    # Use the bulk endpoint internally for consistency
    voice_data = {
        "action": "remove",
        "voice_ids": [voice_id]
    }
    
    try:
        result = await bulk_update_tier_voices(tier_id, voice_data, current_user, db)
        return {
            "status": "success",
            "tier_id": tier_id,
            "voice_removed": voice_id,
            "voices": result["current_voices"]
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in legacy remove_voice_from_tier: {str(e)}")
        raise HTTPException(status_code=500, detail="Error removing voice from tier")

# System-wide voice management endpoints
@app.post("/api/creator/voices")
@verify_role_permission(["creator"])
async def add_voice(
    voice_data: dict,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Add new voice to the system (no premium field)"""
    try:
        # Check if voice already exists
        existing_voice = db.query(AvailableVoice).filter(
            AvailableVoice.voice_id == voice_data["voice_id"]
        ).first()
        
        if existing_voice:
            if existing_voice.is_active:
                raise HTTPException(status_code=400, detail=f"Voice '{voice_data['voice_id']}' already exists")
            else:
                # Reactivate existing voice
                existing_voice.is_active = True
                existing_voice.display_name = voice_data["display_name"]
                existing_voice.language_code = voice_data.get("language_code", "en-US")
                existing_voice.gender = voice_data.get("gender")
                # ✅ REMOVED: existing_voice.is_premium = voice_data.get("is_premium", False)
                db.commit()
                return {"status": "success", "voice_reactivated": voice_data["voice_id"]}
        
        new_voice = AvailableVoice(
            voice_id=voice_data["voice_id"],
            display_name=voice_data["display_name"],
            language_code=voice_data.get("language_code", "en-US"),
            gender=voice_data.get("gender")
            # ✅ REMOVED: is_premium=voice_data.get("is_premium", False)
        )
        
        db.add(new_voice)
        db.commit()
        
        return {"status": "success", "voice_added": voice_data["voice_id"]}
    except Exception as e:
        db.rollback()
        logger.error(f"Error adding voice: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Error adding voice: {str(e)}")

@app.delete("/api/creator/voices/{voice_id}")
@verify_role_permission(["creator"])
async def delete_voice(
    voice_id: str,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Remove voice from system"""
    # ✅ FIXED: Remove hardcoded Ava protection - check actual defaults instead
    
    # Check if this voice is used as default in any tracks
    tracks_using_voice = db.query(TTSTrackMeta).join(Track).filter(
        Track.created_by_id == current_user.id,
        TTSTrackMeta.default_voice == voice_id
    ).first()
    
    if tracks_using_voice:
        raise HTTPException(
            status_code=400, 
            detail=f"Cannot delete voice '{voice_id}' - it's used as default in existing tracks"
        )
    
    voice = db.query(AvailableVoice).filter(
        AvailableVoice.voice_id == voice_id
    ).first()
    
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")
    
    # Set inactive instead of deleting
    voice.is_active = False
    
    # Remove this voice from all tiers that have it
    tiers_with_voice = db.query(CampaignTier).filter(
        CampaignTier.voice_access.contains([voice_id])
    ).all()
    
    for tier in tiers_with_voice:
        if tier.voice_access and voice_id in tier.voice_access:
            tier.voice_access.remove(voice_id)
            # ✅ FIXED: Don't add hardcoded default - let tiers manage their own voices
    
    db.commit()
    
    return {"status": "success", "voice_removed": voice_id}

# Additional endpoint to get tier information for dropdowns
@app.get("/api/creator/tiers/list")
@verify_role_permission(["creator"])
async def get_creator_tiers_list(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get a list of all creator tiers for dropdowns"""
    tiers = db.query(CampaignTier).filter(
        and_(
            CampaignTier.creator_id == current_user.id,
            CampaignTier.is_active == True
        )
    ).order_by(CampaignTier.amount_cents).all()
    
    return {
        "tiers": [
            {
                "id": tier.id,
                "title": tier.title,
                "amount_cents": tier.amount_cents,
                "amount_display": f"${tier.amount_cents/100:.2f}/month",
                "is_kofi": tier.platform_type == 'KOFI',
                "patron_count": getattr(tier, 'patron_count', 0),
                "voice_access": tier.voice_access or []  # ✅ FIXED: No hardcoded default
            }
            for tier in tiers
        ]
    }

@app.get("/api/creator/voices")
@verify_role_permission(["creator"])
async def get_all_available_voices(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get all available voices in the system with tier assignments"""
    voices = db.query(AvailableVoice).filter(
        AvailableVoice.is_active == True
    ).order_by(AvailableVoice.language_code, AvailableVoice.display_name).all()
    
    tiers = db.query(CampaignTier).filter(
        and_(
            CampaignTier.creator_id == current_user.id,
            CampaignTier.is_active == True
        )
    ).all()
    
    # Build voice assignment map
    voice_assignments = {}
    for tier in tiers:
        tier_voices = tier.voice_access or []  # ✅ FIXED: No hardcoded default
        for voice_id in tier_voices:
            if voice_id not in voice_assignments:
                voice_assignments[voice_id] = []
            voice_assignments[voice_id].append({
                "tier_title": tier.title,
                "tier_amount": f"${tier.amount_cents/100:.2f}",
                "is_kofi": tier.platform_type == 'KOFI'
            })
    
    # ✅ FIXED: Import the function from enhanced_tts_api_voice.py instead of duplicating
    from enhanced_tts_api_voice import check_track_access
    
    return {
        "voices": [
            {
                "voice_id": voice.voice_id,
                "display_name": voice.display_name,
                "language_code": voice.language_code,
                "gender": voice.gender,
                "assigned_tiers": voice_assignments.get(voice.voice_id, []),
                "has_access": check_voice_access(current_user, voice.voice_id, db)
            }
            for voice in voices
        ]
    }

def check_voice_access(user: User, voice_id: str, db: Session) -> bool:
    """
    FIXED: No hardcoded defaults - check actual track defaults per user's accessible tracks
    """
    try:
        # Check if voice exists and is active
        voice = db.query(AvailableVoice).filter(
            AvailableVoice.voice_id == voice_id,
            AvailableVoice.is_active == True
        ).first()
        
        if not voice:
            logger.warning(f"Voice {voice_id} not found or inactive in database")
            return False
        
        # Creators and team members always have access
        if user.is_creator or user.is_team:
            logger.info(f"Voice access: Creator/team access granted to user {user.id} for voice {voice_id}")
            return True
        
        # Get creator ID
        creator_id = user.created_by if user.created_by else user.id
        
        # ✅ NEW: Check if this voice is used as default in ANY track this user can access
        user_accessible_tracks_query = db.query(TTSTrackMeta.default_voice).join(Track).filter(
            Track.creator_id == creator_id,
            TTSTrackMeta.default_voice == voice_id
        )
        
        # Add additional access checks based on user type
        if user.is_patreon or user.is_kofi or user.is_guest_trial:
            # For patrons, they can access tracks from their creator
            pass  # The query above already filters by creator_id
        elif user.created_by:
            # For team members, they can access their creator's tracks
            pass  # The query above already filters by creator_id
        
        default_voice_track = user_accessible_tracks_query.first()
        
        if default_voice_track:
            logger.info(f"Voice access: DEFAULT VOICE access granted to user {user.id} for voice {voice_id} (used as default in accessible tracks)")
            return True
        
        # Continue with tier-based access checking
        tier_data = user.patreon_tier_data if user.patreon_tier_data else {}
        user_amount = tier_data.get("amount_cents", 0)
        
        # Get ALL tiers that contain this voice
        all_tiers_with_voice = db.query(CampaignTier).filter(
            CampaignTier.creator_id == creator_id,
            CampaignTier.is_active == True,
            CampaignTier.voice_access.contains([voice_id])
        ).all()
        
        if not all_tiers_with_voice:
            logger.info(f"Voice {voice_id} not found in any tier for creator {creator_id}")
            return False
        
        # Use HIGHEST tier requirement for restriction enforcement
        paid_tiers = [tier for tier in all_tiers_with_voice if tier.amount_cents > 0]
        
        if paid_tiers:
            required_tier = max(paid_tiers, key=lambda t: t.amount_cents)
            required_amount = required_tier.amount_cents
            
            logger.info(f"RESTRICTION CHECK: Voice {voice_id} requires {required_amount} cents (tier: {required_tier.title}), user has {user_amount} cents")
        else:
            # Voice is ONLY in free tiers
            required_amount = 0
            logger.info(f"RESTRICTION CHECK: Voice {voice_id} is truly free (only in $0 tiers)")
        
        # Check if user meets the requirement
        if (user.is_patreon or user.is_kofi or user.is_guest_trial) and tier_data:
            # Check base tier amount
            if user_amount >= required_amount:
                logger.info(f"ACCESS GRANTED: User {user.email} meets tier requirement ({user_amount} >= {required_amount})")
                return True
            
            # Special case for Ko-fi users with donations
            if user.is_kofi and tier_data.get('has_donations', False):
                donation_amount = tier_data.get('donation_amount_cents', 0)
                total_amount = user_amount + donation_amount
                
                if total_amount >= required_amount:
                    logger.info(f"ACCESS GRANTED: Ko-fi user {user.email} meets requirement with donations ({total_amount} >= {required_amount})")
                    return True
        
        # Access denied
        logger.info(f"ACCESS DENIED: User {user.email} does not meet tier requirement ({user_amount} < {required_amount})")
        return False
        
    except Exception as e:
        logger.error(f"Error checking voice access for user {user.id}, voice {voice_id}: {str(e)}")
        # Conservative fallback - no hardcoded defaults
        return False


@app.get("/api/creator/tiers/{tier_id}/read-along")
@verify_role_permission(["creator"])
async def get_tier_read_along_access(
    tier_id: str,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get read-along access for a specific tier"""
    try:
        if not tier_id or tier_id.strip() == "":
            raise HTTPException(status_code=400, detail="Tier ID cannot be empty")
        
        tier_id = unquote(tier_id)
        logger.info(f"Looking up tier: '{tier_id}' for creator {current_user.id}")
        
        # Debug: List all tiers for this creator
        all_tiers = db.query(CampaignTier).filter(
            CampaignTier.creator_id == current_user.id
        ).all()
        logger.info(f"All tiers for creator {current_user.id}: {[t.title for t in all_tiers]}")
        
        tier = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == current_user.id,
                CampaignTier.title == tier_id,
                CampaignTier.is_active == True
            )
        ).first()
        
        if not tier:
            logger.warning(f"Tier '{tier_id}' not found for creator {current_user.id}")
            # Try without is_active filter
            tier_inactive = db.query(CampaignTier).filter(
                and_(
                    CampaignTier.creator_id == current_user.id,
                    CampaignTier.title == tier_id
                )
            ).first()
            if tier_inactive:
                logger.warning(f"Tier '{tier_id}' exists but is inactive: {tier_inactive.is_active}")
            raise HTTPException(status_code=404, detail=f"Tier '{tier_id}' not found")
        
        return {
            "tier_id": tier_id,
            "read_along_access": getattr(tier, 'read_along_access', False),
            "tier_title": tier.title,
            "amount_display": f"${tier.amount_cents/100:.2f}/month"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting read-along access for tier '{tier_id}': {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get read-along access")

@app.patch("/api/creator/tiers/{tier_id}/read-along")
@verify_role_permission(["creator"])
async def update_tier_read_along_access(
    tier_id: str,
    access_data: dict,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Update read-along access for a specific tier"""
    try:
        if not tier_id or tier_id.strip() == "":
            raise HTTPException(status_code=400, detail="Tier ID cannot be empty")
        
        tier_id = unquote(tier_id)
        read_along_access = access_data.get('read_along_access', False)
        
        logger.info(f"Updating read-along access for tier '{tier_id}' to {read_along_access}")
        
        tier = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == current_user.id,
                CampaignTier.title == tier_id,
                CampaignTier.is_active == True
            )
        ).first()
        
        if not tier:
            logger.warning(f"Tier '{tier_id}' not found for creator {current_user.id}")
            raise HTTPException(status_code=404, detail=f"Tier '{tier_id}' not found")
        
        # Update read-along access
        tier.read_along_access = read_along_access
        
        db.commit()
        logger.info(f"Successfully updated read-along access for tier '{tier_id}'")
        
        action = "granted" if read_along_access else "removed"
        return {
            "status": "success",
            "tier_id": tier_id,
            "read_along_access": read_along_access,
            "message": f"Read-along access {action} for {tier.title}"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating read-along access for tier '{tier_id}': {str(e)}")
        raise HTTPException(status_code=500, detail="Database error occurred")

def check_read_along_access(user: User, db: Session) -> tuple[bool, Optional[str]]:
    """Check if user has access to read-along feature"""
    try:
        # Creators always have access
        if user.is_creator:
            return True, None
        
        # Team members always have access (same as creators)
        if user.is_team:
            return True, None
        
        creator_id = user.created_by if user.created_by else user.id
        
        # Get user's tier data (same pattern as existing voice access checking)
        tier_data = user.patreon_tier_data if user.patreon_tier_data else {}
        tier_title = tier_data.get('title')
        
        if not tier_title:
            return False, "Read-along feature requires a subscription"
        
        # Find user's tier and check read_along_access
        user_tier = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == creator_id,
                CampaignTier.title == tier_title,
                CampaignTier.is_active == True
            )
        ).first()
        
        if not user_tier:
            return False, "Invalid subscription tier"
        
        # Check if tier has read-along access
        if getattr(user_tier, 'read_along_access', False):
            return True, None
        
        return False, f"Read-along feature not included in '{tier_title}' tier"
        
    except Exception as e:
        logger.error(f"Error checking read-along access: {str(e)}")
        return False, "Error checking read-along access"

# Updated read-along endpoint to check tier access

# Main Entry Point
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8001,
        workers=4,  # Number of worker processes
        loop="uvloop",  # Faster event loop implementation
        limit_concurrency=5  # Limit concurrent connections per worker
    )
