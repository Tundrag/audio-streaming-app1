# kofi_service.py
import logging
import httpx
import asyncio
import time
import random
import uuid
from datetime import datetime, timezone, timedelta
from dateutil.relativedelta import relativedelta
from typing import Optional, Dict, List, Any, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from models import User, UserRole, KofiSettings, KofiWebhook, CampaignTier

logger = logging.getLogger(__name__)

class KofiService:
    """Service to handle Ko-fi authentication via Google Sheets API with activity-based reset logic"""
    
    def __init__(self, google_sheet_url=None):
        self.base_url = google_sheet_url or "https://broad-morning-70d0.tkinrinde.workers.dev/webhook"
        self.timeout = httpx.Timeout(60.0)
        self.grace_period_days = 3  # 3-day grace period
    
    async def verify_patron(self, email: str, check_grace_period: bool = True) -> Optional[Dict]:
        """
        Verify if a user has an active Ko-fi subscription by checking the Google Sheet
        Uses cache busting to prevent stale data
        
        Args:
            email: The email to check
            check_grace_period: Whether to include grace period in the check
            
        Returns:
            Dict with patron data if found and active (or in grace period), None otherwise
        """
        try:
            # Generate cache busting parameters
            cache_buster = f"{int(time.time())}_{random.randint(1000, 9999)}"
            request_id = str(uuid.uuid4())[:8]
            
            logger.info(f"Checking Ko-fi status for {email} with cache buster: {cache_buster}")
            
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                # Set up parameters with cache busting
                params = {
                    "email": email,
                    "_nocache": cache_buster,
                    "debug_id": request_id
                }
                
                # Set headers to prevent caching
                headers = {
                    "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
                    "Pragma": "no-cache",
                    "Expires": "0"
                }
                
                response = await client.get(f"{self.base_url}", params=params, headers=headers)
                
                if response.status_code != 200:
                    logger.error(f"Error checking Ko-fi status: {response.status_code} - {response.text}")
                    return None
                
                data = response.json()
                
                # Log script version if provided
                if "version" in data:
                    logger.info(f"Google Sheet script version: {data.get('version')}")
                
                if data.get("status") == "not_found":
                    logger.info(f"No Ko-fi subscription found for {email}")
                    return None
                    
                if data.get("status") == "expired":
                    logger.info(f"Ko-fi subscription expired for {email}")
                    
                    if check_grace_period:
                        # Check if within grace period
                        last_transaction = data.get("lastTransaction", {})
                        last_payment_str = last_transaction.get("timestamp")
                        
                        if last_payment_str:
                            try:
                                last_payment_date = datetime.fromisoformat(last_payment_str.replace('Z', '+00:00'))
                                expiry_date = self.calculate_expiry_date(last_payment_date)
                                grace_period_end = expiry_date + timedelta(days=self.grace_period_days)
                                now = datetime.now(timezone.utc)
                                
                                if now <= grace_period_end:
                                    logger.info(f"User {email} is within grace period until {grace_period_end.isoformat()}")
                                    
                                    # Return data indicating grace period status
                                    return {
                                        "email": email,
                                        "patron_status": "grace_period",
                                        "subscription_expired_at": expiry_date.isoformat(),
                                        "grace_period_ends_at": grace_period_end.isoformat(),
                                        "last_transaction": last_transaction,
                                        "last_payment_date": last_payment_str,
                                        "is_subscription": last_transaction.get("isSubscription", True),
                                        "tier_name": last_transaction.get("tierName", "Ko-fi Supporter"),
                                        "amount": last_transaction.get("amount", 0),
                                        "currency": last_transaction.get("currency", "USD"),
                                        "status": "expired"  # Keep API status for reset logic
                                    }
                                else:
                                    logger.info(f"Grace period ended for {email} on {grace_period_end.isoformat()}")
                            except Exception as e:
                                logger.error(f"Error calculating grace period: {str(e)}")
                    
                    return None
                
                if data.get("status") == "active":
                    # Extract key information
                    tier_info = data.get("transaction", {})
                    patron_data = {
                        "email": email,
                        "patron_status": "active_patron",
                        "from_name": tier_info.get("from_name", ""),
                        "amount": tier_info.get("amount", 0),
                        "currency": tier_info.get("currency", "USD"),
                        "kofi_transaction_id": tier_info.get("transactionId", ""),
                        "is_subscription": tier_info.get("isSubscription", False),
                        "is_first_subscription": tier_info.get("isFirstSubscription", False),
                        "tier_name": tier_info.get("tierName", "Ko-fi Supporter"),
                        "tier_data": {
                            "title": tier_info.get("tierName", "Ko-fi Supporter"),
                            "amount_cents": int(float(tier_info.get("amount", 0)) * 100),
                            "description": f"Ko-fi {'Subscription' if tier_info.get('isSubscription') else 'Support'}"
                        },
                        "last_payment_date": tier_info.get("timestamp"),
                        "expiry_date": data.get("expiryDate"),
                        "days_since_payment": data.get("daysSincePayment", 0),
                        "status": "active",  # Keep API status for reset logic
                        "has_donations": data.get("hasDonations", False),
                        "total_donations": data.get("totalDonations", 0),
                        "donation_count": data.get("donationCount", 0)
                    }
                    
                    return patron_data
                
                return None
                
        except Exception as e:
            logger.error(f"Error verifying Ko-fi patron: {str(e)}")
            return None
    
    def calculate_expiry_date(self, payment_date: datetime) -> datetime:
        """
        Calculate expiry date based on calendar month
        
        Args:
            payment_date: The date of payment
            
        Returns:
            Expiry date (same day next month, or last day if that date doesn't exist)
        """
        # Add one month to the payment date
        next_month = payment_date + relativedelta(months=1)
        
        # Check if the day exists in the next month
        # If payment was on 31st and next month only has 30 days, use last day of month
        last_day_of_month = (next_month.replace(day=1) + relativedelta(months=1, days=-1)).day
        
        if payment_date.day > last_day_of_month:
            # Use the last day of the month
            expiry_date = next_month.replace(day=last_day_of_month)
        else:
            # Use the same day of the month
            expiry_date = next_month
            
        return expiry_date
    
    def check_and_reset_monthly_downloads(self, current_data: dict, api_data: Optional[dict] = None, activity_type: str = "subscription") -> Tuple[int, int, int, bool]:
        """
        FIXED: Activity-based monthly reset logic matching main Ko-fi routes
        - Reset only once per calendar month maximum
        - Reset triggered by subscription renewal OR donation (if not already reset this month)
        - No reset during grace period
        - No automatic calendar resets
        
        Args:
            current_data: User's current patreon_tier_data
            api_data: Ko-fi API response data from login/webhook
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
    
    async def initialize_patron_data(self, user: User, patron_data: Dict, db: Session, activity_type: str = "subscription") -> User:
        """Initialize or update user with Ko-fi patron data, handling grace periods properly"""
        try:
            logger.info(f"Initializing Ko-fi data for {user.email} with activity_type: {activity_type}")
            
            patron_status = patron_data.get("patron_status", "active_patron")
            
            # Check if user is in grace period
            if patron_status == "grace_period":
                return await self._handle_grace_period_user(user, patron_data, db, activity_type)
            
            # Handle active patrons
            return await self._handle_active_patron(user, patron_data, db, activity_type)
            
        except Exception as e:
            logger.error(f"Error initializing Ko-fi patron: {str(e)}")
            db.rollback()
            raise
    
    async def _handle_grace_period_user(self, user: User, patron_data: Dict, db: Session, activity_type: str) -> User:
        """Handle user in grace period - preserve their existing tier, NO RESET"""
        logger.info(f"Handling grace period for user {user.email}")
        
        current_tier_data = user.patreon_tier_data or {}
        
        # If user has existing tier data, preserve it
        if current_tier_data and current_tier_data.get('title'):
            # Preserve all tier benefits
            preserved_tier_data = dict(current_tier_data)
            
            # Update only status and grace period info
            preserved_tier_data.update({
                'patron_status': 'grace_period',
                'subscription_expired_at': patron_data.get('subscription_expired_at'),
                'grace_period_ends_at': patron_data.get('grace_period_ends_at')
            })
            
            # Calculate time remaining
            grace_end = datetime.fromisoformat(patron_data.get('grace_period_ends_at'))
            now = datetime.now(timezone.utc)
            days_remaining = (grace_end - now).days
            hours_remaining = int((grace_end - now).total_seconds() / 3600)
            
            if days_remaining > 0:
                time_message = f"{days_remaining} day{'s' if days_remaining > 1 else ''}"
            elif hours_remaining > 0:
                time_message = f"{hours_remaining} hour{'s' if hours_remaining > 1 else ''}"
            else:
                minutes_remaining = int((grace_end - now).total_seconds() / 60)
                time_message = f"{minutes_remaining} minute{'s' if minutes_remaining > 1 else ''}"
            
            preserved_tier_data['grace_period_message'] = (
                f"Your subscription has expired, but you have {time_message} left in your grace period. "
                "Please renew to maintain access."
            )
            
            # *** FIXED: NO RESET for grace period users ***
            album_used = preserved_tier_data.get('album_downloads_used', 0)
            track_used = preserved_tier_data.get('track_downloads_used', 0)
            book_used = preserved_tier_data.get('book_requests_used', 0)
            
            logger.info(f"🚫 Grace period - preserving usage: albums={album_used}, tracks={track_used}, books={book_used}")
            
            # Keep existing usage counters unchanged
            preserved_tier_data.update({
                'album_downloads_used': album_used,
                'track_downloads_used': track_used,
                'book_requests_used': book_used,
                # Don't update last_reset_month - preserve existing
            })
            
            user.patreon_tier_data = preserved_tier_data
            user.grace_period_ends_at = grace_end
            
            logger.info(f"Preserved tier '{preserved_tier_data.get('title')}' during grace period with existing usage")
        else:
            # No existing tier data - need to look up based on payment info
            logger.warning(f"No existing tier data for grace period user {user.email}")
            
            # Try to find appropriate tier based on amount
            tier_name = patron_data.get("tier_name", "Ko-fi Supporter")
            amount_cents = int(float(patron_data.get("amount", 0)) * 100)
            
            campaign_tier = db.query(CampaignTier).filter(
                and_(
                    CampaignTier.creator_id == user.created_by,
                    CampaignTier.is_active == True,
                    CampaignTier.platform_type == "KOFI"
                )
            ).filter(
                or_(
                    func.lower(CampaignTier.title) == func.lower(tier_name),
                    CampaignTier.amount_cents == amount_cents
                )
            ).first()
            
            if campaign_tier:
                new_tier_data = {
                    "title": campaign_tier.title,
                    "amount_cents": campaign_tier.amount_cents,
                    "description": f"Ko-fi Subscription (Grace Period)",
                    "tier_description": campaign_tier.description,
                    "kofi_user": True,
                    "last_payment_date": patron_data.get("last_payment_date"),
                    "expires_at": patron_data.get("subscription_expired_at"),
                    "patron_status": "grace_period",
                    "subscription_expired_at": patron_data.get("subscription_expired_at"),
                    "grace_period_ends_at": patron_data.get("grace_period_ends_at"),
                    "album_downloads_allowed": campaign_tier.album_downloads_allowed,
                    "track_downloads_allowed": campaign_tier.track_downloads_allowed,
                    "book_requests_allowed": getattr(campaign_tier, 'book_requests_allowed', 0),
                    "album_downloads_used": 0,  # Start fresh if no existing data
                    "track_downloads_used": 0,
                    "book_requests_used": 0,
                    "max_sessions": campaign_tier.max_sessions,
                    "grace_period_message": "Your subscription has expired. You are in a 3-day grace period.",
                    "chapters_allowed_per_book_request": getattr(campaign_tier, 'chapters_allowed_per_book_request', 0)
                }
                user.patreon_tier_data = new_tier_data
                user.grace_period_ends_at = datetime.fromisoformat(patron_data.get('grace_period_ends_at'))
        
        db.commit()
        db.refresh(user)
        return user
    
    async def _handle_active_patron(self, user: User, patron_data: Dict, db: Session, activity_type: str) -> User:
        """Handle active patron - set up their tier with activity-based reset logic"""
        logger.info(f"Handling active patron {user.email} with activity_type: {activity_type}")
        
        # Check if this is a new webhook transaction
        transaction_id = patron_data.get("kofi_transaction_id", "")
        if transaction_id:
            existing_webhook = db.query(KofiWebhook).filter(
                KofiWebhook.transaction_id  == transaction_id
            ).first()
            
            if existing_webhook:
                logger.info(f"Transaction {transaction_id} already processed")
            else:
                # Record new transaction
                webhook = KofiWebhook(
                    transaction_id=transaction_id,
                    email=patron_data.get("email"),
                    user_id=user.id,
                    amount=patron_data.get("amount", 0),
                    is_subscription=patron_data.get("is_subscription", False),
                    timestamp=datetime.fromisoformat(
                        patron_data.get("last_payment_date", datetime.now(timezone.utc).isoformat()).replace("Z", "+00:00")
                    ),
                )
                db.add(webhook)
                db.flush()

        # Get tier information
        is_subscription = patron_data.get("is_subscription", False)
        tier_name = patron_data.get("tier_name", "Ko-fi Supporter")

        # Find matching campaign tier
        campaign_tier = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == user.created_by,
                func.lower(CampaignTier.title) == func.lower(tier_name),
                CampaignTier.is_active == True,
                CampaignTier.platform_type == "KOFI"
            )
        ).first()

        logger.info(f"Looking for tier: {tier_name}, Found: {campaign_tier.title if campaign_tier else 'None'}")

        # Parse payment date
        payment_date = None
        if patron_data.get("last_payment_date"):
            try:
                payment_date = datetime.fromisoformat(
                    patron_data.get("last_payment_date").replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                payment_date = datetime.now(timezone.utc)
        else:
            payment_date = datetime.now(timezone.utc)

        # Calculate expiration date
        expiry_date = self.calculate_expiry_date(payment_date)

        # *** FIXED: Use activity-based reset logic ***
        current_tier_data = user.patreon_tier_data or {}
        album_used, track_used, book_used, was_reset = self.check_and_reset_monthly_downloads(
            current_tier_data, 
            patron_data,  # Pass patron_data as api_data
            activity_type
        )

        # Get current month
        current_month = datetime.now(timezone.utc).strftime("%Y-%m")

        # Create new tier data
        new_tier_data = {
            "title": tier_name,
            "amount_cents": patron_data.get("tier_data", {}).get(
                "amount_cents", int(float(patron_data.get("amount", 0)) * 100)
            ),
            "description": f"Ko-fi {'Subscription' if is_subscription else 'Support'}",
            "tier_description": campaign_tier.description if campaign_tier else f"Ko-fi {tier_name}",
            "kofi_user": True,
            "last_payment_date": payment_date.isoformat(),
            "expires_at": expiry_date.isoformat(),
            "period_start": payment_date.isoformat(),
            "payment_day": payment_date.day,
            "patron_status": "active_patron",
            # Download settings from campaign tier
            "album_downloads_allowed": campaign_tier.album_downloads_allowed if campaign_tier else 0,
            "track_downloads_allowed": campaign_tier.track_downloads_allowed if campaign_tier else 0,
            "book_requests_allowed": getattr(campaign_tier, 'book_requests_allowed', 0) if campaign_tier else 0,
            "chapters_allowed_per_book_request": getattr(campaign_tier, 'chapters_allowed_per_book_request', 0) if campaign_tier else 0,
            # Use activity-based reset values
            "album_downloads_used": album_used,
            "track_downloads_used": track_used,
            "book_requests_used": book_used,
            # Session limits from campaign tier
            "max_sessions": campaign_tier.max_sessions if campaign_tier else 1,
            # *** FIXED: Only update reset month if actual reset occurred ***
            "last_reset_month": current_month if was_reset else current_tier_data.get('last_reset_month')
        }
        
        # Add reset metadata if reset occurred
        if was_reset:
            reset_reason = f"{activity_type}_activity_month_{current_month}"
            new_tier_data.update({
                'monthly_reset_date': payment_date.isoformat(),
                'reset_reason': reset_reason,
                'reset_triggered_by': f'{activity_type}_activity'
            })
            logger.info(f"✅ Activity reset for {user.email} ({reset_reason})")
        else:
            logger.info(f"📅 No reset for {user.email} - preserving usage")

        # Add donation information if present
        if patron_data.get("has_donations") or patron_data.get("total_donations", 0) > 0:
            new_tier_data.update({
                'has_donations': True,
                'total_donations': patron_data.get("total_donations", 0),
                'donation_count': patron_data.get("donation_count", 0)
            })

        # Remove any grace period data for active subscriptions
        fields_to_remove = ['grace_period_message', 'grace_period_ends_at', 'subscription_expired_at']
        for field in fields_to_remove:
            if field in new_tier_data:
                del new_tier_data[field]

        # Set the tier data
        user.patreon_tier_data = new_tier_data
        user.grace_period_ends_at = expiry_date + timedelta(days=3)  # Set grace period

        # Update user role
        if user.role not in [UserRole.CREATOR, UserRole.TEAM]:
            user.role = UserRole.KOFI

        db.commit()
        db.refresh(user)
        logger.info(f"Successfully updated Ko-fi data for {user.email}")

        return user

# Create singleton instance
kofi_service = KofiService()