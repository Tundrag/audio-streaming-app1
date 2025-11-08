# forum_routes.py - Complete Enhanced Forum with Existing Notification System Integration
from sqlalchemy import and_
from models import CampaignTier
from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, Body
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, and_, text, or_
from typing import List, Optional, Dict, Set
from pydantic import BaseModel
from datetime import datetime, timezone
import json
import re
import asyncio
import logging
from websocket_auth import get_websocket_auth, WebSocketSessionAuth
from websocket_manager import WebSocketManager
import asyncio
from forum_models import ForumThread, ForumMessage, ForumMention, ForumThreadFollower, ForumNotification
from models import User, ForumUserSettings
from models import User, ForumUserSettings

# Import your existing systems
from auth import login_required
from database import get_db
from models import User
from forum_models import ForumThread, ForumMessage, ForumMention, ForumThreadFollower, ForumNotification
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from forum_models import ForumMessageLike
from fastapi.templating import Jinja2Templates
from cache_busting import cache_busted_url_for
logger = logging.getLogger(__name__)

forum_router = APIRouter(prefix="/api/forum", tags=["forum"])
templates = Jinja2Templates(directory="templates")
templates.env.globals['url_for'] = cache_busted_url_for
templates.env.filters['url_for'] = cache_busted_url_for

# WebSocket managers for real-time forum updates with Redis pub/sub support
forum_thread_manager = WebSocketManager(channel="forum_threads")
forum_global_manager = WebSocketManager(channel="forum_global")

async def require_forum_alias(current_user: User, db: Session):
    """Ensure user has set a forum alias before accessing forum"""
    settings = db.query(ForumUserSettings).filter(ForumUserSettings.user_id == current_user.id).first()
    
    if not settings or not settings.use_alias or not settings.display_alias:
        raise HTTPException(
            status_code=403, 
            detail="Forum alias required. Please set your forum alias in settings before accessing the forum."
        )
    
    return settings

def get_user_forum_display_name(user: User, db: Session) -> str:
    """Get user's forum display name (alias or username)"""
    if hasattr(user, 'forum_settings') and user.forum_settings:
        if user.forum_settings.use_alias and user.forum_settings.display_alias:
            return user.forum_settings.display_alias
    return user.username

def should_send_quick_reply_notification(user_id: int, notification_type: str, db: Session) -> bool:
    """Check if user should receive quick reply notifications"""
    settings = db.query(ForumUserSettings).filter(ForumUserSettings.user_id == user_id).first()
    
    if not settings or not settings.enable_quick_reply_notifications:
        return False
class EveryoneModeratorSettings(BaseModel):
    team_rate_limit: int = 3  # Uses per time window
    rate_limit_window_hours: int = 24
    global_cooldown_minutes: int = 0  # Global cooldown between any @everyone uses
    require_approval: bool = False  # Require creator approval for team @everyone
    max_length: Optional[int] = None  # Max message length for @everyone messages
    allowed_threads_only: bool = False  # Only allow in specific threads
    notification_limit: Optional[int] = None  # Max users to notify per @everyone

class UserEveryoneRestriction(BaseModel):
    user_id: int
    is_restricted: bool = False
    custom_rate_limit: Optional[int] = None
    restriction_reason: Optional[str] = None
    restricted_until: Optional[datetime] = None

class EveryoneUsageAnalytics(BaseModel):
    user_id: int
    username: str
    total_uses: int
    last_used: Optional[datetime]
    success_rate: float
    avg_notifications_sent: float
    violation_count: int = 0

class ForumSettingsResponse(BaseModel):
    display_alias: Optional[str] = None
    use_alias: bool = False
    enable_quick_reply_notifications: bool = True
    quick_reply_for_mentions: bool = True
    quick_reply_for_replies: bool = True
    quick_reply_auto_dismiss_seconds: int = 10
    notification_position: str = "top-right"
    enable_notification_sound: bool = False
    show_online_status: bool = True
    allow_direct_mentions: bool = True

class ForumSettingsUpdate(BaseModel):
    display_alias: Optional[str] = None
    use_alias: Optional[bool] = None
    enable_quick_reply_notifications: Optional[bool] = None
    quick_reply_for_mentions: Optional[bool] = None
    quick_reply_for_replies: Optional[bool] = None
    quick_reply_auto_dismiss_seconds: Optional[int] = None
    notification_position: Optional[str] = None
    enable_notification_sound: Optional[bool] = None
    show_online_status: Optional[bool] = None
    allow_direct_mentions: Optional[bool] = None

# Enhanced Connection Manager with Notification Support
class ForumConnectionManager:
    def __init__(self):
        # Dictionary of thread_id -> set of WebSocket connections
        self.thread_connections: Dict[int, Set[WebSocket]] = {}
        # Dictionary of user_id -> set of WebSocket connections (for notifications)
        self.user_connections: Dict[int, Set[WebSocket]] = {}
        # Dictionary of WebSocket -> user info for identification
        self.connection_users: Dict[WebSocket, dict] = {}
    
    async def connect(self, websocket: WebSocket, thread_id: int, user_info: dict):
        """Enhanced connect method with better error handling"""
        try:
            user_id = user_info['user_id']
            logger.info(f"🔗 Connecting user {user_id} to thread {thread_id}")
            
            # Add to thread connections
            if thread_id not in self.thread_connections:
                self.thread_connections[thread_id] = set()
                logger.debug(f"Created new thread connection set for thread {thread_id}")
            self.thread_connections[thread_id].add(websocket)
            logger.debug(f"Added WebSocket to thread {thread_id} connections")
            
            # Add to user connections
            if user_id not in self.user_connections:
                self.user_connections[user_id] = set()
                logger.debug(f"Created new user connection set for user {user_id}")
            self.user_connections[user_id].add(websocket)
            logger.debug(f"Added WebSocket to user {user_id} connections")
            
            # Store connection info
            self.connection_users[websocket] = user_info
            logger.debug(f"Stored connection info for WebSocket")
            
            # Send connection confirmation
            await websocket.send_json({
                "type": "connected",
                "thread_id": thread_id,
                "message": "Connected to live updates"
            })
            logger.info(f"✅ Successfully connected user {user_id} to thread {thread_id}")
            
        except Exception as e:
            logger.error(f"❌ Error in manager.connect: {str(e)}")
            raise  # Re-raise the exception so caller can handle it
    
    def disconnect(self, websocket: WebSocket, thread_id: int = None):
        user_info = self.connection_users.get(websocket)
        if user_info:
            user_id = user_info['user_id']
            
            # Remove from user connections
            if user_id in self.user_connections:
                self.user_connections[user_id].discard(websocket)
                if not self.user_connections[user_id]:
                    del self.user_connections[user_id]
        
        # Remove from thread connections
        if thread_id and thread_id in self.thread_connections:
            self.thread_connections[thread_id].discard(websocket)
            if not self.thread_connections[thread_id]:
                del self.thread_connections[thread_id]
        
        self.connection_users.pop(websocket, None)
    
    async def broadcast_to_thread(self, thread_id: int, data: dict):
        """Broadcast to all users in a specific thread"""
        if thread_id in self.thread_connections:
            disconnected = set()
            for connection in self.thread_connections[thread_id].copy():
                try:
                    await connection.send_json(data)
                except:
                    disconnected.add(connection)
            
            # Clean up disconnected connections
            for connection in disconnected:
                self.disconnect(connection, thread_id)
    
    async def send_to_user(self, user_id: int, data: dict):
        """Send notification to specific user across all their connections"""
        if user_id not in self.user_connections:
            return False
            
        disconnected = set()
        sent = False
        
        for connection in self.user_connections[user_id].copy():
            try:
                await connection.send_json(data)
                sent = True
            except:
                disconnected.add(connection)
        
        # Clean up disconnected connections
        for connection in disconnected:
            self.disconnect(connection)
            
        return sent
    
    # 🚀 NEW: Broadcast to all connected users
    async def broadcast_to_all_users(self, data: dict):
        """Broadcast to all connected users"""
        disconnected = set()
        sent_count = 0
        
        # Get all unique connections across all users
        all_connections = set()
        for connections in self.user_connections.values():
            all_connections.update(connections)
        
        for connection in all_connections:
            try:
                await connection.send_json(data)
                sent_count += 1
            except:
                disconnected.add(connection)
        
        # Clean up disconnected connections
        for connection in disconnected:
            self.disconnect(connection)
        
        return sent_count
    
    async def send_mention_notification(self, user_id: int, data: dict):
        """Send mention notification to a specific user (compatibility method)"""
        return await self.send_to_user(user_id, data)

manager = ForumConnectionManager()

# Enhanced Pydantic models
class ThreadResponse(BaseModel):
    id: int
    title: str
    user_id: int
    username: str
    user_role: str
    user_badge_color: str
    message_count: int
    view_count: int
    is_pinned: bool
    is_locked: bool
    min_tier_cents: int
    last_message_at: str
    created_at: str
    can_access: bool
    is_following: bool = False
    follower_count: int = 0
    thread_type: str = "main"
    unread_count: int = 0
    # 🆕 NEW: Add tier display information
    tier_info: Optional[dict] = None

class MessageResponse(BaseModel):
    id: int
    content: str
    content_html: str
    user_id: int
    username: str
    user_role: str
    user_badge_color: str
    is_edited: bool
    created_at: str
    mentions: List[str] = []
    reply_to_id: Optional[int] = None
    reply_to_message: Optional[dict] = None
    reply_count: int = 0
    # NEW FIELDS FOR LIKES
    like_count: int = 0
    user_has_liked: bool = False

class CreateThreadRequest(BaseModel):
    title: str
    content: str
    min_tier_id: Optional[int] = None  # 🆕 NEW: Reference to CampaignTier.id
    # 🗑️ REMOVED: min_tier_cents, roles_allowed (now database-driven)

class UpdateThreadRequest(BaseModel):
    min_tier_id: Optional[int] = None  # 🆕 NEW: Reference to CampaignTier.id
    is_pinned: Optional[bool] = None
    is_locked: Optional[bool] = None



class CreateMessageRequest(BaseModel):
    content: str
    reply_to_id: Optional[int] = None

class UserSearchResponse(BaseModel):
    username: str
    display_name: str
    role: str
    badge_color: str

class CreateThreadFromMessageRequest(BaseModel):
    title: str
    content: str

class ThreadHierarchyResponse(BaseModel):
    id: int
    title: str
    user_id: int
    username: str
    user_role: str
    user_badge_color: str
    message_count: int
    view_count: int
    follower_count: int
    is_pinned: bool
    is_locked: bool
    min_tier_cents: int
    last_message_at: str
    created_at: str
    can_access: bool
    can_delete: bool
    can_manage: bool
    is_following: bool
    thread_type: str
    parent_message_id: Optional[int] = None
    created_from_message: Optional[dict] = None
    unread_count: int = 0
    tier_info: Optional[dict] = None

class MessageHierarchyResponse(BaseModel):
    id: int
    content: str
    content_html: str
    user_id: int
    username: str
    user_role: str
    user_badge_color: str
    is_edited: bool
    created_at: str
    mentions: List[str] = []
    spawned_thread_count: int = 0
    can_create_thread: bool = True
    reply_to_id: Optional[int] = None
    reply_to_message: Optional[dict] = None
    reply_count: int = 0
    # NEW FIELDS FOR LIKES
    like_count: int = 0
    user_has_liked: bool = False

class FollowThreadRequest(BaseModel):
    notify_on_new_message: bool = True
    notify_on_mention: bool = True
    notify_on_reply: bool = True

def get_message_like_info(message_id: int, user_id: int, db: Session) -> tuple[int, bool]:
    """Get like count and whether current user has liked the message"""
    like_count = db.query(func.count(ForumMessageLike.id)).filter(
        ForumMessageLike.message_id == message_id
    ).scalar() or 0
    
    user_has_liked = db.query(ForumMessageLike).filter(
        ForumMessageLike.message_id == message_id,
        ForumMessageLike.user_id == user_id
    ).first() is not None
    
    return like_count, user_has_liked

# UPDATED: Replace forum notification functions with existing system integration
async def create_forum_notification_via_existing_system(
    db: Session,
    user_id: int,
    thread_id: int,
    notification_type: str,
    title: str,
    content: str,
    message_id: Optional[int] = None,
    sender_id: Optional[int] = None
) -> int:
    """Create forum notification using existing notification system with enhanced data"""
    try:
        # Import existing notification system
        from notifications import create_notification_raw_sql_with_websocket
        
        # Get thread info
        thread = db.query(ForumThread).filter(ForumThread.id == thread_id).first()
        thread_title = thread.title if thread else "Forum Thread"
        
        # Get sender info  
        sender_username = None
        if sender_id:
            sender = db.query(User).filter(User.id == sender_id).first()
            sender_username = get_user_forum_display_name(sender, db) if sender else "Unknown User"
        
        # Get message content if message_id provided
        message_content = None
        message_preview = None
        if message_id:
            message = db.query(ForumMessage).filter(ForumMessage.id == message_id).first()
            if message:
                message_content = message.content
                message_preview = message.content[:200] + "..." if len(message.content) > 200 else message.content
        
        # Map forum notification types to existing enum values
        notification_type_map = {
            'mention': 'mention',
            'reply': 'reply',
            'new_message': 'new_content'
        }
        
        system_notification_type = notification_type_map.get(notification_type, 'new_content')
        
        # Create enhanced notification data that identifies this as forum-related
        notification_data = {
            'source': 'forum',
            'forum_type': notification_type,
            'thread_id': thread_id,
            'thread_title': thread_title,
            'message_id': message_id,
            'sender_username': sender_username,
            # NEW: Include actual message content
            'message_content': message_content,
            'message_preview': message_preview
        }
        
        # Add forum identifier to title - keep content as-is since it now includes message text
        forum_title = f"[Forum] {title}"
        # Content now already includes the message text with quotes
        forum_content = content
        
        # Use existing notification system with WebSocket
        notification_id = await create_notification_raw_sql_with_websocket(
            db=db,
            user_id=user_id,
            notification_type=system_notification_type,
            content=forum_content,  # This now includes quoted message content
            title=forum_title,
            sender_id=sender_id,
            notification_data=notification_data  # This also includes message content as backup
        )
        
        logger.info(f"✅ Enhanced forum notification {notification_id} sent to user {user_id}")
        return notification_id
        
    except Exception as e:
        logger.error(f"Error creating enhanced forum notification: {str(e)}")
        return None

async def notify_thread_followers_via_existing_system(db: Session, thread: ForumThread, message: ForumMessage, sender: User):
    """Notify thread followers using existing notification system - FIXED for direct mentions and no duplicates"""
    try:
        sender_display_name = get_user_forum_display_name(sender, db)
        
        # Parse mentions to check for special mentions
        user_mentions, has_everyone, has_creator, has_team = parse_mentions(message.content)
        
        # Track who gets notified to avoid duplicates
        notified_user_ids = set()
        
        # STEP 1: Handle @everyone mention
        if has_everyone and can_use_everyone_mention(sender):
            await notify_everyone_mention(db, thread, message, sender)
            # Get user IDs who received @everyone notifications
            eligible_users = db.query(User.id).join(ForumUserSettings).filter(
                ForumUserSettings.allow_everyone_mentions == True
            ).all()
            notified_user_ids.update([user_id[0] for user_id in eligible_users])
            logger.info(f"🔔 @everyone: notified {len(notified_user_ids)} users")
        
        # STEP 2: Handle @creator mention
        if has_creator:
            await notify_creator_mention(db, thread, message, sender, notified_user_ids)
            # Add all creators to notified list to prevent double notifications
            all_users = db.query(User).all()
            for user in all_users:
                if user.is_creator:
                    notified_user_ids.add(user.id)
            logger.info(f"🔔 @creator: added creators to notified list")
        
        # STEP 3: Handle @team mention
        if has_team and can_use_team_mention(sender):
            await notify_team_mention(db, thread, message, sender, notified_user_ids)
            # Add all team members to notified list to prevent double notifications
            all_users = db.query(User).all()
            for user in all_users:
                if user.is_creator or user.is_team:
                    notified_user_ids.add(user.id)
            logger.info(f"🔔 @team: added team members to notified list")
        
        # STEP 4: 🆕 NEW - Handle direct user mentions (@username) - NOTIFY EVEN IF NOT FOLLOWING
        for mention in message.mentions:
            if mention.mentioned_user_id and mention.mentioned_user_id not in notified_user_ids:
                mentioned_user = db.query(User).filter(User.id == mention.mentioned_user_id).first()
                
                if mentioned_user and mentioned_user.id != sender.id:  # Don't notify sender
                    # Check if mentioned user can access this thread
                    if thread.can_access(mentioned_user):
                        # Check if user allows direct mentions (default: True)
                        user_settings = db.query(ForumUserSettings).filter(
                            ForumUserSettings.user_id == mentioned_user.id
                        ).first()
                        
                        allow_mentions = True  # Default to allowing mentions
                        if user_settings and hasattr(user_settings, 'allow_direct_mentions'):
                            allow_mentions = user_settings.allow_direct_mentions
                        
                        if allow_mentions:
                            message_preview = message.content[:150] + "..." if len(message.content) > 150 else message.content
                            
                            await create_forum_notification_via_existing_system(
                                db=db,
                                user_id=mentioned_user.id,
                                thread_id=thread.id,
                                message_id=message.id,
                                sender_id=sender.id,
                                notification_type="mention",
                                title="You were mentioned",
                                content=f'{sender_display_name} mentioned you: "{message_preview}"'
                            )
                            
                            notified_user_ids.add(mentioned_user.id)
                            logger.info(f"✅ Direct mention notification sent to {mentioned_user.username} (following: {is_following_thread(mentioned_user.id, thread.id, db)})")
                        else:
                            logger.info(f"⚠️ {mentioned_user.username} has disabled direct mentions")
                    else:
                        logger.info(f"⚠️ {mentioned_user.username} cannot access thread {thread.id}")
        
        # STEP 5: Handle regular thread followers - but SKIP users already notified
        followers = db.query(ForumThreadFollower).filter(
            ForumThreadFollower.thread_id == thread.id,
            ForumThreadFollower.is_active == True,
            ForumThreadFollower.user_id != sender.id  # Don't notify sender
        ).all()
        
        logger.info(f"🔔 Checking {len(followers)} thread followers...")
        
        for follower in followers:
            # 🚀 KEY FIX: Skip if user already got a notification (special mention or direct mention)
            if follower.user_id in notified_user_ids:
                logger.info(f"⏭️ Skipping follower {follower.user_id} - already notified via special/direct mention")
                continue
                
            # Check if this follower should be notified for regular notifications
            if follower.should_notify_for_message(message):
                message_preview = message.content[:150] + "..." if len(message.content) > 150 else message.content
                
                # Determine notification type and create content
                if message.reply_to and message.reply_to.user_id == follower.user_id:
                    notif_type = "reply"
                    title = "New Reply"
                    content = f'{sender_display_name} replied: "{message_preview}"'
                    
                else:
                    notif_type = "new_message"
                    title = "New Message"
                    content = f'{sender_display_name} posted: "{message_preview}"'
                
                # Create notification using existing system
                await create_forum_notification_via_existing_system(
                    db=db,
                    user_id=follower.user_id,
                    thread_id=thread.id,
                    message_id=message.id,
                    sender_id=sender.id,
                    notification_type=notif_type,
                    title=title,
                    content=content
                )
                
                logger.info(f"✅ Sent follower notification to user {follower.user_id}")
        
        logger.info(f"🎯 Final summary: {len(notified_user_ids)} users notified total")
        
    except Exception as e:
        logger.error(f"Error notifying thread followers via existing system: {str(e)}")


# Helper function to check if user is following a thread
def is_following_thread(user_id: int, thread_id: int, db: Session) -> bool:
    """Check if user is following a thread"""
    follower = db.query(ForumThreadFollower).filter(
        ForumThreadFollower.thread_id == thread_id,
        ForumThreadFollower.user_id == user_id,
        ForumThreadFollower.is_active == True
    ).first()
    return follower is not None

@forum_router.get("/debug/user/{user_id}/forum-settings")
async def debug_user_forum_settings(
    user_id: int,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Debug endpoint to check a user's forum settings"""
    
    if not (current_user.is_creator or current_user.is_team):
        raise HTTPException(status_code=403, detail="Creator/Team access required")
    
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    forum_settings = db.query(ForumUserSettings).filter(
        ForumUserSettings.user_id == user_id
    ).first()
    
    return {
        "user_id": user_id,
        "username": target_user.username,
        "is_team": target_user.is_team,
        "is_creator": target_user.is_creator,
        "has_forum_settings": forum_settings is not None,
        "forum_settings": {
            "display_alias": forum_settings.display_alias if forum_settings else None,
            "use_alias": forum_settings.use_alias if forum_settings else False,
            "allow_direct_mentions": forum_settings.allow_direct_mentions if forum_settings else True,
            "allow_everyone_mentions": forum_settings.allow_everyone_mentions if forum_settings else True,
        } if forum_settings else "No forum settings found - using defaults"
    }

# Helper functions
def get_user_role_display(user: User) -> str:
    """Get user's display role using your existing system"""
    if user.is_creator:
        return "Creator"
    elif user.is_team:
        return "Team"
    elif user.is_patreon:
        return "Patron"
    elif user.is_kofi:
        return "Supporter"
    else:
        return "Member"

def get_user_badge_color(user: User) -> str:
    """Get badge color using your existing system"""
    if user.is_creator:
        return "#f59e0b"  # Gold
    elif user.is_team:
        return "#3b82f6"  # Blue
    elif user.is_patreon:
        return "#f97316"  # Orange
    elif user.is_kofi:
        return "#10b981"  # Green
    else:
        return "#6b7280"  # Gray

def parse_mentions(content: str) -> tuple[List[str], bool, bool, bool]:
    """Extract @mentions from message content - ENHANCED with @everyone, @creator, @team support"""
    mention_pattern = r'@(\w+)'
    mentions = re.findall(mention_pattern, content, re.IGNORECASE)
    
    # Check for special mentions (case insensitive)
    has_everyone = any(mention.lower() == 'everyone' for mention in mentions)
    has_creator = any(mention.lower() == 'creator' for mention in mentions)
    has_team = any(mention.lower() == 'team' for mention in mentions)
    
    # Remove special mentions from regular mentions list
    user_mentions = [mention for mention in mentions 
                    if mention.lower() not in ['everyone', 'creator', 'team']]
    user_mentions = list(set(user_mentions))  # Remove duplicates
    
    return user_mentions, has_everyone, has_creator, has_team


def parse_all_mentions(content: str) -> tuple[List[str], bool, bool, bool]:
    """Extract ALL @mentions from message content - FULL VERSION"""
    mention_pattern = r'@(\w+)'
    mentions = re.findall(mention_pattern, content, re.IGNORECASE)
    
    # Check for special mentions (case insensitive)
    has_everyone = any(mention.lower() == 'everyone' for mention in mentions)
    has_creator = any(mention.lower() == 'creator' for mention in mentions)
    has_team = any(mention.lower() == 'team' for mention in mentions)
    
    # Remove special mentions from regular mentions list
    user_mentions = [mention for mention in mentions 
                    if mention.lower() not in ['everyone', 'creator', 'team']]
    user_mentions = list(set(user_mentions))  # Remove duplicates
    
    return user_mentions, has_everyone, has_creator, has_team


def format_message_html(content: str, db: Session) -> str:
    """Convert @mentions to HTML links - ENHANCED with special mentions support"""
    mention_pattern = r'@(\w+)'
    
    def replace_mention(match):
        mention_name = match.group(1)
        mention_lower = mention_name.lower()
        
        # Handle special mentions
        if mention_lower == 'everyone':
            return f'<span class="mention mention-everyone" data-mention-type="everyone">@everyone</span>'
        elif mention_lower == 'creator':
            return f'<span class="mention mention-creator" data-mention-type="creator">@creator</span>'
        elif mention_lower == 'team':
            return f'<span class="mention mention-team" data-mention-type="team">@team</span>'
        
        # Regular user mention logic (existing code)
        user = db.query(User).filter(User.username.ilike(mention_name)).first()
        
        if not user:
            forum_setting = db.query(ForumUserSettings).filter(
                and_(
                    ForumUserSettings.use_alias == True,
                    ForumUserSettings.display_alias.ilike(mention_name),
                    ForumUserSettings.display_alias.isnot(None)
                )
            ).first()
            
            if forum_setting:
                user = db.query(User).filter(User.id == forum_setting.user_id).first()
        
        if user:
            display_name = get_user_forum_display_name(user, db)
            return f'<span class="mention" data-user-id="{user.id}">@{display_name}</span>'
        
        return match.group(0)  # Return original if user doesn't exist
    
    return re.sub(mention_pattern, replace_mention, content, flags=re.IGNORECASE)


async def create_mentions(message_id: int, mentions: List[str], has_everyone: bool, has_creator: bool, has_team: bool, db: Session):
    """Create mention records in database - FIXED to use only existing database fields"""
    
    # Handle regular user mentions (existing logic)
    for mention_name in mentions:
        user = None
        
        # First check if it's a username
        user = db.query(User).filter(User.username.ilike(mention_name)).first()
        
        # If not found by username, check if it's an alias
        if not user:
            forum_setting = db.query(ForumUserSettings).filter(
                and_(
                    ForumUserSettings.use_alias == True,
                    ForumUserSettings.display_alias.ilike(mention_name),
                    ForumUserSettings.display_alias.isnot(None)
                )
            ).first()
            
            if forum_setting:
                user = db.query(User).filter(User.id == forum_setting.user_id).first()
        
        if user:
            mention = ForumMention(
                message_id=message_id,
                mentioned_user_id=user.id,
                is_everyone_mention=False,  # Regular user mention
                mention_type="user",
                created_at=datetime.now(timezone.utc)
            )
            db.add(mention)
    
    # Handle @everyone mention
    if has_everyone:
        everyone_mention = ForumMention(
            message_id=message_id,
            mentioned_user_id=None,  # No specific user
            is_everyone_mention=True,  # This IS an @everyone mention
            mention_type="everyone",
            created_at=datetime.now(timezone.utc)
        )
        db.add(everyone_mention)
    
    # Handle @creator mention - FIXED: only use existing fields
    if has_creator:
        creator_mention = ForumMention(
            message_id=message_id,
            mentioned_user_id=None,  # No specific user
            is_everyone_mention=False,  # This is NOT an @everyone mention
            mention_type="creator",  # Use mention_type to identify it as @creator
            created_at=datetime.now(timezone.utc)
        )
        db.add(creator_mention)
    
    # Handle @team mention - FIXED: only use existing fields
    if has_team:
        team_mention = ForumMention(
            message_id=message_id,
            mentioned_user_id=None,  # No specific user
            is_everyone_mention=False,  # This is NOT an @everyone mention  
            mention_type="team",  # Use mention_type to identify it as @team
            created_at=datetime.now(timezone.utc)
        )
        db.add(team_mention)

def can_use_everyone_mention(user: User) -> bool:
    """Check if user has permission to use @everyone"""
    # Only creators and team members can use @everyone
    return user.is_creator or user.is_team

async def notify_creator_mention(
    db: Session, 
    thread: ForumThread, 
    message: ForumMessage, 
    sender: User,
    exclude_user_ids: Set[int] = None
):
    """Send @creator notifications - FIXED to use same logic as badge display"""
    try:
        exclude_user_ids = exclude_user_ids or set()
        exclude_user_ids.add(sender.id)  # Don't notify the sender
        
        logger.info(f"🔔 Processing @creator mention by {sender.username} in thread {thread.id}")
        
        # Use the SAME logic as get_user_role_display() function
        all_users = db.query(User).all()
        
        creators = []
        for user in all_users:
            # Skip excluded users
            if user.id in exclude_user_ids:
                continue
                
            # Find creators using same logic as badge display
            if user.is_creator:
                creators.append(user)
                logger.info(f"✅ Found creator: {user.username}")
        
        logger.info(f"🔍 Found {len(creators)} creators to notify")
        
        for creator in creators:
            if thread.can_access(creator):
                await create_forum_notification_via_existing_system(
                    db=db,
                    user_id=creator.id,
                    thread_id=thread.id,
                    message_id=message.id,
                    sender_id=sender.id,
                    notification_type="mention",
                    title="@creator Mention",
                    content=f"{get_user_forum_display_name(sender, db)} mentioned you as creator: \"{message.content[:150]}{'...' if len(message.content) > 150 else ''}\""
                )
                logger.info(f"✅ Sent @creator notification to {creator.username}")
            else:
                logger.info(f"⚠️ Creator {creator.username} cannot access thread {thread.id}")
        
    except Exception as e:
        logger.error(f"Error sending @creator notification: {str(e)}")

async def notify_team_mention(
    db: Session, 
    thread: ForumThread, 
    message: ForumMessage, 
    sender: User,
    exclude_user_ids: Set[int] = None
):
    """Send @team notifications - FIXED to use same logic as badge display"""
    try:
        exclude_user_ids = exclude_user_ids or set()
        exclude_user_ids.add(sender.id)  # Don't notify the sender
        
        logger.info(f"🔔 Processing @team mention by {sender.username} in thread {thread.id}")
        
        # Use the SAME logic as get_user_role_display() function
        # Get ALL users, then filter in Python (same as badge logic)
        all_users = db.query(User).all()
        
        team_members = []
        for user in all_users:
            # Skip excluded users (like the sender)
            if user.id in exclude_user_ids:
                continue
                
            # Use EXACT same logic as get_user_role_display()
            if user.is_creator or user.is_team:
                team_members.append(user)
                logger.info(f"✅ Found team member: {user.username} (creator: {user.is_creator}, team: {user.is_team})")
        
        logger.info(f"🔍 Found {len(team_members)} team members to notify")
        
        notification_count = 0
        
        for member in team_members:
            # Check if team member can access this thread
            if thread.can_access(member):
                await create_forum_notification_via_existing_system(
                    db=db,
                    user_id=member.id,
                    thread_id=thread.id,
                    message_id=message.id,
                    sender_id=sender.id,
                    notification_type="mention",
                    title="@team Mention",
                    content=f"{get_user_forum_display_name(sender, db)} mentioned the team: \"{message.content[:150]}{'...' if len(message.content) > 150 else ''}\""
                )
                notification_count += 1
                logger.info(f"✅ Sent @team notification to {member.username}")
            else:
                logger.info(f"⚠️ Team member {member.username} cannot access thread {thread.id}")
        
        logger.info(f"✅ Sent @team notifications to {notification_count} team members")
        
        # Send live WebSocket update
        team_mention_data = {
            "type": "team_mention",
            "thread_id": thread.id,
            "message_id": message.id,
            "sender": {
                "id": sender.id,
                "username": get_user_forum_display_name(sender, db),
                "role": get_user_role_display(sender)
            },
            "thread_title": thread.title,
            "notification_count": notification_count
        }
        # New WebSocketManager (Redis pub/sub)
        await forum_global_manager.broadcast(team_mention_data)
        # Legacy manager (backwards compatibility)
        await manager.broadcast_to_all_users(team_mention_data)
        
    except Exception as e:
        logger.error(f"Error sending @team notifications: {str(e)}")

async def notify_everyone_mention(
    db: Session, 
    thread: ForumThread, 
    message: ForumMessage, 
    sender: User,
    exclude_user_ids: Set[int] = None
):
    """Send @everyone notifications to all eligible forum users"""
    try:
        exclude_user_ids = exclude_user_ids or set()
        exclude_user_ids.add(sender.id)  # Don't notify the sender
        
        logger.info(f"🔔 Processing @everyone mention by {sender.username} in thread {thread.id}")
        
        # Get all users who have forum settings (indicating they've used the forum)
        # and haven't opted out of @everyone mentions
        eligible_users = db.query(User).join(ForumUserSettings).filter(
            and_(
                ForumUserSettings.allow_everyone_mentions == True,  # New setting
                ~User.id.in_(exclude_user_ids)
            )
        ).all()
        
        # Also include users without forum settings but who are active
        users_without_settings = db.query(User).filter(
            and_(
                ~User.id.in_(db.query(ForumUserSettings.user_id).subquery()),
                ~User.id.in_(exclude_user_ids),
                User.is_active == True  # Assuming you have an is_active field
            )
        ).all()
        
        all_eligible_users = eligible_users + users_without_settings
        
        logger.info(f"🔔 Found {len(all_eligible_users)} eligible users for @everyone notification")
        
        # Batch notifications to avoid overwhelming the system
        batch_size = 50
        notification_count = 0
        
        for i in range(0, len(all_eligible_users), batch_size):
            batch = all_eligible_users[i:i + batch_size]
            
            # Create notifications for this batch
            tasks = []
            for user in batch:
                # Check if user can access the thread
                if thread.can_access(user):
                    task = create_forum_notification_via_existing_system(
                        db=db,
                        user_id=user.id,
                        thread_id=thread.id,
                        message_id=message.id,
                        sender_id=sender.id,
                        notification_type="mention",
                        title="@everyone Mention",
                        content=f"{get_user_forum_display_name(sender, db)} mentioned everyone: \"{message.content[:150]}{'...' if len(message.content) > 150 else ''}\""
                    )
                    tasks.append(task)
            
            # Execute batch of notifications
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
                notification_count += len(tasks)
                
                # Small delay between batches to avoid overwhelming the system
                if i + batch_size < len(all_eligible_users):
                    await asyncio.sleep(0.1)
        
        logger.info(f"✅ Sent {notification_count} @everyone notifications")
        
        # Send live WebSocket update to all connected users
        everyone_mention_data = {
            "type": "everyone_mention",
            "thread_id": thread.id,
            "message_id": message.id,
            "sender": {
                "id": sender.id,
                "username": get_user_forum_display_name(sender, db),
                "role": get_user_role_display(sender)
            },
            "thread_title": thread.title,
            "notification_count": notification_count
        }
        # New WebSocketManager (Redis pub/sub)
        await forum_global_manager.broadcast(everyone_mention_data)
        # Legacy manager (backwards compatibility)
        await manager.broadcast_to_all_users(everyone_mention_data)
        
    except Exception as e:
        logger.error(f"Error sending @everyone notifications: {str(e)}")

async def get_user_by_id(user_id: int, db: Session) -> Optional[User]:
    """Simple user lookup by ID for WebSocket connections"""
    try:
        user = db.query(User).filter(User.id == user_id).first()
        return user
    except Exception as e:
        logger.error(f"Error getting user by ID: {e}")
        return None

def build_reply_data(message: ForumMessage, db: Session) -> Optional[dict]:  # ADD db parameter
    """Build reply data for a message"""
    if not message.reply_to:
        return None
    
    return {
        "id": message.reply_to.id,
        "content": message.reply_to.content[:100] + "..." if len(message.reply_to.content) > 100 else message.reply_to.content,
        "username": get_user_forum_display_name(message.reply_to.user, db),  # CHANGED: Use display name
        "user_role": get_user_role_display(message.reply_to.user),
        "user_badge_color": get_user_badge_color(message.reply_to.user),
        "created_at": message.reply_to.created_at.isoformat()
    }
# Main Endpoints

@forum_router.get("/", response_class=HTMLResponse)
async def forum_page(
    request: Request, 
    current_user: User = Depends(login_required)
):
    """Forum page - extends base.html with SPA content"""
    return templates.TemplateResponse("forum.html", {
        "request": request, 
        "user": current_user,
        "page_title": "Community Forum"
    })

# Enhanced threads endpoint with filtering
@forum_router.get("/threads", response_model=List[ThreadResponse])
async def get_threads(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=50),
    filter_type: str = Query("main", description="Filter type: 'main', 'following', 'all'"),
    thread_type: str = Query("all", description="Filter by thread type: 'main', 'sub', or 'all'"),
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Get forum threads with enhanced filtering and tier info"""
    
    offset = (page - 1) * limit
    
    # Build base query based on filter_type (existing logic stays the same)
    if filter_type == "following":
        query = db.query(ForumThread).join(ForumThreadFollower).filter(
            ForumThreadFollower.user_id == current_user.id,
            ForumThreadFollower.is_active == True
        )
    elif filter_type == "main":
        query = db.query(ForumThread).filter(ForumThread.thread_type == "main")
    else:
        query = db.query(ForumThread)
    
    # Apply additional thread type filtering if specified (existing logic)
    if thread_type == "main":
        query = query.filter(ForumThread.thread_type == "main")
    elif thread_type == "sub":
        query = query.filter(ForumThread.thread_type == "sub")
    
    # Apply ordering (existing logic)
    if filter_type == "following":
        query = query.order_by(
            ForumThread.is_pinned.desc(),
            ForumThread.last_message_at.desc()
        )
    elif filter_type == "main":
        query = query.order_by(
            ForumThread.is_pinned.desc(),
            ForumThread.last_message_at.desc()
        )
    else:
        query = query.order_by(
            ForumThread.thread_type.desc(),
            ForumThread.is_pinned.desc(),
            ForumThread.last_message_at.desc()
        )
    
    all_threads = query.offset(offset).limit(limit).all()
    
    # Filter threads user can access and add tier info
    accessible_threads = []
    for thread in all_threads:
        if thread.can_access(current_user):  # ✅ Uses new tier logic
            # Get follower info (existing logic)
            follower = thread.get_follower(current_user)
            is_following = follower is not None and follower.is_active
            
            # Get unread count (existing logic)
            unread_count = 0
            if is_following:
                try:
                    unread_count = db.execute(
                        text("""
                        SELECT COUNT(*) as count
                        FROM notifications
                        WHERE user_id = :user_id 
                        AND is_read = false
                        AND (
                            title LIKE '[Forum]%' 
                            OR notification_data::text LIKE '%"source": "forum"%'
                        )
                        AND notification_data::text LIKE :thread_filter
                        """),
                        {
                            "user_id": current_user.id,
                            "thread_filter": f'%"thread_id": {thread.id}%'
                        }
                    ).scalar() or 0
                except Exception as e:
                    logger.error(f"Error getting unread count for thread {thread.id}: {e}")
                    unread_count = 0
            
            # 🆕 NEW: Get tier info
            tier_info = thread.get_tier_info(db)
            
            accessible_threads.append(ThreadResponse(
                id=thread.id,
                title=thread.title,
                user_id=thread.user_id,
                username=get_user_forum_display_name(thread.user, db),
                user_role=get_user_role_display(thread.user),
                user_badge_color=get_user_badge_color(thread.user),
                message_count=thread.message_count,
                view_count=thread.view_count,
                is_pinned=thread.is_pinned,
                is_locked=thread.is_locked,
                min_tier_cents=thread.min_tier_cents,
                last_message_at=thread.last_message_at.isoformat() if thread.last_message_at else datetime.now(timezone.utc).isoformat(),
                created_at=thread.created_at.isoformat() if thread.created_at else datetime.now(timezone.utc).isoformat(),
                can_access=True,
                is_following=is_following,
                follower_count=thread.follower_count or 0,
                thread_type=thread.thread_type,
                unread_count=unread_count,
                tier_info=tier_info  # 🆕 NEW: Include tier info
            ))
    
    return accessible_threads

# UPDATED: Forum notification endpoints that work with existing system
@forum_router.get("/notifications/count")
async def get_forum_notification_count(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get count of unread forum notifications"""
    try:
        count = db.execute(
            text("""
            SELECT COUNT(*) as count
            FROM notifications
            WHERE user_id = :user_id 
            AND is_read = false
            AND (
                title LIKE '[Forum]%' 
                OR notification_data::text LIKE '%"source": "forum"%'
            )
            """),
            {"user_id": current_user.id}
        ).scalar() or 0
        return {"count": count}
    except Exception as e:
        logger.error(f"Error getting forum notification count: {e}")
        return {"count": 0}

@forum_router.get("/notifications")
async def get_forum_notifications(
    limit: int = Query(20, ge=1, le=100),
    skip: int = Query(0, ge=0),
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get forum notifications from main notifications table"""
    try:
        # Query main notifications table for forum-related notifications
        result = db.execute(
            text("""
            SELECT 
                id, user_id, sender_id, type, content, title, 
                is_read, notification_data, created_at, read_at
            FROM notifications 
            WHERE user_id = :user_id 
            AND (
                title LIKE '[Forum]%' 
                OR notification_data::text LIKE '%"source": "forum"%'
            )
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :skip
            """),
            {
                "user_id": current_user.id,
                "limit": limit,
                "skip": skip
            }
        )
        
        # Format the response
        notifications = []
        for row in result.mappings():
            sender_data = None
            if row["sender_id"]:
                sender = db.query(User).filter(User.id == row["sender_id"]).first()
                if sender:
                    sender_data = {
                        "id": sender.id,
                        "username": sender.username
                    }
            
            # Parse notification_data JSON
            notification_data = row["notification_data"] or {}
            if isinstance(notification_data, str):
                import json
                try:
                    notification_data = json.loads(notification_data)
                except:
                    notification_data = {}
            
            # Format time ago
            created_at = row["created_at"]
            time_since = ""
            if created_at:
                now = datetime.now(timezone.utc)
                diff = now - created_at
                if diff.days > 7:
                    time_since = created_at.strftime("%B %d, %Y")
                elif diff.days > 0:
                    time_since = f"{diff.days} days ago"
                elif diff.seconds > 3600:
                    hours = diff.seconds // 3600
                    time_since = f"{hours} hours ago"
                elif diff.seconds > 60:
                    minutes = diff.seconds // 60
                    time_since = f"{minutes} minutes ago"
                else:
                    time_since = "just now"
            
            # Get forum notification type from notification_data
            forum_type = notification_data.get('forum_type', 'new_message')
            
            # Clean up title (remove [Forum] prefix)
            clean_title = str(row["title"]).replace("[Forum] ", "")
            
            notifications.append({
                "id": row["id"],
                "notification_type": forum_type,
                "type": f"forum_{forum_type}",  # Add forum_ prefix for compatibility
                "content": row["content"],
                "title": clean_title,
                "sender": sender_data,
                "thread_id": notification_data.get("thread_id"),
                "thread_title": notification_data.get("thread_title"),
                "message_id": notification_data.get("message_id"),
                "is_read": row["is_read"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "time_since": time_since
            })
        
        # Get the total unread count for forum notifications
        unread_result = db.execute(
            text("""
            SELECT COUNT(*) as count
            FROM notifications
            WHERE user_id = :user_id 
            AND is_read = false
            AND (
                title LIKE '[Forum]%' 
                OR notification_data::text LIKE '%"source": "forum"%'
            )
            """),
            {"user_id": current_user.id}
        )
        unread_count = unread_result.scalar()
        
        return {
            "notifications": notifications,
            "unread_count": unread_count,
            "total": len(notifications)
        }
        
    except Exception as e:
        logger.error(f"Error getting forum notifications: {str(e)}")
        # Return empty results instead of error to prevent 404
        return {
            "notifications": [],
            "unread_count": 0,
            "total": 0
        }

@forum_router.post("/notifications/{notification_id}/read")
async def mark_forum_notification_read(
    notification_id: int,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Mark a forum notification as read"""
    try:
        # First check if notification exists and belongs to user
        check_result = db.execute(
            text("""
            SELECT id, is_read 
            FROM notifications 
            WHERE id = :notification_id AND user_id = :user_id
            AND (
                title LIKE '[Forum]%' 
                OR notification_data::text LIKE '%"source": "forum"%'
            )
            """),
            {
                "notification_id": notification_id,
                "user_id": current_user.id
            }
        ).first()
        
        if not check_result:
            raise HTTPException(status_code=404, detail="Forum notification not found")
        
        # Update the notification to mark as read
        db.execute(
            text("""
            UPDATE notifications 
            SET is_read = true, read_at = :read_at 
            WHERE id = :notification_id
            """),
            {
                "notification_id": notification_id,
                "read_at": datetime.now(timezone.utc)
            }
        )
        
        db.commit()
        
        # Return updated unread count for forum notifications only
        unread_count = db.execute(
            text("""
            SELECT COUNT(*) as count
            FROM notifications
            WHERE user_id = :user_id 
            AND is_read = false
            AND (
                title LIKE '[Forum]%' 
                OR notification_data::text LIKE '%"source": "forum"%'
            )
            """),
            {"user_id": current_user.id}
        ).scalar()
        
        return {"success": True, "unread_count": unread_count}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error marking forum notification as read: {str(e)}")
        db.rollback()
        return {"success": False, "error": "An error occurred"}

@forum_router.post("/notifications/mark-all-read")
async def mark_all_forum_notifications_read(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Mark all forum notifications as read"""
    try:
        result = db.execute(
            text("""
            UPDATE notifications
            SET is_read = true, read_at = :read_at
            WHERE user_id = :user_id 
            AND is_read = false
            AND (
                title LIKE '[Forum]%' 
                OR notification_data::text LIKE '%"source": "forum"%'
            )
            """),
            {
                "user_id": current_user.id,
                "read_at": datetime.now(timezone.utc)
            }
        )
        
        db.commit()
        
        # Get number of rows affected
        affected_rows = result.rowcount if hasattr(result, "rowcount") else 0
        
        return {"success": True, "marked_read": affected_rows, "unread_count": 0}
    except Exception as e:
        logger.error(f"Error marking all forum notifications as read: {str(e)}")
        db.rollback()
        return {"success": False, "error": "An error occurred"}

# Add a simple settings endpoint for the quick reply manager
@forum_router.get("/settings", response_model=ForumSettingsResponse)
async def get_forum_settings(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get forum settings for current user"""
    settings = db.query(ForumUserSettings).filter(ForumUserSettings.user_id == current_user.id).first()
    
    if not settings:
        # Create default settings
        settings = ForumUserSettings(user_id=current_user.id)
        db.add(settings)
        db.commit()
    
    return ForumSettingsResponse(
        display_alias=settings.display_alias,
        use_alias=settings.use_alias,
        enable_quick_reply_notifications=settings.enable_quick_reply_notifications,
        quick_reply_for_mentions=settings.quick_reply_for_mentions,
        quick_reply_for_replies=settings.quick_reply_for_replies,
        quick_reply_auto_dismiss_seconds=settings.quick_reply_auto_dismiss_seconds,
        notification_position=settings.notification_position,
        enable_notification_sound=settings.enable_notification_sound,
        show_online_status=settings.show_online_status,
        allow_direct_mentions=settings.allow_direct_mentions
    )


@forum_router.patch("/settings", response_model=ForumSettingsResponse)
async def update_forum_settings(
    settings_update: ForumSettingsUpdate,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Update forum settings for current user"""
    settings = db.query(ForumUserSettings).filter(ForumUserSettings.user_id == current_user.id).first()
    
    if not settings:
        settings = ForumUserSettings(user_id=current_user.id)
        db.add(settings)
    
    # Validate alias if provided
    if settings_update.display_alias is not None:
        alias = settings_update.display_alias.strip()
        if alias and len(alias) < 2:
            raise HTTPException(status_code=400, detail="Alias must be at least 2 characters long")
        if alias and len(alias) > 50:
            raise HTTPException(status_code=400, detail="Alias must be no more than 50 characters long")
        
        # Check if alias is already taken (case-insensitive)
        if alias:
            existing = db.query(ForumUserSettings).filter(
                func.lower(ForumUserSettings.display_alias) == func.lower(alias),
                ForumUserSettings.user_id != current_user.id
            ).first()
            if existing:
                raise HTTPException(status_code=400, detail="This alias is already taken")
        
        settings.display_alias = alias if alias else None
    
    # Update other settings
    update_data = settings_update.dict(exclude_unset=True)
    for field, value in update_data.items():
        if hasattr(settings, field):
            setattr(settings, field, value)
    
    settings.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(settings)
    
    return ForumSettingsResponse(
        display_alias=settings.display_alias,
        use_alias=settings.use_alias,
        enable_quick_reply_notifications=settings.enable_quick_reply_notifications,
        quick_reply_for_mentions=settings.quick_reply_for_mentions,
        quick_reply_for_replies=settings.quick_reply_for_replies,
        quick_reply_auto_dismiss_seconds=settings.quick_reply_auto_dismiss_seconds,
        notification_position=settings.notification_position,
        enable_notification_sound=settings.enable_notification_sound,
        show_online_status=settings.show_online_status,
        allow_direct_mentions=settings.allow_direct_mentions
    )


@forum_router.get("/settings/alias/check")
async def check_alias_availability(
    alias: str = Query(..., min_length=2, max_length=50),
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Check if an alias is available"""
    alias = alias.strip()
    
    # Check if alias is already taken (case-insensitive)
    existing = db.query(ForumUserSettings).filter(
        func.lower(ForumUserSettings.display_alias) == func.lower(alias),
        ForumUserSettings.user_id != current_user.id
    ).first()
    
    if existing:
        return {"available": False, "message": "This alias is already taken"}
    
    # Check if it matches any existing username (case-insensitive)
    existing_user = db.query(User).filter(
        func.lower(User.username) == func.lower(alias),
        User.id != current_user.id
    ).first()
    
    if existing_user:
        return {"available": False, "message": "This alias matches an existing username"}
    
    return {"available": True, "message": "Alias is available"}

@forum_router.post("/settings/reset")
async def reset_forum_settings(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Reset forum settings to defaults"""
    settings = db.query(ForumUserSettings).filter(ForumUserSettings.user_id == current_user.id).first()
    
    if settings:
        db.delete(settings)
        db.commit()
    
    # Create new default settings
    default_settings = ForumUserSettings(user_id=current_user.id)
    db.add(default_settings)
    db.commit()
    
    return {"success": True, "message": "Settings reset to defaults"}


# Get single thread endpoint
@forum_router.get("/threads/{thread_id}", response_model=ThreadHierarchyResponse)
async def get_thread(
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Get a single thread by ID with tier info"""
    
    thread = db.query(ForumThread).filter(ForumThread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    
    if not thread.can_access(current_user):  # ✅ Uses new tier logic
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Get follower info (existing logic)
    follower = thread.get_follower(current_user)
    is_following = follower is not None and follower.is_active
    
    # Get unread count (existing logic)
    unread_count = 0
    if is_following:
        try:
            unread_count = db.execute(
                text("""
                SELECT COUNT(*) as count
                FROM notifications
                WHERE user_id = :user_id 
                AND is_read = false
                AND (
                    title LIKE '[Forum]%' 
                    OR notification_data::text LIKE '%"source": "forum"%'
                )
                AND notification_data::text LIKE :thread_filter
                """),
                {
                    "user_id": current_user.id,
                    "thread_filter": f'%"thread_id": {thread.id}%'
                }
            ).scalar() or 0
        except Exception as e:
            logger.error(f"Error getting unread count for thread {thread.id}: {e}")
            unread_count = 0
    
    # Build created_from_message info (existing logic)
    created_from_message = None
    if thread.thread_type == "sub" and thread.created_from_message:
        created_from_message = {
            "id": thread.created_from_message.id,
            "content": thread.created_from_message.content[:100] + "..." if len(thread.created_from_message.content) > 100 else thread.created_from_message.content,
            "username": get_user_forum_display_name(thread.created_from_message.user, db),
            "created_at": thread.created_from_message.created_at.isoformat()
        }
    
    # 🆕 NEW: Get tier info
    tier_info = thread.get_tier_info(db)
    
    return ThreadHierarchyResponse(
        id=thread.id,
        title=thread.title,
        user_id=thread.user_id,
        username=get_user_forum_display_name(thread.user, db), 
        user_role=get_user_role_display(thread.user),
        user_badge_color=get_user_badge_color(thread.user),
        message_count=thread.message_count,
        view_count=thread.view_count,
        follower_count=thread.follower_count or 0,
        is_pinned=thread.is_pinned,
        is_locked=thread.is_locked,
        min_tier_cents=thread.min_tier_cents,
        last_message_at=thread.last_message_at.isoformat() if thread.last_message_at else datetime.now(timezone.utc).isoformat(),
        created_at=thread.created_at.isoformat() if thread.created_at else datetime.now(timezone.utc).isoformat(),
        can_access=True,
        can_delete=thread.can_delete(current_user),
        can_manage=thread.can_manage(current_user),
        is_following=is_following,
        thread_type=thread.thread_type,
        parent_message_id=thread.parent_message_id,
        created_from_message=created_from_message,
        unread_count=unread_count,
        tier_info=tier_info  # 🆕 NEW: Include tier info
    )

@forum_router.get("/threads/{thread_id}/messages", response_model=List[MessageHierarchyResponse])
async def get_thread_messages(
    thread_id: int,
    before_id: Optional[int] = Query(None, description="Load messages before this ID (for infinite scroll)"),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Get messages for a thread with infinite scroll support and auto-mark notifications as read"""
    
    print(f"🔍 API: Getting messages for thread {thread_id}")
    print(f"🔍 API: before_id={before_id}, limit={limit}")
    
    thread = db.query(ForumThread).filter(ForumThread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    
    if not thread.can_access(current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Get total message count for debugging
    total_messages = db.query(func.count(ForumMessage.id)).filter(ForumMessage.thread_id == thread_id).scalar()
    print(f"🔍 API: Thread has {total_messages} total messages")
    
    # NEW: Auto-mark forum notifications as read when visiting thread (initial load only)
    marked_count = 0
    if not before_id:  # Only on initial load, not pagination
        print(f"🔔 API: Initial load - marking forum notifications as read for thread {thread_id}")
        try:
            # Mark all unread forum notifications for this thread as read
            mark_read_result = db.execute(
                text("""
                UPDATE notifications 
                SET is_read = true, read_at = :read_at 
                WHERE user_id = :user_id 
                AND is_read = false
                AND (
                    title LIKE '[Forum]%' 
                    OR notification_data::text LIKE '%"source": "forum"%'
                )
                AND notification_data::text LIKE :thread_filter
                """),
                {
                    "user_id": current_user.id,
                    "thread_filter": f'%"thread_id": {thread_id}%',
                    "read_at": datetime.now(timezone.utc)
                }
            )
            
            # Get number of notifications marked as read
            marked_count = mark_read_result.rowcount if hasattr(mark_read_result, "rowcount") else 0
            print(f"✅ API: Marked {marked_count} forum notifications as read for thread {thread_id}")
            
            # Commit the notification updates
            db.commit()
            
            # 🚀 NEW: Send live update to user's other connections about unread count change
            if marked_count > 0:
                # Get updated total unread count for all forum notifications
                total_unread = db.execute(
                    text("""
                    SELECT COUNT(*) as count
                    FROM notifications
                    WHERE user_id = :user_id 
                    AND is_read = false
                    AND (
                        title LIKE '[Forum]%' 
                        OR notification_data::text LIKE '%"source": "forum"%'
                    )
                    """),
                    {"user_id": current_user.id}
                ).scalar() or 0
                
                # Send live update to user about unread count changes
                unread_update_data = {
                    "type": "unread_count_updated",
                    "thread_id": thread_id,
                    "thread_unread_count": 0,  # This thread now has 0 unread
                    "total_forum_unread": total_unread,
                    "marked_read_count": marked_count
                }
                # New WebSocketManager (Redis pub/sub)
                await forum_global_manager.send_to_user(str(current_user.id), unread_update_data)
                # Legacy manager (backwards compatibility)
                await manager.send_to_user(current_user.id, unread_update_data)
                print(f"📡 Sent live unread count update to user {current_user.id}")
            
        except Exception as e:
            logger.error(f"Error marking thread notifications as read: {e}")
            # Don't fail the whole request if notification marking fails
            db.rollback()
    
    # Increment view count (only for initial load, not pagination)
    if not before_id:
        thread.view_count += 1
        try:
            db.commit()
            print(f"🔍 API: Initial load - getting {limit} most recent messages for natural display")
        except Exception as e:
            logger.error(f"Error updating view count: {e}")
            db.rollback()
    else:
        print(f"🔍 API: Pagination load - getting {limit} messages before ID {before_id}")
    
    # Build query for messages
    query = db.query(ForumMessage).filter(ForumMessage.thread_id == thread_id)
    
    if before_id:
        # Pagination: Load messages before this ID (infinite scroll up)
        query = query.filter(ForumMessage.id < before_id)
    
    # CORE LOGIC: Get newest messages first, then reverse to chronological
    # This ensures newest messages end up at the bottom and are immediately visible
    messages = query.order_by(ForumMessage.created_at.desc()).limit(limit).all()
    
    print(f"🔍 API: Query returned {len(messages)} messages")
    
    # Reverse to chronological order for natural display (oldest→newest)
    # Result: newest message at end of array = bottom of screen = immediately visible!
    messages.reverse()
    
    # DEBUG: Message range info
    if messages:
        print(f"🔍 API: Message range - ID {messages[0].id} to ID {messages[-1].id}")
        print(f"🔍 API: Time range - {messages[0].created_at} to {messages[-1].created_at}")
        print(f"✅ API: Newest message (ID {messages[-1].id}) will appear at bottom")
    else:
        print("📭 API: No messages found in thread")
    
    # Build response
    result = []
    for message in messages:
        # ✅ FIX: Properly unpack the tuple from parse_mentions
        user_mentions, has_everyone, has_creator, has_team = parse_mentions(message.content)
        
        content_html = format_message_html(message.content, db)
        reply_to_message = build_reply_data(message, db)
        
        # GET LIKE INFO
        like_count, user_has_liked = get_message_like_info(message.id, current_user.id, db)
        
        # ✅ FIX: Create final mentions list including @everyone if present
        final_mentions = user_mentions.copy()
        if has_everyone:
            final_mentions.append("everyone")
        
        result.append(MessageHierarchyResponse(
            id=message.id,
            content=message.content,
            content_html=content_html,
            user_id=message.user_id,
            username=get_user_forum_display_name(message.user, db), 
            user_role=get_user_role_display(message.user),
            user_badge_color=get_user_badge_color(message.user),
            is_edited=message.is_edited,
            created_at=message.created_at.isoformat() if message.created_at else datetime.now(timezone.utc).isoformat(),
            mentions=final_mentions,  # ✅ FIX: Pass list of strings, not tuple
            spawned_thread_count=message.spawned_thread_count or 0,
            can_create_thread=True,
            reply_to_id=message.reply_to_id,
            reply_to_message=reply_to_message,
            reply_count=message.reply_count or 0,
            # LIKE INFO
            like_count=like_count,
            user_has_liked=user_has_liked
        ))
    
    return result


@forum_router.get("/threads/{thread_id}/message-count")
async def get_thread_message_count(
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Get total message count for a thread"""
    
    thread = db.query(ForumThread).filter(ForumThread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    
    if not thread.can_access(current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    
    total_count = db.query(func.count(ForumMessage.id)).filter(
        ForumMessage.thread_id == thread_id
    ).scalar()
    
    return {
        "thread_id": thread_id,
        "total_messages": total_count,
        "thread_message_count": thread.message_count  # From thread table
    }
@forum_router.post("/threads", response_model=ThreadResponse)
async def create_thread(
    request: CreateThreadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Create a new thread with database-driven tier restrictions"""
    
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Only creators can create threads")
    
    # Get current timestamp
    now = datetime.now(timezone.utc)
    
    # Create thread
    thread = ForumThread(
        title=request.title,
        user_id=current_user.id,
        thread_type="main",
        created_at=now,
        last_message_at=now,
        follower_count=1  # Creator auto-follows
    )
    
    # Set tier restriction if provided
    if request.min_tier_id:
        thread.set_tier_restriction(request.min_tier_id, db)
    # else: thread defaults to free access (min_tier_cents = 0)
    
    db.add(thread)
    db.flush()  # Get thread ID
    
    # Auto-follow the creator
    auto_follower = ForumThreadFollower(
        thread_id=thread.id,
        user_id=current_user.id,
        notify_on_new_message=True,
        notify_on_mention=True,
        notify_on_reply=True,
        auto_followed=True,
        is_active=True,
        created_at=now
    )
    db.add(auto_follower)
    
    # Create first message
    first_message = ForumMessage(
        thread_id=thread.id,
        user_id=current_user.id,
        content=request.content,
        created_at=now
    )
    
    db.add(first_message)
    db.flush()  # Get message ID
    
    # ✅ FIX: Handle mentions in first message properly
    user_mentions, has_everyone, has_creator, has_team = parse_mentions(request.content)
    if user_mentions or has_everyone or has_creator or has_team:
         await create_mentions(message.id, user_mentions, has_everyone, has_creator, has_team, db)
    
    # Update thread stats
    thread.message_count = 1
    thread.last_message_user_id = current_user.id
    
    db.commit()
    
    # Build thread response with tier info
    tier_info = thread.get_tier_info(db)
    
    thread_response = ThreadResponse(
        id=thread.id,
        title=thread.title,
        user_id=thread.user_id,
        username=get_user_forum_display_name(thread.user, db), 
        user_role=get_user_role_display(current_user),
        user_badge_color=get_user_badge_color(current_user),
        message_count=1,
        view_count=0,
        is_pinned=thread.is_pinned,
        is_locked=thread.is_locked,
        min_tier_cents=thread.min_tier_cents,
        last_message_at=now.isoformat(),
        created_at=now.isoformat(),
        can_access=True,
        is_following=True,
        follower_count=1,
        thread_type="main",
        unread_count=0,
        tier_info=tier_info
    )
    print(f"📦 DEBUG: Thread response object built successfully")
    
    # 🚀 NEW: Broadcast new thread to ALL users who can access it
    print(f"📡 DEBUG: Starting broadcast for new thread {thread.id}")
    
    # Check what connections we have
    print(f"🔍 DEBUG: Connection manager state:")
    print(f"   - user_connections keys: {list(manager.user_connections.keys())}")
    print(f"   - total user_connections: {len(manager.user_connections)}")
    print(f"   - connection_users count: {len(manager.connection_users)}")
    
    # Get all connected users and send them the new thread if they can access it
    all_connected_users = list(manager.user_connections.keys())
    print(f"📡 DEBUG: Found {len(all_connected_users)} connected users: {all_connected_users}")
    
    if not all_connected_users:
        print(f"⚠️ DEBUG: No connected users found - nobody to broadcast to")
    
    broadcast_count = 0
    
    for user_id in all_connected_users:
        print(f"🔍 DEBUG: Processing user {user_id} for broadcast...")
        
        try:
            # Get user and check if they can access this thread
            user = db.query(User).filter(User.id == user_id).first()
            print(f"   - User {user_id} found in DB: {user is not None}")
            
            if not user:
                print(f"   - ❌ User {user_id} not found in database, skipping")
                continue
                
            print(f"   - User {user_id} details: {user.username}, is_creator: {user.is_creator}")
            
            # Check thread access
            can_access = thread.can_access(user)
            print(f"   - User {user_id} can_access thread: {can_access}")
            
            if can_access:
                print(f"   - ✅ User {user_id} can access thread, sending broadcast...")
                
                # Prepare broadcast data
                broadcast_data = {
                    "type": "new_thread_created",
                    "thread": thread_response.dict(),
                    "creator": {
                        "id": current_user.id,
                        "username": get_user_forum_display_name(current_user, db),
                        "role": get_user_role_display(current_user)
                    }
                }

                # Send new thread notification to this user
                # New WebSocketManager (Redis pub/sub)
                await forum_global_manager.send_to_user(str(user_id), broadcast_data)
                # Legacy manager (backwards compatibility)
                send_result = await manager.send_to_user(user_id, broadcast_data)
                print(f"   - Send result for user {user_id}: {send_result}")
                
                if send_result:
                    broadcast_count += 1
                    print(f"   - ✅ Successfully sent to user {user_id}")
                else:
                    print(f"   - ❌ Failed to send to user {user_id}")
            else:
                print(f"   - ❌ User {user_id} cannot access this thread, skipping")
                
        except Exception as e:
            print(f"   - 🚨 Error broadcasting new thread to user {user_id}: {e}")
            logger.error(f"Error broadcasting new thread to user {user_id}: {e}")
    
    print(f"✅ DEBUG: Broadcast completed - reached {broadcast_count} out of {len(all_connected_users)} connected users")
    
    if broadcast_count == 0:
        print(f"⚠️ DEBUG: Zero users received the broadcast! Possible issues:")
        print(f"   - No users connected to global WebSocket")
        print(f"   - Thread access restrictions too strict") 
        print(f"   - WebSocket connections not properly tracked")
        print(f"   - send_to_user method failing")
    
    return thread_response
# UPDATED: Enhanced create message endpoint with existing notification system
@forum_router.post("/threads/{thread_id}/messages", response_model=MessageResponse)
async def create_message(
    thread_id: int,
    request: CreateMessageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Create a message in a thread - ENHANCED with special mentions + FIXED async notifications"""
    
    thread = db.query(ForumThread).filter(ForumThread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    
    if not thread.can_access(current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    
    if thread.is_locked and not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Thread is locked")
    
    # Parse mentions using the enhanced function
    user_mentions, has_everyone, has_creator, has_team = parse_all_mentions(request.content)
    
    # FIXED: Check permissions for special mentions
    if has_everyone and not can_use_everyone_mention(current_user):
        raise HTTPException(
            status_code=403, 
            detail="Only creators and team members can use @everyone mentions"
        )
    
    # NEW: @creator can be used by anyone (no restriction)
    # has_creator is allowed for all users
    
    # FIXED: @team uses same permission as @everyone
    if has_team and not can_use_team_mention(current_user):
        raise HTTPException(
            status_code=403, 
            detail="Only creators and team members can use @team mentions"
        )
    
    # Validate reply_to_id if provided
    reply_to_message = None
    if request.reply_to_id:
        reply_to_message = db.query(ForumMessage).filter(
            ForumMessage.id == request.reply_to_id,
            ForumMessage.thread_id == thread_id
        ).first()
        if not reply_to_message:
            raise HTTPException(status_code=404, detail="Reply target message not found")
    
    # Get current timestamp
    now = datetime.now(timezone.utc)
    
    # Create message
    message = ForumMessage(
        thread_id=thread_id,
        user_id=current_user.id,
        content=request.content,
        reply_to_id=request.reply_to_id,
        created_at=now,
        has_everyone_mention=has_everyone  # Track @everyone usage
    )
    
    db.add(message)
    db.flush()  # Get message ID
    
    # Update reply count if this is a reply
    if reply_to_message:
        reply_to_message.reply_count = (reply_to_message.reply_count or 0) + 1
    
    # FIXED: Handle mentions (both regular and special mentions)
    if user_mentions or has_everyone or has_creator or has_team:
        await create_mentions(message.id, user_mentions, has_everyone, has_creator, has_team, db)
    
    # Auto-follow logic (existing code continues...)
    existing_follower = db.query(ForumThreadFollower).filter(
        ForumThreadFollower.thread_id == thread_id,
        ForumThreadFollower.user_id == current_user.id
    ).first()
    
    if not existing_follower:
        auto_follower = ForumThreadFollower(
            thread_id=thread_id,
            user_id=current_user.id,
            notify_on_new_message=False,
            notify_on_mention=True,
            notify_on_reply=True,
            auto_followed=True,
            is_active=True,
            created_at=now
        )
        db.add(auto_follower)
        thread.follower_count = (thread.follower_count or 0) + 1
    
    # Update thread stats
    thread.message_count += 1
    thread.last_message_at = now
    thread.last_message_user_id = current_user.id
    
    db.commit()
    
    # Build response (rest of function continues as before...)
    try:
        reply_to_data = None
        if reply_to_message:
            reply_to_data = {
                "id": reply_to_message.id,
                "content": reply_to_message.content[:100] + "..." if len(reply_to_message.content) > 100 else reply_to_message.content,
                "username": get_user_forum_display_name(reply_to_message.user, db),
                "user_role": get_user_role_display(reply_to_message.user),
                "user_badge_color": get_user_badge_color(reply_to_message.user),
                "created_at": reply_to_message.created_at.isoformat()
            }
        
        content_html = format_message_html(request.content, db)
        username = get_user_forum_display_name(current_user, db) or current_user.username
        user_role = get_user_role_display(current_user) or "Member"
        user_badge_color = get_user_badge_color(current_user) or "#6b7280"
        
        # Include all special mentions in mentions array
        all_mentions = user_mentions.copy()
        if has_everyone:
            all_mentions.append("everyone")
        if has_creator:
            all_mentions.append("creator")
        if has_team:
            all_mentions.append("team")
        
        message_response = MessageResponse(
            id=message.id,
            content=message.content,
            content_html=content_html or message.content,
            user_id=message.user_id,
            username=username,
            user_role=user_role,
            user_badge_color=user_badge_color,
            is_edited=False,
            created_at=now.isoformat(),
            mentions=all_mentions,  # Include all special mentions
            reply_to_id=request.reply_to_id,
            reply_to_message=reply_to_data,
            reply_count=0,
            like_count=0,
            user_has_liked=False
        )
        
    except Exception as e:
        logger.error(f"Error building message response: {str(e)}")
        # Fallback response
        message_response = MessageResponse(
            id=message.id,
            content=message.content,
            content_html=message.content,
            user_id=message.user_id,
            username=current_user.username,
            user_role="Member",
            user_badge_color="#6b7280",
            is_edited=False,
            created_at=now.isoformat(),
            mentions=user_mentions + (["everyone"] if has_everyone else []) + (["creator"] if has_creator else []) + (["team"] if has_team else []),
            reply_to_id=request.reply_to_id,
            reply_to_message=None,
            reply_count=0,
            like_count=0,
            user_has_liked=False
        )
    
    # Broadcast to live connections
    try:
        message_data = {
            "type": "new_message",
            "thread_id": thread_id,
            "message": message_response.dict()
        }
        # New WebSocketManager (Redis pub/sub)
        await forum_thread_manager.broadcast(message_data)
        # Legacy manager (backwards compatibility)
        await manager.broadcast_to_thread(thread_id, message_data)
    except Exception as e:
        logger.error(f"Error broadcasting message: {str(e)}")
    
    # 🚀 SIMPLE FIX: Send notifications in background (non-blocking)
    asyncio.create_task(
        notify_thread_followers_via_existing_system(db, thread, message, current_user)
    )
    
    return message_response

def can_use_team_mention(user: User) -> bool:
    """Only creators and team members can use @team mentions"""
    return user.is_creator or user.is_team

def can_use_creator_mention(user: User) -> bool:
    """Anyone can mention @creator"""
    return True

def build_message_response_with_likes(message: ForumMessage, current_user_id: int, db: Session, is_new: bool = False) -> dict:
    """Helper function to build complete message response with like info"""
    
    # ✅ FIX: Properly unpack parse_mentions tuple
    user_mentions, has_everyone, has_creator, has_team = parse_mentions(message.content)
    
    content_html = format_message_html(message.content, db)
    reply_to_message = build_reply_data(message, db)
    like_count, user_has_liked = get_message_like_info(message.id, current_user_id, db)
    
    # ✅ FIX: Create final mentions list
    final_mentions = user_mentions.copy()
    if has_everyone:
        final_mentions.append("everyone")
    
    return {
        "id": message.id,
        "content": message.content,
        "content_html": content_html,
        "user_id": message.user_id,
        "username": get_user_forum_display_name(message.user, db),
        "user_role": get_user_role_display(message.user),
        "user_badge_color": get_user_badge_color(message.user),
        "is_edited": message.is_edited,
        "created_at": message.created_at.isoformat(),
        "mentions": final_mentions,  # ✅ FIX: Pass list, not tuple
        "spawned_thread_count": message.spawned_thread_count or 0,
        "reply_to_id": message.reply_to_id,
        "reply_to_message": reply_to_message,
        "reply_count": message.reply_count or 0,
        "like_count": like_count,
        "user_has_liked": user_has_liked
    }
    
@forum_router.get("/threads/{thread_id}/recent", response_model=List[MessageResponse])
async def get_recent_messages(
    thread_id: int,
    since_id: int = Query(0, description="Get messages after this ID"),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Get recent messages for polling-based updates"""
    
    thread = db.query(ForumThread).filter(ForumThread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    
    if not thread.can_access(current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Get messages after since_id
    messages = db.query(ForumMessage).filter(
        ForumMessage.thread_id == thread_id,
        ForumMessage.id > since_id
    ).order_by(ForumMessage.created_at.asc()).limit(limit).all()
    
    result = []
    for message in messages:
        # ✅ FIX: Properly unpack parse_mentions tuple
        user_mentions, has_everyone, has_creator, has_team = parse_mentions(message.content)
        
        content_html = format_message_html(message.content, db)
        reply_to_message = build_reply_data(message, db)
        
        # ✅ FIX: Create final mentions list
        final_mentions = user_mentions.copy()
        if has_everyone:
            final_mentions.append("everyone")
        
        result.append(MessageResponse(
            id=message.id,
            content=message.content,
            content_html=content_html,
            user_id=message.user_id,
            username=get_user_forum_display_name(message.user, db), 
            user_role=get_user_role_display(message.user),
            user_badge_color=get_user_badge_color(message.user),
            is_edited=message.is_edited,
            created_at=message.created_at.isoformat() if message.created_at else datetime.now(timezone.utc).isoformat(),
            mentions=final_mentions,  # ✅ FIX: Pass list, not tuple
            reply_to_id=message.reply_to_id,
            reply_to_message=reply_to_message,
            reply_count=message.reply_count or 0
        ))
    
    return result

@forum_router.get("/users/search")
async def search_users(
    q: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=20),
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Search users for @mention autocomplete - includes forum aliases and special mentions"""
    
    results = []
    query_lower = q.lower()
    
    # 🆕 NEW: Add synthetic @everyone option
    if (len(q) >= 2 and 
        "everyone".startswith(query_lower) and 
        can_use_everyone_mention(current_user)):
        
        results.append(UserSearchResponse(
            username="everyone",           
            display_name="everyone",       
            role="Everyone",               
            badge_color="#ef4444"          # Red for @everyone
        ))
    
    # 🆕 NEW: Add synthetic @creator option
    if (len(q) >= 2 and 
        "creator".startswith(query_lower)):
        
        results.append(UserSearchResponse(
            username="creator",           
            display_name="creator",       
            role="Creator",               
            badge_color="#f59e0b"         # Gold for @creator
        ))
    
    # 🆕 NEW: Add synthetic @team option  
    if (len(q) >= 2 and 
        "team".startswith(query_lower) and 
        can_use_everyone_mention(current_user)):  # Same permission as @everyone
        
        results.append(UserSearchResponse(
            username="team",           
            display_name="team",       
            role="Team",               
            badge_color="#3b82f6"      # Blue for @team
        ))
    
    # Search by username first
    username_matches = db.query(User).filter(
        User.username.ilike(f"%{q}%")
    ).limit(limit).all()
    
    # Search by alias - get users who have aliases matching the query
    alias_user_ids = db.query(ForumUserSettings.user_id).filter(
        and_(
            ForumUserSettings.use_alias == True,
            ForumUserSettings.display_alias.ilike(f"%{q}%"),
            ForumUserSettings.display_alias.isnot(None)
        )
    ).subquery()
    
    alias_matches = db.query(User).filter(
        User.id.in_(alias_user_ids)
    ).all()
    
    # Combine and deduplicate users
    all_users = username_matches + alias_matches
    seen_user_ids = set()
    unique_users = []
    
    for user in all_users:
        if user.id not in seen_user_ids:
            unique_users.append(user)
            seen_user_ids.add(user.id)
    
    # Limit regular user results (leaving room for special mentions)
    special_mentions_count = len(results)
    max_user_results = limit - special_mentions_count
    unique_users = unique_users[:max_user_results]
    
    # Add regular users to results
    for user in unique_users:
        # Get forum settings for this user
        forum_settings = db.query(ForumUserSettings).filter(
            ForumUserSettings.user_id == user.id
        ).first()
        
        # Determine display name
        if forum_settings and forum_settings.use_alias and forum_settings.display_alias:
            mention_name = forum_settings.display_alias
        else:
            mention_name = user.username
        
        results.append(UserSearchResponse(
            username=mention_name,      
            display_name=mention_name,  
            role=get_user_role_display(user),
            badge_color=get_user_badge_color(user)
        ))
    
    return results

@forum_router.patch("/threads/{thread_id}")
async def update_thread(
    thread_id: int,
    request: UpdateThreadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Update thread settings - creator only"""
    
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Creator access required")
    
    thread = db.query(ForumThread).filter(ForumThread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    
    # Update fields
    if request.min_tier_id is not None:
        thread.set_tier_restriction(request.min_tier_id, db)
    
    if request.is_pinned is not None:
        thread.is_pinned = request.is_pinned
    if request.is_locked is not None:
        thread.is_locked = request.is_locked
    
    thread.updated_at = datetime.now(timezone.utc)
    db.commit()
    
    # Broadcast thread update to live connections
    update_data = {
        "type": "thread_updated",
        "thread_id": thread_id,
        "updates": {
            "is_pinned": thread.is_pinned,
            "is_locked": thread.is_locked,
            "min_tier_cents": thread.min_tier_cents,
            "tier_info": thread.get_tier_info(db)  # 🆕 NEW: Include tier info
        }
    }
    # New WebSocketManager (Redis pub/sub)
    await forum_thread_manager.broadcast(update_data)
    # Legacy manager (backwards compatibility)
    await manager.broadcast_to_thread(thread_id, update_data)
    
    return {"success": True, "message": "Thread updated"}
@forum_router.get("/settings/everyone-mentions")
async def get_everyone_mention_settings(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get user's @everyone mention preferences"""
    settings = db.query(ForumUserSettings).filter(ForumUserSettings.user_id == current_user.id).first()
    
    allow_everyone_mentions = True  # Default to true
    if settings:
        allow_everyone_mentions = settings.allow_everyone_mentions
    
    return {
        "allow_everyone_mentions": allow_everyone_mentions,
        "can_use_everyone": can_use_everyone_mention(current_user)
    }
@forum_router.patch("/settings/everyone-mentions")
async def update_everyone_mention_settings(
    allow_everyone_mentions: bool = Body(..., embed=True),
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Update user's @everyone mention preferences"""
    settings = db.query(ForumUserSettings).filter(ForumUserSettings.user_id == current_user.id).first()
    
    if not settings:
        settings = ForumUserSettings(user_id=current_user.id)
        db.add(settings)
    
    settings.allow_everyone_mentions = allow_everyone_mentions
    settings.updated_at = datetime.now(timezone.utc)
    
    db.commit()
    
    return {
        "success": True,
        "allow_everyone_mentions": allow_everyone_mentions,
        "message": f"@everyone mentions {'enabled' if allow_everyone_mentions else 'disabled'}"
    }

# Enhanced follow/unfollow endpoints
@forum_router.post("/threads/{thread_id}/follow")
async def follow_thread(
    thread_id: int,
    request: FollowThreadRequest = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Follow a thread with notification preferences"""
    
    thread = db.query(ForumThread).filter(ForumThread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    
    if not thread.can_access(current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Check if already following
    existing = db.query(ForumThreadFollower).filter(
        ForumThreadFollower.thread_id == thread_id,
        ForumThreadFollower.user_id == current_user.id
    ).first()
    
    if existing:
        if existing.is_active:
            return {
                "success": True, 
                "message": "Already following", 
                "is_following": True,
                "requires_warning": False
            }
        else:
            # Reactivate existing follow
            existing.is_active = True
            existing.muted_until = None
            if request:
                existing.notify_on_new_message = request.notify_on_new_message
                existing.notify_on_mention = request.notify_on_mention
                existing.notify_on_reply = request.notify_on_reply
    else:
        # Create new follow relationship
        follower = ForumThreadFollower(
            thread_id=thread_id,
            user_id=current_user.id,
            notify_on_new_message=request.notify_on_new_message if request else True,
            notify_on_mention=request.notify_on_mention if request else True,
            notify_on_reply=request.notify_on_reply if request else True,
            auto_followed=False,
            is_active=True,
            created_at=datetime.now(timezone.utc)
        )
        db.add(follower)
    
    # Update thread follower count
    thread.follower_count = db.query(ForumThreadFollower).filter(
        ForumThreadFollower.thread_id == thread_id,
        ForumThreadFollower.is_active == True
    ).count()
    
    db.commit()
    
    # Determine if warning is needed (for main threads with high activity)
    requires_warning = (
        thread.thread_type == "main" and 
        thread.message_count > 10 and 
        (request is None or request.notify_on_new_message)
    )
    
    return {
        "success": True, 
        "message": "Thread followed", 
        "is_following": True,
        "follower_count": thread.follower_count,
        "requires_warning": requires_warning,
        "warning_message": f"You will receive notifications for all messages in this active thread. You can adjust notification settings anytime." if requires_warning else None
    }

@forum_router.delete("/threads/{thread_id}/follow")
async def unfollow_thread(
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Unfollow a thread"""
    
    thread = db.query(ForumThread).filter(ForumThread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    
    # Find and deactivate follow relationship
    follower = db.query(ForumThreadFollower).filter(
        ForumThreadFollower.thread_id == thread_id,
        ForumThreadFollower.user_id == current_user.id
    ).first()
    
    if follower:
        follower.is_active = False
        
        # Update thread follower count
        thread.follower_count = db.query(ForumThreadFollower).filter(
            ForumThreadFollower.thread_id == thread_id,
            ForumThreadFollower.is_active == True
        ).count()
        
        db.commit()
        
        return {
            "success": True, 
            "message": "Thread unfollowed", 
            "is_following": False,
            "follower_count": thread.follower_count
        }
    
    return {
        "success": True, 
        "message": "Not following", 
        "is_following": False,
        "follower_count": thread.follower_count or 0
    }

# Thread Hierarchy Endpoints



@forum_router.get("/messages/{message_id}/threads", response_model=List[ThreadHierarchyResponse])
async def get_message_threads(
    message_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Get all threads created from a specific message"""
    
    # Verify message exists and user has access
    message = db.query(ForumMessage).filter(ForumMessage.id == message_id).first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    if not message.thread.can_access(current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Get threads created from this message
    threads = db.query(ForumThread).filter(
        ForumThread.created_from_message_id == message_id
    ).order_by(ForumThread.created_at.desc()).all()
    
    result = []
    for thread in threads:
        if thread.can_access(current_user):
            # Get follower info
            follower = thread.get_follower(current_user)
            is_following = follower is not None and follower.is_active
            
            # Get unread count for this user using existing notification system
            unread_count = 0
            if is_following:
                try:
                    unread_count = db.execute(
                        text("""
                        SELECT COUNT(*) as count
                        FROM notifications
                        WHERE user_id = :user_id 
                        AND is_read = false
                        AND (type::text LIKE 'forum_%' OR (notification_data::text LIKE '%thread_id%'))
                        AND notification_data::text LIKE :thread_filter
                        """),
                        {
                            "user_id": current_user.id,
                            "thread_filter": f'%"thread_id": {thread.id}%'
                        }
                    ).scalar() or 0
                except Exception as e:
                    logger.error(f"Error getting unread count for thread {thread.id}: {e}")
                    unread_count = 0
            
            result.append(ThreadHierarchyResponse(
                id=thread.id,
                title=thread.title,
                user_id=thread.user_id,
                username=get_user_forum_display_name(thread.user, db),  # FIXED - use alias
                user_role=get_user_role_display(thread.user),
                user_badge_color=get_user_badge_color(thread.user),
                message_count=thread.message_count,
                view_count=thread.view_count,
                follower_count=thread.follower_count or 0,
                is_pinned=thread.is_pinned,
                is_locked=thread.is_locked,
                min_tier_cents=thread.min_tier_cents,
                last_message_at=thread.last_message_at.isoformat() if thread.last_message_at else datetime.now(timezone.utc).isoformat(),
                created_at=thread.created_at.isoformat() if thread.created_at else datetime.now(timezone.utc).isoformat(),
                can_access=True,
                can_delete=thread.can_delete(current_user),
                can_manage=thread.can_manage(current_user),
                is_following=is_following,
                thread_type=thread.thread_type,
                parent_message_id=thread.parent_message_id,
                unread_count=unread_count
            ))
    
    return result

@forum_router.get("/messages/{message_id}/replies", response_model=List[MessageHierarchyResponse])
async def get_message_replies(
    message_id: int,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Get all replies to a specific message"""
    
    # Verify the parent message exists and user has access
    parent_message = db.query(ForumMessage).filter(ForumMessage.id == message_id).first()
    if not parent_message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    if not parent_message.thread.can_access(current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Get replies
    offset = (page - 1) * limit
    replies = db.query(ForumMessage).filter(
        ForumMessage.reply_to_id == message_id
    ).order_by(ForumMessage.created_at.asc()).offset(offset).limit(limit).all()
    
    result = []
    for reply in replies:
        # ✅ FIX: Properly unpack parse_mentions tuple
        user_mentions, has_everyone, has_creator, has_team = parse_mentions(reply.content)
        
        content_html = format_message_html(reply.content, db)
        reply_to_message = build_reply_data(reply, db)
        
        # ✅ FIX: Create final mentions list
        final_mentions = user_mentions.copy()
        if has_everyone:
            final_mentions.append("everyone")
        
        result.append(MessageHierarchyResponse(
            id=reply.id,
            content=reply.content,
            content_html=content_html,
            user_id=reply.user_id,
            username=get_user_forum_display_name(reply.user, db),
            user_role=get_user_role_display(reply.user),
            user_badge_color=get_user_badge_color(reply.user),
            is_edited=reply.is_edited,
            created_at=reply.created_at.isoformat(),
            mentions=final_mentions,  # ✅ FIX: Pass list, not tuple
            spawned_thread_count=reply.spawned_thread_count or 0,
            can_create_thread=True,
            reply_to_id=reply.reply_to_id,
            reply_to_message=reply_to_message,
            reply_count=reply.reply_count or 0
        ))
    
    return result

@forum_router.get("/messages/{message_id}/thread-count")
async def get_message_thread_count(
    message_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Get count of threads spawned from a message"""
    
    message = db.query(ForumMessage).filter(ForumMessage.id == message_id).first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    if not message.thread.can_access(current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    
    return {"count": message.spawned_thread_count or 0}

@forum_router.delete("/threads/{thread_id}/delete-hierarchy")
async def delete_thread_hierarchy(
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Delete a thread with proper hierarchy permissions"""
    
    thread = db.query(ForumThread).filter(ForumThread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    
    if not thread.can_delete(current_user):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    # Delete the thread (cascade will handle messages, followers, etc.)
    db.delete(thread)
    db.commit()
    
    return {"success": True, "message": "Thread deleted"}

@forum_router.get("/threads/{thread_id}/hierarchy")
async def get_thread_hierarchy(
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Get hierarchy information for a thread"""
    
    thread = db.query(ForumThread).filter(ForumThread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    
    if not thread.can_access(current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Get follower info
    follower = thread.get_follower(current_user)
    is_following = follower is not None and follower.is_active
    
    hierarchy_info = {
        "thread_type": thread.thread_type,
        "can_delete": thread.can_delete(current_user),
        "can_manage": thread.can_manage(current_user),
        "is_following": is_following,
        "follower_count": thread.follower_count or 0,
        "parent_message": None,
        "created_from_message": None
    }
    
    # Add parent message info for sub-threads - FIXED: Use alias
    if thread.thread_type == "sub" and thread.created_from_message:
        hierarchy_info["created_from_message"] = {
            "id": thread.created_from_message.id,
            "content": thread.created_from_message.content[:100] + "..." if len(thread.created_from_message.content) > 100 else thread.created_from_message.content,
            "username": get_user_forum_display_name(thread.created_from_message.user, db),  # FIXED - use alias
            "created_at": thread.created_from_message.created_at.isoformat()
        }
    
    return hierarchy_info
# Additional Advanced Endpoints

@forum_router.get("/search")
async def search_forum(
    q: str = Query(..., min_length=2, description="Search query"),
    thread_type: str = Query("all", description="Filter by thread type: 'main', 'sub', or 'all'"),
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Search forum threads and messages"""
    
    results = {
        "threads": [],
        "messages": []
    }
    
    # Search threads
    thread_query = db.query(ForumThread).filter(
        ForumThread.title.ilike(f"%{q}%")
    )
    
    if thread_type == "main":
        thread_query = thread_query.filter(ForumThread.thread_type == "main")
    elif thread_type == "sub":
        thread_query = thread_query.filter(ForumThread.thread_type == "sub")
    
    threads = thread_query.order_by(desc(ForumThread.last_message_at)).limit(limit // 2).all()
    
    for thread in threads:
        if thread.can_access(current_user):
            results["threads"].append({
                "id": thread.id,
                "title": thread.title,
                "username": get_user_forum_display_name(thread.user, db),  # FIXED - use alias
                "thread_type": thread.thread_type,
                "message_count": thread.message_count,
                "created_at": thread.created_at.isoformat()
            })
    
    # Search messages
    messages = db.query(ForumMessage).join(ForumThread).filter(
        ForumMessage.content.ilike(f"%{q}%")
    ).order_by(desc(ForumMessage.created_at)).limit(limit // 2).all()
    
    for message in messages:
        if message.thread.can_access(current_user):
            # Highlight search term in content preview
            content_preview = message.content[:200]
            if len(message.content) > 200:
                content_preview += "..."
            
            results["messages"].append({
                "id": message.id,
                "content_preview": content_preview,
                "username": get_user_forum_display_name(message.user, db),  # FIXED - use alias
                "thread_id": message.thread_id,
                "thread_title": message.thread.title,
                "created_at": message.created_at.isoformat()
            })
    
    return results

@forum_router.get("/stats")
async def get_forum_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Get forum statistics"""
    
    # Total threads user can access
    total_threads = 0
    main_threads = 0
    sub_threads = 0
    
    all_threads = db.query(ForumThread).all()
    accessible_thread_ids = []
    
    for thread in all_threads:
        if thread.can_access(current_user):
            total_threads += 1
            accessible_thread_ids.append(thread.id)
            if thread.thread_type == "main":
                main_threads += 1
            else:
                sub_threads += 1
    
    # Total messages in accessible threads
    total_messages = 0
    if accessible_thread_ids:
        total_messages = db.query(func.sum(ForumThread.message_count)).filter(
            ForumThread.id.in_(accessible_thread_ids)
        ).scalar() or 0
    
    # Most active thread
    most_active = None
    if accessible_thread_ids:
        most_active_thread = db.query(ForumThread).filter(
            ForumThread.id.in_(accessible_thread_ids)
        ).order_by(desc(ForumThread.message_count)).first()
        
        if most_active_thread:
            most_active = {
                "id": most_active_thread.id,
                "title": most_active_thread.title,
                "message_count": most_active_thread.message_count
            }
    
    # Recent activity
    recent_messages = 0
    if accessible_thread_ids:
        recent_messages = db.query(func.count(ForumMessage.id)).filter(
            ForumMessage.thread_id.in_(accessible_thread_ids),
            ForumMessage.created_at >= func.now() - func.interval('24 hours')
        ).scalar() or 0
    
    return {
        "total_threads": total_threads,
        "main_threads": main_threads,
        "sub_threads": sub_threads,
        "total_messages": total_messages,
        "recent_messages_24h": recent_messages,
        "most_active_thread": most_active
    }

@forum_router.get("/user/{user_id}/activity")
async def get_user_forum_activity(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Get a user's forum activity summary"""
    
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Get accessible threads created by user
    created_threads = db.query(ForumThread).filter(
        ForumThread.user_id == user_id
    ).all()
    
    accessible_created_threads = [
        {
            "id": thread.id,
            "title": thread.title,
            "thread_type": thread.thread_type,
            "message_count": thread.message_count,
            "created_at": thread.created_at.isoformat()
        }
        for thread in created_threads 
        if thread.can_access(current_user)
    ]
    
    # Get recent messages in accessible threads
    recent_messages = db.query(ForumMessage).join(ForumThread).filter(
        ForumMessage.user_id == user_id
    ).order_by(desc(ForumMessage.created_at)).limit(10).all()
    
    accessible_recent_messages = []
    for message in recent_messages:
        if message.thread.can_access(current_user):
            accessible_recent_messages.append({
                "id": message.id,
                "content_preview": message.content[:100] + ("..." if len(message.content) > 100 else ""),
                "thread_id": message.thread_id,
                "thread_title": message.thread.title,
                "created_at": message.created_at.isoformat(),
                "reply_count": message.reply_count or 0
            })
    
    # Count total messages in accessible threads
    total_messages = 0
    for message in db.query(ForumMessage).filter(ForumMessage.user_id == user_id).all():
        if message.thread.can_access(current_user):
            total_messages += 1
    
    return {
        "user": {
            "id": target_user.id,
            "username": get_user_forum_display_name(target_user, db),  # FIXED - use alias
            "role": get_user_role_display(target_user),
            "badge_color": get_user_badge_color(target_user)
        },
        "created_threads": accessible_created_threads,
        "recent_messages": accessible_recent_messages,
        "total_messages": total_messages,
        "threads_created": len(accessible_created_threads)
    }

@forum_router.patch("/messages/{message_id}")
async def edit_message(
    message_id: int,
    content: str = Body(..., embed=True),
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Edit a message (users can only edit their own messages)"""
    
    message = db.query(ForumMessage).filter(ForumMessage.id == message_id).first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    # Only allow users to edit their own messages
    if message.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only edit your own messages")
    
    if not message.thread.can_access(current_user):
        raise HTTPException(status_code=403, detail="Access denied to thread")
    
    # Update message
    message.content = content.strip()
    message.is_edited = True
    message.edited_at = datetime.now(timezone.utc)
    
    # Update mentions
    db.query(ForumMention).filter(ForumMention.message_id == message_id).delete()
    
    # ✅ FIX: Handle mentions properly
    user_mentions, has_everyone, has_creator, has_team = parse_mentions(content)
    if user_mentions or has_everyone or has_creator or has_team:
        await create_mentions(message.id, user_mentions, has_everyone, has_creator, has_team, db)
    
    db.commit()
    
    # ✅ FIX: Create final mentions list for broadcast
    final_mentions = user_mentions.copy()
    if has_everyone:
        final_mentions.append("everyone")
    
    # Broadcast edit to live connections
    content_html = format_message_html(content, db)
    edit_data = {
        "type": "message_edited",
        "thread_id": message.thread_id,
        "message": {
            "id": message.id,
            "content": message.content,
            "content_html": content_html,
            "is_edited": True,
            "edited_at": message.edited_at.isoformat(),
            "mentions": final_mentions  # ✅ FIX: Pass list, not tuple
        }
    }
    # New WebSocketManager (Redis pub/sub)
    await forum_thread_manager.broadcast(edit_data)
    # Legacy manager (backwards compatibility)
    await manager.broadcast_to_thread(message.thread_id, edit_data)
    
    return {"success": True, "message": "Message updated"}

@forum_router.delete("/messages/{message_id}")
async def delete_message(
    message_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Delete a message and all its replies (using database cascading)"""
    
    message = db.query(ForumMessage).filter(ForumMessage.id == message_id).first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    # Check permissions
    if message.user_id != current_user.id and not (current_user.is_creator or current_user.is_team):
        raise HTTPException(status_code=403, detail="Permission denied")
    
    thread = message.thread
    if not thread.can_access(current_user):
        raise HTTPException(status_code=403, detail="Access denied to thread")
    
    # Can't delete the first message of a thread
    first_message = db.query(ForumMessage).filter(
        ForumMessage.thread_id == thread.id
    ).order_by(ForumMessage.created_at.asc()).first()
    
    if first_message and first_message.id == message_id:
        raise HTTPException(status_code=400, detail="Cannot delete the first message of a thread")
    
    # Get all messages that will be deleted before deletion (for counting and notifications)
    def get_all_reply_ids(parent_id: int) -> List[int]:
        """Recursively get all reply IDs"""
        reply_ids = []
        direct_replies = db.query(ForumMessage).filter(ForumMessage.reply_to_id == parent_id).all()
        
        for reply in direct_replies:
            reply_ids.append(reply.id)
            reply_ids.extend(get_all_reply_ids(reply.id))
        
        return reply_ids
    
    all_delete_ids = [message_id] + get_all_reply_ids(message_id)
    deleted_count = len(all_delete_ids)
    
    # Update parent message reply count if this message is a reply
    if message.reply_to_id:
        parent_message = db.query(ForumMessage).filter(ForumMessage.id == message.reply_to_id).first()
        if parent_message:
            # Count direct children being deleted
            direct_children_count = sum(1 for msg_id in all_delete_ids 
                                      if db.query(ForumMessage).filter(
                                          ForumMessage.id == msg_id,
                                          ForumMessage.reply_to_id == parent_message.id
                                      ).first())
            parent_message.reply_count = max(0, (parent_message.reply_count or 0) - direct_children_count)
    
    # Delete the message (database cascading will handle replies automatically)
    db.delete(message)
    
    # Update thread message count
    thread.message_count = max(0, thread.message_count - deleted_count)
    
    # Update last message info if needed
    if thread.last_message_user_id == message.user_id:
        last_message = db.query(ForumMessage).filter(
            ForumMessage.thread_id == thread.id,
            ForumMessage.id != message_id
        ).order_by(desc(ForumMessage.created_at)).first()
        
        if last_message:
            thread.last_message_at = last_message.created_at
            thread.last_message_user_id = last_message.user_id
    
    db.commit()
    
    # Broadcast deletion to live connections
    delete_data = {
        "type": "messages_deleted",
        "thread_id": thread.id,
        "message_ids": all_delete_ids,
        "deleted_count": deleted_count
    }
    # New WebSocketManager (Redis pub/sub)
    await forum_thread_manager.broadcast(delete_data)
    # Legacy manager (backwards compatibility)
    await manager.broadcast_to_thread(thread.id, delete_data)

    return {
        "success": True, 
        "message": f"Message and {deleted_count - 1} replies deleted" if deleted_count > 1 else "Message deleted",
        "deleted_count": deleted_count,
        "deleted_ids": all_delete_ids
    }

# Enhanced WebSocket endpoint
@forum_router.websocket("/ws/thread/{thread_id}")
async def secure_thread_websocket_endpoint(
    websocket: WebSocket,
    thread_id: int,
    ws_auth: WebSocketSessionAuth = Depends(get_websocket_auth)
):
    """FIXED Thread WebSocket endpoint with proper session management"""

    current_user = None
    session_id = None
    user_info = None
    display_name = None

    # Auth phase - create temporary session
    from database import SessionLocal
    db = SessionLocal()

    try:
        logger.info(f"🧵 Thread WebSocket connection attempt for thread {thread_id}")

        # Accept connection first
        await websocket.accept()
        logger.info(f"🤝 Thread WebSocket connection accepted for thread {thread_id}")

        # Authenticate using session cookies
        current_user = await ws_auth.authenticate_websocket(websocket, db, require_session=True)
        if not current_user:
            logger.warning(f"❌ Thread WebSocket authentication failed for thread {thread_id}")
            return

        session_id = ws_auth.connection_sessions.get(websocket)
        logger.info(f"✅ Thread WebSocket authenticated: user {current_user.id} for thread {thread_id}")

        # Check thread exists and access permissions
        try:
            thread = db.query(ForumThread).filter(ForumThread.id == thread_id).first()
            if not thread:
                logger.warning(f"❌ Thread {thread_id} not found")
                await websocket.close(code=1008, reason="Thread not found")
                return

            if not thread.can_access(current_user):
                logger.warning(f"❌ User {current_user.id} denied access to thread {thread_id}")
                await websocket.close(code=1008, reason="Access denied")
                return

            logger.info(f"✅ Thread {thread_id} access granted for user {current_user.id}")

        except Exception as e:
            logger.error(f"❌ Error checking thread access: {str(e)}")
            await websocket.close(code=1011, reason="Database error")
            return

        # Get forum display name BEFORE closing db
        try:
            display_name = get_user_forum_display_name(current_user, db)
            logger.info(f"✅ Fetched display name: {display_name}")
        except Exception as e:
            logger.error(f"Error fetching display name: {e}")
            display_name = current_user.username  # Fallback

        # Build user info
        try:
            user_info = {
                "user_id": current_user.id,
                "username": current_user.username,
                "display_name": display_name,  # Cached forum name
                "session_id": session_id,
                "thread_id": thread_id
            }
            logger.info(f"✅ User info built for thread WebSocket: {user_info}")

        except Exception as e:
            logger.error(f"❌ Error building user info: {str(e)}")
            await websocket.close(code=1011, reason="User info error")
            return

    finally:
        # ✅ CLOSE DB BEFORE ENTERING LOOP
        db.close()
        logger.info(f"✅ DB session closed for thread {thread_id}")

    # Message loop - NO db session
    try:
        # Connect to WebSocketManager for Redis pub/sub support
        try:
            await forum_thread_manager.connect(websocket, user_id=str(current_user.id))
            logger.info(f"✅ Thread WebSocket connected to forum_thread_manager for thread {thread_id}")

            # Also connect to old manager for backwards compatibility during migration
            await manager.connect(websocket, thread_id, user_info)
            logger.info(f"✅ Thread WebSocket connected to legacy manager for thread {thread_id}")

        except Exception as e:
            logger.error(f"❌ Error connecting to manager: {str(e)}")
            await websocket.close(code=1011, reason="Manager connection error")
            return

        # Send connection confirmation
        try:
            await websocket.send_json({
                "type": "connected",
                "thread_id": thread_id,
                "message": f"Connected to thread {thread_id} live updates",
                "user_id": current_user.id
            })
            logger.info(f"✅ Connection confirmation sent for thread {thread_id}")

        except Exception as e:
            logger.error(f"❌ Error sending connection confirmation: {str(e)}")
            # Don't close connection for this, continue
        
        # Main message loop with enhanced error handling
        try:
            while True:
                try:
                    # Wait for messages with timeout
                    data = await asyncio.wait_for(websocket.receive_json(), timeout=30)
                    logger.debug(f"📨 Thread WebSocket message from user {current_user.id}: {data.get('type', 'unknown')}")
                    
                    # Handle different message types
                    message_type = data.get("type")
                    
                    if message_type == "typing":
                        # Handle typing indicators
                        try:
                            typing_data = {
                                "type": "user_typing",
                                "thread_id": thread_id,
                                "user_id": user_info["user_id"],
                                "username": user_info["display_name"],  # ✅ Use cached name
                                "is_typing": data.get("is_typing", False)
                            }
                            # New WebSocketManager (Redis pub/sub)
                            await forum_thread_manager.broadcast(typing_data)
                            # Legacy manager (backwards compatibility)
                            await manager.broadcast_to_thread(thread_id, typing_data)
                        except Exception as e:
                            logger.error(f"Error broadcasting typing indicator: {e}")
                    
                    elif message_type == "ping":
                        # Handle ping/pong for keepalive
                        try:
                            await websocket.send_json({"type": "pong"})
                        except Exception as e:
                            logger.error(f"Error sending pong: {e}")
                            break
                    
                    else:
                        logger.debug(f"Unhandled message type: {message_type}")
                        
                except asyncio.TimeoutError:
                    # Send heartbeat if no messages received
                    try:
                        await websocket.send_json({
                            "type": "heartbeat",
                            "timestamp": asyncio.get_event_loop().time()
                        })
                        logger.debug(f"💓 Sent heartbeat to thread {thread_id} user {current_user.id}")
                    except Exception as e:
                        logger.error(f"💔 Heartbeat failed for thread {thread_id}: {e}")
                        break
                        
                except WebSocketDisconnect:
                    logger.info(f"🔌 User {current_user.id} disconnected from thread {thread_id}")
                    break
                    
                except Exception as e:
                    logger.error(f"🚨 Error handling message in thread {thread_id}: {str(e)}")
                    # Continue loop, don't break on individual message errors
                    continue
                    
        except Exception as e:
            logger.error(f"🚨 Fatal error in thread WebSocket main loop: {str(e)}")
    
    except Exception as e:
        logger.error(f"🚨 Fatal error in thread WebSocket connection: {str(e)}")
        try:
            if websocket.application_state.CONNECTED:
                await websocket.close(code=1011, reason="Internal error")
        except:
            pass
    
    finally:
        # Cleanup with enhanced error handling
        logger.info(f"🧹 Cleaning up thread WebSocket for thread {thread_id}")

        try:
            # Disconnect from new WebSocketManager
            forum_thread_manager.disconnect(websocket)
            logger.info(f"✅ Disconnected from forum_thread_manager for thread {thread_id}")
        except Exception as e:
            logger.error(f"Error disconnecting from forum_thread_manager: {e}")

        try:
            # Disconnect from legacy manager
            if websocket in manager.connection_users:
                manager.disconnect(websocket, thread_id)
                logger.info(f"✅ Disconnected from legacy manager for thread {thread_id}")
        except Exception as e:
            logger.error(f"Error disconnecting from legacy manager: {e}")

        try:
            if ws_auth and websocket:
                ws_auth.disconnect_websocket(websocket)
                logger.info(f"✅ Cleaned up WebSocket auth for thread {thread_id}")
        except Exception as e:
            logger.error(f"Error cleaning up WebSocket auth: {e}")

        logger.info(f"❌ Thread WebSocket disconnected for thread {thread_id}")

@forum_router.post("/test-notification")
async def test_forum_notification(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Test function to create a sample forum notification using mapped enum values"""
    try:
        # Create a test forum notification using existing system with correct enum values
        notification_id = await create_forum_notification_via_existing_system(
            db=db,
            user_id=current_user.id,  # Send to yourself for testing
            thread_id=1,  # Fake thread ID for testing
            notification_type="mention",  # Will map to 'mention' enum
            title="Test Forum Mention",
            content=f"Test mention: @{current_user.username} this is a test forum notification",
            message_id=999,  # Fake message ID
            sender_id=current_user.id  # From yourself
        )
        
        return {
            "success": True,
            "message": "Test notification created with mapped enum values",
            "notification_id": notification_id,
            "enum_mapping": {
                "mention": "mention",
                "reply": "reply", 
                "new_message": "new_content"
            }
        }
        
    except Exception as e:
        logger.error(f"Error creating test notification: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }
@forum_router.get("/debug/thread/{thread_id}")
async def debug_thread_messages(
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Simple debug - just visit this URL in browser after posting a message"""
    
    messages = db.query(ForumMessage).filter(
        ForumMessage.thread_id == thread_id
    ).order_by(ForumMessage.created_at.desc()).limit(10).all()
    
    debug_info = []
    for msg in messages:
        debug_info.append({
            "id": msg.id,
            "content": msg.content,
            "user": msg.user.username,
            "created_at": str(msg.created_at)
        })
    
    return {
        "thread_id": thread_id,
        "message_count": len(debug_info),
        "messages": debug_info
    }
# 1. FIRST - Add this debug endpoint to see what's in the database
@forum_router.get("/debug/thread/{thread_id}/messages")
async def debug_thread_messages_simple(
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Simple debug - check what messages exist"""
    
    # Get all messages for this thread
    messages = db.query(ForumMessage).filter(
        ForumMessage.thread_id == thread_id
    ).order_by(ForumMessage.created_at.asc()).all()
    
    return {
        "thread_id": thread_id,
        "total_messages": len(messages),
        "messages": [
            {
                "id": msg.id,
                "content": msg.content,
                "user": msg.user.username,
                "created_at": str(msg.created_at)
            } for msg in messages
        ]
    }

@forum_router.post("/messages/{message_id}/like")
async def like_message(
    message_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Like a message"""
    
    # Get the message
    message = db.query(ForumMessage).filter(ForumMessage.id == message_id).first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    # Check thread access
    if not message.thread.can_access(current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Optional: Prevent users from liking their own messages
    if message.user_id == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot like your own message")
    
    # Check if already liked
    existing_like = db.query(ForumMessageLike).filter(
        ForumMessageLike.message_id == message_id,
        ForumMessageLike.user_id == current_user.id
    ).first()
    
    if existing_like:
        return {
            "success": True,
            "message": "Already liked",
            "like_count": message.like_count or 0,
            "user_has_liked": True
        }
    
    # Create like
    like = ForumMessageLike(
        message_id=message_id,
        user_id=current_user.id,
        created_at=datetime.now(timezone.utc)
    )
    db.add(like)
    
    # Update message like count
    message.like_count = (message.like_count or 0) + 1
    
    db.commit()
    
    # Broadcast like to live connections
    like_data = {
        "type": "message_liked",
        "thread_id": message.thread_id,
        "message_id": message_id,
        "like_count": message.like_count,
        "liked_by": {
            "id": current_user.id,
            "username": get_user_forum_display_name(current_user, db)
        }
    }
    # New WebSocketManager (Redis pub/sub)
    await forum_thread_manager.broadcast(like_data)
    # Legacy manager (backwards compatibility)
    await manager.broadcast_to_thread(message.thread_id, like_data)
    
    # Optional: Create notification for message author
    if message.user_id != current_user.id:
        try:
            await create_forum_notification_via_existing_system(
                db=db,
                user_id=message.user_id,
                thread_id=message.thread_id,
                message_id=message_id,
                sender_id=current_user.id,
                notification_type="new_content",  # Using existing enum
                title="Message Liked",
                content=f"{get_user_forum_display_name(current_user, db)} liked your message"
            )
        except Exception as e:
            logger.error(f"Error creating like notification: {e}")
    
    return {
        "success": True,
        "message": "Message liked",
        "like_count": message.like_count,
        "user_has_liked": True
    }

@forum_router.delete("/messages/{message_id}/like")
async def unlike_message(
    message_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Unlike a message"""
    
    # Get the message
    message = db.query(ForumMessage).filter(ForumMessage.id == message_id).first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    # Check thread access
    if not message.thread.can_access(current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Find existing like
    existing_like = db.query(ForumMessageLike).filter(
        ForumMessageLike.message_id == message_id,
        ForumMessageLike.user_id == current_user.id
    ).first()
    
    if not existing_like:
        return {
            "success": True,
            "message": "Not liked",
            "like_count": message.like_count or 0,
            "user_has_liked": False
        }
    
    # Remove like
    db.delete(existing_like)
    
    # Update message like count
    message.like_count = max(0, (message.like_count or 0) - 1)
    
    db.commit()
    
    # Broadcast unlike to live connections
    unlike_data = {
        "type": "message_unliked",
        "thread_id": message.thread_id,
        "message_id": message_id,
        "like_count": message.like_count,
        "unliked_by": {
            "id": current_user.id,
            "username": get_user_forum_display_name(current_user, db)
        }
    }
    # New WebSocketManager (Redis pub/sub)
    await forum_thread_manager.broadcast(unlike_data)
    # Legacy manager (backwards compatibility)
    await manager.broadcast_to_thread(message.thread_id, unlike_data)

    return {
        "success": True,
        "message": "Message unliked",
        "like_count": message.like_count,
        "user_has_liked": False
    }
@forum_router.get("/messages/{message_id}/likes")
async def get_message_likes(
    message_id: int,
    limit: int = Query(20, ge=1, le=100),
    skip: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Get users who liked a message"""
    
    # Get the message
    message = db.query(ForumMessage).filter(ForumMessage.id == message_id).first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    # Check thread access
    if not message.thread.can_access(current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Get likes with user info
    likes = db.query(ForumMessageLike).filter(
        ForumMessageLike.message_id == message_id
    ).order_by(desc(ForumMessageLike.created_at)).offset(skip).limit(limit).all()
    
    result = []
    for like in likes:
        result.append({
            "id": like.id,
            "user": {
                "id": like.user.id,
                "username": get_user_forum_display_name(like.user, db),
                "role": get_user_role_display(like.user),
                "badge_color": get_user_badge_color(like.user)
            },
            "created_at": like.created_at.isoformat()
        })
    
    total_likes = db.query(func.count(ForumMessageLike.id)).filter(
        ForumMessageLike.message_id == message_id
    ).scalar() or 0
    
    return {
        "likes": result,
        "total_likes": total_likes,
        "user_has_liked": any(like.user_id == current_user.id for like in likes)
    }
@forum_router.websocket("/ws/global")
async def secure_global_websocket_endpoint(
    websocket: WebSocket,
    ws_auth: WebSocketSessionAuth = Depends(get_websocket_auth)
):
    """SECURE Global WebSocket endpoint with proper session management"""

    current_user = None
    session_id = None
    user_info = None

    # Auth phase - create temporary session
    from database import SessionLocal
    db = SessionLocal()

    try:
        print(f"🌐 DEBUG: Global WebSocket connection attempt")

        # Accept connection first
        await websocket.accept()
        print(f"🤝 DEBUG: WebSocket connection accepted")

        # Authenticate using session cookies
        current_user = await ws_auth.authenticate_websocket(websocket, db, require_session=True)
        if not current_user:
            print(f"❌ DEBUG: Authentication failed, connection will be closed")
            return

        session_id = ws_auth.connection_sessions.get(websocket)
        print(f"✅ DEBUG: User {current_user.id} ({current_user.username}) authenticated with session {session_id}")

        # Build user info
        user_info = {
            "user_id": current_user.id,
            "username": current_user.username,
            "session_id": session_id,
            "connection_type": "global"
        }

    finally:
        # ✅ CLOSE DB BEFORE ENTERING LOOP
        db.close()
        print(f"✅ DEBUG: DB session closed for global WebSocket")

    # Message loop - NO db session
    try:
        # Connect to WebSocketManager for Redis pub/sub support
        await forum_global_manager.connect(websocket, user_id=str(user_info["user_id"]))
        logger.info(f"✅ Global WebSocket connected to forum_global_manager for user {user_info['user_id']}")

        # Also add to legacy global connections for backwards compatibility
        if user_info["user_id"] not in manager.user_connections:
            manager.user_connections[user_info["user_id"]] = set()
        manager.user_connections[user_info["user_id"]].add(websocket)

        manager.connection_users[websocket] = user_info

        print(f"📊 DEBUG: Connection tracking updated:")
        print(f"   - User {user_info['user_id']} now has {len(manager.user_connections[user_info['user_id']])} connections")
        print(f"   - Total users with connections: {len(manager.user_connections)}")

        logger.info(f"✅ Secure global WebSocket connected for user {user_info['user_id']}")

        # Send connection confirmation
        await websocket.send_json({
            "type": "connected",
            "message": "Connected to global forum updates",
            "user_id": user_info["user_id"],
            "session_id": session_id[:8] + "..."
        })
        print(f"📨 DEBUG: Sent connection confirmation to user {user_info['user_id']}")
        
        # Connection heartbeat and message handling
        last_heartbeat = asyncio.get_event_loop().time()
        heartbeat_interval = 30  # 30 seconds
        
        while True:
            try:
                # Set a timeout for receiving messages
                data = await asyncio.wait_for(websocket.receive_json(), timeout=heartbeat_interval)
                print(f"📨 DEBUG: Received message from user {user_info['user_id']}: {data.get('type', 'unknown')}")

                # Handle different message types
                if data.get("type") == "ping":
                    await websocket.send_json({"type": "pong", "timestamp": asyncio.get_event_loop().time()})
                    print(f"🏓 DEBUG: Sent pong to user {user_info['user_id']}")
                elif data.get("type") == "heartbeat":
                    last_heartbeat = asyncio.get_event_loop().time()
                    await websocket.send_json({"type": "heartbeat_ack"})

            except asyncio.TimeoutError:
                # Send heartbeat if no messages received
                try:
                    await websocket.send_json({"type": "heartbeat", "timestamp": asyncio.get_event_loop().time()})
                    print(f"💓 DEBUG: Sent heartbeat to user {user_info['user_id']}")
                except:
                    print(f"💔 DEBUG: Heartbeat failed for user {user_info['user_id']}, connection lost")
                    break

            except WebSocketDisconnect:
                print(f"🔌 DEBUG: User {user_info['user_id']} disconnected normally")
                break
            except Exception as e:
                print(f"🚨 DEBUG: Error handling message from user {user_info['user_id']}: {e}")
                logger.error(f"Global WebSocket message error: {e}")
                break

    except Exception as e:
        print(f"🚨 DEBUG: Global WebSocket connection error: {e}")
        logger.error(f"Global WebSocket connection error: {e}")
        try:
            if websocket.application_state.CONNECTED:
                await websocket.close(code=1011, reason="Internal error")
        except:
            pass

    finally:
        # Cleanup
        print(f"🧹 DEBUG: Cleaning up connection for user {user_info['user_id'] if user_info else 'unknown'}")

        try:
            # Disconnect from new WebSocketManager
            forum_global_manager.disconnect(websocket)
            logger.info(f"✅ Disconnected from forum_global_manager")
        except Exception as e:
            logger.error(f"Error disconnecting from forum_global_manager: {e}")

        if websocket in manager.connection_users:
            cleanup_user_info = manager.connection_users[websocket]
            cleanup_user_id = cleanup_user_info.get("user_id")

            if cleanup_user_id and cleanup_user_id in manager.user_connections:
                manager.user_connections[cleanup_user_id].discard(websocket)
                print(f"🧹 DEBUG: Removed connection from user {cleanup_user_id}")

                if not manager.user_connections[cleanup_user_id]:
                    del manager.user_connections[cleanup_user_id]
                    print(f"🧹 DEBUG: User {cleanup_user_id} has no more connections, removed from tracking")

            del manager.connection_users[websocket]
            print(f"🧹 DEBUG: Removed connection from tracking")

        if ws_auth:
            ws_auth.disconnect_websocket(websocket)

        print(f"📊 DEBUG: After cleanup:")
        print(f"   - Total users with connections: {len(manager.user_connections)}")
        print(f"   - Total tracked connections: {len(manager.connection_users)}")

        logger.info(f"❌ Secure global WebSocket disconnected")


@forum_router.post("/messages/{message_id}/create-thread", response_model=ThreadHierarchyResponse)
async def create_thread_from_message(
    message_id: int,
    request: CreateThreadFromMessageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Create a sub-thread from a specific message"""
    
    # Get the parent message
    parent_message = db.query(ForumMessage).filter(ForumMessage.id == message_id).first()
    if not parent_message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    # Check if user can access the parent thread
    parent_thread = parent_message.thread
    if not parent_thread.can_access(current_user):
        raise HTTPException(status_code=403, detail="Access denied to parent thread")
    
    # ADD THIS CHECK:
    if parent_thread.thread_type != "main":
        raise HTTPException(status_code=400, detail="Sub-threads can only be created from main threads")
    
    # Get current timestamp
    now = datetime.now(timezone.utc)
    
    # ✅ FIXED: Create the sub-thread without roles_allowed
    sub_thread = ForumThread(
        title=request.title,
        user_id=current_user.id,
        thread_type="sub",
        created_from_message_id=message_id,
        parent_message_id=message_id,
        # ✅ REMOVED: roles_allowed line that was causing the error
        created_at=now,
        last_message_at=now,
        follower_count=1  # Creator auto-follows
    )
    
    db.add(sub_thread)
    db.flush()  # Get thread ID
    
    # ✅ NEW: Inherit tier restrictions from parent using the new system
    if hasattr(parent_thread, 'tier_restrictions') and parent_thread.tier_restrictions:
        # If parent has tier restrictions, copy them to sub-thread
        tier_restrictions = parent_thread.tier_restrictions.copy() if parent_thread.tier_restrictions else {}
        if tier_restrictions.get('min_tier_id'):
            sub_thread.set_tier_restriction(tier_restrictions['min_tier_id'], db)
    else:
        # If parent has old min_tier_cents, inherit that
        if parent_thread.min_tier_cents > 0:
            sub_thread.min_tier_cents = parent_thread.min_tier_cents
    
    # Auto-follow the creator
    auto_follower = ForumThreadFollower(
        thread_id=sub_thread.id,
        user_id=current_user.id,
        notify_on_new_message=True,
        notify_on_mention=True,
        notify_on_reply=True,
        auto_followed=True,
        is_active=True,
        created_at=now
    )
    db.add(auto_follower)
    
    # Create first message in the sub-thread
    first_message = ForumMessage(
        thread_id=sub_thread.id,
        user_id=current_user.id,
        content=request.content,
        created_at=now
    )
    
    db.add(first_message)
    db.flush()  # Get message ID
    
    # ✅ FIXED: Handle mentions properly
    user_mentions, has_everyone, has_creator, has_team = parse_mentions(request.content)
    if user_mentions or has_everyone or has_creator or has_team:
         await create_mentions(message.id, user_mentions, has_everyone, has_creator, has_team, db)
    
    # Update sub-thread stats
    sub_thread.message_count = 1
    sub_thread.last_message_user_id = current_user.id
    
    # ⭐ CRITICAL: Update parent message spawned thread count
    old_count = parent_message.spawned_thread_count or 0
    parent_message.spawned_thread_count = old_count + 1
    new_count = parent_message.spawned_thread_count
    
    logger.info(f"🧵 Updated message {message_id} thread count: {old_count} -> {new_count}")
    
    db.commit()
    
    # 🚀 ENHANCED: Broadcast the message thread count update to parent thread viewers
    print(f"📡 Broadcasting thread count update for message {message_id} in thread {parent_thread.id}")
    print(f"📊 Thread count: {old_count} -> {new_count}")
    
    try:
        broadcast_data = {
            "type": "message_thread_count_updated",
            "message_id": message_id,
            "spawned_thread_count": new_count,
            "thread_id": parent_thread.id,
            "sub_thread_id": sub_thread.id,
            "sub_thread_title": sub_thread.title,
            "creator_username": get_user_forum_display_name(current_user, db)
        }

        # New WebSocketManager (Redis pub/sub)
        await forum_thread_manager.broadcast(broadcast_data)
        # Legacy manager (backwards compatibility)
        await manager.broadcast_to_thread(parent_thread.id, broadcast_data)
        print(f"✅ Successfully broadcasted thread count update to thread {parent_thread.id}")

    except Exception as e:
        logger.error(f"❌ Error broadcasting thread count update: {e}")
        print(f"❌ Error broadcasting thread count update: {e}")
    
    # Build created_from_message info
    created_from_message = {
        "id": parent_message.id,
        "content": parent_message.content[:100] + "..." if len(parent_message.content) > 100 else parent_message.content,
        "username": get_user_forum_display_name(parent_message.user, db),
        "created_at": parent_message.created_at.isoformat()
    }
    
    # ✅ FIXED: Get tier info using the new system
    tier_info = sub_thread.get_tier_info(db)
    
    # Build response
    thread_response = ThreadHierarchyResponse(
        id=sub_thread.id,
        title=sub_thread.title,
        user_id=sub_thread.user_id,
        username=get_user_forum_display_name(current_user, db),
        user_role=get_user_role_display(current_user),
        user_badge_color=get_user_badge_color(current_user),
        message_count=1,
        view_count=0,
        follower_count=1,
        is_pinned=sub_thread.is_pinned,
        is_locked=sub_thread.is_locked,
        min_tier_cents=sub_thread.min_tier_cents,
        last_message_at=now.isoformat(),
        created_at=now.isoformat(),
        can_access=True,
        can_delete=sub_thread.can_delete(current_user),
        can_manage=sub_thread.can_manage(current_user),
        is_following=True,
        thread_type="sub",
        parent_message_id=message_id,
        created_from_message=created_from_message,
        unread_count=0,
        tier_info=tier_info  # ✅ NEW: Include tier info
    )
    
    # 🚀 NEW: Broadcast new sub-thread to users following the parent thread
    print(f"📡 Broadcasting new sub-thread {sub_thread.id} to parent thread followers")
    
    # Get all users following the parent thread
    parent_followers = db.query(ForumThreadFollower).filter(
        ForumThreadFollower.thread_id == parent_thread.id,
        ForumThreadFollower.is_active == True
    ).all()
    
    broadcast_count = 0
    for follower in parent_followers:
        try:
            # Check if follower can access the new sub-thread
            follower_user = db.query(User).filter(User.id == follower.user_id).first()
            if follower_user and sub_thread.can_access(follower_user):
                # Send new sub-thread notification
                sub_thread_data = {
                    "type": "new_sub_thread_created",
                    "thread": thread_response.dict(),
                    "creator": {
                        "id": current_user.id,
                        "username": get_user_forum_display_name(current_user, db),
                        "role": get_user_role_display(current_user)
                    },
                    "parent_thread_id": parent_thread.id,
                    "parent_message_id": message_id
                }
                # New WebSocketManager (Redis pub/sub)
                await forum_global_manager.send_to_user(str(follower.user_id), sub_thread_data)
                # Legacy manager (backwards compatibility)
                await manager.send_to_user(follower.user_id, sub_thread_data)
                broadcast_count += 1
        except Exception as e:
            logger.error(f"Error broadcasting new sub-thread to user {follower.user_id}: {e}")
    
    print(f"✅ Broadcasted new sub-thread to {broadcast_count} parent thread followers")
    
    return thread_response
@forum_router.post("/threads/{thread_id}/mark-read")
async def mark_thread_notifications_read(
    thread_id: int,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Mark all notifications for a specific thread as read"""
    try:
        # Mark all unread forum notifications for this thread as read
        result = db.execute(
            text("""
            UPDATE notifications 
            SET is_read = true, read_at = :read_at 
            WHERE user_id = :user_id 
            AND is_read = false
            AND (
                title LIKE '[Forum]%' 
                OR notification_data::text LIKE '%"source": "forum"%'
            )
            AND notification_data::text LIKE :thread_filter
            """),
            {
                "user_id": current_user.id,
                "thread_filter": f'%"thread_id": {thread_id}%',
                "read_at": datetime.now(timezone.utc)
            }
        )
        
        db.commit()
        
        # Get number of notifications marked as read
        marked_count = result.rowcount if hasattr(result, "rowcount") else 0
        
        # Get updated total unread count for all forum notifications
        total_unread = db.execute(
            text("""
            SELECT COUNT(*) as count
            FROM notifications
            WHERE user_id = :user_id 
            AND is_read = false
            AND (
                title LIKE '[Forum]%' 
                OR notification_data::text LIKE '%"source": "forum"%'
            )
            """),
            {"user_id": current_user.id}
        ).scalar() or 0
        
        # Send live update to user's WebSocket connections
        if marked_count > 0:
            unread_update_data = {
                "type": "unread_count_updated",
                "thread_id": thread_id,
                "thread_unread_count": 0,
                "total_forum_unread": total_unread,
                "marked_read_count": marked_count
            }
            # New WebSocketManager (Redis pub/sub)
            await forum_global_manager.send_to_user(str(current_user.id), unread_update_data)
            # Legacy manager (backwards compatibility)
            await manager.send_to_user(current_user.id, unread_update_data)
        
        return {
            "success": True, 
            "marked_read": marked_count,
            "total_unread": total_unread
        }
        
    except Exception as e:
        logger.error(f"Error marking thread notifications as read: {str(e)}")
        db.rollback()
        return {"success": False, "error": "An error occurred"}



@forum_router.get("/tiers/available")
async def get_available_forum_tiers(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get available campaign tiers for forum restrictions"""
    try:
        # Only creators can see tiers (since they're the ones setting restrictions)
        if not current_user.is_creator:
            return {"tiers": []}
        
        # Get active campaign tiers for this creator
        tiers = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == current_user.id,
                CampaignTier.is_active == True
            )
        ).order_by(CampaignTier.amount_cents.asc()).all()
        
        tier_options = []
        
        # Add "Free" option
        tier_options.append({
            "id": None,
            "title": "Free Access",
            "amount_cents": 0,
            "description": "Anyone can access",
            "color": "#6b7280"
        })
        
        # Add database tiers
        for tier in tiers:
            tier_options.append({
                "id": tier.id,
                "title": tier.title,
                "amount_cents": tier.amount_cents,
                "description": tier.description or f"${tier.amount_cents/100:.2f}+ tier",
                "color": getattr(tier, 'color', '#3b82f6')
            })
        
        return {"tiers": tier_options}
        
    except Exception as e:
        logger.error(f"Error getting forum tiers: {str(e)}")
        return {"tiers": [], "error": str(e)}

# 🆕 NEW: Get user's tier information
@forum_router.get("/user-tier-info")
async def get_user_tier_info(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get current user's tier information for forum access"""
    try:
        user_tier_info = {
            "user_id": current_user.id,
            "is_creator": current_user.is_creator,
            "is_team": current_user.is_team,
            "is_patreon": current_user.is_patreon,
            "is_kofi": current_user.is_kofi,
            "current_tier_amount": 0,
            "current_tier_title": "Free User",
            "can_access_tiers": []
        }
        
        # Get user's current tier amount
        if current_user.patreon_tier_data:
            user_tier_info["current_tier_amount"] = current_user.patreon_tier_data.get("amount_cents", 0)
            user_tier_info["current_tier_title"] = current_user.patreon_tier_data.get("title", "Patron")
        
        # If user is creator/team, they have unlimited access
        if current_user.is_creator or current_user.is_team:
            user_tier_info["current_tier_title"] = "Creator" if current_user.is_creator else "Team"
            user_tier_info["current_tier_amount"] = 999999999  # ✅ FIX: Use large number instead of inf
        
        # Get all tiers they can access
        all_tiers = db.query(CampaignTier).filter(
            CampaignTier.is_active == True
        ).order_by(CampaignTier.amount_cents.asc()).all()
        
        accessible_tiers = []
        for tier in all_tiers:
            if (current_user.is_creator or current_user.is_team or 
                user_tier_info["current_tier_amount"] >= tier.amount_cents):
                accessible_tiers.append({
                    "id": tier.id,
                    "title": tier.title,
                    "amount_cents": tier.amount_cents
                })
        
        user_tier_info["can_access_tiers"] = accessible_tiers
        
        return user_tier_info
        
    except Exception as e:
        logger.error(f"Error getting user tier info: {str(e)}")
        return {"error": str(e)}

# 🆕 NEW: Update thread tier restrictions
@forum_router.patch("/threads/{thread_id}/tier-access")
async def update_thread_tier_access(
    thread_id: int,
    tier_access: dict,  # {"min_tier_id": int or null}
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Update thread tier access using database tiers (same pattern as albums)"""
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Only creators can update tier access")
    
    try:
        thread = db.query(ForumThread).filter(
            and_(
                ForumThread.id == thread_id,
                ForumThread.user_id == current_user.id
            )
        ).first()
        
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")
        
        min_tier_id = tier_access.get("min_tier_id")
        
        if min_tier_id:
            # Validate tier exists and belongs to creator
            tier = db.query(CampaignTier).filter(
                and_(
                    CampaignTier.id == min_tier_id,
                    CampaignTier.creator_id == current_user.id,
                    CampaignTier.is_active == True
                )
            ).first()
            
            if not tier:
                raise HTTPException(status_code=400, detail="Invalid tier selected")
            
            # Update thread with tier restrictions using the model method
            thread.set_tier_restriction(tier.id, db)
        else:
            # Remove tier restrictions
            thread.set_tier_restriction(None, db)
        
        thread.updated_at = datetime.now(timezone.utc)
        db.commit()
        
        return {
            "status": "success",
            "thread_id": thread_id,
            "tier_restrictions": thread.tier_restrictions
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating thread tier access: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# 🆕 NEW: Get tier information for a specific thread
@forum_router.get("/threads/{thread_id}/tier-info")
async def get_thread_tier_info(
    thread_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Get tier information for a thread"""
    try:
        thread = db.query(ForumThread).filter(ForumThread.id == thread_id).first()
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")
        
        tier_info = thread.get_tier_info(db)
        tier_info["user_can_access"] = thread.can_access(current_user)
        
        return tier_info
        
    except Exception as e:
        logger.error(f"Error getting thread tier info: {str(e)}")
        raise HTTPException(status_code=500, detail="Error getting tier information")


@forum_router.get("/moderation/everyone/settings")
async def get_everyone_moderation_settings(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get @everyone moderation settings (creators only)"""
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Creator access required")
    
    settings = db.query(ForumModerationSettings).filter(
        ForumModerationSettings.creator_id == current_user.id
    ).first()
    
    if not settings:
        # Create default settings
        settings = ForumModerationSettings(creator_id=current_user.id)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    
    return {
        "team_rate_limit": settings.team_rate_limit,
        "rate_limit_window_hours": settings.rate_limit_window_hours,
        "global_cooldown_minutes": settings.global_cooldown_minutes,
        "require_approval": settings.require_approval,
        "max_message_length": settings.max_message_length,
        "notification_limit": settings.notification_limit,
        "quiet_hours_start": settings.quiet_hours_start,
        "quiet_hours_end": settings.quiet_hours_end,
        "timezone": settings.timezone,
        "everyone_globally_disabled": settings.everyone_globally_disabled,
        "emergency_disable_reason": settings.emergency_disable_reason
    }

@forum_router.patch("/moderation/everyone/settings")
async def update_everyone_moderation_settings(
    settings_update: dict,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Update @everyone moderation settings (creators only)"""
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Creator access required")
    
    settings = db.query(ForumModerationSettings).filter(
        ForumModerationSettings.creator_id == current_user.id
    ).first()
    
    if not settings:
        settings = ForumModerationSettings(creator_id=current_user.id)
        db.add(settings)
    
    # Update allowed fields
    allowed_fields = [
        'team_rate_limit', 'rate_limit_window_hours', 'global_cooldown_minutes',
        'require_approval', 'max_message_length', 'notification_limit',
        'quiet_hours_start', 'quiet_hours_end', 'timezone'
    ]
    
    for field, value in settings_update.items():
        if field in allowed_fields and hasattr(settings, field):
            setattr(settings, field, value)
    
    settings.updated_at = datetime.now(timezone.utc)
    db.commit()
    
    return {"success": True, "message": "Moderation settings updated"}

@forum_router.post("/moderation/everyone/emergency-disable")
async def emergency_disable_everyone(
    reason: str = Body(..., embed=True),
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Emergency disable @everyone globally (creators only)"""
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Creator access required")
    
    settings = db.query(ForumModerationSettings).filter(
        ForumModerationSettings.creator_id == current_user.id
    ).first()
    
    if not settings:
        settings = ForumModerationSettings(creator_id=current_user.id)
        db.add(settings)
    
    settings.everyone_globally_disabled = True
    settings.emergency_disable_reason = reason
    settings.updated_at = datetime.now(timezone.utc)
    db.commit()
    
    # Broadcast to all connected users
    await manager.broadcast_to_all_users({
        "type": "everyone_disabled",
        "reason": reason,
        "disabled_by": current_user.username
    })
    
    return {"success": True, "message": "@everyone has been globally disabled"}

@forum_router.post("/moderation/everyone/emergency-enable")
async def emergency_enable_everyone(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Re-enable @everyone globally (creators only)"""
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Creator access required")
    
    settings = db.query(ForumModerationSettings).filter(
        ForumModerationSettings.creator_id == current_user.id
    ).first()
    
    if settings:
        settings.everyone_globally_disabled = False
        settings.emergency_disable_reason = None
        settings.updated_at = datetime.now(timezone.utc)
        db.commit()
    
    # Broadcast to all connected users
    await manager.broadcast_to_all_users({
        "type": "everyone_enabled",
        "enabled_by": current_user.username
    })
    
    return {"success": True, "message": "@everyone has been re-enabled"}

@forum_router.get("/moderation/everyone/analytics")
async def get_everyone_analytics(
    days: int = Query(7, ge=1, le=90),
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get @everyone usage analytics (creators only)"""
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Creator access required")
    
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
    
    # Overall statistics
    total_uses = db.query(func.count(ForumEveryoneMentionLog.id)).filter(
        ForumEveryoneMentionLog.created_at >= cutoff_date
    ).scalar() or 0
    
    total_notifications = db.query(func.sum(ForumEveryoneMentionLog.notification_count)).filter(
        ForumEveryoneMentionLog.created_at >= cutoff_date
    ).scalar() or 0
    
    unique_users = db.query(func.count(func.distinct(ForumEveryoneMentionLog.user_id))).filter(
        ForumEveryoneMentionLog.created_at >= cutoff_date
    ).scalar() or 0
    
    # Per-user analytics
    user_analytics = db.query(
        ForumEveryoneMentionLog.user_id,
        User.username,
        func.count(ForumEveryoneMentionLog.id).label('usage_count'),
        func.max(ForumEveryoneMentionLog.created_at).label('last_used'),
        func.avg(ForumEveryoneMentionLog.notification_count).label('avg_notifications')
    ).join(User).filter(
        ForumEveryoneMentionLog.created_at >= cutoff_date
    ).group_by(ForumEveryoneMentionLog.user_id, User.username).order_by(
        func.count(ForumEveryoneMentionLog.id).desc()
    ).all()
    
    # Daily usage breakdown
    daily_usage = db.query(
        func.date(ForumEveryoneMentionLog.created_at).label('date'),
        func.count(ForumEveryoneMentionLog.id).label('uses'),
        func.sum(ForumEveryoneMentionLog.notification_count).label('notifications')
    ).filter(
        ForumEveryoneMentionLog.created_at >= cutoff_date
    ).group_by(func.date(ForumEveryoneMentionLog.created_at)).order_by('date').all()
    
    return {
        "period_days": days,
        "summary": {
            "total_uses": total_uses,
            "total_notifications": total_notifications,
            "unique_users": unique_users,
            "avg_notifications_per_use": round(total_notifications / max(total_uses, 1), 1)
        },
        "user_analytics": [
            {
                "user_id": row.user_id,
                "username": row.username,
                "usage_count": row.usage_count,
                "last_used": row.last_used.isoformat() if row.last_used else None,
                "avg_notifications": round(float(row.avg_notifications or 0), 1)
            }
            for row in user_analytics
        ],
        "daily_breakdown": [
            {
                "date": row.date.isoformat(),
                "uses": row.uses,
                "notifications": row.notifications or 0
            }
            for row in daily_usage
        ]
    }

@forum_router.post("/moderation/everyone/restrict-user")
async def restrict_user_everyone(
    user_id: int,
    restriction_data: dict,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Restrict a user's @everyone usage (creators only)"""
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Creator access required")
    
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if target_user.is_creator:
        raise HTTPException(status_code=400, detail="Cannot restrict other creators")
    
    user_settings = db.query(ForumUserSettings).filter(
        ForumUserSettings.user_id == user_id
    ).first()
    
    if not user_settings:
        user_settings = ForumUserSettings(user_id=user_id)
        db.add(user_settings)
    
    user_settings.everyone_restricted = True
    user_settings.everyone_restriction_reason = restriction_data.get('reason', 'No reason provided')
    
    # Handle temporary vs permanent restrictions
    if restriction_data.get('duration_hours'):
        user_settings.everyone_restricted_until = datetime.now(timezone.utc) + timedelta(
            hours=restriction_data['duration_hours']
        )
    
    if restriction_data.get('custom_rate_limit'):
        user_settings.everyone_custom_rate_limit = restriction_data['custom_rate_limit']
    
    user_settings.everyone_violation_count += 1
    db.commit()
    
    # Notify the user
    restricted_data = {
        "type": "everyone_restricted",
        "reason": user_settings.everyone_restriction_reason,
        "until": user_settings.everyone_restricted_until.isoformat() if user_settings.everyone_restricted_until else None,
        "restricted_by": current_user.username
    }
    # New WebSocketManager (Redis pub/sub)
    await forum_global_manager.send_to_user(str(user_id), restricted_data)
    # Legacy manager (backwards compatibility)
    await manager.send_to_user(user_id, restricted_data)
    
    return {
        "success": True,
        "message": f"User {target_user.username} restricted from using @everyone"
    }

@forum_router.delete("/moderation/everyone/restrict-user/{user_id}")
async def unrestrict_user_everyone(
    user_id: int,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Remove @everyone restriction from a user (creators only)"""
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Creator access required")
    
    user_settings = db.query(ForumUserSettings).filter(
        ForumUserSettings.user_id == user_id
    ).first()
    
    if user_settings:
        user_settings.everyone_restricted = False
        user_settings.everyone_restricted_until = None
        user_settings.everyone_restriction_reason = None
        user_settings.everyone_custom_rate_limit = None
        db.commit()
    
    # Notify the user
    unrestricted_data = {
        "type": "everyone_unrestricted",
        "unrestricted_by": current_user.username
    }
    # New WebSocketManager (Redis pub/sub)
    await forum_global_manager.send_to_user(str(user_id), unrestricted_data)
    # Legacy manager (backwards compatibility)
    await manager.send_to_user(user_id, unrestricted_data)
    
    return {"success": True, "message": "User restriction removed"}

@forum_router.get("/moderation/everyone/restricted-users")
async def get_restricted_users(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get list of users restricted from @everyone (creators only)"""
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Creator access required")
    
    restricted_users = db.query(
        ForumUserSettings.user_id,
        User.username,
        ForumUserSettings.everyone_restriction_reason,
        ForumUserSettings.everyone_restricted_until,
        ForumUserSettings.everyone_custom_rate_limit,
        ForumUserSettings.everyone_violation_count
    ).join(User).filter(
        ForumUserSettings.everyone_restricted == True
    ).all()
    
    return {
        "restricted_users": [
            {
                "user_id": row.user_id,
                "username": row.username,
                "reason": row.everyone_restriction_reason,
                "until": row.everyone_restricted_until.isoformat() if row.everyone_restricted_until else None,
                "custom_rate_limit": row.everyone_custom_rate_limit,
                "violation_count": row.everyone_violation_count
            }
            for row in restricted_users
        ]
    }


@forum_router.delete("/notifications/{notification_id}")
async def delete_forum_notification(
    notification_id: int,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Delete a specific forum notification"""
    try:
        result = db.execute(
            text("""
            DELETE FROM notifications 
            WHERE id = :notification_id 
            AND user_id = :user_id 
            AND (notification_data->>'source' = 'forum' OR title LIKE '[Forum]%')
            RETURNING id
            """),
            {
                "notification_id": notification_id,
                "user_id": current_user.id
            }
        )
        
        deleted_id = result.scalar()
        if not deleted_id:
            raise HTTPException(status_code=404, detail="Forum notification not found")
        
        db.commit()
        
        # Get updated forum unread count
        unread_count = db.execute(
            text("""
            SELECT COUNT(*) as count
            FROM notifications
            WHERE user_id = :user_id 
            AND is_read = false 
            AND (notification_data->>'source' = 'forum' OR title LIKE '[Forum]%')
            """),
            {"user_id": current_user.id}
        ).scalar()
        
        return {"success": True, "unread_count": unread_count}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting forum notification: {str(e)}")
        db.rollback()
        return {"success": False, "error": "An error occurred"}

