"""
Centralized Permission System for Audio Streaming App

This module consolidates all permission-related logic including:
- Permission enum definitions
- Role-based permission mappings
- User permission checks and decorators
- Tier-based access control

All permission logic is centralized here for better visibility and management.
"""

from enum import Flag, auto
from typing import Dict, List, Optional, Tuple
from functools import wraps
from datetime import datetime, timezone, timedelta
import logging

from fastapi import HTTPException, Depends
from sqlalchemy.orm import Session
from sqlalchemy import and_

# Logger
logger = logging.getLogger(__name__)

# ============================================================================
# PERMISSION ENUM (moved from models.py)
# ============================================================================

class Permission(Flag):
    """
    Permission flags using bitwise operations for efficient permission checking.
    Can be combined using | (OR) and checked using & (AND).
    """
    NONE = 0
    VIEW = auto()
    CREATE = auto()
    RENAME = auto()
    DELETE = auto()
    DOWNLOAD = auto()
    ALL = VIEW | CREATE | RENAME | DELETE | DOWNLOAD
    TEAM_ACCESS = VIEW | CREATE | RENAME | DOWNLOAD


# ============================================================================
# ROLE PERMISSIONS MAPPING (moved from app.py)
# ============================================================================

def get_role_permissions_mapping():
    """
    Get the mapping of UserRole to Permission flags.
    This is a function to avoid circular import issues with UserRole.
    """
    from models import UserRole

    return {
        UserRole.CREATOR: Permission.ALL,
        UserRole.TEAM: Permission.TEAM_ACCESS,
        UserRole.PATREON: Permission.VIEW | Permission.DOWNLOAD,
        UserRole.KOFI: Permission.VIEW | Permission.DOWNLOAD,
        UserRole.GUEST: Permission.VIEW
    }


# ============================================================================
# DETAILED PERMISSION FUNCTION (moved from app.py line 1210)
# ============================================================================

def get_user_permissions(user) -> Dict:
    """
    Get detailed permissions based on user role - FIXED: Proper Guest Trial handling

    This function returns a comprehensive dictionary with all permissions,
    tier info, download limits, and usage tracking.

    Args:
        user: User object with role and tier information

    Returns:
        Dict containing all permission flags and metadata
    """
    from models import UserTier, CampaignTier, UserRole

    logger.info(f"Getting permissions for user {user.email} (creator: {user.is_creator}, team: {user.is_team}, patreon: {user.is_patreon}, kofi: {user.is_kofi}, guest_trial: {user.is_guest_trial})")

    # Base permissions structure
    permissions = {
        "can_view": False,
        "can_create": False,
        "can_rename": False,
        "can_delete": False,
        "can_delete_albums": False,
        "can_delete_tracks": False,
        "can_download": False,
        "downloads_blocked": False,
        "is_creator": user.is_creator,
        "is_team": user.is_team,
        "is_patreon": user.is_patreon,
        "is_kofi": user.is_kofi,
        "is_guest_trial": user.is_guest_trial
    }

    # Creator permissions
    if user.is_creator:
        permissions.update({
            "can_view": True,
            "can_create": True,
            "can_rename": True,
            "can_delete": True,
            "can_delete_albums": True,
            "can_delete_tracks": True,
            "can_manage_team": True,
            "can_download": True,
            "role_type": "creator"
        })
        logger.info(f"Set creator permissions for {user.email}")
        return permissions

    # ✅ FIXED: Guest Trial permissions - Read from tier association
    if user.is_guest_trial and user.role == UserRole.GUEST:
        logger.info(f"Processing guest trial user: {user.email}")

        # Check if trial is still active
        if not user.trial_active:
            logger.info(f"Guest trial expired for {user.email}")
            permissions.update({
                "can_view": True,
                "can_create": False,
                "can_rename": False,
                "can_delete": False,
                "can_delete_albums": False,
                "can_delete_tracks": False,
                "can_download": False,
                "downloads_blocked": True,
                "role_type": "expired_guest_trial",
                "trial_expired": True,
                "album_downloads_allowed": 0,
                "track_downloads_allowed": 0,
                "book_requests_allowed": 0,
                "max_sessions": 1
            })
            return permissions

        # ✅ Active trial - get benefits from tier association (NOT stored data)
        from sqlalchemy.orm import sessionmaker
        from database import engine

        # Get database session (this is a hack but needed for the query)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db = SessionLocal()

        try:
            # Debug: Log what we're looking for
            logger.info(f"Looking for UserTier associations for guest trial user {user.id}")

            # Find any active UserTier association for this user
            user_tier = db.query(UserTier).filter(
                and_(
                    UserTier.user_id == user.id,
                    UserTier.is_active == True
                )
            ).first()

            tier = None
            if user_tier:
                logger.info(f"Found UserTier association: user_id={user_tier.user_id}, tier_id={user_tier.tier_id}")

                # Get the actual tier using tier_id
                tier = db.query(CampaignTier).filter(
                    CampaignTier.id == user_tier.tier_id
                ).first()

                if tier:
                    logger.info(f"Found associated tier: {tier.title} (ID: {tier.id}, platform: {tier.platform_type})")

                    # Check if this is the Guest Trial tier
                    if tier.title == "Guest Trial" and tier.platform_type == "KOFI":
                        logger.info(f"Confirmed Guest Trial tier for user {user.email}")
                    else:
                        logger.warning(f"User {user.email} has different tier: {tier.title} (platform: {tier.platform_type})")
                else:
                    logger.error(f"Could not find CampaignTier with ID {user_tier.tier_id}")
                    tier = None
            else:
                logger.warning(f"No active UserTier association found for guest trial user {user.email} (ID: {user.id})")

                # Try to find Guest Trial tier and create association
                guest_tier = db.query(CampaignTier).filter(
                    and_(
                        CampaignTier.creator_id == user.created_by,
                        CampaignTier.title == "Guest Trial",
                        CampaignTier.platform_type == "KOFI",
                        CampaignTier.is_active == True
                    )
                ).first()

                if guest_tier:
                    logger.info(f"Found Guest Trial tier {guest_tier.id}, creating association for user {user.email}")

                    # Create missing UserTier association
                    user_tier_association = UserTier(
                        user_id=user.id,
                        tier_id=guest_tier.id,
                        joined_at=user.trial_started_at or datetime.now(timezone.utc),
                        expires_at=user.trial_expires_at,
                        is_active=True,
                        payment_status='guest_trial'
                    )
                    db.add(user_tier_association)
                    db.commit()

                    tier = guest_tier
                    logger.info(f"✅ Created missing UserTier association for guest trial user {user.email}")
                else:
                    logger.error(f"No Guest Trial tier found for creator {user.created_by}")
                    tier = None

            if tier:
                # Get usage from user data (reset monthly)
                tier_data = user.patreon_tier_data or {}
                album_used = tier_data.get('album_downloads_used', 0)
                track_used = tier_data.get('track_downloads_used', 0)
                book_used = tier_data.get('book_requests_used', 0)

                permissions.update({
                    "can_view": True,
                    "can_create": False,
                    "can_rename": False,
                    "can_delete": False,
                    "can_delete_albums": False,
                    "can_delete_tracks": False,
                    "can_download": (tier.album_downloads_allowed > 0 or tier.track_downloads_allowed > 0),
                    "downloads_blocked": False,
                    "role_type": "guest_trial",
                    "trial_active": True,
                    "trial_expires_at": user.trial_expires_at.isoformat() if user.trial_expires_at else None,
                    "trial_hours_remaining": user.trial_hours_remaining,

                    # ✅ Benefits from tier (NOT stored in user data)
                    "album_downloads_allowed": tier.album_downloads_allowed,
                    "track_downloads_allowed": tier.track_downloads_allowed,
                    "book_requests_allowed": tier.book_requests_allowed,
                    "chapters_allowed_per_book_request": getattr(tier, 'chapters_allowed_per_book_request', 0),
                    "max_sessions": tier.max_sessions,

                    # Usage tracking (from user data)
                    "album_downloads_used": album_used,
                    "track_downloads_used": track_used,
                    "book_requests_used": book_used,
                    "album_downloads_remaining": max(0, tier.album_downloads_allowed - album_used),
                    "track_downloads_remaining": max(0, tier.track_downloads_allowed - track_used),
                    "book_requests_remaining": max(0, tier.book_requests_allowed - book_used)
                })

                logger.info(f"✅ Guest trial permissions set from tier: {tier.title} (ID: {tier.id}) - "
                           f"Albums: {tier.album_downloads_allowed} (used: {album_used}), "
                           f"Tracks: {tier.track_downloads_allowed} (used: {track_used}), "
                           f"Books: {tier.book_requests_allowed} (used: {book_used})")

                return permissions
            else:
                logger.warning(f"No Guest Trial tier found for user {user.email}")
                # Fallback to minimal permissions
                permissions.update({
                    "can_view": True,
                    "role_type": "guest_trial_no_tier",
                    "album_downloads_allowed": 0,
                    "track_downloads_allowed": 0,
                    "book_requests_allowed": 0,
                    "max_sessions": 1
                })
                return permissions

        finally:
            db.close()

    # Team member permissions
    if user.is_team:
        tier_data = user.patreon_tier_data or {}
        album_downloads = tier_data.get('album_downloads_allowed', 0)
        track_downloads = tier_data.get('track_downloads_allowed', 0)

        # INDIVIDUAL DELETION PERMISSIONS FOR ALBUMS
        album_deletions_allowed = tier_data.get('album_deletions_allowed', 0)
        album_deletions_used = tier_data.get('album_deletions_used', 0)

        # INDIVIDUAL DELETION PERMISSIONS FOR TRACKS
        track_deletions_allowed = tier_data.get('track_deletions_allowed', 0)
        track_deletions_used = tier_data.get('track_deletions_used', 0)

        # Check if 24-hour period has reset
        deletion_start = tier_data.get('deletion_period_start')
        if deletion_start and (album_deletions_allowed > 0 or track_deletions_allowed > 0):
            try:
                start_time = datetime.fromisoformat(deletion_start.replace('Z', '+00:00'))
                if datetime.now(timezone.utc) >= start_time + timedelta(hours=24):
                    pass
            except (ValueError, TypeError):
                pass

        can_delete_albums = album_deletions_allowed > 0 and album_deletions_used < album_deletions_allowed
        can_delete_tracks = track_deletions_allowed > 0 and track_deletions_used < track_deletions_allowed

        permissions.update({
            "can_view": True,
            "can_create": True,
            "can_rename": True,
            "can_delete": can_delete_albums or can_delete_tracks,
            "can_delete_albums": can_delete_albums,
            "can_delete_tracks": can_delete_tracks,
            "can_download": album_downloads >= 0 or track_downloads >= 0,
            "creator_id": user.created_by,
            "role_type": "team",
            "album_downloads": album_downloads,
            "track_downloads": track_downloads,
            "album_deletions_allowed": album_deletions_allowed,
            "album_deletions_used": album_deletions_used,
            "album_deletions_remaining": max(0, album_deletions_allowed - album_deletions_used),
            "track_deletions_allowed": track_deletions_allowed,
            "track_deletions_used": track_deletions_used,
            "track_deletions_remaining": max(0, track_deletions_allowed - track_deletions_used)
        })
        return permissions

    # Patreon member permissions
    if user.is_patreon:
        if user.patreon_tier_data:
            logger.info(f"Patreon tier data for {user.email}: {user.patreon_tier_data}")

        album_downloads = user.patreon_tier_data.get('album_downloads_allowed', 0) if user.patreon_tier_data else 0
        track_downloads = user.patreon_tier_data.get('track_downloads_allowed', 0) if user.patreon_tier_data else 0

        logger.info(
            f"Patron {user.email} download limits: "
            f"Albums={album_downloads}, Tracks={track_downloads}"
        )

        permissions.update({
            "can_view": True,
            "can_create": False,
            "can_rename": False,
            "can_delete": False,
            "can_delete_albums": False,
            "can_delete_tracks": False,
            "can_download": album_downloads > 0 or track_downloads > 0,
            "tier_info": user.get_tier_info(),
            "role_type": "patreon",
            "album_downloads": album_downloads,
            "track_downloads": track_downloads
        })
        logger.info(f"Set patreon permissions for {user.email}: {permissions}")
        return permissions

    # Ko‑fi member permissions
    if user.is_kofi:
        if user.patreon_tier_data:
            logger.info(f"Ko-fi tier data for {user.email}: {user.patreon_tier_data}")

        album_downloads = user.patreon_tier_data.get('album_downloads_allowed', 0) if user.patreon_tier_data else 0
        track_downloads = user.patreon_tier_data.get('track_downloads_allowed', 0) if user.patreon_tier_data else 0
        book_requests = user.patreon_tier_data.get('book_requests_allowed', 0) if user.patreon_tier_data else 0

        logger.info(
            f"Ko-fi user {user.email} download limits: "
            f"Albums={album_downloads}, Tracks={track_downloads}"
        )

        permissions.update({
            "can_view": True,
            "can_create": False,
            "can_rename": False,
            "can_delete": False,
            "can_delete_albums": False,
            "can_delete_tracks": False,
            "can_download": album_downloads > 0 or track_downloads > 0,
            "tier_info": user.get_tier_info(),
            "role_type": "kofi",
            "album_downloads": album_downloads,
            "track_downloads": track_downloads,
            "book_requests": book_requests
        })
        logger.info(f"Set Ko-fi permissions for {user.email}: {permissions}")
        return permissions

    # If no special role is detected, return base permissions with role_type 'unknown'
    permissions["role_type"] = "unknown"
    logger.info(f"No special permissions for {user.email}, using base permissions: {permissions}")
    return permissions


# ============================================================================
# FLAG-BASED PERMISSION FUNCTION (moved from app.py line 1599)
# ============================================================================

def get_user_permission_flags(user) -> Permission:
    """
    Get permissions for a given user based on their role as Permission flags.

    This function returns Permission enum flags for bitwise operations.
    Use this for simple permission checks.

    Args:
        user: User object with role information

    Returns:
        Permission flags combined with bitwise OR
    """
    # Check user role and return appropriate permissions
    if user.is_creator:
        return Permission.ALL
    elif user.is_team:
        return Permission.TEAM_ACCESS
    elif user.is_patreon:
        return Permission.VIEW | Permission.DOWNLOAD
    elif user.is_kofi:
        return Permission.VIEW | Permission.DOWNLOAD
    else:
        return Permission.VIEW  # Default guest permissions


# ============================================================================
# TEMPLATE PERMISSION DICT (moved from app.py line 1614)
# ============================================================================

def get_user_permissions_dict(user) -> dict:
    """
    Get permissions for a user as a dictionary for templates.

    Converts Permission flags to a simple dict with boolean values
    for easy use in Jinja2 templates.

    Args:
        user: User object

    Returns:
        Dict with permission booleans and user role flags
    """
    perms = get_user_permission_flags(user)

    # Check if downloads are currently blocked (e.g., system maintenance)
    downloads_blocked = False  # You can add logic here if needed

    return {
        "can_view": bool(perms & Permission.VIEW),
        "can_create": bool(perms & Permission.CREATE),
        "can_rename": bool(perms & Permission.RENAME),
        "can_delete": bool(perms & Permission.DELETE),
        "can_download": bool(perms & Permission.DOWNLOAD),
        "downloads_blocked": downloads_blocked,
        "is_creator": user.is_creator if user else False,
        "is_team": user.is_team if user else False
    }


# ============================================================================
# PERMISSION CHECKER (moved from app.py line 1636)
# ============================================================================

def check_permission(user, required_permission: Permission):
    """
    Check if user has the required permission.

    Raises HTTPException if user doesn't have permission.

    Args:
        user: User object
        required_permission: Permission flag(s) to check

    Raises:
        HTTPException: If user lacks required permission
    """
    if not user:
        raise HTTPException(
            status_code=403,
            detail="Authentication required"
        )

    # Get user permissions
    permissions = get_user_permission_flags(user)

    if not (permissions & required_permission):
        raise HTTPException(
            status_code=403,
            detail="You don't have permission to perform this action"
        )


# ============================================================================
# ROLE PERMISSION DECORATOR (moved from app.py line 1523)
# ============================================================================

def verify_role_permission(allowed_roles: List[str]):
    """
    Decorator to verify user has one of the allowed roles.

    Uses the detailed get_user_permissions() function to check role_type.

    Args:
        allowed_roles: List of allowed role strings (e.g., ["creator", "team"])

    Returns:
        Decorator function

    Raises:
        HTTPException: If user doesn't have an allowed role
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(
            *args,
            current_user = None,  # Will be injected by Depends(login_required)
            **kwargs
        ):
            # Import here to avoid circular imports
            from auth import login_required

            # If current_user not provided in kwargs, it should be in args
            if current_user is None:
                # Find current_user in kwargs
                current_user = kwargs.get('current_user')

            if current_user is None:
                raise HTTPException(
                    status_code=403,
                    detail="Authentication required"
                )

            permissions = get_user_permissions(current_user)

            if permissions["role_type"] not in allowed_roles:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "message": "Access denied",
                        "required_roles": allowed_roles,
                        "current_role": permissions["role_type"]
                    }
                )

            return await func(*args, current_user=current_user, **kwargs)
        return wrapper
    return decorator


# ============================================================================
# TIER-BASED ACCESS CHECK (from enhanced_app_routes_voice.py line 116)
# ============================================================================

def check_tier_access(track, current_user) -> Tuple[bool, str]:
    """
    Check if user has sufficient tier level to access track.
    Returns (has_access, error_message)

    Security: This prevents users from bypassing frontend checks by directly
    requesting HLS segments, playlists, or master files.

    Args:
        track: Track object with album and tier restrictions
        current_user: User object

    Returns:
        Tuple of (has_access: bool, error_message: str)
    """
    # Creator and team bypass all restrictions
    if current_user.is_creator or current_user.is_team:
        return True, ""

    # Get album restrictions
    album = track.album
    if not album:
        return True, ""

    restrictions = album.tier_restrictions

    # If no restrictions or not explicitly restricted, grant access
    if not restrictions or restrictions.get("is_restricted") is not True:
        return True, ""

    # Get required tier info for error message
    required_tier = restrictions.get("minimum_tier", "").strip()
    tier_message = f"the {required_tier} tier or above" if required_tier else "a higher tier subscription"

    # Get user's tier data
    tier_data = current_user.patreon_tier_data if current_user.patreon_tier_data else {}
    user_amount = tier_data.get("amount_cents", 0)
    required_amount = restrictions.get("minimum_tier_amount", 0)

    # Check Patreon, Ko-fi, and guest trial users
    if (current_user.is_patreon or current_user.is_kofi or current_user.is_guest_trial) and tier_data:
        # Simple amount check
        if user_amount >= required_amount:
            return True, ""

        # Special case for Ko-fi users with donations
        if current_user.is_kofi and tier_data.get('has_donations', False):
            donation_amount = tier_data.get('donation_amount_cents', 0)
            total_amount = user_amount + donation_amount

            if total_amount >= required_amount:
                return True, ""

    # Access denied
    error_msg = f"This content requires {tier_message}"
    return False, error_msg


# ============================================================================
# SIMPLE PERMISSION HELPER (from enhanced_app_routes_voice.py line 105)
# ============================================================================

def get_simple_user_permissions(user) -> dict:
    """
    Get simplified user permissions for UI features.

    This is a lightweight version for comment/like/share permissions.

    Args:
        user: User object

    Returns:
        Dict with UI-specific permissions
    """
    return {
        "can_comment": True,
        "can_like": True,
        "can_share": True,
        "can_create": user.is_creator or user.is_team,
        "can_edit": user.is_creator or user.is_team,
        "can_delete": user.is_creator
    }
