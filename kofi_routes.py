# kofi_routes.py
import logging
import json
import time
import random
import uuid
from datetime import datetime, timezone
import logging
import json
import httpx
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta
from typing import Optional, Dict, Tuple
from fastapi import APIRouter, Request, Depends, Form, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from datetime import datetime, timezone, timedelta
from models import User, UserRole, CampaignTier, Campaign, KofiSettings, KofiWebhook
from typing import Optional, Dict, Tuple
from calendar import monthrange
from fastapi.responses import JSONResponse
from auth import login_required
import json
from guest_trial_routes import check_trial_user_login
from database import get_db

logger = logging.getLogger(__name__)

# Create router
router = APIRouter(
    prefix="/api/kofi",
    tags=["kofi"],
    responses={404: {"description": "Not found"}}
)

# Helper functions for Ko-fi processing
async def find_matching_tier(email: str, creator_id: int, tier_name: str, amount_cents: int, 
                           is_subscription: bool, donation_amount: int, db: Session) -> CampaignTier:
    """
    Find the matching campaign tier based on comprehensive criteria:
    1. For subscription only: Match by name first, then by amount
    2. For subscription + donation: Match by total amount
    3. For donation only: Match by donation amount
    Always returns the highest qualifying tier.
    """
    logger.info(f"Finding tier for {email}: tier_name={tier_name}, sub_amount={amount_cents}, "
                f"donation={donation_amount}, is_sub={is_subscription}")
    
    # Get all active tiers for this creator - ONLY KO-FI PLATFORM TIERS
    all_tiers = db.query(CampaignTier).filter(
        and_(
            CampaignTier.creator_id == creator_id,
            CampaignTier.is_active == True,
            CampaignTier.platform_type == "KOFI"  # Add platform filter
        )
    ).order_by(CampaignTier.amount_cents.desc()).all()
    
    if not all_tiers:
        logger.warning(f"No Ko-fi tiers found for creator {creator_id} - will create default tier")
        return None
        
    logger.info(f"Found {len(all_tiers)} available Ko-fi tiers")
    
    # Calculate total contribution amount
    total_amount = amount_cents
    if donation_amount > 0:
        total_amount += donation_amount
        logger.info(f"Total contribution amount: {total_amount} cents")
    
    # SUBSCRIPTION ONLY - try name match first
    if is_subscription and donation_amount == 0:
        # Try exact name match first (case-insensitive)
        for tier in all_tiers:
            if tier.title.lower() == tier_name.lower():
                logger.info(f"Found exact name match: {tier.title}")
                return tier
                
        # If exact name failed, try partial name match
        for tier in all_tiers:
            if tier_name.lower() in tier.title.lower() or tier.title.lower() in tier_name.lower():
                logger.info(f"Found partial name match: {tier.title}")
                return tier
    
    # For ALL CASES - try amount-based matching
    # Find highest tier where amount qualifies
    qualifying_tier = None
    for tier in all_tiers:
        if total_amount >= tier.amount_cents:
            qualifying_tier = tier
            logger.info(f"Found qualifying tier by amount: {tier.title} (requires {tier.amount_cents} cents)")
            break  # We take the highest tier since list is sorted by amount desc
    
    if qualifying_tier:
        return qualifying_tier
        
    # If nothing else worked and it's "Ko-fi Supporter" - try generic fallback
    if tier_name == "Ko‑fi Supporter" or tier_name == "Ko-fi Supporter":
        for tier in all_tiers:
            if "kofi" in tier.title.lower():
                logger.info(f"Found fallback Ko-fi tier: {tier.title}")
                return tier
    
    # Last resort - return the lowest tier
    lowest_tier = db.query(CampaignTier).filter(
        and_(
            CampaignTier.creator_id == creator_id,
            CampaignTier.is_active == True
        )
    ).order_by(CampaignTier.amount_cents.asc()).first()
    
    if lowest_tier:
        logger.info(f"Using lowest available tier: {lowest_tier.title}")
        return lowest_tier
    
    # If we got here, we need to create a default tier
    logger.info("No suitable tier found or created - this should never happen")
    return None


def record_transaction(transaction: dict, email: str, user_id: int, timestamp: datetime, db: Session) -> None:
    """Record a Ko-fi transaction in the database"""
    transaction_id = transaction.get("transactionId")
    if not transaction_id:
        logger.warning("No transaction ID provided - skipping transaction recording")
        return
    
    existing_webhook = db.query(KofiWebhook).filter(
        KofiWebhook.transaction_id == transaction_id
    ).first()
    
    if existing_webhook:
        logger.info(f"Transaction already recorded: {transaction_id}")
        return
    
    amount_val = float(transaction.get("amount", 0))
    is_subscription = transaction.get("isSubscription", False)
    
    webhook_record = KofiWebhook(
        transaction_id=transaction_id,
        email=email,
        user_id=user_id,
        amount=amount_val,
        is_subscription=is_subscription,
        timestamp=timestamp
    )
    db.add(webhook_record)
    logger.info(f"Recorded Ko-fi transaction: {transaction_id}, Amount={amount_val}")

def get_or_create_donor_tier(creator_id: int, db: Session) -> CampaignTier:
    """Get or create a donor tier for Ko-fi donations"""
    # Look for existing donor tier - ONLY KO-FI PLATFORM TIERS
    donor_tier = db.query(CampaignTier).filter(
        and_(
            CampaignTier.creator_id == creator_id,
            CampaignTier.is_active == True,
            CampaignTier.platform_type == "KOFI",  # Add platform filter
            or_(
                func.lower(CampaignTier.title).contains("donor"),
                func.lower(CampaignTier.title).contains("donation")
            )
        )
    ).first()
    
    if donor_tier:
        return donor_tier
    
    # Look for free tier to base settings on - ONLY KO-FI PLATFORM TIERS
    free_tier = db.query(CampaignTier).filter(
        and_(
            CampaignTier.creator_id == creator_id,
            CampaignTier.is_active == True,
            CampaignTier.platform_type == "KOFI",  # Add platform filter
            or_(
                func.lower(CampaignTier.title).contains("free"),
                CampaignTier.amount_cents == 0
            )
        )
    ).first()
    
    # Create new donor tier
    downloads_allowed = 0 if free_tier is None else free_tier.album_downloads_allowed
    max_sessions = 1 if free_tier is None else free_tier.max_sessions
    
    donor_tier = CampaignTier(
        creator_id=creator_id,
        title="Ko-fi Donor",
        description="Access for Ko-fi donors below tier thresholds",
        amount_cents=0,
        patron_count=0,
        is_active=True,
        platform_type="KOFI",  # Set platform type when creating
        album_downloads_allowed=downloads_allowed,
        track_downloads_allowed=downloads_allowed,
        max_sessions=max_sessions,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc)
    )
    db.add(donor_tier)
    db.flush()
    logger.info(f"Created new donor tier for creator {creator_id}")
    
    return donor_tier

async def assign_free_tier(email: str, creator: User, data: dict, db: Session) -> tuple[User, JSONResponse | None]:
    """
    Assign free tier to users - NO RESET, preserve usage from when they were active
    """
    user = db.query(User).filter(
        or_(
            func.lower(User.email) == email.lower()
        )
    ).first()
    
    if not user:
        logger.info(f"Creating new user for expired Ko‑fi subscriber: {email}")
        username = email.split('@')[0]
        user = User(
            email=email,
            username=username,
            role=UserRole.KOFI,
            created_by=creator.id,
            is_active=True
        )
        db.add(user)
        db.flush()
    else:
        logger.info(f"Found existing user with expired Ko‑fi subscription: ID={user.id}, Email={user.email}")
        user.is_active = True
    
    campaign = db.query(Campaign).filter(
        and_(
            Campaign.creator_id == creator.id,
            Campaign.is_primary == True
        )
    ).first()
    
    if campaign:
        user.campaign_id = str(campaign.id)
    
    # Find or create free tier
    free_tier = db.query(CampaignTier).filter(
        and_(
            CampaignTier.creator_id == creator.id,
            CampaignTier.is_active == True,
            CampaignTier.platform_type == "KOFI",
            or_(
                func.lower(CampaignTier.title).contains("free"),
                CampaignTier.amount_cents == 0
            )
        )
    ).first()
    
    if not free_tier:
        logger.info(f"No free Ko-fi tier found for creator {creator.id} - creating one automatically")
        free_tier = CampaignTier(
            creator_id=creator.id,
            title="Free Ko-fi",
            description="Free access for expired subscribers",
            amount_cents=0,
            patron_count=0,
            is_active=True,
            platform_type="KOFI",
            album_downloads_allowed=0,
            track_downloads_allowed=0,
            book_requests_allowed=0,
            max_sessions=1,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        db.add(free_tier)
        db.flush()
        logger.info(f"Created new free Ko-fi tier for creator {creator.id}")
    else:
        logger.info(f"Found existing free Ko-fi tier: {free_tier.title}")
    
    # *** NO RESET for expired users - preserve their usage from when they were active ***
    current_data = user.patreon_tier_data or {}
    album_downloads_used = current_data.get('album_downloads_used', 0)
    track_downloads_used = current_data.get('track_downloads_used', 0) 
    book_requests_used = current_data.get('book_requests_used', 0)
    
    logger.info(f"📅 Free tier assignment - preserving usage: albums={album_downloads_used}, tracks={track_downloads_used}, books={book_requests_used}")
    
    transaction = data.get("transaction", {}) if data else {}
    
    # Build free tier data - PRESERVE ALL USAGE
    free_tier_data = {
        'title': free_tier.title,
        'description': 'Expired Ko‑fi Subscription',
        'tier_description': free_tier.description,
        'amount_cents': free_tier.amount_cents,
        'album_downloads_allowed': free_tier.album_downloads_allowed,
        'track_downloads_allowed': free_tier.track_downloads_allowed,
        'book_requests_allowed': getattr(free_tier, 'book_requests_allowed', 0),
        'album_downloads_used': album_downloads_used,  # *** PRESERVE existing usage ***
        'track_downloads_used': track_downloads_used,  # *** PRESERVE existing usage ***
        'book_requests_used': book_requests_used,      # *** PRESERVE existing usage ***
        'last_payment_date': transaction.get("timestamp") if transaction.get("timestamp") else None,
        'period_start': datetime.now(timezone.utc).isoformat(),
        'expires_at': None,
        'kofi_user': True,
        'max_sessions': free_tier.max_sessions,
        'patron_status': 'expired_patron',
        'last_reset_month': current_data.get('last_reset_month'),  # *** PRESERVE existing reset month ***
        'grace_period_ended_at': datetime.now(timezone.utc).isoformat()
    }
    
    # Remove any grace period related fields
    fields_to_remove = ['grace_period_message', 'grace_period_ends_at', 'subscription_expired_at']
    for field in fields_to_remove:
        if field in free_tier_data:
            del free_tier_data[field]
    
    user.patreon_tier_data = free_tier_data
    user.grace_period_ends_at = None  # Clear grace period
    
    logger.info(f"Assigned free tier to expired user {email}: {free_tier.title}")
    
    db.commit()
    db.refresh(user)
    return user, None


async def process_subscription(email: str, creator: User, data: dict, db: Session) -> tuple[User, JSONResponse | None]:
    """
    ENHANCED: Process subscription logic for Ko-fi users
    Now uses activity-based monthly reset logic
    """
    logger.info(f"Processing subscription for email: {email}")
    transaction = data.get("transaction", {})
    
    # Create or get user
    user = db.query(User).filter(
        or_(
            func.lower(User.email) == email.lower()
        )
    ).first()
    if not user:
        logger.info(f"Creating new user for email: {email}")
        username = transaction.get("fromName") or email.split('@')[0]
        user = User(
            email=email,
            username=username,
            role=UserRole.KOFI,
            created_by=creator.id,
            is_active=True
        )
        db.add(user)
        db.flush()
    else:
        logger.info(f"Found existing user: ID={user.id}, Email: {user.email}")
    
    # Update basic user info
    user.email = email
    username = transaction.get("fromName") or user.username
    user.username = username
    user.role = UserRole.KOFI
    user.created_by = creator.id
    user.is_active = True
    
    # Assign to campaign
    campaign = db.query(Campaign).filter(
        and_(
            Campaign.creator_id == creator.id,
            Campaign.is_primary == True
        )
    ).first()
    if campaign:
        user.campaign_id = str(campaign.id)
    
    # Parse timestamps
    try:
        timestamp_str = transaction.get("timestamp")
        if timestamp_str:
            payment_timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        else:
            payment_timestamp = datetime.now(timezone.utc)
    except Exception as e:
        logger.error(f"Error parsing timestamp: {str(e)}")
        payment_timestamp = datetime.now(timezone.utc)
    
    expiry_date = calculate_kofi_expiry_date(payment_timestamp)
    
    # Extract amounts correctly from API response
    subscription_amount = float(transaction.get("amount", 0))
    subscription_cents = int(subscription_amount * 100)
    
    has_donations = data.get("hasDonations", False)
    total_donations = data.get("totalDonations", 0)
    donation_cents = int(float(total_donations) * 100) if total_donations else 0
    
    # Total amount for tier matching
    total_amount = subscription_cents + donation_cents
    
    logger.info(f"Subscription amount: {subscription_amount} EUR ({subscription_cents} cents)")
    logger.info(f"Donation amount: {total_donations} EUR ({donation_cents} cents)")
    logger.info(f"Total amount for tier matching: {total_amount} cents")
    
    # Find matching tier (existing logic)
    tier_name = transaction.get("tierName", "Ko‑fi Supporter")
    matching_tier = db.query(CampaignTier).filter(
        and_(
            CampaignTier.creator_id == creator.id,
            func.lower(CampaignTier.title) == func.lower(tier_name),
            CampaignTier.platform_type == "KOFI",
            CampaignTier.is_active == True
        )
    ).first()
    
    if not matching_tier:
        # Try amount-based matching or create default tier
        all_tiers = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == creator.id,
                CampaignTier.is_active == True,
                CampaignTier.platform_type == "KOFI"
            )
        ).order_by(CampaignTier.amount_cents.desc()).all()
        
        for tier in all_tiers:
            if total_amount >= tier.amount_cents:
                matching_tier = tier
                break
        
        if not matching_tier:
            matching_tier = CampaignTier(
                creator_id=creator.id,
                title="Ko-fi Basic",
                description="Default tier for Ko-fi subscribers",
                amount_cents=max(100, subscription_cents),
                patron_count=0,
                platform_type="KOFI", 
                is_active=True,
                album_downloads_allowed=0,
                track_downloads_allowed=0,
                book_requests_allowed=0,
                max_sessions=1,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            db.add(matching_tier)
            db.flush()
    
    # *** NEW: Activity-based reset check ***
    current_data = user.patreon_tier_data or {}
    album_downloads_used, track_downloads_used, book_requests_used, was_reset = check_and_reset_monthly_downloads(
        current_data, 
        data, 
        activity_type="subscription"
    )
    
    if was_reset:
        current_month = datetime.now(timezone.utc).strftime("%Y-%m")
        reset_reason = f"subscription_renewal_month_{current_month}"
        logger.info(f"✅ Subscription renewal reset for user {user.email}")
    else:
        reset_reason = "no_reset_conditions_met"
        logger.info(f"📅 No reset for {user.email} - Albums: {album_downloads_used}, Tracks: {track_downloads_used}, Books: {book_requests_used}")
    
    # Get current month for tracking
    current_month = datetime.now(timezone.utc).strftime("%Y-%m")
    
    # Create tier data
    tier_data = {
        'title': matching_tier.title,
        'description': transaction.get("type", "Ko‑fi Subscription"),
        'tier_description': f"Ko‑fi {matching_tier.title} - {transaction.get('type', 'Subscription')}",
        'amount_cents': total_amount,
        'subscription_amount_cents': subscription_cents,
        'donation_amount_cents': donation_cents,
        'album_downloads_allowed': matching_tier.album_downloads_allowed,
        'track_downloads_allowed': matching_tier.track_downloads_allowed,
        'book_requests_allowed': getattr(matching_tier, 'book_requests_allowed', 0),
        'album_downloads_used': album_downloads_used,
        'track_downloads_used': track_downloads_used,
        'book_requests_used': book_requests_used,
        'last_payment_date': payment_timestamp.isoformat(),
        'period_start': payment_timestamp.isoformat(),
        'expires_at': expiry_date.isoformat(),
        'payment_day': payment_timestamp.day,
        'kofi_user': True,
        'max_sessions': matching_tier.max_sessions,
        'patron_status': 'active_patron',
        'chapters_allowed_per_book_request': getattr(matching_tier, 'chapters_allowed_per_book_request', 0),
        'last_reset_month': current_month if was_reset else current_data.get('last_reset_month')  # Only update if reset occurred
    }
    
    # Add reset tracking metadata
    if was_reset:
        tier_data.update({
            'monthly_reset_date': payment_timestamp.isoformat(),
            'reset_reason': reset_reason,
            'reset_triggered_by': 'subscription_renewal'
        })
    
    # Add donation information if present
    if has_donations or donation_cents > 0:
        tier_data.update({
            'has_donations': True,
            'total_donations': total_donations,
            'donation_count': data.get("donationCount", 0)
        })
    
    # Clean up any grace period data for active subscriptions
    fields_to_remove = ['grace_period_message', 'grace_period_ends_at', 'subscription_expired_at']
    for field in fields_to_remove:
        if field in tier_data:
            del tier_data[field]
    
    user.patreon_tier_data = tier_data
    user.grace_period_ends_at = expiry_date + timedelta(days=3)
    
    # Record transaction
    record_transaction(transaction, email, user.id, payment_timestamp, db)
    
    # Commit changes
    db.commit()
    db.refresh(user)
    
    logger.info(f"✅ Ko‑fi subscription processed for user ID: {user.id}, Email: {user.email}")
    return user, None



def upgrade_user_tier(user: User, donation_cents: int, creator: User, db: Session) -> None:
    """
    For a subscriber, validates if the user qualifies for a tier upgrade based on
    their subscription amount plus donation amount.
    """
    current_data = user.patreon_tier_data or {}
    base_tier_title = current_data.get("title", "")
    
    # *** FIXED: Get the actual subscription amount from stored tier data ***
    # The subscription amount should be stored from the original API response
    subscription_amount = current_data.get('subscription_amount_cents')
    
    if not subscription_amount:
        # Fallback: extract from the current amount_cents if it exists
        # This assumes the current amount_cents represents the subscription amount
        subscription_amount = current_data.get('amount_cents', 0)
        
        # If still no amount, try to extract from transaction data in current_data
        if not subscription_amount and 'transaction' in current_data:
            transaction_amount = current_data['transaction'].get('amount', 0)
            subscription_amount = int(float(transaction_amount) * 100)
        
        # Last resort fallback - log warning
        if not subscription_amount:
            logger.warning(f"Could not determine subscription amount for user {user.email}, using 0")
            subscription_amount = 0
    
    # Calculate aggregated amount (subscription + donation)
    aggregated_amount = subscription_amount + donation_cents
    
    logger.info(f"Subscription amount: {subscription_amount} cents")
    logger.info(f"New donation: {donation_cents} cents")
    logger.info(f"Aggregated amount: {aggregated_amount} cents")
    
    # Query available tiers
    available_tiers = db.query(CampaignTier).filter(
        CampaignTier.creator_id == creator.id,
        CampaignTier.is_active == True,
        CampaignTier.platform_type == "KOFI"
    ).order_by(CampaignTier.amount_cents.desc()).all()  # *** FIXED: Order by DESC to get highest qualifying tier
    
    # Find highest tier that matches the total amount
    eligible_tier = None
    for tier in available_tiers:
        if tier.amount_cents <= aggregated_amount:
            eligible_tier = tier
            logger.info(f"Found qualifying tier: {tier.title} (requires {tier.amount_cents} cents)")
            break  # Take the first (highest) qualifying tier
    
    # Only upgrade if a higher tier exists
    if base_tier_title and eligible_tier and eligible_tier.title != base_tier_title:
        logger.info(f"Upgrading user from tier '{base_tier_title}' to '{eligible_tier.title}'")
        
        # Create a copy of the current data to preserve important fields
        updated_tier_data = dict(current_data)
        
        # Update with new tier information
        updated_tier_data.update({
            'title': eligible_tier.title,
            'tier_description': eligible_tier.description,
            'amount_cents': aggregated_amount,  # Store the total aggregated amount
            'album_downloads_allowed': eligible_tier.album_downloads_allowed,
            'track_downloads_allowed': eligible_tier.track_downloads_allowed,
            'book_requests_allowed': eligible_tier.book_requests_allowed,
            'max_sessions': eligible_tier.max_sessions,
            'patron_status': 'active_patron',
            'donation_amount_cents': donation_cents,
            'subscription_amount_cents': subscription_amount,  # *** FIXED: Store actual subscription amount
            'chapters_allowed_per_book_request': getattr(eligible_tier, 'chapters_allowed_per_book_request', 0)
        })
        
        # Remove grace period message if present
        if 'grace_period_message' in updated_tier_data:
            logger.info("Removing grace period message for upgraded tier")
            del updated_tier_data['grace_period_message']
        
        user.patreon_tier_data = updated_tier_data
        
        # Update grace period
        if 'expires_at' in updated_tier_data and updated_tier_data['expires_at']:
            try:
                expires_at = datetime.fromisoformat(updated_tier_data['expires_at'].replace('Z', '+00:00'))
                user.grace_period_ends_at = expires_at + timedelta(days=3)
                logger.info(f"Updated grace period for upgraded tier: {user.grace_period_ends_at.isoformat()}")
            except (ValueError, TypeError) as e:
                logger.error(f"Error updating grace period during tier upgrade: {str(e)}")
                
        db.commit()
        logger.info("User tier upgraded successfully.")
    else:
        if not eligible_tier:
            logger.info(f"No tier qualifies for aggregated amount {aggregated_amount} cents")
        elif eligible_tier.title == base_tier_title:
            logger.info(f"User already has the highest qualifying tier: {base_tier_title}")
        else:
            logger.info("No tier upgrade applicable; user remains at current subscription tier.")

async def process_donation(email: str, creator: User, data: dict, db: Session) -> tuple[User, JSONResponse | None]:
    """
    Process donation logic for Ko-fi users with activity-based monthly reset
    """
    logger.info(f"Processing donation for email: {email}")
    transaction = data.get("transaction", {})
    
    # Create or get user (existing code remains the same)
    user = db.query(User).filter(
        or_(
            func.lower(User.email) == email.lower()
        )
    ).first()
    if not user:
        logger.info(f"Creating new user for email: {email}")
        username = transaction.get("fromName") or email.split('@')[0]
        logger.info(f"Using username: {username}")
        user = User(
            email=email,
            username=username,
            role=UserRole.KOFI,
            created_by=creator.id,
            is_active=True
        )
        db.add(user)
        db.flush()
        logger.info(f"New user created with ID: {user.id}")
    else:
        logger.info(f"Found existing user: ID={user.id}, Email: {user.email}")
    
    # Update basic user info
    user.email = email
    username = transaction.get("fromName") or user.username
    user.username = username
    user.role = UserRole.KOFI
    user.created_by = creator.id
    user.is_active = True
    logger.info(f"Updated user basic info: Username={username}, Role={UserRole.KOFI}")
    
    # Assign to campaign
    campaign = db.query(Campaign).filter(
        and_(
            Campaign.creator_id == creator.id,
            Campaign.is_primary == True
        )
    ).first()
    if campaign:
        user.campaign_id = str(campaign.id)
        logger.info(f"Assigned campaign ID: {campaign.id}")
    else:
        logger.warning(f"No primary campaign found for creator ID: {creator.id}")
    
    # Parse timestamps
    try:
        timestamp_str = transaction.get("timestamp")
        logger.info(f"Raw timestamp from transaction: {timestamp_str}")
        if timestamp_str:
            payment_timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            logger.info(f"Parsed payment timestamp: {payment_timestamp}")
        else:
            payment_timestamp = datetime.now(timezone.utc)
            logger.info(f"Using current time as payment timestamp: {payment_timestamp}")
    except Exception as e:
        logger.error(f"Error parsing timestamp: {str(e)}")
        payment_timestamp = datetime.now(timezone.utc)
        logger.info(f"Using current time as payment timestamp after error: {payment_timestamp}")
    
    expiry_date = calculate_kofi_expiry_date(payment_timestamp)
    logger.info(f"Calculated expiry date: {expiry_date}")
    
    # Check if user already has an active subscription (existing logic)
    current_data = user.patreon_tier_data or {}
    current_status = current_data.get('patron_status')
    is_subscription = current_data.get('description', '').lower().find('subscription') != -1
    
    # If user has an active subscription, potentially upgrade their tier
    if current_status in ['active_patron', 'grace_period'] and is_subscription:
        logger.info(f"User has subscription (status: {current_status}) - checking for tier upgrade")
        
        total_donation_amount = data.get("totalDonations", 0)
        donation_cents = int(float(total_donation_amount) * 100)
        
        if donation_cents > 0:
            logger.info(f"Upgrading tier with donation amount: {donation_cents} cents")
            
            # *** Check for activity-based reset before upgrade ***
            album_downloads_used, track_downloads_used, book_requests_used, was_reset = check_and_reset_monthly_downloads(
                current_data, 
                data, 
                activity_type="donation"
            )
            
            if was_reset:
                current_month = datetime.now(timezone.utc).strftime("%Y-%m")
                logger.info(f"✅ Donation triggered monthly reset for subscription user {user.email}")
                # Update current_data with reset values
                current_data.update({
                    'album_downloads_used': album_downloads_used,
                    'track_downloads_used': track_downloads_used,
                    'book_requests_used': book_requests_used,
                    'last_reset_month': current_month,
                    'monthly_reset_date': datetime.now(timezone.utc).isoformat(),
                    'reset_triggered_by': 'donation_activity'
                })
                user.patreon_tier_data = current_data
                db.flush()
            
            upgrade_user_tier(user, donation_cents, creator, db)
            record_transaction(transaction, email, user.id, payment_timestamp, db)
            db.commit()
            db.refresh(user)
            return user, None
    
    # Continue with normal donation handling
    total_donation_amount = data.get("totalDonations", 0)
    donation_cents = int(float(total_donation_amount) * 100)
    logger.info(f"Total donation amount for this month: ${total_donation_amount} ({donation_cents} cents)")
    
    # Get all available tiers sorted by amount
    available_tiers = db.query(CampaignTier).filter(
        CampaignTier.creator_id == creator.id,
        CampaignTier.is_active == True,
        CampaignTier.platform_type == "KOFI"
    ).order_by(CampaignTier.amount_cents.asc()).all()
    
    # Find the highest tier the donation amount qualifies for
    eligible_tier = None
    for tier in available_tiers:
        if tier.amount_cents <= donation_cents:
            eligible_tier = tier
        else:
            break
    
    if not eligible_tier:
        logger.info(f"Donation amount doesn't meet any tier criteria - using default donor tier")
        eligible_tier = get_or_create_donor_tier(creator.id, db)
    else:
        logger.info(f"Donation qualifies for tier: {eligible_tier.title} (requires {eligible_tier.amount_cents} cents)")
    
    # *** Activity-based reset for donation users ***
    album_downloads_used, track_downloads_used, book_requests_used, was_reset = check_and_reset_monthly_downloads(
        current_data, 
        data, 
        activity_type="donation"
    )
    
    if was_reset:
        logger.info(f"✅ Donation triggered monthly reset for user {user.email}")
    else:
        logger.info(f"📅 No reset for donation user {user.email} - preserving usage")
    
    # Get current month for tracking
    current_month = datetime.now(timezone.utc).strftime("%Y-%m")
    
    tier_data = {
        'title': eligible_tier.title,
        'description': "Ko‑fi Donation",
        'tier_description': eligible_tier.description,
        'amount_cents': donation_cents,
        'album_downloads_allowed': eligible_tier.album_downloads_allowed,
        'track_downloads_allowed': eligible_tier.track_downloads_allowed,
        'book_requests_allowed': getattr(eligible_tier, 'book_requests_allowed', 0),
        'album_downloads_used': album_downloads_used,
        'track_downloads_used': track_downloads_used,
        'book_requests_used': book_requests_used,
        'last_payment_date': payment_timestamp.isoformat(),
        'period_start': payment_timestamp.isoformat(),
        'expires_at': expiry_date.isoformat(),
        'payment_day': payment_timestamp.day,
        'kofi_user': True,
        'max_sessions': eligible_tier.max_sessions,
        'patron_status': 'active_patron',
        'donation_based': True,
        'total_donations': total_donation_amount,
        'chapters_allowed_per_book_request': getattr(eligible_tier, 'chapters_allowed_per_book_request', 0),
        'last_reset_month': current_month if was_reset else current_data.get('last_reset_month')  # Only update if reset occurred
    }
    
    # Add reset tracking metadata if reset occurred
    if was_reset:
        tier_data.update({
            'monthly_reset_date': payment_timestamp.isoformat(),
            'reset_triggered_by': 'donation_activity'
        })
    
    logger.info(f"New tier data to save: {json.dumps(tier_data)}")
    user.patreon_tier_data = tier_data
    
    # Set grace period for donation (3 days after expiry)
    user.grace_period_ends_at = expiry_date + timedelta(days=3)
    logger.info(f"Set donation grace period until {user.grace_period_ends_at.isoformat()}")
    
    # Record transaction
    record_transaction(transaction, email, user.id, payment_timestamp, db)
    
    # Commit changes
    logger.info(f"Committing database changes for user ID: {user.id}")
    db.commit()
    db.refresh(user)
    logger.info(f"Ko‑fi login successful for user ID: {user.id}, Email: {user.email}")
    return user, None


def upgrade_user_tier(user: User, donation_cents: int, creator: User, db: Session) -> None:
    """
    For a subscriber, validates if the user qualifies for a tier upgrade based on
    their subscription amount plus donation amount.
    """
    current_data = user.patreon_tier_data or {}
    base_tier_title = current_data.get("title", "")
    
    # *** FIXED: Get the actual subscription amount from stored tier data ***
    # The subscription amount should be stored from the original API response
    subscription_amount = current_data.get('subscription_amount_cents')
    
    if not subscription_amount:
        # Fallback: extract from the current amount_cents if it exists
        # This assumes the current amount_cents represents the subscription amount
        subscription_amount = current_data.get('amount_cents', 0)
        
        # If still no amount, try to extract from transaction data in current_data
        if not subscription_amount and 'transaction' in current_data:
            transaction_amount = current_data['transaction'].get('amount', 0)
            subscription_amount = int(float(transaction_amount) * 100)
        
        # Last resort fallback - log warning
        if not subscription_amount:
            logger.warning(f"Could not determine subscription amount for user {user.email}, using 0")
            subscription_amount = 0
    
    # Calculate aggregated amount (subscription + donation)
    aggregated_amount = subscription_amount + donation_cents
    
    logger.info(f"Subscription amount: {subscription_amount} cents")
    logger.info(f"New donation: {donation_cents} cents")
    logger.info(f"Aggregated amount: {aggregated_amount} cents")
    
    # Query available tiers
    available_tiers = db.query(CampaignTier).filter(
        CampaignTier.creator_id == creator.id,
        CampaignTier.is_active == True,
        CampaignTier.platform_type == "KOFI"
    ).order_by(CampaignTier.amount_cents.desc()).all()  # *** FIXED: Order by DESC to get highest qualifying tier
    
    # Find highest tier that matches the total amount
    eligible_tier = None
    for tier in available_tiers:
        if tier.amount_cents <= aggregated_amount:
            eligible_tier = tier
            logger.info(f"Found qualifying tier: {tier.title} (requires {tier.amount_cents} cents)")
            break  # Take the first (highest) qualifying tier
    
    # Only upgrade if a higher tier exists
    if base_tier_title and eligible_tier and eligible_tier.title != base_tier_title:
        logger.info(f"Upgrading user from tier '{base_tier_title}' to '{eligible_tier.title}'")
        
        # Create a copy of the current data to preserve important fields
        updated_tier_data = dict(current_data)
        
        # Update with new tier information
        updated_tier_data.update({
            'title': eligible_tier.title,
            'tier_description': eligible_tier.description,
            'amount_cents': aggregated_amount,  # Store the total aggregated amount
            'album_downloads_allowed': eligible_tier.album_downloads_allowed,
            'track_downloads_allowed': eligible_tier.track_downloads_allowed,
            'book_requests_allowed': getattr(eligible_tier, 'book_requests_allowed', 0),
            'max_sessions': eligible_tier.max_sessions,
            'patron_status': 'active_patron',
            'donation_amount_cents': donation_cents,
            'subscription_amount_cents': subscription_amount,  # *** FIXED: Store actual subscription amount
            'chapters_allowed_per_book_request': getattr(eligible_tier, 'chapters_allowed_per_book_request', 0)
        })
        
        # Remove grace period message if present
        if 'grace_period_message' in updated_tier_data:
            logger.info("Removing grace period message for upgraded tier")
            del updated_tier_data['grace_period_message']
        
        user.patreon_tier_data = updated_tier_data
        
        # Update grace period
        if 'expires_at' in updated_tier_data and updated_tier_data['expires_at']:
            try:
                expires_at = datetime.fromisoformat(updated_tier_data['expires_at'].replace('Z', '+00:00'))
                user.grace_period_ends_at = expires_at + timedelta(days=3)
                logger.info(f"Updated grace period for upgraded tier: {user.grace_period_ends_at.isoformat()}")
            except (ValueError, TypeError) as e:
                logger.error(f"Error updating grace period during tier upgrade: {str(e)}")
                
        db.commit()
        logger.info("User tier upgraded successfully.")
    else:
        if not eligible_tier:
            logger.info(f"No tier qualifies for aggregated amount {aggregated_amount} cents")
        elif eligible_tier.title == base_tier_title:
            logger.info(f"User already has the highest qualifying tier: {base_tier_title}")
        else:
            logger.info("No tier upgrade applicable; user remains at current subscription tier.")
@router.post("/webhook")
async def kofi_webhook(request: Request, db: Session = Depends(get_db)):
    """Process Ko-fi webhook"""
    try:
        # Get webhook data from request
        webhook_data = await request.json()
        logger.info(f"Received Ko-fi webhook: {webhook_data}")
        
        # Get creator ID from webhook
        creator_id = webhook_data.get("creator_id")
        if not creator_id:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Missing creator ID"})
            
        # Get Ko-fi settings for creator
        kofi_settings = db.query(KofiSettings).filter(
            KofiSettings.creator_id == creator_id
        ).first()
        
        if not kofi_settings:
            return JSONResponse(status_code=400, content={"status": "error", "message": "Creator Ko-fi settings not found"})
        
        # Process webhook here
        return JSONResponse(status_code=200, content={"status": "success"})
    except Exception as e:
        logger.error(f"Error processing Ko-fi webhook: {str(e)}")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

@router.get("/verify")
async def verify_kofi(email: str, db: Session = Depends(get_db)):
    """Verify Ko-fi status for an email"""
    try:
        from kofi_service import kofi_service
        logger.info(f"Verifying Ko-fi status for email: {email}")
        patron_data = await kofi_service.verify_patron(email)
        if patron_data:
            logger.info(f"Ko-fi patron verified successfully: {email}")
            return JSONResponse(status_code=200, content=patron_data)
        logger.info(f"Ko-fi patron not found: {email}")
        return JSONResponse(status_code=404, content={"status": "not_found"})
    except Exception as e:
        logger.error(f"Error verifying Ko-fi status: {str(e)}")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

async def fetch_kofi_data(email: str, base_url: str) -> Optional[Dict]:
    """
    Fetch Ko-fi data for a user from the Google Sheet API with cache busting
    """
    try:
        timeout = httpx.Timeout(60.0)
        
        # Add cache busting parameters
        cache_buster = f"{int(time.time())}_{random.randint(1000, 9999)}"
        request_id = str(uuid.uuid4())[:8]
        
        params = {
            "email": email,
            "_nocache": cache_buster,  # Time-based cache buster
            "debug_id": request_id     # Request ID for tracking in logs
        }
        
        logger.info(f"Fetching Ko-fi data for {email} from URL: {base_url} with cache-buster: {cache_buster}")
        logger.info(f"Using request ID: {request_id}")
        
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            # Set headers to prevent caching
            headers = {
                "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0"
            }
            
            logger.info(f"Checking Ko-fi status for {email} with params: {params}")
            response = await client.get(base_url, params=params, headers=headers)
            
            logger.info(f"Google Sheet API response status: {response.status_code}")
            if response.status_code != 200:
                logger.error(f"Error checking Ko-fi status: {response.status_code} - {response.text}")
                return None
            
            data = response.json()
            
            # Log script version if provided
            if "version" in data:
                logger.info(f"Google Sheet script version: {data.get('version')}")
                
            logger.info(f"Google Sheet API response data: {json.dumps(data)}")
            
            if data.get("status") == "not_found":
                logger.info(f"No Ko-fi subscription found for {email}")
                return None
                
            if data.get("status") == "expired":
                logger.info(f"Ko-fi subscription expired for {email}")
                return None
            
            if data.get("status") == "active":
                logger.info(f"Active Ko-fi subscription found for {email}")
                return data
                
            logger.warning(f"Unknown status in Ko-fi data: {data.get('status')}")
            return None
            
    except Exception as e:
        logger.error(f"Error fetching Ko-fi data: {str(e)}", exc_info=True)
        return None
async def handle_kofi_login(
    email: str,
    creator_pin: str, 
    db: Session,
    request: Request
) -> Tuple[Optional["User"], Optional[JSONResponse]]:
    """
    Handle Ko‑fi user login logic - FIXED: Includes guest trial check first.
    This is the main entry point for both Ko-fi and guest trial users.
    """
    try:
        logger.info(f"Processing login for {email} with PIN: {creator_pin[:2]}****")
        
        # 1) Verify the creator
        creator = db.query(User).filter(
            and_(
                User.creator_pin == creator_pin,
                User.role == UserRole.CREATOR,
                User.is_active == True
            )
        ).first()
        
        if not creator:
            logger.error(f"No creator found with PIN: {creator_pin[:2]}****")
            return None, JSONResponse(
                status_code=400,
                content={"error": "Invalid creator PIN", "step": "pin"}
            )
        
        logger.info(f"Creator found: ID={creator.id}, Email={creator.email}")
        
        # 2) ✅ FIRST: Check for guest trial user (they use same login modal)
        from guest_trial_routes import check_trial_user_login
        trial_user, trial_error = await check_trial_user_login(email, creator.id, db)
        if trial_user:
            logger.info(f"Found guest trial user: {email}")
            return trial_user, None
        elif trial_error:
            logger.warning(f"Guest trial login failed for {email}: {trial_error}")
            # Don't return error yet - could be Ko-fi user, continue to Ko-fi check
        else:
            # No guest trial user found, no error - continue to Ko-fi check
            trial_error = None
        
        # 3) Get existing Ko-fi user data ONLY
        existing_user = db.query(User).filter(
            and_(
                func.lower(User.email) == email.lower(),
                User.created_by == creator.id,
                User.role == UserRole.KOFI  # ONLY Ko-fi users
            )
        ).first()
        
        # 4) Get Ko-fi settings for the API
        kofi_settings = db.query(KofiSettings).filter(
            KofiSettings.creator_id == creator.id
        ).first()
        
        if not kofi_settings:
            logger.error(f"No Ko‑fi settings found for creator ID: {creator.id}")
            return None, JSONResponse(
                status_code=400,
                content={"error": "Creator has no Ko‑fi settings", "step": "kofi"}
            )
        
        google_sheet_url = kofi_settings.google_sheet_url or "https://curly-cloud-17fc.tkinrinde.workers.dev/webhook"
        
        # 5) ALWAYS make API call first to check current status
        cache_buster = f"{int(time.time())}_{random.randint(1000, 9999)}"
        request_id = str(uuid.uuid4())[:8]
        
        params = {
            "email": email,
            "_nocache": cache_buster,
            "debug_id": request_id
        }
        
        logger.info(f"Making API call to check current Ko-fi status (cache-buster: {cache_buster})")
        
        # 6) Make API call
        api_data = None
        api_error = None
        try:
            timeout = httpx.Timeout(60.0)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                headers = {
                    "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
                    "Pragma": "no-cache",
                    "Expires": "0"
                }
                
                response = await client.get(google_sheet_url, params=params, headers=headers)
                
                logger.info(f"API response status: {response.status_code}")
                if response.status_code == 200:
                    try:
                        api_data = response.json()
                        if "version" in api_data:
                            logger.info(f"Google Sheet script version: {api_data.get('version')}")
                        logger.info(f"API response: {json.dumps(api_data)}")
                    except json.JSONDecodeError:
                        logger.error(f"Failed to parse API response as JSON")
                        api_error = "Invalid JSON response"
                else:
                    logger.error(f"API error: {response.status_code} - {response.text}")
                    api_error = f"HTTP {response.status_code}"
        
        except Exception as e:
            logger.error(f"API call failed: {str(e)}")
            api_error = str(e)
        
        # 7) Process API response if available
        if api_data:
            status = api_data.get("status")
            
            if status == "active":
                logger.info(f"API shows ACTIVE subscription - processing renewal")
                
                transaction_type = api_data.get("transactionType", "")
                if not transaction_type:
                    transaction = api_data.get("transaction", {})
                    transaction_type = "subscription" if transaction.get("isSubscription") else "donation"
                
                total_donations = api_data.get("totalDonations", 0)
                has_donations = api_data.get("hasDonations", False) or total_donations > 0
                
                if transaction_type == "subscription":
                    user, response = await process_subscription(email, creator, api_data, db)
                    if response:
                        return user, response
                    
                    # Apply donation upgrade if present
                    if has_donations and total_donations > 0:
                        logger.info("Applying donation upgrade for renewed subscription")
                        donation_cents = int(float(total_donations) * 100)
                        upgrade_user_tier(user, donation_cents, creator, db)
                        db.commit()
                        db.refresh(user)
                    
                    # Clean up any grace period data
                    if user.patreon_tier_data:
                        tier_data = user.patreon_tier_data
                        grace_fields = ['grace_period_message', 'subscription_expired_at', 'grace_period_ends_at']
                        for field in grace_fields:
                            tier_data.pop(field, None)
                        tier_data['patron_status'] = 'active_patron'
                        user.patreon_tier_data = tier_data
                        user.grace_period_ends_at = None
                        db.commit()
                        db.refresh(user)
                    
                    logger.info(f"✅ RENEWED: User {email} subscription reactivated")
                    return user, None
                
                else:
                    # Handle donation renewals
                    if existing_user:
                        current_data = existing_user.patreon_tier_data or {}
                        if current_data.get('patron_status') == 'active_patron':
                            logger.info("Active donation upgrade for existing subscription")
                            donation_cents = int(float(total_donations) * 100)
                            if donation_cents > 0:
                                upgrade_user_tier(existing_user, donation_cents, creator, db)
                                db.commit()
                                db.refresh(existing_user)
                                return existing_user, None
                    
                    # New donation user
                    user, response = await process_donation(email, creator, api_data, db)
                    if response:
                        return user, response
                    
                    logger.info(f"✅ RENEWED: User {email} donation processed")
                    return user, None
            
            elif status == "expired":
                logger.info(f"API confirms EXPIRED status - applying grace period logic")
                if existing_user:
                    return await handle_expired_subscription(email, creator, api_data, db)
                else:
                    return await assign_free_tier(email, creator, api_data, db)
            
            elif status == "not_found":
                logger.info(f"API shows NOT FOUND - checking fallback")
                return await fallback_kofi_login(email, creator, None, db)
            
            else:
                logger.error(f"Unknown API status: {status}")
                api_error = f"Unknown status: {status}"
        
        # 8) API failed or returned no data - use fallback with grace period logic
        if existing_user and existing_user.patreon_tier_data:
            tier_data = existing_user.patreon_tier_data
            patron_status = tier_data.get('patron_status')
            
            # Check if user is in grace period
            if patron_status == 'grace_period' and existing_user.grace_period_ends_at:
                now = datetime.now(timezone.utc)
                if existing_user.grace_period_ends_at > now:
                    logger.info(f"API unavailable - preserving grace period until {existing_user.grace_period_ends_at.isoformat()}")
                    
                    # Update grace period message to indicate API issue
                    days_remaining = (existing_user.grace_period_ends_at - now).days
                    hours_remaining = int((existing_user.grace_period_ends_at - now).total_seconds() / 3600)
                    
                    if days_remaining > 0:
                        time_message = f"{days_remaining} day{'s' if days_remaining > 1 else ''}"
                    else:
                        time_message = f"{hours_remaining} hour{'s' if hours_remaining > 1 else ''}"
                    
                    tier_data['grace_period_message'] = (
                        f"Your subscription has expired, but you have {time_message} left in your grace period. "
                        f"Please renew soon to maintain access. (Ko-fi API temporarily unavailable)"
                    )
                    
                    # Check for monthly reset even during grace period fallback
                    album_downloads_used, track_downloads_used, book_requests_used, was_reset = check_and_reset_monthly_downloads(tier_data)
                    if was_reset:
                        logger.info(f"✅ Monthly downloads reset during grace period fallback")
                        tier_data.update({
                            'album_downloads_used': album_downloads_used,
                            'track_downloads_used': track_downloads_used,
                            'book_requests_used': book_requests_used,
                            'last_reset_month': datetime.now(timezone.utc).strftime("%Y-%m"),
                            'monthly_reset_date': datetime.now(timezone.utc).isoformat()
                        })
                    
                    existing_user.patreon_tier_data = tier_data
                    db.commit()
                    db.refresh(existing_user)
                    return existing_user, None
        
        # 9) Use standard fallback logic for Ko-fi users
        logger.info(f"Using Ko-fi fallback login (API error: {api_error})")
        fallback_result = await fallback_kofi_login(email, creator, None, db)
        
        # If fallback found a Ko-fi user, return it
        if fallback_result[0]:  # fallback_result is (user, error_response)
            return fallback_result
        
        # Neither guest trial nor Ko-fi user found
        logger.info(f"No user found for {email} (neither guest trial nor Ko-fi)")
        
        # If we had a guest trial error, return that for better UX
        if trial_error:
            return None, JSONResponse(
                status_code=400,
                content=trial_error
            )
        
        # Otherwise, return generic not found
        return None, JSONResponse(
            status_code=404,
            content={"error": "User not found or inactive", "code": "USER_NOT_FOUND"}
        )
        
    except Exception as e:
        logger.error(f"Error in handle_kofi_login: {str(e)}", exc_info=True)
        db.rollback()
        return None, JSONResponse(
            status_code=500,
            content={"error": "Internal server error during login", "step": "server"}
        )

async def fallback_kofi_login(email: str, creator: User, campaign: Campaign, db: Session):
    """
    Fallback login for Ko-fi when API is unavailable or returns no data.
    Properly handles grace period logic.
    FIXED: Added None checks for last_payment_date
    """
    logger.info(f"Using fallback login for Ko-fi user: {email}")
    
    # Check if the user exists
    user = db.query(User).filter(
        and_(
            func.lower(User.email) == email.lower(),
            User.created_by == creator.id
        )
    ).first()
    
    if not user:
        logger.info(f"User {email} not found in database - rejecting login")
        return None, JSONResponse(
            status_code=400,
            content={"error": "Email not found or patron is inactive", "step": "email"}
        )
    
    logger.info(f"User found in DB: {user.email} - checking local subscription data.")
    tier_data = user.patreon_tier_data or {}
    patron_status = tier_data.get('patron_status')
    
    # Check if user is in grace period
    if patron_status == 'grace_period' and user.grace_period_ends_at:
        now = datetime.now(timezone.utc)
        if user.grace_period_ends_at > now:
            # Still in grace period - keep existing tier
            logger.info(f"User still in grace period until {user.grace_period_ends_at.isoformat()}")
            
            # Update grace period message
            hours_remaining = int((user.grace_period_ends_at - now).total_seconds() / 3600)
            days_remaining = (user.grace_period_ends_at - now).days
            
            if days_remaining > 0:
                time_message = f"{days_remaining} day{'s' if days_remaining > 1 else ''}"
            else:
                time_message = f"{hours_remaining} hour{'s' if hours_remaining > 1 else ''}"
            
            tier_data['grace_period_message'] = (
                f"Your subscription has expired, but you have {time_message} left in your grace period. "
                "Please renew soon to maintain access. (Ko-fi API temporarily unavailable)"
            )
            
            # Check for monthly reset
            album_downloads_used, track_downloads_used, book_requests_used, was_reset = check_and_reset_monthly_downloads(tier_data)
            if was_reset:
                tier_data['album_downloads_used'] = album_downloads_used
                tier_data['track_downloads_used'] = track_downloads_used
                tier_data['book_requests_used'] = book_requests_used
                tier_data['last_reset_month'] = datetime.now(timezone.utc).strftime("%Y-%m")
                tier_data['monthly_reset_date'] = datetime.now(timezone.utc).isoformat()
            
            user.patreon_tier_data = tier_data
            user.is_active = True
            if campaign and not user.campaign_id:
                user.campaign_id = str(campaign.id)
            db.commit()
            db.refresh(user)
            return user, None
        else:
            # Grace period ended
            logger.info("Grace period has ended - proceeding to check subscription status")
    
    # Check subscription status from stored data
    has_payment_data = False
    last_payment_date = None
    
    # FIXED: Added comprehensive None checks for last_payment_date
    last_payment_date_str = tier_data.get('last_payment_date')
    if last_payment_date_str is not None:
        try:
            last_payment_date = datetime.fromisoformat(last_payment_date_str.replace('Z', '+00:00'))
            has_payment_data = True
            logger.info(f"Last payment date: {last_payment_date.isoformat()}")
        except (ValueError, TypeError) as e:
            logger.error(f"Error parsing last_payment_date '{last_payment_date_str}': {str(e)}")
            last_payment_date = None
            has_payment_data = False
    else:
        logger.warning("last_payment_date is None or missing from tier_data")
        has_payment_data = False
    
    is_active = False
    if has_payment_data and last_payment_date:
        now = datetime.now(timezone.utc)
        expiry_date = calculate_kofi_expiry_date(last_payment_date)
        grace_period_end = expiry_date + timedelta(days=3)
        logger.info(f"Calculated expiry_date: {expiry_date.isoformat()}")
        logger.info(f"Grace period ends: {grace_period_end.isoformat()}")
        
        if now < expiry_date:
            # Still active
            is_active = True
            logger.info("Subscription is still active")
        elif now <= grace_period_end:
            # In grace period - preserve tier
            logger.info("Subscription expired but within grace period")
            
            # Preserve existing tier data but update status
            preserved_tier_data = dict(tier_data)
            preserved_tier_data.update({
                'patron_status': 'grace_period',
                'subscription_expired_at': expiry_date.isoformat(),
                'grace_period_ends_at': grace_period_end.isoformat()
            })
            
            days_remaining = (grace_period_end - now).days
            hours_remaining = int((grace_period_end - now).total_seconds() / 3600)
            
            if days_remaining > 0:
                time_message = f"{days_remaining} day{'s' if days_remaining > 1 else ''}"
            else:
                time_message = f"{hours_remaining} hour{'s' if hours_remaining > 1 else ''}"
            
            preserved_tier_data['grace_period_message'] = (
                f"Your subscription has expired, but you have {time_message} left in your grace period. "
                "Please renew soon to maintain access. (Ko-fi API temporarily unavailable)"
            )
            
            user.patreon_tier_data = preserved_tier_data
            user.grace_period_ends_at = grace_period_end
            user.is_active = True
            if campaign and not user.campaign_id:
                user.campaign_id = str(campaign.id)
            db.commit()
            db.refresh(user)
            return user, None
    
    if is_active and tier_data.get('patron_status') == 'active_patron':
        logger.info("User is still within monthly renewal => keep existing tier.")
        user.is_active = True
        if campaign and not user.campaign_id:
            user.campaign_id = str(campaign.id)
        db.commit()
        db.refresh(user)
        return user, None
    
    # If we get here, either no valid subscription or grace period ended
    logger.info("User is expired and past grace period => assigning to free tier.")
    return await assign_free_tier(email, creator, None, db)


async def handle_expired_subscription(email: str, creator: User, data: dict, db: Session) -> tuple[User, JSONResponse | None]:
    """
    Handle expired Ko-fi subscriptions with proper grace period logic.
    Preserves user's original tier for 3 days after expiry.
    """
    user = db.query(User).filter(
        and_(
            func.lower(User.email) == email.lower(),
            User.created_by == creator.id
        )
    ).first()
    
    if not user:
        # New user with expired subscription - create with free tier
        logger.info(f"No existing user found for expired subscription: {email}")
        return await assign_free_tier(email, creator, data, db)
    
    logger.info(f"Found existing user with expired subscription: ID={user.id}")
    user.is_active = True
    now = datetime.now(timezone.utc)
    
    # Get existing tier data
    existing_tier_data = user.patreon_tier_data or {}
    
    # Get last payment information
    last_payment_str = None
    last_transaction = data.get("lastTransaction")
    if last_transaction:
        last_payment_str = last_transaction.get("timestamp")
    
    if not last_payment_str and 'last_payment_date' in existing_tier_data:
        last_payment_str = existing_tier_data['last_payment_date']
    
    if not last_payment_str:
        # No payment history - assign free tier
        logger.info("No payment history found - assigning free tier")
        return await assign_free_tier(email, creator, data, db)
    
    try:
        last_payment_date = datetime.fromisoformat(last_payment_str.replace('Z', '+00:00'))
        expiry_date = calculate_kofi_expiry_date(last_payment_date)
        grace_period_end = expiry_date + timedelta(days=3)
        
        logger.info(f"Subscription expired on: {expiry_date.isoformat()}")
        logger.info(f"Grace period ends on: {grace_period_end.isoformat()}")
        
        if now <= grace_period_end:
            # WITHIN GRACE PERIOD - PRESERVE ORIGINAL TIER
            days_since_expiry = (now - expiry_date).days
            hours_since_expiry = int((now - expiry_date).total_seconds() / 3600)
            days_remaining = (grace_period_end - now).days
            hours_remaining = int((grace_period_end - now).total_seconds() / 3600)
            
            logger.info(f"User is within grace period ({hours_since_expiry} hours since expiry, {hours_remaining} hours remaining)")
            
            # Check if we already have tier data to preserve
            if existing_tier_data and existing_tier_data.get('title'):
                # Preserve ALL existing tier benefits
                preserved_tier_data = dict(existing_tier_data)
                
                # Update only the status and grace period info
                preserved_tier_data.update({
                    'patron_status': 'grace_period',
                    'subscription_expired_at': expiry_date.isoformat(),
                    'grace_period_ends_at': grace_period_end.isoformat()
                })
                
                # Create appropriate message based on time remaining
                if days_remaining > 0:
                    time_message = f"{days_remaining} day{'s' if days_remaining > 1 else ''}"
                elif hours_remaining > 0:
                    time_message = f"{hours_remaining} hour{'s' if hours_remaining > 1 else ''}"
                else:
                    minutes_remaining = int((grace_period_end - now).total_seconds() / 60)
                    time_message = f"{minutes_remaining} minute{'s' if minutes_remaining > 1 else ''}"
                
                preserved_tier_data['grace_period_message'] = (
                    f"Your subscription expired {days_since_expiry if days_since_expiry > 0 else 'today'}. "
                    f"You have {time_message} left in your grace period. "
                    "Please renew to maintain access."
                )
                
                # Check for monthly reset even during grace period
                album_downloads_used, track_downloads_used, book_requests_used, was_reset = check_and_reset_monthly_downloads(preserved_tier_data)
                if was_reset:
                    logger.info(f"✅ Monthly downloads reset during grace period for user {user.email}")
                    preserved_tier_data['album_downloads_used'] = album_downloads_used
                    preserved_tier_data['track_downloads_used'] = track_downloads_used
                    preserved_tier_data['book_requests_used'] = book_requests_used
                    preserved_tier_data['last_reset_month'] = datetime.now(timezone.utc).strftime("%Y-%m")
                    preserved_tier_data['monthly_reset_date'] = datetime.now(timezone.utc).isoformat()
                
                # DO NOT change tier benefits (title, downloads allowed, etc.)
                user.patreon_tier_data = preserved_tier_data
                user.grace_period_ends_at = grace_period_end
                
                logger.info(f"Preserved tier '{preserved_tier_data.get('title')}' during grace period")
            else:
                # No existing tier data - need to look up their previous tier
                # This shouldn't normally happen, but handle it gracefully
                logger.warning("No existing tier data found during grace period - using default tier")
                
                # Try to find a reasonable default tier
                default_tier = db.query(CampaignTier).filter(
                    and_(
                        CampaignTier.creator_id == creator.id,
                        CampaignTier.is_active == True,
                        CampaignTier.platform_type == "KOFI"
                    )
                ).order_by(CampaignTier.amount_cents.asc()).first()
                
                if default_tier:
                    tier_data = {
                        'title': default_tier.title,
                        'description': 'Ko‑fi Subscription (Grace Period)',
                        'tier_description': default_tier.description,
                        'amount_cents': default_tier.amount_cents,
                        'album_downloads_allowed': default_tier.album_downloads_allowed,
                        'track_downloads_allowed': default_tier.track_downloads_allowed,
                        'book_requests_allowed': getattr(default_tier, 'book_requests_allowed', 0),
                        'chapters_allowed_per_book_request': getattr(matching_tier, 'chapters_allowed_per_book_request', 0),
                        'album_downloads_used': 0,
                        'track_downloads_used': 0,
                        'book_requests_used': 0,
                        'last_payment_date': last_payment_str,
                        'period_start': last_payment_str,
                        'expires_at': expiry_date.isoformat(),
                        'kofi_user': True,
                        'max_sessions': default_tier.max_sessions,
                        'patron_status': 'grace_period',
                        'subscription_expired_at': expiry_date.isoformat(),
                        'grace_period_ends_at': grace_period_end.isoformat(),
                        'grace_period_message': (
                            f"Your subscription has expired. You have {days_remaining} days left in your grace period. "
                            "Please renew to maintain access."
                        )
                    }
                    user.patreon_tier_data = tier_data
                    user.grace_period_ends_at = grace_period_end
                else:
                    # No tiers found - last resort
                    return await assign_free_tier(email, creator, data, db)
            
            # Assign to campaign if needed
            campaign = db.query(Campaign).filter(
                and_(
                    Campaign.creator_id == creator.id,
                    Campaign.is_primary == True
                )
            ).first()
            
            if campaign and not user.campaign_id:
                user.campaign_id = str(campaign.id)
            
            db.commit()
            db.refresh(user)
            return user, None
            
        else:
            # GRACE PERIOD ENDED - assign free tier
            logger.info(f"Grace period ended {(now - grace_period_end).days} days ago - assigning free tier")
            return await assign_free_tier(email, creator, data, db)
            
    except Exception as e:
        logger.error(f"Error calculating grace period: {e}")
        # On error, fallback to free tier
        return await assign_free_tier(email, creator, data, db)



def calculate_kofi_expiry_date(payment_date: datetime) -> datetime:
    """Calculate expiry date based on calendar month"""
    # Add one month to the payment date
    current_day = payment_date.day
    current_month = payment_date.month
    current_year = payment_date.year
    
    # Calculate next month
    next_month = current_month + 1
    next_year = current_year
    
    # Handle year rollover
    if next_month > 12:
        next_month = 1
        next_year += 1
    
    # Calculate days in next month
    days_in_next_month = monthrange(next_year, next_month)[1]
    
    # If the payment day doesn't exist in next month (e.g., 31st in a 30-day month),
    # use the last day of the month
    expiry_day = min(current_day, days_in_next_month)
    
    # Create expiry date
    expiry_date = datetime(next_year, next_month, expiry_day, 
                          payment_date.hour, payment_date.minute, payment_date.second,
                          tzinfo=payment_date.tzinfo or timezone.utc)
    
    return expiry_date

@router.get("/settings")
async def get_kofi_settings(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get Ko-fi settings for the current creator"""
    # Verify the user is a creator
    if not current_user.is_creator:
        return JSONResponse(status_code=403, content={"status": "error", "message": "Only creators can access Ko-fi settings"})
    
    # Get the Ko-fi settings
    kofi_settings = db.query(KofiSettings).filter(
        KofiSettings.creator_id == current_user.id
    ).first()
    
    if not kofi_settings:
        return JSONResponse(status_code=200, content={
            "status": "success",
            "settings": None
        })
    
    return JSONResponse(status_code=200, content={
        "status": "success",
        "settings": {
            "google_sheet_url": kofi_settings.google_sheet_url,
            "verification_token": kofi_settings.verification_token
        }
    })

@router.post("/settings")
async def save_kofi_settings(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Save Ko-fi settings for the current creator"""
    # Verify the user is a creator
    if not current_user.is_creator:
        return JSONResponse(status_code=403, content={"status": "error", "message": "Only creators can access Ko-fi settings"})
    
    # Get the request data
    try:
        data = await request.json()
        google_sheet_url = data.get("google_sheet_url")
        verification_token = data.get("verification_token")
    except Exception:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid request data"})
    
    if not google_sheet_url or not verification_token:
        return JSONResponse(status_code=400, content={
            "status": "error",
            "message": "Google Sheet URL and verification token are required"
        })
    
    # Get or create Ko-fi settings
    kofi_settings = db.query(KofiSettings).filter(
        KofiSettings.creator_id == current_user.id
    ).first()
    
    if not kofi_settings:
        kofi_settings = KofiSettings(
            creator_id=current_user.id,
            google_sheet_url=google_sheet_url,
            verification_token=verification_token
        )
        db.add(kofi_settings)
    else:
        kofi_settings.google_sheet_url = google_sheet_url
        kofi_settings.verification_token = verification_token
    
    try:
        db.commit()
        return JSONResponse(status_code=200, content={
            "status": "success",
            "message": "Ko-fi settings saved successfully"
        })
    except Exception as e:
        db.rollback()
        logger.error(f"Error saving Ko-fi settings: {str(e)}")
        return JSONResponse(status_code=500, content={
            "status": "error",
            "message": "Error saving Ko-fi settings"
        })

@router.post("/test-connection")
async def test_kofi_connection(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Test connection to Ko-fi via Google Sheet API"""
    # Verify the user is a creator
    if not current_user.is_creator:
        return JSONResponse(status_code=403, content={"status": "error", "message": "Only creators can test Ko-fi connection"})
    
    # Get the request data
    try:
        data = await request.json()
        google_sheet_url = data.get("google_sheet_url")
    except Exception:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid request data"})
    
    if not google_sheet_url:
        return JSONResponse(status_code=400, content={
            "status": "error",
            "message": "Google Sheet URL is required"
        })
    
    # Test the connection by sending a test request to the Google Sheet API
    try:
        timeout = httpx.Timeout(60.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            params = {"test": "true"}
            logger.info(f"Testing Ko-fi connection to URL: {google_sheet_url}")
            response = await client.get(google_sheet_url, params=params)
            
            if response.status_code != 200:
                logger.error(f"Error connecting to Google Sheet API: Status code {response.status_code}")
                return JSONResponse(status_code=400, content={
                    "status": "error",
                    "message": f"Error connecting to Google Sheet API: Status code {response.status_code}"
                })
            
            try:
                response_data = response.json()
                logger.info(f"Google Sheet API test response: {response_data}")
                return JSONResponse(status_code=200, content={
                    "status": "success",
                    "message": "Connection successful",
                    "data": response_data
                })
            except json.JSONDecodeError:
                logger.error("Google Sheet API returned invalid JSON")
                return JSONResponse(status_code=400, content={
                    "status": "error",
                    "message": "Google Sheet API returned invalid JSON"
                })
    except Exception as e:
        logger.error(f"Error testing Ko-fi connection: {str(e)}")
        return JSONResponse(status_code=500, content={
            "status": "error",
            "message": f"Error connecting to Google Sheet API: {str(e)}"
        })

@router.post("/tier-update")
async def kofi_tier_update(request: Request, db: Session = Depends(get_db)):
    """
    Process real-time Ko-fi tier updates for logged-in users.
    Properly handles grace periods and tier preservation.
    """
    try:
        # Parse request data (unchanged)
        try:
            payload = await request.json()
            logger.info(f"Received Ko-fi tier update as JSON: {payload}")
        except json.JSONDecodeError:
            form_data = await request.form()
            payload = dict(form_data)
            logger.info(f"Received Ko-fi tier update as form data: {payload}")
            
            if "tierData" in payload and isinstance(payload["tierData"], str):
                try:
                    payload["tierData"] = json.loads(payload["tierData"])
                except json.JSONDecodeError:
                    logger.error("Failed to parse tierData JSON string")
        
        # Extract email and access data
        email = payload.get("email")
        tier_data = payload.get("tierData")
        verification_token = payload.get("verificationToken")
        
        if not email or not tier_data:
            logger.error("Missing required data in tier update request")
            return JSONResponse(
                status_code=400, 
                content={"status": "error", "message": "Missing required data"}
            )

        # Verify token and find creator
        kofi_settings = db.query(KofiSettings).filter(
            KofiSettings.verification_token == verification_token
        ).first()
        
        if not kofi_settings:
            logger.warning(f"Invalid verification token for tier update")
            return JSONResponse(
                status_code=403, 
                content={"status": "error", "message": "Invalid verification token"}
            )
            
        creator = db.query(User).filter(
            User.id == kofi_settings.creator_id
        ).first()
        
        if not creator:
            logger.error(f"Creator not found for token: {verification_token}")
            return JSONResponse(
                status_code=404, 
                content={"status": "error", "message": "Creator not found"}
            )
            
        logger.info(f"Processing tier update for creator: {creator.id}")
            
        # Find the user in the database
        user = db.query(User).filter(
            func.lower(User.email) == email.lower()
        ).first()
        
        # Handle expired status with proper grace period
        if tier_data.get("status") == "expired":
            logger.info(f"Ko-fi tier update indicates expired status - handling grace period")
            
            if not user:
                # Create new user in free tier
                user, _ = await assign_free_tier(email, creator, tier_data, db)
                return JSONResponse(
                    status_code=200, 
                    content={
                        "status": "success", 
                        "message": "New user created with free tier"
                    }
                )
            
            # For existing users, handle grace period properly
            now = datetime.now(timezone.utc)
            current_tier_data = user.patreon_tier_data or {}
            
            # Get last payment date
            last_payment_str = None
            last_transaction = tier_data.get("lastTransaction")
            if last_transaction:
                last_payment_str = last_transaction.get("timestamp")
            
            if not last_payment_str and 'last_payment_date' in current_tier_data:
                last_payment_str = current_tier_data['last_payment_date']
            
            if last_payment_str:
                try:
                    last_payment_date = datetime.fromisoformat(last_payment_str.replace('Z', '+00:00'))
                    expiry_date = calculate_kofi_expiry_date(last_payment_date)
                    grace_period_end = expiry_date + timedelta(days=3)
                    
                    if now <= grace_period_end:
                        # Within grace period - preserve tier
                        logger.info(f"User within grace period - preserving tier benefits")
                        
                        # Preserve all tier benefits
                        preserved_tier_data = dict(current_tier_data)
                        preserved_tier_data.update({
                            'patron_status': 'grace_period',
                            'subscription_expired_at': expiry_date.isoformat(),
                            'grace_period_ends_at': grace_period_end.isoformat()
                        })
                        
                        days_remaining = (grace_period_end - now).days
                        hours_remaining = int((grace_period_end - now).total_seconds() / 3600)
                        
                        if days_remaining > 0:
                            time_message = f"{days_remaining} day{'s' if days_remaining > 1 else ''}"
                        else:
                            time_message = f"{hours_remaining} hour{'s' if hours_remaining > 1 else ''}"
                        
                        preserved_tier_data['grace_period_message'] = (
                            f"Your subscription has expired, but you have {time_message} left in your grace period. "
                            "Please renew to maintain access."
                        )
                        
                        user.patreon_tier_data = preserved_tier_data
                        user.grace_period_ends_at = grace_period_end
                        
                        db.commit()
                        return JSONResponse(
                            status_code=200, 
                            content={
                                "status": "success", 
                                "message": "User set to grace period with tier preserved",
                                "grace_period_ends_at": grace_period_end.isoformat()
                            }
                        )
                    else:
                        # Grace period ended - assign free tier
                        logger.info("Grace period has ended - assigning free tier")
                        user, _ = await assign_free_tier(email, creator, tier_data, db)
                        db.commit()
                        return JSONResponse(
                            status_code=200, 
                            content={
                                "status": "success", 
                                "message": "User downgraded to free tier after grace period"
                            }
                        )
                        
                except Exception as e:
                    logger.error(f"Error processing grace period: {str(e)}")
            
            # Fallback - assign free tier
            user, _ = await assign_free_tier(email, creator, tier_data, db)
            db.commit()
            return JSONResponse(
                status_code=200, 
                content={
                    "status": "success", 
                    "message": "User set to free tier"
                }
            )
        
        # Handle new users for active subscriptions/donations
        if not user:
            logger.info(f"User not found for tier update - creating new user: {email}")
            
            transaction_type = tier_data.get("transactionType", "")
            if not transaction_type:
                transaction = tier_data.get("transaction", {})
                transaction_type = ("subscription" 
                    if transaction.get("isSubscription") else "donation")
            
            logger.info(f"New user with transaction type: {transaction_type}")
            
            if transaction_type == "subscription":
                user, _ = await process_subscription(email, creator, tier_data, db)
                if user:
                    return JSONResponse(
                        status_code=200, 
                        content={"status": "success", "message": "New user created and tier set via subscription"}
                    )
            else:
                user, _ = await process_donation(email, creator, tier_data, db)
                if user:
                    return JSONResponse(
                        status_code=200, 
                        content={"status": "success", "message": "New user created and tier set via donation"}
                    )
            
            if not user:
                logger.error(f"Failed to create new user for tier update: {email}")
                return JSONResponse(
                    status_code=500, 
                    content={"status": "error", "message": "Failed to create new user"}
                )
        
        # For existing users with active subscriptions
        if user.created_by != creator.id:
            logger.info(f"User {user.id} was created by {user.created_by}, updating to creator {creator.id}")
            user.created_by = creator.id
        
        # Process active subscriptions and donations
        transaction_type = tier_data.get("transactionType", "")
        if not transaction_type:
            transaction = tier_data.get("transaction", {})
            transaction_type = ("subscription" 
                if transaction.get("isSubscription") else "donation")
                
        has_donations = tier_data.get("hasDonations", False)
        total_donations = tier_data.get("totalDonations", 0)
        donation_count = tier_data.get("donationCount", 0)
        
        if transaction_type == "subscription":
            logger.info(f"Processing subscription update for existing user {email}")
            updated_user, _ = await process_subscription(email, creator, tier_data, db)
            
            if has_donations or total_donations > 0 or donation_count > 0:
                logger.info(f"User has both subscription and donations - applying combined amount")
                donation_cents = int(float(total_donations) * 100)
                
                if donation_cents > 0:
                    upgrade_user_tier(updated_user, donation_cents, creator, db)
                    db.commit()
                    db.refresh(updated_user)
                    
            # Clean up any grace period data for active subscriptions
            if updated_user.patreon_tier_data:
                tier_data = updated_user.patreon_tier_data
                fields_to_remove = ['grace_period_message', 'grace_period_ends_at', 'subscription_expired_at']
                for field in fields_to_remove:
                    if field in tier_data:
                        del tier_data[field]
                tier_data['patron_status'] = 'active_patron'
                updated_user.patreon_tier_data = tier_data
                db.commit()
        else:
            logger.info(f"Processing donation update for existing user {email}")
            
            current_data = user.patreon_tier_data or {}
            current_status = current_data.get('patron_status')
            is_subscription = current_data.get('description', '').lower().find('subscription') != -1
            
            if current_status in ['active_patron', 'grace_period'] and is_subscription:
                logger.info(f"User has an existing subscription (status: {current_status}) - checking for tier upgrade")
                donation_cents = int(float(total_donations or 0) * 100)
                
                if donation_cents > 0:
                    upgrade_user_tier(user, donation_cents, creator, db)
                    
                    transaction = tier_data.get("transaction", {})
                    if transaction and transaction.get("transactionId"):
                        try:
                            timestamp_str = transaction.get("timestamp")
                            if timestamp_str:
                                timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                            else:
                                timestamp = datetime.now(timezone.utc)
                        except (ValueError, TypeError):
                            timestamp = datetime.now(timezone.utc)
                            
                        record_transaction(transaction, email, user.id, timestamp, db)
                    
                    db.commit()
                    db.refresh(user)
            else:
                # Normal donation processing
                await process_donation(email, creator, tier_data, db)
        
        logger.info(f"Tier updated successfully for user: {email}")
        return JSONResponse(
            status_code=200, 
            content={"status": "success", "message": "Tier updated successfully"}
        )
        
    except Exception as e:
        logger.error(f"Error processing Ko-fi tier update: {str(e)}", exc_info=True)
        db.rollback()
        return JSONResponse(
            status_code=500, 
            content={"status": "error", "message": str(e)}
        )



def check_and_reset_monthly_downloads(current_data: dict, api_data: Optional[dict] = None, activity_type: str = "subscription") -> tuple[int, int, int, bool]:
    """
    Activity-based monthly reset logic:
    - Reset only once per calendar month maximum
    - Reset triggered by subscription renewal OR donation (if not already reset this month)
    - No reset during grace period
    - No automatic calendar resets
    
    Args:
        current_data: User's current patreon_tier_data
        api_data: Ko-fi API response data from login
        activity_type: "subscription" or "donation"
        
    Returns: 
        (album_used, track_used, book_requests_used, was_reset)
    """
    current_month = datetime.now(timezone.utc).strftime("%Y-%m")  # "2025-07"
    last_reset_month = current_data.get('last_reset_month')
    patron_status = current_data.get('patron_status')
    api_status = api_data.get("status") if api_data else None
    
    # Get current usage
    album_used = current_data.get('album_downloads_used', 0)
    track_used = current_data.get('track_downloads_used', 0)
    book_requests_used = current_data.get('book_requests_used', 0)
    
    # RULE 1: Grace period users NEVER get reset
    if patron_status == 'grace_period':
        logger.info(f"🚫 Grace period user - no reset allowed: albums={album_used}, tracks={track_used}, books={book_requests_used}")
        return album_used, track_used, book_requests_used, False
    
    # RULE 2: Check if already reset this month
    if last_reset_month == current_month:
        logger.info(f"🚫 Already reset this month ({current_month}) - preserving usage: albums={album_used}, tracks={track_used}, books={book_requests_used}")
        return album_used, track_used, book_requests_used, False
    
    # RULE 3: Reset only on active subscription/donation activity
    if api_status != "active":
        logger.info(f"🚫 No active subscription ({api_status}) - no reset: albums={album_used}, tracks={track_used}, books={book_requests_used}")
        return album_used, track_used, book_requests_used, False
    
    # RULE 4: Activity-triggered reset (subscription renewal or donation)
    if activity_type in ["subscription", "donation"]:
        logger.info(f"✅ ACTIVITY RESET: {activity_type} triggered reset for month {current_month}")
        logger.info(f"   Reset: Albums: {album_used}→0, Tracks: {track_used}→0, Books: {book_requests_used}→0")
        return 0, 0, 0, True
    
    # RULE 5: No qualifying activity - preserve usage
    logger.info(f"📅 No qualifying activity - preserving usage: albums={album_used}, tracks={track_used}, books={book_requests_used}")
    return album_used, track_used, book_requests_used, False


