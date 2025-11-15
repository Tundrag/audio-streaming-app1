#comment_routes.py
import re
from fastapi import APIRouter, Depends, HTTPException, Form, Request, Response, BackgroundTasks
from sqlalchemy import and_, or_, desc, func, text
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional, Dict, Any, Set
from datetime import datetime, timezone
from fastapi import WebSocket, WebSocketDisconnect, Query
from typing import Dict, Set
import asyncio
import json
import logging
import uuid
import asyncio

from models import Comment, CommentLike, User, Track, Album, NotificationType
from database import get_db, SessionLocal
from auth import login_required
from redis_state.config import redis_client
from websocket_manager import WebSocketManager

# Create a router for comment-related endpoints
comment_router = APIRouter(prefix="/api")

# Regular expression to find mentions in text
MENTION_PATTERN = r'@(\w+)'

#=============================================
# MENTION PROCESSING
#=============================================

# Create singleton WebSocket manager for track comments with Redis pub/sub
comment_manager = WebSocketManager(channel="track_comments")

# Add the WebSocket endpoint
@comment_router.websocket("/ws/track/{track_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    track_id: str,
    user_id: int = Query(..., description="User ID for authentication")
):
    """WebSocket endpoint for live comment updates on a specific track"""
    # Create manual session for auth only
    db = SessionLocal()
    try:
        # Get user by ID
        current_user = db.query(User).filter(User.id == user_id).first()
        if not current_user:
            await websocket.close(code=1008, reason="User not found")
            return

        # Verify track exists
        track = db.query(Track).filter(Track.id == track_id).first()
        if not track:
            await websocket.close(code=1008, reason="Track not found")
            return
    finally:
        # Close DB BEFORE entering loop
        db.close()

    # Connect using centralized WebSocket manager
    # Use composite key "track:{track_id}:user:{user_id}" to support multiple tracks per user
    user_key = f"track:{track_id}:user:{user_id}"

    try:
        await websocket.accept()

        await comment_manager.connect(websocket, user_id=user_key)

        # Send initial connection message with track context
        await websocket.send_json({
            "type": "connected",
            "track_id": track_id,
            "message": "Connected to comment updates"
        })

        # Keep connection alive and handle incoming messages
        while True:
            try:
                data = await websocket.receive_json()

                # Handle typing indicators
                if data.get("type") == "typing":
                    await comment_manager.broadcast({
                        "type": "user_typing",
                        "track_id": track_id,
                        "user_id": current_user.id,
                        "username": current_user.username,
                        "is_typing": data.get("is_typing", False)
                    })

            except WebSocketDisconnect:
                break
            except Exception as e:
                logging.error(f"WebSocket message error: {e}")
                break

    except Exception as e:
        logging.error(f"WebSocket connection error: {e}")
        try:
            await websocket.close(code=1011, reason="Internal error")
        except:
            pass
    finally:
        comment_manager.disconnect(websocket)


async def process_mentions(
    db: Session,
    comment_id: int,
    content: str,
    track_id: str,
    sender_id: int
) -> List[int]:
    """
    Process @mentions in comment content and send notifications
    
    Args:
        db: Database session
        comment_id: ID of the comment containing mentions
        content: Comment text content
        track_id: ID of the track being commented on
        sender_id: ID of the user who made the comment
        
    Returns:
        List of notification IDs created
    """
    try:
        # Extract all mentions from the content
        mentions = set(re.findall(MENTION_PATTERN, content))
        if not mentions:
            return []
            
        logging.info(f"Found mentions in comment {comment_id}: {mentions}")
        
        # Get the commenter
        sender = db.query(User).filter(User.id == sender_id).first()
        if not sender:
            logging.warning(f"Sender {sender_id} not found for mentions")
            return []
            
        # Get the track and album
        track = db.query(Track).filter(Track.id == track_id).first()
        if not track:
            logging.warning(f"Track {track_id} not found for mentions")
            return []
            
        # Get album to find creator
        album = db.query(Album).filter(Album.id == track.album_id).first()
        if not album or not album.created_by_id:
            logging.warning(f"Album not found or has no creator for track {track_id}")
            return []
            
        # Get creator
        creator_id = album.created_by_id
        creator = db.query(User).filter(User.id == creator_id).first()
        if not creator:
            logging.warning(f"Creator with ID {creator_id} not found")
            return []
            
        # Track which users have been notified
        notified_users = set()
        notification_ids = []
        
        # Handle special mentions
        if "creator" in mentions:
            # Don't notify if sender is mentioning themselves
            if creator_id != sender_id:
                notif_id = await create_notification(
                    db=db,
                    user_id=creator_id,
                    notification_type="mention",
                    title="You Were Mentioned",
                    content=f"{sender.username} mentioned you in a comment on {track.title}",
                    sender_id=sender_id,
                    notification_data={
                        "track_id": str(track_id),
                        "comment_id": comment_id,
                        "mention_type": "creator"
                    }
                )
                notification_ids.append(notif_id)
                notified_users.add(creator_id)
                logging.info(f"Notified creator {creator_id} about mention")
                
        if "team" in mentions:
            # Get all team members
            team_members = db.query(User).filter(
                and_(
                    User.created_by == creator_id,
                    User.is_team == True,
                    User.is_active == True,
                    User.id != sender_id  # Don't notify the sender
                )
            ).all()
            
            # Notify all team members
            for team_member in team_members:
                if team_member.id not in notified_users:
                    notif_id = await create_notification(
                        db=db,
                        user_id=team_member.id,
                        notification_type="mention",
                        title="Team Mention",
                        content=f"{sender.username} mentioned the team in a comment on {track.title}",
                        sender_id=sender_id,
                        notification_data={
                            "track_id": str(track_id),
                            "comment_id": comment_id,
                            "mention_type": "team"
                        }
                    )
                    notification_ids.append(notif_id)
                    notified_users.add(team_member.id)
            
            # Also notify creator if they weren't already notified
            if creator_id != sender_id and creator_id not in notified_users:
                notif_id = await create_notification(
                    db=db,
                    user_id=creator_id,
                    notification_type="mention",
                    title="Team Mention",
                    content=f"{sender.username} mentioned the team in a comment on {track.title}",
                    sender_id=sender_id,
                    notification_data={
                        "track_id": str(track_id),
                        "comment_id": comment_id,
                        "mention_type": "team"
                    }
                )
                notification_ids.append(notif_id)
                notified_users.add(creator_id)
                logging.info(f"Notified creator {creator_id} about team mention")
                
        # Process regular @username mentions
        usernames = [mention for mention in mentions if mention not in ["creator", "team"]]
        if usernames:
            # Get all users with matching usernames
            mentioned_users = db.query(User).filter(
                and_(
                    User.username.in_(usernames),
                    User.is_active == True,
                    User.id != sender_id  # Don't notify the sender
                )
            ).all()
            
            # Notify each mentioned user
            for user in mentioned_users:
                if user.id not in notified_users:
                    notif_id = await create_notification(
                        db=db,
                        user_id=user.id,
                        notification_type="mention",
                        title="You Were Mentioned",
                        content=f"{sender.username} mentioned you in a comment on {track.title}",
                        sender_id=sender_id,
                        notification_data={
                            "track_id": str(track_id),
                            "comment_id": comment_id,
                            "mention_type": "user"
                        }
                    )
                    notification_ids.append(notif_id)
                    notified_users.add(user.id)
                    logging.info(f"Notified user {user.id} about mention")
        
        return notification_ids

    except Exception as e:
        logging.error(f"Error processing mentions: {str(e)}")
        return []
    finally:
        db.close()
#=============================================
# NOTIFICATION FUNCTIONS
#=============================================

async def create_notification(
    db: Session,
    user_id: int,
    notification_type: str,
    content: str,
    title: str = None,
    sender_id: Optional[int] = None,
    notification_data: Dict[str, Any] = None
) -> int:
    """
    Create a notification using raw SQL to avoid enum validation issues
    """
    try:
        # Generate UUID
        notification_uuid = str(uuid.uuid4())
        
        # Convert notification data to JSON string
        notification_data_json = json.dumps(notification_data or {})
        
        # Get default title if not provided
        if title is None:
            title = get_notification_title(notification_type.lower())
        
        # Use raw SQL to insert notification - use lowercase for notification type
        result = db.execute(
            text("""
            INSERT INTO notifications 
            (uuid, user_id, sender_id, type, title, content, is_read, notification_data, created_at) 
            VALUES (:uuid, :user_id, :sender_id, :type, :title, :content, :is_read, :notification_data, :created_at)
            RETURNING id
            """),
            {
                "uuid": notification_uuid,
                "user_id": user_id,
                "sender_id": sender_id,
                "type": notification_type.lower(),  # Ensure lowercase for DB consistency
                "title": title,
                "content": content,
                "is_read": False,
                "notification_data": notification_data_json,
                "created_at": datetime.now(timezone.utc)
            }
        )
        
        # Get the ID of the newly created notification
        notification_id = result.scalar()
        
        # Commit the transaction
        db.commit()
        
        logging.info(f"Created notification (ID: {notification_id}) for user {user_id}, type: {notification_type}")
        return notification_id
    except Exception as e:
        db.rollback()
        logging.error(f"Error creating notification: {str(e)}")
        # Return 0 instead of raising an exception to prevent blocking the main flow
        return 0

def get_notification_title(notification_type: str) -> str:
    """Get appropriate title for notification type"""
    type_titles = {
        "comment": "New Comment",
        "reply": "New Reply",
        "like": "New Like",
        "share": "New Share",
        "comment_like": "Comment Liked",
        "mention": "New Mention",
        "new_content": "New Content",
        "tier_update": "Tier Update",
        "system": "System Notification"
    }
    
    return type_titles.get(notification_type.lower(), "New Notification")

async def create_comment_notifications(comment_id: int, user_id: int, track_id: str, parent_id: Optional[int] = None):
    """Background task to create notifications for a new comment"""
    logging.info(f"Starting notification task for comment {comment_id}")
    db = SessionLocal()
    
    try:
        # Get necessary data
        comment = db.query(Comment).filter(Comment.id == comment_id).first()
        if not comment:
            logging.warning(f"Comment {comment_id} not found for notification")
            return
            
        commenter = db.query(User).filter(User.id == user_id).first()
        if not commenter:
            logging.warning(f"User {user_id} not found for notification")
            return
            
        track = db.query(Track).filter(Track.id == track_id).first()
        if not track:
            logging.warning(f"Track {track_id} not found for notification")
            return
            
        # Get album to find creator
        album = db.query(Album).filter(Album.id == track.album_id).first()
        if not album or not album.created_by_id:
            logging.warning(f"Album not found or has no creator for track {track_id}")
            return
            
        creator_id = album.created_by_id
        
        notification_data = {
            "track_id": str(track_id),
            "comment_id": comment_id
        }
        
        # 1. Notify parent comment author if this is a reply
        if parent_id:
            parent_comment = db.query(Comment).filter(Comment.id == parent_id).first()
            if parent_comment and parent_comment.user_id != user_id:  # Don't notify self
                await create_notification(
                    db=db,
                    user_id=parent_comment.user_id,
                    notification_type="reply",
                    title="New Reply",
                    content=f"{commenter.username} replied to your comment",
                    sender_id=user_id,
                    notification_data={
                        "track_id": str(track_id),
                        "comment_id": comment_id,
                        "parent_id": parent_id
                    }
                )
                logging.info(f"Created reply notification for user {parent_comment.user_id}")
        
        # 2. Notify creator about comment (if they're not the commenter)
        if creator_id != user_id:
            await create_notification(
                db=db,
                user_id=creator_id,
                notification_type="comment",
                title="New Comment",
                content=f"{commenter.username} commented on {track.title}",
                sender_id=user_id,
                notification_data=notification_data
            )
            logging.info(f"Created notification for creator {creator_id} about comment {comment_id}")
        
        # 3. FIXED: Notify team members by matching the exact pattern in your database
        from sqlalchemy import text
        
        # Exact query to match team members in your database structure
        team_query = text("""
        SELECT id, username, email 
        FROM users 
        WHERE created_by = :creator_id
        AND is_active = true 
        AND id != :sender_id
        AND patreon_tier_data->>'title' = 'Team Member'
        """)
        
        team_result = db.execute(team_query, {
            "creator_id": creator_id, 
            "sender_id": user_id
        })
        
        team_members = list(team_result)
        logging.info(f"Found {len(team_members)} team members to notify for creator {creator_id}")
        
        for team_member in team_members:
            logging.info(f"Sending notification to team member {team_member.username} (ID: {team_member.id})")
            
            await create_notification(
                db=db,
                user_id=team_member.id,
                notification_type="comment",
                title="New Comment",
                content=f"{commenter.username} commented on {track.title}",
                sender_id=user_id,
                notification_data=notification_data
            )
            
        if team_members:
            logging.info(f"Created notifications for {len(team_members)} team members")
                
    except Exception as e:
        logging.error(f"Error in comment notification task: {str(e)}", exc_info=True)
    finally:
        db.close()

async def create_comment_like_notification(comment_id: int, liker_id: int):
    """Background task to create notification when a comment is liked"""
    db = SessionLocal()
    
    try:
        # Get the comment
        comment = db.query(Comment).filter(Comment.id == comment_id).first()
        if not comment:
            logging.warning(f"Comment {comment_id} not found for like notification")
            return
            
        # Don't notify if liking your own comment
        if comment.user_id == liker_id:
            return
            
        # Get commenter and liker
        commenter = db.query(User).filter(User.id == comment.user_id).first()
        liker = db.query(User).filter(User.id == liker_id).first()
        
        if not commenter or not liker:
            logging.warning(f"User not found for comment like notification")
            return
            
        # Get track for context
        track = db.query(Track).filter(Track.id == comment.track_id).first()
        if not track:
            logging.warning(f"Track not found for comment like notification")
            return
        
        # Create notification for comment author - CHANGED: using 'like' instead of 'comment_like'
        await create_notification(
            db=db,
            user_id=comment.user_id,
            notification_type="like",  # Using standard 'like' type instead of 'comment_like'
            title="Comment Liked",
            content=f"{liker.username} liked your comment on {track.title}",
            sender_id=liker_id,
            notification_data={
                "track_id": str(comment.track_id),
                "comment_id": comment_id,
                "is_comment_like": True  # Add flag to distinguish from track likes
            }
        )
        
        logging.info(f"Created comment like notification for user {comment.user_id}")
                
    except Exception as e:
        logging.error(f"Error in comment like notification task: {str(e)}")
    finally:
        db.close()

async def create_track_like_notification(track_id: str, liker_id: int):
    """Background task to create notification when a track is liked"""
    db = SessionLocal()
    
    try:
        # Get the track
        track = db.query(Track).filter(Track.id == track_id).first()
        if not track:
            logging.warning(f"Track {track_id} not found for like notification")
            return
            
        # Get album to find creator
        album = db.query(Album).filter(Album.id == track.album_id).first()
        if not album or not album.created_by_id:
            logging.warning(f"Album not found or has no creator for track {track_id}")
            return
            
        creator_id = album.created_by_id
        
        # Don't notify if creator is liking their own track
        if creator_id == liker_id:
            return
            
        # Get liker info
        liker = db.query(User).filter(User.id == liker_id).first()
        if not liker:
            logging.warning(f"Liker user {liker_id} not found")
            return
        
        # Create notification for track creator
        await create_notification(
            db=db,
            user_id=creator_id,
            notification_type="like",
            title="New Like",
            content=f"{liker.username} liked your track {track.title}",
            sender_id=liker_id,
            notification_data={
                "track_id": str(track_id)
            }
        )
        
        logging.info(f"Created track like notification for creator {creator_id}")
                
    except Exception as e:
        logging.error(f"Error in track like notification task: {str(e)}")
    finally:
        db.close()

async def create_track_share_notification(track_id: str, sharer_id: int, platform: str = "unknown"):
    """Background task to create notification when a track is shared"""
    db = SessionLocal()
    
    try:
        # Get the track
        track = db.query(Track).filter(Track.id == track_id).first()
        if not track:
            logging.warning(f"Track {track_id} not found for share notification")
            return
            
        # Get album to find creator
        album = db.query(Album).filter(Album.id == track.album_id).first()
        if not album or not album.created_by_id:
            logging.warning(f"Album not found or has no creator for track {track_id}")
            return
            
        creator_id = album.created_by_id
        
        # Don't notify if creator is sharing their own track
        if creator_id == sharer_id:
            return
            
        # Get sharer info
        sharer = db.query(User).filter(User.id == sharer_id).first()
        if not sharer:
            logging.warning(f"Sharer user {sharer_id} not found")
            return
        
        # Format platform name nicely
        platform_display = platform.capitalize() if platform != "unknown" else "social media"
        
        # Create notification for track creator
        await create_notification(
            db=db,
            user_id=creator_id,
            notification_type="share",
            title="New Share",
            content=f"{sharer.username} shared your track {track.title} on {platform_display}",
            sender_id=sharer_id,
            notification_data={
                "track_id": str(track_id),
                "platform": platform
            }
        )
        
        logging.info(f"Created track share notification for creator {creator_id}")
                
    except Exception as e:
        logging.error(f"Error in track share notification task: {str(e)}")
    finally:
        db.close()

#=============================================
# TRACK METRICS ENDPOINTS
#=============================================

@comment_router.get("/tracks/{track_id}/metrics")
async def get_track_metrics(
    track_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Get likes, comments, and shares metrics for a track"""
    # Verify track exists
    track = db.query(Track).filter(Track.id == track_id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    # Format keys for Redis
    track_likes_key = f"track:{track_id}:likes"
    track_likes_users_key = f"track:{track_id}:likes:users"
    track_shares_key = f"track:{track_id}:shares"
    
    # Get track like count
    likes_count = redis_client.scard(track_likes_users_key) or 0
    
    # Check if current user liked this track
    is_liked = redis_client.sismember(track_likes_users_key, str(current_user.id)) or False
    
    # Get share count
    shares_count = redis_client.get(track_shares_key) or 0
    if shares_count:
        shares_count = int(shares_count)
    else:
        shares_count = 0
    
    # Get comment count
    comment_count = db.query(func.count(Comment.id)).filter(Comment.track_id == track_id).scalar() or 0
    
    return {
        "likes": likes_count,
        "comments": comment_count,
        "shares": shares_count,
        "is_liked": is_liked
    }

@comment_router.post("/tracks/{track_id}/like")
async def like_track(
    track_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Like a track and notify creator"""
    # Verify track exists
    track = db.query(Track).filter(Track.id == track_id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    
    # Format keys for Redis
    track_likes_key = f"track:{track_id}:likes"
    track_likes_users_key = f"track:{track_id}:likes:users"
    
    # Check if user already liked this track
    already_liked = redis_client.sismember(track_likes_users_key, str(current_user.id))
    
    # Only send notification if this is a new like
    if not already_liked:
        # Add like notification in background
        background_tasks.add_task(
            create_track_like_notification,
            track_id=track_id,
            liker_id=current_user.id
        )
    
    # Add user to the set of users who liked this track
    redis_client.sadd(track_likes_users_key, str(current_user.id))
    
    # Get updated count
    likes_count = redis_client.scard(track_likes_users_key) or 0
    
    return {"success": True, "likes": likes_count}

@comment_router.delete("/tracks/{track_id}/like")
async def unlike_track(
    track_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Unlike a track"""
    # Verify track exists
    track = db.query(Track).filter(Track.id == track_id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    
    # Format keys for Redis
    track_likes_key = f"track:{track_id}:likes"
    track_likes_users_key = f"track:{track_id}:likes:users"
    
    # Remove user from the set of users who liked this track
    redis_client.srem(track_likes_users_key, str(current_user.id))
    
    # Get updated count
    likes_count = redis_client.scard(track_likes_users_key) or 0
    
    return {"success": True, "likes": likes_count}

@comment_router.post("/tracks/{track_id}/share")
async def share_track(
    track_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Track when a user shares content and notify creator"""
    # Verify track exists
    track = db.query(Track).filter(Track.id == track_id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    
    # Get share data from request
    data = await request.json()
    platform = data.get("platform", "unknown")
    
    # Format keys for Redis
    track_shares_key = f"track:{track_id}:shares"
    track_shares_platform_key = f"track:{track_id}:shares:{platform}"
    
    # Increment share counts
    shares_count = redis_client.incr(track_shares_key)
    redis_client.incr(track_shares_platform_key)
    
    # Add share notification in background
    background_tasks.add_task(
        create_track_share_notification,
        track_id=track_id,
        sharer_id=current_user.id,
        platform=platform
    )
    
    return {"success": True, "shares": shares_count, "platform": platform}

#=============================================
# COMMENT ENDPOINTS
#=============================================

@comment_router.get("/tracks/{track_id}/comments")
async def get_track_comments(
    track_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Get all comments for a track, including replies"""
    # Verify track exists and user has access
    track = db.query(Track).filter(Track.id == track_id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    
    # Fetch comments with author info eagerly loaded to avoid N+1 queries
    comments = (
        db.query(Comment)
        .options(joinedload(Comment.user))
        .filter(Comment.track_id == track_id)
        .order_by(Comment.created_at.desc())
        .all()
    )

    if not comments:
        return []

    comment_ids = [comment.id for comment in comments]

    # Aggregate like counts in a single query
    like_counts = {
        comment_id: count
        for comment_id, count in (
            db.query(CommentLike.comment_id, func.count(CommentLike.id))
            .filter(CommentLike.comment_id.in_(comment_ids))
            .group_by(CommentLike.comment_id)
            .all()
        )
    }

    # Determine which comments the current user liked
    user_likes = {
        row.comment_id
        for row in (
            db.query(CommentLike.comment_id)
            .filter(
                CommentLike.comment_id.in_(comment_ids),
                CommentLike.user_id == current_user.id
            )
            .all()
        )
    }

    # Format comments with user info and like status
    result = []
    for comment in comments:
        user = comment.user
        result.append({
            "id": comment.id,
            "user_id": comment.user_id,
            "username": user.username if user else "Unknown User",
            "author_is_creator": bool(user.is_creator) if user else False,
            "author_is_team": bool(user.is_team) if user else False,
            "track_id": str(comment.track_id),
            "parent_id": comment.parent_id,
            "content": comment.content,
            "timestamp": comment.timestamp,
            "is_edited": comment.is_edited,
            "created_at": comment.created_at.isoformat() if comment.created_at else None,
            "last_edited_at": comment.last_edited_at.isoformat() if comment.last_edited_at else None,
            "user_has_liked": comment.id in user_likes,
            "like_count": like_counts.get(comment.id, 0)
        })
    
    return result

@comment_router.post("/tracks/{track_id}/comments")
async def create_comment(
    track_id: str,
    background_tasks: BackgroundTasks,
    content: str = Form(...),
    parent_id: Optional[int] = Form(None),
    timestamp: Optional[float] = Form(0),
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Create a new comment or reply for a track"""
    try:
        # Verify track exists
        track = db.query(Track).filter(Track.id == track_id).first()
        if not track:
            raise HTTPException(status_code=404, detail="Track not found")
        
        # If this is a reply, verify parent comment exists
        if parent_id:
            parent_comment = db.query(Comment).filter(Comment.id == parent_id).first()
            if not parent_comment:
                raise HTTPException(status_code=404, detail="Parent comment not found")
        
        # Validate timestamp
        valid_timestamp = validate_timestamp(timestamp, track_id, db)
        
        # Create new comment with validated timestamp
        new_comment = Comment(
            user_id=current_user.id,
            track_id=track_id,
            parent_id=parent_id,
            content=content,
            timestamp=valid_timestamp,
            created_at=datetime.now(timezone.utc)
        )
        
        # Save the comment
        db.add(new_comment)
        db.commit()
        db.refresh(new_comment)
        
        # Prepare comment data for response and broadcast
        comment_data = {
            "id": new_comment.id,
            "user_id": new_comment.user_id,
            "username": current_user.username,
            "author_is_creator": current_user.is_creator,
            "author_is_team": current_user.is_team,
            "track_id": str(new_comment.track_id),
            "parent_id": new_comment.parent_id,
            "content": new_comment.content,
            "timestamp": new_comment.timestamp,
            "is_edited": False,
            "created_at": new_comment.created_at.isoformat() if new_comment.created_at else None,
            "like_count": 0,
            "user_has_liked": False
        }
        
        # Broadcast new comment via WebSocket to all replicas
        await comment_manager.broadcast({
            "type": "new_comment",
            "track_id": track_id,
            "comment": comment_data
        })

        # Process mentions and broadcast mention notifications
        mentions = set(re.findall(MENTION_PATTERN, content))
        if mentions:
            # Send WebSocket notifications for mentions
            for mention in mentions:
                if mention == "creator":
                    # Get album creator
                    track_obj = db.query(Track).filter(Track.id == track_id).first()
                    if track_obj:
                        album = db.query(Album).filter(Album.id == track_obj.album_id).first()
                        if album and album.created_by_id != current_user.id:
                            await comment_manager.send_to_user(
                                user_id=str(album.created_by_id),
                                message={
                                    "type": "mention",
                                    "track_id": track_id,
                                    "comment": comment_data,
                                    "from_user": current_user.username
                                }
                            )
                else:
                    # Regular user mention
                    mentioned_user = db.query(User).filter(
                        User.username.ilike(mention),
                        User.is_active == True
                    ).first()
                    if mentioned_user and mentioned_user.id != current_user.id:
                        await comment_manager.send_to_user(
                            user_id=str(mentioned_user.id),
                            message={
                                "type": "mention",
                                "track_id": track_id,
                                "comment": comment_data,
                                "from_user": current_user.username
                            }
                        )
        
        # Add notification tasks to background
        background_tasks.add_task(
            create_comment_notifications, 
            comment_id=new_comment.id,
            user_id=current_user.id,
            track_id=track_id,
            parent_id=parent_id
        )
        
        background_tasks.add_task(
            process_mentions,
            db=SessionLocal(),
            comment_id=new_comment.id,
            content=content,
            track_id=track_id,
            sender_id=current_user.id
        )
        
        return comment_data
        
    except Exception as e:
        db.rollback()
        logging.error(f"Error creating comment: {str(e)}")
        raise HTTPException(status_code=500, detail="An error occurred while creating the comment")


@comment_router.delete("/comments/{comment_id}")
async def delete_comment(
    comment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Delete a comment with proper permission handling"""
    try:
        # Get the comment
        comment = db.query(Comment).filter(Comment.id == comment_id).first()
        if not comment:
            raise HTTPException(status_code=404, detail="Comment not found")
        
        # Store track_id before deletion for WebSocket broadcast
        track_id = str(comment.track_id)
        
        # Get the comment author
        comment_author = db.query(User).filter(User.id == comment.user_id).first()
        if not comment_author:
            raise HTTPException(status_code=404, detail="Comment author not found")
        
        # Permission logic:
        # 1. Creators can delete any comment.
        # 2. Team members can delete their own comments and comments from regular users.
        # 3. Regular users can only delete their own comments.
        has_permission = False
        
        if current_user.is_creator:
            has_permission = True
        elif current_user.is_team:
            # Team members can delete their own comments OR comments from regular users
            if current_user.id == comment.user_id or (not comment_author.is_creator and not comment_author.is_team):
                has_permission = True
        elif current_user.id == comment.user_id:
            has_permission = True
        
        if not has_permission:
            raise HTTPException(
                status_code=403, 
                detail="You don't have permission to delete this comment"
            )
        
        # Delete any likes associated with this comment
        db.query(CommentLike).filter(CommentLike.comment_id == comment.id).delete()
        
        # Delete the comment
        db.query(Comment).filter(Comment.id == comment.id).delete()
        
        db.commit()

        # Broadcast the deletion via WebSocket to all replicas
        await comment_manager.broadcast({
            "type": "comment_deleted",
            "track_id": track_id,
            "comment_id": comment_id
        })

        return {"success": True, "message": "Comment deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="An error occurred while deleting the comment")


@comment_router.put("/comments/{comment_id}")
async def edit_comment(
    comment_id: int,
    content: str = Form(...),
    timestamp: Optional[float] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Edit an existing comment. Users can only edit their own comments."""
    # Get the comment
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    
    # Only allow the owner of the comment to edit it
    if comment.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only edit your own comment")
    
    comment.content = content
    if timestamp is not None:
        valid_timestamp = validate_timestamp(timestamp, str(comment.track_id), db)
        comment.timestamp = valid_timestamp
    
    comment.is_edited = True
    comment.last_edited_at = datetime.now(timezone.utc)
    
    db.commit()
    db.refresh(comment)

    # Broadcast the edit via WebSocket to all replicas
    await comment_manager.broadcast({
        "type": "comment_edited",
        "track_id": str(comment.track_id),
        "comment_id": comment.id,
        "content": comment.content,
        "timestamp": comment.timestamp,
        "is_edited": True,
        "last_edited_at": comment.last_edited_at.isoformat() if comment.last_edited_at else None
    })

    return {
        "id": comment.id,
        "content": comment.content,
        "timestamp": comment.timestamp,
        "is_edited": comment.is_edited,
        "last_edited_at": comment.last_edited_at.isoformat() if comment.last_edited_at else None
    }

@comment_router.post("/comments/{comment_id}/like")
async def like_comment(
    comment_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Like a comment and notify comment author"""
    # Verify comment exists
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    
    # Check if user already liked this comment
    existing_like = db.query(CommentLike).filter(
        and_(
            CommentLike.comment_id == comment_id,
            CommentLike.user_id == current_user.id
        )
    ).first()
    
    if not existing_like:
        # Create new like
        new_like = CommentLike(
            user_id=current_user.id,
            comment_id=comment_id,
            created_at=datetime.now(timezone.utc)
        )
        db.add(new_like)
        db.commit()
        
        # Add notification in background
        background_tasks.add_task(
            create_comment_like_notification,
            comment_id=comment_id,
            liker_id=current_user.id
        )
    
    like_count = db.query(func.count(CommentLike.id)).filter(
        CommentLike.comment_id == comment_id
    ).scalar()
    
    return {"success": True, "like_count": like_count}

@comment_router.delete("/comments/{comment_id}/like")
async def unlike_comment(
    comment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Unlike a comment"""
    # Verify comment exists
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    
    user_like = db.query(CommentLike).filter(
        and_(
            CommentLike.comment_id == comment_id,
            CommentLike.user_id == current_user.id
        )
    ).first()
    
    if user_like:
        db.delete(user_like)
        db.commit()
    
    like_count = db.query(func.count(CommentLike.id)).filter(
        CommentLike.comment_id == comment_id
    ).scalar()
    
    return {"success": True, "like_count": like_count}

@comment_router.post("/comments/{comment_id}/report")
async def report_comment(
    comment_id: int,
    reason: str = Form(...),
    details: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Report a comment for moderation"""
    # Verify comment exists
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    
    # Check if user already reported this comment
    from models import CommentReport, SegmentStatus
    existing_report = db.query(CommentReport).filter(
        and_(
            CommentReport.comment_id == comment_id,
            CommentReport.user_id == current_user.id
        )
    ).first()
    
    if existing_report:
        existing_report.reason = reason
        existing_report.details = details
        existing_report.updated_at = datetime.now(timezone.utc)
    else:
        new_report = CommentReport(
            user_id=current_user.id,
            comment_id=comment_id,
            reason=reason,
            details=details,
            status=SegmentStatus.PENDING,
            created_at=datetime.now(timezone.utc)
        )
        db.add(new_report)
    
    db.commit()
    
    return {"success": True, "message": "Comment reported successfully"}

def validate_timestamp(timestamp: Optional[float], track_id: str, db: Session) -> Optional[float]:
    """
    Validates that a timestamp is within the track duration.
    Returns the timestamp if valid, otherwise None.
    """
    if timestamp is None or timestamp <= 0:
        return None
        
    # Get track duration
    track = db.query(Track).filter(Track.id == track_id).first()
    if not track:
        return None
        
    # If we have track duration metadata, validate timestamp is within range
    if hasattr(track, 'duration') and track.duration:
        if timestamp > track.duration:
            return None
    
    return timestamp

@comment_router.get("/tracks/{track_id}/comments/since/{comment_id}")
async def get_comments_since(
    track_id: str,
    comment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Get comments that are newer than the specified comment ID"""
    # Verify track exists
    track = db.query(Track).filter(Track.id == track_id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    
    # Get comments newer than the specified ID
    comments = (
        db.query(Comment)
        .filter(Comment.track_id == track_id, Comment.id > comment_id)
        .order_by(Comment.created_at.desc())
        .all()
    )
    
    # Format comments with user info and like status
    result = []
    for comment in comments:
        # Get user info
        user = db.query(User).filter(User.id == comment.user_id).first()
        
        # Check if current user has liked this comment
        user_like = db.query(CommentLike).filter(
            and_(
                CommentLike.comment_id == comment.id,
                CommentLike.user_id == current_user.id
            )
        ).first()
        
        # Count total likes
        like_count = db.query(func.count(CommentLike.id)).filter(
            CommentLike.comment_id == comment.id
        ).scalar()
        
        # Format comment data
        comment_data = {
            "id": comment.id,
            "user_id": comment.user_id,
            "username": user.username if user else "Unknown User",
            "author_is_creator": user.is_creator if user else False,
            "author_is_team": user.is_team if user else False,
            "track_id": str(comment.track_id),
            "parent_id": comment.parent_id,
            "content": comment.content,
            "timestamp": comment.timestamp,
            "is_edited": comment.is_edited,
            "created_at": comment.created_at.isoformat() if comment.created_at else None,
            "last_edited_at": comment.last_edited_at.isoformat() if comment.last_edited_at else None,
            "user_has_liked": user_like is not None,
            "like_count": like_count
        }
        
        result.append(comment_data)

    return result
