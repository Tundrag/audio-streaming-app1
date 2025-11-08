from fastapi import APIRouter, Depends, WebSocket, HTTPException, Request, Query
from fastapi.websockets import WebSocketDisconnect
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, text
from typing import Dict, List, Any, Optional
from typing import Dict, List, Any, Optional, Union
import asyncio
from fastapi.websockets import WebSocketDisconnect, WebSocketState
from datetime import datetime, timezone
from fastapi.websockets import WebSocketDisconnect, WebSocketState
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from forum_routes import get_user_forum_display_name
import logging
import json
import asyncio
from typing import Dict, Set
import uuid

from database import get_db
from models import User, Notification, NotificationType, Track, Album, Comment
from auth import login_required

# Configure logger
logger = logging.getLogger(__name__)

# Create router
notifications_router = APIRouter(prefix="/api/notifications", tags=["notifications"])

# UPDATED: Simple WebSocket manager class for notifications
class SimpleNotificationManager:
    def __init__(self):
        # User ID -> Set of WebSocket connections
        self.user_connections: Dict[int, Set[WebSocket]] = {}
        # WebSocket -> User info for cleanup
        self.connection_users: Dict[WebSocket, dict] = {}
    
    async def connect(self, websocket: WebSocket, user_id: int, user_info: dict):
        await websocket.accept()
        
        # Add to user's connections
        if user_id not in self.user_connections:
            self.user_connections[user_id] = set()
        self.user_connections[user_id].add(websocket)
        
        # Store user info
        self.connection_users[websocket] = user_info
        
        logger.info(f"User {user_info['username']} connected to notifications WebSocket")
        
        # Send connection confirmation
        await websocket.send_json({
            "type": "connected",
            "message": "Connected to live notifications"
        })
    
    def disconnect(self, websocket: WebSocket):
        user_info = self.connection_users.get(websocket)
        if user_info:
            user_id = user_info['user_id']
            
            # Remove from user connections
            if user_id in self.user_connections:
                self.user_connections[user_id].discard(websocket)
                if not self.user_connections[user_id]:
                    del self.user_connections[user_id]
            
            del self.connection_users[websocket]
            logger.info(f"User {user_info['username']} disconnected from notifications WebSocket")
    
    async def send_to_user(self, user_id: int, message: dict):
        """Send message to specific user's all connections"""
        if user_id not in self.user_connections:
            return False
        
        disconnected = set()
        sent = False
        
        for websocket in self.user_connections[user_id].copy():
            try:
                await websocket.send_json(message)
                sent = True
            except Exception as e:
                logger.error(f"Error sending to user {user_id}: {e}")
                disconnected.add(websocket)
        
        # Clean up disconnected
        for websocket in disconnected:
            self.disconnect(websocket)
        
        return sent

# Create global manager instance
simple_notification_manager = SimpleNotificationManager()

# WebSocket endpoint for real-time notifications
@notifications_router.websocket("/ws")
async def notifications_websocket(
    websocket: WebSocket,
    user_id: int = Query(..., description="User ID for authentication")
):
    """WebSocket endpoint for real-time notifications (activity logs, etc.)"""
    # Create manual session for auth only
    from database import SessionLocal
    db = SessionLocal()
    try:
        # Get user by ID
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            await websocket.close(code=1008, reason="User not found")
            return

        # Prepare user info
        user_info = {
            "user_id": user.id,
            "username": user.username,
            "is_creator": user.is_creator,
            "is_team": user.is_team
        }
    finally:
        # Close DB BEFORE entering loop
        db.close()

    # NOW enter message loop WITHOUT db
    try:
        await simple_notification_manager.connect(websocket, user_info["user_id"], user_info)

        try:
            while True:
                data = await websocket.receive_text()

                # Handle ping/pong for keepalive
                if data == "pong":
                    continue

                # Send ping periodically
                if data == "ping":
                    await websocket.send_text("pong")

        except WebSocketDisconnect:
            logger.info(f"WebSocket disconnected for user {user_info['username']}")
        except Exception as e:
            logger.error(f"WebSocket error for user {user_info['username']}: {e}")

    except Exception as e:
        logger.error(f"Error in notifications WebSocket: {e}")
    finally:
        simple_notification_manager.disconnect(websocket)

# Keep existing HTTP endpoints for compatibility
@notifications_router.get("/pending-count")
async def get_pending_notification_count(current_user: User = Depends(login_required)):
    """Return the count of pending book requests for the current user"""
    if not (current_user.is_creator or current_user.is_team):
        return {"count": 0}
    
    db = next(get_db())
    try:
        # Import here to avoid circular imports
        from book_request import get_pending_book_request_count

        count = await get_pending_book_request_count(current_user, db)
        return {"count": count}
    except Exception as e:
        logger.error(f"Error getting pending count: {str(e)}")
        return {"count": 0}
    finally:
        db.close()

@notifications_router.get("/list")
async def get_notifications(
    limit: int = Query(2000, ge=1, le=10000),  # ← CHANGED FROM 20 TO 200
    skip: int = Query(0, ge=0),
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get user's notifications"""
    try:
        # Use raw SQL to bypass ORM enum validation
        from sqlalchemy import text
        
        # Execute raw SQL query to get notifications
        result = db.execute(
            text("""
            SELECT 
                id, user_id, sender_id, type, content, title, 
                is_read, notification_data, created_at, read_at
            FROM notifications 
            WHERE user_id = :user_id
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
                    # Parse notification_data
                    notification_data = row["notification_data"] or {}
                    if isinstance(notification_data, str):
                        import json
                        try:
                            notification_data = json.loads(notification_data)
                        except:
                            notification_data = {}
                    
                    # Check if this is a forum notification
                    is_forum_notification = (
                        notification_data.get('source') == 'forum' or
                        (row["title"] and row["title"].startswith('[Forum]'))
                    )
                    
                    if is_forum_notification:
                        # Use forum display name for forum notifications
                        from forum_routes import get_user_forum_display_name
                        display_name = get_user_forum_display_name(sender, db)
                    else:
                        # Use regular username for all other notifications
                        display_name = sender.username
                    
                    sender_data = {
                        "id": sender.id,
                        "username": display_name
                    }
            
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
            
            notifications.append({
                "id": row["id"],
                "type": row["type"],
                "content": row["content"],
                "title": row["title"] if "title" in row else None,
                "sender": sender_data,
                "notification_data": row["notification_data"] or {},
                "is_read": row["is_read"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "time_since": time_since
            })
        
        # Get the total unread count using raw SQL
        unread_result = db.execute(
            text("""
            SELECT COUNT(*) as count
            FROM notifications
            WHERE user_id = :user_id AND is_read = false
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
        import logging
        import traceback
        logging.error(f"Error getting notifications: {str(e)}")
        logging.error(traceback.format_exc())
        
        # Return empty results instead of error
        return {
            "notifications": [],
            "unread_count": 0,
            "total": 0,
            "error": f"Could not retrieve notifications: {str(e)}"
        }

@notifications_router.get("/unread-count")
async def get_unread_count(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get count of unread notifications"""
    count = db.query(Notification).filter(
        Notification.user_id == current_user.id,
        Notification.is_read == False
    ).count()
    return {"count": count}

@notifications_router.post("/{notification_id}/read")
async def mark_notification_read(
    notification_id: int,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Mark a notification as read"""
    try:
        # Use raw SQL to avoid enum validation issues
        from sqlalchemy import text
        
        # First check if notification exists and belongs to user
        check_result = db.execute(
            text("""
            SELECT id, is_read 
            FROM notifications 
            WHERE id = :notification_id AND user_id = :user_id
            """),
            {
                "notification_id": notification_id,
                "user_id": current_user.id
            }
        ).first()
        
        if not check_result:
            raise HTTPException(status_code=404, detail="Notification not found")
        
        # Update the notification to mark as read using raw SQL
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
        
        # Return updated unread count
        unread_count = db.execute(
            text("""
            SELECT COUNT(*) as count
            FROM notifications
            WHERE user_id = :user_id AND is_read = false
            """),
            {"user_id": current_user.id}
        ).scalar()
        
        return {"success": True, "unread_count": unread_count}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error marking notification as read: {str(e)}")
        db.rollback()
        return {"success": False, "error": "An error occurred"}


@notifications_router.delete("/delete-read")
async def delete_all_read_notifications_fixed(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """FIXED: Delete all read notifications (including forum notifications) with enhanced error handling"""
    try:
        logger.info(f"🗑️ DELETE READ: Starting delete for user {current_user.id}")
        
        # First, count how many notifications will be deleted
        count_result = db.execute(
            text("""
            SELECT COUNT(*) as count
            FROM notifications
            WHERE user_id = :user_id AND is_read = true
            """),
            {"user_id": current_user.id}
        )
        notifications_to_delete = count_result.scalar() or 0
        
        logger.info(f"🔍 DELETE READ: Found {notifications_to_delete} read notifications to delete for user {current_user.id}")
        
        if notifications_to_delete == 0:
            logger.info(f"✅ DELETE READ: No read notifications to delete for user {current_user.id}")
            return {
                "success": True, 
                "deleted_count": 0, 
                "message": "No read notifications to delete",
                "remaining_unread": 0
            }
        
        # Delete all read notifications (including forum ones) using raw SQL
        try:
            delete_result = db.execute(
                text("""
                DELETE FROM notifications
                WHERE user_id = :user_id AND is_read = true
                RETURNING id
                """),
                {"user_id": current_user.id}
            )
            
            # Get the actual deleted IDs
            deleted_rows = delete_result.fetchall()
            deleted_count = len(deleted_rows)
            deleted_ids = [row[0] for row in deleted_rows]
            
            logger.info(f"🗑️ DELETE READ: Successfully deleted {deleted_count} notifications for user {current_user.id}")
            
            # Commit the transaction
            db.commit()
            
        except Exception as delete_error:
            logger.error(f"❌ DELETE READ: Database error during delete: {str(delete_error)}")
            db.rollback()
            raise HTTPException(
                status_code=500, 
                detail=f"Database error during delete: {str(delete_error)}"
            )
        
        # Get updated unread count
        try:
            unread_result = db.execute(
                text("""
                SELECT COUNT(*) as count
                FROM notifications
                WHERE user_id = :user_id AND is_read = false
                """),
                {"user_id": current_user.id}
            )
            remaining_unread = unread_result.scalar() or 0
            
        except Exception as count_error:
            logger.error(f"❌ DELETE READ: Error getting unread count: {str(count_error)}")
            remaining_unread = 0
        
        logger.info(f"✅ DELETE READ: Success. Deleted: {deleted_count}, Remaining unread: {remaining_unread}")
        
        response_data = {
            "success": True, 
            "deleted_count": deleted_count,
            "remaining_unread": remaining_unread,
            "message": f"Successfully deleted {deleted_count} read notifications"
        }
        
        # Include sample deleted IDs for debugging (limit to 5)
        if deleted_ids:
            response_data["sample_deleted_ids"] = deleted_ids[:5]
        
        return response_data
        
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        # Catch any other unexpected errors
        error_msg = f"Unexpected error in delete_all_read_notifications: {str(e)}"
        logger.error(f"❌ DELETE READ: {error_msg}")
        logger.error(f"❌ DELETE READ: Traceback: {traceback.format_exc()}")
        
        # Ensure rollback on any error
        try:
            db.rollback()
        except:
            pass
        
        # Return error response instead of raising exception to avoid 422
        return {
            "success": False, 
            "error": error_msg,
            "deleted_count": 0,
            "user_id": current_user.id,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }


@notifications_router.post("/mark-all-read")
async def mark_all_notifications_read_fixed(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """FIXED: Mark all notifications as read with enhanced error handling"""
    try:
        logger.info(f"📖 MARK ALL READ: Starting for user {current_user.id}")
        
        # Count unread notifications first
        count_result = db.execute(
            text("""
            SELECT COUNT(*) as count
            FROM notifications
            WHERE user_id = :user_id AND is_read = false
            """),
            {"user_id": current_user.id}
        )
        unread_count = count_result.scalar() or 0
        
        logger.info(f"🔍 MARK ALL READ: Found {unread_count} unread notifications for user {current_user.id}")
        
        if unread_count == 0:
            return {
                "success": True, 
                "marked_read": 0, 
                "unread_count": 0, 
                "message": "No unread notifications to mark as read"
            }
        
        # Mark all as read
        try:
            update_result = db.execute(
                text("""
                UPDATE notifications
                SET is_read = true, read_at = :read_at
                WHERE user_id = :user_id AND is_read = false
                """),
                {
                    "user_id": current_user.id,
                    "read_at": datetime.now(timezone.utc)
                }
            )
            
            db.commit()
            
            # Get number of rows affected
            affected_rows = update_result.rowcount if hasattr(update_result, "rowcount") else unread_count
            
        except Exception as update_error:
            logger.error(f"❌ MARK ALL READ: Database error during update: {str(update_error)}")
            db.rollback()
            raise HTTPException(
                status_code=500, 
                detail=f"Database error during update: {str(update_error)}"
            )
        
        logger.info(f"✅ MARK ALL READ: Marked {affected_rows} notifications as read for user {current_user.id}")
        
        return {
            "success": True, 
            "marked_read": affected_rows, 
            "unread_count": 0,
            "message": f"Marked {affected_rows} notifications as read"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Unexpected error in mark_all_notifications_read: {str(e)}"
        logger.error(f"❌ MARK ALL READ: {error_msg}")
        logger.error(f"❌ MARK ALL READ: Traceback: {traceback.format_exc()}")
        
        try:
            db.rollback()
        except:
            pass
        
        return {
            "success": False, 
            "error": error_msg,
            "marked_read": 0
        }



# DEBUG: Enhanced debug endpoint
@notifications_router.get("/debug/state")
async def debug_notification_state_enhanced(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Enhanced debug endpoint to check notification state"""
    try:
        logger.info(f"🐛 DEBUG STATE: Checking for user {current_user.id}")
        
        # Get detailed notification counts by type and read status
        result = db.execute(
            text("""
            SELECT 
                is_read,
                CASE 
                    WHEN notification_data::text LIKE '%"source": "forum"%' OR title LIKE '[Forum]%' THEN 'forum'
                    ELSE 'general'
                END as source_type,
                COUNT(*) as count
            FROM notifications
            WHERE user_id = :user_id
            GROUP BY is_read, source_type
            ORDER BY is_read, source_type
            """),
            {"user_id": current_user.id}
        )
        
        counts = {}
        for row in result.mappings():
            key = f"{'read' if row['is_read'] else 'unread'}_{row['source_type']}"
            counts[key] = row['count']
        
        # Get total counts
        total_result = db.execute(
            text("""
            SELECT 
                COUNT(*) as total,
                COUNT(CASE WHEN is_read = false THEN 1 END) as unread,
                COUNT(CASE WHEN is_read = true THEN 1 END) as read
            FROM notifications
            WHERE user_id = :user_id
            """),
            {"user_id": current_user.id}
        )
        
        totals = total_result.mappings().first()
        
        # Get recent notifications sample
        recent_result = db.execute(
            text("""
            SELECT id, type, title, is_read, created_at,
                   CASE 
                       WHEN notification_data::text LIKE '%"source": "forum"%' OR title LIKE '[Forum]%' THEN 'forum'
                       ELSE 'general'
                   END as source_type
            FROM notifications
            WHERE user_id = :user_id
            ORDER BY created_at DESC
            LIMIT 10
            """),
            {"user_id": current_user.id}
        )
        
        recent_notifications = []
        for row in recent_result.mappings():
            recent_notifications.append({
                "id": row['id'],
                "type": row['type'],
                "title": row['title'],
                "is_read": row['is_read'],
                "source_type": row['source_type'],
                "created_at": row['created_at'].isoformat() if row['created_at'] else None
            })
        
        debug_data = {
            "user_id": current_user.id,
            "username": current_user.username,
            "breakdown": counts,
            "totals": dict(totals),
            "recent_notifications": recent_notifications,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "database_connection": "OK"
        }
        
        logger.info(f"🐛 DEBUG STATE: Success for user {current_user.id}")
        return debug_data
        
    except Exception as e:
        logger.error(f"❌ DEBUG STATE: Error for user {current_user.id}: {str(e)}")
        return {
            "error": str(e),
            "user_id": current_user.id,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

# TEST: Simple test endpoint
@notifications_router.get("/test/delete-read")
async def test_delete_read_endpoint(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Test endpoint to check if delete-read would work"""
    try:
        # Count read notifications
        count_result = db.execute(
            text("""
            SELECT COUNT(*) as count
            FROM notifications
            WHERE user_id = :user_id AND is_read = true
            """),
            {"user_id": current_user.id}
        )
        read_count = count_result.scalar() or 0
        
        # Get total count
        total_result = db.execute(
            text("""
            SELECT COUNT(*) as count
            FROM notifications
            WHERE user_id = :user_id
            """),
            {"user_id": current_user.id}
        )
        total_count = total_result.scalar() or 0
        
        return {
            "user_id": current_user.id,
            "total_notifications": total_count,
            "read_notifications": read_count,
            "unread_notifications": total_count - read_count,
            "can_delete": read_count > 0,
            "endpoint_accessible": True,
            "database_connection": "OK",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        return {
            "error": str(e),
            "endpoint_accessible": True,
            "database_connection": "ERROR",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

# UPDATED: WebSocket version of create_notification function
async def create_notification_raw_sql_with_websocket(
    db: Session,
    user_id: int,
    notification_type: str,  
    content: str,
    title: str = None,
    sender_id: Optional[int] = None,
    notification_data: Dict[str, Any] = None
) -> int:
    """Create notification and send via WebSocket if user is connected"""
    try:
        # Generate UUID
        notification_uuid = str(uuid.uuid4())
        
        # Convert notification data to JSON string
        notification_data_json = json.dumps(notification_data or {})
        
        # Get default title if not provided
        if title is None:
            title = get_notification_title(notification_type.lower())
            
        # Use raw SQL to insert the notification with title
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
                "type": notification_type.lower(),
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
        
        # Simple forum detection and name selection
        sender_data = None
        if sender_id:
            sender = db.query(User).filter(User.id == sender_id).first()
            if sender:
                # Check if this is a forum notification
                is_forum_notification = (
                    (notification_data and notification_data.get('source') == 'forum') or
                    (title and title.startswith('[Forum]'))
                )
                
                if is_forum_notification:
                    # Use forum display name for forum notifications
                    from forum_routes import get_user_forum_display_name
                    display_name = get_user_forum_display_name(sender, db)
                else:
                    # Use regular username for all other notifications
                    display_name = sender.username
                
                sender_data = {
                    "id": sender.id,
                    "username": display_name
                }
        
        # Send to user via WebSocket
        websocket_message = {
            "type": "new_notification",
            "notification": {
                "id": notification_id,
                "type": notification_type.lower(),
                "content": content,
                "title": title,
                "sender": sender_data,
                "notification_data": notification_data or {},
                "is_read": False,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
        }
        
        # Try to send via WebSocket
        websocket_sent = await simple_notification_manager.send_to_user(user_id, websocket_message)
        
        if websocket_sent:
            logging.info(f"✅ Notification {notification_id} sent via WebSocket to user {user_id}")
        else:
            logging.info(f"📱 User {user_id} offline, notification {notification_id} stored in DB")
        
        return notification_id
        
    except Exception as e:
        db.rollback()
        logging.error(f"Error creating notification: {str(e)}")
        raise


# Keep original function for backward compatibility
async def create_notification_raw_sql(
    db: Session,
    user_id: int,
    notification_type: str,  
    content: str,
    title: str = None,
    sender_id: Optional[int] = None,
    notification_data: Dict[str, Any] = None
) -> int:
    """Original function - now calls WebSocket version"""
    return await create_notification_raw_sql_with_websocket(
        db=db,
        user_id=user_id,
        notification_type=notification_type,
        content=content,
        title=title,
        sender_id=sender_id,
        notification_data=notification_data
    )

# Helper function to get title for notification types
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
        "system": "System Notification",
        # Book request notification types
        "book_request_approved": "Request Approved",
        "book_request_rejected": "Request Rejected",
        "book_request_accepted": "Request In Progress",
        "book_request_fulfilled": "Request Fulfilled"
    }
    
    return type_titles.get(notification_type.lower(), "New Notification")

# UPDATED: All notification functions now use WebSocket
async def notify_comment(db: Session, comment: Comment, sender: User) -> List[int]:
    """Notify appropriate users about a new comment - WITH WEBSOCKET"""
    notification_ids = []
    
    try:
        # Get the track object
        track = db.query(Track).filter(Track.id == comment.track_id).first()
        if not track:
            return notification_ids
            
        # Get album (to find the creator)
        album = db.query(Album).filter(Album.id == track.album_id).first()
        if not album:
            return notification_ids
            
        # Get creator
        creator_id = album.created_by_id
        
        # 1. If it's a reply, notify the parent comment owner
        if comment.parent_id:
            parent_comment = db.query(Comment).filter(Comment.id == comment.parent_id).first()
            if parent_comment and parent_comment.user_id != sender.id:
                # Use WebSocket version
                reply_notif_id = await create_notification_raw_sql_with_websocket(
                    db=db,
                    user_id=parent_comment.user_id,
                    notification_type="reply",
                    title="New Reply",
                    content=f"{sender.username} replied to your comment",
                    sender_id=sender.id,
                    notification_data={
                        "track_id": str(comment.track_id),
                        "comment_id": comment.id,
                        "parent_id": comment.parent_id
                    }
                )
                notification_ids.append(reply_notif_id)
        
        # 2. Notify creator (if they're not the commenter)
        if creator_id != sender.id:
            creator_notif_id = await create_notification_raw_sql_with_websocket(
                db=db,
                user_id=creator_id,
                notification_type="comment",
                title="New Comment",
                content=f"{sender.username} commented on {track.title}",
                sender_id=sender.id,
                notification_data={
                    "track_id": str(comment.track_id),
                    "comment_id": comment.id
                }
            )
            notification_ids.append(creator_notif_id)
            
        # 3. Notify team members using existing SQL query
        from sqlalchemy import text
        
        team_query = text("""
        SELECT id, username, email 
        FROM users 
        WHERE created_by = :creator_id 
        AND is_team = true 
        AND is_active = true 
        AND id != :sender_id
        """)
        
        team_result = db.execute(team_query, {
            "creator_id": creator_id, 
            "sender_id": sender.id
        })
        
        team_members = team_result.fetchall()
        
        for team_member in team_members:
            team_notif_id = await create_notification_raw_sql_with_websocket(
                db=db,
                user_id=team_member.id,
                notification_type="comment",
                title="New Comment",
                content=f"{sender.username} commented on {track.title}",
                sender_id=sender.id,
                notification_data={
                    "track_id": str(comment.track_id),
                    "comment_id": comment.id
                }
            )
            notification_ids.append(team_notif_id)
            
    except Exception as e:
        import logging
        logging.error(f"Error creating comment notifications: {str(e)}", exc_info=True)
        
    return notification_ids

async def notify_like(db: Session, track_id: str, sender: User) -> Optional[int]:
    """Notify creator when someone likes their track - WITH WEBSOCKET"""
    try:
        track = db.query(Track).filter(Track.id == track_id).first()
        if not track:
            logger.warning(f"No track found with ID {track_id}")
            return None
            
        album = db.query(Album).filter(Album.id == track.album_id).first()
        if not album:
            logger.warning(f"No album found for track {track_id}")
            return None
            
        creator_id = album.created_by_id
        
        if creator_id == sender.id:
            return None
            
        # Use WebSocket version
        notification_id = await create_notification_raw_sql_with_websocket(
            db=db,
            user_id=creator_id,
            notification_type="like",
            title="New Like",
            content=f"{sender.username} liked your track {track.title}",
            sender_id=sender.id,
            notification_data={
                "track_id": str(track_id)
            }
        )
        
        return notification_id
        
    except Exception as e:
        logger.error(f"Error creating like notification: {str(e)}")
        return None

async def notify_share(db: Session, track_id: str, sender: User, platform: str = "unknown") -> Optional[int]:
    """Notify creator when someone shares their track - WITH WEBSOCKET"""
    try:
        track = db.query(Track).filter(Track.id == track_id).first()
        if not track:
            logger.warning(f"No track found with ID {track_id}")
            return None
            
        album = db.query(Album).filter(Album.id == track.album_id).first()
        if not album:
            logger.warning(f"No album found for track {track_id}")
            return None
            
        creator_id = album.created_by_id
        
        if creator_id == sender.id:
            return None
            
        platform_display = platform.capitalize() if platform != "unknown" else "social media"
        
        # Use WebSocket version
        notification_id = await create_notification_raw_sql_with_websocket(
            db=db,
            user_id=creator_id,
            notification_type="share",
            title="New Share",
            content=f"{sender.username} shared your track {track.title} on {platform_display}",
            sender_id=sender.id,
            notification_data={
                "track_id": str(track_id),
                "platform": platform
            }
        )
        
        return notification_id
        
    except Exception as e:
        logger.error(f"Error creating share notification: {str(e)}")
        return None


@notifications_router.delete("/{notification_id}")
async def delete_notification(
    notification_id: int,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Delete a specific notification"""
    try:
        result = db.execute(
            text("""
            DELETE FROM notifications 
            WHERE id = :notification_id AND user_id = :user_id
            RETURNING id
            """),
            {
                "notification_id": notification_id,
                "user_id": current_user.id
            }
        )
        
        deleted_id = result.scalar()
        if not deleted_id:
            raise HTTPException(status_code=404, detail="Notification not found")
        
        db.commit()
        
        # Get updated unread count
        unread_count = db.execute(
            text("""
            SELECT COUNT(*) as count
            FROM notifications
            WHERE user_id = :user_id AND is_read = false
            """),
            {"user_id": current_user.id}
        ).scalar()
        
        return {"success": True, "unread_count": unread_count}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting notification: {str(e)}")
        db.rollback()
        return {"success": False, "error": "An error occurred"}


async def broadcast(self, message: Dict[str, Any], user_id: Optional[int] = None, is_system_broadcast: bool = False):
    """Broadcast to specific user, all users, or system broadcast to everyone"""
    for connection in self.active_connections:
        try:
            # If this is a system broadcast, send to all connections
            if is_system_broadcast:
                await connection.send_json(message)
            # If user_id is specified, only send to that user's connections
            elif user_id:
                conn_user_id = self.connection_user_map.get(connection)
                if conn_user_id == user_id:
                    await connection.send_json(message)
            else:
                # Send to all connections
                await connection.send_json(message)
        except Exception as e:
            logger.error(f"Error broadcasting: {str(e)}")
            # Will be removed on next attempt
            pass