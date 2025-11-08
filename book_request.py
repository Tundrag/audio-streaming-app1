#book_request.py

from fastapi import APIRouter, HTTPException, Depends, Request, Form
from sqlalchemy import and_, or_, func, text 
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from models import User, BookRequest, CampaignTier, BookRequestStatus, UserRole
from dateutil.relativedelta import relativedelta
import json
import re
import logging
from functools import wraps
from fastapi import HTTPException, Depends
from typing import List
from fastapi.templating import Jinja2Templates
from pathlib import Path
from starlette.websockets import WebSocketDisconnect
from notifications import create_notification_raw_sql_with_websocket
from typing import List, Dict, Any
from fastapi import WebSocket, Query
from typing import Set
import asyncio


from database import get_db
from auth import login_required
from activity_logs_router import log_activity_isolated
from models import AuditLogType
from redis_state import RedisStateManager
from redis_state.config import redis_client
from websocket_manager import WebSocketManager

# Configure logger
logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Create book request router
book_request_router = APIRouter(prefix="/api/book-requests")
book_request_pages_router = APIRouter()

# Initialize Redis state manager for distributed locking
book_request_state = RedisStateManager("book_request")

# Initialize centralized WebSocket manager
book_request_ws_manager = WebSocketManager(channel="book_requests")

# Admin user cache for targeted broadcasting
_admin_user_cache: Dict[int, Set[str]] = {}  # creator_id -> set of admin user_ids (as strings)
_cache_lock = asyncio.Lock()


# Helper Functions

async def get_admin_user_ids(creator_id: int, db: Session) -> Set[str]:
    """
    Get all admin user IDs (creator + team members) for a creator.
    Results are cached to avoid repeated DB queries.

    Args:
        creator_id: The creator's user ID
        db: Database session

    Returns:
        Set of admin user IDs as strings (for WebSocketManager compatibility)
    """
    async with _cache_lock:
        if creator_id not in _admin_user_cache:
            try:
                admin_users = db.query(User.id).filter(
                    or_(
                        User.id == creator_id,
                        and_(
                            User.created_by == creator_id,
                            User.is_team == True
                        )
                    ),
                    User.is_active == True
                ).all()

                _admin_user_cache[creator_id] = {str(u.id) for u in admin_users}
                logger.info(f"Cached {len(_admin_user_cache[creator_id])} admin user IDs for creator {creator_id}")

            except Exception as e:
                logger.error(f"Error fetching admin user IDs for creator {creator_id}: {e}")
                return set()

        return _admin_user_cache[creator_id]

def invalidate_admin_cache(creator_id: int = None):
    """
    Invalidate admin user cache.

    Args:
        creator_id: If provided, only invalidate for this creator. Otherwise clear all.
    """
    if creator_id:
        _admin_user_cache.pop(creator_id, None)
        logger.info(f"Invalidated admin cache for creator {creator_id}")
    else:
        _admin_user_cache.clear()
        logger.info("Cleared entire admin cache")

async def broadcast_book_request_update(
    book_request_dict: dict,
    action: str,
    user_id: int,
    creator_id: int,
    db: Session
):
    """
    Broadcast book request update to user and admins using centralized WebSocketManager.

    Args:
        book_request_dict: Serialized book request data
        action: Action type ("created", "status_changed", "reply_added")
        user_id: Requesting user's ID
        creator_id: Creator's ID (for admin notifications)
        db: Database session
    """
    message = {
        "type": "book_request_update",
        "book_request": book_request_dict,
        "action": action,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    # Get all admin user IDs
    admin_ids = await get_admin_user_ids(creator_id, db)

    # Target: user who made the request + all admins
    target_users = admin_ids | {str(user_id)}

    # Broadcast using centralized manager
    await book_request_ws_manager.broadcast(message, target_user_ids=target_users)

    logger.info(
        f"Broadcast book request update: action={action}, "
        f"user_id={user_id}, creator_id={creator_id}, "
        f"targets={len(target_users)}"
    )

async def broadcast_pending_count_update(
    creator_id: int,
    pending_count: int,
    db: Session
):
    """
    Broadcast pending count update to all admins.

    Args:
        creator_id: Creator's ID
        pending_count: Number of pending requests
        db: Database session
    """
    message = {
        "type": "pending_count_update",
        "pending_count": pending_count
    }

    # Get all admin user IDs
    admin_ids = await get_admin_user_ids(creator_id, db)

    # Broadcast to admins only
    await book_request_ws_manager.broadcast(message, target_user_ids=admin_ids)

    logger.info(
        f"Broadcast pending count update: creator_id={creator_id}, "
        f"pending_count={pending_count}, targets={len(admin_ids)}"
    )

def verify_role_permission(allowed_roles: List[str]):
    def decorator(func):
        @wraps(func)
        async def wrapper(
            *args,
            current_user: User = Depends(login_required),
            **kwargs
        ):
            # Simple role check
            role_type = "creator" if current_user.is_creator else \
                        "team" if current_user.is_team else \
                        "patreon" if current_user.is_patreon else "unknown"

            if role_type not in allowed_roles:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "message": "Access denied",
                        "required_roles": allowed_roles,
                        "current_role": role_type
                    }
                )

            return await func(*args, current_user=current_user, **kwargs)
        return wrapper
    return decorator

def get_user_permissions(user: User) -> dict:
    """Get user permissions for templates"""
    # Base permissions
    permissions = {
        "can_view": False,
        "can_create": False,
        "can_rename": False,
        "can_delete": False,
        "can_download": False,
        "downloads_blocked": False,
        "is_creator": user.is_creator,
        "is_team": user.is_team,
        "is_patreon": user.is_patreon,
        "role_type": "creator" if user.is_creator else "team" if user.is_team else "patreon" if user.is_patreon else "unknown"
    }
    
    # Set appropriate permissions based on role
    if user.is_creator or user.is_team:
        permissions["can_view"] = True
        permissions["can_create"] = True
        permissions["can_rename"] = True
        permissions["can_download"] = True
        if user.is_creator:
            permissions["can_delete"] = True
            permissions["can_manage_team"] = True
    elif user.is_patreon:
        permissions["can_view"] = True
        # Check download permissions based on tier
        if user.patreon_tier_data:
            album_downloads = user.patreon_tier_data.get('album_downloads_allowed', 0)
            track_downloads = user.patreon_tier_data.get('track_downloads_allowed', 0)
            permissions["can_download"] = album_downloads > 0 or track_downloads > 0
            permissions["album_downloads"] = album_downloads
            permissions["track_downloads"] = track_downloads
    
    return permissions

# WebSocket endpoint
@book_request_router.websocket("/ws")
async def book_request_websocket(
    websocket: WebSocket,
    user_id: int = Query(..., description="User ID for authentication")
):
    """WebSocket endpoint for book requests real-time updates"""
    from database import SessionLocal

    # Create manual session ONLY for auth/initial data
    db = SessionLocal()
    user_info = None

    try:
        # Get user by ID
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            await websocket.close(code=1008, reason="User not found")
            return

        # Prepare user info
        user_info = {
            'user_id': user.id,
            'username': user.username,
            'is_creator': user.is_creator,
            'is_team': user.is_team,
            'created_by': user.created_by
        }

        # Get initial data while we have db
        quota = await get_user_book_request_quota(user, db)
        pending_count = 0
        if user.is_creator or user.is_team:
            pending_count = await get_pending_book_request_count(user, db)

    finally:
        # Close db session BEFORE entering message loop
        db.close()

    # Now enter WebSocket loop WITHOUT db session
    try:
        await websocket.accept()

        # Connect to book request WebSocket using centralized manager
        await book_request_ws_manager.connect(
            websocket,
            user_id=str(user_info['user_id']),
            **user_info  # Pass user info as metadata
        )

        # Send initial data (already fetched)
        await websocket.send_json({
            "type": "initial_data",
            "quota": quota,
            "pending_count": pending_count
        })

        # Keep connection alive and handle messages
        while True:
            try:
                # Listen for messages
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)

                if data == "ping":
                    await websocket.send_text("pong")
                else:
                    # Handle other message types
                    try:
                        message = json.loads(data)
                        await handle_book_request_websocket_message(websocket, user_info, message)
                    except json.JSONDecodeError:
                        pass

            except asyncio.TimeoutError:
                # Send ping to keep connection alive
                try:
                    await websocket.send_text("ping")
                except:
                    break
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error(f"WebSocket message error: {e}")
                break

    except WebSocketDisconnect:
        logger.info(f"WebSocket client disconnected: user={user_info['username'] if user_info else 'unknown'}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        try:
            await websocket.close(code=1011, reason="Internal error")
        except:
            pass

    finally:
        book_request_ws_manager.disconnect(websocket)

async def handle_book_request_websocket_message(websocket: WebSocket, user_info: dict,
                                               message: dict):
    """Handle incoming WebSocket messages for book requests

    Note: Changed from User object + db to user_info dict.
    Creates fresh db session per request.
    """
    from database import SessionLocal
    message_type = message.get("type")

    if message_type == "refresh_quota":
        # Create fresh session for this query
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == user_info['user_id']).first()
            if user:
                quota = await get_user_book_request_quota(user, db)
                await websocket.send_json({
                    "type": "quota_update",
                    "quota": quota
                })
        finally:
            db.close()

    elif message_type == "refresh_pending":
        # Send updated pending count (for admins)
        if user_info['is_creator'] or user_info['is_team']:
            # Create fresh session for this query
            db = SessionLocal()
            try:
                user = db.query(User).filter(User.id == user_info['user_id']).first()
                if user:
                    pending_count = await get_pending_book_request_count(user, db)
                    await websocket.send_json({
                        "type": "pending_count_update",
                        "pending_count": pending_count
                    })
            finally:
                db.close()
async def get_user_book_request_quota(user: User, db: Session) -> dict:
    """Get user's current book request quota using counter system from patreon_tier_data"""
    try:
        user_email = getattr(user, 'email', 'unknown-email')
        current_month = datetime.now(timezone.utc).strftime("%Y-%m")
        
        # Check if user is a creator - they should have unlimited requests
        if hasattr(user, 'is_creator') and user.is_creator:
            logger.info(f"Creator account {user_email} has unlimited book requests")
            unlimited_value = 9999
            result = {
                "requests_allowed": unlimited_value,
                "requests_used": 0,
                "requests_remaining": unlimited_value,
                "current_month": current_month,
                "chapters_allowed_per_book_request": 9999
            }
            db.commit()
            return result
        
        # Check if user has tier data
        if not user or not hasattr(user, 'patreon_tier_data') or not user.patreon_tier_data:
            logger.info(f"No patreon_tier_data for user {user_email}")
            result = {
                "requests_allowed": 0,
                "requests_used": 0,
                "requests_remaining": 0,
                "current_month": current_month,
                "chapters_allowed_per_book_request": 0
            }
            db.commit()
            return result
        
        # ✅ NEW: Get book request info from tier data counters (like downloads)
        requests_allowed = user.patreon_tier_data.get('book_requests_allowed', 0)
        requests_used = user.patreon_tier_data.get('book_requests_used', 0)  # ← Use counter
        chapters_allowed = user.patreon_tier_data.get('chapters_allowed_per_book_request', 0)
        
        requests_remaining = max(0, requests_allowed - requests_used)
        
        logger.info(
            f"📊 Book request quota for {user_email}: "
            f"allowed={requests_allowed}, used={requests_used}, remaining={requests_remaining}, "
            f"chapters_per_request={chapters_allowed}, month={current_month}"
        )
        
        result = {
            "requests_allowed": requests_allowed,
            "requests_used": requests_used,
            "requests_remaining": requests_remaining,
            "current_month": current_month,
            "chapters_allowed_per_book_request": chapters_allowed
        }
        
        db.commit()
        return result
        
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
            
        user_email = getattr(user, 'email', 'unknown-email') if user else 'unknown-user'
        logger.error(f"Error getting book request quota for {user_email}: {str(e)}")
        
        return {
            "requests_allowed": 0,
            "requests_used": 0, 
            "requests_remaining": 0,
            "current_month": datetime.now(timezone.utc).strftime("%Y-%m"),
            "chapters_allowed_per_book_request": 0
        }


async def initialize_team_downloads(user: User, db: Session):
    """Initialize/reset download settings for team members - add book request logic"""
    try:
        logger.info(f"Initializing download settings for team member: {user.email}")
        
        team_tier = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == user.created_by,
                CampaignTier.title == "Team Members",
                CampaignTier.is_active == True
            )
        ).first()
        
        if not team_tier:
            team_tier = await get_or_create_team_tier(user.created_by, db)
            
        # Get all settings from team tier
        album_downloads = team_tier.album_downloads_allowed
        track_downloads = team_tier.track_downloads_allowed
        book_requests = team_tier.book_requests_allowed
        chapters_per_request = getattr(team_tier, 'chapters_allowed_per_book_request', 0)
        max_sessions = getattr(team_tier, 'max_sessions', 1)
        
        logger.info(
            f"Using team tier settings - "
            f"Albums: {album_downloads}, "
            f"Tracks: {track_downloads}, "
            f"Book Requests: {book_requests}, "
            f"Chapters: {chapters_per_request}, "
            f"Max Sessions: {max_sessions}"
        )
        
        now = datetime.now(timezone.utc)
        current_data = user.patreon_tier_data or {}
        
        # First time initialization if period_start is missing:
        if 'period_start' not in current_data:
            logger.info(f"First time initialization for team member {user.email}")
            current_data = {
                'title': 'Team Member',
                'album_downloads_allowed': album_downloads,
                'track_downloads_allowed': track_downloads,
                'book_requests_allowed': book_requests,
                'chapters_allowed_per_book_request': chapters_per_request,
                'album_downloads_used': 0,
                'track_downloads_used': 0,
                'max_sessions': max_sessions,
                'period_start': now.isoformat()
            }
        else:
            # Always update with current tier settings
            current_data.update({
                'album_downloads_allowed': album_downloads,
                'track_downloads_allowed': track_downloads,
                'book_requests_allowed': book_requests,
                'chapters_allowed_per_book_request': chapters_per_request,  # ALWAYS UPDATE THIS
                'max_sessions': max_sessions
            })
            
            # Handle monthly reset logic
            period_start = datetime.fromisoformat(current_data['period_start'].replace('Z', '+00:00'))
            next_reset = period_start + relativedelta(months=1)
            
            if now >= next_reset:
                logger.info(f"Monthly reset for team member {user.email}")
                new_period_start = now.replace(day=period_start.day)
                if new_period_start < now:
                    new_period_start += relativedelta(months=1)
                current_data.update({
                    'album_downloads_used': 0,
                    'track_downloads_used': 0,
                    'period_start': new_period_start.isoformat()
                })
        
        user.patreon_tier_data = current_data
        db.commit()
        db.refresh(user)
        
        logger.info(f"Team member {user.email} updated with chapters: {chapters_per_request}")
        logger.info(f"Team member download settings: {json.dumps(current_data, indent=2)}")
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

        # Get tier info
        tier_data = patron_data.get("tier_data", {})
        tier_title = tier_data.get("title")

        # Get CampaignTier settings
        campaign_tier = await get_tier_settings(creator.id, tier_title, db)
        if campaign_tier:
            logger.info(f"Found campaign tier: {tier_title}")
            album_downloads = campaign_tier.album_downloads_allowed
            track_downloads = campaign_tier.track_downloads_allowed
            book_requests = campaign_tier.book_requests_allowed
            chapters_per_request = getattr(campaign_tier, 'chapters_allowed_per_book_request', 0)
            max_sessions = getattr(campaign_tier, 'max_sessions', 1)
            logger.info(f"Campaign tier chapters setting: {chapters_per_request}")
        else:
            logger.warning(f"No campaign tier found for {tier_title}, defaulting to 0 downloads")
            album_downloads = 0
            track_downloads = 0
            book_requests = 0
            chapters_per_request = 0
            max_sessions = 1

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
            'book_requests_allowed': book_requests,
            'chapters_allowed_per_book_request': chapters_per_request,  # ENSURE THIS IS ALWAYS SET
            'tier_amount_cents': campaign_tier.amount_cents if campaign_tier else 0,
            'tier_description': campaign_tier.description if campaign_tier else "",
            'max_sessions': max_sessions
        }

        # Initialize or preserve download counts
        new_data['album_downloads_used'] = current_data.get('album_downloads_used', 0)
        new_data['track_downloads_used'] = current_data.get('track_downloads_used', 0)

        # Set period_start from last_charge_date
        if last_charge_date:
            new_data['period_start'] = last_charge_date
        elif 'period_start' not in current_data:
            new_data['period_start'] = datetime.now(timezone.utc).isoformat()

        # Update user data
        user.patreon_tier_data = new_data
        db.commit()
        db.refresh(user)
        
        logger.info(f"Updated patron {user.email} with chapters: {chapters_per_request}")
        logger.info(f"Verified saved data: {json.dumps(user.patreon_tier_data, indent=2)}")
        
        return user
        
    except Exception as e:
        logger.error(f"Error initializing patron downloads: {str(e)}", exc_info=True)
        db.rollback()
        raise

async def get_or_create_team_tier(creator_id: int, db: Session) -> CampaignTier:
    """Get existing team tier or create new one - with dynamic book_requests_allowed"""
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
            book_requests = 0  # Default to 0
            chapters_per_request = 0  # Default to 0
            max_sessions = 1
            
            if default_tier:
                album_downloads = getattr(default_tier, 'album_downloads_allowed', 4)
                track_downloads = getattr(default_tier, 'track_downloads_allowed', 2)
                book_requests = getattr(default_tier, 'book_requests_allowed', 0)
                chapters_per_request = getattr(default_tier, 'chapters_allowed_per_book_request', 0)
                max_sessions = getattr(default_tier, 'max_sessions', 1)
            
            # Create new team tier with derived values
            team_tier = CampaignTier(
                creator_id=creator_id,
                title="Team Members",
                description="Team Member Access",
                amount_cents=0,
                patron_count=0,
                is_active=True,
                album_downloads_allowed=album_downloads,
                track_downloads_allowed=track_downloads,
                book_requests_allowed=book_requests,
                chapters_allowed_per_book_request=chapters_per_request,  # INCLUDE THIS
                max_sessions=max_sessions,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            db.add(team_tier)
            db.flush()
            logger.info(f"Created new team tier for creator {creator_id} with chapters: {chapters_per_request}")
            
        return team_tier
        
    except Exception as e:
        logger.error(f"Error getting/creating team tier: {str(e)}")
        raise

# Import the get_tier_settings function if it's defined elsewhere
async def get_tier_settings(creator_id: int, tier_title: str, db: Session):
    """Get tier settings - this is a stub, replace with your implementation"""
    # Return the campaign tier matching the title
    return db.query(CampaignTier).filter(
        and_(
            CampaignTier.creator_id == creator_id,
            CampaignTier.title == tier_title,
            CampaignTier.is_active == True
        )
    ).first()

async def get_pending_book_request_count(current_user: User, db: Session) -> int:
    """Get count of pending book requests for admins - UNCHANGED"""
    if not (current_user.is_creator or current_user.is_team):
        logger.info(f"User {current_user.email} is not creator/team, returning 0 pending requests")
        return 0
        
    try:
        creator_id = current_user.id if current_user.is_creator else current_user.created_by
        
        # Keep using raw SQL to count pending requests (this is fine)
        from sqlalchemy import text
        count_query = text("""
            SELECT COUNT(*) FROM book_requests br
            JOIN users u ON br.user_id = u.id
            WHERE (u.id = :creator_id OR u.created_by = :creator_id)
            AND br.status = :pending_status
        """)
        
        result = db.execute(count_query, {
            'creator_id': creator_id,
            'pending_status': 'pending'
        })
        
        pending_count = result.scalar() or 0
        db.commit()
        
        logger.info(f"Found {pending_count} pending book requests for creator_id {creator_id}")
        return pending_count
        
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        logger.error(f"Error getting pending book request count: {str(e)}")
        return 0
async def add_pending_request_count(request, current_user, db):
    """Add pending book request count to request state."""
    if not hasattr(request, 'state'):
        request.state = {}
        logger.info("Initialized request.state")
    
    if current_user.is_creator or current_user.is_team:
        # Use your existing function to get the count
        count = await get_pending_book_request_count(current_user, db)
        request.state.pending_book_requests = count
        logger.info(f"Added {count} pending requests to request.state for {current_user.email}")
    else:
        request.state.pending_book_requests = 0
        logger.info(f"Set pending_book_requests to 0 for non-admin user {current_user.email}")
    
    return request


async def send_book_request_notification(
    db: Session,
    book_request,
    new_status: str,
    responder_user: User = None,
    custom_message: str = None
) -> int:
    """Send notification to user about their book request status change or admin about user reply"""
    try:
        # Determine notification recipient
        if new_status.lower() == "user_reply":
            # This is a user replying to admin - send notification to admin
            if not responder_user or not book_request.responded_by_id:
                return None
            
            user_id = book_request.responded_by_id  # Admin who responded originally
            title = "User Reply on Book Request"
            content = custom_message or f"User {responder_user.username} replied to book request '{book_request.title}'"
            
            # Additional data
            notification_data = {
                "book_request_id": book_request.id,
                "book_request_status": "user_reply",
                "book_title": book_request.title,
                "reply_from": responder_user.username
            }
        else:
            # This is admin responding to user - send notification to user
            user_id = book_request.user_id
            book_title = book_request.title
            
            # Determine notification content based on status
            if new_status.lower() == "approved":
                title = "Book Request Approved"
                content = custom_message or f"Your book request for '{book_title}' has been approved!"
            elif new_status.lower() == "rejected":
                title = "Book Request Rejected"
                content = custom_message or f"Your book request for '{book_title}' has been rejected."
            elif new_status.lower() == "fulfilled":
                title = "Book Request Fulfilled"
                content = custom_message or f"Your book request for '{book_title}' has been fulfilled!"
            elif new_status.lower() == "accepted":
                title = "Book Request In Progress"
                responder_name = responder_user.username if responder_user else "A team member"
                content = custom_message or f"{responder_name} is working on your book request for '{book_title}'."
            else:
                title = "Book Request Update"
                content = custom_message or f"Your book request for '{book_title}' has been updated to {new_status}."
            
            # Additional data to include with notification
            notification_data = {
                "book_request_id": book_request.id,
                "book_request_status": new_status.lower(),
                "book_title": book_title
            }
        
        # Map to a valid notification type from the existing enum
        notification_type = "system"
        
        # CHANGE: Use WebSocket version instead of HTTP
        notification_id = await create_notification_raw_sql_with_websocket(
            db=db,
            user_id=user_id,
            notification_type=notification_type,
            title=title,
            content=content,
            sender_id=responder_user.id if responder_user else None,
            notification_data=notification_data
        )
        
        return notification_id
    except Exception as e:
        logger.error(f"Error sending book request notification: {str(e)}")
        return None

# API Endpoints

# Submit a new book request
@book_request_router.post("/")
async def create_book_request(
    request: Request,
    title: str = Form(...),
    author: str = Form(...),
    link: str = Form(None),
    description: str = Form(None),
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Create a new book request with WebSocket broadcasting."""
    try:
        # Check if user has remaining quota
        quota = await get_user_book_request_quota(current_user, db)
        if quota["requests_remaining"] <= 0:
            raise HTTPException(
                status_code=403,
                detail={
                    "message": "No book requests remaining for this month",
                    "requests_used": quota["requests_used"],
                    "requests_allowed": quota["requests_allowed"],
                    "current_month": quota["current_month"]
                }
            )
        
        # Get current time
        now = datetime.now(timezone.utc)
        
        # Use raw SQL to insert the record
        from sqlalchemy import text
        
        query = text("""
        INSERT INTO book_requests 
        (user_id, title, author, link, description, status, month_year, created_at) 
        VALUES 
        (:user_id, :title, :author, :link, :description, 'pending', :month_year, :created_at)
        RETURNING id
        """)
        
        result = db.execute(
            query,
            {
                'user_id': current_user.id,
                'title': title,
                'author': author,
                'link': link,
                'description': description,
                'month_year': quota["current_month"],
                'created_at': now
            }
        )
        
        book_request_id = result.scalar()
        db.commit()
        
        # ✅ INCREMENT BOOK REQUEST USAGE COUNTER
        try:
            # Get current tier data
            current_tier_data = current_user.patreon_tier_data or {}
            
            # Increment the usage counter
            current_book_requests_used = current_tier_data.get('book_requests_used', 0)
            current_tier_data['book_requests_used'] = current_book_requests_used + 1
            
            # Save back to user
            current_user.patreon_tier_data = current_tier_data
            db.commit()  # Commit the usage increment
            
            logger.info(f"✅ Incremented book_requests_used for {current_user.email}: {current_book_requests_used} -> {current_book_requests_used + 1}")
            
        except Exception as usage_error:
            logger.error(f"Error updating book request usage counter: {str(usage_error)}")
            # Don't fail the entire request if usage tracking fails
            pass
        
        # Retrieve the newly created book request
        new_request_query = text("""
        SELECT id, user_id, title, author, link, description, status, created_at, 
               updated_at, responded_by_id, response_message, response_date, month_year
        FROM book_requests
        WHERE id = :id
        """)
        new_request_result = db.execute(new_request_query, {'id': book_request_id})
        new_request = new_request_result.fetchone()
        
        # Convert to dictionary format
        book_request_dict = {
            "id": new_request.id,
            "user_id": new_request.user_id,
            "title": new_request.title,
            "author": new_request.author,
            "link": new_request.link,
            "description": new_request.description,
            "status": new_request.status,
            "created_at": new_request.created_at.isoformat() if new_request.created_at else None,
            "updated_at": new_request.updated_at.isoformat() if new_request.updated_at else None,
            "responded_by_id": new_request.responded_by_id,
            "response_message": new_request.response_message,
            "response_date": new_request.response_date.isoformat() if new_request.response_date else None,
            "month_year": new_request.month_year
        }
        
        # Get creator ID for broadcasting
        creator_id = current_user.id if current_user.is_creator else current_user.created_by

        # Broadcast via WebSocket using helper function
        await broadcast_book_request_update(
            book_request_dict=book_request_dict,
            action="created",
            user_id=current_user.id,
            creator_id=creator_id,
            db=db
        )

        # Update quota for all user connections
        updated_quota = await get_user_book_request_quota(current_user, db)
        await book_request_ws_manager.send_to_user(
            str(current_user.id),
            {
                "type": "quota_update",
                "quota": updated_quota
            }
        )
        
        # Note: Pending count updates are handled by book_request_update message
        # The admin SPA will recalculate counts when it receives the created action
        # Sending a separate pending_count_update would cause double counting
        
        return {
            "status": "success",
            "message": "Book request submitted successfully",
            "book_request": book_request_dict,
            "quota": updated_quota
        }
    
    except HTTPException as he:
        raise he
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating book request: {str(e)}")
        raise HTTPException(status_code=500, detail="Error creating book request")


# Get book request quota
@book_request_router.get("/quota")
async def get_book_request_quota(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get user's book request quota."""
    try:
        quota = await get_user_book_request_quota(current_user, db)
        return quota
    except Exception as e:
        logger.error(f"Error getting book request quota: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching book request quota")

# Get user's book requests
@book_request_router.get("/")
async def get_user_book_requests(
    status: str = None,
    month_year: str = None,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get user's book requests with optional filters - UPDATED to include user_reply."""
    try:
        # Start with clean transaction state
        db.rollback()
        
        # Use raw SQL to bypass SQLAlchemy's enum handling
        from sqlalchemy import text
        
        # Build the SQL query with optional filters - UPDATED SQL to include user_reply
        sql = """
        SELECT id, user_id, title, author, link, description, status, 
               created_at, updated_at, responded_by_id, response_message, 
               response_date, month_year, accepted_by_id, accepted_at, user_reply
        FROM book_requests
        WHERE user_id = :user_id
        """
        
        params = {'user_id': current_user.id}
        
        # Add status filter if provided
        if status:
            sql += " AND status = :status"
            params['status'] = status
        
        # Add month_year filter if provided
        if month_year:
            # Validate month_year format
            if not re.match(r'^\d{4}-\d{2}$', month_year):
                raise HTTPException(status_code=400, detail="Invalid month_year format. Use YYYY-MM format.")
            sql += " AND month_year = :month_year"
            params['month_year'] = month_year
        
        # Add order by
        sql += " ORDER BY created_at DESC"
        
        # Execute the query
        result = db.execute(text(sql), params)
        rows = result.fetchall()
        
        # Convert results to dictionaries - UPDATED to include user_reply
        book_requests = []
        for row in rows:
            book_requests.append({
                "id": row.id,
                "user_id": row.user_id,
                "title": row.title,
                "author": row.author,
                "link": row.link,
                "description": row.description,
                "status": row.status,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                "responded_by_id": row.responded_by_id,
                "response_message": row.response_message,
                "response_date": row.response_date.isoformat() if row.response_date else None,
                "month_year": row.month_year,
                "accepted_by_id": row.accepted_by_id,
                "accepted_at": row.accepted_at.isoformat() if row.accepted_at else None,
                "user_reply": getattr(row, 'user_reply', None)  # Handle cases where column might not exist yet
            })
        
        return {
            "requests": book_requests,
            "quota": await get_user_book_request_quota(current_user, db),
            "count": len(book_requests)
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        db.rollback()
        logger.error(f"Error getting book requests: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching book requests")

# Get all book requests (for team members and creators)
@book_request_router.get("/admin")
@verify_role_permission(["creator", "team"])
async def get_all_book_requests(
    status: str = None,
    user_id: int = None,
    month_year: str = None,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get all book requests (for team members and creators) - UPDATED to include user_reply."""
    try:
        # Start with clean transaction state
        db.rollback()
        
        # Import text function from sqlalchemy
        from sqlalchemy import text
                
        creator_id = current_user.id if current_user.is_creator else current_user.created_by
        
        # Get all users under this creator
        user_query = """
        SELECT id FROM users
        WHERE id = :creator_id OR created_by = :creator_id
        """
        user_rows = db.execute(text(user_query), {'creator_id': creator_id}).fetchall()
        user_ids = [row[0] for row in user_rows]
        
        if not user_ids:
            return {
                "requests": [],
                "count": 0,
                "pending_count": 0,
                "users": [],
                "months": []
            }
        
        # Build SQL query for book requests - UPDATED SQL to include user_reply
        sql = """
        SELECT id, user_id, title, author, link, description, status, 
               created_at, updated_at, responded_by_id, response_message, 
               response_date, month_year, accepted_by_id, accepted_at, user_reply
        FROM book_requests
        WHERE user_id IN :user_ids
        """
        
        # Fix for single-item tuples in SQL parameters
        user_ids_param = tuple(user_ids)
        if len(user_ids) == 1:
            # Special handling for single-item IN clause
            sql = sql.replace("IN :user_ids", "= :user_id")
            params = {'user_id': user_ids[0]}
        else:
            params = {'user_ids': user_ids_param}
        
        # Add status filter if provided
        if status:
            sql += " AND status = :status"
            params['status'] = status
        
        # Add user_id filter if provided
        if user_id:
            sql += " AND user_id = :filter_user_id"
            params['filter_user_id'] = user_id
            
        # Add month_year filter if provided
        if month_year:
            # Validate month_year format
            if not re.match(r'^\d{4}-\d{2}$', month_year):
                raise HTTPException(status_code=400, detail="Invalid month_year format. Use YYYY-MM format.")
            sql += " AND month_year = :month_year"
            params['month_year'] = month_year
        
        # Add order by
        sql += " ORDER BY created_at DESC"
        
        # Execute query
        book_request_rows = db.execute(text(sql), params).fetchall()
        
        # Get all users for reference - including patreon_tier_data
        if len(user_ids) == 1:
            users_sql = "SELECT id, email, username, role, patreon_tier_data FROM users WHERE id = :user_id"
            user_detail_rows = db.execute(text(users_sql), {'user_id': user_ids[0]}).fetchall()
        else:
            users_sql = "SELECT id, email, username, role, patreon_tier_data FROM users WHERE id IN :user_ids"
            user_detail_rows = db.execute(text(users_sql), {'user_ids': user_ids_param}).fetchall()
        
        # Process users with tier information
        users = {}
        for row in user_detail_rows:
            # Extract tier information from patreon_tier_data
            tier_display = ""
            chapters_allowed = 0
            
            if row.patreon_tier_data:
                try:
                    import json
                    tier_data = json.loads(row.patreon_tier_data) if isinstance(row.patreon_tier_data, str) else row.patreon_tier_data
                    tier_name = tier_data.get('title', '')
                    chapters_allowed = tier_data.get('chapters_allowed_per_book_request', 0)
                    
                    if tier_name:
                        if row.role == 'patreon':
                            tier_display = f" (Patreon {tier_name})"
                        elif row.role == 'kofi':
                            tier_display = f" (Ko-fi {tier_name})"
                        elif row.role == 'team':
                            tier_display = " (Team Member)"
                        else:
                            tier_display = f" ({tier_name})"
                except (json.JSONDecodeError, TypeError, AttributeError):
                    pass
            elif row.role == 'creator':
                tier_display = " (Creator)"
                chapters_allowed = 9999
            elif row.role == 'team':
                tier_display = " (Team Member)"
            
            users[row.id] = {
                "id": row.id,
                "email": row.email,
                "username": row.username,
                "role": row.role,
                "tier_display": tier_display,
                "display_name": f"{row.username or row.email}{tier_display}",
                "chapters_allowed": chapters_allowed
            }
        
        # Convert book requests to dictionaries - UPDATED to include user_reply
        result = []
        months = set()
        pending_count = 0
        
        for row in book_request_rows:
            # Add to months set
            if row.month_year:
                months.add(row.month_year)
                
            # Count pending
            if row.status == 'pending':
                pending_count += 1
                
            # Create dictionary - UPDATED to include user_reply
            br_dict = {
                "id": row.id,
                "user_id": row.user_id,
                "title": row.title,
                "author": row.author,
                "link": row.link,
                "description": row.description,
                "status": row.status,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                "responded_by_id": row.responded_by_id,
                "response_message": row.response_message,
                "response_date": row.response_date.isoformat() if row.response_date else None,
                "month_year": row.month_year,
                "accepted_by_id": row.accepted_by_id,
                "accepted_at": row.accepted_at.isoformat() if row.accepted_at else None,
                "user_reply": getattr(row, 'user_reply', None)  # Handle cases where column might not exist yet
            }
            
            # Add user info (now includes tier information)
            if row.user_id in users:
                br_dict["user"] = users[row.user_id]
            
            # Add responder info
            if row.responded_by_id and row.responded_by_id in users:
                br_dict["responder"] = users[row.responded_by_id]
            
            result.append(br_dict)
        
        return {
            "requests": result,
            "count": len(result),
            "pending_count": pending_count,
            "users": list(users.values()),
            "months": sorted(list(months), reverse=True)
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        db.rollback()
        logger.error(f"Error getting all book requests: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching book requests")

# Respond to a book request (for team members and creators)
@book_request_router.post("/{request_id}/respond")
@verify_role_permission(["creator", "team"])
async def respond_to_book_request(
    request_id: int,
    status: str = Form(...),
    response_message: str = Form(None),
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Respond to a book request with WebSocket broadcasting."""
    try:
        # Start with a clean transaction state
        db.rollback()

        from sqlalchemy import text

        creator_id = current_user.id if current_user.is_creator else current_user.created_by

        # Validate the status value
        valid_statuses = ['pending', 'approved', 'rejected', 'fulfilled']
        if status.lower() not in valid_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status value: {status}. Valid values are: {', '.join(valid_statuses)}"
            )
        
        # Get book request
        query = text("""
        SELECT id, user_id, title, author, link, description, status, 
               created_at, updated_at, responded_by_id, response_message, 
               response_date, month_year, accepted_by_id, accepted_at, user_reply
        FROM book_requests
        WHERE id = :request_id
        """)
        
        result = db.execute(query, {"request_id": request_id})
        book_request = result.fetchone()
        
        if not book_request:
            raise HTTPException(status_code=404, detail="Book request not found")
        
        # Check permissions (existing code)
        user_query = text("""
        SELECT id, email, username, role, created_by
        FROM users
        WHERE id = :user_id
        """)
        
        user_result = db.execute(user_query, {"user_id": book_request.user_id})
        requester = user_result.fetchone()
        
        if not requester:
            raise HTTPException(status_code=404, detail="Requester not found")
        
        if requester.created_by != creator_id and requester.id != creator_id:
            raise HTTPException(status_code=403, detail="Access denied to this request")
        
        # Get current time
        now = datetime.now(timezone.utc)
        
        # Update book request
        update_query = text("""
        UPDATE book_requests
        SET status = :status,
            responded_by_id = :responded_by_id,
            response_message = :response_message,
            response_date = :response_date,
            updated_at = :updated_at,
            user_reply = NULL
        WHERE id = :request_id
        """)
        
        db.execute(update_query, {
            "status": status.lower(),
            "responded_by_id": current_user.id,
            "response_message": response_message,
            "response_date": now,
            "updated_at": now,
            "request_id": request_id
        })

        # Log activity for approval or rejection
        try:
            action_description = f"Book request '{book_request.title}' by {book_request.author} was {status.lower()}"
            if response_message:
                action_description += f" with message: {response_message}"

            await log_activity_isolated(
                user_id=current_user.id,
                action_type=AuditLogType.UPDATE,
                table_name="book_requests",
                record_id=str(request_id),
                description=action_description,
                old_values={"status": book_request.status},
                new_values={"status": status.lower(), "response_message": response_message},
                ip_address=None,
                user_agent=None
            )
        except Exception as log_error:
            logger.error(f"Failed to log activity for book request response: {str(log_error)}")

        # Send notification
        try:
            await send_book_request_notification(
                db=db,
                book_request=book_request,
                new_status=status.lower(),
                responder_user=current_user,
                custom_message=response_message
            )
        except Exception as notif_error:
            logger.error(f"Notification error but continuing: {str(notif_error)}")
        
        # ✅ COMPLETE REFUND LOGIC FOR REJECTED REQUESTS
        refunded = False
        if status.lower() == 'rejected':
            logger.info(f"Processing refund for rejected book request - user {book_request.user_id} (request {request_id})")
            
            try:
                # Get the requester user with their current tier data
                requester_user = db.query(User).filter(User.id == book_request.user_id).first()
                
                if not requester_user:
                    logger.error(f"Cannot refund - requester user not found: {book_request.user_id}")
                elif not requester_user.patreon_tier_data:
                    logger.error(f"Cannot refund - no tier data found for user: {requester_user.email}")
                else:
                    # 🔒 CRITICAL: Acquire user-level lock to prevent refund race conditions
                    # This prevents concurrent rejections from corrupting the usage counter
                    refund_lock_key = f"user_refund_{book_request.user_id}"

                    if not book_request_state.acquire_lock(refund_lock_key, timeout=10):
                        logger.warning(f"Could not acquire refund lock for user {requester_user.email}, skipping refund")
                    else:
                        try:
                            # Re-fetch user with fresh tier data under lock
                            db.refresh(requester_user)

                            # Get current tier data
                            current_tier_data = dict(requester_user.patreon_tier_data)  # Make a copy
                            current_used = current_tier_data.get('book_requests_used', 0)

                            logger.info(f"Current book_requests_used for {requester_user.email}: {current_used}")

                            # Only refund if there's usage to refund
                            if current_used > 0:
                                # Decrement the usage counter
                                new_used = current_used - 1
                                current_tier_data['book_requests_used'] = new_used

                                # Add refund tracking information
                                current_tier_data['last_refund_date'] = datetime.now(timezone.utc).isoformat()
                                current_tier_data['last_refund_request_id'] = request_id

                                # Save the updated tier data
                                requester_user.patreon_tier_data = current_tier_data
                                db.flush()  # Ensure the refund is saved before commit

                                logger.info(f"✅ Successfully refunded book request for {requester_user.email}: {current_used} -> {new_used}")
                                refunded = True

                            elif current_used == 0:
                                logger.warning(f"Cannot refund book request for {requester_user.email} - usage counter already at 0")
                                # Still set refunded to True so the UI shows the refund was attempted
                                refunded = True

                            else:
                                logger.error(f"Invalid book_requests_used value for {requester_user.email}: {current_used}")

                        finally:
                            # 🔓 Always release refund lock
                            book_request_state.release_lock(refund_lock_key)
                        
            except Exception as refund_error:
                logger.error(f"Error processing book request refund: {str(refund_error)}", exc_info=True)
                # Don't fail the entire request if refund fails, but log the error
                # The admin response will still be recorded even if refund fails
                pass
        
        db.commit()
        
        # Get the updated book request
        updated_query = text("""
        SELECT id, user_id, title, author, link, description, status, 
               created_at, updated_at, responded_by_id, response_message, 
               response_date, month_year, accepted_by_id, accepted_at, user_reply
        FROM book_requests
        WHERE id = :request_id
        """)
        
        updated_result = db.execute(updated_query, {"request_id": request_id})
        updated_book_request = updated_result.fetchone()
        
        # Convert to dictionary
        book_request_dict = {
            "id": updated_book_request.id,
            "user_id": updated_book_request.user_id,
            "title": updated_book_request.title,
            "author": updated_book_request.author,
            "link": updated_book_request.link,
            "description": updated_book_request.description,
            "status": updated_book_request.status,
            "created_at": updated_book_request.created_at.isoformat() if updated_book_request.created_at else None,
            "updated_at": updated_book_request.updated_at.isoformat() if updated_book_request.updated_at else None,
            "responded_by_id": updated_book_request.responded_by_id,
            "response_message": updated_book_request.response_message,
            "response_date": updated_book_request.response_date.isoformat() if updated_book_request.response_date else None,
            "month_year": updated_book_request.month_year,
            "accepted_by_id": updated_book_request.accepted_by_id,
            "accepted_at": updated_book_request.accepted_at.isoformat() if updated_book_request.accepted_at else None,
            "user_reply": updated_book_request.user_reply,
            "responder": {
                "id": current_user.id,
                "username": current_user.username
            }
        }
        
        # Broadcast via WebSocket using helper function
        await broadcast_book_request_update(
            book_request_dict=book_request_dict,
            action="status_changed",
            user_id=book_request.user_id,
            creator_id=creator_id,
            db=db
        )

        # Update pending count for admins
        pending_count = await get_pending_book_request_count(current_user, db)
        await broadcast_pending_count_update(
            creator_id=creator_id,
            pending_count=pending_count,
            db=db
        )

        # Update quota if rejected (refunded) - This will now show the correct decremented count
        if refunded:
            updated_quota = await get_user_book_request_quota(
                db.query(User).filter(User.id == book_request.user_id).first(),
                db
            )
            await book_request_ws_manager.send_to_user(
                str(book_request.user_id),
                {
                    "type": "quota_update",
                    "quota": updated_quota
                }
            )
        
        return {
            "status": "success",
            "message": "Response recorded successfully",
            "book_request": book_request_dict,
            "refunded": refunded
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        db.rollback()
        logger.error(f"Error responding to book request: {str(e)}")
        raise HTTPException(status_code=500, detail="Error recording response")

@book_request_pages_router.get("/my-book-requests")
async def my_book_requests(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    # Add this line to populate request.state.pending_book_requests
    request = await add_pending_request_count(request, current_user, db)
    """User's book requests page"""
    # Get the user's book requests API data
    api_result = await get_user_book_requests(
        status=None,
        month_year=None,
        current_user=current_user,
        db=db
    )
    
    # Get quota information
    quota = await get_user_book_request_quota(current_user, db)
    
    # Organize requests by month
    requests_by_month = {}
    sorted_months = []
    
    for request_data in api_result.get("requests", []):
        month = request_data.get("month_year")
        if month not in requests_by_month:
            requests_by_month[month] = []
            sorted_months.append(month)
        requests_by_month[month].append(request_data)
    
    # Sort months in descending order (newest first)
    sorted_months.sort(reverse=True)
    
    # Calculate status counts
    all_requests = api_result.get("requests", [])
    status_counts = {
        "total": len(all_requests),
        "pending": sum(1 for r in all_requests if r.get("status") == "pending"),
        "approved": sum(1 for r in all_requests if r.get("status") == "approved"),
        "rejected": sum(1 for r in all_requests if r.get("status") == "rejected"),
        "fulfilled": sum(1 for r in all_requests if r.get("status") == "fulfilled")
    }
    pending_book_requests = 0
    if current_user.is_creator or current_user.is_team:
        pending_book_requests = await get_pending_book_request_count(current_user, db)
    
    return templates.TemplateResponse(
        "book_request.html",
        {
            "request": request,
            "user": current_user,
            "quota": quota,
            "requests_by_month": requests_by_month,
            "sorted_months": sorted_months,
            "status_counts": status_counts,
            "permissions": get_user_permissions(current_user),
            "pending_book_requests": pending_book_requests  # Add this line
        }
    )
@book_request_pages_router.get("/admin/book-requests")
@verify_role_permission(["creator", "team"])
async def admin_book_requests(
    request: Request,
    status: str = None,
    month_year: str = None,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    request = await add_pending_request_count(request, current_user, db)
    """Admin page for managing book requests"""
    # Get all book requests data from the admin API endpoint
    api_result = await get_all_book_requests(
        status=status,
        user_id=None,
        month_year=month_year,
        current_user=current_user,
        db=db
    )
    
    book_requests = api_result.get("requests", [])
    available_months = api_result.get("months", [])
    
    # Organize request counts by month
    requests_by_month = {}
    for month in available_months:
        month_requests = [r for r in book_requests if r.get("month_year") == month]
        requests_by_month[month] = {
            "total": len(month_requests),
            "pending": sum(1 for r in month_requests if r.get("status") == "pending")
        }
    
    # Calculate status counts
    status_counts = {
        "total": len(book_requests),
        "pending": sum(1 for r in book_requests if r.get("status") == "pending"),
        "approved": sum(1 for r in book_requests if r.get("status") == "approved"),
        "rejected": sum(1 for r in book_requests if r.get("status") == "rejected"),
        "fulfilled": sum(1 for r in book_requests if r.get("status") == "fulfilled")
    }
    
    pending_book_requests = status_counts["pending"]
    
    return templates.TemplateResponse(
        "manager_book_request.html",
        {
            "request": request,
            "user": current_user,
            "book_requests": book_requests,
            "available_months": available_months,
            "requests_by_month": requests_by_month,
            "status_counts": status_counts,
            "active_status": status,
            "active_month": month_year,
            "permissions": get_user_permissions(current_user),
            "pending_book_requests": pending_book_requests  # Add this line
        }
    )

@book_request_router.post("/settings")
@verify_role_permission(["creator"])
async def update_book_request_settings(
    settings: Dict[str, Any],
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Update book request allowances for a tier using CampaignTier - FIXED"""
    try:
        creator_id = current_user.id
        tier_title = settings.get('tier_id')
        book_requests_allowed = settings.get('book_requests_allowed', 0)
        
        logger.info(f"Updating book request settings:")
        logger.info(f"Tier: {tier_title}")
        logger.info(f"Book requests allowed: {book_requests_allowed}")

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
                book_requests_allowed=book_requests_allowed,
                is_active=True,
                # For team tier, use special handling
                patreon_tier_id=None if is_team_tier else tier_title,
                amount_cents=0 if is_team_tier else None
            )
            db.add(campaign_tier)
            logger.info(f"Created new campaign tier: {tier_title} with book requests: {book_requests_allowed}")
        else:
            campaign_tier.book_requests_allowed = book_requests_allowed
            logger.info(f"Updated existing campaign tier: {tier_title} with book requests: {book_requests_allowed}")

        db.flush()

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
        
        for user in users_to_update:
            try:
                # ✅ FIXED: Use the same pattern as downloads settings
                current_data = user.patreon_tier_data or {}
                updated_data = current_data.copy()  # ✅ Preserves ALL existing data including usage
                
                # ✅ FIXED: Only update allowances, preserve usage
                updated_data.update({
                    'book_requests_allowed': book_requests_allowed,
                    # ✅ CRITICAL: No book_requests_used = 0 line!
                    # Usage is preserved automatically from current_data.copy()
                })
                
                user.patreon_tier_data = updated_data
                update_count += 1
                
                logger.info(f"Updated user {user.email}: book requests: {book_requests_allowed}")

            except Exception as e:
                logger.error(f"Error processing user {user.email}: {str(e)}")
                continue

        db.commit()
        
        return {
            "status": "success",
            "message": f"Successfully updated tier and {update_count} users",
            "book_requests_allowed": book_requests_allowed,
            "tier_title": tier_title
        }

    except Exception as e:
        db.rollback()
        logger.error(f"Error updating book request settings: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@book_request_router.post("/{request_id}/accept")
@verify_role_permission(["creator", "team"])
async def accept_book_request(
    request_id: int,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Accept a book request (for team members and creators)."""
    try:
        creator_id = current_user.id if current_user.is_creator else current_user.created_by
        
        # Get book request
        book_request = db.query(BookRequest).filter(BookRequest.id == request_id).first()
        if not book_request:
            raise HTTPException(status_code=404, detail="Book request not found")
        
        # Check if already accepted
        if book_request.accepted_by_id:
            raise HTTPException(
                status_code=400, 
                detail=f"This request has already been accepted by another team member"
            )
        
        # Update book request
        book_request.accepted_by_id = current_user.id
        book_request.accepted_at = datetime.now(timezone.utc)
        book_request.updated_at = datetime.now(timezone.utc)
        await send_book_request_notification(
            db=db,
            book_request=book_request,
            new_status="accepted",
            responder_user=current_user
        )
        
        db.commit()
        db.refresh(book_request)

        
        db.commit()
        db.refresh(book_request)
        
        return {
            "status": "success",
            "message": f"Request accepted by {current_user.username}",
            "book_request": book_request.to_dict()
        }
    
    except HTTPException as he:
        raise he
    except Exception as e:
        db.rollback()
        logger.error(f"Error accepting book request: {str(e)}")
        raise HTTPException(status_code=500, detail="Error accepting request")


@book_request_router.post("/{request_id}/fulfill")
@verify_role_permission(["creator", "team"])
async def fulfill_book_request(
    request_id: int,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Mark a book request as fulfilled (for team members and creators)."""
    try:
        # Start with a clean transaction state
        db.rollback()

        # Import text for SQL
        from sqlalchemy import text

        creator_id = current_user.id if current_user.is_creator else current_user.created_by
        
        # Get book request using raw SQL
        query = text("""
        SELECT id, user_id, title, author, link, description, status, 
               created_at, updated_at, responded_by_id, response_message, 
               response_date, month_year, accepted_by_id, accepted_at
        FROM book_requests
        WHERE id = :request_id
        """)
        
        result = db.execute(query, {"request_id": request_id})
        book_request = result.fetchone()
        
        if not book_request:
            raise HTTPException(status_code=404, detail="Book request not found")
            
        # Validate current status - can only fulfill if status is 'approved'
        if book_request.status != 'approved':
            raise HTTPException(
                status_code=400,
                detail=f"Cannot fulfill this request. Current status is '{book_request.status}'. "
                       f"Only approved requests can be fulfilled."
            )
        
        # Check if the request is for a user under this creator
        user_query = text("""
        SELECT id, email, username, role, created_by
        FROM users
        WHERE id = :user_id
        """)
        
        user_result = db.execute(user_query, {"user_id": book_request.user_id})
        requester = user_result.fetchone()
        
        if not requester:
            raise HTTPException(status_code=404, detail="Requester not found")
        
        # Check user's creator
        if requester.created_by != creator_id and requester.id != creator_id:
            raise HTTPException(status_code=403, detail="Access denied to this request")
        
        # Get current time
        now = datetime.now(timezone.utc)
        
        # Update book request using raw SQL - set status to 'fulfilled'
        update_query = text("""
        UPDATE book_requests
        SET status = 'fulfilled',
            updated_at = :updated_at
        WHERE id = :request_id
        """)
        
        db.execute(update_query, {
            "updated_at": now,
            "request_id": request_id
        })

        # Log activity for fulfillment
        try:
            action_description = f"Book request '{book_request.title}' by {book_request.author} was fulfilled"

            await log_activity_isolated(
                user_id=current_user.id,
                action_type=AuditLogType.UPDATE,
                table_name="book_requests",
                record_id=str(request_id),
                description=action_description,
                old_values={"status": book_request.status},
                new_values={"status": "fulfilled"},
                ip_address=None,
                user_agent=None
            )
        except Exception as log_error:
            logger.error(f"Failed to log activity for book request fulfillment: {str(log_error)}")

        await send_book_request_notification(
            db=db,
            book_request=book_request,
            new_status="fulfilled",
            responder_user=current_user
        )
        
        db.commit()        
        # Get the updated book request
        updated_query = text("""
        SELECT id, user_id, title, author, link, description, status, 
               created_at, updated_at, responded_by_id, response_message, 
               response_date, month_year, accepted_by_id, accepted_at
        FROM book_requests
        WHERE id = :request_id
        """)
        
        updated_result = db.execute(updated_query, {"request_id": request_id})
        updated_book_request = updated_result.fetchone()
        
        # Convert to dictionary
        book_request_dict = {
            "id": updated_book_request.id,
            "user_id": updated_book_request.user_id,
            "title": updated_book_request.title,
            "author": updated_book_request.author,
            "link": updated_book_request.link,
            "description": updated_book_request.description,
            "status": updated_book_request.status,
            "created_at": updated_book_request.created_at.isoformat() if updated_book_request.created_at else None,
            "updated_at": updated_book_request.updated_at.isoformat() if updated_book_request.updated_at else None,
            "responded_by_id": updated_book_request.responded_by_id,
            "response_message": updated_book_request.response_message,
            "response_date": updated_book_request.response_date.isoformat() if updated_book_request.response_date else None,
            "month_year": updated_book_request.month_year,
            "accepted_by_id": updated_book_request.accepted_by_id,
            "accepted_at": updated_book_request.accepted_at.isoformat() if updated_book_request.accepted_at else None
        }
        
        return {
            "status": "success",
            "message": f"Book request marked as fulfilled by {current_user.username}",
            "book_request": book_request_dict
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        db.rollback()
        logger.error(f"Error fulfilling book request: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fulfilling book request")

@book_request_router.post("/chapters-settings")
@verify_role_permission(["creator"])
async def update_chapters_settings(
    settings: Dict[str, Any],
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Update chapters allowed per book request for a tier - FIXED"""
    try:
        creator_id = current_user.id
        tier_title = settings.get('tier_id')
        chapters_allowed = settings.get('chapters_allowed_per_book_request', 0)
        
        logger.info(f"Updating chapters settings:")
        logger.info(f"Tier: {tier_title}")
        logger.info(f"Chapters allowed per book request: {chapters_allowed}")

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
                chapters_allowed_per_book_request=chapters_allowed,
                is_active=True,
                # For team tier, use special handling
                patreon_tier_id=None if is_team_tier else tier_title,
                amount_cents=0 if is_team_tier else None
            )
            db.add(campaign_tier)
            logger.info(f"Created new campaign tier: {tier_title} with chapters: {chapters_allowed}")
        else:
            campaign_tier.chapters_allowed_per_book_request = chapters_allowed
            logger.info(f"Updated existing campaign tier: {tier_title} with chapters: {chapters_allowed}")

        db.flush()

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
        
        for user in users_to_update:
            try:
                # ✅ FIXED: Use the same pattern as downloads settings
                current_data = user.patreon_tier_data or {}
                updated_data = current_data.copy()  # ✅ Preserves ALL existing data including usage
                
                # ✅ FIXED: Only update chapters allowance, preserve all usage
                updated_data.update({
                    'chapters_allowed_per_book_request': chapters_allowed,
                    # ✅ CRITICAL: No book_requests_used = 0 line!
                    # ✅ CRITICAL: No album_downloads_used = 0 line!  
                    # ✅ CRITICAL: No track_downloads_used = 0 line!
                    # All usage is preserved automatically from current_data.copy()
                })
                
                user.patreon_tier_data = updated_data
                update_count += 1
                
                logger.info(f"Updated user {user.email}: chapters per book request: {chapters_allowed}")

            except Exception as e:
                logger.error(f"Error processing user {user.email}: {str(e)}")
                continue

        db.commit()
        
        return {
            "status": "success",
            "message": f"Successfully updated tier and {update_count} users",
            "chapters_allowed_per_book_request": chapters_allowed,
            "tier_title": tier_title
        }

    except Exception as e:
        db.rollback()
        logger.error(f"Error updating chapters settings: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@book_request_router.post("/{request_id}/reply")
async def reply_to_book_request_response(
    request_id: int,
    user_reply: str = Form(...),
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Allow users to reply to admin responses with WebSocket broadcasting."""
    try:
        db.rollback()
        
        from sqlalchemy import text
        
        # Get book request
        query = text("""
        SELECT id, user_id, title, author, link, description, status, 
               created_at, updated_at, responded_by_id, response_message, 
               response_date, month_year, accepted_by_id, accepted_at, user_reply
        FROM book_requests
        WHERE id = :request_id
        """)
        
        result = db.execute(query, {"request_id": request_id})
        book_request = result.fetchone()
        
        if not book_request:
            raise HTTPException(status_code=404, detail="Book request not found")
        
        # Validation checks (existing code)
        if book_request.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="You can only reply to your own book requests")
        
        if not book_request.response_message:
            raise HTTPException(status_code=400, detail="No admin response to reply to")
        
        if book_request.status == 'rejected':
            raise HTTPException(status_code=400, detail="Cannot reply to rejected requests")
        
        if book_request.user_reply:
            raise HTTPException(status_code=400, detail="You have already replied to this response. Wait for admin to respond before replying again.")
        
        # Update with user reply
        now = datetime.now(timezone.utc)
        
        update_query = text("""
        UPDATE book_requests
        SET user_reply = :user_reply,
            updated_at = :updated_at
        WHERE id = :request_id
        """)
        
        db.execute(update_query, {
            "user_reply": user_reply,
            "updated_at": now,
            "request_id": request_id
        })
        
        # Send notification to admin
        try:
            if book_request.responded_by_id:
                admin_query = text("SELECT id, username, email FROM users WHERE id = :admin_id")
                admin_result = db.execute(admin_query, {"admin_id": book_request.responded_by_id})
                admin_user = admin_result.fetchone()
                
                if admin_user:
                    await send_book_request_notification(
                        db=db,
                        book_request=book_request,
                        new_status="user_reply",
                        responder_user=current_user,
                        custom_message=f"User {current_user.username} replied to their book request '{book_request.title}': {user_reply[:100]}{'...' if len(user_reply) > 100 else ''}"
                    )
        except Exception as notif_error:
            logger.error(f"Notification error but continuing: {str(notif_error)}")
        
        db.commit()
        
        # Get updated book request
        updated_result = db.execute(query, {"request_id": request_id})
        updated_book_request = updated_result.fetchone()
        
        # Convert to dictionary
        book_request_dict = {
            "id": updated_book_request.id,
            "user_id": updated_book_request.user_id,
            "title": updated_book_request.title,
            "author": updated_book_request.author,
            "link": updated_book_request.link,
            "description": updated_book_request.description,
            "status": updated_book_request.status,
            "created_at": updated_book_request.created_at.isoformat() if updated_book_request.created_at else None,
            "updated_at": updated_book_request.updated_at.isoformat() if updated_book_request.updated_at else None,
            "responded_by_id": updated_book_request.responded_by_id,
            "response_message": updated_book_request.response_message,
            "response_date": updated_book_request.response_date.isoformat() if updated_book_request.response_date else None,
            "month_year": updated_book_request.month_year,
            "accepted_by_id": updated_book_request.accepted_by_id,
            "accepted_at": updated_book_request.accepted_at.isoformat() if updated_book_request.accepted_at else None,
            "user_reply": updated_book_request.user_reply
        }
        
        # Get creator ID
        creator_id = current_user.created_by if current_user.created_by else current_user.id

        # Broadcast via WebSocket using helper function
        await broadcast_book_request_update(
            book_request_dict=book_request_dict,
            action="reply_added",
            user_id=current_user.id,
            creator_id=creator_id,
            db=db
        )
        
        return {
            "status": "success",
            "message": "Reply submitted successfully",
            "book_request": book_request_dict
        }
    
    except HTTPException as he:
        raise he
    except Exception as e:
        db.rollback()
        logger.error(f"Error submitting user reply: {str(e)}")
        raise HTTPException(status_code=500, detail="Error submitting reply")
