# forum_settings_routes.py - Forum Settings API
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone

from auth import login_required
from database import get_db
from models import User
from forum_models import ForumThread, ForumMessage, ForumMention, ForumThreadFollower, ForumNotification
from models import User, ForumUserSettings
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse


forum_settings_router = APIRouter(prefix="/api/forum/settings", tags=["forum-settings"])
# Pydantic models
class ForumSettingsResponse(BaseModel):
    display_alias: Optional[str] = None
    use_alias: bool = False
    enable_quick_reply_notifications: bool = True
    quick_reply_for_mentions: bool = True
    quick_reply_for_replies: bool = True
    quick_reply_auto_dismiss_seconds: int = 10
    enable_notification_sound: bool = False
    notification_position: str = "top-right"
    show_online_status: bool = True
    allow_direct_mentions: bool = True

class UpdateForumSettingsRequest(BaseModel):
    display_alias: Optional[str] = None
    use_alias: Optional[bool] = None
    enable_quick_reply_notifications: Optional[bool] = None
    quick_reply_for_mentions: Optional[bool] = None
    quick_reply_for_replies: Optional[bool] = None
    quick_reply_auto_dismiss_seconds: Optional[int] = None
    enable_notification_sound: Optional[bool] = None
    notification_position: Optional[str] = None
    show_online_status: Optional[bool] = None
    allow_direct_mentions: Optional[bool] = None

def get_or_create_forum_settings(user_id: int, db: Session) -> ForumUserSettings:
    """Get existing forum settings or create default ones"""
    settings = db.query(ForumUserSettings).filter(ForumUserSettings.user_id == user_id).first()
    
    if not settings:
        settings = ForumUserSettings(user_id=user_id)
        db.add(settings)
        db.flush()
    
    return settings


@forum_settings_router.get("", response_model=ForumSettingsResponse)
async def get_forum_settings(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get user's forum settings"""
    
    settings = get_or_create_forum_settings(current_user.id, db)
    
    return ForumSettingsResponse(
        display_alias=settings.display_alias,
        use_alias=settings.use_alias,
        enable_quick_reply_notifications=settings.enable_quick_reply_notifications,
        quick_reply_for_mentions=settings.quick_reply_for_mentions,
        quick_reply_for_replies=settings.quick_reply_for_replies,
        quick_reply_auto_dismiss_seconds=settings.quick_reply_auto_dismiss_seconds,
        enable_notification_sound=settings.enable_notification_sound,
        notification_position=settings.notification_position,
        show_online_status=settings.show_online_status,
        allow_direct_mentions=settings.allow_direct_mentions
    )

@forum_settings_router.patch("", response_model=ForumSettingsResponse)
async def update_forum_settings(
    request: UpdateForumSettingsRequest,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Update user's forum settings"""
    
    settings = get_or_create_forum_settings(current_user.id, db)
    
    # Update fields if provided
    if request.display_alias is not None:
        # Validate alias length and content
        if request.display_alias and (len(request.display_alias) < 2 or len(request.display_alias) > 50):
            raise HTTPException(status_code=400, detail="Display alias must be between 2 and 50 characters")
        settings.display_alias = request.display_alias.strip() if request.display_alias else None
    
    if request.use_alias is not None:
        settings.use_alias = request.use_alias
    
    if request.enable_quick_reply_notifications is not None:
        settings.enable_quick_reply_notifications = request.enable_quick_reply_notifications
    
    if request.quick_reply_for_mentions is not None:
        settings.quick_reply_for_mentions = request.quick_reply_for_mentions
    
    if request.quick_reply_for_replies is not None:
        settings.quick_reply_for_replies = request.quick_reply_for_replies
    
    if request.quick_reply_auto_dismiss_seconds is not None:
        # Validate auto-dismiss time (5-60 seconds)
        if request.quick_reply_auto_dismiss_seconds < 5 or request.quick_reply_auto_dismiss_seconds > 60:
            raise HTTPException(status_code=400, detail="Auto-dismiss time must be between 5 and 60 seconds")
        settings.quick_reply_auto_dismiss_seconds = request.quick_reply_auto_dismiss_seconds
    
    if request.enable_notification_sound is not None:
        settings.enable_notification_sound = request.enable_notification_sound
    
    if request.notification_position is not None:
        valid_positions = ["top-right", "top-left", "bottom-right", "bottom-left"]
        if request.notification_position not in valid_positions:
            raise HTTPException(status_code=400, detail=f"Invalid position. Must be one of: {valid_positions}")
        settings.notification_position = request.notification_position
    
    if request.show_online_status is not None:
        settings.show_online_status = request.show_online_status
    
    if request.allow_direct_mentions is not None:
        settings.allow_direct_mentions = request.allow_direct_mentions
    
    settings.updated_at = datetime.now(timezone.utc)
    db.commit()
    
    return ForumSettingsResponse(
        display_alias=settings.display_alias,
        use_alias=settings.use_alias,
        enable_quick_reply_notifications=settings.enable_quick_reply_notifications,
        quick_reply_for_mentions=settings.quick_reply_for_mentions,
        quick_reply_for_replies=settings.quick_reply_for_replies,
        quick_reply_auto_dismiss_seconds=settings.quick_reply_auto_dismiss_seconds,
        enable_notification_sound=settings.enable_notification_sound,
        notification_position=settings.notification_position,
        show_online_status=settings.show_online_status,
        allow_direct_mentions=settings.allow_direct_mentions
    )
@forum_settings_router.post("/reset")
async def reset_forum_settings(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Reset forum settings to defaults"""
    
    # Delete existing settings to recreate with defaults
    existing = db.query(ForumUserSettings).filter(ForumUserSettings.user_id == current_user.id).first()
    if existing:
        db.delete(existing)
    
    # Create new default settings
    settings = ForumUserSettings(user_id=current_user.id)
    db.add(settings)
    db.commit()
    
    return {"success": True, "message": "Forum settings reset to defaults"}

@forum_settings_router.get("/alias/check")
async def check_alias_availability(
    alias: str,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Check if a display alias is available"""
    
    if not alias or len(alias) < 2 or len(alias) > 50:
        return {"available": False, "message": "Alias must be between 2 and 50 characters"}
    
    # Check if alias is already used by another user
    existing = db.query(ForumUserSettings).filter(
        ForumUserSettings.display_alias.ilike(alias),
        ForumUserSettings.user_id != current_user.id,
        ForumUserSettings.use_alias == True
    ).first()
    
    if existing:
        return {"available": False, "message": "This alias is already taken"}
    
    # Check if it matches any existing username
    existing_user = db.query(User).filter(User.username.ilike(alias)).first()
    if existing_user:
        return {"available": False, "message": "This alias matches an existing username"}
    
    return {"available": True, "message": "Alias is available"}

# Helper function to get user's forum display name
def get_user_forum_display_name(user: User, db: Session) -> str:
    """Get user's forum display name (alias or username)"""
    settings = db.query(ForumUserSettings).filter(ForumUserSettings.user_id == user.id).first()
    
    if settings and settings.use_alias and settings.display_alias:
        return settings.display_alias
    
    return user.username

# Helper function to check if user should receive quick reply notifications
def should_send_quick_reply_notification(user_id: int, notification_type: str, db: Session) -> bool:
    """Check if user should receive quick reply notifications"""
    settings = db.query(ForumUserSettings).filter(ForumUserSettings.user_id == user_id).first()
    
    if not settings or not settings.enable_quick_reply_notifications:
        return False
    
    if notification_type == "mention" and not settings.quick_reply_for_mentions:
        return False
    
    if notification_type == "reply" and not settings.quick_reply_for_replies:
        return False
    
    return True