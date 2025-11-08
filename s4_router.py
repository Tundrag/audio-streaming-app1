# s4_router.py - Complete S4 API router

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query, Request
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text, and_, desc
from database import get_db
from auth import login_required
from models import User, Track, Album, UserDownload, DownloadType
from mega_s4_client import mega_s4_client
from storage import storage  # Your S4-powered storage
from typing import Optional, List, Dict
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json

logger = logging.getLogger(__name__)

s4_router = APIRouter(prefix="/api/s4", tags=["MEGA S4 Object Storage"])

# ===================================================================
# S4 System Status & Health Endpoints
# ===================================================================

@s4_router.get("/status")
async def get_s4_system_status():
    """Get S4 system status and bucket statistics"""
    try:
        bucket_info = await mega_s4_client.get_bucket_info()
        
        return {
            "status": "operational",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bucket": {
                "name": bucket_info['bucket_name'],
                "region": bucket_info['region'],
                "object_count": bucket_info.get('object_count', 0),
                "total_size_mb": bucket_info.get('total_size_mb', 0),
                "endpoint": bucket_info['endpoint']
            },
            "features": {
                "direct_downloads": True,
                "presigned_urls": True,
                "s3_compatible": True
            }
        }
    except Exception as e:
        logger.error(f"S4 status check failed: {e}")
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        )

@s4_router.get("/health")
async def s4_health_check():
    """Quick health check for S4 connectivity"""
    try:
        # Quick connectivity test
        bucket_info = await mega_s4_client.get_bucket_info()
        return {
            "healthy": True,
            "response_time_ms": 0,  # You could measure this
            "bucket_accessible": True
        }
    except Exception as e:
        logger.error(f"S4 health check failed: {e}")
        return JSONResponse(
            status_code=503,
            content={
                "healthy": False,
                "error": str(e)
            }
        )

# ===================================================================
# Migration Management Endpoints
# ===================================================================

@s4_router.get("/migration/status")
async def get_migration_status(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get detailed migration status for user's content"""
    try:
        # Allow creators to see their own stats, others see global stats
        if current_user.is_creator:
            # Creator-specific statistics
            total_query = text("""
                SELECT COUNT(*) FROM tracks 
                WHERE created_by = :creator_id
            """)
            migrated_query = text("""
                SELECT COUNT(*) FROM tracks 
                WHERE created_by = :creator_id AND s4_available = true
            """)
            
            total_tracks = db.execute(total_query, {"creator_id": current_user.id}).scalar()
            migrated_tracks = db.execute(migrated_query, {"creator_id": current_user.id}).scalar()
            
            # Get recent migrations
            recent_query = text("""
                SELECT id, title, s4_uploaded_at 
                FROM tracks 
                WHERE created_by = :creator_id AND s4_available = true 
                ORDER BY s4_uploaded_at DESC 
                LIMIT 10
            """)
            recent_migrations = db.execute(recent_query, {"creator_id": current_user.id}).fetchall()
            
        else:
            # Global statistics for non-creators
            total_query = text("SELECT COUNT(*) FROM tracks")
            migrated_query = text("SELECT COUNT(*) FROM tracks WHERE s4_available = true")
            
            total_tracks = db.execute(total_query).scalar()
            migrated_tracks = db.execute(migrated_query).scalar()
            recent_migrations = []
        
        percentage = (migrated_tracks / total_tracks * 100) if total_tracks > 0 else 0
        
        return {
            "total_tracks": total_tracks,
            "migrated_tracks": migrated_tracks,
            "pending_migration": total_tracks - migrated_tracks,
            "percentage_complete": round(percentage, 1),
            "recent_migrations": [
                {
                    "track_id": str(m.id),
                    "title": m.title,
                    "migrated_at": m.s4_uploaded_at.isoformat() if m.s4_uploaded_at else None
                }
                for m in recent_migrations
            ],
            "user_type": "creator" if current_user.is_creator else "viewer"
        }
        
    except Exception as e:
        logger.error(f"Error getting migration status: {e}")
        raise HTTPException(status_code=500, detail="Failed to get migration status")

@s4_router.post("/migration/start")
async def start_bulk_migration(
    background_tasks: BackgroundTasks,
    batch_size: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Start bulk migration of tracks to S4 (creators only)"""
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Creator access required")
    
    try:
        # Get count of tracks needing migration
        pending_query = text("""
            SELECT COUNT(*) FROM tracks 
            WHERE created_by = :creator_id 
            AND s4_available = false 
            AND file_url IS NOT NULL
        """)
        pending_count = db.execute(pending_query, {"creator_id": current_user.id}).scalar()
        
        if pending_count == 0:
            return {
                "message": "No tracks need migration",
                "pending_count": 0,
                "batch_size": 0
            }
        
        # Start background migration
        background_tasks.add_task(
            bulk_migrate_tracks,
            creator_id=current_user.id,
            batch_size=min(batch_size, pending_count)
        )
        
        logger.info(f"Started migration for creator {current_user.id}: {batch_size} tracks")
        
        return {
            "message": "Migration started",
            "pending_count": pending_count,
            "batch_size": min(batch_size, pending_count),
            "estimated_duration_minutes": min(batch_size, pending_count) * 2  # Rough estimate
        }
        
    except Exception as e:
        logger.error(f"Error starting migration: {e}")
        raise HTTPException(status_code=500, detail="Failed to start migration")

@s4_router.post("/migration/track/{track_id}")
async def migrate_single_track(
    track_id: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Migrate a single track to S4"""
    try:
        # Get track and verify ownership
        track = db.query(Track).filter(Track.id == track_id).first()
        if not track:
            raise HTTPException(status_code=404, detail="Track not found")
        
        # Check permissions
        if not current_user.is_creator or track.created_by != current_user.id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        if track.s4_available:
            return {
                "message": "Track already migrated to S4",
                "track_id": track_id,
                "object_key": track.s4_object_key
            }
        
        # Start migration
        background_tasks.add_task(migrate_track_to_s4, track, db)
        
        return {
            "message": "Track migration started",
            "track_id": track_id,
            "track_title": track.title
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error migrating track {track_id}: {e}")
        raise HTTPException(status_code=500, detail="Migration failed")

# ===================================================================
# Download Endpoints (S4-powered)
# ===================================================================

@s4_router.get("/download/track/{track_id}")
async def get_s4_track_download(
    track_id: str,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get optimized S4 download URL for a track"""
    try:
        # Get track and verify access
        track = db.query(Track).filter(Track.id == track_id).first()
        if not track:
            raise HTTPException(status_code=404, detail="Track not found")
        
        # Check album access permissions (your existing logic)
        album = db.query(Album).filter(Album.id == track.album_id).first()
        if not await check_access(current_user, album):
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Get S4 download method
        download_info = await storage.get_download_method(
            track_id=track_id,
            file_url=track.file_url,
            db=db
        )
        
        if download_info['method'] == 's4_direct':
            # Direct S4 download - redirect user
            logger.info(f"S4 direct download for track {track_id}")
            return {
                "download_type": "direct",
                "download_url": download_info['url'],
                "expires_in": download_info['expires_in'],
                "object_key": download_info['object_key'],
                "performance": "optimized",
                "message": "Direct download from MEGA S4"
            }
        else:
            # Error case
            logger.error(f"Track {track_id} not available in S4: {download_info.get('error')}")
            raise HTTPException(
                status_code=503, 
                detail=f"Track not available: {download_info.get('error', 'Unknown error')}"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"S4 download error for track {track_id}: {e}")
        raise HTTPException(status_code=500, detail="Download preparation failed")

@s4_router.get("/download/direct/{track_id}")
async def direct_s4_download(
    track_id: str,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Direct redirect to S4 presigned URL"""
    try:
        # Get track and verify access (same as above)
        track = db.query(Track).filter(Track.id == track_id).first()
        if not track:
            raise HTTPException(status_code=404, detail="Track not found")
        
        album = db.query(Album).filter(Album.id == track.album_id).first()
        if not await check_access(current_user, album):
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Get S4 presigned URL
        if track.s4_available and track.s4_object_key:
            presigned_url = mega_s4_client.generate_presigned_url(
                object_key=track.s4_object_key,
                expires_in=3600
            )
            
            # Log download for analytics
            logger.info(f"Direct S4 download: user={current_user.id}, track={track_id}")
            
            # Redirect directly to S4
            return RedirectResponse(url=presigned_url, status_code=302)
        else:
            raise HTTPException(status_code=503, detail="Track not available in S4")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Direct download error: {e}")
        raise HTTPException(status_code=500, detail="Download failed")

@s4_router.get("/download/album/{album_id}")
async def get_s4_album_download(
    album_id: str,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get S4-optimized album download information"""
    try:
        # Get album and tracks
        album = db.query(Album).filter(Album.id == album_id).first()
        if not album:
            raise HTTPException(status_code=404, detail="Album not found")
        
        if not await check_access(current_user, album):
            raise HTTPException(status_code=403, detail="Access denied")
        
        tracks = db.query(Track).filter(Track.album_id == album_id).all()
        if not tracks:
            raise HTTPException(status_code=404, detail="No tracks found in album")
        
        # Check S4 availability
        s4_tracks = [t for t in tracks if t.s4_available and t.s4_object_key]
        total_tracks = len(tracks)
        s4_available_count = len(s4_tracks)
        
        if s4_available_count == 0:
            raise HTTPException(
                status_code=503, 
                detail="Album not available in S4 storage"
            )
        
        # Calculate statistics
        s4_percentage = (s4_available_count / total_tracks) * 100
        
        return {
            "album_id": album_id,
            "album_title": album.title,
            "total_tracks": total_tracks,
            "s4_available_tracks": s4_available_count,
            "s4_percentage": round(s4_percentage, 1),
            "download_ready": s4_percentage >= 95,  # 95% threshold
            "tracks": [
                {
                    "track_id": str(t.id),
                    "title": t.title,
                    "s4_available": t.s4_available,
                    "object_key": t.s4_object_key if t.s4_available else None
                }
                for t in tracks
            ],
            "message": f"{s4_available_count}/{total_tracks} tracks available in S4"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Album download info error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get album info")

# ===================================================================
# Analytics & Monitoring Endpoints
# ===================================================================

@s4_router.get("/analytics/downloads")
async def get_download_analytics(
    days: int = Query(default=7, ge=1, le=30),
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get download analytics for creators"""
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Creator access required")
    
    try:
        # Get download statistics for the creator's tracks
        since_date = datetime.now(timezone.utc) - timedelta(days=days)
        
        # This would require adding download tracking to your system
        # For now, return placeholder data
        return {
            "period_days": days,
            "total_downloads": 0,  # Implement based on your download tracking
            "s4_downloads": 0,
            "traditional_downloads": 0,
            "performance_improvement": "N/A",
            "most_downloaded_tracks": [],
            "download_trends": []
        }
        
    except Exception as e:
        logger.error(f"Analytics error: {e}")
        raise HTTPException(status_code=500, detail="Failed to get analytics")

@s4_router.get("/objects/list")
async def list_s4_objects(
    prefix: str = Query(default="", description="Object key prefix filter"),
    limit: int = Query(default=100, ge=1, le=1000),
    current_user: User = Depends(login_required)
):
    """List objects in S4 bucket (admin/creator feature)"""
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Creator access required")
    
    try:
        objects = await mega_s4_client.list_objects(prefix=prefix, max_keys=limit)
        
        return {
            "objects": [
                {
                    "key": obj["key"],
                    "size_bytes": obj["size"],
                    "size_mb": round(obj["size"] / 1024 / 1024, 2),
                    "last_modified": obj["last_modified"].isoformat(),
                    "etag": obj["etag"]
                }
                for obj in objects
            ],
            "count": len(objects),
            "prefix_filter": prefix,
            "truncated": len(objects) == limit
        }
        
    except Exception as e:
        logger.error(f"Error listing S4 objects: {e}")
        raise HTTPException(status_code=500, detail="Failed to list objects")

# ===================================================================
# Background Tasks
# ===================================================================

async def bulk_migrate_tracks(creator_id: int, batch_size: int):
    """Background task to migrate multiple tracks"""
    try:
        from database import get_db
        db = next(get_db())
        
        # Get tracks that need migration
        tracks_query = text("""
            SELECT * FROM tracks 
            WHERE created_by = :creator_id 
            AND s4_available = false 
            AND file_url IS NOT NULL 
            ORDER BY play_count DESC NULLS LAST
            LIMIT :batch_size
        """)
        
        tracks = db.execute(tracks_query, {
            "creator_id": creator_id,
            "batch_size": batch_size
        }).fetchall()
        
        logger.info(f"Starting bulk migration of {len(tracks)} tracks for creator {creator_id}")
        
        success_count = 0
        for track_row in tracks:
            try:
                # Convert row to track object
                track = db.query(Track).filter(Track.id == track_row.id).first()
                if track:
                    success = await migrate_track_to_s4(track, db)
                    if success:
                        success_count += 1
                        logger.info(f"Successfully migrated track {track.id}: {track.title}")
                    else:
                        logger.error(f"Failed to migrate track {track.id}: {track.title}")
                        
                # Small delay between migrations
                await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Error migrating track {track_row.id}: {e}")
                continue
        
        logger.info(f"Bulk migration complete: {success_count}/{len(tracks)} successful")
        
    except Exception as e:
        logger.error(f"Error in bulk migration: {e}")
    finally:
        try:
            db.close()
        except:
            pass

async def migrate_track_to_s4(track: Track, db: Session) -> bool:
    """Migrate a single track to S4"""
    try:
        # Download from current storage first
        temp_path = await storage.download_audio_file(track.id, track.file_url)
        
        if not temp_path or not temp_path.exists():
            logger.error(f"Failed to download track {track.id} for migration")
            return False
        
        # Generate S4 object key
        filename = Path(track.file_url).name
        object_key = mega_s4_client.generate_object_key(filename, prefix="audio")
        
        # Upload to S4
        success = await mega_s4_client.upload_file(
            local_path=temp_path,
            object_key=object_key,
            content_type="audio/mpeg"
        )
        
        if success:
            # Update database
            track.s4_object_key = object_key
            track.s4_available = True
            track.s4_uploaded_at = datetime.now(timezone.utc)
            db.commit()
            
            logger.info(f"Successfully migrated track {track.id} to S4: {object_key}")
            return True
        else:
            logger.error(f"Failed to upload track {track.id} to S4")
            return False
            
    except Exception as e:
        logger.error(f"Error migrating track {track.id}: {e}")
        return False
    finally:
        # Cleanup temp file
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except Exception as cleanup_error:
                logger.error(f"Error cleaning up temp file: {cleanup_error}")

# ===================================================================
# Utility Functions (import these from your existing code)
# ===================================================================

async def check_access(user: User, album: Album) -> bool:
    """
    Import this from your existing app.py - this is your existing access control logic
    """
    # Your existing access control logic here
    try:
        logger.info(f"Checking access for user {user.email}")
        
        # Creators and team always have access
        if user.is_creator or user.is_team:
            return True
            
        # If album is not restricted, all users have access
        restrictions = album.tier_restrictions
        if not restrictions or not restrictions.get("is_restricted"):
            return True
            
        # Check if user has valid tier data
        has_tier_data = user.patreon_tier_data is not None and len(user.patreon_tier_data) > 0
        is_supporter = any([user.is_patreon, user.is_kofi])
        
        if not (is_supporter and has_tier_data):
            return False
            
        # Get user's actual tier amount
        user_amount = user.patreon_tier_data.get("amount_cents", 0)
        required_amount = restrictions.get("minimum_tier_amount", 0)
        
        has_access = user_amount >= required_amount
        return has_access
        
    except Exception as e:
        logger.error(f"Error checking access: {str(e)}")
        return False