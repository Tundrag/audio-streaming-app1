#platform_tier.py
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import and_, func
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import logging
import json
from fastapi import Body

from models import User, UserRole, CampaignTier, PlatformType
from database import get_db
from auth import login_required

logger = logging.getLogger(__name__)

# Create router
platform_router = APIRouter(
    prefix="/api/platforms",
    tags=["platforms"]
)

# Platform types enum - now we can reference these from the model
PLATFORM_TYPES = ["PATREON", "KOFI"]

# Define the verify_role_permission function directly here instead of importing it
def verify_role_permission(allowed_roles: List[str]):
    def decorator(func):
        @wraps(func)
        async def wrapper(
            *args,
            current_user: User = Depends(login_required),
            **kwargs
        ):
            # Get user permissions
            if current_user.role.value.lower() not in [r.lower() for r in allowed_roles]:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "message": "Access denied",
                        "required_roles": allowed_roles,
                        "current_role": current_user.role.value
                    }
                )

            return await func(*args, current_user=current_user, **kwargs)
        return wrapper
    return decorator

# Need to import wraps here after defining the decorator that uses it
from functools import wraps

@platform_router.get("/tiers")
@verify_role_permission(["creator"])
async def get_platform_tiers(
    platform: str,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get tiers for a specific platform (patreon, kofi)"""
    try:
        platform = platform.upper()
        if platform not in PLATFORM_TYPES:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid platform. Must be one of: {', '.join(PLATFORM_TYPES).lower()}"
            )
            
        # Use string directly instead of enum
        tiers = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == current_user.id,
                CampaignTier.platform_type == platform,  # Direct string comparison
                CampaignTier.is_active == True
            )
        ).order_by(CampaignTier.amount_cents).all()      
        return {
            "platform": platform,
            "tiers": [
                {
                    "id": tier.id,
                    "title": tier.title,
                    "description": tier.description,
                    "amount_cents": tier.amount_cents,
                    "album_downloads_allowed": tier.album_downloads_allowed,
                    "track_downloads_allowed": tier.track_downloads_allowed,
                    "max_sessions": tier.max_sessions,
                    "patron_count": tier.patron_count,
                    "is_active": tier.is_active
                }
                for tier in tiers
            ]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching platform tiers: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching platform tiers: {str(e)}")


@platform_router.post("/import/kofi")
@verify_role_permission(["creator"])
async def import_kofi_tiers(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Import Ko-fi tiers from existing Ko-fi users"""
    try:
        # Find all Ko-fi users for this creator
        kofi_users = db.query(User).filter(
            and_(
                User.created_by == current_user.id,
                User.role == UserRole.KOFI,
                User.is_active == True
            )
        ).all()
        
        if not kofi_users:
            return {
                "status": "info",
                "message": "No Ko-fi users found to import tiers from"
            }
            
        logger.info(f"Found {len(kofi_users)} Ko-fi users to analyze for tier import")
        
        # Dictionary to collect unique Ko-fi tiers by title
        unique_tiers = {}
        
        # Process each Ko-fi user
        for user in kofi_users:
            if not user.patreon_tier_data:
                continue
                
            tier_data = user.patreon_tier_data
            tier_title = tier_data.get('title', 'Ko-fi Supporter')
            
            # Create a key for this tier - combine title and price to ensure uniqueness
            amount_cents = tier_data.get('amount_cents', 0)
            tier_key = f"{tier_title}_{amount_cents}"
            
            if tier_key not in unique_tiers:
                # This is a new tier we haven't seen before
                unique_tiers[tier_key] = {
                    'title': tier_title,
                    'amount_cents': amount_cents,
                    'album_downloads_allowed': tier_data.get('album_downloads_allowed', 0),
                    'track_downloads_allowed': tier_data.get('track_downloads_allowed', 0),
                    'max_sessions': tier_data.get('max_sessions', 1),
                    'description': tier_data.get('description', f"Ko-fi {tier_title} tier"),
                    'patron_count': 1
                }
            else:
                # We've seen this tier before, increment the patron count
                unique_tiers[tier_key]['patron_count'] += 1
        
        logger.info(f"Found {len(unique_tiers)} unique Ko-fi tiers to import")
        
        # Check for existing Ko-fi tiers to avoid duplicates
        existing_kofi_tiers = []
        
        # If the platform_type column exists, filter by it
        try:
            existing_kofi_tiers = db.query(CampaignTier).filter(
                and_(
                    CampaignTier.creator_id == current_user.id,
                    CampaignTier.platform_type == PlatformType.KOFI  # FIXED: Using enum
                )
            ).all()
        except Exception:
            # If the column doesn't exist yet, check by title containing "kofi"
            existing_kofi_tiers = db.query(CampaignTier).filter(
                and_(
                    CampaignTier.creator_id == current_user.id,
                    func.lower(CampaignTier.title).contains("kofi")
                )
            ).all()
            
        existing_tier_keys = set()
        for tier in existing_kofi_tiers:
            existing_tier_keys.add(f"{tier.title}_{tier.amount_cents}")
            
        logger.info(f"Found {len(existing_tier_keys)} existing Ko-fi tiers")
        
        # Create new campaign tiers for each unique Ko-fi tier
        imported_tiers = []
        for tier_key, tier_data in unique_tiers.items():
            if tier_key in existing_tier_keys:
                logger.info(f"Skipping existing Ko-fi tier: {tier_data['title']}")
                continue
                
            logger.info(f"Importing Ko-fi tier: {tier_data['title']}")
            
            # Create a new campaign tier for this Ko-fi tier
            new_tier = CampaignTier(
                creator_id=current_user.id,
                title=tier_data['title'],
                description=tier_data['description'],
                amount_cents=tier_data['amount_cents'],
                album_downloads_allowed=tier_data['album_downloads_allowed'],
                track_downloads_allowed=tier_data['track_downloads_allowed'],
                max_sessions=tier_data['max_sessions'],
                patron_count=tier_data['patron_count'],
                is_active=True,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            
            # Only set platform_type if the column exists
            try:
                new_tier.platform_type = PlatformType.KOFI  # FIXED: Using enum
            except:
                # Column doesn't exist yet, so just make sure "kofi" is in the title
                if "kofi" not in new_tier.title.lower():
                    new_tier.title = f"Ko-fi {new_tier.title}"
            
            db.add(new_tier)
            imported_tiers.append(tier_data['title'])
        
        # Commit the changes
        if imported_tiers:
            db.commit()
            logger.info(f"Successfully imported {len(imported_tiers)} Ko-fi tiers")
            return {
                "status": "success", 
                "message": f"Successfully imported {len(imported_tiers)} Ko-fi tiers",
                "imported_tiers": imported_tiers
            }
        else:
            logger.info("No new Ko-fi tiers to import")
            return {
                "status": "info",
                "message": "No new Ko-fi tiers to import",
                "imported_tiers": []
            }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error importing Ko-fi tiers: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error importing Ko-fi tiers: {str(e)}")

@platform_router.post("/update/platform-types")
@verify_role_permission(["creator"])
async def update_platform_types(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Update existing tiers to have the correct platform type"""
    try:
        # Get all tiers without a platform type
        tiers_without_platform = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == current_user.id,
                CampaignTier.platform_type.is_(None)  # Tiers with NULL platform_type
            )
        ).all()
        
        updated_count = 0
        
        # Update each tier - assume all are Patreon tiers by default
        for tier in tiers_without_platform:
            # Check if it's likely a Ko-fi tier
            if tier.title and "kofi" in tier.title.lower():
                tier.platform_type = PlatformType.KOFI  # FIXED: Using enum
            else:
                tier.platform_type = PlatformType.PATREON  # FIXED: Using enum
            updated_count += 1
            
        db.commit()
        
        return {
            "status": "success",
            "message": f"Updated platform types for {updated_count} tiers",
            "updated_count": updated_count
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating platform types: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating platform types: {str(e)}")


@platform_router.post("/tiers")
@verify_role_permission(["creator"])
async def create_tier(
    data: dict = Body(...),
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Create a new tier for the current creator"""
    try:
        # Extract tier data from request
        title = data.get("title")
        platform_type = data.get("platform_type")
        amount_cents = data.get("amount_cents", 0)
        album_downloads_allowed = data.get("album_downloads_allowed", 0)
        track_downloads_allowed = data.get("track_downloads_allowed", 0)
        book_requests_allowed = data.get("book_requests_allowed", 0)
        max_sessions = data.get("max_sessions", 1)
        is_active = data.get("is_active", True)
        
        # Validate required fields
        if not title:
            raise HTTPException(status_code=400, detail="Title is required")
            
        # Validate platform type
        platform_type = platform_type.upper() if platform_type else "PATREON"
        if platform_type not in ["PATREON", "KOFI"]:
            raise HTTPException(
                status_code=400,
                detail="Invalid platform type. Must be PATREON or KOFI"
            )
            
        # Check for duplicate tier title
        existing_tier = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == current_user.id,
                func.lower(CampaignTier.title) == func.lower(title)
            )
        ).first()
        
        if existing_tier:
            raise HTTPException(
                status_code=400,
                detail="A tier with this title already exists"
            )
            
        # Validate max_sessions
        if max_sessions < 1 or max_sessions > 5:
            raise HTTPException(
                status_code=400,
                detail="Max sessions must be between 1 and 5"
            )
        
        # Create new tier with direct string assignment
        new_tier = CampaignTier(
            creator_id=current_user.id,
            title=title,
            description="",  # Can be empty
            amount_cents=amount_cents,
            album_downloads_allowed=album_downloads_allowed,
            track_downloads_allowed=track_downloads_allowed,
            book_requests_allowed=book_requests_allowed,
            max_sessions=max_sessions,
            platform_type=platform_type,  # Direct string assignment instead of enum
            is_active=is_active,
            patron_count=0,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        
        db.add(new_tier)
        db.commit()
        db.refresh(new_tier)
        
        return {
            "status": "success",
            "message": "Tier created successfully",
            "tier": {
                "id": new_tier.id,
                "title": new_tier.title,
                "amount_cents": new_tier.amount_cents,
                "album_downloads_allowed": new_tier.album_downloads_allowed,
                "track_downloads_allowed": new_tier.track_downloads_allowed,
                "book_requests_allowed": new_tier.book_requests_allowed,
                "max_sessions": new_tier.max_sessions,
                "platform_type": new_tier.platform_type,  # Direct access without .value
                "is_active": new_tier.is_active,
                "patron_count": 0
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating tier: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error creating tier: {str(e)}")

@platform_router.delete("/tier/{tier_id}")
@verify_role_permission(["creator"])
async def delete_tier(
    tier_id: int,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Delete a tier regardless of patron count"""
    try:
        # Find the tier
        tier = db.query(CampaignTier).filter(
            and_(
                CampaignTier.id == tier_id,
                CampaignTier.creator_id == current_user.id
            )
        ).first()
        
        if not tier:
            raise HTTPException(
                status_code=404,
                detail="Tier not found"
            )
        
        # Log patron count information but don't prevent deletion
        if tier.patron_count > 0:
            logger.warning(f"Deleting tier '{tier.title}' with {tier.patron_count} active patrons")
        
        # Save tier info for the response before deletion
        tier_title = tier.title
        patron_count = tier.patron_count
        
        # Delete the tier
        db.delete(tier)
        db.commit()
        
        return {
            "status": "success",
            "message": f"Tier '{tier_title}' deleted successfully",
            "patron_count": patron_count
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting tier: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting tier: {str(e)}")