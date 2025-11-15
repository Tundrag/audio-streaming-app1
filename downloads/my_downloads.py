# Unified endpoint for My Downloads - handles both SSR and SPA
from fastapi import APIRouter, Depends, Request, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import text, and_
from datetime import datetime, timezone, timedelta
from pathlib import Path
import logging

from database import get_db
from auth import login_required
from models import User, UserDownload, Track, Album
from fastapi.templating import Jinja2Templates

router = APIRouter()
logger = logging.getLogger(__name__)

# Setup templates
BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/my-downloads")
async def my_downloads_page(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """
    HTML page endpoint - Returns the My Downloads page template
    """
    try:
        return templates.TemplateResponse(
            "my_downloads.html",
            {
                "request": request,
                "user": current_user,
                "page_title": "My Downloads"
            }
        )
    except Exception as e:
        logger.error(f"Error loading my downloads page: {str(e)}", exc_info=True)
        return HTMLResponse(
            status_code=500,
            content="<h1>Error Loading Downloads</h1><p>Please try refreshing.</p>"
        )


@router.get("/api/my-downloads")
async def my_downloads_api(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """
    JSON API endpoint - Returns active downloads as JSON
    """
    try:
        return await _get_downloads_json(current_user, db)
    except Exception as e:
        logger.error(f"Error in my_downloads API: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": "Error retrieving downloads"}
        )


@router.get("/api/my-downloads/history")
async def my_downloads_history_unified(
    request: Request,
    limit: int = 1000,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """
    Unified endpoint for download history - always returns JSON
    (Called via fetch() from SPA, not directly by browser)
    """
    try:
        limit = max(1, min(limit, 2000))
        six_months_ago = datetime.now(timezone.utc) - timedelta(days=180)
        
        from models import DownloadHistory
        
        q = text("""
          SELECT dh.id,
                 dh.downloaded_at,
                 dh.status,
                 dh.download_type,
                 dh.entity_id,
                 dh.voice_id,
                 dh.error_message,
                 CASE WHEN dh.download_type = 'track'
                      THEN (SELECT t.title FROM tracks t WHERE t.id::text = dh.entity_id LIMIT 1)
                      ELSE (SELECT a.title FROM albums a WHERE a.id::text = dh.entity_id LIMIT 1)
                 END AS title
          FROM download_history dh
          WHERE dh.user_id = :uid
            AND dh.downloaded_at >= :since
          ORDER BY dh.downloaded_at DESC
          LIMIT :lim
        """)
        
        rows = db.execute(q, {
            "uid": current_user.id,
            "since": six_months_ago,
            "lim": limit
        }).fetchall()
        
        logger.info(f"Retrieved {len(rows)} history records for user {current_user.id}")
        
        return JSONResponse([
            {
                "id": r.id,
                "downloaded_at": r.downloaded_at.isoformat(),
                "status": r.status,
                "download_type": r.download_type,
                "entity_id": r.entity_id,
                "voice_id": r.voice_id,
                "error_message": r.error_message,
                "title": r.title or "(untitled)",
            }
            for r in rows
        ])
        
    except Exception as e:
        logger.error(f"Error retrieving history: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": "Error retrieving download history"}
        )


@router.get("/api/my-downloads/{download_id}/file")
async def download_file(
    download_id: int,
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Download a specific file - always returns file"""
    try:
        query = text("""
            SELECT 
                id, user_id, download_type, album_id, track_id, 
                download_path, original_filename, is_available,
                expires_at
            FROM 
                user_downloads
            WHERE 
                id = :download_id
                AND user_id = :user_id
                AND is_available = true
            LIMIT 1
        """)
        
        result = db.execute(query, {"download_id": download_id, "user_id": current_user.id})
        download_data = result.fetchone()
        
        if not download_data:
            raise HTTPException(status_code=404, detail="Download not found")
        
        # Check if expired
        if datetime.now(timezone.utc) >= download_data.expires_at:
            update_query = text("""
                UPDATE user_downloads
                SET is_available = false
                WHERE id = :download_id
            """)
            db.execute(update_query, {"download_id": download_id})
            db.commit()
            
            raise HTTPException(status_code=410, detail="Download expired")
        
        # Check file exists
        file_path = Path(download_data.download_path)
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File not found")
        
        is_album = download_data.download_type.lower() == 'album'
        media_type = 'application/zip' if is_album else 'audio/mpeg'
        
        return FileResponse(
            path=str(file_path),
            media_type=media_type,
            filename=download_data.original_filename,
            headers={'Content-Disposition': f'attachment; filename="{download_data.original_filename}"'}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving download: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error processing download")


@router.delete("/api/my-downloads/{download_id}")
async def delete_download(
    download_id: int,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Delete a download - returns JSON"""
    try:
        query = text("""
            SELECT 
                id, user_id, download_path, is_available
            FROM 
                user_downloads
            WHERE 
                id = :download_id
                AND user_id = :user_id
                AND is_available = true
            LIMIT 1
        """)
        
        result = db.execute(query, {"download_id": download_id, "user_id": current_user.id})
        download_data = result.fetchone()
        
        if not download_data:
            raise HTTPException(status_code=404, detail="Download not found")
        
        # Delete file
        file_path = Path(download_data.download_path)
        if file_path.exists():
            try:
                file_path.unlink()
                logger.info(f"Deleted file: {file_path}")
            except Exception as e:
                logger.error(f"Error deleting file: {e}")
        
        # Mark as unavailable
        update_query = text("""
            UPDATE user_downloads
            SET is_available = false
            WHERE id = :download_id
        """)
        db.execute(update_query, {"download_id": download_id})
        db.commit()
        
        return JSONResponse({"success": True, "message": "Download removed"})
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting download: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error processing request")


# ===== Helper function for JSON response =====

async def _get_downloads_json(current_user: User, db: Session) -> JSONResponse:
    """Get active downloads as JSON"""
    try:
        query = text("""
            SELECT 
                id, user_id, download_type, album_id, track_id, voice_id,
                download_path, original_filename, is_available,
                expires_at, downloaded_at, updated_at
            FROM 
                user_downloads
            WHERE 
                user_id = :user_id
                AND is_available = true
                AND expires_at > :now
            ORDER BY 
                downloaded_at DESC
        """)
        
        now = datetime.now(timezone.utc)
        result = db.execute(query, {"user_id": current_user.id, "now": now})
        
        raw_rows = result.fetchall()
        logger.info(f"Found {len(raw_rows)} downloads for user {current_user.id}")
        
        downloads = []
        for row in raw_rows:
            download = {
                "id": row.id,
                "type": row.download_type,
                "entity_id": str(row.album_id) if row.album_id else row.track_id,
                "filename": row.original_filename,
                "download_url": f"/api/my-downloads/{row.id}/file",
                "expires_at": row.expires_at.isoformat(),
                "downloaded_at": row.downloaded_at.isoformat(),
                "is_available": row.is_available,
                "voice_id": row.voice_id
            }
            
            # Calculate time remaining
            time_remaining = row.expires_at - now
            hours, remainder = divmod(time_remaining.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            download["time_remaining"] = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            
            # Get album data if album download
            if row.album_id:
                album = db.query(Album).filter(Album.id == row.album_id).first()
                if album:
                    download["album"] = {
                        "id": str(album.id),
                        "title": album.title,
                        "cover_path": album.cover_path or '/static/images/default-album.jpg'
                    }
            
            # Get track data if track download
            if row.track_id:
                track = db.query(Track).filter(Track.id == row.track_id).first()
                if track:
                    download["track"] = {
                        "id": track.id,
                        "title": track.title
                    }
                    
                    if track.album_id:
                        album = db.query(Album).filter(Album.id == track.album_id).first()
                        if album:
                            download["track"]["album"] = {
                                "id": str(album.id),
                                "title": album.title,
                                "cover_path": album.cover_path or '/static/images/default-track.jpg'
                            }
            
            downloads.append(download)
        
        logger.info(f"Returning {len(downloads)} downloads")
        return JSONResponse(downloads)
        
    except Exception as e:
        logger.error(f"Error retrieving downloads: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error retrieving downloads")


# ===== Creator endpoints (also unified) =====

@router.get("/api/creator/downloads/users/search")
async def creator_search_users(
    q: str,
    limit: int = 25,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Creator search users - returns JSON"""
    try:
        if not current_user.is_creator:
            raise HTTPException(status_code=403, detail="Creator access required")
        
        limit = max(1, min(limit, 100))
        
        uq = text("""
          SELECT id, email, COALESCE(username, email) AS display_name
          FROM users
          WHERE (LOWER(email) LIKE LOWER(:qs) OR LOWER(COALESCE(username, email)) LIKE LOWER(:qs))
            AND id != :current_user_id
          ORDER BY id DESC
          LIMIT :lim
        """)
        
        rows = db.execute(uq, {
            "qs": f"%{q}%",
            "lim": limit,
            "current_user_id": current_user.id
        }).fetchall()
        
        logger.info(f"Creator search returned {len(rows)} results")
        return JSONResponse([{"id": r.id, "email": r.email, "name": r.display_name} for r in rows])
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in creator search: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error searching users")


@router.get("/api/creator/users/{user_id}/downloads")
async def creator_user_downloads(
    user_id: int,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get user's active downloads - returns JSON (creator only)"""
    try:
        if not current_user.is_creator:
            raise HTTPException(status_code=403, detail="Creator access required")
        
        q = text("""
          SELECT id, download_type, album_id, track_id, voice_id, original_filename,
                 download_path, is_available, downloaded_at, expires_at
          FROM user_downloads
          WHERE user_id = :uid
            AND is_available = true
            AND expires_at > :now
          ORDER BY downloaded_at DESC
          LIMIT 200
        """)
        
        rows = db.execute(q, {"uid": user_id, "now": datetime.now(timezone.utc)}).fetchall()
        
        logger.info(f"Retrieved {len(rows)} downloads for user {user_id}")
        
        return JSONResponse([
            {
                "id": r.id,
                "type": str(r.download_type),
                "album_id": str(r.album_id) if getattr(r, "album_id", None) else None,
                "track_id": str(r.track_id) if getattr(r, "track_id", None) else None,
                "voice_id": r.voice_id,
                "original_filename": r.original_filename,
                "download_path": r.download_path,
                "is_available": r.is_available,
                "downloaded_at": r.downloaded_at.isoformat(),
                "expires_at": r.expires_at.isoformat(),
            }
            for r in rows
        ])
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving user downloads: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error retrieving user downloads")


@router.get("/api/creator/users/{user_id}/history")
async def creator_user_history(
    user_id: int,
    limit: int = 500,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get user's download history - returns JSON (creator only)"""
    try:
        if not current_user.is_creator:
            raise HTTPException(status_code=403, detail="Creator access required")
        
        limit = max(1, min(limit, 1000))
        
        from models import DownloadHistory
        
        q = text("""
          SELECT dh.id, dh.downloaded_at, dh.status, dh.download_type, dh.entity_id, dh.voice_id, dh.error_message,
                 CASE WHEN dh.download_type = 'track'
                      THEN (SELECT t.title FROM tracks t WHERE t.id::text = dh.entity_id LIMIT 1)
                      ELSE (SELECT a.title FROM albums a WHERE a.id::text = dh.entity_id LIMIT 1)
                 END AS title
          FROM download_history dh
          WHERE dh.user_id = :uid
          ORDER BY dh.downloaded_at DESC
          LIMIT :lim
        """)
        
        rows = db.execute(q, {"uid": user_id, "lim": limit}).fetchall()
        
        logger.info(f"Retrieved {len(rows)} history records for user {user_id}")
        
        return JSONResponse([
            {
                "id": r.id,
                "downloaded_at": r.downloaded_at.isoformat(),
                "status": r.status,
                "download_type": r.download_type,
                "entity_id": r.entity_id,
                "voice_id": r.voice_id,
                "error_message": r.error_message,
                "title": r.title or "(untitled)",
            }
            for r in rows
        ])
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving user history: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error retrieving user history")