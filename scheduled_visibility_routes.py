"""
Scheduled Visibility API Routes

API endpoints for scheduling automatic visibility changes for albums and tracks.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, validator
from sqlalchemy.orm import Session
from database import get_db
from models import Album, Track, User, UserRole
from auth import login_required

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["scheduled-visibility"])

# Constants
MIN_SCHEDULE_MINUTES = 5  # Minimum 5 minutes in the future
MAX_SCHEDULE_DAYS = 365  # Maximum 1 year in the future

# Valid visibility statuses
VALID_VISIBILITY_STATUSES = ["visible", "hidden_from_users", "hidden_from_all"]


# Pydantic Models
class ScheduleVisibilityRequest(BaseModel):
    """Request body for scheduling a visibility change"""
    scheduled_at: str  # ISO 8601 timestamp
    visibility_status: str

    @validator('visibility_status')
    def validate_visibility_status(cls, v):
        if v not in VALID_VISIBILITY_STATUSES:
            raise ValueError(
                f"visibility_status must be one of: {', '.join(VALID_VISIBILITY_STATUSES)}"
            )
        return v

    @validator('scheduled_at')
    def validate_scheduled_at(cls, v):
        try:
            scheduled_time = datetime.fromisoformat(v.replace('Z', '+00:00'))

            # Ensure it's timezone-aware
            if scheduled_time.tzinfo is None:
                raise ValueError("scheduled_at must include timezone information")

            # Convert to UTC
            scheduled_time = scheduled_time.astimezone(timezone.utc)

            # Check if it's in the future
            now = datetime.now(timezone.utc)
            time_diff = (scheduled_time - now).total_seconds()

            if time_diff < MIN_SCHEDULE_MINUTES * 60:
                raise ValueError(f"scheduled_at must be at least {MIN_SCHEDULE_MINUTES} minutes in the future")

            if time_diff > MAX_SCHEDULE_DAYS * 86400:
                raise ValueError(f"scheduled_at cannot be more than {MAX_SCHEDULE_DAYS} days in the future")

            return v
        except ValueError as e:
            raise ValueError(f"Invalid scheduled_at format: {str(e)}")


class ScheduleResponse(BaseModel):
    """Response for schedule operations"""
    success: bool
    message: str
    schedule: Optional[dict] = None


class ScheduleInfoResponse(BaseModel):
    """Response for getting schedule information"""
    has_schedule: bool
    schedule: Optional[dict] = None


# Helper Functions
def _calculate_countdown(scheduled_at: datetime) -> dict:
    """Calculate countdown string and seconds from scheduled time"""
    now = datetime.now(timezone.utc)
    time_diff = (scheduled_at - now).total_seconds()

    if time_diff <= 0:
        return {"countdown": "Expired", "countdown_seconds": 0}

    days = int(time_diff // 86400)
    hours = int((time_diff % 86400) // 3600)
    minutes = int((time_diff % 3600) // 60)
    seconds = int(time_diff % 60)

    # Format countdown string
    if days > 0:
        countdown = f"{days}d {hours}h"
    elif hours > 0:
        countdown = f"{hours}h {minutes}m"
    elif minutes > 0:
        countdown = f"{minutes}m {seconds}s"
    else:
        countdown = f"{seconds}s"

    return {"countdown": countdown, "countdown_seconds": int(time_diff)}


def _check_creator_permission(current_user: User, resource_creator_id: int) -> bool:
    """Check if user is the creator of the resource or a TEAM member"""
    # TEAM members and creators can manage schedules
    if current_user.role in [UserRole.CREATOR, UserRole.TEAM]:
        return True
    # Non-team users can only manage their own resources
    return current_user.id == resource_creator_id


def _can_schedule_hidden_from_all(current_user: User, resource_creator_id: int) -> bool:
    """Check if user can schedule 'hidden_from_all' status"""
    # Only CREATOR can schedule hidden_from_all (TEAM cannot see this visibility level)
    return current_user.id == resource_creator_id and current_user.role == UserRole.CREATOR


# Album Endpoints
@router.post("/albums/{album_id}/schedule-visibility", response_model=ScheduleResponse)
async def schedule_album_visibility(
    album_id: str,
    request: ScheduleVisibilityRequest,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Schedule a visibility change for an album"""

    # Get album
    album = db.query(Album).filter(Album.id == album_id).first()
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    # SECURITY: Team members cannot access hidden_from_all content
    if current_user.role == UserRole.TEAM and album.visibility_status == "hidden_from_all":
        raise HTTPException(status_code=404, detail="Album not found")

    # Check permission (creator and team can schedule)
    if not _check_creator_permission(current_user, album.created_by_id):
        raise HTTPException(
            status_code=403,
            detail="Only the album creator or team members can schedule visibility changes"
        )

    # Check if user can schedule 'hidden_from_all' status
    if request.visibility_status == "hidden_from_all":
        if not _can_schedule_hidden_from_all(current_user, album.created_by_id):
            raise HTTPException(
                status_code=403,
                detail="Only the creator can schedule 'hidden_from_all' status"
            )

    # Check if album already has a schedule
    if album.scheduled_visibility_change_at is not None:
        raise HTTPException(
            status_code=400,
            detail="Album already has a scheduled visibility change. Cancel it first."
        )

    # Parse scheduled time
    try:
        scheduled_time = datetime.fromisoformat(request.scheduled_at.replace('Z', '+00:00'))
        scheduled_time = scheduled_time.astimezone(timezone.utc)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid scheduled_at format: {str(e)}")

    # Update album with schedule
    album.scheduled_visibility_change_at = scheduled_time
    album.scheduled_visibility_status = request.visibility_status

    # Calculate countdown
    countdown_info = _calculate_countdown(scheduled_time)

    # Log action
    logger.info(
        f"📅 Scheduled visibility change for album '{album.title}' (id={album.id}): "
        f"'{album.visibility_status}' → '{request.visibility_status}' at {scheduled_time}"
    )

    # Create audit log in the same transaction
    try:
        from models import AuditLog, AuditLogType
        now = datetime.now(timezone.utc)
        audit_log = AuditLog(
            user_id=current_user.id,
            action_type=AuditLogType.UPDATE,
            table_name="albums",
            record_id=str(album.id),
            old_values={"scheduled_visibility_change_at": None},
            new_values={
                "scheduled_visibility_change_at": scheduled_time.isoformat(),
                "scheduled_visibility_status": request.visibility_status
            },
            description=f"Scheduled visibility change to '{request.visibility_status}' at {scheduled_time}",
            created_at=now,
            updated_at=now
        )
        db.add(audit_log)
    except Exception as e:
        logger.warning(f"Failed to create audit log: {e}")
        # Continue without audit log

    # Commit both album update and audit log together
    try:
        db.commit()
    except Exception as e:
        import traceback
        logger.error(f"Failed to commit schedule: {e}")
        logger.error(f"Full traceback:\n{traceback.format_exc()}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to save schedule")

    return ScheduleResponse(
        success=True,
        message=f"Visibility change scheduled for {scheduled_time.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        schedule={
            "scheduled_at": scheduled_time.isoformat(),
            "visibility_status": request.visibility_status,
            "current_status": album.visibility_status,
            **countdown_info
        }
    )


@router.get("/albums/{album_id}/schedule-visibility", response_model=ScheduleInfoResponse)
async def get_album_schedule(
    album_id: str,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get the current scheduled visibility change for an album"""

    # Get album
    album = db.query(Album).filter(Album.id == album_id).first()
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    # SECURITY: Only CREATOR and TEAM can view schedule timers
    # Regular users (PATREON, KOFI, GUEST) should NEVER see timers
    if current_user.role not in [UserRole.CREATOR, UserRole.TEAM]:
        raise HTTPException(status_code=403, detail="Access denied")

    # SECURITY: Team members cannot access hidden_from_all content or its schedules
    if current_user.role == UserRole.TEAM and album.visibility_status == "hidden_from_all":
        raise HTTPException(status_code=404, detail="Album not found")

    # Check if album has a schedule
    if album.scheduled_visibility_change_at is None:
        return ScheduleInfoResponse(has_schedule=False, schedule=None)

    # Calculate countdown
    countdown_info = _calculate_countdown(album.scheduled_visibility_change_at)

    return ScheduleInfoResponse(
        has_schedule=True,
        schedule={
            "scheduled_at": album.scheduled_visibility_change_at.isoformat(),
            "visibility_status": album.scheduled_visibility_status,
            "current_status": album.visibility_status,
            **countdown_info
        }
    )


@router.delete("/albums/{album_id}/schedule-visibility", response_model=ScheduleResponse)
async def cancel_album_schedule(
    album_id: str,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Cancel a scheduled visibility change for an album"""

    # Get album
    album = db.query(Album).filter(Album.id == album_id).first()
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")

    # SECURITY: Team members cannot access hidden_from_all content
    if current_user.role == UserRole.TEAM and album.visibility_status == "hidden_from_all":
        raise HTTPException(status_code=404, detail="Album not found")

    # Check permission (creator and team can cancel)
    if not _check_creator_permission(current_user, album.created_by_id):
        raise HTTPException(
            status_code=403,
            detail="Only the album creator or team members can cancel scheduled visibility changes"
        )

    # Check if album has a schedule
    if album.scheduled_visibility_change_at is None:
        raise HTTPException(status_code=400, detail="Album has no scheduled visibility change")

    # Store schedule info for logging
    scheduled_at = album.scheduled_visibility_change_at
    target_status = album.scheduled_visibility_status

    # Cancel schedule
    album.scheduled_visibility_change_at = None
    album.scheduled_visibility_status = None

    # Log action
    logger.info(
        f"❌ Cancelled scheduled visibility change for album '{album.title}' (id={album.id}): "
        f"was scheduled for {scheduled_at} to '{target_status}'"
    )

    # Create audit log in the same transaction
    try:
        from models import AuditLog, AuditLogType
        now = datetime.now(timezone.utc)
        audit_log = AuditLog(
            user_id=current_user.id,
            action_type=AuditLogType.UPDATE,
            table_name="albums",
            record_id=str(album.id),
            old_values={
                "scheduled_visibility_change_at": scheduled_at.isoformat(),
                "scheduled_visibility_status": target_status
            },
            new_values={
                "scheduled_visibility_change_at": None,
                "scheduled_visibility_status": None
            },
            description=f"Cancelled scheduled visibility change (was scheduled for {scheduled_at})",
            created_at=now,
            updated_at=now
        )
        db.add(audit_log)
    except Exception as e:
        logger.warning(f"Failed to create audit log: {e}")

    # Commit both cancel and audit log together
    try:
        db.commit()
    except Exception as e:
        logger.error(f"Failed to commit cancel: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to cancel schedule")

    return ScheduleResponse(
        success=True,
        message="Scheduled visibility change cancelled",
        schedule=None
    )


# Track Endpoints
@router.post("/tracks/{track_id}/schedule-visibility", response_model=ScheduleResponse)
async def schedule_track_visibility(
    track_id: str,
    request: ScheduleVisibilityRequest,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Schedule a visibility change for a track"""

    # Get track
    track = db.query(Track).filter(Track.id == track_id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    # SECURITY: Team members cannot access hidden_from_all content
    if current_user.role == UserRole.TEAM and track.visibility_status == "hidden_from_all":
        raise HTTPException(status_code=404, detail="Track not found")

    # Check permission (creator and team can schedule)
    if not _check_creator_permission(current_user, track.created_by_id):
        raise HTTPException(
            status_code=403,
            detail="Only the track creator or team members can schedule visibility changes"
        )

    # Check if user can schedule 'hidden_from_all' status
    if request.visibility_status == "hidden_from_all":
        if not _can_schedule_hidden_from_all(current_user, track.created_by_id):
            raise HTTPException(
                status_code=403,
                detail="Only the creator can schedule 'hidden_from_all' status"
            )

    # Check if track already has a schedule
    if track.scheduled_visibility_change_at is not None:
        raise HTTPException(
            status_code=400,
            detail="Track already has a scheduled visibility change. Cancel it first."
        )

    # Parse scheduled time
    try:
        scheduled_time = datetime.fromisoformat(request.scheduled_at.replace('Z', '+00:00'))
        scheduled_time = scheduled_time.astimezone(timezone.utc)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid scheduled_at format: {str(e)}")

    # Update track with schedule
    track.scheduled_visibility_change_at = scheduled_time
    track.scheduled_visibility_status = request.visibility_status

    # Calculate countdown
    countdown_info = _calculate_countdown(scheduled_time)

    # Log action
    logger.info(
        f"📅 Scheduled visibility change for track '{track.title}' (id={track.id}): "
        f"'{track.visibility_status}' → '{request.visibility_status}' at {scheduled_time}"
    )

    # Create audit log in the same transaction
    try:
        from models import AuditLog, AuditLogType
        now = datetime.now(timezone.utc)
        audit_log = AuditLog(
            user_id=current_user.id,
            action_type=AuditLogType.UPDATE,
            table_name="tracks",
            record_id=track.id,
            old_values={"scheduled_visibility_change_at": None},
            new_values={
                "scheduled_visibility_change_at": scheduled_time.isoformat(),
                "scheduled_visibility_status": request.visibility_status
            },
            description=f"Scheduled visibility change to '{request.visibility_status}' at {scheduled_time}",
            created_at=now,
            updated_at=now
        )
        db.add(audit_log)
    except Exception as e:
        logger.warning(f"Failed to create audit log: {e}")

    # Commit both track update and audit log together
    try:
        db.commit()
    except Exception as e:
        logger.error(f"Failed to commit schedule: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to save schedule")

    return ScheduleResponse(
        success=True,
        message=f"Visibility change scheduled for {scheduled_time.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        schedule={
            "scheduled_at": scheduled_time.isoformat(),
            "visibility_status": request.visibility_status,
            "current_status": track.visibility_status,
            **countdown_info
        }
    )


@router.get("/tracks/{track_id}/schedule-visibility", response_model=ScheduleInfoResponse)
async def get_track_schedule(
    track_id: str,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get the current scheduled visibility change for a track"""

    # Get track
    track = db.query(Track).filter(Track.id == track_id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    # SECURITY: Only CREATOR and TEAM can view schedule timers
    # Regular users (PATREON, KOFI, GUEST) should NEVER see timers
    if current_user.role not in [UserRole.CREATOR, UserRole.TEAM]:
        raise HTTPException(status_code=403, detail="Access denied")

    # SECURITY: Team members cannot access hidden_from_all content or its schedules
    if current_user.role == UserRole.TEAM and track.visibility_status == "hidden_from_all":
        raise HTTPException(status_code=404, detail="Track not found")

    # Check if track has a schedule
    if track.scheduled_visibility_change_at is None:
        return ScheduleInfoResponse(has_schedule=False, schedule=None)

    # Calculate countdown
    countdown_info = _calculate_countdown(track.scheduled_visibility_change_at)

    return ScheduleInfoResponse(
        has_schedule=True,
        schedule={
            "scheduled_at": track.scheduled_visibility_change_at.isoformat(),
            "visibility_status": track.scheduled_visibility_status,
            "current_status": track.visibility_status,
            **countdown_info
        }
    )


@router.delete("/tracks/{track_id}/schedule-visibility", response_model=ScheduleResponse)
async def cancel_track_schedule(
    track_id: str,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Cancel a scheduled visibility change for a track"""

    # Get track
    track = db.query(Track).filter(Track.id == track_id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    # SECURITY: Team members cannot access hidden_from_all content
    if current_user.role == UserRole.TEAM and track.visibility_status == "hidden_from_all":
        raise HTTPException(status_code=404, detail="Track not found")

    # Check permission (creator and team can cancel)
    if not _check_creator_permission(current_user, track.created_by_id):
        raise HTTPException(
            status_code=403,
            detail="Only the track creator or team members can cancel scheduled visibility changes"
        )

    # Check if track has a schedule
    if track.scheduled_visibility_change_at is None:
        raise HTTPException(status_code=400, detail="Track has no scheduled visibility change")

    # Store schedule info for logging
    scheduled_at = track.scheduled_visibility_change_at
    target_status = track.scheduled_visibility_status

    # Cancel schedule
    track.scheduled_visibility_change_at = None
    track.scheduled_visibility_status = None

    # Log action
    logger.info(
        f"❌ Cancelled scheduled visibility change for track '{track.title}' (id={track.id}): "
        f"was scheduled for {scheduled_at} to '{target_status}'"
    )

    # Create audit log in the same transaction
    try:
        from models import AuditLog, AuditLogType
        now = datetime.now(timezone.utc)
        audit_log = AuditLog(
            user_id=current_user.id,
            action_type=AuditLogType.UPDATE,
            table_name="tracks",
            record_id=track.id,
            old_values={
                "scheduled_visibility_change_at": scheduled_at.isoformat(),
                "scheduled_visibility_status": target_status
            },
            new_values={
                "scheduled_visibility_change_at": None,
                "scheduled_visibility_status": None
            },
            description=f"Cancelled scheduled visibility change (was scheduled for {scheduled_at})",
            created_at=now,
            updated_at=now
        )
        db.add(audit_log)
    except Exception as e:
        logger.warning(f"Failed to create audit log: {e}")

    # Commit both cancel and audit log together
    try:
        db.commit()
    except Exception as e:
        logger.error(f"Failed to commit cancel: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to cancel schedule")

    return ScheduleResponse(
        success=True,
        message="Scheduled visibility change cancelled",
        schedule=None
    )
