from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from models import User, DiscordSettings
from typing import Dict
from auth import login_required
from database import get_db
from discord_integration import discord
import logging

# Initialize router
router = APIRouter(prefix="/api/creator/discord", tags=["discord"])
logger = logging.getLogger(__name__)

@router.get("/settings")
async def get_discord_settings(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get Discord integration settings from the database"""
    # Ensure user is a creator
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Only creators can access this endpoint")
    
    # Get settings from database
    settings = db.query(DiscordSettings).filter(DiscordSettings.creator_id == current_user.id).first()
    
    # If no settings exist yet, return empty values
    if not settings:
        return {
            "webhook_url": "",
            "webhook_id": "",
            "webhook_token": "",
            "bot_token": "",
            "base_url": "",
            "has_bot_token": False,
            "is_active": False,
            "last_synced": None,
            "sync_message_ids": []
        }
    
    # Return complete settings
    return {
        "webhook_url": settings.webhook_url or "",
        "webhook_id": settings.webhook_id or "",
        "webhook_token": settings.webhook_token or "",
        "bot_token": settings.bot_token or "",
        "base_url": settings.base_url or "",
        "has_bot_token": bool(settings.bot_token),
        "is_active": settings.is_active,
        "last_synced": settings.last_synced.isoformat() if settings.last_synced else None,
        "sync_message_ids": settings.sync_message_ids or []
    }

@router.post("/settings")
async def save_discord_settings(
    settings: Dict,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Save Discord integration settings to the database"""
    # Ensure user is a creator
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Only creators can access this endpoint")
    
    try:
        webhook_url = settings.get("webhook_url", "").strip()
        bot_token = settings.get("bot_token", "").strip()
        base_url = settings.get("base_url", "").strip()
        
        # Get existing settings or create new
        db_settings = db.query(DiscordSettings).filter(DiscordSettings.creator_id == current_user.id).first()
        
        if not db_settings:
            # Create new settings record
            db_settings = DiscordSettings(creator_id=current_user.id)
            db.add(db_settings)
        
        # Update settings
        db_settings.webhook_url = webhook_url
        db_settings.bot_token = bot_token
        db_settings.base_url = base_url
        
        # Parse webhook URL to extract ID and token
        if webhook_url:
            try:
                parts = webhook_url.strip('/').split('/')
                if len(parts) >= 2:
                    db_settings.webhook_id = parts[-2]
                    db_settings.webhook_token = parts[-1]
            except Exception as e:
                logger.error(f"Error parsing webhook URL: {str(e)}")
        
        # Set active status based on webhook URL
        db_settings.is_active = bool(webhook_url)
        
        # Commit changes
        db.commit()
        
        # Also update the Discord integration instance if needed
        if hasattr(discord, 'creator_id') and discord.creator_id == current_user.id:
            discord.webhook_url = webhook_url
            discord.webhook_id = db_settings.webhook_id
            discord.webhook_token = db_settings.webhook_token
            discord.bot_token = bot_token
            discord.base_url = base_url
            discord.initialized = bool(webhook_url)
            
        return {"status": "success", "message": "Discord settings saved successfully"}
    except Exception as e:
        logger.error(f"Error saving Discord settings: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error saving settings: {str(e)}")

@router.post("/sync")
async def sync_discord_albums(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Manually sync albums to Discord"""
    # Ensure user is a creator or team member
    if not (current_user.is_creator or current_user.is_team):
        raise HTTPException(status_code=403, detail="Only creators and team members can access this endpoint")
    
    try:
        # Get creator ID (if team member, use their creator's ID)
        creator_id = current_user.id if current_user.is_creator else current_user.created_by
        
        if not creator_id:
            raise HTTPException(status_code=400, detail="Could not determine creator ID")
        
        # Initialize Discord integration with settings from DB
        await discord.initialize(db, creator_id)
        
        if not discord.initialized:
            raise HTTPException(status_code=400, detail="Discord integration not configured. Please set up webhook URL first.")

        # Run the sync in a background task to avoid timeouts with large album collections
        # Create wrapper to ensure background task has its own session
        async def _sync_with_new_session():
            from database import SessionLocal
            db = SessionLocal()
            try:
                await discord.sync_album_list(db)
            finally:
                db.close()

        background_tasks.add_task(_sync_with_new_session)

        return {
            "status": "success",
            "message": "Album sync has been started. Previous Discord messages will be cleaned up and replaced with the latest album information."
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting Discord sync: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error syncing albums: {str(e)}")

@router.post("/cleanup")
async def cleanup_discord_messages(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Manually clean up all Discord messages and reset tracking"""
    # Ensure user is a creator or team member
    if not (current_user.is_creator or current_user.is_team):
        raise HTTPException(status_code=403, detail="Only creators and team members can access this endpoint")
    
    try:
        # Get creator ID (if team member, use their creator's ID)
        creator_id = current_user.id if current_user.is_creator else current_user.created_by
        
        if not creator_id:
            raise HTTPException(status_code=400, detail="Could not determine creator ID")
        
        # Initialize Discord integration with settings from DB
        await discord.initialize(db, creator_id)
        
        if not discord.initialized:
            raise HTTPException(status_code=400, detail="Discord integration not configured. Please set up webhook URL first.")
            
        # Delete existing tracked messages
        success = await discord.clean_old_sync_messages(db)
        
        # Also clear the tracking database to ensure clean slate
        discord._save_sync_message_ids(db, [])
        
        return {"status": "success", "message": "Discord messages have been deleted and tracking reset"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cleaning up Discord messages: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error cleaning up: {str(e)}")