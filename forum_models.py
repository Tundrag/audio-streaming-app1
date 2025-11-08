# forum_models.py - Complete Forum Models with Fixed Relationships
from sqlalchemy.orm import relationship
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime, timezone
from database import Base
from sqlalchemy.orm import relationship, Session 

class ForumThread(Base):
    """Forum threads - creator creates discussion topics, users create sub-threads"""
    __tablename__ = "forum_threads"
    
    # Core columns
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    # Database-driven tier system
    min_tier_cents = Column(Integer, default=0)
    min_tier_id = Column(Integer, ForeignKey("campaign_tiers.id"), nullable=True)
    tier_restrictions = Column(JSONB, nullable=True)
    
    # Thread management
    is_private = Column(Boolean, default=False)
    is_pinned = Column(Boolean, default=False)
    is_locked = Column(Boolean, default=False)
    
    # Stats
    message_count = Column(Integer, default=0)
    view_count = Column(Integer, default=0)
    last_message_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_message_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    # Thread hierarchy columns
    parent_message_id = Column(Integer, ForeignKey("forum_messages.id", ondelete="CASCADE"), nullable=True)
    thread_type = Column(String(20), default="main")  # 'main' or 'sub'
    created_from_message_id = Column(Integer, ForeignKey("forum_messages.id"), nullable=True)
    follower_count = Column(Integer, default=0)
    
    # FIXED: Simplified relationships
    user = relationship("User", foreign_keys=[user_id])
    last_message_user = relationship("User", foreign_keys=[last_message_user_id])
    
    # FIXED: Main relationship - specify foreign_keys to resolve ambiguity
    messages = relationship(
        "ForumMessage", 
        back_populates="thread", 
        foreign_keys="ForumMessage.thread_id",
        cascade="all, delete-orphan"
    )
    
    # One-way relationships to avoid conflicts
    parent_message = relationship(
        "ForumMessage", 
        foreign_keys=[parent_message_id],
        post_update=True
    )
    
    created_from_message = relationship(
        "ForumMessage", 
        foreign_keys=[created_from_message_id],
        post_update=True
    )
    
    followers = relationship(
        "ForumThreadFollower", 
        back_populates="thread", 
        cascade="all, delete-orphan"
    )
    
    campaign_tier = relationship("CampaignTier", foreign_keys=[min_tier_id])
    
    def can_access(self, user) -> bool:
        """Check if user can access this thread"""
        # Creators and team always have access
        if user.is_creator or user.is_team:
            return True
        
        # Private threads are creator/team only
        if self.is_private:
            return False
        
        # Free access
        if not self.min_tier_cents or self.min_tier_cents == 0:
            return True
        
        # Tier-based access
        user_tier_data = user.patreon_tier_data or {}
        user_amount = user_tier_data.get("amount_cents", 0)
        
        return user_amount >= self.min_tier_cents
    
    def can_delete(self, user) -> bool:
        """Check if user can delete this thread"""
        if self.thread_type == "main":
            return user.is_creator or user.is_team
        return self.user_id == user.id or user.is_creator or user.is_team
    
    def can_manage(self, user) -> bool:
        """Check if user can manage this thread (pin, lock, etc.)"""
        if self.thread_type == "main":
            return user.is_creator or user.is_team
        return self.user_id == user.id or user.is_creator or user.is_team
    
    def get_follower(self, user):
        """Get follower record for user"""
        for follower in self.followers:
            if follower.user_id == user.id:
                return follower
        return None
    
    def is_followed_by(self, user) -> bool:
        """Check if user follows this thread"""
        follower = self.get_follower(user)
        return follower is not None and follower.is_active
    
    def get_tier_info(self, db_session=None):
        """Get tier information for display"""
        if not self.min_tier_cents or self.min_tier_cents == 0:
            return {
                "is_restricted": False,
                "tier_title": "Free Access",
                "min_tier_cents": 0,
                "tier_id": None
            }
        
        tier_info = {
            "is_restricted": True,
            "min_tier_cents": self.min_tier_cents,
            "tier_id": self.min_tier_id
        }
        
        # If we have tier restrictions metadata, use it
        if self.tier_restrictions:
            tier_info.update({
                "tier_title": self.tier_restrictions.get("tier_title", f"${self.min_tier_cents/100:.2f}+ Tier"),
                "tier_color": self.tier_restrictions.get("tier_color"),
                "tier_description": self.tier_restrictions.get("tier_description")
            })
        
        # If we have a tier ID and database session, get fresh tier data
        elif self.min_tier_id and db_session:
            from models import CampaignTier
            tier = db_session.query(CampaignTier).filter(
                CampaignTier.id == self.min_tier_id
            ).first()
            
            if tier:
                tier_info.update({
                    "tier_title": tier.title,
                    "tier_description": tier.description,
                    "tier_color": getattr(tier, 'color', None),
                    "amount_display": f"${tier.amount_cents/100:.2f}+"
                })
            else:
                tier_info["tier_title"] = f"${self.min_tier_cents/100:.2f}+ Tier"
        else:
            tier_info["tier_title"] = f"${self.min_tier_cents/100:.2f}+ Tier"
        
        return tier_info
    
    def set_tier_restriction(self, tier_id: int, db_session):
        """Set tier restriction using CampaignTier"""
        from models import CampaignTier
        
        if tier_id:
            tier = db_session.query(CampaignTier).filter(
                CampaignTier.id == tier_id
            ).first()
            
            if tier:
                self.min_tier_id = tier.id
                self.min_tier_cents = tier.amount_cents
                self.tier_restrictions = {
                    "tier_id": tier.id,
                    "tier_title": tier.title,
                    "tier_description": tier.description,
                    "tier_color": getattr(tier, 'color', None),
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }
        else:
            # Remove tier restrictions
            self.min_tier_id = None
            self.min_tier_cents = 0
            self.tier_restrictions = None

class ForumMessage(Base):
    """Forum messages - users discuss with each other"""
    __tablename__ = "forum_messages"
    
    # Core columns
    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(Integer, ForeignKey("forum_threads.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    content = Column(Text, nullable=False)
    
    # Reply system with proper cascading delete
    reply_to_id = Column(Integer, ForeignKey("forum_messages.id", ondelete="CASCADE"), nullable=True)
    reply_count = Column(Integer, default=0)

    has_everyone_mention = Column(Boolean, default=False)
    everyone_mention_count = Column(Integer, default=0)
    
    # Message features
    is_edited = Column(Boolean, default=False)
    edited_at = Column(DateTime(timezone=True), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    # Thread creation tracking
    spawned_thread_count = Column(Integer, default=0)
    like_count = Column(Integer, default=0)
    
    # FIXED: Thread relationship - specify foreign_keys to resolve ambiguity
    thread = relationship("ForumThread", back_populates="messages", foreign_keys=[thread_id])
    user = relationship("User", foreign_keys=[user_id])
    mentions = relationship("ForumMention", back_populates="message", cascade="all, delete-orphan")
    
    # One-way relationships to avoid conflicts - specify foreign_keys explicitly
    spawned_threads = relationship(
        "ForumThread", 
        foreign_keys="ForumThread.created_from_message_id",
        post_update=True
    )
    
    # Reply relationships with proper cascading
    reply_to = relationship("ForumMessage", remote_side=[id], backref="replies")
    likes = relationship("ForumMessageLike", back_populates="message", cascade="all, delete-orphan")

class ForumMention(Base):
    """User mentions in forum messages"""
    __tablename__ = "forum_mentions"
    
    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("forum_messages.id", ondelete="CASCADE"), nullable=False)
    mentioned_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    # Notification tracking
    is_read = Column(Boolean, default=False)
    read_at = Column(DateTime(timezone=True), nullable=True)

    is_everyone_mention = Column(Boolean, default=False)
    mention_type = Column(String(20), default="user")
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    
    # Relationships
    message = relationship("ForumMessage", back_populates="mentions")
    mentioned_user = relationship("User", foreign_keys=[mentioned_user_id])
    
    # Unique constraint to prevent duplicate mentions
    __table_args__ = (
        # UniqueConstraint('message_id', 'mentioned_user_id', name='unique_message_mention'),
        {'extend_existing': True}
    )
class ForumThreadFollower(Base):
    """Users following forum threads for notifications"""
    __tablename__ = "forum_thread_followers"
    
    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(Integer, ForeignKey("forum_threads.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    # Follow preferences
    notify_on_new_message = Column(Boolean, default=True)
    notify_on_mention = Column(Boolean, default=True)
    notify_on_reply = Column(Boolean, default=True)
    
    # Follow status
    is_active = Column(Boolean, default=True)
    auto_followed = Column(Boolean, default=False)  # Auto-followed vs manually followed
    muted_until = Column(DateTime(timezone=True), nullable=True)  # Temporary muting
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    # Relationships
    thread = relationship("ForumThread", back_populates="followers")
    user = relationship("User", foreign_keys=[user_id])
    
    # Unique constraint to prevent duplicate follows
    __table_args__ = (
        UniqueConstraint('thread_id', 'user_id', name='unique_thread_follower'),
        {'extend_existing': True}
    )
    
    def should_notify_for_message(self, message) -> bool:
        """Check if this follower should be notified for a specific message"""
        if not self.is_active:
            return False
        
        if self.muted_until and self.muted_until > datetime.now(timezone.utc):
            return False
        
        # Check if message is a reply to this user's message
        if message.reply_to and message.reply_to.user_id == self.user_id and self.notify_on_reply:
            return True
        
        # Check if user is mentioned in the message
        if any(mention.mentioned_user_id == self.user_id for mention in message.mentions) and self.notify_on_mention:
            return True
        
        # Check for general new message notifications
        if self.notify_on_new_message:
            return True
        
        return False

class ForumNotification(Base):
    """Forum-specific notifications for mentions, replies, etc."""
    __tablename__ = "forum_notifications"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    thread_id = Column(Integer, ForeignKey("forum_threads.id", ondelete="CASCADE"), nullable=False)
    message_id = Column(Integer, ForeignKey("forum_messages.id", ondelete="CASCADE"), nullable=True)
    sender_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    
    # Notification content
    notification_type = Column(String(50), nullable=False)  # 'mention', 'reply', 'new_message'
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    
    # Status tracking
    is_read = Column(Boolean, default=False)
    is_preview_shown = Column(Boolean, default=False)  # For quick reply notifications
    read_at = Column(DateTime(timezone=True), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    
    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    thread = relationship("ForumThread", foreign_keys=[thread_id])
    message = relationship("ForumMessage", foreign_keys=[message_id])
    sender = relationship("User", foreign_keys=[sender_id])

class ForumMessageLike(Base):
    """Likes for forum messages"""
    __tablename__ = "forum_message_likes"
    
    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("forum_messages.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    
    # Relationships
    message = relationship("ForumMessage", back_populates="likes")
    user = relationship("User", foreign_keys=[user_id])
    
    # Unique constraint to prevent duplicate likes
    __table_args__ = (
        UniqueConstraint('message_id', 'user_id', name='unique_message_like'),
        {'extend_existing': True}
    )

# ForumUserSettings is already defined in models.py

# Helper functions
def get_user_forum_display_name(user) -> str:
    """Get user's forum display name (alias or username)"""
    if hasattr(user, 'forum_settings') and user.forum_settings:
        if user.forum_settings.use_alias and user.forum_settings.display_alias:
            return user.forum_settings.display_alias
    return user.username

def should_send_quick_reply_notification(user_id: int, notification_type: str, db_session) -> bool:
    """Check if user should receive quick reply notifications"""
    # Import here to avoid circular imports - ForumUserSettings is in models.py
    from models import ForumUserSettings
    
    settings = db_session.query(ForumUserSettings).filter(ForumUserSettings.user_id == user_id).first()
    
    if not settings or not settings.enable_quick_reply_notifications:
        return False
    
    if notification_type == "mention" and not settings.quick_reply_for_mentions:
        return False
    
    if notification_type == "reply" and not settings.quick_reply_for_replies:
        return False
    
    return True

class ForumEveryoneMentionLog(Base):
    """Track @everyone mention usage for analytics and moderation"""
    __tablename__ = "forum_everyone_mention_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("forum_messages.id", ondelete="CASCADE"), nullable=False)
    thread_id = Column(Integer, ForeignKey("forum_threads.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    # Analytics data
    notification_count = Column(Integer, default=0)  # How many users were notified
    eligible_user_count = Column(Integer, default=0)  # How many users were eligible
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    
    # Relationships
    message = relationship("ForumMessage", foreign_keys=[message_id])
    thread = relationship("ForumThread", foreign_keys=[thread_id])
    user = relationship("User", foreign_keys=[user_id])

# Helper function to check @everyone usage limits (optional rate limiting)
def can_use_everyone_mention_with_limits(user, db_session, hours_lookback: int = 24) -> tuple[bool, str]:
    """Check if user can use @everyone with rate limiting"""
    from sqlalchemy import func
    
    # Basic permission check
    if not (user.is_creator or user.is_team):
        return False, "Only creators and team members can use @everyone"
    
    # Rate limiting for team members (creators have unlimited usage)
    if user.is_team and not user.is_creator:
        # Check usage in last 24 hours
        recent_usage = db_session.query(func.count(ForumEveryoneMentionLog.id)).filter(
            and_(
                ForumEveryoneMentionLog.user_id == user.id,
                ForumEveryoneMentionLog.created_at >= datetime.now(timezone.utc) - timedelta(hours=hours_lookback)
            )
        ).scalar() or 0
        
        max_usage = 3  # Team members can use @everyone 3 times per day
        if recent_usage >= max_usage:
            return False, f"Rate limit exceeded. Team members can use @everyone {max_usage} times per {hours_lookback} hours."
    
    return True, ""

# Helper function to get @everyone mention statistics
def get_everyone_mention_stats(db_session, days_back: int = 7):
    """Get @everyone mention usage statistics"""
    from sqlalchemy import func
    from datetime import timedelta
    
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_back)
    
    stats = db_session.query(
        func.count(ForumEveryoneMentionLog.id).label('total_mentions'),
        func.sum(ForumEveryoneMentionLog.notification_count).label('total_notifications'),
        func.count(func.distinct(ForumEveryoneMentionLog.user_id)).label('unique_users'),
        func.count(func.distinct(ForumEveryoneMentionLog.thread_id)).label('unique_threads')
    ).filter(
        ForumEveryoneMentionLog.created_at >= cutoff_date
    ).first()
    
    return {
        'total_mentions': stats.total_mentions or 0,
        'total_notifications': stats.total_notifications or 0,
        'unique_users': stats.unique_users or 0,
        'unique_threads': stats.unique_threads or 0,
        'days_analyzed': days_back
    }

class ForumModerationSettings(Base):
    """Creator moderation settings for @everyone system"""
    __tablename__ = "forum_moderation_settings"
    
    id = Column(Integer, primary_key=True, index=True)
    creator_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    
    # Rate limiting controls
    team_rate_limit = Column(Integer, default=3)
    rate_limit_window_hours = Column(Integer, default=24)
    global_cooldown_minutes = Column(Integer, default=0)
    
    # Approval controls
    require_approval = Column(Boolean, default=False)
    auto_approve_trusted = Column(Boolean, default=True)
    
    # Content controls
    max_message_length = Column(Integer, nullable=True)
    forbidden_words = Column(JSONB, nullable=True)  # List of forbidden words
    
    # Targeting controls
    notification_limit = Column(Integer, nullable=True)  # Max users per @everyone
    allowed_thread_types = Column(JSONB, nullable=True)  # ['main', 'sub'] restrictions
    
    # Timing controls
    quiet_hours_start = Column(Integer, nullable=True)  # Hour (0-23)
    quiet_hours_end = Column(Integer, nullable=True)    # Hour (0-23)
    timezone = Column(String(50), default="UTC")
    
    # Emergency controls
    everyone_globally_disabled = Column(Boolean, default=False)
    emergency_disable_reason = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    # Relationships
    creator = relationship("User", foreign_keys=[creator_id])

# Enhanced permission checking with creator controls
def can_use_everyone_mention_enhanced(user, db: Session):
    from models import User, ForumUserSettings
    """Enhanced permission check with creator moderation controls"""
    
    # Basic permission check
    if not (user.is_creator or user.is_team):
        return False, "Only creators and team members can use @everyone"
    
    # Creators always have access (unless globally disabled)
    if user.is_creator:
        # Check if globally disabled by any creator
        global_disable = db.query(ForumModerationSettings).filter(
            ForumModerationSettings.everyone_globally_disabled == True
        ).first()
        
        if global_disable and global_disable.creator_id != user.id:
            return False, f"@everyone is globally disabled: {global_disable.emergency_disable_reason}"
        
        return True, ""
    
    # Team member checks - get the creator's moderation settings
    creator = db.query(User).filter(User.is_creator == True).first()
    if not creator:
        return False, "No creator found to check moderation settings"
    
    mod_settings = db.query(ForumModerationSettings).filter(
        ForumModerationSettings.creator_id == creator.id
    ).first()
    
    # Check if globally disabled
    if mod_settings and mod_settings.everyone_globally_disabled:
        return False, f"@everyone is disabled: {mod_settings.emergency_disable_reason or 'Temporarily disabled by creator'}"
    
    # Check individual user restrictions
    user_settings = db.query(ForumUserSettings).filter(
        ForumUserSettings.user_id == user.id
    ).first()
    
    if user_settings and user_settings.everyone_restricted:
        if user_settings.everyone_restricted_until:
            if datetime.now(timezone.utc) < user_settings.everyone_restricted_until:
                return False, f"You are restricted from using @everyone until {user_settings.everyone_restricted_until}. Reason: {user_settings.everyone_restriction_reason}"
            else:
                # Restriction expired, clear it
                user_settings.everyone_restricted = False
                user_settings.everyone_restricted_until = None
                user_settings.everyone_restriction_reason = None
                db.commit()
        else:
            return False, f"You are permanently restricted from using @everyone. Reason: {user_settings.everyone_restriction_reason}"
    
    # Check rate limiting with custom settings
    rate_limit = mod_settings.team_rate_limit if mod_settings else 3
    window_hours = mod_settings.rate_limit_window_hours if mod_settings else 24
    
    # Check for custom rate limit for this user
    if user_settings and user_settings.everyone_custom_rate_limit is not None:
        rate_limit = user_settings.everyone_custom_rate_limit
    
    # Check usage in the time window
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    recent_usage = db.query(func.count(ForumEveryoneMentionLog.id)).filter(
        ForumEveryoneMentionLog.user_id == user.id,
        ForumEveryoneMentionLog.created_at >= cutoff_time
    ).scalar() or 0
    
    if recent_usage >= rate_limit:
        return False, f"Rate limit exceeded: {recent_usage}/{rate_limit} uses in last {window_hours} hours"
    
    # Check global cooldown
    if mod_settings and mod_settings.global_cooldown_minutes > 0:
        last_anyone_usage = db.query(ForumEveryoneMentionLog.created_at).order_by(
            ForumEveryoneMentionLog.created_at.desc()
        ).first()
        
        if last_anyone_usage:
            time_since_last = datetime.now(timezone.utc) - last_anyone_usage[0]
            cooldown_remaining = timedelta(minutes=mod_settings.global_cooldown_minutes) - time_since_last
            
            if cooldown_remaining.total_seconds() > 0:
                minutes_left = int(cooldown_remaining.total_seconds() / 60)
                return False, f"Global cooldown active: wait {minutes_left} more minutes"
    
    # Check quiet hours
    if mod_settings and mod_settings.quiet_hours_start is not None and mod_settings.quiet_hours_end is not None:
        import pytz
        tz = pytz.timezone(mod_settings.timezone)
        current_hour = datetime.now(tz).hour
        
        start_hour = mod_settings.quiet_hours_start
        end_hour = mod_settings.quiet_hours_end
        
        # Handle overnight quiet hours (e.g., 22:00 to 08:00)
        if start_hour > end_hour:
            if current_hour >= start_hour or current_hour < end_hour:
                return False, f"@everyone disabled during quiet hours ({start_hour}:00 - {end_hour}:00 {mod_settings.timezone})"
        else:
            if start_hour <= current_hour < end_hour:
                return False, f"@everyone disabled during quiet hours ({start_hour}:00 - {end_hour}:00 {mod_settings.timezone})"
    
    return True, ""
