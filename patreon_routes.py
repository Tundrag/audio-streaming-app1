from fastapi import APIRouter, HTTPException, Depends, Request
from typing import Dict, Optional
import os
import httpx
from dotenv import load_dotenv
from pathlib import Path
from datetime import datetime, timezone, timedelta
import logging
from pydantic import BaseModel
from sqlalchemy.orm import Session
from database import get_db
from models import User, Campaign, CampaignTier, UserTier
from auth import login_required
from uuid import UUID
from patreon_client import patreon_client
from functools import lru_cache
from sync.sync_service import PatreonSyncService
import asyncio
from models import Campaign

logger = logging.getLogger(__name__)
router = APIRouter()

# Model for Patreon settings
class PatreonSettings(BaseModel):
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    campaign_id: Optional[str] = None
    webhook_secret: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None

# Global variables for token refresh tracking
_last_refresh = None
_refresh_cooldown = timedelta(seconds=30)

# Dependency for sync service
sync_service_instance = None

async def initialize_sync_service() -> PatreonSyncService:
    """Initialize a new sync service instance"""
    from database import SessionLocal
    sync_service = PatreonSyncService(db_factory=SessionLocal)
    await sync_service.initialize(enabled=True)
    return sync_service

# Store sync service instance
_sync_service = None

async def get_sync_service(db: Session = Depends(get_db)) -> PatreonSyncService:
    """Get or create PatreonSyncService instance"""
    global _sync_service
    if _sync_service is None:
        _sync_service = await initialize_sync_service()
    return _sync_service

@router.on_event("startup")
async def startup_event():
    """Initialize sync service on application startup"""
    global _sync_service
    if _sync_service is None:
        _sync_service = await initialize_sync_service()

@router.on_event("shutdown")
async def shutdown_event():
    """Cleanup sync service on application shutdown"""
    global _sync_service
    if _sync_service:
        await _sync_service.stop_periodic_task()
        _sync_service = None

async def delete_campaign_and_data(
    campaign_id: UUID,
    creator_id: int,
    db: Session
) -> dict:
    """Delete campaign and all associated data"""
    try:
        # Get campaign with its relationships
        campaign = (
            db.query(Campaign)
            .filter(
                Campaign.id == campaign_id,
                Campaign.creator_id == creator_id
            )
            .first()
        )
        
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        
        # Check if this is the only active campaign
        if campaign.is_primary and campaign.is_active:
            active_count = (
                db.query(Campaign)
                .filter(
                    Campaign.creator_id == creator_id,
                    Campaign.is_active == True
                )
                .count()
            )
            
            if active_count == 1:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot delete the only active campaign"
                )

        # Get counts before deletion for reporting
        tier_count = (
            db.query(CampaignTier)
            .filter(CampaignTier.campaign_id == campaign_id)
            .count()
        )
            
        patron_count = (
            db.query(User)
            .filter(
                User.campaign_id == campaign.id,  # now using campaign.id
                User.role == "PATREON"
            )
            .count()
        )

        # Delete campaign – cascading should handle tiers
        db.delete(campaign)
        db.commit()
        
        return {
            "status": "success",
            "message": f"Campaign '{campaign.name}' deleted successfully",
            "details": {
                "campaign_id": str(campaign.id),
                "patreon_campaign_id": campaign.id,  # return same value as campaign.id
                "tiers_deleted": tier_count,
                "patrons_removed": patron_count
            }
        }
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting campaign: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/creator/patreon/settings")
async def get_patreon_settings_from_db(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get current Patreon settings from database"""
    try:
        campaign = (
            db.query(Campaign)
            .filter(
                Campaign.creator_id == current_user.id,
                Campaign.is_active == True,
                Campaign.is_primary == True
            )
            .first()
        )
        
        if not campaign:
            return {
                "access_token": "",
                "refresh_token": "",
                "campaign_id": "",
                "webhook_secret": "",
                "client_id": "",
                "client_secret": ""
            }
            
        settings = {
            "access_token": campaign.access_token or "",
            "refresh_token": campaign.refresh_token or "",
            "campaign_id": campaign.id or "",  # updated here
            "webhook_secret": campaign.webhook_secret or "",
            "client_id": campaign.client_id or "",
            "client_secret": campaign.client_secret or ""
        }
        
        return settings
        
    except Exception as e:
        logger.error(f"Error getting Patreon settings from db: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/creator/patreon/env-settings")
async def get_patreon_settings_from_env():
    """Get current Patreon settings from .env"""
    try:
        # Force reload of .env to ensure latest values
        load_dotenv(override=True)
        
        settings = {
            "access_token": os.getenv("PATREON_ACCESS_TOKEN", ""),
            "refresh_token": os.getenv("PATREON_REFRESH_TOKEN", ""),
            "campaign_id": os.getenv("PATREON_CAMPAIGN_ID", ""),
            "webhook_secret": os.getenv("PATREON_WEBHOOK_SECRET", ""),
            "client_id": os.getenv("PATREON_CLIENT_ID", ""),
            "client_secret": os.getenv("PATREON_CLIENT_SECRET", "")
        }
        
        # Log retrieved values for debugging (only partial values for security)
        logger.debug(
            "Retrieved Patreon settings: " +
            ", ".join(f"{k}: {v[:5]}..." if v else f"{k}: empty" for k, v in settings.items())
        )
        
        return settings
        
    except Exception as e:
        logger.error(f"Error getting Patreon settings: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/creator/patreon/settings")
async def update_patreon_settings(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Update or create Patreon campaign settings"""
    try:
        data = await request.json()
        is_new_campaign = data.pop('isNewCampaign', False)
        
        if is_new_campaign:
            # Create new campaign – assign the provided campaign id to the primary key field "id"
            campaign_name = data.pop('name', None)
            if not campaign_name:
                raise HTTPException(status_code=400, detail="Campaign name is required")
                
            campaign = Campaign(
                creator_id=current_user.id,
                name=campaign_name,
                id=data.get('campaign_id', ''),  # use provided campaign id
                access_token=data.get('access_token', ''),
                refresh_token=data.get('refresh_token', ''),
                webhook_secret=data.get('webhook_secret', ''),
                client_id=data.get('client_id', ''),
                client_secret=data.get('client_secret', ''),
                is_active=True,
                is_primary=False  # New campaigns are not primary by default
            )
            
            db.add(campaign)
            db.commit()
            db.refresh(campaign)
            
            logger.info(f"Created new campaign: {campaign_name}")
            
            return {
                "status": "success",
                "message": f"Created new campaign: {campaign_name}",
                "campaign_id": str(campaign.id),
                "campaign_name": campaign.name
            }
            
        else:
            # Update existing campaign
            campaign_db_id = data.pop('campaign_db_id', None)
            if not campaign_db_id:
                raise HTTPException(status_code=400, detail="Missing campaign_db_id")
                
            campaign = (
                db.query(Campaign)
                .filter(
                    Campaign.id == campaign_db_id,
                    Campaign.creator_id == current_user.id
                )
                .first()
            )
            
            if not campaign:
                raise HTTPException(status_code=404, detail="Campaign not found")
            
            # Update campaign settings – if the key is "campaign_id", update campaign.id instead.
            for key, value in data.items():
                if key == "campaign_id":
                    setattr(campaign, "id", value)
                elif hasattr(campaign, key):
                    setattr(campaign, key, value)
                    
            campaign.updated_at = datetime.now(timezone.utc)
            db.commit()
            
            return {
                "status": "success",
                "message": f"Updated campaign: {campaign.name}",
                "campaign_id": str(campaign.id),
                "campaign_name": campaign.name
            }
            
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating Patreon settings: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/creator/patreon/refresh-token")
async def refresh_patreon_token(full_refresh: bool = False):
    """Refresh Patreon tokens"""
    global _last_refresh
    
    try:
        load_dotenv(override=True)
        
        client_id = os.getenv("PATREON_CLIENT_ID")
        client_secret = os.getenv("PATREON_CLIENT_SECRET")
        refresh_token = os.getenv("PATREON_REFRESH_TOKEN")
        
        logger.info(f"Using refresh token from env: {refresh_token[:10]}...")
        
        if not all([client_id, client_secret, refresh_token]):
            raise ValueError("Missing required Patreon credentials")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                'https://www.patreon.com/api/oauth2/token',
                data={
                    'grant_type': 'refresh_token',
                    'refresh_token': refresh_token,
                    'client_id': client_id,
                    'client_secret': client_secret
                }
            )

            if response.status_code != 200:
                logger.error(f"Token refresh failed. Status: {response.status_code}")
                logger.error(f"Response: {response.text}")
                if response.status_code == 401:
                    return {
                        "success": False,
                        "message": "Invalid credentials. Please check your Patreon settings.",
                        "error": "invalid_credentials"
                    }
                raise HTTPException(
                    status_code=response.status_code,
                    detail="Failed to refresh Patreon token"
                )

            data = response.json()
            updates = {}

            if 'access_token' in data:
                updates['access_token'] = data['access_token']
                logger.info("Received new access token")

            if 'refresh_token' in data:
                if full_refresh:
                    updates['refresh_token'] = data['refresh_token']
                    logger.info("Full refresh: updating refresh token")
                elif data['refresh_token'] != refresh_token:
                    logger.info("New refresh token received during regular refresh")
                    updates['refresh_token'] = data['refresh_token']

            env_path = Path('.env')
            if not update_env_file(env_path, updates):
                raise Exception("Failed to update .env file")
            
            load_dotenv(override=True)
            _last_refresh = datetime.now(timezone.utc)

            return {
                "success": True,
                "message": f"{'Full' if full_refresh else 'Access token'} refresh successful",
                "expires_in": data.get('expires_in', 2592000),
                "next_refresh_allowed": (_last_refresh + _refresh_cooldown).isoformat()
            }

    except Exception as e:
        logger.error(f"Error refreshing token: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/creator/patreon/refresh-status")
async def get_refresh_status():
    """Get token refresh status"""
    try:
        load_dotenv()
        
        access_token = os.getenv("PATREON_ACCESS_TOKEN")
        refresh_token = os.getenv("PATREON_REFRESH_TOKEN")
        client_id = os.getenv("PATREON_CLIENT_ID")
        client_secret = os.getenv("PATREON_CLIENT_SECRET")

        if not all([client_id, client_secret]):
            return {
                "status": "error",
                "message": "Missing Patreon API credentials",
                "has_valid_token": False,
                "needs_refresh": True,
                "token_type": "missing_credentials"
            }

        if not refresh_token:
            return {
                "status": "error",
                "message": "Missing refresh token - please update settings",
                "has_valid_token": False,
                "needs_refresh": True,
                "token_type": "missing_refresh"
            }

        if not access_token:
            return {
                "status": "warning",
                "message": "Access token needs refresh",
                "has_valid_token": False,
                "needs_refresh": True,
                "token_type": "needs_refresh"
            }

        return {
            "status": "success",
            "message": "Tokens present",
            "has_valid_token": True,
            "needs_refresh": False,
            "token_type": "valid"
        }

    except Exception as e:
        logger.error(f"Error checking refresh status: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/creator/patreon/test-connection")
async def test_patreon_connection(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Test Patreon API connection using current active campaign"""
    try:
        if not patreon_client._current_campaign_id:
            return {
                "success": False,
                "message": "No active campaign selected - please select a campaign first",
                "details": {
                    "campaign_status": False,
                    "campaign_name": None,
                    "tier_count": 0,
                    "patron_count": 0,
                    "webhook_status": False
                }
            }

        if not patreon_client._credentials or not patreon_client._credentials.get('access_token'):
            return {
                "success": False,
                "message": "No Patreon credentials found - please sync campaign first",
                "details": {
                    "campaign_status": False,
                    "campaign_name": None,
                    "tier_count": 0,
                    "patron_count": 0,
                    "webhook_status": False
                }
            }

        tiers = await patreon_client.get_campaign_tiers()
        
        if not tiers:
            return {
                "success": False,
                "message": "Could not fetch campaign data from Patreon",
                "details": {
                    "campaign_status": False,
                    "campaign_name": patreon_client._credentials.get('name'),
                    "tier_count": 0,
                    "patron_count": 0,
                    "webhook_status": bool(patreon_client._credentials.get('webhook_secret'))
                }
            }

        patron_count = sum(tier.get('patron_count', 0) for tier in tiers)
        
        return {
            "success": True,
            "message": "Successfully connected to Patreon API",
            "details": {
                "campaign_status": True,
                "campaign_name": patreon_client._credentials.get('name', 'Unknown Campaign'),
                "tier_count": len(tiers),
                "patron_count": patron_count,
                "webhook_status": bool(patreon_client._credentials.get('webhook_secret')),
                "db_campaign_id": patreon_client._current_campaign_id,
                "patreon_campaign_id": patreon_client._credentials.get('campaign_id')
            }
        }
            
    except Exception as e:
        logger.error(f"Patreon connection test failed: {str(e)}")
        return {
            "success": False,
            "message": str(e),
            "details": {
                "campaign_status": False,
                "campaign_name": patreon_client._credentials.get('name'),
                "tier_count": 0,
                "patron_count": 0,
                "webhook_status": False,
                "error": str(e)
            }
        }


@router.get("/api/creator/patreon/campaigns")
async def get_campaigns(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get all campaigns for the creator"""
    try:
        campaigns = db.query(Campaign).filter(Campaign.creator_id == current_user.id).all()
        return {
            "campaigns": [{
                "id": str(c.id),
                "name": c.name,
                "campaign_id": c.id,  # using the new field
                "is_primary": c.is_primary,
                "is_active": c.is_active
            } for c in campaigns]
        }
    except Exception as e:
        logger.error(f"Error getting campaigns: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/creator/patreon/campaigns/{db_campaign_id}")
async def delete_campaign(
    db_campaign_id: str,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Delete a campaign endpoint"""
    if not current_user.is_creator:
        raise HTTPException(
            status_code=403,
            detail="Only creators can delete campaigns"
        )
        
    result = await delete_campaign_and_data(db_campaign_id, current_user.id, db)
    return result


@router.get("/api/creator/patreon/settings/{campaign_id}")
async def get_campaign_settings(
    campaign_id: str,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get settings for a specific campaign"""
    try:
        campaign = (
            db.query(Campaign)
            .filter(
                Campaign.id == campaign_id,
                Campaign.creator_id == current_user.id
            )
            .first()
        )
        
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
            
        return {
            "access_token": campaign.access_token or "",
            "refresh_token": campaign.refresh_token or "",
            "campaign_id": campaign.id or "",  # updated here
            "webhook_secret": campaign.webhook_secret or "",
            "client_id": campaign.client_id or "",
            "client_secret": campaign.client_secret or ""
        }
        
    except Exception as e:
        logger.error(f"Error getting campaign settings: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/creator/patreon/sync-campaigns")
async def sync_patreon_campaigns(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    try:
        raw_data = await request.json()
        db_campaign_id = raw_data.get('db_campaign_id')
        logger.info(f"Syncing campaign with DB ID: {db_campaign_id}")
        
        # Get campaign from database
        db_campaign = db.query(Campaign).filter(
            Campaign.id == db_campaign_id,
            Campaign.creator_id == current_user.id
        ).first()

        if not db_campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        # Switch to the specified campaign first
        if not await patreon_client.switch_campaign(db, db_campaign_id):
            raise HTTPException(status_code=400, detail="Failed to switch to campaign")

        # Make request to Patreon API using active campaign credentials
        headers = {
            "Authorization": f"Bearer {patreon_client._credentials['access_token']}",
            "Accept": "application/json"
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://www.patreon.com/api/oauth2/v2/campaigns",
                headers=headers,
                params={
                    "include": "creator",
                    "fields[campaign]": "creation_name",
                    "fields[user]": "full_name"
                }
            )
            
            if response.status_code != 200:
                logger.error(f"Patreon API error: {response.text}")
                raise HTTPException(
                    status_code=502,
                    detail=f"Patreon API error: {response.text}"
                )

            patreon_data = response.json()
            
            if not patreon_data.get('data'):
                raise HTTPException(status_code=404, detail="No campaigns found in Patreon account")

            # Get campaign and creator info
            campaign_data = patreon_data['data'][0]
            creator_data = next((user for user in patreon_data.get('included', []) 
                          if user['type'] == 'user'), None)

            # Store previous values for response
            previous_campaign_id = db_campaign.id
            previous_name = db_campaign.name

            # Update campaign in database – now updating the id field
            db_campaign.id = campaign_data['id']
            db_campaign.name = (creator_data['attributes']['full_name'] 
                                if creator_data 
                                else campaign_data['attributes'].get('creation_name', 'Unknown Campaign'))
            db_campaign.updated_at = datetime.now(timezone.utc)
            
            # Set this campaign as primary (this will also handle switching the PatreonClient)
            if not await patreon_client.set_campaign_as_primary(db, db_campaign_id):
                raise HTTPException(status_code=500, detail="Failed to set campaign as primary")

            # Get sync service and trigger sync
            sync_service = await get_sync_service(db)
            if sync_service:
                logger.info("Triggering sync worker for campaign update")
                await sync_service.perform_manual_sync(current_user.id, db)
            
            return {
                "status": "success",
                "message": f"Campaign synced and set as primary: {db_campaign.name}",
                "new_campaign_id": campaign_data['id'],
                "previous_campaign_id": previous_campaign_id,
                "previous_name": previous_name,
                "new_name": db_campaign.name,
                "is_primary": True
            }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Campaign sync failed: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to synchronize campaign data")


@router.get("/api/creator/patreon/client-status")
async def get_client_status():
    """Get current PatreonClient status"""
    return patreon_client.current_status
