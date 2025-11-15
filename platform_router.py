# platform_router
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import and_, func
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import logging
import json

from models import User, UserRole, CampaignTier
from database import get_db
from auth import login_required

logger = logging.getLogger(__name__)

# Create router
platform_router = APIRouter(
    prefix="/api/platforms",
    tags=["platforms"]
)

# Platform types enum
PLATFORM_TYPES = ["PATREON", "KOFI"]

@platform_router.get("/tiers")
async def get_platform_tiers(
    platform: str,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get tiers for a specific platform (patreon, kofi)"""
    # Check if user is creator
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Only creators can access this endpoint")

    try:
        platform = platform.upper()
        if platform not in PLATFORM_TYPES:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid platform. Must be one of: {', '.join(PLATFORM_TYPES).lower()}"
            )
            
        # Query for tiers with this platform
        tiers = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == current_user.id,
                CampaignTier.platform_type == platform,
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
async def import_kofi_tiers(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Import Ko-fi tiers from existing Ko-fi users"""
    # Check if user is creator
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Only creators can access this endpoint")

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
        existing_kofi_tiers = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == current_user.id,
                CampaignTier.platform_type == "KOFI"
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
                platform_type="KOFI",  # Mark this as a Ko-fi tier
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            
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

# Add this endpoint to update all Patreon tiers to have the correct platform type
@platform_router.post("/update/platform-types")
async def update_platform_types(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Update existing tiers to have the correct platform type"""
    # Check if user is creator
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Only creators can access this endpoint")

    try:
        # First, get all tiers without a platform type
        tiers_without_platform = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == current_user.id,
                CampaignTier.platform_type.is_(None)  # Tiers with NULL platform_type
            )
        ).all()
        
        updated_count = 0
        
        # Update each tier - assume all existing tiers are Patreon tiers
        for tier in tiers_without_platform:
            tier.platform_type = "PATREON"
            updated_count += 1
            
        # Get any Ko-fi tiers that might be misclassified (checking title)
        potential_kofi_tiers = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == current_user.id,
                CampaignTier.platform_type != "KOFI",
                func.lower(CampaignTier.title).like("%kofi%")
            )
        ).all()
        
        # Update these to be Ko-fi tiers
        for tier in potential_kofi_tiers:
            tier.platform_type = "KOFI"
            updated_count += 1
            
        # Commit the changes
        if updated_count > 0:
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