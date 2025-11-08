from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from typing import List, Dict, Any, Optional, Union
from datetime import datetime, timezone
import logging
from models import User, Track, Album, Comment, Notification, NotificationType
from typing import Dict, List, Any, Optional, Union

logger = logging.getLogger(__name__)

class NotificationService:
    """Service for managing user notifications"""
    
    def __init__(self, db: Session):
        self.db = db
        
    async def create_notification(
        self, 
        user_id: int, 
        notification_type: NotificationType, 
        content: str, 
        sender_id: Optional[int] = None, 
        metadata: Dict[str, Any] = None
    ) -> Notification:
        """Create a notification for a user"""
        notification = Notification(
            user_id=user_id,
            sender_id=sender_id, 
            type=notification_type,
            content=content,
            metadata=metadata or {},
            created_at=datetime.now(timezone.utc)
        )
        
        self.db.add(notification)
        self.db.commit()
        self.db.refresh(notification)
        
        logger.info(f"Created notification: {notification.id} for user {user_id}")
        
        # Broadcast notification to connected clients via WebSocket if available
        try:
            from notifications import notification_manager
            await notification_manager.broadcast({
                "type": "new_notification",
                "user_id": user_id,
                "notification": {
                    "id": notification.id,
                    "type": notification_type.value,
                    "content": content,
                    "sender_id": sender_id,
                    "metadata": metadata or {},
                    "is_read": False,
                    "created_at": notification.created_at.isoformat() if notification.created_at else None
                }
            }, user_id=user_id)
        except (ImportError, AttributeError) as e:
            logger.warning(f"Could not broadcast notification: {str(e)}")
        
        return notification
    
    async def notify_comment(self, comment: Comment, sender: User) -> List[Notification]:
        """Notify appropriate users about a new comment
        
        Notifies:
        - The track/album creator
        - Team members if applicable
        - Parent comment author if it's a reply
        """
        notifications = []
        
        try:
            # Get the track object
            track = self.db.query(Track).filter(Track.id == comment.track_id).first()
            if not track:
                logger.warning(f"No track found for comment {comment.id}")
                return notifications
                
            # Get album (to find the creator)
            album = self.db.query(Album).filter(Album.id == track.album_id).first()
            if not album:
                logger.warning(f"No album found for track {track.id}")
                return notifications
                
            # Get creator
            creator_id = album.created_by_id
            
            # 1. If it's a reply, notify the parent comment owner
            if comment.parent_id:
                parent_comment = self.db.query(Comment).filter(Comment.id == comment.parent_id).first()
                if parent_comment and parent_comment.user_id != sender.id:  # Don't notify yourself
                    # Notify parent comment author
                    reply_notif = await self.create_notification(
                        user_id=parent_comment.user_id,
                        notification_type=NotificationType.REPLY,
                        content=f"{sender.username} replied to your comment",
                        sender_id=sender.id,
                        metadata={
                            "track_id": str(comment.track_id),
                            "comment_id": comment.id,
                            "parent_id": comment.parent_id
                        }
                    )
                    notifications.append(reply_notif)
            
            # 2. Notify creator (if they're not the commenter)
            if creator_id != sender.id:
                creator_notif = await self.create_notification(
                    user_id=creator_id,
                    notification_type=NotificationType.COMMENT,
                    content=f"{sender.username} commented on {track.title}",
                    sender_id=sender.id,
                    metadata={
                        "track_id": str(comment.track_id),
                        "comment_id": comment.id
                    }
                )
                notifications.append(creator_notif)
                
            # 3. Notify team members (if applicable)
            team_members = self.db.query(User).filter(
                and_(
                    User.created_by == creator_id,
                    User.is_team == True,
                    User.is_active == True,
                    User.id != sender.id  # Don't notify the commenter
                )
            ).all()
            
            for team_member in team_members:
                team_notif = await self.create_notification(
                    user_id=team_member.id,
                    notification_type=NotificationType.COMMENT,
                    content=f"{sender.username} commented on {track.title}",
                    sender_id=sender.id,
                    metadata={
                        "track_id": str(comment.track_id),
                        "comment_id": comment.id
                    }
                )
                notifications.append(team_notif)
                
        except Exception as e:
            logger.error(f"Error creating comment notifications: {str(e)}")
            
        return notifications
    
    async def notify_like(self, track_id: str, sender: User) -> Optional[Notification]:
        """Notify creator when someone likes their track"""
        try:
            # Get the track
            track = self.db.query(Track).filter(Track.id == track_id).first()
            if not track:
                logger.warning(f"No track found with ID {track_id}")
                return None
                
            # Get album (to find the creator)
            album = self.db.query(Album).filter(Album.id == track.album_id).first()
            if not album:
                logger.warning(f"No album found for track {track_id}")
                return None
                
            # Get creator
            creator_id = album.created_by_id
            
            # Don't notify if the liker is the creator
            if creator_id == sender.id:
                return None
                
            # Create notification for creator only (not team members)
            notification = await self.create_notification(
                user_id=creator_id,
                notification_type=NotificationType.LIKE,
                content=f"{sender.username} liked your track {track.title}",
                sender_id=sender.id,
                metadata={
                    "track_id": str(track_id)
                }
            )
            
            return notification
            
        except Exception as e:
            logger.error(f"Error creating like notification: {str(e)}")
            return None
    
    async def notify_share(self, track_id: str, sender: User, platform: str = "unknown") -> Optional[Notification]:
        """Notify creator when someone shares their track"""
        try:
            # Get the track
            track = self.db.query(Track).filter(Track.id == track_id).first()
            if not track:
                logger.warning(f"No track found with ID {track_id}")
                return None
                
            # Get album (to find the creator)
            album = self.db.query(Album).filter(Album.id == track.album_id).first()
            if not album:
                logger.warning(f"No album found for track {track_id}")
                return None
                
            # Get creator
            creator_id = album.created_by_id
            
            # Don't notify if the sharer is the creator
            if creator_id == sender.id:
                return None
                
            # Create notification for creator only (not team members)
            notification = await self.create_notification(
                user_id=creator_id,
                notification_type=NotificationType.SHARE,
                content=f"{sender.username} shared your track {track.title} on {platform}",
                sender_id=sender.id,
                metadata={
                    "track_id": str(track_id),
                    "platform": platform
                }
            )
            
            return notification
            
        except Exception as e:
            logger.error(f"Error creating share notification: {str(e)}")
            return None
    
    async def get_user_notifications(self, user_id: int, limit: int = 2000, skip: int = 0) -> List[Notification]:
        """Get notifications for a user"""
        return self.db.query(Notification).filter(
            Notification.user_id == user_id
        ).order_by(
            Notification.created_at.desc()
        ).offset(skip).limit(limit).all()
    
    async def mark_as_read(self, notification_id: int, user_id: int) -> bool:
        """Mark a notification as read"""
        notification = self.db.query(Notification).filter(
            Notification.id == notification_id,
            Notification.user_id == user_id
        ).first()
        
        if not notification:
            return False
            
        notification.is_read = True
        notification.read_at = datetime.now(timezone.utc)
        self.db.commit()
        return True
    
    async def mark_all_as_read(self, user_id: int) -> int:
        """Mark all notifications as read for a user"""
        result = self.db.query(Notification).filter(
            Notification.user_id == user_id,
            Notification.is_read == False
        ).update({"is_read": True, "read_at": datetime.now(timezone.utc)})
        
        self.db.commit()
        return result
    
    async def count_unread(self, user_id: int) -> int:
        """Count unread notifications for a user"""
        return self.db.query(Notification).filter(
            Notification.user_id == user_id,
            Notification.is_read == False
        ).count()