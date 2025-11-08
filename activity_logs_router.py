# activity_logs_router.py
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, and_, text
from datetime import datetime, timezone, timedelta
from typing import Optional
import logging

from models import User, AuditLog, AuditLogType
from auth import login_required
from database import get_db, AsyncSessionLocal, SessionLocal
from redis_state.config import redis_client
from notifications import simple_notification_manager

router = APIRouter(tags=["Activity Logs"])
templates = Jinja2Templates(directory="templates")
logger = logging.getLogger(__name__)

# ===== HELPER FUNCTIONS =====
async def get_unread_activity_logs_count(user_id: int, db: Session) -> int:
    """Get count of unread activity logs from last 24 hours for admin/team users"""
    try:

        # Get last viewed timestamp from Redis
        last_viewed_key = f"activity_logs:last_viewed:{user_id}"
        last_viewed_str = redis_client.get(last_viewed_key)

        if last_viewed_str:
            # Redis client already decodes responses (decode_responses=True in config)
            last_viewed = datetime.fromisoformat(last_viewed_str)
        else:
            # If never viewed, use 24 hours ago
            last_viewed = datetime.now(timezone.utc) - timedelta(hours=24)

        # Get user's creator_id to filter logs
        user = db.query(User).filter(User.id == user_id).first()
        if not user or not (user.is_creator or user.is_team):
            return 0

        creator_id = user.id if user.is_creator else user.created_by

        # Build team user IDs: creator + team members
        from models import UserRole
        team_user_ids = [creator_id]
        team_members = db.query(User.id).filter(
            and_(
                User.created_by == creator_id,
                User.role == UserRole.TEAM
            )
        ).all()
        team_user_ids.extend([m.id for m in team_members])

        # Count logs created after last viewed and within 24 hours
        twenty_four_hours_ago = datetime.now(timezone.utc) - timedelta(hours=24)
        cutoff_time = max(last_viewed, twenty_four_hours_ago)

        count = db.query(func.count(AuditLog.id)).filter(
            and_(
                AuditLog.user_id.in_(team_user_ids),
                AuditLog.created_at > cutoff_time
            )
        ).scalar()

        logger.info(f"Unread activity logs for user {user_id}: {count} (since {cutoff_time})")
        return count or 0

    except Exception as e:
        logger.error(f"Error getting unread activity logs count: {e}")
        return 0

async def add_activity_logs_count(request: Request, current_user: User, db: Session) -> Request:
    """Add unread activity logs count to request.state for template rendering"""
    if current_user.is_creator or current_user.is_team:
        count = await get_unread_activity_logs_count(current_user.id, db)
        request.state.unread_activity_logs = count
        logger.info(f"Added {count} unread activity logs to request.state for {current_user.email}")
    else:
        request.state.unread_activity_logs = 0

    return request

async def mark_activity_logs_as_read(user_id: int):
    """Mark activity logs as read by updating last viewed timestamp in Redis"""
    try:
        last_viewed_key = f"activity_logs:last_viewed:{user_id}"
        current_time = datetime.now(timezone.utc).isoformat()

        # Set with 7 day expiry (cleanup old keys)
        redis_client.set(last_viewed_key, current_time, ex=7 * 24 * 60 * 60)
        logger.info(f"Marked activity logs as read for user {user_id} at {current_time}")

    except Exception as e:
        logger.error(f"Error marking activity logs as read: {e}")

async def notify_admins_new_activity_log(db: Session, creator_id: int):
    """Send WebSocket notification to all admin/team users about new activity log"""
    try:
        # Get creator
        creator = db.query(User).filter(User.id == creator_id).first()
        if not creator:
            return

        # Get all team members
        from models import UserRole
        team_members = db.query(User).filter(
            and_(
                User.created_by == creator_id,
                User.role == UserRole.TEAM
            )
        ).all()

        # Send to creator
        count = await get_unread_activity_logs_count(creator.id, db)
        logger.info(f"📊 Sending activity log count update to creator {creator.id}: count={count}")
        sent = await simple_notification_manager.send_to_user(
            creator.id,
            {
                "type": "activity_log_count_update",
                "count": count
            }
        )
        logger.info(f"✅ Creator notification sent: {sent}")

        # Send to each team member
        for member in team_members:
            count = await get_unread_activity_logs_count(member.id, db)
            logger.info(f"📊 Sending activity log count update to team member {member.id}: count={count}")
            sent = await simple_notification_manager.send_to_user(
                member.id,
                {
                    "type": "activity_log_count_update",
                    "count": count
                }
            )
            logger.info(f"✅ Team member notification sent: {sent}")

        logger.info(f"🎯 Sent activity log notifications to creator {creator_id} and {len(team_members)} team members")

    except Exception as e:
        logger.error(f"Error notifying admins about new activity log: {e}")

# ===== PAGE ROUTE =====
@router.get("/activity-logs", response_class=HTMLResponse)
async def view_activity_logs(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """View activity logs page - SPA shell"""
    if not (current_user.is_creator or current_user.is_team):
        raise HTTPException(status_code=403, detail="Not authorized")

    # Add unread count to request.state for badge display
    request = await add_activity_logs_count(request, current_user, db)

    # Mark logs as read when user visits this page
    await mark_activity_logs_as_read(current_user.id)

    # Return base template - SPA will populate content via JavaScript
    return templates.TemplateResponse(
        "base.html",
        {
            "request": request,
            "user": current_user,
            "page_title": "Activity Logs"
        }
    )

# ===== API ROUTES =====
@router.get("/api/activity-logs/team-members")
async def get_team_members(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get team members for filter dropdown - only creator and actual team members"""
    if not (current_user.is_creator or current_user.is_team):
        raise HTTPException(status_code=403, detail="Not authorized")
    
    from models import UserRole
    
    creator_id = current_user.id if current_user.is_creator else current_user.created_by
    
    # Get creator
    creator = db.query(User).filter(User.id == creator_id).first()
    
    # Get actual team members (role == TEAM)
    team_members = db.query(User).filter(
        and_(
            User.created_by == creator_id,
            User.role == UserRole.TEAM
        )
    ).all()
    
    # Combine creator + team members
    all_members = []
    if creator:
        all_members.append({
            "id": creator.id,
            "username": creator.username,
            "role": "Creator"
        })
    
    for member in team_members:
        all_members.append({
            "id": member.id,
            "username": member.username,
            "role": "Team"
        })
    
    return {"team_members": all_members}

@router.get("/api/activity-logs")
async def get_activity_logs(
    year: Optional[int] = None,
    month: Optional[int] = None,
    action_type: Optional[str] = None,
    user_id: Optional[int] = None,
    page: int = 1,
    per_page: int = 50,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get paginated activity logs with filters - only creator and team members"""
    if not (current_user.is_creator or current_user.is_team):
        raise HTTPException(status_code=403, detail="Not authorized")
    
    from models import UserRole
    
    creator_id = current_user.id if current_user.is_creator else current_user.created_by
    
    # Build team user IDs: creator + actual team members only
    team_user_ids = [creator_id]
    
    team_members = db.query(User.id).filter(
        and_(
            User.created_by == creator_id,
            User.role == UserRole.TEAM
        )
    ).all()
    
    team_user_ids.extend([m.id for m in team_members])
    
    logger.info(f"Querying activity logs for team user IDs: {team_user_ids}")
    
    query = db.query(AuditLog).filter(AuditLog.user_id.in_(team_user_ids))
    
    if year:
        start_date = datetime(year, 1, 1, tzinfo=timezone.utc)
        end_date = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        query = query.filter(AuditLog.created_at >= start_date, AuditLog.created_at < end_date)
    
    if month and year:
        start_date = datetime(year, month, 1, tzinfo=timezone.utc)
        if month == 12:
            end_date = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end_date = datetime(year, month + 1, 1, tzinfo=timezone.utc)
        query = query.filter(AuditLog.created_at >= start_date, AuditLog.created_at < end_date)
    
    if action_type:
        try:
            action_enum = AuditLogType[action_type.upper()]
            query = query.filter(AuditLog.action_type == action_enum)
        except KeyError:
            pass
    
    if user_id:
        query = query.filter(AuditLog.user_id == user_id)
    
    query = query.order_by(AuditLog.created_at.desc())
    
    total = query.count()
    offset = (page - 1) * per_page
    logs = query.offset(offset).limit(per_page).all()
    
    formatted_logs = []
    for log in logs:
        user = db.query(User).filter(User.id == log.user_id).first()
        user_role = "Creator" if user and user.id == creator_id else "Team"
        
        formatted_logs.append({
            "id": log.id,
            "user": {
                "id": user.id if user else None,
                "username": user.username if user else "Unknown",
                "role": user_role
            },
            "action_type": log.action_type.value,
            "table_name": log.table_name,
            "record_id": log.record_id,
            "description": log.description,
            "old_values": log.old_values,
            "new_values": log.new_values,
            "ip_address": log.ip_address,
            "created_at": log.created_at.isoformat() if log.created_at else None,
            "changes_summary": log.changes_summary
        })
    
    return {
        "logs": formatted_logs,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": (total + per_page - 1) // per_page
        }
    }

@router.get("/api/activity-logs/summary")
async def get_activity_summary(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get summary statistics - only creator and team members"""
    if not (current_user.is_creator or current_user.is_team):
        raise HTTPException(status_code=403, detail="Not authorized")
    
    from models import UserRole
    
    creator_id = current_user.id if current_user.is_creator else current_user.created_by
    
    # Build team user IDs: creator + actual team members only
    team_user_ids = [creator_id]
    
    team_members = db.query(User.id).filter(
        and_(
            User.created_by == creator_id,
            User.role == UserRole.TEAM
        )
    ).all()
    
    team_user_ids.extend([m.id for m in team_members])
    
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    recent_count = db.query(func.count(AuditLog.id)).filter(
        AuditLog.user_id.in_(team_user_ids),
        AuditLog.created_at >= thirty_days_ago
    ).scalar()
    
    most_active = db.query(
        AuditLog.user_id,
        func.count(AuditLog.id).label('count')
    ).filter(
        AuditLog.user_id.in_(team_user_ids)
    ).group_by(AuditLog.user_id).order_by(text('count DESC')).first()
    
    most_active_user = None
    if most_active:
        user = db.query(User).filter(User.id == most_active.user_id).first()
        if user:
            most_active_user = {"username": user.username, "count": most_active.count}
    
    return {
        "recent_activity_count": recent_count,
        "most_active_user": most_active_user,
        "team_size": len(team_user_ids)
    }

@router.post("/api/activity-logs/mark-read")
async def mark_logs_as_read(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Mark activity logs as read for current user - updates timestamp in Redis"""
    if not (current_user.is_creator or current_user.is_team):
        raise HTTPException(status_code=403, detail="Not authorized")

    await mark_activity_logs_as_read(current_user.id)

    # Return updated count (should be 0 now)
    count = await get_unread_activity_logs_count(current_user.id, db)

    return {
        "success": True,
        "unread_count": count
    }

# ===== ISOLATED LOGGING (PREVENTS SESSION POISONING) =====
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from database import async_engine

# Standalone async session factory just for audit logging
AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False)


def _truncate(text: Optional[str], limit: int = 2000) -> Optional[str]:
    """Truncate long descriptions to prevent bloat"""
    if text is None:
        return None
    return text if len(text) <= limit else (text[:limit - 1] + "…")


async def log_activity_isolated(
    *,
    user_id: int,
    action_type: AuditLogType,
    table_name: str,
    record_id: str,
    description: Optional[str] = None,
    old_values: dict = None,
    new_values: dict = None,
    ip_address: str = None,
    user_agent: str = None
) -> None:
    """
    Write an activity log in its own transaction/session so failures
    NEVER poison the caller's session.

    Uses raw SQL INSERT to bypass SQLAlchemy ORM mapper issues.
    """
    try:
        from sqlalchemy import text
        import json as json_lib

        async with AsyncSessionLocal() as s:
            async with s.begin():
                # Use raw SQL to bypass ORM mapper issues
                # Cast the action_type to the enum type explicitly
                await s.execute(
                    text("""
                        INSERT INTO audit_logs
                        (user_id, action_type, table_name, record_id, description,
                         old_values, new_values, ip_address, user_agent, created_at)
                        VALUES
                        (:user_id, CAST(:action_type AS auditlogtype), :table_name, :record_id, :description,
                         :old_values, :new_values, :ip_address, :user_agent, :created_at)
                    """),
                    {
                        "user_id": user_id,
                        "action_type": action_type.value,
                        "table_name": table_name,
                        "record_id": record_id,
                        "description": _truncate(description, 2000),
                        "old_values": json_lib.dumps(old_values) if old_values else None,
                        "new_values": json_lib.dumps(new_values) if new_values else None,
                        "ip_address": ip_address,
                        "user_agent": user_agent,
                        "created_at": datetime.now(timezone.utc)
                    }
                )
        logger.info(f"Activity logged: {action_type.value} on {table_name} by user {user_id}")

        # Send WebSocket notification to admins about new activity log
        try:
            # Get user to determine creator_id
            async with AsyncSessionLocal() as notify_session:
                user = await notify_session.execute(
                    text("SELECT id, created_by FROM users WHERE id = :user_id"),
                    {"user_id": user_id}
                )
                user_row = user.fetchone()
                if user_row:
                    creator_id = user_row[1] if user_row[1] else user_row[0]
                    # Use sync db session for notification function
                    from database import SessionLocal
                    with SessionLocal() as sync_db:
                        await notify_admins_new_activity_log(sync_db, creator_id)
        except Exception as notify_error:
            logger.error(f"Failed to send activity log notification: {notify_error}")

    except Exception as e:
        # Non-fatal by design - logging should never break the app
        logger.warning(f"Non-fatal: failed to write activity log: {e}")


async def log_activity(
    *,
    db: Optional[AsyncSession] = None,
    user_id: int,
    action_type: AuditLogType,
    table_name: str,
    record_id: str,
    description: Optional[str] = None,
    old_values: dict = None,
    new_values: dict = None,
    ip_address: str = None,
    user_agent: str = None
) -> None:
    """
    Backward-compatible shim:
    - If a db session is passed, try to use it safely (rollback on error).
    - Otherwise, fall back to the isolated writer.
    """
    if db is None:
        await log_activity_isolated(
            user_id=user_id,
            action_type=action_type,
            table_name=table_name,
            record_id=record_id,
            description=description,
            old_values=old_values,
            new_values=new_values,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return

    try:
        db.add(
            AuditLog(
                user_id=user_id,
                action_type=action_type,
                table_name=table_name,
                record_id=record_id,
                description=_truncate(description, 2000),
                old_values=old_values,
                new_values=new_values,
                ip_address=ip_address,
                user_agent=user_agent,
                created_at=datetime.now(timezone.utc)
            )
        )
        await db.flush()
        logger.info(f"Activity logged: {action_type.value} on {table_name} by user {user_id}")
    except Exception as e:
        # Make absolutely sure we don't doom the caller's session
        try:
            await db.rollback()
        except Exception:
            pass
        logger.warning(f"Non-fatal: activity log failed; rolled back local change: {e}")