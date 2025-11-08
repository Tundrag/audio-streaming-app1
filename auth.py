import os
import hmac
import hashlib
import httpx
from typing import Optional, Dict, List
import logging
import json
from typing import Optional, Dict, List
from patreon_client import patreon_client
from datetime import datetime, timezone
from fastapi import (
    APIRouter, Request, HTTPException, 
    Depends, Header, Response,
    FastAPI, UploadFile, File, Form
)
from fastapi.responses import JSONResponse
from sqlalchemy import and_
from sqlalchemy.orm import Session
from fastapi.responses import JSONResponse, RedirectResponse
from models import User, UserRole, CampaignTier, UserSession
from models import User, UserRole, CampaignTier
from database import get_db

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Load environment variables
PATREON_ACCESS_TOKEN = os.getenv("PATREON_ACCESS_TOKEN")
PATREON_CAMPAIGN_ID = os.getenv("PATREON_CAMPAIGN_ID")
PATREON_WEBHOOK_SECRET = os.getenv("PATREON_WEBHOOK_SECRET")


@router.post("/verify")
async def verify_patron_and_pin(
    request: Request,
    email: str = Form(...),
    creator_pin: str = Form(...),
    db: Session = Depends(get_db)
):
    """Verify Patreon membership and creator PIN"""
    try:
        # First verify patron status
        patron_data = await patreon_client.verify_patron(email)
        if not patron_data:
            return JSONResponse({
                "success": False,
                "error": "Invalid Patreon account or inactive membership"
            })

        # Then find the creator by PIN
        creator = db.query(User).filter(
            and_(
                User.creator_pin == creator_pin,
                User.role == UserRole.CREATOR,
                User.is_active == True
            )
        ).first()
        
        if not creator:
            return JSONResponse({
                "success": False,
                "error": "Invalid creator PIN"
            })

        return JSONResponse({
            "success": True,
            "patron_data": patron_data,
            "creator_id": creator.id,
            "creator_name": creator.username
        })

    except Exception as e:
        logger.error(f"Verification error: {str(e)}")
        return JSONResponse({
            "success": False,
            "error": "An error occurred during verification"
        })
@router.get("/debug/verify-patron/{email}")
async def debug_verify_patron(email: str):
    """Debug endpoint to verify patron status"""
    try:
        patron_data = await patreon_client.verify_patron(email)
        return {
            "email": email,
            "found": patron_data is not None,
            "patron_data": patron_data if patron_data else None
        }
    except Exception as e:
        return {"error": str(e)}

@router.post("/patreon/webhook")
async def patreon_webhook(
    request: Request,
    x_patreon_event: str = Header(...),
    x_patreon_signature: str = Header(...),
    db: Session = Depends(get_db)
):
    try:
        # Verify webhook signature
        webhook_secret = os.getenv("PATREON_WEBHOOK_SECRET")
        if not webhook_secret:
            logger.error("Missing PATREON_WEBHOOK_SECRET")
            return Response(status_code=500)

        body = await request.body()
        if not verify_hmac_signature(body, x_patreon_signature, webhook_secret):
            logger.error("Invalid webhook signature")
            return Response(status_code=401)

        event_data = json.loads(body)
        logger.info(f"Received webhook event: {x_patreon_event}")

        # Only process specific important events
        important_events = {
            # Process patron deletion to deactivate users
            "members:delete": handle_member_delete,
            
            # Process pledge deletion to deactivate access
            "members:pledge:delete": lambda data, db: handle_tier_webhook("members:pledge:delete", data, db),
            
            # Only process updates that change tier/status
            "members:update": handle_significant_member_update,
            
            # Handle tier structure changes
            "tiers:create": handle_tier_create,
            "tiers:delete": handle_tier_delete,
            "tiers:update": handle_tier_update
        }

        handler = important_events.get(x_patreon_event)
        if handler:
            logger.info(f"Processing important event: {x_patreon_event}")
            await handler(event_data, db)
        else:
            logger.info(f"Skipping non-critical event: {x_patreon_event}")

        return Response(status_code=200)

    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        return Response(status_code=500)


async def handle_significant_member_update(data: dict, db: Session):
    """Only handle member updates that have significant changes"""
    try:
        attributes = data.get("data", {}).get("attributes", {})
        old_tier_data = {}
        
        # Get user
        patron_id = data.get("data", {}).get("id")
        if not patron_id:
            return
            
        user = db.query(User).filter(User.patreon_id == patron_id).first()
        if not user:
            return

        old_tier_data = user.patreon_tier_data or {}
        
        # Check for significant changes
        significant_changes = (
            attributes.get("patron_status") != old_tier_data.get("patron_status") or
            attributes.get("last_charge_status") != old_tier_data.get("last_charge_status") or
            _has_tier_changed(data, old_tier_data)
        )

        if significant_changes:
            logger.info(f"Processing significant update for patron {user.email}")
            await handle_member_update(data, db)
        else:
            logger.info(f"Skipping non-significant update for patron {user.email}")

    except Exception as e:
        logger.error(f"Error checking member update significance: {str(e)}")


def _has_tier_changed(webhook_data: dict, old_tier_data: dict) -> bool:
    """Check if the patron's tier has changed"""
    try:
        # Get new tier info
        new_tier = None
        for included in webhook_data.get("included", []):
            if included.get("type") == "tier":
                new_tier = {
                    "title": included.get("attributes", {}).get("title"),
                    "amount_cents": included.get("attributes", {}).get("amount_cents")
                }
                break

        return (
            new_tier and (
                new_tier.get("title") != old_tier_data.get("title") or
                new_tier.get("amount_cents") != old_tier_data.get("amount_cents")
            )
        )
    except Exception:
        return False


async def handle_member_create(data: dict, db: Session):
    """Handle new member creation from Patreon webhook"""
    try:
        # Extract member data
        member_data = data.get("data", {})
        attributes = member_data.get("attributes", {})
        relationships = member_data.get("relationships", {})
        
        # Get patron info
        patron_email = attributes.get("email")
        if not patron_email:
            logger.error("No email found in member data")
            return

        # Get campaign/creator info
        campaign = relationships.get("campaign", {}).get("data", {})
        campaign_id = campaign.get("id")
        
        if not campaign_id:
            logger.error("No campaign ID found in member data")
            return

        # Find the creator for this campaign
        creator = db.query(User).filter(
            and_(
                User.role == UserRole.CREATOR,
                User.campaign_id == campaign_id
            )
        ).first()

        if not creator:
            logger.error(f"No creator found for campaign {campaign_id}")
            return
            
        logger.info(f"Processing new member {patron_email} for creator {creator.username}")

        # Check patron status
        patron_status = attributes.get("patron_status")
        if patron_status != "active_patron":
            logger.info(f"New member {patron_email} is not an active patron (status: {patron_status})")
            return

        # Get tier info
        tier_info = None
        tiers = relationships.get("currently_entitled_tiers", {}).get("data", [])
        if tiers:
            tier_id = tiers[0].get("id")  # Get first tier
            
            # Find tier details from included data
            for included in data.get("included", []):
                if included.get("id") == tier_id and included.get("type") == "tier":
                    tier_info = {
                        "title": included.get("attributes", {}).get("title", "Patron"),
                        "amount_cents": included.get("attributes", {}).get("amount_cents", 0)
                    }
                    break

        # Create or update patron user
        existing_user = db.query(User).filter(
            and_(
                User.email == patron_email,
                User.created_by == creator.id
            )
        ).first()

        if existing_user:
            logger.info(f"Updating existing user {patron_email}")
            existing_user.patreon_id = member_data.get("id")
            existing_user.is_active = True
            existing_user.role = UserRole.PATREON
            existing_user.patreon_tier_data = {
                "amount_cents": attributes.get("currently_entitled_amount_cents", 0),
                "patron_status": patron_status,
                "last_charge_status": attributes.get("last_charge_status"),
                "title": tier_info["title"] if tier_info else "Patron",
                "downloads_allowed": 0,  # Will be updated by sync service
                "downloads_used": 0
            }
            existing_user.updated_at = datetime.now(timezone.utc)
        else:
            logger.info(f"Creating new user for {patron_email}")
            new_user = User(
                email=patron_email,
                username=attributes.get("full_name") or patron_email.split('@')[0],
                role=UserRole.PATREON,
                patreon_id=member_data.get("id"),
                patreon_tier_data={
                    "amount_cents": attributes.get("currently_entitled_amount_cents", 0),
                    "patron_status": patron_status,
                    "last_charge_status": attributes.get("last_charge_status"),
                    "title": tier_info["title"] if tier_info else "Patron",
                    "downloads_allowed": 0,  # Will be updated by sync service
                    "downloads_used": 0
                },
                created_by=creator.id,
                is_active=True,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            db.add(new_user)

        # Commit changes
        db.commit()
        logger.info(f"Successfully processed new member {patron_email}")

        # Trigger download settings sync
        # Import here to avoid circular imports
        from sync_service import PatreonSyncService
        sync_service = PatreonSyncService(db)
        await sync_service.sync_patron_downloads(creator.id)

    except Exception as e:
        db.rollback()
        logger.error(f"Error processing new member: {str(e)}")
        raise
def verify_hmac_signature(payload: bytes, signature: str, webhook_secret: str) -> bool:
    """
    Verify Patreon webhook signature using MD5 HMAC.
    https://docs.patreon.com/#webhooks
    """
    try:
        # Log incoming data
        logger.info(f"Raw signature from Patreon: {signature}")
        logger.info(f"Webhook secret: {webhook_secret[:5]}...")  # Log first 5 chars
        
        # Create test payload to verify our calculation
        test_payload = payload.decode('utf-8')[:50]  # First 50 chars
        logger.info(f"Test payload snippet: {test_payload}")
        
        # Try different secret encodings
        secret_utf8 = webhook_secret.encode('utf-8')
        
        # Calculate with UTF-8 secret
        hmac_utf8 = hmac.new(
            secret_utf8,
            payload,
            hashlib.md5
        ).hexdigest()
        
        # Try raw secret without encoding
        hmac_raw = hmac.new(
            webhook_secret.encode(),
            payload,
            hashlib.md5
        ).hexdigest()
        
        # Log all attempts
        logger.info(f"UTF-8 secret signature: {hmac_utf8}")
        logger.info(f"Raw secret signature: {hmac_raw}")
        logger.info(f"Patreon signature: {signature}")
        
        # Try both comparisons
        match_utf8 = hmac.compare_digest(hmac_utf8.lower(), signature.lower())
        match_raw = hmac.compare_digest(hmac_raw.lower(), signature.lower())
        
        logger.info(f"UTF-8 match: {match_utf8}")
        logger.info(f"Raw match: {match_raw}")

        return match_utf8 or match_raw

    except Exception as e:
        logger.error(f"Signature verification error: {str(e)}")
        return False
        
async def handle_tier_create(data: dict, db: Session):
    """Handle tier creation event"""
    try:
        attributes = data.get("data", {}).get("attributes", {})
        relationships = data.get("data", {}).get("relationships", {})
        campaign = relationships.get("campaign", {}).get("data", {})
        
        if not campaign or not attributes:
            logger.error("Missing required data in tier creation webhook")
            return

        creator = db.query(User).filter(
            and_(
                User.role == UserRole.CREATOR,
                User.campaign_id == campaign.get("id")
            )
        ).first()

        if not creator:
            logger.error(f"No creator found for campaign {campaign.get('id')}")
            return

        # Check if tier already exists
        existing_tier = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == creator.id,
                CampaignTier.title == attributes.get("title")
            )
        ).first()

        if existing_tier:
            # Update existing tier
            existing_tier.amount_cents = attributes.get("amount_cents", existing_tier.amount_cents)
            existing_tier.description = attributes.get("description", existing_tier.description)
            existing_tier.patron_count = attributes.get("patron_count", existing_tier.patron_count)
            existing_tier.is_active = True
            existing_tier.updated_at = datetime.now(timezone.utc)
            logger.info(f"Updated existing tier: {existing_tier.title}")
        else:
            # Create new tier
            new_tier = CampaignTier(
                creator_id=creator.id,
                title=attributes.get("title"),
                description=attributes.get("description", ""),
                amount_cents=attributes.get("amount_cents", 0),
                patron_count=attributes.get("patron_count", 0),
                is_active=True,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            db.add(new_tier)
            logger.info(f"Created new tier: {new_tier.title}")

        db.commit()

    except Exception as e:
        logger.error(f"Error handling tier creation: {str(e)}")
        db.rollback()
        
async def handle_tier_update(data: dict, db: Session):
    """Handle tier update event"""
    try:
        tier_id = data.get("data", {}).get("id")
        attributes = data.get("data", {}).get("attributes", {})
        
        if not tier_id or not attributes:
            logger.error("Missing required data in tier update webhook")
            return

        tier = db.query(CampaignTier).filter(
            CampaignTier.patreon_tier_id == tier_id
        ).first()

        if not tier:
            logger.warning(f"Tier not found for update: {tier_id}")
            return

        tier.title = attributes.get("title", tier.title)
        tier.description = attributes.get("description", tier.description)
        tier.amount_cents = attributes.get("amount_cents", tier.amount_cents)
        tier.benefits = attributes.get("benefits", tier.benefits)
        tier.position = attributes.get("position", tier.position)
        tier.updated_at = datetime.now(timezone.utc)

        db.commit()
        logger.info(f"Updated tier: {tier.title}")

    except Exception as e:
        logger.error(f"Error handling tier update: {str(e)}")
        db.rollback()
        
async def handle_member_update(data: dict, db: Session):
    """Handle member update webhook using data directly from the payload"""
    try:
        member_data = data.get("data", {})
        attributes = member_data.get("attributes", {})
        patron_id = member_data.get("id")
        
        if not patron_id:
            logger.error("No patron ID in webhook data")
            return

        # Get tier info from included data
        tier_info = {}
        for included in data.get("included", []):
            if included.get("type") == "tier":
                tier_info = {
                    "title": included.get("attributes", {}).get("title"),
                    "amount_cents": included.get("attributes", {}).get("amount_cents")
                }
                break

        user = db.query(User).filter(User.patreon_id == patron_id).first()
        if user:
            # Preserve existing download data
            current_tier_data = user.patreon_tier_data or {}
            
            # Update tier data while keeping download counts
            updated_tier_data = {
                "title": tier_info.get("title", current_tier_data.get("title")),
                "amount_cents": tier_info.get("amount_cents", current_tier_data.get("amount_cents")),
                "patron_status": attributes.get("patron_status"),
                "last_charge_status": attributes.get("last_charge_status"),
                "downloads_allowed": current_tier_data.get("downloads_allowed", 0),
                "downloads_used": current_tier_data.get("downloads_used", 0),
                "period_start": current_tier_data.get("period_start")
            }

            user.patreon_tier_data = updated_tier_data
            user.updated_at = datetime.now(timezone.utc)
            user.is_active = attributes.get("patron_status") == "active_patron"
            
            db.commit()
            logger.info(f"Updated patron {user.email} with webhook data")
        else:
            logger.warning(f"Received update for unknown patron ID: {patron_id}")

    except Exception as e:
        logger.error(f"Error handling member update: {str(e)}")
        db.rollback()

async def handle_tier_delete(data: dict, db: Session):
    """Handle tier deletion event"""
    try:
        tier_id = data.get("data", {}).get("id")
        if not tier_id:
            logger.error("Missing tier ID in deletion webhook")
            return

        tier = db.query(CampaignTier).filter(
            CampaignTier.patreon_tier_id == tier_id
        ).first()

        if not tier:
            logger.warning(f"Tier not found for deletion: {tier_id}")
            return

        # Soft delete the tier
        tier.is_active = False
        tier.updated_at = datetime.now(timezone.utc)
        db.commit()
        
        # Remove tier from album restrictions
        await remove_tier_from_albums(tier.title, tier.creator_id)
        
        logger.info(f"Marked tier as inactive: {tier.title}")

    except Exception as e:
        logger.error(f"Error handling tier deletion: {str(e)}")
        
        db.rollback()

async def handle_tier_patron_count_update(data: dict, db: Session):
    """Handle tier patron count update event"""
    try:
        tier_id = data.get("data", {}).get("id")
        attributes = data.get("data", {}).get("attributes", {})
        patron_count = attributes.get("patron_count", 0)

        if not tier_id:
            logger.error("Missing tier ID in patron count update webhook")
            return

        tier = db.query(CampaignTier).filter(
            CampaignTier.patreon_tier_id == tier_id
        ).first()

        if not tier:
            logger.warning(f"Tier not found for patron count update: {tier_id}")
            return

        tier.patron_count = patron_count
        tier.updated_at = datetime.now(timezone.utc)
        db.commit()
        
        logger.info(f"Updated patron count for tier {tier.title}: {patron_count}")

    except Exception as e:
        logger.error(f"Error handling patron count update: {str(e)}")
        db.rollback()

async def handle_member_delete(data: dict, db: Session):
    """
    Handle member deletion webhook from Patreon
    - Deactivates user
    - Clears tier data
    - Removes active sessions
    - Handles user data cleanup
    """
    try:
        # Extract member data
        member_data = data.get("data", {})
        attributes = member_data.get("attributes", {})
        relationships = member_data.get("relationships", {})

        # Get patron ID and email 
        patron_id = member_data.get("id")
        patron_email = attributes.get("email")

        if not patron_id and not patron_email:
            logger.error("No patron identification in webhook data")
            return

        # Find user(s) by either patron ID or email
        users = []
        if patron_id:
            user = db.query(User).filter(User.patreon_id == patron_id).first()
            if user:
                users.append(user)

        if patron_email and not users:
            users = db.query(User).filter(User.email == patron_email).all()

        if not users:
            logger.warning(f"No users found for deletion. Patron ID: {patron_id}, Email: {patron_email}")
            return

        for user in users:
            logger.info(f"Processing deletion for user: {user.email}")

            # Clear sensitive user data
            user.patreon_id = None
            user.patreon_tier_data = None
            user.is_active = False
            user.updated_at = datetime.now(timezone.utc)

            # Remove all active sessions
            active_sessions = db.query(UserSession).filter(
                and_(
                    UserSession.user_id == user.id,
                    UserSession.is_active == True
                )
            ).all()

            for session in active_sessions:
                session.is_active = False
                session.updated_at = datetime.now(timezone.utc)
                logger.info(f"Deactivated session {session.session_id} for user {user.email}")

            # Clear download progress data
            db.query(PlaybackProgress).filter(
                PlaybackProgress.user_id == user.id
            ).delete(synchronize_session=False)

            # Clear any album management records
            db.query(UserAlbumManagement).filter(
                UserAlbumManagement.user_id == user.id
            ).delete(synchronize_session=False)

            # Add audit log entry
            # Log the deletion
            logger.info(f"Deactivated user {user.email} and cleaned up associated data")

        # Commit all changes
        db.commit()

        # Create one final log summary
        logger.info(f"Successfully processed deletion for {len(users)} users")

    except Exception as e:
        db.rollback()
        logger.error(f"Error handling member deletion: {str(e)}")
        raise


async def handle_pledge_create(data: dict, db: Session):
    """Handle pledge creation event"""
    try:
        patron_id = data.get("data", {}).get("relationships", {}).get("patron", {}).get("data", {}).get("id")
        if not patron_id:
            logger.error("No patron ID in webhook data")
            return

        user = db.query(User).filter(User.patreon_id == patron_id).first()
        if user:
            # Get updated patron data including tier
            patron_data = await patreon_client.verify_patron(user.email)
            if patron_data:
                user.patreon_tier_data = patron_data["tier_data"]
                user.updated_at = datetime.now(timezone.utc)
                db.commit()
                logger.info(f"Updated pledge for patron: {user.email}")
        else:
            logger.warning(f"Received pledge for unknown patron ID: {patron_id}")

    except Exception as e:
        logger.error(f"Error handling pledge create: {str(e)}")
        db.rollback()

async def login_required(
    request: Request,
    db: Session = Depends(get_db)
) -> User:
    """Enhanced login verification with better session handling"""
    try:
        # Check for session_id cookie
        session_id = request.cookies.get("session_id")
        if not session_id:
            logger.warning("No session ID found in cookies")
            if request.url.path.startswith('/api/'):
                raise HTTPException(status_code=401, detail="Authentication required")
            else:
                raise HTTPException(
                    status_code=303, 
                    detail="Redirect to login",
                    headers={"Location": "/login"}
                )

        # Verify session exists and is active
        session = db.query(UserSession).filter(
            and_(
                UserSession.session_id == session_id,
                UserSession.is_active == True,
                UserSession.expires_at > datetime.now(timezone.utc)
            )
        ).first()

        if not session:
            logger.warning(f"No active session found for ID: {session_id}")
            # No need to clear request.session - we're using PostgreSQL sessions
            if request.url.path.startswith('/api/'):
                raise HTTPException(status_code=401, detail="Session expired")
            else:
                raise HTTPException(
                    status_code=303,
                    detail="Redirect to login",
                    headers={"Location": "/login"}
                )

        # Get user
        user = db.query(User).filter(User.id == session.user_id).first()
        if not user or not user.is_active:
            logger.warning(f"No active user found for session: {session_id}")
            # No need to clear request.session - we're using PostgreSQL sessions
            if request.url.path.startswith('/api/'):
                raise HTTPException(status_code=401, detail="User not found or inactive")
            else:
                raise HTTPException(
                    status_code=303,
                    detail="Redirect to login",
                    headers={"Location": "/login"}
                )

        # Store user in request state for middleware access
        request.state.user = user

        # Update session activity
        session.last_active = datetime.now(timezone.utc)
        if user.is_creator:
            session.extend_session(hours=48)

        # Session data is already in PostgreSQL (UserSession.session_data)
        # No need to update request.session - it doesn't exist anymore

        db.commit()
        return user

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Session verification error: {str(e)}", exc_info=True)
        if request.url.path.startswith('/api/'):
            raise HTTPException(status_code=500, detail="Internal server error")
        else:
            raise HTTPException(
                status_code=303,
                detail="Redirect to login",
                headers={"Location": "/login"}
            )


async def handle_tier_webhook(event_type: str, data: dict, db: Session):
    """Handle Patreon tier-related webhooks"""
    try:
        attributes = data.get("data", {}).get("attributes", {})
        tier_data = {
            "title": attributes.get("title"),
            "amount_cents": attributes.get("amount_cents"),
            "description": attributes.get("description"),
            "patron_status": attributes.get("patron_status"),
            "last_charge_status": attributes.get("last_charge_status")
        }
        
        # Get the campaign/creator information
        relationships = data.get("data", {}).get("relationships", {})
        campaign = relationships.get("campaign", {}).get("data", {})
        campaign_id = campaign.get("id")
        
        if not campaign_id:
            logger.error("No campaign ID in webhook data")
            return
            
        # Find the creator for this campaign
        creator = db.query(User).filter(
            and_(
                User.role == UserRole.CREATOR,
                User.patreon_id == campaign_id
            )
        ).first()
        
        if not creator:
            logger.error(f"No creator found for campaign {campaign_id}")
            return

        # Update patron tier data
        if event_type == "members:pledge:create" or event_type == "members:pledge:update":
            patron_email = attributes.get("email")
            if patron_email:
                patron = db.query(User).filter(
                    and_(
                        User.email == patron_email,
                        User.role == UserRole.PATREON,
                        User.created_by == creator.id
                    )
                ).first()
                
                if patron:
                    patron.patreon_tier_data = tier_data
                    db.commit()
                    logger.info(f"Updated tier data for patron: {patron_email}")

        # Handle tier deletion
        elif event_type == "members:pledge:delete":
            patron_email = attributes.get("email")
            if patron_email:
                patron = db.query(User).filter(
                    and_(
                        User.email == patron_email,
                        User.role == UserRole.PATREON,
                        User.created_by == creator.id
                    )
                ).first()
                
                if patron:
                    # Update tier data to show inactive status
                    tier_data["patron_status"] = "inactive"
                    patron.patreon_tier_data = tier_data
                    db.commit()
                    logger.info(f"Marked tier as inactive for patron: {patron_email}")
                    
                    # Update any album restrictions that used this tier
                    await remove_tier_from_albums(patron.patreon_tier_data.get("title"), creator.id)

    except Exception as e:
        logger.error(f"Error handling tier webhook: {str(e)}")
        db.rollback()
async def remove_tier_from_albums(tier_title: str, creator_id: int):
    """Remove a tier from all albums when it's deleted"""
    try:
        albums = load_creator_albums(creator_id)
        updated = False
        
        for album in albums:
            restrictions = album.get("tier_restrictions", {})
            allowed_tiers = restrictions.get("allowed_tiers", [])
            
            if tier_title in allowed_tiers:
                allowed_tiers.remove(tier_title)
                restrictions["allowed_tiers"] = allowed_tiers
                album["tier_restrictions"] = restrictions
                updated = True
        
        if updated:
            save_creator_albums(creator_id, albums)
            logger.info(f"Removed tier {tier_title} from album restrictions")
            
    except Exception as e:
        logger.error(f"Error removing tier from albums: {str(e)}")


