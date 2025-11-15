# model.py
from __future__ import annotations
from sqlalchemy import Text
from sqlalchemy.dialects.postgresql import JSONB, BYTEA
from typing import Optional, List, Dict, Any, Union
import zlib
import json
import struct
from typing import List, Dict, Optional, Generator
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Float, LargeBinary, Index
from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    ForeignKey,
    Enum,
    func,
    JSON,
    Text,
    CheckConstraint,
    UniqueConstraint,
    Float,
    event,
    DDL,
    text,
    Index,
    and_,    # <<< add this
    or_,     # <<< and this
)
from sqlalchemy.orm import validates
from sqlalchemy import Column, String, Text, Boolean, Integer, Enum as SQLEnum
import secrets
import hashlib
import json
from sqlalchemy import Enum
from sqlalchemy.orm import relationship, validates, Session
import enum
from sqlalchemy import JSON
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy import TypeDecorator, Enum
from sqlalchemy.orm import relationship, backref 
from sqlalchemy import TypeDecorator, String
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.sql import func, expression
from sqlalchemy.dialects.postgresql import UUID, JSONB
from uuid import uuid4
from datetime import datetime, timezone, timedelta
from enum import Flag, auto, Enum as PyEnum
from database import Base
import os
from passlib.context import CryptContext
from uuid import uuid4
import logging
logger = logging.getLogger(__name__)

def generate_uuid() -> str:
    """Generate a UUID for unique identifiers"""
    return str(uuid4())

# Password hashing configuration
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
DEFAULT_TIER_RESTRICTIONS = {
    "allowed_tiers": [],
    "is_restricted": False,
    "minimum_cents": 0,
    "allowed_tier_ids": []
}

class UserRole(PyEnum):
    PATREON = "PATREON"
    TEAM = "TEAM"
    CREATOR = "CREATOR"
    KOFI = "KOFI"
    GUEST = "GUEST"

class TaskType(PyEnum):
    PIN_ROTATION = "pin_rotation"
    SYNC_PATRONS = "sync_patrons"
    BACKUP = "backup"

    def __str__(self):
        return self.value

class TaskStatus(PyEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    def __str__(self):
        return self.value

class PlatformType(enum.Enum):
    PATREON = "PATREON"
    KOFI = "KOFI"
    
    def __str__(self):
        return self.value

# Permission class moved to permissions.py for centralized permission management
# Import from permissions module if needed: from permissions import Permission

class PatreonTier(PyEnum):
    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"

class NotificationType(PyEnum):
    COMMENT = "comment"
    REPLY = "reply"
    LIKE = "like"        # Adding like notification type
    SHARE = "share"      # Adding share notification type
    MENTION = "mention"
    NEW_CONTENT = "new_content"
    TIER_UPDATE = "tier_update"
    SYSTEM = "system"

class AuditLogType(PyEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    LOGIN = "login"
    PERMISSION_CHANGE = "permission_change"
    CONTENT_ACCESS = "content_access"

class SegmentStatus(PyEnum):
    PENDING = "pending"    # lowercase to match DB
    READY = "ready"        # lowercase to match DB
    
    def __str__(self):
        return self.value

class TrackStatus(PyEnum):
    COMPLETE = "complete"      # Exactly match database values
    INCOMPLETE = "incomplete"  # Exactly match database values

    def __str__(self):
        return self.value

class BookRequestStatus(PyEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    FULFILLED = "fulfilled"
    
    def __str__(self):
        return self.value

class TTSStatus:
    PENDING = 'pending'
    PROCESSING = 'processing'
    READY = 'ready'
    ERROR = 'error'

class TrackType:
    AUDIO = 'audio'
    TTS = 'tts'

class BookRequest(Base):
    """Stores book requests from users based on their tier quotas"""
    __tablename__ = "book_requests"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(255), nullable=False)
    author = Column(String(255), nullable=False)
    link = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    status = Column(Enum(BookRequestStatus), default=BookRequestStatus.PENDING, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    responded_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    response_message = Column(Text, nullable=True)
    response_date = Column(DateTime(timezone=True), nullable=True)
    month_year = Column(String(7), nullable=False)  # Format: YYYY-MM to track monthly requests
    accepted_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    
    # NEW: Add user reply field
    user_reply = Column(Text, nullable=True)
    
    # Add relationship for accepted_by
    accepted_by = relationship("User", foreign_keys=[accepted_by_id])
    
    # Relationships
    user = relationship("User", foreign_keys=[user_id], back_populates="book_requests")
    responder = relationship("User", foreign_keys=[responded_by_id])
    
    def to_dict(self) -> dict:
        """Convert book request to dictionary representation"""
        result = {
            "id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "author": self.author,
            "link": self.link,
            "description": self.description,
            "status": self.status.value,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "responded_by_id": self.responded_by_id,
            "response_message": self.response_message,
            "response_date": self.response_date.isoformat() if self.response_date else None,
            "month_year": self.month_year,
            "accepted_by_id": self.accepted_by_id,
            "accepted_at": self.accepted_at.isoformat() if self.accepted_at else None,
            "user_reply": self.user_reply  # NEW: Include user_reply in to_dict
        }
        return result

class TrackStatusType(TypeDecorator):
    """Custom type decorator to handle track status enum values"""
    impl = Enum(TrackStatus, native_enum=False)  # Use string storage
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """Convert enum to string when saving to DB"""
        if value is None:
            return None
        return value.value if isinstance(value, TrackStatus) else value

    def process_result_value(self, value, dialect):
        """Convert string from DB to enum"""
        if value is None:
            return None
        try:
            # Handle both string and enum input
            if isinstance(value, TrackStatus):
                return value
            # Map lowercase string values to enum members
            if value == "complete":
                return TrackStatus.COMPLETE
            elif value == "incomplete":
                return TrackStatus.INCOMPLETE
            return TrackStatus(value)
        except (ValueError, AttributeError):
            return TrackStatus.INCOMPLETE  # Default if conversion fails

class Album(Base):
    __tablename__ = "albums"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4, index=True)
    title = Column(String, nullable=False)
    cover_path = Column(String)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True))
    tier_restrictions = Column(
        JSONB,
        nullable=True,
        default=DEFAULT_TIER_RESTRICTIONS
    )
    visibility_status = Column(
        String(20),
        nullable=False,
        default="visible",
        server_default="visible",
        index=True
    )

    # Scheduled visibility change fields
    scheduled_visibility_change_at = Column(
        DateTime(timezone=True),
        nullable=True,
        index=True
    )
    scheduled_visibility_status = Column(
        String(20),
        nullable=True
    )

    # Relationships
    creator = relationship("User", back_populates="albums", foreign_keys=[created_by_id])
    tracks = relationship("Track", back_populates="album", cascade="all, delete-orphan")
    comments = relationship("Comment", back_populates="album", cascade="all, delete-orphan")
    user_management = relationship("UserAlbumManagement", back_populates="album", cascade="all, delete-orphan")

    # NEW relationship for user downloads (ALBUM)
    # Note: Removed problematic primaryjoin with string comparison - let SQLAlchemy infer the join
    user_downloads = relationship(
        "UserDownload",
        back_populates="album",
        foreign_keys="[UserDownload.album_id]",
        cascade="all, delete-orphan"
    )

    def to_dict(self) -> dict:
        """Convert album to dictionary representation"""
        return {
            "id": str(self.id),
            "title": self.title,
            "cover_path": self.cover_path,
            "creator_id": self.created_by_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "tier_restrictions": self.tier_restrictions,
            "visibility_status": self.visibility_status,
            "tracks": [track.to_dict() for track in self.tracks] if self.tracks else []
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Album":
        """Create album instance from dictionary"""
        return cls(
            id=data.get("id"),
            title=data["title"],
            cover_path=data.get("cover_path"),
            created_by_id=data["creator_id"],
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None,
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else None,
            tier_restrictions=data.get("tier_restrictions")
        )
    @property
    def total_plays(self) -> int:
        """Get total plays across all tracks in album"""
        return sum(
            sum(play.play_count for play in track.plays)
            for track in self.tracks
        )

    @property
    def average_completion_rate(self) -> float:
        """Get average completion rate across all tracks"""
        total_completion = 0
        total_tracks_with_plays = 0
        
        for track in self.tracks:
            if track.plays:
                total_completion += track.average_completion_rate
                total_tracks_with_plays += 1
                
        return total_completion / total_tracks_with_plays if total_tracks_with_plays > 0 else 0.0

    @property
    def last_played(self) -> Optional[datetime]:
        """Get most recent play time across all tracks"""
        latest = None
        for track in self.tracks:
            for play in track.plays:
                if not latest or (play.last_played and play.last_played > latest):
                    latest = play.last_played
        return latest

    @classmethod
    def get_popular_albums(cls, db: Session, creator_id: int, limit: int = 10) -> List["Album"]:
        """Get most popular albums by total play count"""
        return (
            db.query(
                cls,
                func.sum(TrackPlays.play_count).label('total_plays'),
                func.avg(TrackPlays.completion_rate).label('avg_completion'),
                func.max(TrackPlays.last_played).label('last_played')
            )
            .join(Track)
            .join(TrackPlays)
            .filter(cls.created_by_id == creator_id)
            .group_by(cls.id)
            .order_by(text('total_plays DESC'))
            .limit(limit)
            .all()
        )

    def get_top_tracks(self, db: Session, limit: int = 5) -> List[Dict]:
        """Get top tracks for this album with play metrics"""
        return (
            db.query(
                Track,
                func.sum(TrackPlays.play_count).label('play_count'),
                func.avg(TrackPlays.completion_rate).label('completion_rate'),
                func.max(TrackPlays.last_played).label('last_played')
            )
            .join(TrackPlays)
            .filter(Track.album_id == self.id)
            .group_by(Track.id)
            .order_by(text('play_count DESC'))
            .limit(limit)
            .all()
        )

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, index=True, nullable=False)  # Remove unique=True
    username = Column(String, index=True, nullable=False)
    password_hash = Column(String, nullable=True)
    creator_pin = Column(String, nullable=True)
    campaign_id = Column(String, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=True)
    last_sync = Column(DateTime(timezone=True), nullable=True)
    patreon_id = Column(String, unique=True, nullable=True)
    patreon_tier_data = Column(MutableDict.as_mutable(JSON), nullable=True, default=lambda: {})
    role = Column(Enum(UserRole), nullable=False)
    is_active = Column(Boolean, server_default='true', nullable=False)  # Fix: Add server_default
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    last_login = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=True)
    stripe_customer_id = Column(String, unique=True, nullable=True)
    track_plays = relationship("TrackPlays", back_populates="user", cascade="all, delete-orphan")
    grace_period_ends_at = Column(DateTime(timezone=True), nullable=True)  # <-- Add this
    forum_settings = relationship("ForumUserSettings", back_populates="user", uselist=False)
    download_reservations = relationship("DownloadReservation", back_populates="user", cascade="all, delete-orphan")
    is_guest_trial = Column(Boolean, default=False, nullable=False)
    trial_started_at = Column(DateTime(timezone=True), nullable=True)
    trial_expires_at = Column(DateTime(timezone=True), nullable=True)
    guest_device_fingerprint = Column(String, nullable=True)
    guest_ip_address = Column(String, nullable=True)
    guest_identifier = Column(String, unique=True, nullable=True)  # Unique identifier for guest
    preferred_voice = Column(String, nullable=True)  # Stores voice IDs like "en-US-AriaNeural"

    @property
    def is_guest(self):
        return self.role == UserRole.GUEST
    
    @property
    def trial_active(self):
        if not self.is_guest_trial or not self.trial_expires_at:
            return False
        return datetime.now(timezone.utc) < self.trial_expires_at
    
    @property
    def trial_hours_remaining(self):
        if not self.trial_active:
            return 0
        delta = self.trial_expires_at - datetime.now(timezone.utc)
        return max(0, delta.total_seconds() / 3600)
        
    @property
    def trial_status(self):
        if not self.is_guest_trial:
            return "not_trial"
        if not self.trial_active:
            return "expired" 
        return "active"
        
    def get_display_name(self) -> str:
        """Get user's preferred display name (falls back to username)"""
        return self.display_name if self.display_name else self.username
    
    # Keep all the existing helper methods you already have:
    def get_forum_display_name(self):
        """Get the display name for forum (alias or username)"""
        if self.forum_settings and self.forum_settings.use_alias and self.forum_settings.display_alias:
            return self.forum_settings.display_alias
        return self.username

    def get_forum_settings(self, db_session):
        """Get user forum settings or create default ones"""
        if not self.forum_settings:
            # Import here to avoid circular imports
            from forum_models import ForumUserSettings
            settings = ForumUserSettings(user_id=self.id)
            db_session.add(settings)
            db_session.flush()
            return settings
        return self.forum_settings

    def should_receive_quick_reply(self, notification_type: str) -> bool:
        """Check if user should receive quick reply notifications"""
        if not self.forum_settings or not self.forum_settings.enable_quick_reply_notifications:
            return False
        
        if notification_type == "mention" and not self.forum_settings.quick_reply_for_mentions:
            return False
        
        if notification_type == "reply" and not self.forum_settings.quick_reply_for_replies:
            return False
        
        return True

    @property
    def is_within_grace_period(self):
        """Check if the user is within their grace period."""
        if not self.grace_period_ends_at:
            return False

        return datetime.now(timezone.utc) < self.grace_period_ends_at



    campaigns = relationship(
        "Campaign", 
        back_populates="creator",
        foreign_keys="Campaign.creator_id",  # Explicit foreign key
        primaryjoin="User.id == Campaign.creator_id"  # Explicit join condition
    )

    book_requests = relationship(
        "BookRequest", 
        back_populates="user", 
        foreign_keys="BookRequest.user_id",
        cascade="all, delete-orphan"
    )
    # Existing relationships
    albums = relationship("Album", back_populates="creator", foreign_keys=[Album.created_by_id])
    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")
    patron_of = relationship(
        "Campaign",
        foreign_keys=[campaign_id],
        back_populates="patrons",
        primaryjoin="User.campaign_id == Campaign.id" 
    )
    album_management = relationship("UserAlbumManagement", back_populates="user", cascade="all, delete-orphan")
    guest_trial_settings = relationship("GuestTrialSettings", back_populates="creator", uselist=False)

    team_members = relationship(
        "User",
        backref="creator",
        remote_side=[id],
        foreign_keys=[created_by]
    )
    subscribed_tiers = relationship(
        "CampaignTier",
        secondary="user_tiers",
        back_populates="patrons"
    )

    
    # NEW relationship for user downloads
    downloads = relationship("UserDownload", back_populates="user", cascade="all, delete-orphan")

    @property
    def is_valid_creator(self) -> bool:
        """Check if user is a valid creator with a campaign ID"""
        return self.is_creator and self.campaign_id is not None


    @property
    def is_creator(self):
        return self.role == UserRole.CREATOR

    @property
    def is_team(self):
        return self.role == UserRole.TEAM

    @property
    def is_patreon(self):
        return self.role == UserRole.PATREON

    def verify_password(self, password: str) -> bool:
        if not self.password_hash:
            return False
        return pwd_context.verify(password, self.password_hash)

    @property
    def requires_password(self):
        return self.role in [UserRole.CREATOR, UserRole.TEAM]

    @property
    def requires_patreon_auth(self):
        return self.role == UserRole.PATREON

    @property
    def tier_level(self) -> int:
        if not self.patreon_tier_data:
            return 0
        return self.patreon_tier_data.get('amount_cents', 0)

    @property
    def display_name(self) -> str:
        """Get user's preferred display name (forum alias or username)"""
        if self.forum_settings and self.forum_settings.use_alias and self.forum_settings.display_alias:
            return self.forum_settings.display_alias
        return self.username
    
    # UPDATE your existing method (4-space indentation):
    def get_display_name(self) -> str:
        """Get user's preferred display name (falls back to username)"""
        return self.display_name
    
    def get_forum_display_name(self):
        """Get the display name for forum (alias or username)"""
        if self.forum_settings and self.forum_settings.use_alias and self.forum_settings.display_alias:
            return self.forum_settings.display_alias
        return self.username

    @property
    def formatted_tier(self) -> str:
        if self.is_creator:
            return "Creator"
        elif self.is_team:
            return "Team Member"
        elif self.is_patreon:
            tier_info = self.get_tier_info()
            return f"Patron ({tier_info['name']})"
        return self.role.value.capitalize()



    def get_campaign_id(self) -> Optional[str]:
        """Get campaign ID, handling both direct field and legacy patreon_tier_data"""
        if self.campaign_id:
            return self.campaign_id
        # Legacy fallback
        if self.patreon_tier_data and 'campaign_id' in self.patreon_tier_data:
            return self.patreon_tier_data['campaign_id']
        return None    


    def get_tier_info(self) -> dict:
        """Get tier information for display - UPDATED to handle all user types"""
        
        # ✅ Handle Guest Trial Users FIRST
        if self.role == UserRole.GUEST and self.is_guest_trial:
            if self.patreon_tier_data:
                return {
                    "name": self.patreon_tier_data.get("title", "Guest Trial"),
                    "description": self.patreon_tier_data.get("tier_description", "Free trial user"),
                    "level": 1,  # Give guest users a level > 0 for display
                    "platform": "kofi",
                    "trial_expires_at": self.patreon_tier_data.get("trial_expires_at"),
                    "patron_status": self.patreon_tier_data.get("patron_status", "active_trial"),
                    "service_type": "Guest Trial"
                }
            else:
                return {
                    "name": "Guest Trial",
                    "description": "Free trial user",
                    "level": 1,
                    "platform": "kofi",
                    "service_type": "Guest Trial"
                }

        # ✅ Handle Team Members
        if self.is_team and self.patreon_tier_data:
            return {
                "name": self.patreon_tier_data.get("title", "Team Member"),
                "description": self.patreon_tier_data.get("tier_description", "Team member access"),
                "level": 99,  # High level for team members
                "platform": "team",
                "service_type": "Team"
            }

        # ✅ Handle Ko-fi Users
        if self.is_kofi and self.patreon_tier_data:
            return {
                "name": self.patreon_tier_data.get("title", "Ko-fi Supporter"),
                "description": self.patreon_tier_data.get("tier_description", "Ko-fi supporter"),
                "amount": f"€{self.patreon_tier_data.get('amount_cents', 0) / 100:.2f}",
                "level": self.patreon_tier_data.get("amount_cents", 0),
                "platform": "kofi",
                "patron_status": self.patreon_tier_data.get("patron_status", "active_patron"),
                "service_type": "Ko-fi"
            }

        # ✅ Handle Patreon Users (existing logic)
        if self.is_patreon and self.patreon_tier_data:
            return {
                "name": self.patreon_tier_data.get('title', 'Unknown Tier'),
                "description": self.patreon_tier_data.get('description', ''),
                "amount": f"${self.patreon_tier_data.get('amount_cents', 0) / 100:.2f}/month",
                "level": self.patreon_tier_data.get('amount_cents', 0),
                "platform": "patreon",
                "patron_status": self.patreon_tier_data.get("patron_status", "active_patron"),
                "service_type": "Patreon"
            }

        # ✅ Default case
        return {
            "name": "No Tier",
            "description": "Not a supporter",
            "level": 0,
            "platform": "none",
            "service_type": "Free"
        }

    __table_args__ = (
        # Composite unique constraint: same email can't exist twice under same creator
        UniqueConstraint('email', 'created_by', name='users_email_creator_unique'),
        
        # Indexes for performance
        Index('ix_users_email_creator', 'email', 'created_by'),
        Index('ix_users_role_active', 'role', 'is_active'),
        Index('ix_users_guest_trial', 'is_guest_trial', 'trial_expires_at'),
        
        # Keep existing check constraints
        CheckConstraint('trial_started_at IS NULL OR trial_expires_at IS NULL OR trial_started_at <= trial_expires_at', 
                       name='check_trial_dates'),
    )

class GuestAbuseTracking(Base):
    """Comprehensive tracking to prevent guest trial abuse"""
    __tablename__ = "guest_abuse_tracking"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, nullable=False, index=True)
    ip_address = Column(String, nullable=False, index=True)
    device_fingerprint = Column(String, nullable=True, index=True)
    browser_fingerprint = Column(Text, nullable=True)  # Detailed browser info
    user_agent = Column(String, nullable=True)
    creator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # Tracking fields
    registration_count = Column(Integer, default=1)
    first_registration = Column(DateTime(timezone=True), server_default=func.now())
    last_registration = Column(DateTime(timezone=True), server_default=func.now())
    is_blocked = Column(Boolean, default=False)
    block_reason = Column(String, nullable=True)
    blocked_until = Column(DateTime(timezone=True), nullable=True)
    
    # Usage tracking
    successful_trials = Column(Integer, default=0)
    failed_attempts = Column(Integer, default=0)
    
    # Relationships
    creator = relationship("User", foreign_keys=[creator_id])

    __table_args__ = (
        Index('ix_guest_abuse_email_creator', 'email', 'creator_id'),
        Index('ix_guest_abuse_ip_creator', 'ip_address', 'creator_id'),
        Index('ix_guest_abuse_device_creator', 'device_fingerprint', 'creator_id'),
        Index('ix_guest_abuse_last_reg', 'last_registration'),
    )

class GuestOTP(Base):
    """Store OTP codes for guest registration with enhanced security and passkey support"""
    __tablename__ = "guest_otp"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, nullable=False, index=True)
    otp_code = Column(String(6), nullable=False)
    creator_pin = Column(String(6), nullable=False)
    creator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # Request details (existing)
    username = Column(String, nullable=False)
    ip_address = Column(String, nullable=True)
    device_fingerprint = Column(String, nullable=True)
    browser_fingerprint = Column(Text, nullable=True)
    user_agent = Column(String, nullable=True)
    
    # NEW: Passkey verification fields (MINIMAL DB CHANGES)
    webauthn_available = Column(Boolean, default=False)
    passkey_check_passed = Column(Boolean, default=True)
    existing_passkey_id = Column(String, nullable=True)
    
    # Security and timing (existing)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    is_used = Column(Boolean, default=False)
    attempt_count = Column(Integer, default=0)
    max_attempts = Column(Integer, default=3)

    # NEW: Staged registration tracking fields
    otp_verified = Column(Boolean, default=False, nullable=False)  # Track if OTP was verified
    registration_completed = Column(Boolean, default=False, nullable=False)  # Track if registration completed
    registration_aborted = Column(Boolean, default=False, nullable=False)  # Track if registration was aborted
    abort_reason = Column(String, nullable=True)  # Why registration was aborted
    completed_at = Column(DateTime(timezone=True), nullable=True)  # When registration completed
    aborted_at = Column(DateTime(timezone=True), nullable=True)  # When registration was aborted
    
    # Rate limiting (existing)
    last_resend = Column(DateTime(timezone=True), nullable=True)
    resend_count = Column(Integer, default=0)
    max_resends = Column(Integer, default=3)
    
    # Relationships
    creator = relationship("User", foreign_keys=[creator_id])
    
    @property
    def is_expired(self):
        return datetime.now(timezone.utc) > self.expires_at
    
    @property
    def can_resend(self):
        if self.resend_count >= self.max_resends:
            return False
        if not self.last_resend:
            return True
        return datetime.now(timezone.utc) > self.last_resend + timedelta(minutes=1)
    
    @property
    def attempts_remaining(self):
        return max(0, self.max_attempts - self.attempt_count)

    __table_args__ = (
        Index('ix_guest_otp_email_creator', 'email', 'creator_id'),
        Index('ix_guest_otp_expires', 'expires_at'),
        Index('ix_guest_otp_created', 'created_at'),
        Index('ix_guest_otp_passkey', 'existing_passkey_id'),
    )
class GuestPasskeyCredential(Base):
    """Store WebAuthn passkey credentials for guest trial abuse prevention"""
    __tablename__ = "guest_passkey_credentials"
    
    id = Column(Integer, primary_key=True, index=True)
    credential_id = Column(String, unique=True, nullable=False, index=True)
    creator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    
    # Credential details
    public_key = Column(Text, nullable=False)
    attestation_object = Column(Text, nullable=True)
    client_data_json = Column(Text, nullable=True)
    
    # User and device info
    email = Column(String, nullable=False, index=True)
    username = Column(String, nullable=False)
    device_fingerprint = Column(String, nullable=True, index=True)
    
    # Tracking
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_used = Column(DateTime(timezone=True), nullable=True)
    use_count = Column(Integer, default=0)
    
    # Status
    is_active = Column(Boolean, default=True)
    is_revoked = Column(Boolean, default=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoked_reason = Column(String, nullable=True)
    
    # Relationships
    creator = relationship("User", foreign_keys=[creator_id])
    user = relationship("User", foreign_keys=[user_id])
    
    __table_args__ = (
        Index('ix_passkey_creator_email', 'creator_id', 'email'),
        Index('ix_passkey_device', 'device_fingerprint', 'creator_id'),
    )
class GuestDeviceTracking(Base):
    """Advanced device tracking for guest users with passkey support"""
    __tablename__ = "guest_device_tracking"
    
    id = Column(Integer, primary_key=True, index=True)
    device_fingerprint = Column(String, nullable=False, index=True)
    creator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # Device details (existing)
    screen_resolution = Column(String, nullable=True)
    timezone_offset = Column(Integer, nullable=True)
    language = Column(String, nullable=True)
    platform = Column(String, nullable=True)
    cookies_enabled = Column(Boolean, nullable=True)
    local_storage_enabled = Column(Boolean, nullable=True)
    session_storage_enabled = Column(Boolean, nullable=True)
    
    # NEW: Passkey tracking fields (MINIMAL DB CHANGES)
    passkey_credential_id = Column(String, nullable=True, index=True)
    passkey_created_at = Column(DateTime(timezone=True), nullable=True)
    
    # Tracking (existing)
    first_seen = Column(DateTime(timezone=True), server_default=func.now())
    last_seen = Column(DateTime(timezone=True), server_default=func.now())
    trial_count = Column(Integer, default=0)
    
    # Status (existing)
    is_suspicious = Column(Boolean, default=False)
    suspicion_reason = Column(String, nullable=True)
    
    # Relationships
    creator = relationship("User", foreign_keys=[creator_id])

class GuestTrialSettings(Base):
    """Creator-specific guest trial settings"""
    __tablename__ = "guest_trial_settings"
    
    id = Column(Integer, primary_key=True, index=True)
    creator_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    
    # Trial configuration
    is_enabled = Column(Boolean, default=True)
    trial_duration_hours = Column(Integer, default=48)  # 2 days
    guest_tier_amount_cents = Column(Integer, default=0)
    
    # Abuse prevention settings
    max_trials_per_ip_per_day = Column(Integer, default=3)
    max_trials_per_email_per_week = Column(Integer, default=1)
    max_trials_per_device_per_month = Column(Integer, default=1)
    
    # OTP settings
    otp_expiry_minutes = Column(Integer, default=10)
    max_otp_attempts = Column(Integer, default=3)
    max_otp_resends = Column(Integer, default=3)
    
    # Notifications
    notify_on_guest_registration = Column(Boolean, default=True)
    notify_on_suspicious_activity = Column(Boolean, default=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    max_daily_registrations = Column(Integer, default=50)  # Max per day (0 = unlimited)
    max_total_registrations = Column(Integer, default=0)   # Total cap (0 = unlimited)
    
    # NEW: Current counters
    current_daily_count = Column(Integer, default=0)
    current_total_count = Column(Integer, default=0)
    last_daily_reset = Column(DateTime(timezone=True), server_default=func.now())
    
    # NEW: Registration tracking
    enable_daily_reset = Column(Boolean, default=True)  # Whether to reset daily
    registration_limit_enabled = Column(Boolean, default=False)  # Master switch for limits

    
    # Relationships
    creator = relationship("User", back_populates="guest_trial_settings")


class GuestRegistrationCount(Base):
    """Track daily registration counts for automatic reset"""
    __tablename__ = "guest_registration_counts"
    
    id = Column(Integer, primary_key=True, index=True)
    creator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    date = Column(DateTime(timezone=True), nullable=False)  # Date for this count
    successful_registrations = Column(Integer, default=0)
    failed_attempts = Column(Integer, default=0)
    
    # Relationships
    creator = relationship("User", foreign_keys=[creator_id])
    
    __table_args__ = (
        UniqueConstraint('creator_id', 'date', name='uq_creator_date_count'),
        Index('ix_guest_reg_count_creator_date', 'creator_id', 'date'),
    )


class GuestTrialService:
    def __init__(self, db: Session):
        self.db = db
        self.default_trial_duration_hours = 48
        self.default_guest_tier_amount_cents = 0


    async def check_passkey_abuse(
        self, 
        credential_id: str = None,
        device_fingerprint: str = None,
        creator_id: int = None
    ) -> dict:
        """Check if a passkey credential already exists for this creator"""
        
        if not credential_id and not device_fingerprint:
            return {"allowed": True}
            
        # Check for existing credential ID
        if credential_id:
            existing_credential = self.db.query(GuestPasskeyCredential).filter(
                and_(
                    GuestPasskeyCredential.credential_id == credential_id,
                    GuestPasskeyCredential.creator_id == creator_id,
                    GuestPasskeyCredential.is_active == True,
                    GuestPasskeyCredential.is_revoked == False
                )
            ).first()
            
            if existing_credential:
                return {
                    "allowed": False,
                    "reason": "This device already has a trial passkey registered",
                    "code": "PASSKEY_EXISTS",
                    "credential_id": credential_id,
                    "registered_email": existing_credential.email,
                    "created_at": existing_credential.created_at.isoformat()
                }
        
        # Check device tracking table for passkey
        if device_fingerprint:
            device_with_passkey = self.db.query(GuestDeviceTracking).filter(
                and_(
                    GuestDeviceTracking.device_fingerprint == device_fingerprint,
                    GuestDeviceTracking.creator_id == creator_id,
                    GuestDeviceTracking.passkey_credential_id.isnot(None),
                    GuestDeviceTracking.trial_count > 0
                )
            ).first()
            
            if device_with_passkey:
                return {
                    "allowed": False,
                    "reason": "This device already has a trial passkey registered",
                    "code": "DEVICE_HAS_PASSKEY",
                    "device_passkey_id": device_with_passkey.passkey_credential_id,
                    "created_at": device_with_passkey.passkey_created_at.isoformat() if device_with_passkey.passkey_created_at else None
                }
        
        return {"allowed": True}

    def store_passkey_credential(
        self,
        credential_data: dict,
        user_data: dict,
        creator_id: int,
        user_id: int = None
    ) -> str:
        """Store WebAuthn passkey credential for abuse prevention"""
        
        try:
            # Create passkey credential record
            passkey_credential = GuestPasskeyCredential(
                credential_id=credential_data['credentialId'],
                creator_id=creator_id,
                user_id=user_id,
                public_key=credential_data['publicKey'],
                attestation_object=credential_data.get('attestationObject'),
                client_data_json=credential_data.get('clientDataJSON'),
                email=user_data['email'],
                username=user_data['username'],
                device_fingerprint=user_data.get('device_fingerprint'),
                is_active=True
            )
            
            self.db.add(passkey_credential)
            
            # Update device tracking with passkey info
            if user_data.get('device_fingerprint'):
                device_tracking = self.db.query(GuestDeviceTracking).filter(
                    and_(
                        GuestDeviceTracking.device_fingerprint == user_data['device_fingerprint'],
                        GuestDeviceTracking.creator_id == creator_id
                    )
                ).first()
                
                if device_tracking:
                    device_tracking.passkey_credential_id = credential_data['credentialId']
                    device_tracking.passkey_created_at = datetime.now(timezone.utc)
                    device_tracking.last_seen = datetime.now(timezone.utc)
                else:
                    # Create new device tracking record
                    new_device_tracking = GuestDeviceTracking(
                        device_fingerprint=user_data['device_fingerprint'],
                        creator_id=creator_id,
                        passkey_credential_id=credential_data['credentialId'],
                        passkey_created_at=datetime.now(timezone.utc),
                        trial_count=1,
                        language=user_data.get('language', 'unknown')[:10],
                        platform=user_data.get('platform', 'unknown')[:50]
                    )
                    self.db.add(new_device_tracking)
            
            self.db.flush()
            
            logger.info(f"✅ Stored passkey credential {credential_data['credentialId'][:16]}... for creator {creator_id}")
            return credential_data['credentialId']
            
        except Exception as e:
            logger.error(f"Error storing passkey credential: {str(e)}")
            raise

    async def comprehensive_abuse_check(
        self, 
        email: str, 
        device_data: dict, 
        creator_id: int,
        existing_passkey_id: str = None
    ) -> dict:
        """Comprehensive abuse checking with both light and strict layers"""
        
        # Layer 1: Light checks (email, device signature)
        light_check = await light_trial_abuse_check(email, device_data, creator_id, self.db)
        if not light_check["allowed"]:
            logger.info(f"Light layer blocked registration for {email}: {light_check['reason']}")
            return light_check
        
        # Layer 2: Passkey checks (if WebAuthn available)
        if device_data.get('webauthn_available') and existing_passkey_id:
            passkey_check = await self.check_passkey_abuse(
                credential_id=existing_passkey_id,
                device_fingerprint=light_check.get('device_signature'),
                creator_id=creator_id
            )
            
            if not passkey_check["allowed"]:
                logger.info(f"Passkey layer blocked registration for {email}: {passkey_check['reason']}")
                return passkey_check
        
        return {
            "allowed": True, 
            "device_signature": light_check.get('device_signature'),
            "layers_passed": ["light", "passkey"] if device_data.get('webauthn_available') else ["light"]
        }

    def create_guest_otp_with_passkey_data(
        self,
        email: str,
        username: str,
        creator_pin: str,
        creator_id: int,
        request_data: dict
    ) -> GuestOTP:
        """Create OTP record with passkey verification data"""
        
        settings = self.get_or_create_trial_settings(creator_id)
        device_fingerprint = self.generate_device_fingerprint(request_data)
        
        # Clean up old OTPs for this email/creator
        self.db.query(GuestOTP).filter(
            and_(
                GuestOTP.email == email.lower(),
                GuestOTP.creator_id == creator_id,
                GuestOTP.is_used == False
            )
        ).delete()
        
        otp_code = self.generate_otp()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.otp_expiry_minutes)
        
        guest_otp = GuestOTP(
            email=email.lower(),
            username=username,
            otp_code=otp_code,
            creator_pin=creator_pin,
            creator_id=creator_id,
            expires_at=expires_at,
            ip_address=request_data.get('ip_address'),
            device_fingerprint=device_fingerprint,
            browser_fingerprint=request_data.get('browser_fingerprint'),
            user_agent=request_data.get('user_agent'),
            max_attempts=settings.max_otp_attempts,
            max_resends=settings.max_otp_resends,
            # Passkey data
            webauthn_available=request_data.get('webauthn_available', False),
            passkey_check_passed=request_data.get('passkey_check_passed', True),
            existing_passkey_id=request_data.get('existing_passkey_id')
        )
        
        self.db.add(guest_otp)
        self.db.flush()
        
        return guest_otp

    def abort_registration(self, otp_id: int, reason: str = "user_cancelled") -> bool:
        """Abort a guest trial registration"""
        try:
            guest_otp = self.db.query(GuestOTP).filter(
                and_(
                    GuestOTP.id == otp_id,
                    GuestOTP.registration_completed == False
                )
            ).first()
            
            if guest_otp:
                guest_otp.registration_aborted = True
                guest_otp.abort_reason = reason
                guest_otp.is_used = True
                self.db.commit()
                
                logger.info(f"Aborted guest trial registration for {guest_otp.email}, reason: {reason}")
                return True
                
            return False
            
        except Exception as e:
            logger.error(f"Error aborting registration: {str(e)}")
            self.db.rollback()
            return False

    
    def generate_device_fingerprint(self, request_data: dict) -> str:
        """Generate device fingerprint from request data"""
        fingerprint_data = {
            'user_agent': request_data.get('user_agent', ''),
            'screen_width': str(request_data.get('screen_width', 0)),
            'screen_height': str(request_data.get('screen_height', 0)),
            'color_depth': str(request_data.get('color_depth', 24)),
            'hardware_concurrency': str(request_data.get('hardware_concurrency', 0)),
            'platform': request_data.get('platform', 'unknown')[:20],
            'timezone': request_data.get('timezone', 'unknown')[:30],
            'language': request_data.get('language', 'unknown')[:10]
        }
        
        # Create hash of combined data
        fingerprint_string = '|'.join(f"{k}:{v}" for k, v in sorted(fingerprint_data.items()))
        return hashlib.sha256(fingerprint_string.encode()).hexdigest()[:32]

    
    def get_or_create_trial_settings(self, creator_id: int) -> GuestTrialSettings:
        """Get creator's trial settings or create default ones"""
        settings = self.db.query(GuestTrialSettings).filter(
            GuestTrialSettings.creator_id == creator_id
        ).first()
        
        if not settings:
            settings = GuestTrialSettings(
                creator_id=creator_id,
                is_enabled=True,
                trial_duration_hours=self.default_trial_duration_hours,
                guest_tier_amount_cents=self.default_guest_tier_amount_cents
            )
            self.db.add(settings)
            self.db.flush()
            
        return settings


    
    async def check_abuse_prevention(
        self, 
        email: str, 
        ip_address: str, 
        device_fingerprint: str,
        creator_id: int
    ) -> dict:
        """Comprehensive abuse checking"""
        
        settings = self.get_or_create_trial_settings(creator_id)
        
        if not settings.is_enabled:
            return {
                "allowed": False,
                "reason": "Guest trials are disabled for this creator",
                "code": "TRIALS_DISABLED"
            }
        
        now = datetime.now(timezone.utc)
        
        # Check if user is blocked
        blocked_tracking = self.db.query(GuestAbuseTracking).filter(
            and_(
                or_(
                    GuestAbuseTracking.email == email.lower(),
                    GuestAbuseTracking.ip_address == ip_address,
                    GuestAbuseTracking.device_fingerprint == device_fingerprint
                ),
                GuestAbuseTracking.creator_id == creator_id,
                GuestAbuseTracking.is_blocked == True,
                or_(
                    GuestAbuseTracking.blocked_until.is_(None),
                    GuestAbuseTracking.blocked_until > now
                )
            )
        ).first()
        
        if blocked_tracking:
            return {
                "allowed": False,
                "reason": f"Blocked: {blocked_tracking.block_reason}",
                "code": "BLOCKED",
                "blocked_until": blocked_tracking.blocked_until.isoformat() if blocked_tracking.blocked_until else None
            }
        
        # Check email frequency
        email_check = self.db.query(GuestAbuseTracking).filter(
            and_(
                GuestAbuseTracking.email == email.lower(),
                GuestAbuseTracking.creator_id == creator_id,
                GuestAbuseTracking.last_registration > now - timedelta(days=7)
            )
        ).first()
        
        if email_check and email_check.successful_trials >= settings.max_trials_per_email_per_week:
            return {
                "allowed": False,
                "reason": "Email recently used for trial",
                "code": "EMAIL_COOLDOWN",
                "retry_after": (email_check.last_registration + timedelta(days=7)).isoformat()
            }
        
        # Check IP frequency
        ip_count = self.db.query(func.count(GuestAbuseTracking.id)).filter(
            and_(
                GuestAbuseTracking.ip_address == ip_address,
                GuestAbuseTracking.creator_id == creator_id,
                GuestAbuseTracking.last_registration > now - timedelta(days=1),
                GuestAbuseTracking.successful_trials > 0
            )
        ).scalar()
        
        if ip_count >= settings.max_trials_per_ip_per_day:
            return {
                "allowed": False,
                "reason": "Too many trials from this IP today",
                "code": "IP_LIMIT",
                "retry_after": (now + timedelta(days=1)).isoformat()
            }
        
        # Check device fingerprint
        if device_fingerprint:
            device_check = self.db.query(GuestDeviceTracking).filter(
                and_(
                    GuestDeviceTracking.device_fingerprint == device_fingerprint,
                    GuestDeviceTracking.creator_id == creator_id,
                    GuestDeviceTracking.trial_count >= settings.max_trials_per_device_per_month,
                    GuestDeviceTracking.last_seen > now - timedelta(days=30)
                )
            ).first()
            
            if device_check:
                return {
                    "allowed": False,
                    "reason": "Device recently used for trial",
                    "code": "DEVICE_LIMIT",
                    "retry_after": (device_check.last_seen + timedelta(days=30)).isoformat()
                }
        
        return {"allowed": True, "code": "APPROVED"}
    
    def record_registration_attempt(
        self,
        email: str,
        ip_address: str,
        device_fingerprint: str,
        creator_id: int,
        browser_fingerprint: str = None,
        user_agent: str = None,
        success: bool = False
    ):
        """Record guest registration attempt with detailed tracking"""
        
        # Update or create abuse tracking
        tracking = self.db.query(GuestAbuseTracking).filter(
            and_(
                GuestAbuseTracking.email == email.lower(),
                GuestAbuseTracking.creator_id == creator_id
            )
        ).first()
        
        if tracking:
            tracking.registration_count += 1
            tracking.last_registration = datetime.now(timezone.utc)
            tracking.ip_address = ip_address
            if device_fingerprint:
                tracking.device_fingerprint = device_fingerprint
            if browser_fingerprint:
                tracking.browser_fingerprint = browser_fingerprint
            if success:
                tracking.successful_trials += 1
            else:
                tracking.failed_attempts += 1
        else:
            tracking = GuestAbuseTracking(
                email=email.lower(),
                ip_address=ip_address,
                device_fingerprint=device_fingerprint,
                browser_fingerprint=browser_fingerprint,
                user_agent=user_agent,
                creator_id=creator_id,
                successful_trials=1 if success else 0,
                failed_attempts=0 if success else 1
            )
            self.db.add(tracking)
        
        # Update device tracking
        if device_fingerprint:
            device_tracking = self.db.query(GuestDeviceTracking).filter(
                and_(
                    GuestDeviceTracking.device_fingerprint == device_fingerprint,
                    GuestDeviceTracking.creator_id == creator_id
                )
            ).first()
            
            if device_tracking:
                device_tracking.last_seen = datetime.now(timezone.utc)
                if success:
                    device_tracking.trial_count += 1
            else:
                device_tracking = GuestDeviceTracking(
                    device_fingerprint=device_fingerprint,
                    creator_id=creator_id,
                    trial_count=1 if success else 0
                )
                self.db.add(device_tracking)
    
    async def get_or_create_guest_tier(self, creator_id: int) -> CampaignTier:
        """Get existing guest tier or create new one with creator's current settings"""
        
        settings = self.get_or_create_trial_settings(creator_id)
        
        guest_tier = self.db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == creator_id,
                CampaignTier.title == "Guest Trial",
                CampaignTier.platform_type == "KOFI",
                CampaignTier.is_active == True
            )
        ).first()
        
        if guest_tier:
            # ✅ ALWAYS update amount to current setting
            guest_tier.amount_cents = settings.guest_tier_amount_cents
            guest_tier.updated_at = datetime.now(timezone.utc)
            return guest_tier
        
        # ✅ Create new guest tier with current settings amount
        guest_tier = CampaignTier(
            creator_id=creator_id,
            title="Guest Trial",
            description=f"{settings.trial_duration_hours}-hour free trial for new users",
            amount_cents=settings.guest_tier_amount_cents,  # ✅ Use current settings
            patron_count=0,
            platform_type="KOFI",
            is_active=True,
            album_downloads_allowed=0,  # Creator can modify in benefits page
            track_downloads_allowed=0,  # Creator can modify in benefits page
            book_requests_allowed=0,    # Creator can modify in benefits page
            chapters_allowed_per_book_request=0,  # Creator can modify
            max_sessions=1,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        self.db.add(guest_tier)
        self.db.flush()
        
        logger.info(f"Created new guest trial tier for creator {creator_id} with amount {settings.guest_tier_amount_cents} cents")
        return guest_tier

    
    def generate_otp(self) -> str:
        """Generate secure 6-digit OTP"""
        return f"{secrets.randbelow(900000) + 100000:06d}"
    
    def create_guest_otp(
        self,
        email: str,
        username: str,
        creator_pin: str,
        creator_id: int,
        request_data: dict
    ) -> GuestOTP:
        """Create OTP record for guest registration"""
        
        settings = self.get_or_create_trial_settings(creator_id)
        device_fingerprint = self.generate_device_fingerprint(request_data)
        
        # Clean up old OTPs for this email/creator
        self.db.query(GuestOTP).filter(
            and_(
                GuestOTP.email == email.lower(),
                GuestOTP.creator_id == creator_id,
                GuestOTP.is_used == False
            )
        ).delete()
        
        otp_code = self.generate_otp()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.otp_expiry_minutes)
        
        guest_otp = GuestOTP(
            email=email.lower(),
            username=username,
            otp_code=otp_code,
            creator_pin=creator_pin,
            creator_id=creator_id,
            expires_at=expires_at,
            ip_address=request_data.get('ip_address'),
            device_fingerprint=device_fingerprint,
            browser_fingerprint=request_data.get('browser_fingerprint'),
            user_agent=request_data.get('user_agent'),
            max_attempts=settings.max_otp_attempts,
            max_resends=settings.max_otp_resends
        )
        
        self.db.add(guest_otp)
        self.db.flush()
        
        return guest_otp



    def check_registration_limits(self, creator_id: int) -> dict:
        """Check if registration limits have been reached"""
        
        settings = self.get_or_create_trial_settings(creator_id)
        
        # If limits are disabled, allow registration
        if not settings.registration_limit_enabled:
            return {"allowed": True, "code": "LIMITS_DISABLED"}
        
        # Reset daily counter if needed
        self.reset_daily_counter_if_needed(creator_id)
        
        # Check daily limit
        if settings.max_daily_registrations > 0:
            if settings.current_daily_count >= settings.max_daily_registrations:
                next_reset = self.get_next_reset_time(settings.last_daily_reset)
                return {
                    "allowed": False,
                    "reason": f"Daily registration limit reached ({settings.max_daily_registrations})",
                    "code": "DAILY_LIMIT_REACHED",
                    "current_count": settings.current_daily_count,
                    "max_daily": settings.max_daily_registrations,
                    "next_reset": next_reset.isoformat(),
                    "hours_until_reset": self.get_hours_until_reset(next_reset)
                }
        
        # Check total limit
        if settings.max_total_registrations > 0:
            if settings.current_total_count >= settings.max_total_registrations:
                return {
                    "allowed": False,
                    "reason": f"Maximum total registrations reached ({settings.max_total_registrations})",
                    "code": "TOTAL_LIMIT_REACHED",
                    "current_total": settings.current_total_count,
                    "max_total": settings.max_total_registrations
                }
        
        return {
            "allowed": True,
            "code": "WITHIN_LIMITS",
            "daily_remaining": (settings.max_daily_registrations - settings.current_daily_count) 
                             if settings.max_daily_registrations > 0 else "unlimited",
            "total_remaining": (settings.max_total_registrations - settings.current_total_count) 
                             if settings.max_total_registrations > 0 else "unlimited"
        }

    def reset_daily_counter_if_needed(self, creator_id: int):
        """Reset daily counter if 24 hours have passed"""
        
        settings = self.get_or_create_trial_settings(creator_id)
        
        if not settings.enable_daily_reset:
            return
        
        now = datetime.now(timezone.utc)
        last_reset = settings.last_daily_reset or now
        
        # Check if 24 hours have passed
        if now - last_reset >= timedelta(hours=24):
            logger.info(f"Resetting daily registration counter for creator {creator_id}")
            
            settings.current_daily_count = 0
            settings.last_daily_reset = now
            
            # Create daily tracking record
            today = now.replace(hour=0, minute=0, second=0, microsecond=0)
            daily_record = self.db.query(GuestRegistrationCount).filter(
                and_(
                    GuestRegistrationCount.creator_id == creator_id,
                    GuestRegistrationCount.date == today
                )
            ).first()
            
            if not daily_record:
                daily_record = GuestRegistrationCount(
                    creator_id=creator_id,
                    date=today,
                    successful_registrations=0,
                    failed_attempts=0
                )
                self.db.add(daily_record)
            
            self.db.commit()

    def increment_registration_count(self, creator_id: int, success: bool = True):
        """Increment registration counters"""
        
        settings = self.get_or_create_trial_settings(creator_id)
        
        if success:
            settings.current_daily_count += 1
            settings.current_total_count += 1
            
            # Update daily tracking
            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            daily_record = self.db.query(GuestRegistrationCount).filter(
                and_(
                    GuestRegistrationCount.creator_id == creator_id,
                    GuestRegistrationCount.date == today
                )
            ).first()
            
            if daily_record:
                daily_record.successful_registrations += 1
            else:
                daily_record = GuestRegistrationCount(
                    creator_id=creator_id,
                    date=today,
                    successful_registrations=1,
                    failed_attempts=0
                )
                self.db.add(daily_record)
        
        self.db.commit()
        
        logger.info(f"Updated registration count for creator {creator_id}: "
                   f"daily={settings.current_daily_count}, total={settings.current_total_count}")

    def get_next_reset_time(self, last_reset: datetime) -> datetime:
        """Calculate next reset time (24 hours from last reset)"""
        return last_reset + timedelta(hours=24)

    def get_hours_until_reset(self, next_reset: datetime) -> float:
        """Get hours until next reset"""
        now = datetime.now(timezone.utc)
        if next_reset <= now:
            return 0.0
        delta = next_reset - now
        return delta.total_seconds() / 3600

    def get_registration_stats(self, creator_id: int) -> dict:
        """Get comprehensive registration statistics"""
        
        settings = self.get_or_create_trial_settings(creator_id)
        self.reset_daily_counter_if_needed(creator_id)
        
        # Get today's stats
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        today_record = self.db.query(GuestRegistrationCount).filter(
            and_(
                GuestRegistrationCount.creator_id == creator_id,
                GuestRegistrationCount.date == today
            )
        ).first()
        
        # Get last 7 days stats
        week_ago = today - timedelta(days=7)
        weekly_stats = self.db.query(
            func.sum(GuestRegistrationCount.successful_registrations).label('total_success'),
            func.sum(GuestRegistrationCount.failed_attempts).label('total_failed'),
            func.count(GuestRegistrationCount.id).label('active_days')
        ).filter(
            and_(
                GuestRegistrationCount.creator_id == creator_id,
                GuestRegistrationCount.date >= week_ago
            )
        ).first()
        
        next_reset = self.get_next_reset_time(settings.last_daily_reset)
        
        return {
            "limits_enabled": settings.registration_limit_enabled,
            "daily_limit": settings.max_daily_registrations,
            "total_limit": settings.max_total_registrations,
            "current_daily_count": settings.current_daily_count,
            "current_total_count": settings.current_total_count,
            "daily_remaining": (settings.max_daily_registrations - settings.current_daily_count) 
                             if settings.max_daily_registrations > 0 else "unlimited",
            "total_remaining": (settings.max_total_registrations - settings.current_total_count) 
                             if settings.max_total_registrations > 0 else "unlimited",
            "next_reset": next_reset.isoformat(),
            "hours_until_reset": self.get_hours_until_reset(next_reset),
            "today_stats": {
                "successful": today_record.successful_registrations if today_record else 0,
                "failed": today_record.failed_attempts if today_record else 0
            },
            "weekly_stats": {
                "successful": weekly_stats.total_success or 0,
                "failed": weekly_stats.total_failed or 0,
                "active_days": weekly_stats.active_days or 0
            }
        }

    def reset_counters(self, creator_id: int, reset_daily: bool = True, reset_total: bool = False):
        """Manually reset registration counters"""
        
        settings = self.get_or_create_trial_settings(creator_id)
        
        if reset_daily:
            settings.current_daily_count = 0
            settings.last_daily_reset = datetime.now(timezone.utc)
            logger.info(f"Manual daily counter reset for creator {creator_id}")
        
        if reset_total:
            settings.current_total_count = 0
            logger.info(f"Manual total counter reset for creator {creator_id}")
        
        self.db.commit()


class Track(Base):
    __tablename__ = "tracks"

    # Primary Key
    id = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    file_path = Column(String, nullable=False)

    # FKs
    album_id = Column(UUID(as_uuid=True), ForeignKey("albums.id"), nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    visibility_status = Column(
        String(20),
        nullable=False,
        default="visible",
        server_default="visible",
        index=True
    )

    # Scheduled visibility change fields
    scheduled_visibility_change_at = Column(
        DateTime(timezone=True),
        nullable=True,
        index=True
    )
    scheduled_visibility_status = Column(
        String(20),
        nullable=True
    )

    # Processing/Status
    upload_status = Column(
        String, nullable=False, default="pending", server_default="pending"
    )  # pending, uploading, processing, complete, failed

    processing_locked_at = Column(DateTime(timezone=True), nullable=True)
    processing_type = Column(String(20), nullable=True)  # 'initial' | 'regeneration'
    failed_at = Column(DateTime(timezone=True), nullable=True)

    # TTS / segmentation (keep string statuses)
    status = Column(
        String(20),
        nullable=False,
        default="generating",
        server_default="generating",
        index=True,
    )
    segmentation_status = Column(
        String,
        nullable=False,
        default="incomplete",
        server_default="incomplete",
    )
    # Optional: enforce allowed values with a check
    __table_args__ = (
        CheckConstraint(
            "segmentation_status IN ('incomplete','segmenting','complete','failed')",
            name="check_segmentation_status",
        ),
    )

    # Source text & content versioning
    source_text_path = Column(String(500), nullable=True, index=True)
    source_text_hash = Column(String(64), nullable=True, index=True)
    source_text_size = Column(Integer, nullable=True)
    source_text_compressed_size = Column(Integer, nullable=True)
    content_version = Column(Integer, default=1, nullable=False)

    # Which voice is currently being processed (if any)
    processing_voice = Column(String(50), nullable=True, index=True)

    # Last processing error message (optional)
    processing_error = Column(Text, nullable=True)

    # Whether HLS is ready (status_lock toggles this on successful segmentation)
    hls_ready = Column(Boolean, nullable=False, default=False, server_default=text("false"))

    # When TTS finished (you set this in process_enhanced_voice_tts_track)
    tts_completed_at = Column(DateTime(timezone=True), nullable=True)

    # Where you store “voice-en-US-XYZ” dir name (you set this after upload)
    voice_directory = Column(String(255), nullable=True)

# List of voices available for this track (you append to it)
    available_voices = Column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )


    # Audio Metadata
    duration = Column(Float, nullable=True)
    codec = Column(String(50), nullable=True)
    bit_rate = Column(Integer, nullable=True)
    sample_rate = Column(Integer, nullable=True)
    channels = Column(Integer, nullable=True)
    format = Column(String(50), nullable=True)
    audio_metadata = Column(
        JSONB,
        nullable=True,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    # Track Organization
    order = Column(Integer, nullable=True)

    # Access Control
    tier_requirements = Column(
        JSONB,
        nullable=True,
        default=lambda: {"is_public": True, "minimum_cents": 0, "allowed_tier_ids": []},
        server_default=text(
            '{"is_public": true, "minimum_cents": 0, "allowed_tier_ids": []}' " ::jsonb"
        ),
    )

    # Usage Stats
    last_accessed = Column(DateTime(timezone=True), nullable=True)
    access_count = Column(Integer, nullable=True, server_default=text("0"))

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    # TTS / Read-Along
    track_type = Column(  # string type: 'audio' | 'tts'
        String(50),
        nullable=False,
        default="audio",
        server_default="audio",
    )
    source_text = Column(Text, nullable=True)
    default_voice = Column(String(50), nullable=True)

    text_chunks = Column(  # array JSONB
        JSONB,
        nullable=True,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    voice_segments = Column(  # object JSONB
        JSONB,
        nullable=True,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    has_read_along = Column(Boolean, nullable=False, default=True, server_default=text("true"))

    tts_status = Column(  # 'pending' | 'processing' | 'ready' | 'error'
        String(20),
        nullable=True,
        default="pending",
        server_default="pending",
    )
    tts_progress = Column(Integer, nullable=True, default=0, server_default=text("0"))

    # Relationships
    tts_segments = relationship(
        "TTSTextSegment", back_populates="track", cascade="all, delete-orphan"
    )
    tts_meta = relationship(
        "TTSTrackMeta", back_populates="track", uselist=False, cascade="all, delete-orphan"
    )
    album = relationship("Album", back_populates="tracks")
    creator = relationship("User", foreign_keys=[created_by_id])
    comments = relationship("Comment", back_populates="track", cascade="all, delete-orphan")
    playback_history = relationship(
        "PlaybackProgress", back_populates="track", cascade="all, delete-orphan"
    )
    plays = relationship("TrackPlays", back_populates="track", cascade="all, delete-orphan")
    # Note: Removed problematic primaryjoin with string comparison - let SQLAlchemy infer the join
    user_downloads = relationship(
        "UserDownload",
        back_populates="track",
        foreign_keys="[UserDownload.track_id]",
        cascade="all, delete-orphan"
    )
    file_storage_metadata = relationship(
        "FileStorageMetadata", back_populates="track", cascade="all, delete-orphan"
    )

    # Indexes / Constraints (append to previous __table_args__)
    __table_args__ = __table_args__ + (
        Index("ix_tracks_album_id", "album_id"),
        Index("ix_tracks_created_by_id", "created_by_id"),
        Index("ix_tracks_duration", "duration"),
        Index("ix_tracks_bit_rate", "bit_rate"),
        Index("idx_tracks_processing_locked", "processing_locked_at"),
        CheckConstraint("duration >= 0", name="check_positive_duration"),
        CheckConstraint("bit_rate > 0", name="check_positive_bit_rate"),
        CheckConstraint("sample_rate > 0", name="check_positive_sample_rate"),
        CheckConstraint("channels > 0", name="check_positive_channels"),
        CheckConstraint("tts_progress >= 0 AND tts_progress <= 100", name="check_tts_progress_range"),
        CheckConstraint("track_type IN ('audio','tts')", name="check_track_type_valid"),

        # ✅ Updated: allow FS-backed text (source_text_path) for TTS,
        # and require neither text field for pure audio tracks.
        CheckConstraint(
            "("
            "  (track_type = 'audio' AND (source_text IS NULL AND source_text_path IS NULL))"
            "  OR"
            "  (track_type = 'tts'   AND (source_text IS NOT NULL OR source_text_path IS NOT NULL))"
            ")",
            name="check_tts_text_required",
        ),

        Index("ix_tracks_track_type", "track_type"),
        Index("ix_tracks_tts_status", "tts_status"),
        Index("ix_tracks_has_read_along", "has_read_along"),
    )

    def to_dict(self) -> dict:
        """Convert track to dictionary representation with complete TTS metadata"""
        return {
            "id": self.id,
            "title": self.title,
            "file_path": self.file_path,
            "album_id": str(self.album_id),
            "created_by_id": self.created_by_id,
            "visibility_status": self.visibility_status,

            # Audio metadata
            "duration": float(self.duration) if self.duration else None,
            "codec": self.codec,
            "bit_rate": self.bit_rate,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "format": self.format,
            "audio_metadata": self.audio_metadata or {},

            # Track organization
            "order": self.order,
            "upload_status": self.upload_status,
            "processing_locked_at": self.processing_locked_at.isoformat() if self.processing_locked_at else None,
            "processing_type": self.processing_type,
            "tier_requirements": self.tier_requirements,
            "access_count": self.access_count,

            # TTS fields - CRITICAL for frontend detection
            "track_type": self.track_type,
            "is_tts_track": self.is_tts_track,
            "source_text": self.source_text,
            "default_voice": self.default_voice,
            "has_read_along": self.has_read_along,
            "tts_status": self.tts_status,
            "tts_progress": self.tts_progress,
            "text_chunks": self.text_chunks or [],
            "voice_segments": self.voice_segments or {},

            # Computed TTS properties
            "supports_voice_switching": self.supports_voice_switching,
            "supports_read_along": self.supports_read_along,
            "chunk_count": self.get_chunk_count(),
            "word_count": self.get_word_count(),

            # Timestamps
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_accessed": self.last_accessed.isoformat() if self.last_accessed else None
        }

    @property
    def is_tts_track(self) -> bool:
        """Check if this is a TTS-generated track"""
        return self.track_type == 'tts'
    
    @property
    def supports_voice_switching(self) -> bool:
        """Check if track supports live voice switching"""
        return self.is_tts_track and bool(self.text_chunks)
    
    @property
    def supports_read_along(self) -> bool:
        """Check if track supports read-along functionality"""
        return self.is_tts_track and self.has_read_along and bool(self.text_chunks)
    
    def get_chunk_count(self) -> int:
        """Get number of text chunks"""
        return len(self.text_chunks) if self.text_chunks else 0
    
    def get_word_count(self) -> int:
        """Get total word count from source text"""
        if not self.source_text:
            return 0
        return len(self.source_text.split())
    
    def update_tts_progress(self, progress: int, status: str = None):  # ← Changed TTSStatus to str
        """Update TTS processing progress"""
        self.tts_progress = max(0, min(100, progress))
        if status:
            self.tts_status = status
    
    def set_text_chunks(self, chunks: list):
        """Set text chunks with validation"""
        if not isinstance(chunks, list):
            raise ValueError("Chunks must be a list")
        
        # Validate chunk structure
        for i, chunk in enumerate(chunks):
            required_fields = ['index', 'text', 'start_time', 'end_time', 'words']
            if not all(field in chunk for field in required_fields):
                raise ValueError(f"Chunk {i} missing required fields: {required_fields}")
        
        self.text_chunks = chunks
        self.has_read_along = len(chunks) > 0
    
    def get_chunk_at_time(self, time_seconds: float) -> dict:
        """Get the chunk that should be active at given time"""
        if not self.text_chunks:
            return None
            
        for chunk in self.text_chunks:
            if chunk['start_time'] <= time_seconds <= chunk['end_time']:
                return chunk
        return None
    
    def get_voice_options(self) -> list:
        """Get available voice options for this track"""
        if not self.is_tts_track:
            return []
        
        return [
            {"id": "en-US-AvaNeural", "name": "Ava", "gender": "female"},
            {"id": "en-US-AriaNeural", "name": "Aria", "gender": "female"}, 
            {"id": "en-US-GuyNeural", "name": "Guy", "gender": "male"},
            {"id": "en-US-JennyNeural", "name": "Jenny", "gender": "female"}
        ]
    
    def to_dict(self) -> dict:
        """Enhanced to_dict with TTS fields"""
        base_dict = {
            "id": self.id,
            "title": self.title,
            "file_path": self.file_path,
            "album_id": str(self.album_id),
            "created_by_id": self.created_by_id,
            "visibility_status": self.visibility_status,
            # Audio metadata
            "duration": float(self.duration) if self.duration else None,
            "codec": self.codec,
            "bit_rate": self.bit_rate,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "format": self.format,
            "audio_metadata": self.audio_metadata or {},
            # Other fields
            "order": self.order,
            "tier_requirements": self.tier_requirements,
            "access_count": self.access_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }
        
        # Add TTS-specific fields
        base_dict.update({
            "track_type": self.track_type,  # ← Changed from self.track_type.value to self.track_type
            "is_tts_track": self.is_tts_track,
            "source_text": self.source_text,
            "default_voice": self.default_voice,
            "has_read_along": self.has_read_along,
            "supports_voice_switching": self.supports_voice_switching,
            "supports_read_along": self.supports_read_along,
            "chunk_count": self.get_chunk_count(),
            "word_count": self.get_word_count(),
            "tts_status": self.tts_status,  # ← Changed from self.tts_status.value to self.tts_status
            "tts_progress": self.tts_progress,
            "voice_options": self.get_voice_options() if self.is_tts_track else []
        })
        
        return base_dict

    @classmethod
    def from_dict(cls, data: dict) -> "Track":
        """Create track instance from dictionary"""
        return cls(
            id=data.get("id"),
            title=data["title"],
            file_path=data["file_path"],
            album_id=data["album_id"],
            created_by_id=data["created_by_id"],
            duration=data.get("duration"),
            codec=data.get("codec"),
            bit_rate=data.get("bit_rate"),
            sample_rate=data.get("sample_rate"),
            channels=data.get("channels"),
            format=data.get("format"),
            audio_metadata=data.get("audio_metadata"),
            order=data.get("order"),
            tier_requirements=data.get("tier_requirements"),
            access_count=data.get("access_count", 0)
        )

    def update_audio_metadata(self, metadata: dict):
        """Update audio metadata fields"""
        self.duration = metadata.get('duration', self.duration)
        self.codec = metadata.get('codec', self.codec)
        self.bit_rate = metadata.get('bit_rate', self.bit_rate)
        self.sample_rate = metadata.get('sample_rate', self.sample_rate)
        self.channels = metadata.get('channels', self.channels)
        self.format = metadata.get('format', self.format)
        
        # Update extended metadata
        if self.audio_metadata is None:
            self.audio_metadata = {}
        self.audio_metadata.update(metadata.get('extended_metadata', {}))

    @validates('duration', 'bit_rate', 'sample_rate', 'channels')
    def validate_positive_numbers(self, key, value):
        """Validate positive numeric values"""
        if value is not None and value < 0:
            raise ValueError(f"{key} must be positive")
        return value

    @property
    def total_plays(self) -> int:
        """Get total play count across all users"""
        return sum(play.play_count for play in self.plays)
        
    @property
    def average_completion_rate(self) -> float:
        """Get average completion rate across all plays"""
        if not self.plays:
            return 0.0
        return sum(play.completion_rate for play in self.plays) / len(self.plays)
    
    def get_popular_tracks(cls, db: Session, creator_id: int, limit: int = 10) -> List["Track"]:
        """Get most popular tracks by play count for a creator"""
        return (db.query(Track)
                .join(Album)
                .join(TrackPlays)
                .filter(Album.created_by_id == creator_id)
                .group_by(Track.id)
                .order_by(
                    func.sum(TrackPlays.play_count).desc(),
                    func.max(TrackPlays.last_played).desc()
                )
                .limit(limit)
                .all())

    @classmethod
    def find_tracks_for_cleanup(cls, db: Session, min_storage: int) -> List[str]:
        """
        Find tracks to clean up when storage space is needed.
        Returns track IDs ordered by:
        1. Tracks with 0 plays
        2. Tracks with lowest play count
        3. Oldest tracks (by creation date) within same play count
        """
        try:
            cleanup_query = (
                db.query(
                    cls.id,
                    cls.created_at,
                    func.coalesce(func.sum(TrackPlays.play_count), 0).label('total_plays'),
                    func.max(TrackPlays.last_played).label('last_played')
                )
                .outerjoin(TrackPlays)
                .group_by(cls.id, cls.created_at)
                .order_by(
                    func.coalesce(func.sum(TrackPlays.play_count), 0),  # Least played first
                    cls.created_at,  # Then oldest
                    func.coalesce(func.max(TrackPlays.last_played), cls.created_at)  # Break ties with last play
                )
            )

            tracks_to_cleanup = cleanup_query.all()
            logger.info(f"Found {len(tracks_to_cleanup)} tracks for potential cleanup")
            
            # Return track IDs in cleanup order
            return [str(track.id) for track in tracks_to_cleanup]

        except Exception as e:
            logger.error(f"Error finding tracks for cleanup: {str(e)}")
            return []


class Comment(Base):
    """Content comment system with threading support"""
    
    __tablename__ = "comments"
    
    # Primary key and foreign keys
    id = Column(Integer, primary_key=True, server_default=func.nextval('comments_id_seq'))
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    track_id = Column(String, ForeignKey("tracks.id", ondelete="CASCADE"), nullable=True)
    album_id = Column(UUID(as_uuid=True), ForeignKey("albums.id", ondelete="CASCADE"), nullable=True)
    parent_id = Column(Integer, ForeignKey("comments.id", ondelete="CASCADE"), nullable=True)
    edited_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    
    # Content fields
    content = Column(Text, nullable=False)
    timestamp = Column(Integer, nullable=True)
    
    # Status flags with defaults
    is_edited = Column(Boolean, server_default='false', nullable=True)
    is_hidden = Column(Boolean, server_default='false', nullable=True)
    edit_count = Column(Integer, server_default='0', nullable=True)
    moderation_status = Column(String, server_default='approved', nullable=True)
    
    # Additional fields
    moderation_reason = Column(Text, nullable=True)
    last_edited_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)
    
    # Relationships
    user = relationship("User", foreign_keys=[user_id], back_populates="comments")
    editor = relationship("User", foreign_keys=[edited_by_id])
    track = relationship("Track", back_populates="comments")
    album = relationship("Album", back_populates="comments")
    replies = relationship(
        "Comment",
        backref=backref("parent", remote_side=[id]),
        cascade="all, delete-orphan",
        single_parent=True
    )
    likes = relationship("CommentLike", back_populates="comment", cascade="all, delete-orphan")
    reports = relationship("CommentReport", back_populates="comment", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint('track_id IS NOT NULL OR album_id IS NOT NULL', 
                       name='check_comment_target'),
    )

    def edit(self, new_content: str, editor_id: int):
        """Edit comment content with audit"""
        self.content = new_content
        self.is_edited = True
        self.edit_count += 1
        self.last_edited_at = datetime.now(timezone.utc)
        self.edited_by_id = editor_id

    @property
    def like_count(self) -> int:
        """Get total number of likes"""
        return len(self.likes)

    @property
    def report_count(self) -> int:
        """Get total number of reports"""
        return len(self.reports)

    def edit(self, new_content: str, editor_id: int):
        """Edit comment content with audit"""
        self.content = new_content
        self.is_edited = True
        self.edit_count += 1
        self.last_edited_at = datetime.now(timezone.utc)
        self.edited_by_id = editor_id

class CommentLike(Base):
    """Track likes on comments"""
    
    __tablename__ = "comment_likes"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    comment_id = Column(Integer, ForeignKey("comments.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    user = relationship("User", back_populates="comment_likes")
    comment = relationship("Comment", back_populates="likes")

    __table_args__ = (
        UniqueConstraint('user_id', 'comment_id', name='uq_user_comment_like'),
    )

class CommentReport(Base):
    """System for reporting inappropriate comments"""
    
    __tablename__ = "comment_reports"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    comment_id = Column(Integer, ForeignKey("comments.id", ondelete="CASCADE"), nullable=False)
    reason = Column(String, nullable=False)
    details = Column(Text)
    status = Column(
        Enum(SegmentStatus, name='segment_status', create_type=False),
        nullable=False,
        default=SegmentStatus.PENDING,
        server_default=SegmentStatus.PENDING.value
    )

  # pending, reviewed, resolved
    resolution_notes = Column(Text)
    resolved_at = Column(DateTime(timezone=True))
    resolved_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True))
    
    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    resolver = relationship("User", foreign_keys=[resolved_by_id])
    comment = relationship("Comment", back_populates="reports")

    __table_args__ = (
        UniqueConstraint('user_id', 'comment_id', name='uq_user_comment_report'),
    )

class Campaign(Base):
    __tablename__ = "campaigns"

    # Changed from UUID to String primary key
    id = Column(String, primary_key=True)  # This stores Patreon's campaign ID directly
    creator_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    access_token = Column(String, nullable=True)
    refresh_token = Column(String, nullable=True)
    webhook_secret = Column(String, nullable=True)
    client_id = Column(String, nullable=True)
    client_secret = Column(String, nullable=True)
    is_primary = Column(Boolean, default=True)
    is_active = Column(Boolean, default=True)
    last_synced = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Updated relationships
    creator = relationship(
        "User", 
        back_populates="campaigns",
        foreign_keys=[creator_id]
    )
    tiers = relationship(
        "CampaignTier", 
        back_populates="campaign", 
        cascade="all, delete-orphan"
    )
    patrons = relationship(
        "User",
        foreign_keys="User.campaign_id",
        back_populates="patron_of"
    )

    __table_args__ = (
        UniqueConstraint('creator_id', 'id', name='uq_creator_campaign'),
    )

    @classmethod
    def create_from_env(cls, db: Session, creator_id: int) -> Optional["Campaign"]:
        """Create campaign from environment variables"""
        try:
            campaign = cls(
                creator_id=creator_id,
                id=os.getenv("PATREON_CAMPAIGN_ID"),  # 
                name=os.getenv("PATREON_CAMPAIGN_NAME", "Default Campaign"),
                access_token=os.getenv("PATREON_ACCESS_TOKEN"),
                refresh_token=os.getenv("PATREON_REFRESH_TOKEN"),
                webhook_secret=os.getenv("PATREON_WEBHOOK_SECRET"),
                client_id=os.getenv("PATREON_CLIENT_ID"),
                client_secret=os.getenv("PATREON_CLIENT_SECRET"),
                is_primary=True,
                is_active=True
            )
            
            if not campaign.id: 
                return None
                
            db.add(campaign)
            db.commit()
            return campaign
            
        except Exception as e:
            db.rollback()
            logger.error(f"Error creating campaign from env: {str(e)}")
            return None

    def to_dict(self) -> dict:
        """Convert campaign to dictionary"""
        return {
            "id": str(self.id),
            "creator_id": self.creator_id,
            "patreon_campaign_id": self.patreon_campaign_id,
            "name": self.name,
            "is_primary": self.is_primary,
            "is_active": self.is_active,
            "last_synced": self.last_synced.isoformat() if self.last_synced else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }

    def update_credentials(self, credentials: dict):
        """Update campaign credentials"""
        self.access_token = credentials.get('access_token', self.access_token)
        self.refresh_token = credentials.get('refresh_token', self.refresh_token)
        self.webhook_secret = credentials.get('webhook_secret', self.webhook_secret)
        self.client_id = credentials.get('client_id', self.client_id)
        self.client_secret = credentials.get('client_secret', self.client_secret)
        self.last_synced = datetime.now(timezone.utc)

class CampaignTier(Base):
    __tablename__ = "campaign_tiers"
    
    id = Column(Integer, primary_key=True, index=True)
    patreon_tier_id = Column(String, nullable=True, unique=True)
    campaign_id = Column(String, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    creator_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(String, nullable=False)
    description = Column(String, nullable=True)
    amount_cents = Column(Integer, nullable=False)
    patron_count = Column(Integer, nullable=False, default=0)
    platform_type = Column(Enum(PlatformType), nullable=True, default=PlatformType.PATREON)
    benefits = Column(JSONB, nullable=False, default=lambda: {})
    track_downloads_allowed = Column(Integer, nullable=False, default=0)
    album_downloads_allowed = Column(Integer, nullable=False, default=0)
    chapters_allowed_per_book_request = Column(Integer, default=0)
    book_requests_allowed = Column(Integer, nullable=False, default=0)
    custom_perks = Column(JSONB, nullable=False, default=lambda: {})
    is_active = Column(Boolean, nullable=False, default=True)
    position = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=True)
    max_sessions = Column(Integer, nullable=False, default=1)
    session_config = Column(JSONB, nullable=False, default=lambda: {"reset_on_logout": False, "session_duration": 86400})
    voice_access = Column(JSONB, nullable=False, default=lambda: [])
    read_along_access = Column(Boolean, default=False, nullable=False)
    uuid = Column(String, unique=True, nullable=False, default=generate_uuid)
    # Relationships
    campaign = relationship(
        "Campaign", 
        back_populates="tiers",
        foreign_keys=[campaign_id]
    )
    creator = relationship(
        "User", 
        back_populates="campaign_tiers",
        foreign_keys=[creator_id],  # Explicit foreign key
        primaryjoin="CampaignTier.creator_id == User.id"
    )
    patrons = relationship("User", secondary="user_tiers", back_populates="subscribed_tiers")
    active_sessions = relationship("UserSession", back_populates="tier")
    
    __table_args__ = (
        UniqueConstraint('creator_id', 'title', name='uq_creator_tier_title'),
        CheckConstraint('max_sessions BETWEEN 1 AND 5', name='check_max_sessions'),
        Index('ix_campaign_tiers_creator', 'creator_id', 'is_active')
    )

    def get_active_session_count(self, db: Session) -> int:
        """Get count of active sessions for this tier"""
        return db.query(UserSession).filter(
            and_(
                UserSession.tier_id == self.id,
                UserSession.is_active == True,
                UserSession.expires_at > datetime.now(timezone.utc)
            )
        ).count()

    def can_create_new_session(self, db: Session) -> bool:
        """Check if new session can be created for this tier"""
        if not self.is_active:
            return False
        return self.get_active_session_count(db) < self.max_sessions
    def check_voice_access(self, user: User, requested_voice: str, db: Session) -> bool:
        """Check if user's tier allows this voice"""
        # Default voice always allowed
        if requested_voice == track.default_voice:
            return True

        # Creators have all voices
        if user.is_creator:
            return True

        tier_data = user.patreon_tier_data or {}
        tier_title = tier_data.get('title')

        if not tier_title:
            return False

        creator_id = user.created_by if user.created_by else user.id

        campaign_tier = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == creator_id,
                CampaignTier.title == tier_title,
                CampaignTier.is_active == True
            )
        ).first()

        if not campaign_tier:
            return False

        allowed_voices = campaign_tier.voice_access or ["en-US-AvaNeural"]
        return requested_voice in allowed_voices


    def to_dict(self) -> dict:
        """Convert campaign tier to dictionary representation"""
        return {
            "id": self.id,
            "uuid": self.uuid,
            "patreon_tier_id": self.patreon_tier_id,
            "campaign_id": self.campaign_id,
            "creator_id": self.creator_id,
            "title": self.title,
            "description": self.description,
            "amount_cents": self.amount_cents,
            "patron_count": self.patron_count,
            "benefits": self.benefits,
            "track_downloads_allowed": self.track_downloads_allowed,
            "album_downloads_allowed": self.album_downloads_allowed,
            "book_requests_allowed": self.book_requests_allowed,
            "custom_perks": self.custom_perks,
            "is_active": self.is_active,
            "position": self.position,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "max_sessions": self.max_sessions,
            "session_config": self.session_config
        }

    @property
    def monthly_revenue(self) -> float:
        """Calculate monthly revenue from this tier"""
        return (self.amount_cents * self.patron_count) / 100.0

    @validates('amount_cents')
    def validate_amount(self, key, amount):
        """Ensure amount is positive"""
        if amount < 0:
            raise ValueError("Amount cannot be negative")
        return amount

    @validates('max_sessions')
    def validate_max_sessions(self, key, value):
        """Validate session limit is within allowed range"""
        if not isinstance(value, int) or value < 1 or value > 5:
            raise ValueError("Max sessions must be between 1 and 5")
        return value

# Update User model relationships
User.comments = relationship("Comment", foreign_keys=[Comment.user_id], back_populates="user")
User.comment_likes = relationship("CommentLike", back_populates="user")
User.campaign_tiers = relationship("CampaignTier", back_populates="creator")

from sqlalchemy import Float  
class PlaybackProgress(Base):
    __tablename__ = 'playback_progress'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    track_id = Column(String, ForeignKey('tracks.id'), nullable=False)
    position = Column(Float, default=0)
    duration = Column(Float, default=0)
    completed = Column(Boolean, default=False)
    play_count = Column(Integer, default=0)

    # Threshold tracking flags (prevent double counting)
    counted_as_listen = Column(Boolean, default=False)  # Hit 60% threshold
    counted_as_completion = Column(Boolean, default=False)  # Hit 90% threshold

    # Word-level position tracking (voice-independent)
    word_position = Column(Integer, nullable=True)  # Current word index
    last_voice_id = Column(String, nullable=True)   # Voice when position was saved

    device_info = Column(JSONB, default=lambda: {})
    last_played = Column(DateTime(timezone=True), server_default=func.now())
    completion_rate = Column(Float, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    user = relationship("User", back_populates="playback_history")
    track = relationship("Track", back_populates="playback_history")

    __table_args__ = (
        UniqueConstraint('user_id', 'track_id', name='uq_user_track_progress'),
    )

    def update_progress(self, current_position: int, total_duration: int):
        """Update playback progress and calculate completion rate"""
        self.position = current_position
        self.duration = total_duration
        if total_duration > 0:
            self.completion_rate = float((current_position / total_duration) * 100)
        else:
            self.completion_rate = 0.0
            
        self.completed = self.completion_rate >= 90  # Consider completed if 90% played
        self.last_played = datetime.now(timezone.utc)
        self.updated_at = datetime.now(timezone.utc)
        
        if self.completed and self.completion_rate >= 90:
            self.play_count += 1

    @property
    def is_in_progress(self) -> bool:
        """Check if track is partially played but not completed"""
        return self.position > 0 and not self.completed

class UserSession(Base):
    """Track user sessions with direct tier relationship"""
    
    __tablename__ = "user_sessions"
    
    # Primary key and identification columns
    id = Column(Integer, primary_key=True, server_default=text("nextval('user_sessions_id_seq'::regclass)"))
    uuid = Column(String, unique=True, nullable=False, default=lambda: str(uuid4()))
    session_id = Column(String, unique=True, nullable=False, default=lambda: str(uuid4()))
    
    # Relationship keys
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    tier_id = Column(Integer, ForeignKey("campaign_tiers.id", ondelete="SET NULL"), nullable=True)
    
    # Device and client information
    device_id = Column(String(64), nullable=True)
    device_type = Column(String(32), nullable=True)
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    
    # Session state and timing - Updated with explicit server_default
    is_active = Column(Boolean, nullable=False, server_default=expression.true())
    is_extended = Column(Boolean, nullable=False, server_default=expression.false())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    last_active = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())
    ended_at = Column(DateTime(timezone=True), nullable=True)
    
    # Session data with explicit JSONB default
    session_data = Column(JSONB, nullable=True, server_default=text("'{}'::jsonb"))
    
    # Relationships
    user = relationship("User", back_populates="sessions")
    tier = relationship("CampaignTier", back_populates="active_sessions")
    
    __table_args__ = (
        Index('ix_user_sessions_user_id', 'user_id'),
        Index('ix_user_sessions_expires_at', 'expires_at'),
        Index('ix_user_sessions_tier_active', 'tier_id', 'is_active'),
        Index('idx_user_sessions_device', 'device_id', 'device_type'),
        Index('idx_user_sessions_active_user', 'user_id', postgresql_where=text('is_active = true')),
        {'extend_existing': True}
    )

    def is_valid(self) -> bool:
        """Check if session is valid and not expired"""
        return (
            self.is_active and 
            self.expires_at > datetime.now(timezone.utc)
        )

    def extend_session(self, hours: int = 24):
        """Extend session expiration"""
        self.expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)
        self.last_active = datetime.now(timezone.utc)

    @property
    def is_expired(self) -> bool:
        """Check if session is expired"""
        return datetime.now(timezone.utc) > self.expires_at

    def record_access(self, ip_address: str = None, user_agent: str = None):
        """Record session access with client info"""
        self.last_active = datetime.now(timezone.utc)
        if ip_address:
            self.ip_address = ip_address
        if user_agent:
            self.user_agent = user_agent

    def end_session(self):
        """End the session"""
        self.is_active = False
        self.ended_at = datetime.now(timezone.utc)

class UserAlbumManagement(Base):
    """User-specific album preferences and management"""
    
    __tablename__ = "user_album_management"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    album_id = Column(String, ForeignKey("albums.id", ondelete="CASCADE"), nullable=False)  # Changed to String to match Album.id
    is_favorite = Column(Boolean, default=False)
    custom_order = Column(Integer)
    notes = Column(Text)
    last_viewed = Column(DateTime(timezone=True))
    view_count = Column(Integer, default=0)
    rating = Column(Integer)  # 1-5 star rating
    tags = Column(JSON, default=list)  # Personal tags/categories
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True))
    
    # Relationships
    user = relationship("User", back_populates="album_management")
    album = relationship("Album", back_populates="user_management")

    __table_args__ = (
        UniqueConstraint('user_id', 'album_id', name='uq_user_album_management'),
        CheckConstraint('rating IS NULL OR (rating >= 1 AND rating <= 5)',
                       name='check_rating_range'),
    )

    def increment_view(self):
        """Increment view count and update last viewed timestamp"""
        self.view_count += 1
        self.last_viewed = datetime.now(timezone.utc)


class UserTrackVoicePreference(Base):
    """Track-specific voice preferences (non-favorited voices)

    Stores per-track voice selections. Works alongside User.preferred_voice:
    - User.preferred_voice = hearted/favorite voice (applies to all tracks)
    - This table = track-specific selections (overrides favorite only for that track)

    Priority: favorite voice (if cached) > track-specific > track default
    """

    __tablename__ = "user_track_voice_preferences"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    track_id = Column(String, ForeignKey('tracks.id', ondelete='CASCADE'), nullable=False, index=True)
    voice_id = Column(String, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    user = relationship("User", backref="track_voice_preferences")
    track = relationship("Track", backref="user_voice_preferences")

    __table_args__ = (
        UniqueConstraint('user_id', 'track_id', name='uq_user_track_voice'),
        Index('idx_user_track_pref_composite', 'user_id', 'track_id'),
    )

    def __repr__(self):
        return f"<UserTrackVoicePreference(user_id={self.user_id}, track_id={self.track_id}, voice_id={self.voice_id})>"


class UserTier(Base):
    """Association table for users and tiers with additional metadata"""
    
    __tablename__ = "user_tiers"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    tier_id = Column(Integer, ForeignKey("campaign_tiers.id", ondelete="CASCADE"), nullable=False)
    joined_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True))
    is_active = Column(Boolean, default=True)
    payment_status = Column(String)
    last_payment_date = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True))
    
    __table_args__ = (
        UniqueConstraint('user_id', 'tier_id', name='uq_user_tier'),
    )

# Update Track model relationships
Track.playback_history = relationship("PlaybackProgress", back_populates="track")

# Update User model with new relationships
User.playback_history = relationship("PlaybackProgress", back_populates="user", cascade="all, delete-orphan")
User.sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")
User.album_management = relationship("UserAlbumManagement", back_populates="user", cascade="all, delete-orphan")

class Notification(Base):
    """User notifications system"""
    
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    sender_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    type = Column(Enum(NotificationType), nullable=False)
    content = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False)
    read_at = Column(DateTime(timezone=True))
    notification_data = Column(JSONB, nullable=True, default=lambda: {})  # Changed from 'metadata' to 'notification_data'
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    user = relationship(
        "User", 
        foreign_keys=[user_id], 
        back_populates="notifications"
    )
    sender = relationship("User", foreign_keys=[sender_id])

    def mark_as_read(self):
        """Mark notification as read"""
        if not self.is_read:
            self.is_read = True
            self.read_at = datetime.now(timezone.utc)

    @property
    def time_since_created(self) -> str:
        """Get human-readable time since notification was created"""
        now = datetime.now(timezone.utc)
        diff = now - self.created_at

        if diff.days > 7:
            return self.created_at.strftime("%B %d, %Y")
        elif diff.days > 0:
            return f"{diff.days} days ago"
        elif diff.seconds > 3600:
            hours = diff.seconds // 3600
            return f"{hours} hours ago"
        elif diff.seconds > 60:
            minutes = diff.seconds // 60
            return f"{minutes} minutes ago"
        else:
            return "Just now"
            
    def to_dict(self) -> dict:
        """Convert notification to dictionary representation"""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "sender_id": self.sender_id,
            "type": self.type.value,
            "content": self.content,
            "is_read": self.is_read,
            "read_at": self.read_at.isoformat() if self.read_at else None,
            "metadata": self.notification_data,  # Returns as 'metadata' for API compatibility
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "time_since": self.time_since_created
        }

class SegmentMetadata(Base):
    """Tracks individual HLS segment information for audio files"""
    __tablename__ = "segment_metadata"

    # Primary Key and Identification
    id = Column(Integer, primary_key=True, index=True)
    segment_index = Column(Integer, nullable=False)
    stream_id = Column(String(64), nullable=False, index=True)
    track_id = Column(String, ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False)
    
    # Timing Information
    start_time = Column(Float, nullable=False)
    duration = Column(Float, nullable=False)
    
    # Technical Metadata
    codec = Column(String(50))
    bitrate = Column(Integer)
    codec_options = Column(
        JSONB,
        nullable=True,
        default={}
    )
    
    # File Information
    source_file = Column(Text, nullable=False)
    segment_path = Column(Text)
    
    # Status and Tracking
    status = Column(
        String,
        nullable=False,
        default='pending',
        server_default='pending'
    )

    error_message = Column(Text)
    access_count = Column(Integer, default=0)
    last_accessed = Column(DateTime(timezone=True))
    
    # Processing Information
    processing_time = Column(Float)
    file_size = Column(Integer)
    
    # Performance Metrics
    avg_processing_time = Column(Float)
    cache_hits = Column(Integer, default=0)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), onupdate=datetime.now(timezone.utc))
    temp_file_path = Column(String, nullable=True)
    upload_time = Column(DateTime(timezone=True))
    original_filename = Column(String, nullable=True)

    # Relationships
    track = relationship("Track", back_populates="segments")
    
    __table_args__ = (
        CheckConstraint('duration > 0', name='positive_duration'),
        CheckConstraint('bitrate > 0', name='positive_bitrate'),
        UniqueConstraint('stream_id', 'segment_index', name='unique_segment_index'),
        Index('ix_segment_metadata_track_status', 'track_id', 'status'),
        Index('ix_segment_metadata_access', 'last_accessed', 'access_count'),
    )

    def __repr__(self):
        return f"<SegmentMetadata(stream_id={self.stream_id}, index={self.segment_index})>"

    def to_dict(self) -> dict:
        """Convert segment metadata to dictionary"""
        return {
            "id": self.id,
            "segment_index": self.segment_index,
            "stream_id": self.stream_id,
            "track_id": self.track_id,
            "start_time": self.start_time,
            "duration": self.duration,
            "codec": self.codec,
            "bitrate": self.bitrate,
            "codec_options": self.codec_options,
            "source_file": self.source_file,
            "segment_path": self.segment_path,
            "status": self.status.value,
            "error_message": self.error_message,
            "access_count": self.access_count,
            "processing_time": self.processing_time,
            "file_size": self.file_size,
            "avg_processing_time": self.avg_processing_time,
            "cache_hits": self.cache_hits,
            "last_accessed": self.last_accessed.isoformat() if self.last_accessed else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }

    def update_access(self, processing_time: float = None, from_cache: bool = False):
        """Update segment access metrics"""
        self.access_count += 1
        self.last_accessed = datetime.now(timezone.utc)
        
        if processing_time is not None:
            if self.avg_processing_time is None:
                self.avg_processing_time = processing_time
            else:
                # Rolling average
                self.avg_processing_time = (
                    (self.avg_processing_time * (self.access_count - 1) + processing_time) 
                    / self.access_count
                )
        
        if from_cache:
            self.cache_hits += 1

    def mark_ready(self, processing_time: float = None, file_size: int = None):
        """Mark segment as ready with optional metrics"""
        self.status = SegmentStatus.READY
        self.error_message = None
        self.processing_time = processing_time
        self.file_size = file_size
        self.updated_at = datetime.now(timezone.utc)

    def set_error(self, error_message: str):
        """Set error status with message"""
        self.status = SegmentStatus.ERROR
        self.error_message = error_message
        self.updated_at = datetime.now(timezone.utc)

    @validates('duration', 'bitrate', 'file_size')
    def validate_positive_numbers(self, key, value):
        """Validate positive numeric values"""
        if value is not None and value < 0:
            raise ValueError(f"{key} must be positive")
        return value

Track.segments = relationship(
    "SegmentMetadata", 
    back_populates="track",
    cascade="all, delete-orphan",
    order_by="SegmentMetadata.segment_index"
)

@event.listens_for(SegmentMetadata, 'before_update')
def segment_before_update(mapper, connection, target):
    target.updated_at = datetime.now(timezone.utc)

class TrackPlays(Base):
    """Track play counts and metrics"""
    
    __tablename__ = "track_plays"

    id = Column(Integer, primary_key=True)
    track_id = Column(String, ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    # Play metrics
    play_count = Column(Integer, default=0)  # Listens (60% threshold)
    completions_count = Column(Integer, default=0)  # Completions (90% threshold)
    completion_rate = Column(Float, default=0.0)
    last_played = Column(DateTime(timezone=True))
    total_play_time = Column(Float, default=0.0)  # Total seconds played
    
    # Device/client info
    device_info = Column(JSONB, default=dict)
    ip_address = Column(String)
    user_agent = Column(String)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    track = relationship("Track", back_populates="plays")
    user = relationship("User", back_populates="track_plays")

    __table_args__ = (
        UniqueConstraint('track_id', 'user_id', name='uq_track_user_plays'),
        Index('ix_track_plays_metrics', 'track_id', 'play_count', 'last_played')
    )

    def increment_play(self, completion_rate: float = None, play_time: float = None,
                      device_info: dict = None, ip_address: str = None,
                      user_agent: str = None):
        """
        Update play metrics (NOT play_count - that's handled by threshold logic)
        This updates completion_rate average, play_time, and metadata only
        """
        self.last_played = datetime.now(timezone.utc)

        # Update average completion rate (use total plays for averaging)
        total_plays = (self.play_count or 0) + (self.completions_count or 0)
        if completion_rate is not None and total_plays > 0:
            if self.completion_rate is None:
                self.completion_rate = completion_rate
            else:
                self.completion_rate = (
                    (self.completion_rate * (total_plays - 1) + completion_rate)
                    / total_plays
                )

        if play_time:
            if self.total_play_time is None:
                self.total_play_time = 0
            self.total_play_time += play_time

        if device_info:
            self.device_info = device_info
        if ip_address:
            self.ip_address = ip_address
        if user_agent:
            self.user_agent = user_agent

class ScheduledTask(Base):
    """Model for scheduling automated tasks"""
    __tablename__ = "scheduled_tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(Integer, ForeignKey('users.id', ondelete="CASCADE"), nullable=False)
    task_type = Column(String, nullable=False)  # Using String instead of Enum for flexibility
    scheduled_for = Column(DateTime(timezone=True), nullable=False)
    status = Column(String, nullable=False, default="pending")  # Using String instead of Enum
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), onupdate=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    user = relationship("User", back_populates="scheduled_tasks")

    def __repr__(self):
        return f"<ScheduledTask(type={self.task_type}, status={self.status}, scheduled_for={self.scheduled_for})>"

User.scheduled_tasks = relationship("ScheduledTask", back_populates="user", cascade="all, delete-orphan")

@event.listens_for(ScheduledTask, 'before_update')
def scheduled_task_before_update(mapper, connection, target):
    target.updated_at = datetime.now(timezone.utc)

class AuditLog(Base):
    """Audit logging for tracking changes and actions"""
    
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action_type = Column(Enum(AuditLogType, native_enum=True, values_callable=lambda x: [e.value for e in x]), nullable=False)
    table_name = Column(String, nullable=False)
    record_id = Column(String)  # Changed from Integer to String to support UUIDs
    old_values = Column(JSON, server_default=text("'{}'::jsonb"))
    new_values = Column(JSON, server_default=text("'{}'::jsonb"))
    ip_address = Column(String)
    user_agent = Column(String)
    description = Column(Text)
    created_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True))

    # Relationships
    user = relationship("User", back_populates="audit_logs")

    @classmethod
    def log_change(cls, db: Session, user_id: int, action_type: AuditLogType, 
                  table_name: str, record_id: int, old_values: dict = None, 
                  new_values: dict = None, description: str = None,
                  ip_address: str = None, user_agent: str = None):
        """Create a new audit log entry"""
        log = cls(
            user_id=user_id,
            action_type=action_type,
            table_name=table_name,
            record_id=record_id,
            old_values=old_values,
            new_values=new_values,
            description=description,
            ip_address=ip_address,
            user_agent=user_agent
        )
        db.add(log)
        try:
            db.flush()
            return log
        except Exception as e:
            db.rollback()
            raise e

    @property
    def changes_summary(self) -> Dict[str, Dict[str, Any]]:
        """Get a summary of changes made"""
        changes = {}
        if self.old_values and self.new_values:
            for key in set(self.old_values.keys()) | set(self.new_values.keys()):
                old_val = self.old_values.get(key)
                new_val = self.new_values.get(key)
                if old_val != new_val:
                    changes[key] = {
                        "old": old_val,
                        "new": new_val
                    }
        return changes

    User.notifications = relationship(
        "Notification", 
        back_populates="user", 
        cascade="all, delete-orphan",
        foreign_keys=[Notification.user_id]  # Explicitly specify which foreign key to use
    )

User.audit_logs = relationship("AuditLog", back_populates="user")

@event.listens_for(User, 'before_update')
def user_before_update(mapper, connection, target):
    target.updated_at = datetime.now(timezone.utc)

@event.listens_for(Album, 'before_update')
def album_before_update(mapper, connection, target):
    target.updated_at = datetime.now(timezone.utc)

@event.listens_for(Track, 'before_update')
def track_before_update(mapper, connection, target):
    target.updated_at = datetime.now(timezone.utc)

# Create indexes for better query performance
for table in Base.metadata.tables.values():
    for column in table.columns:
        if column.name in ['user_id', 'album_id', 'track_id', 'creator_id']:
            DDL(
                f'CREATE INDEX IF NOT EXISTS ix_{table.name}_{column.name} ON {table.name} ({column.name})'
            ).execute_if(dialect='postgresql')

# Ensure all JSON columns have defaults
for table in Base.metadata.tables.values():
    for column in table.columns:
        if isinstance(column.type, JSON) and column.default is None:
            column.default = expression.text('\'{}\'::jsonb')

class DiscordSettings(Base):
    """Store Discord integration settings in database"""
    __tablename__ = "discord_settings"

    id = Column(Integer, primary_key=True, index=True)
    creator_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    # Discord configuration
    webhook_url = Column(String, nullable=True)
    webhook_id = Column(String, nullable=True)
    webhook_token = Column(String, nullable=True)
    bot_token = Column(String, nullable=True)
    base_url = Column(String, nullable=True)  # Added base_url field
    
    # Integration status
    is_active = Column(Boolean, default=True, nullable=False)
    last_synced = Column(DateTime(timezone=True), nullable=True)
    
    # Tracking data - store message IDs for cleanup
    sync_message_ids = Column(JSON, nullable=True, default=list)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    creator = relationship("User", back_populates="discord_settings")

    def __repr__(self):
        return f"<DiscordSettings(creator_id={self.creator_id})>"
    
    def parse_webhook_url(self):
        """Extract webhook ID and token from URL"""
        if not self.webhook_url:
            return False
            
        try:
            parts = self.webhook_url.strip('/').split('/')
            if len(parts) >= 2:
                self.webhook_id = parts[-2]
                self.webhook_token = parts[-1]
                return True
        except Exception:
            return False
            
        return False

# Update User model to include the relationship
User.discord_settings = relationship(
    "DiscordSettings", 
    back_populates="creator", 
    cascade="all, delete-orphan",
    uselist=False  # One-to-one relationship
)

#########################################################################
# NEW CODE: DownloadType, UserDownload model
#########################################################################

# 1) DownloadType enum
class DownloadType(enum.Enum):
    ALBUM = "album"
    TRACK = "track"

# 2) UserDownload model
class UserDownload(Base):
    """
    Track user downloads for 24-hour re-download capability.
    """
    __tablename__ = "user_downloads"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    download_type = Column(Enum(DownloadType), nullable=False)
    album_id = Column(UUID(as_uuid=True), ForeignKey("albums.id", ondelete="CASCADE"), nullable=True)
    track_id = Column(String, ForeignKey("tracks.id", ondelete="CASCADE"), nullable=True)

    download_path = Column(String, nullable=False)        # Path to the stored file
    original_filename = Column(String, nullable=False)    # Original filename for display

    # ✅ NEW: Add voice_id column for TTS track support
    voice_id = Column(String(100), nullable=True)         # Voice ID for TTS tracks (e.g., 'en-US-AvaNeural')

    is_available = Column(Boolean, default=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)

    downloaded_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    user = relationship("User", back_populates="downloads")
    album = relationship("Album", back_populates="user_downloads")
    track = relationship("Track", back_populates="user_downloads")

    def is_expired(self) -> bool:
        """Check if download has expired."""
        return datetime.now(timezone.utc) >= self.expires_at

    def get_time_remaining(self) -> timedelta:
        """Get time remaining before expiry."""
        now = datetime.now(timezone.utc)
        if now >= self.expires_at:
            return timedelta(0)
        return self.expires_at - now

    def get_entity_id(self) -> str:
        """Get the entity ID (album or track) for this download."""
        return str(self.album_id) if self.download_type == DownloadType.ALBUM else self.track_id

    def to_dict(self) -> dict:
        """Convert to dictionary for API response."""
        time_remaining = self.get_time_remaining()
        hours, remainder = divmod(time_remaining.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        return {
            "id": self.id,
            "type": self.download_type.value,
            "entity_id": self.get_entity_id(),
            "filename": self.original_filename,
            "download_url": f"/api/my-downloads/{self.id}/file",
            "expires_at": self.expires_at.isoformat(),
            "downloaded_at": self.downloaded_at.isoformat(),
            "time_remaining": f"{hours:02d}:{minutes:02d}:{seconds:02d}",
            "is_available": self.is_available,
            "voice_id": self.voice_id,  # ✅ NEW: Include voice_id in API response
            "album": self.album.to_dict() if self.album else None,
            "track": self.track.to_dict() if self.track else None
        }

    __table_args__ = (
        # Composite index for the common query pattern used in your code
        Index('idx_user_downloads_user_track_voice', 'user_id', 'track_id', 'voice_id', 'download_type', 'is_available'),
        
        # Index for voice-specific queries
        Index('idx_user_downloads_voice_id', 'voice_id'),
        
        # Index for expiration cleanup
        Index('idx_user_downloads_expires_available', 'expires_at', 'is_available'),
        
        # Index for user's downloads listing
        Index('idx_user_downloads_user_downloaded', 'user_id', 'downloaded_at'),
    )
#########################################################################
# Ko-fi Implementation
#########################################################################

# The Ko-fi webhook data model - minimal storage of transaction data
class KofiWebhook(Base):
    """Store Ko-fi webhook data for verification"""
    __tablename__ = "kofi_webhooks"
    
    id = Column(Integer, primary_key=True, index=True)
    transaction_id = Column(String, unique=True, nullable=False)
    email = Column(String, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    amount = Column(Float, nullable=False)
    is_subscription = Column(Boolean, default=False)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    processed_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationship to User
    user = relationship("User", back_populates="kofi_webhooks")
    
    def __repr__(self):
        return f"<KofiWebhook(transaction_id={self.transaction_id}, user_id={self.user_id})>"

# Add relationship to User model
User.kofi_webhooks = relationship("KofiWebhook", back_populates="user", cascade="all, delete-orphan")

# Add Ko-fi specific methods to User model
def is_kofi(self) -> bool:
    """Check if user is a Ko-fi supporter"""
    return self.role == UserRole.KOFI

User.is_kofi = property(is_kofi)

def update_kofi_tier(self, amount: float, tier_data: dict = None, is_subscription: bool = False) -> None:
    """Update user with Ko-fi tier information
    
    Sets tier information in patreon_tier_data but marks it as Ko-fi
    to leverage existing tier functionality.
    """
    # Get amount in cents for consistency with Patreon
    amount_cents = int(amount * 100)
    
    # Calculate expiration (30 days for one-time, or based on subscription status)
    expires_at = datetime.now(timezone.utc) + timedelta(days=30)
    
    # Store tier info in patreon_tier_data to reuse existing access control
    tier_info = {
        'amount_cents': amount_cents,
        'kofi_user': True,  # Flag to identify Ko-fi data
        'expires_at': expires_at.isoformat(),
    }
    
    # Add additional tier data if provided
    if tier_data:
        tier_info.update(tier_data)
    else:
        # Default values only if no tier data provided
        tier_info.update({
            'title': 'Ko-fi Supporter',
            'description': f"Ko-fi {'Subscription' if is_subscription else 'Support'}"
        })
    
    # Add tier info to patreon_tier_data
    self.patreon_tier_data = tier_info
    
    # Set role to Ko-fi if not a higher privilege
    if self.role not in [UserRole.CREATOR, UserRole.TEAM]:
        self.role = UserRole.KOFI
    
    # Update last sync timestamp
    self.last_sync = datetime.now(timezone.utc)

User.update_kofi_tier = update_kofi_tier

def get_kofi_tier_info(self) -> Dict:
    """Get Ko-fi tier information in the same format as Patreon tier info"""
    if not self.is_kofi or not self.patreon_tier_data or not self.patreon_tier_data.get('kofi_user'):
        return {
            "name": "No Ko-fi Tier",
            "description": "Not a Ko-fi supporter",
            "level": 0,
            "track_downloads_allowed": 0,
            "album_downloads_allowed": 0,
            "book_requests_allowed": 0
        }
    
    # Check expiration
    expires_str = self.patreon_tier_data.get('expires_at')
    if expires_str:
        try:
            expires_at = datetime.fromisoformat(expires_str)
            if datetime.now(timezone.utc) > expires_at:
                return {
                    "name": "Expired Ko-fi Support",
                    "description": "Ko-fi support has expired",
                    "level": 0,
                    "track_downloads_allowed": 0,
                    "album_downloads_allowed": 0,
                    "book_requests_allowed": 0
                }
        except (ValueError, TypeError):
            pass
    
    # Return tier info
    return {
        "name": self.patreon_tier_data.get('title', 'Ko-fi Supporter'),
        "description": self.patreon_tier_data.get('description', 'Ko-fi Support'),
        "amount": f"${self.patreon_tier_data.get('amount_cents', 0)/100:.2f}",
        "level": self.patreon_tier_data.get('amount_cents', 0),
        "track_downloads_allowed": self.patreon_tier_data.get('track_downloads_allowed', 0),
        "album_downloads_allowed": self.patreon_tier_data.get('album_downloads_allowed', 0),
        "book_requests_allowed": self.patreon_tier_data.get('book_requests_allowed', 0)
    }

User.get_kofi_tier_info = get_kofi_tier_info

# Update formatted_tier to include Ko-fi
def formatted_tier_with_kofi(self) -> str:
    """Return formatted tier name, updated to include Ko-fi"""
    if self.is_creator:
        return "Creator"
    elif self.is_team:
        return "Team Member"
    elif self.is_patreon:
        tier_info = self.get_tier_info()
        return f"Patron ({tier_info['name']})"
    elif self.is_kofi:
        tier_info = self.get_kofi_tier_info()
        return f"Ko-fi ({tier_info['name']})"
    return self.role.value.capitalize()

# Replace original formatted_tier property
User.formatted_tier = property(formatted_tier_with_kofi)

# Update tier_level to check for Ko-fi data expiry
def get_tier_level_with_kofi(self) -> int:
    """Get tier level in cents for both Patreon and Ko-fi users"""
    if not self.patreon_tier_data:
        return 0
    
    # Check if Ko-fi data is expired
    if self.patreon_tier_data.get('kofi_user', False):
        # Check if expired
        expires_str = self.patreon_tier_data.get('expires_at')
        if expires_str:
            try:
                expires_at = datetime.fromisoformat(expires_str)
                if datetime.now(timezone.utc) > expires_at:
                    return 0  # Expired
            except (ValueError, TypeError):
                pass
    
    return self.patreon_tier_data.get('amount_cents', 0)

# Replace original tier_level property
User.tier_level = property(get_tier_level_with_kofi)

# Create a verification token column in Creator settings
class KofiSettings(Base):
    """Minimal settings for Ko-fi integration"""
    __tablename__ = "kofi_settings"
    
    id = Column(Integer, primary_key=True, index=True)
    creator_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    google_sheet_url = Column(String, nullable=True)  # Add this field
    verification_token = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationship
    creator = relationship("User", back_populates="kofi_settings")

# Add relationship to User model
User.kofi_settings = relationship("KofiSettings", back_populates="creator", uselist=False)

# Add webhook processing function
def process_kofi_webhook(db: Session, webhook_data: dict, verification_token: str) -> Optional[User]:
    """Process Ko-fi webhook data and update user

    Args:
        db: Database session
        webhook_data: The webhook payload from Ko-fi
        verification_token: The verification token to check

    Returns:
        The updated user or None if validation fails
    """
    # 1. Verify webhook token
    webhook_token = webhook_data.get("verification_token")
    if not webhook_token or webhook_token != verification_token:
        return None
    
    # 2. Extract data from webhook
    kofi_data = webhook_data.get("data", {})
    transaction_id = kofi_data.get("verification_token", "")
    email = kofi_data.get("email", "").lower()
    
    # Check for required data
    if not email or not transaction_id:
        return None
    
    # Check if this transaction was already processed
    existing_webhook = db.query(KofiWebhook).filter(
        KofiWebhook.transaction_id == transaction_id
    ).first()
    
    if existing_webhook:
        return existing_webhook.user
    
    # 3. Parse webhook data
    try:
        amount = float(kofi_data.get("amount", 0))
    except (ValueError, TypeError):
        amount = 0
    
    is_subscription = "subscription" in kofi_data.get("type", "").lower()
    
    # Parse timestamp
    timestamp_str = kofi_data.get("timestamp", "")
    try:
        timestamp = datetime.fromisoformat(timestamp_str)
    except (ValueError, TypeError):
        timestamp = datetime.now(timezone.utc)
    
    # 4. Find or create user
    user = db.query(User).filter(func.lower(User.email) == email).first()
    
    if not user:
        # Create new user
        user = User(
            email=email,
            username=kofi_data.get("from_name", email.split("@")[0]),
            role=UserRole.KOFI,
            is_active=True,
            created_at=datetime.now(timezone.utc)
        )
        db.add(user)
        db.flush()  # Generate user ID
    
    # 5. Create webhook record
    webhook = KofiWebhook(
        transaction_id=transaction_id,
        email=email,
        user_id=user.id,
        amount=amount,
        is_subscription=is_subscription,
        timestamp=timestamp
    )
    db.add(webhook)
    
    # 6. Extract tier data from webhook
    tier_data = {}
    if 'tier_data' in kofi_data:
        tier_data = kofi_data.get('tier_data', {})
    
    # Process any custom fields in the webhook data
    for key, value in kofi_data.items():
        if key.startswith('custom_'):
            tier_data[key] = value
    
    # Update user tier info
    user.update_kofi_tier(amount, tier_data, is_subscription)
    
    try:
        db.commit()
        return user
    except Exception as e:
        db.rollback()
        print(f"Error processing Ko-fi webhook: {e}")
        return None


class Broadcast(Base):
    __tablename__ = "broadcasts"
    id = Column(String, primary_key=True)
    created_by_id = Column(Integer, ForeignKey("users.id"))
    message = Column(Text, nullable=False)
    type = Column(String, default="info")  # info, warning, alert
    is_active = Column(Boolean, default=True, index=True)  # This adds the index
    created_at = Column(DateTime(timezone=True), default=datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), onupdate=datetime.now(timezone.utc))
    # Relationship
    created_by = relationship("User", foreign_keys=[created_by_id])


class ForumUserSettings(Base):
    """User forum settings and preferences"""
    __tablename__ = "forum_user_settings"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    
    # Display preferences
    display_alias = Column(String(50), nullable=True)  # Custom alias for forum
    use_alias = Column(Boolean, default=False)  # Whether to use alias or real username
    
    # Notification preferences
    enable_quick_reply_notifications = Column(Boolean, default=True)
    quick_reply_for_mentions = Column(Boolean, default=True)
    quick_reply_for_replies = Column(Boolean, default=True)
    quick_reply_auto_dismiss_seconds = Column(Integer, default=10)  # Auto-dismiss timer
    
    # Sound and visual preferences
    enable_notification_sound = Column(Boolean, default=True)
    notification_position = Column(String(20), default="top-right")  # top-right, top-left, bottom-right, bottom-left
    
    # 🔥 FIXED: Added missing = signs for @everyone moderation fields
    everyone_restricted = Column(Boolean, default=False)
    everyone_restriction_reason = Column(Text, nullable=True)
    everyone_restricted_until = Column(DateTime(timezone=True), nullable=True)
    everyone_custom_rate_limit = Column(Integer, nullable=True)
    everyone_violation_count = Column(Integer, default=0)
    
    # Privacy preferences
    show_online_status = Column(Boolean, default=True)
    allow_direct_mentions = Column(Boolean, default=True)
    allow_everyone_mentions = Column(Boolean, default=True)
    everyone_mention_sound = Column(Boolean, default=True)  # Special sound for @everyone
    everyone_mention_popup = Column(Boolean, default=True)  # Show popup for @everyone
    
    # Metadata
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    # Relationships
    user = relationship("User", back_populates="forum_settings")
class DownloadReservation(Base):
    """Track credit reservations to prevent race conditions"""
    __tablename__ = "download_reservations"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    download_id = Column(String(255), nullable=False, unique=True)
    download_type = Column(String(50), nullable=False)  # 'album', 'track', 'book'
    reserved_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(20), default='reserved')  # reserved, confirmed, failed, expired
    
    # Relationships
    user = relationship("User", back_populates="download_reservations")
    
    __table_args__ = (
        Index('ix_download_reservations_user_type', 'user_id', 'download_type'),
        Index('ix_download_reservations_status_expires', 'status', 'expires_at'),
    )







class TTSTextSegment(Base):
    """Individual text segments with compression - UPDATED for your schema"""
    
    __tablename__ = "tts_text_segments"
    
    id = Column(Integer, primary_key=True, index=True)
    track_id = Column(String, ForeignKey("tracks.id"), nullable=False, index=True)
    track_meta_id = Column(Integer, ForeignKey("tts_track_meta.id", ondelete="CASCADE"), nullable=False)

    segment_index = Column(Integer, nullable=False)
    start_time = Column(Float, nullable=False)
    end_time = Column(Float, nullable=False)
    duration = Column(Float, nullable=False)
    status = Column(String, default='pending')  # pending, processing, ready, error
    
    # Compressed text storage - using your existing field name
    compressed_text = Column(LargeBinary, nullable=True)  # ✅ Keep your existing field name
    compression_level = Column(Integer, default=6)
    original_size = Column(Integer, nullable=True)
    compressed_size = Column(Integer, nullable=True)
    
    # Cached properties
    _word_count = Column("word_count", Integer, nullable=True)
    _preview_text = Column("preview_text", String(200), nullable=True)
    
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    # Relationships
    voice_segments = relationship("TTSVoiceSegment", back_populates="text_segment", cascade="all, delete-orphan")
    word_timings = relationship("TTSWordTiming", back_populates="text_segment", cascade="all, delete-orphan")
    track = relationship("Track", back_populates="tts_segments")
    track_meta = relationship("TTSTrackMeta", back_populates="text_segments")
    
    __table_args__ = (
        Index('ix_tts_segment_track_index', 'track_id', 'segment_index'),
        Index('ix_tts_segment_time', 'start_time', 'end_time'),
    )
    
    def set_text_content(self, text: str):
        """Compress and store text content with proper error handling"""
        try:
            if not isinstance(text, str):
                raise ValueError("Text must be a string")
            
            if self.compression_level is None:
                self.compression_level = 6
                
            text_bytes = text.encode('utf-8')
            self.original_size = len(text_bytes)
            
            # Compress the text
            compressed = zlib.compress(text_bytes, self.compression_level)
            self.compressed_text = compressed
            self.compressed_size = len(compressed)
            
            # Set cached properties
            self._preview_text = text[:200] if len(text) > 200 else text
            self._word_count = len(text.split())
            
        except Exception as e:
            logger.error(f"Error compressing text: {str(e)}")
            raise Exception(f"Failed to compress text: {str(e)}")
    
    def get_text_content(self) -> str:
        """Get decompressed text content"""
        try:
            if not self.compressed_text:
                return ""
            
            # Decompress the text
            decompressed_bytes = zlib.decompress(self.compressed_text)
            return decompressed_bytes.decode('utf-8')
            
        except Exception as e:
            logger.error(f"Error decompressing text: {str(e)}")
            raise Exception(f"Failed to decompress text: {str(e)}")
    
    @property
    def word_count(self) -> int:
        """Get cached word count"""
        if self._word_count is not None:
            return self._word_count
        
        # Fallback: calculate from text
        try:
            text = self.get_text_content()
            count = len(text.split()) if text else 0
            self._word_count = count
            return count
        except:
            return 0
    
    @property
    def preview_text(self) -> str:
        """Get cached preview text"""
        if self._preview_text:
            return self._preview_text
        
        # Fallback: generate from text
        try:
            text = self.get_text_content()
            preview = text[:197] + "..." if len(text) > 200 else text
            self._preview_text = preview
            return preview
        except:
            return "Error loading text"
    
    @property
    def compression_ratio(self) -> float:
        """Get compression ratio"""
        if self.original_size and self.compressed_size:
            return self.compressed_size / self.original_size
        return 1.0

class TTSWordTiming(Base):
    """Packed word timing data with optional segment mapping."""
    __tablename__ = "tts_word_timings"

    id = Column(Integer, primary_key=True, index=True)
    segment_id = Column(Integer, ForeignKey("tts_text_segments.id", ondelete="CASCADE"), nullable=False)
    voice_id = Column(String(50), nullable=False)

    # Primary packed store (legacy kept empty by new packer)
    timing_data_packed = Column(LargeBinary, nullable=False, default=b"")
    # New compressed store
    compressed_timings = Column(LargeBinary, nullable=True)

    word_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    first_word_time = Column(Float, nullable=False, default=0.0, server_default=text("0"))
    last_word_time = Column(Float, nullable=False, default=0.0, server_default=text("0"))
    total_duration = Column(Float, nullable=True, default=0.0, server_default=text("0"))

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Optional file storage metadata (as in your schema)
    timings_file_path = Column(String(500), nullable=True, index=True)
    timings_file_hash = Column(String(64), nullable=True, index=True)
    timings_file_size = Column(Integer, nullable=True)

    # Relationships
    text_segment = relationship("TTSTextSegment", back_populates="word_timings")

    __table_args__ = (
        Index("idx_tts_word_timing_segment_id", "segment_id"),
        Index("idx_tts_word_timing_voice_id", "voice_id"),
        Index("ix_word_timing_segment", "segment_id", "voice_id"),
        Index("ix_word_timing_time", "segment_id", "first_word_time", "last_word_time"),
        CheckConstraint(
            "word_count >= 0 AND first_word_time >= 0 AND last_word_time >= first_word_time",
            name="check_word_timing_positive",
        ),
    )

    # ---------- pack / unpack (kept your new format, made robust) ----------

    def pack_word_timings(self, word_timings: List[Dict[str, Any]]) -> None:
        """Pack as compressed blob; leaves legacy field empty for new data."""
        if not word_timings:
            self.timing_data_packed = b""
            self.compressed_timings = b""
            self.word_count = 0
            self.first_word_time = 0.0
            self.last_word_time = 0.0
            self.total_duration = 0.0
            return

        self.word_count = len(word_timings)
        self.first_word_time = float(word_timings[0]["start_time"])
        self.last_word_time = float(word_timings[-1]["end_time"])
        self.total_duration = self.last_word_time - self.first_word_time

        self.compressed_timings = self._pack_timing_data_compressed(word_timings)
        self.timing_data_packed = b""  # keep legacy empty

    def unpack_word_timings(self) -> List[Dict[str, Any]]:
        if self.compressed_timings:
            return self._unpack_timing_data_compressed(self.compressed_timings)
        if self.timing_data_packed:
            # legacy fallback if you still have old rows
            return self._unpack_timing_data_legacy(self.timing_data_packed)
        return []

    def _pack_timing_data_compressed(self, word_timings: List[Dict[str, Any]]) -> bytes:
        """
        Entry format:
          uint16 word_len | bytes word | 6 * uint32:
            start_ms, end_ms, text_offset, segment_index_or_FFFFFFFF, segment_offset_ms, word_index
        """
        out = []
        for w in word_timings:
            word_b = w["word"].encode("utf-8")
            wl = len(word_b)
            start_ms = int(float(w["start_time"]) * 1000)
            end_ms = int(float(w["end_time"]) * 1000)
            text_offset = int(w.get("text_offset", 0))
            seg_idx = w.get("segment_index")
            seg_idx_u32 = 0xFFFFFFFF if seg_idx is None else max(0, int(seg_idx))
            seg_off_ms = int(float(w.get("segment_offset", 0.0)) * 1000)
            word_idx = int(w.get("word_index", 0))

            out.append(
                struct.pack("H", wl)
                + word_b
                + struct.pack("IIIIII", start_ms, end_ms, text_offset, seg_idx_u32, seg_off_ms, word_idx)
            )
        blob = b"".join(out)
        return zlib.compress(blob, 6)

    def _unpack_timing_data_compressed(self, blob: bytes) -> List[Dict[str, Any]]:
        data = zlib.decompress(blob)
        res: List[Dict[str, Any]] = []
        off = 0
        L = len(data)
        while off < L:
            wl = struct.unpack("H", data[off : off + 2])[0]
            off += 2
            word = data[off : off + wl].decode("utf-8")
            off += wl

            # prefer the 6-int format; fallback to 3-int if needed
            if off + 24 <= L:
                start_ms, end_ms, text_offset, seg_idx_u32, seg_off_ms, word_idx = struct.unpack(
                    "IIIIII", data[off : off + 24]
                )
                off += 24
                seg_idx = None if seg_idx_u32 == 0xFFFFFFFF else seg_idx_u32
                res.append(
                    {
                        "word": word,
                        "start_time": start_ms / 1000.0,
                        "end_time": end_ms / 1000.0,
                        "text_offset": text_offset,
                        "duration": (end_ms - start_ms) / 1000.0,
                        "segment_index": seg_idx,
                        "segment_offset": seg_off_ms / 1000.0,
                        "word_index": word_idx,
                    }
                )
            else:
                # legacy 3-int layout
                start_ms, end_ms, text_offset = struct.unpack("III", data[off : off + 12])
                off += 12
                res.append(
                    {
                        "word": word,
                        "start_time": start_ms / 1000.0,
                        "end_time": end_ms / 1000.0,
                        "text_offset": text_offset,
                        "duration": (end_ms - start_ms) / 1000.0,
                    }
                )
        return res

    # Optional legacy helpers if you still have old rows
    def _unpack_timing_data_legacy(self, blob: bytes) -> List[Dict[str, Any]]:
        # implement if you truly have old timing_data_packed rows; else return []
        return []

    def get_words_in_time_range(self, start_time: float, end_time: float) -> List[Dict[str, Any]]:
        words = self.unpack_word_timings()
        return [w for w in words if (w["end_time"] > start_time and w["start_time"] < end_time)]

    @property
    def compression_ratio(self) -> float:
        if self.compressed_timings and self.word_count:
            # Not exact; for a quick metric, we can’t easily retrieve original bytes here
            return 1.0  # maintain neutral value unless you compute original size
        return 1.0




class TTSVoiceSegment(Base):
    """Voice-specific audio segments"""
    
    __tablename__ = "tts_voice_segments"
    
    id = Column(Integer, primary_key=True, index=True)
    text_segment_id = Column(Integer, ForeignKey("tts_text_segments.id"), nullable=False)
    voice_id = Column(String, nullable=False)
    voice_name = Column(String, nullable=False)
    hls_segment_path = Column(String, nullable=False)
    actual_duration = Column(Float, nullable=False)
    status = Column(String, default='pending')  # pending, processing, ready, error
    
    # Audio metadata
    file_size = Column(Integer, nullable=True)
    audio_format = Column(String, default='mp3')
    bit_rate = Column(Integer, default=64000)
    sample_rate = Column(Integer, default=44100)
    channels = Column(Integer, default=2)
    
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    # Relationships
    text_segment = relationship("TTSTextSegment", back_populates="voice_segments")
    
    __table_args__ = (
        Index('ix_voice_segment_text_voice', 'text_segment_id', 'voice_id'),
        Index('ix_voice_segment_track', 'text_segment_id', 'status'),
    )


class TTSTrackMeta(Base):
    __tablename__ = "tts_track_meta"

    id = Column(Integer, primary_key=True, index=True)
    track_id = Column(
        String,
        ForeignKey("tracks.id", ondelete="CASCADE"),
        unique=True, index=True, nullable=False
    )

    total_segments    = Column(Integer, nullable=False)
    default_voice     = Column(String,  nullable=False)
    available_voices  = Column(JSONB,   default=list)
    total_words       = Column(Integer, nullable=False)
    total_characters  = Column(Integer, nullable=False)
    total_duration    = Column(Float,   nullable=False)
    segment_duration  = Column(Float,   default=30.0)
    words_per_segment = Column(Integer, nullable=False)
    compression_level = Column(Integer, default=6)
    average_compression_ratio = Column(Float, default=0.0)

    processing_status = Column(String,  default="pending")
    started_at  = Column(DateTime(timezone=True),
                         default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True))

    _processed_segments        = Column("processed_segments",        Integer, default=0)
    _progress_percentage       = Column("progress_percentage",       Float,   default=0.0)
    _failed_segments           = Column("failed_segments",           Integer, default=0)
    _average_compression_ratio = Column("average_compression_ratio", Float,   default=0.0)

    track = relationship("Track", back_populates="tts_meta")
    text_segments = relationship(
        "TTSTextSegment",
        back_populates="track_meta",
        cascade="all, delete-orphan",
        order_by="TTSTextSegment.segment_index"
    )

    __table_args__ = (
        Index("idx_tts_track_meta_track",  "track_id"),
        Index("idx_tts_track_meta_status", "processing_status"),
    )

    # ——— properties ——— #
    @property
    def processed_segments(self) -> int:
        return self._processed_segments or 0

    @processed_segments.setter
    def processed_segments(self, v: int) -> None:
        self._processed_segments = int(v)

    @property
    def progress_percentage(self) -> float:
        return self._progress_percentage or 0.0

    @progress_percentage.setter
    def progress_percentage(self, v: float) -> None:
        self._progress_percentage = float(v)

    @property
    def failed_segments(self) -> int:
        return self._failed_segments or 0

    @failed_segments.setter
    def failed_segments(self, v: int) -> None:
        self._failed_segments = int(v)

    @property
    def average_compression_ratio(self) -> float:
        return self._average_compression_ratio or 0.0

    @average_compression_ratio.setter
    def average_compression_ratio(self, v: float) -> None:
        self._average_compression_ratio = float(v)

    # ——— helpers ——— #
    def mark_started(self) -> None:
        self.processing_status = "processing"
        self.started_at = datetime.now(timezone.utc)

    def mark_complete(self) -> None:
        self.processing_status = "ready"
        self.progress_percentage = 100.0
        self.completed_at = datetime.now(timezone.utc)

    def mark_failed(self) -> None:
        self.processing_status = "error"

# ========================================
# STREAMING SERVICE CLASSES
# ========================================

class TTSStreamingService:
    """Service class for TTS streaming operations"""
    
    @staticmethod
    def get_track_metadata(track_id: str, db: Session) -> Optional[TTSTrackMeta]:
        """Get TTS track metadata"""
        return db.query(TTSTrackMeta).filter(TTSTrackMeta.track_id == track_id).first()
    
    @staticmethod
    def get_segment_at_time(track_id: str, time_position: float, db: Session) -> Optional[TTSTextSegment]:
        """Get text segment at specific time position"""
        return db.query(TTSTextSegment).filter(
            TTSTextSegment.track_id == track_id,
            TTSTextSegment.start_time <= time_position,
            TTSTextSegment.end_time > time_position
        ).first()
    
    @staticmethod
    def get_voice_segment_path(text_segment_id: int, voice_id: str, db: Session) -> Optional[str]:
        """Get voice segment file path"""
        voice_segment = db.query(TTSVoiceSegment).filter(
            TTSVoiceSegment.text_segment_id == text_segment_id,
            TTSVoiceSegment.voice_id == voice_id,
            TTSVoiceSegment.status == 'ready'
        ).first()
        
        return voice_segment.hls_segment_path if voice_segment else None
    
    @staticmethod
    def get_track_segments(track_id: str, db: Session) -> List[TTSTextSegment]:
        """Get all segments for a track"""
        return db.query(TTSTextSegment).filter(
            TTSTextSegment.track_id == track_id
        ).order_by(TTSTextSegment.segment_index).all()
    
    @staticmethod
    def get_segment_word_timings(segment_id: int, voice_id: str, db: Session) -> Optional[TTSWordTiming]:
        """Get word timings for specific segment and voice"""
        return db.query(TTSWordTiming).filter(
            TTSWordTiming.segment_id == segment_id,
            TTSWordTiming.voice_id == voice_id
        ).first()
    
    @staticmethod
    def create_track_metadata(track_id: str, **kwargs) -> TTSTrackMeta:
        """Create new track metadata"""
        return TTSTrackMeta(track_id=track_id, **kwargs)
    
    @staticmethod
    def update_processing_progress(track_id: str, progress: float, db: Session):
        """Update processing progress"""
        track_meta = TTSStreamingService.get_track_metadata(track_id, db)
        if track_meta:
            track_meta.progress_percentage = progress
            db.commit()
    
    @staticmethod
    def mark_track_completed(track_id: str, db: Session):
        """Mark track as completed"""
        track_meta = TTSStreamingService.get_track_metadata(track_id, db)
        if track_meta:
            track_meta.processing_status = 'ready'
            track_meta.progress_percentage = 100.0
            track_meta.completed_at = datetime.now(timezone.utc)
            db.commit()
    
    @staticmethod
    def mark_track_failed(track_id: str, db: Session):
        """Mark track as failed"""
        track_meta = TTSStreamingService.get_track_metadata(track_id, db)
        if track_meta:
            track_meta.processing_status = 'error'
            db.commit()

class FileStorageMetadata(Base):
    """Track file storage operations and metadata"""
    __tablename__ = "file_storage_metadata"
    
    id = Column(Integer, primary_key=True, index=True)
    file_path = Column(String(500), nullable=False, index=True, unique=True)
    file_hash = Column(String(64), nullable=False, index=True)
    file_type = Column(String(50), nullable=False)
    track_id = Column(String, ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False)
    voice_id = Column(String(100), nullable=True, index=True)
    original_size = Column(Integer, nullable=False)
    compressed_size = Column(Integer, nullable=False)
    compression_method = Column(String(20), default='zstd', nullable=False)
    compression_level = Column(Integer, default=3, nullable=False)
    status = Column(String(20), default='active', nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_accessed = Column(DateTime(timezone=True), nullable=True)
    access_count = Column(Integer, default=0, nullable=False)
    checksum_verified_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    
    # Relationship
    track = relationship("Track", back_populates="file_storage_metadata")


class AvailableVoice(Base):
    __tablename__ = "available_voices"

    id = Column(Integer, primary_key=True, index=True)
    voice_id = Column(String(100), unique=True, nullable=False, index=True)
    display_name = Column(String(100), nullable=False)
    language_code = Column(String(10), nullable=False)
    gender = Column(String(10), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<AvailableVoice(voice_id='{self.voice_id}', display_name='{self.display_name}')>"


class VoiceGenerationStatus(Base):
    """Track in-flight and completed voice generations for concurrency control"""
    __tablename__ = "voice_generation_status"

    track_id = Column(String, primary_key=True, nullable=False, index=True)
    voice_id = Column(String, primary_key=True, nullable=False)
    status = Column(String, nullable=False)  # 'generating', 'complete', 'failed'
    started_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)

    __table_args__ = (
        Index('ix_voice_gen_status_track_status', 'track_id', 'status'),
        Index('ix_voice_gen_status_status', 'status'),
    )


class ReadAlongSettings(Base):
    """Read-along specific settings for creators"""
    __tablename__ = "read_along_settings"
    
    id = Column(Integer, primary_key=True, index=True)
    creator_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    
    # Read-along tier requirement
    minimum_tier_cents = Column(Integer, default=0, nullable=False)  # 0 = free for all
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    creator = relationship("User", back_populates="read_along_settings")

# Add to User model
User.read_along_settings = relationship("ReadAlongSettings", back_populates="creator", uselist=False, cascade="all, delete-orphan")

class DownloadHistory(Base):
    """Simple download history tracking - track what was downloaded and outcome."""
    __tablename__ = "download_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    creator_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    # What was downloaded
    download_type = Column(String(10), nullable=False)   # 'album' or 'track'
    entity_id = Column(String(255), nullable=False)      # album_id or track_id
    voice_id = Column(String(100), nullable=True)        # For TTS tracks

    # Outcome
    status = Column(String(10), nullable=False, server_default="success")  # 'success' or 'failure'
    error_message = Column(Text, nullable=True)

    # When it was downloaded
    downloaded_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    creator = relationship("User", foreign_keys=[creator_id])

    __table_args__ = (
        Index('ix_download_history_user', 'user_id'),
        Index('ix_download_history_creator', 'creator_id'),
        Index('ix_download_history_date', 'downloaded_at'),
        Index('ix_download_history_type', 'download_type'),
        Index('ix_download_history_status', 'status'),
    )

    @classmethod
    def record_download(
        cls,
        db: Session,
        user_id: int,
        creator_id: int,
        download_type: str,
        entity_id: str,
        voice_id: str = None,
        status: str = "success",
        error_message: str = None,
    ):
        """Record a download attempt/outcome."""
        try:
            record = cls(
                user_id=user_id,
                creator_id=creator_id,
                download_type=download_type,
                entity_id=entity_id,
                voice_id=voice_id,
                status=status,
                error_message=error_message[:2000] if error_message else None,  # guard size
            )
            db.add(record)
            db.commit()
            outcome = "success" if status == "success" else "failure"
            logger.info(f"Recorded {download_type} download ({outcome}) for user {user_id}")
        except Exception as e:
            db.rollback()
            logger.error(f"Error recording download history: {str(e)}")

    @classmethod
    def get_creator_counts(cls, db: Session, creator_id: int, days: int = 180) -> dict:
        """Get successful download counts for a creator over X days."""
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)

            result = db.query(
                cls.download_type,
                func.count(cls.id).label('count')
            ).filter(
                and_(
                    cls.creator_id == creator_id,
                    cls.downloaded_at >= cutoff,
                    cls.status == "success"  # only count successful downloads
                )
            ).group_by(cls.download_type).all()

            counts = {'albums': 0, 'tracks': 0}
            for row in result:
                counts[row.download_type + 's'] = row.count  # 'album' -> 'albums'

            return counts

        except Exception as e:
            logger.error(f"Error getting download counts: {str(e)}")
            return {'albums': 0, 'tracks': 0}

    @classmethod
    def cleanup_old_records(cls, db: Session, days_to_keep: int = 180):
        """Clean up old download records."""
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days_to_keep)
            deleted = db.query(cls).filter(cls.downloaded_at < cutoff).delete(synchronize_session=False)
            db.commit()
            if deleted > 0:
                logger.info(f"Cleaned up {deleted} old download history records")
            return deleted
        except Exception as e:
            db.rollback()
            logger.error(f"Error cleaning up download history: {str(e)}")
            return 0