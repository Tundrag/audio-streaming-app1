# permissions.py
from enum import Flag, auto
from typing import Dict
from fastapi import HTTPException
from models import PatreonTier

class Permission(Flag):
    NONE = 0
    VIEW = auto()
    CREATE = auto()
    RENAME = auto()
    DELETE = auto()
    ALL = VIEW | CREATE | RENAME | DELETE

# Global permission configuration
TIER_PERMISSIONS: Dict[PatreonTier, Permission] = {
    PatreonTier.CREATOR: Permission.ALL,  # Full access (developers)
    PatreonTier.TEAM: Permission.VIEW | Permission.CREATE | Permission.RENAME,  # Can create and rename, no delete
    PatreonTier.ARCHITECHT: Permission.VIEW,
    PatreonTier.MONARCHS: Permission.VIEW,
    PatreonTier.GOD: Permission.VIEW,
    PatreonTier.EMPEROR: Permission.VIEW,
    PatreonTier.KING: Permission.VIEW,
    PatreonTier.GREAT: Permission.VIEW,
    PatreonTier.SUPPORT: Permission.VIEW,
}

def get_user_permissions(user) -> Permission:
    """Get permissions for a given user"""
    if user.is_creator:
        return Permission.ALL
    if user.is_team:
        return Permission.VIEW | Permission.CREATE | Permission.RENAME
    return TIER_PERMISSIONS.get(user.tier, Permission.NONE)

def check_permission(user, required_permission: Permission) -> bool:
    """Check if user has the required permission"""
    user_permissions = get_user_permissions(user)
    return bool(user_permissions & required_permission)

def verify_permission(user, required_permission: Permission):
    """Verify user permission or raise HTTPException"""
    if not check_permission(user, required_permission):
        raise HTTPException(
            status_code=403,
            detail="You don't have permission to perform this action"
        )

# Permission decorator for routes
def require_permission(required_permission: Permission):
    def decorator(func):
        async def wrapper(*args, **kwargs):
            user = kwargs.get('current_user')
            if not user:
                raise HTTPException(
                    status_code=401,
                    detail="Authentication required"
                )
            verify_permission(user, required_permission)
            return await func(*args, **kwargs)
        return wrapper
    return decorator

# Content access control
def can_access_content(user, content_tier: PatreonTier) -> bool:
    """Check if user can access content of given tier"""
    if user.is_creator or user.is_team:
        return True
    return user.tier.value >= content_tier.value

def verify_content_access(user, content_tier: PatreonTier):
    """Verify content access or raise HTTPException"""
    if not can_access_content(user, content_tier):
        raise HTTPException(
            status_code=403,
            detail="Your current tier does not have access to this content"
        )