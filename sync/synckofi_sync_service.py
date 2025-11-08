# sync/kofi_sync_service.py
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent))

import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from typing import List, Dict, Optional
import asyncio
import json
from models import UserRole, User, CampaignTier, Campaign
from dateutil.relativedelta import relativedelta
from kofi_service import kofi_service

logger = logging.getLogger(__name__)

class KofiSyncService:
    def __init__(self, db: Session):
        self.db = db
        self._last_sync = None
        self._sync_interval = timedelta(days=3)  # More frequent than Patreon
        self._sync_in_progress = False
        self._sync_task = None
        self._stop_event = None
        self._sync_worker = None
        self._enabled = False

    async def initialize(self, background_manager=None, db_factory=None, enabled: bool = True, sync_worker=None):
        """Initialize the Ko-fi sync service"""
        self._enabled = enabled
        self._worker = sync_worker
        if not enabled:
            logger.info("Ko-fi sync service disabled")
            return
        logger.info("Initialized Ko-fi sync service")

    async def start_periodic_task(self):
        """Start the periodic sync task"""
        if not self._enabled:
            logger.info("Ko-fi sync service is disabled, skipping periodic task")
            return

        if self._sync_task is not None:
            logger.warning("Periodic Ko-fi sync already running")
            return

        self._stop_event = asyncio.Event()
        self._sync_task = asyncio.create_task(self._periodic_sync())
        logger.info("Started periodic Ko-fi sync task")

    async def stop_periodic_task(self):
        """Stop the periodic sync task"""
        if self._sync_worker:
            await self._sync_worker.stop()

        if self._stop_event:
            self._stop_event.set()
            if self._sync_task:
                try:
                    await asyncio.wait_for(self._sync_task, timeout=30.0)
                except asyncio.TimeoutError:
                    self._sync_task.cancel()
                self._sync_task = None
            logger.info("Stopped periodic Ko-fi sync task")

    async def _periodic_sync(self):
        """Periodic sync background task for Ko-fi users"""
        try:
            while not self._stop_event.is_set():
                try:
                    if not self._enabled:
                        await asyncio.sleep(60)
                        continue

                    # Get all active creators with campaigns
                    creators = self.db.query(User).filter(
                        and_(
                            User.role == UserRole.CREATOR,
                            User.is_active == True,
                            User.campaign_id.isnot(None)
                        )
                    ).all()

                    for creator in creators:
                        if self._should_sync():
                            try:
                                logger.info(f"Running periodic Ko-fi sync for creator: {creator.username}")
                                if self._sync_worker:
                                    await self._sync_worker.queue_sync(creator.id)
                                else:
                                    await self.sync_kofi_tiers(creator.id)
                                    await self.sync_kofi_downloads(creator.id, self.db)
                            except Exception as e:
                                logger.error(f"Error in Ko-fi sync for creator {creator.username}: {str(e)}")
                                continue

                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(),
                            timeout=self._sync_interval.total_seconds()
                        )
                    except asyncio.TimeoutError:
                        continue

                except Exception as e:
                    logger.error(f"Error in periodic Ko-fi sync: {str(e)}")
                    await asyncio.sleep(60)

        except asyncio.CancelledError:
            logger.info("Periodic Ko-fi sync task cancelled")
        except Exception as e:
            logger.error(f"Fatal error in periodic Ko-fi sync: {str(e)}")
        finally:
            self._sync_task = None
            logger.info("Periodic Ko-fi sync task stopped")

    async def sync_kofi_tiers(self, creator_id: int, force: bool = False) -> List[Dict]:
        """Sync Ko-fi members with campaign tiers"""
        try:
            if self._sync_in_progress:
                logger.info("Ko-fi sync already in progress, skipping")
                return []

            if not force and not self._should_sync():
                logger.info("Skipping Ko-fi sync - not due yet")
                return []

            self._sync_in_progress = True
            logger.info(f"Starting Ko-fi sync for creator {creator_id}")

            # Get the primary campaign
            primary_campaign = self.db.query(Campaign).filter(
                and_(
                    Campaign.creator_id == creator_id,
                    Campaign.is_active == True,
                    Campaign.is_primary == True
                )
            ).first()

            if not primary_campaign:
                logger.error(f"No primary campaign found for creator {creator_id}")
                return []

            logger.info(f"Found primary campaign {primary_campaign.id}")
            
            # Get existing Ko-fi tiers from the database
            existing_tiers = self.db.query(CampaignTier).filter(
                and_(
                    CampaignTier.creator_id == creator_id,
                    CampaignTier.platform_type == "KOFI",
                    CampaignTier.is_active == True
                )
            ).order_by(CampaignTier.amount_cents).all()
            
            logger.info(f"Found {len(existing_tiers)} existing Ko-fi tiers in database")
            
            # Get all Ko-fi users for this creator
            kofi_users = self.db.query(User).filter(
                and_(
                    User.created_by == creator_id,
                    User.role == UserRole.KOFI,
                    User.is_active == True
                )
            ).all()
            
            logger.info(f"Found {len(kofi_users)} Ko-fi users to process")
            
            # Reset tier counts
            for tier in existing_tiers:
                tier.patron_count = 0
            
            # Find or create free tier for expired users
            free_tier = next((t for t in existing_tiers if t.amount_cents == 0), None)
            if not free_tier:
                free_tier = CampaignTier(
                    creator_id=creator_id,
                    title="Free Ko-fi",
                    description="Free access for Ko-fi subscribers",
                    amount_cents=0,
                    patron_count=0,
                    platform_type="KOFI",
                    is_active=True,
                    album_downloads_allowed=0,
                    track_downloads_allowed=0,
                    max_sessions=1,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc)
                )
                self.db.add(free_tier)
                self.db.flush()
                existing_tiers.append(free_tier)
                logger.info(f"Created free Ko-fi tier for creator {creator_id}")
            
            # Process each Ko-fi user
            updated_users = []
            now = datetime.now(timezone.utc)
            
            for user in kofi_users:
                try:
                    current_data = user.patreon_tier_data or {}
                    
                    # Skip users with invalid data format
                    if not isinstance(current_data, dict):
                        logger.warning(f"Invalid tier data format for user {user.email}, skipping")
                        continue
                    
                    tier_title = current_data.get('title')
                    last_payment_date_str = current_data.get('last_payment_date')
                    amount_cents = current_data.get('amount_cents', 0)
                    
                    # Check for Ko-fi specific flag
                    if not current_data.get('kofi_user', False):
                        logger.info(f"User {user.email} not marked as Ko-fi user, skipping")
                        continue
                    
                    # Parse last payment date
                    last_payment_date = None
                    if last_payment_date_str:
                        try:
                            last_payment_date = datetime.fromisoformat(last_payment_date_str.replace('Z', '+00:00'))
                        except (ValueError, TypeError) as e:
                            logger.error(f"Error parsing last_payment_date for {user.email}: {str(e)}")
                    
                    # Calculate expiry date for subscription
                    is_active = False
                    if last_payment_date:
                        expiry_date = self._calculate_expiry_date(last_payment_date)
                        is_active = now < expiry_date
                        logger.info(f"User {user.email} subscription status: {'active' if is_active else 'expired'}, expiry: {expiry_date.isoformat()}")
                    
                    # Check grace period
                    in_grace_period = False
                    if not is_active and user.grace_period_ends_at and now < user.grace_period_ends_at:
                        in_grace_period = True
                        logger.info(f"User {user.email} in grace period until {user.grace_period_ends_at.isoformat()}")
                    
                    # Find matching tier for active users
                    matching_tier = None
                    if is_active or in_grace_period:
                        # First try to match by title
                        if tier_title:
                            matching_tier = next((t for t in existing_tiers if t.title.lower() == tier_title.lower()), None)
                        
                        # If no match by title, try to match by amount
                        if not matching_tier and amount_cents > 0:
                            for tier in sorted(existing_tiers, key=lambda x: x.amount_cents, reverse=True):
                                if amount_cents >= tier.amount_cents:
                                    matching_tier = tier
                                    break
                    
                    # Use free tier for expired users not in grace period
                    if not matching_tier:
                        matching_tier = free_tier
                        logger.info(f"Using free tier for {user.email}")
                    
                    # Update tier information for user
                    updated_tier_data = current_data.copy()
                    
                    # Preserve download counts
                    album_downloads_used = current_data.get('album_downloads_used', 0)
                    track_downloads_used = current_data.get('track_downloads_used', 0)
                    
                    # Update patron status
                    patron_status = 'active_patron' if is_active else 'grace_period' if in_grace_period else 'expired_patron'
                    
                    # Update tier data with latest info
                    updated_tier_data.update({
                        'title': matching_tier.title,
                        'album_downloads_allowed': matching_tier.album_downloads_allowed,
                        'album_downloads_used': album_downloads_used,
                        'track_downloads_allowed': matching_tier.track_downloads_allowed,
                        'track_downloads_used': track_downloads_used,
                        'max_sessions': matching_tier.max_sessions,
                        'patron_status': patron_status,
                        'kofi_user': True,
                    })
                    
                    # Add grace period message if needed
                    if in_grace_period:
                        updated_tier_data['grace_period_message'] = "Your subscription has expired, but you're in a grace period. Please renew to maintain access."
                        updated_tier_data['grace_period_ends_at'] = user.grace_period_ends_at.isoformat()
                    elif 'grace_period_message' in updated_tier_data:
                        del updated_tier_data['grace_period_message']
                    
                    # Update user data
                    user.patreon_tier_data = updated_tier_data
                    
                    # Increment tier patron count
                    matching_tier.patron_count += 1
                    
                    updated_users.append({
                        'email': user.email,
                        'tier': matching_tier.title,
                        'amount_cents': amount_cents,
                        'status': patron_status
                    })
                    
                    logger.info(f"Updated Ko-fi user {user.email} to tier: {matching_tier.title}")
                
                except Exception as e:
                    logger.error(f"Error processing Ko-fi user {user.email}: {str(e)}")
                    continue
            
            # Commit changes
            self.db.commit()
            self._last_sync = datetime.now(timezone.utc)
            logger.info(f"Ko-fi sync completed. Updated {len(updated_users)} members")
            
            return updated_users

        except Exception as e:
            self.db.rollback()
            logger.error(f"Error in Ko-fi sync: {str(e)}")
            raise
        finally:
            self._sync_in_progress = False

    def _calculate_expiry_date(self, payment_date: datetime) -> datetime:
        """Calculate expiry date based on calendar month"""
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

    async def sync_kofi_downloads(self, creator_id: int, db: Session) -> Dict[str, int]:
        """Sync download settings for all Ko-fi users of a creator"""
        try:
            # This method is similar to sync_patron_downloads in PatreonSyncService
            # Get all tiers with valid platform types
            campaign_tiers = {}
            for tier in db.query(CampaignTier).filter(
                and_(
                    CampaignTier.creator_id == creator_id,
                    CampaignTier.is_active == True,
                    CampaignTier.platform_type == "KOFI"
                )
            ).all():
                # Store tier settings by title
                campaign_tiers[tier.title.lower()] = {
                    'track_downloads': tier.track_downloads_allowed,
                    'album_downloads': tier.album_downloads_allowed,
                    'max_sessions': tier.max_sessions
                }

            # Process all Ko-fi users for this creator
            users = db.query(User).filter(
                and_(
                    User.created_by == creator_id,
                    User.role == UserRole.KOFI,
                    User.is_active == True
                )
            ).all()

            updates = {"successful": 0, "failed": 0, "skipped": 0}

            for user in users:
                try:
                    current_data = user.patreon_tier_data or {}
                    tier_title = current_data.get('title', '').lower()
                    
                    # Skip users without Ko-fi flag
                    if not current_data.get('kofi_user', False):
                        updates["skipped"] += 1
                        continue
                    
                    # Only update if we have a matching tier
                    tier_settings = campaign_tiers.get(tier_title)
                    if tier_settings:
                        # Create a copy of current data to update
                        updated_data = current_data.copy()
                        
                        # Update tier-based settings
                        updated_data.update({
                            'album_downloads_allowed': tier_settings['album_downloads'],
                            'track_downloads_allowed': tier_settings['track_downloads'],
                            'max_sessions': tier_settings.get('max_sessions', 1)
                        })
                        
                        # Make sure used values exist
                        if 'album_downloads_used' not in updated_data:
                            updated_data['album_downloads_used'] = 0
                        if 'track_downloads_used' not in updated_data:
                            updated_data['track_downloads_used'] = 0
                        
                        # Update user's tier data
                        user.patreon_tier_data = updated_data
                        updates["successful"] += 1
                    else:
                        updates["skipped"] += 1

                except Exception as e:
                    updates["failed"] += 1
                    logger.error(f"Error updating Ko-fi user {user.email}: {str(e)}")
                    continue

            db.commit()
            logger.info(f"Sync Ko-fi downloads completed. Updated: {updates['successful']}, Skipped: {updates['skipped']}, Failed: {updates['failed']}")
            return updates

        except Exception as e:
            db.rollback()
            logger.error(f"Error syncing Ko-fi downloads: {str(e)}")
            raise

    async def check_expired_kofi_users(self, creator_id: int) -> None:
        """Check for expired Ko-fi users and apply grace periods"""
        try:
            logger.info(f"Checking for expired Ko-fi users for creator {creator_id}")
            
            now = datetime.now(timezone.utc)
            kofi_users = self.db.query(User).filter(
                and_(
                    User.created_by == creator_id,
                    User.role == UserRole.KOFI,
                    User.is_active == True
                )
            ).all()
            
            expired_count = 0
            grace_count = 0
            
            for user in kofi_users:
                try:
                    current_data = user.patreon_tier_data or {}
                    
                    # Skip users without Ko-fi flag
                    if not current_data.get('kofi_user', False):
                        continue
                    
                    # Check if already expired
                    if current_data.get('patron_status') == 'expired_patron':
                        continue
                    
                    last_payment_date_str = current_data.get('last_payment_date')
                    if not last_payment_date_str:
                        continue
                        
                    try:
                        last_payment_date = datetime.fromisoformat(last_payment_date_str.replace('Z', '+00:00'))
                        expiry_date = self._calculate_expiry_date(last_payment_date)
                        
                        if now > expiry_date:
                            # Subscription has expired
                            
                            # Check if already in grace period
                            if user.grace_period_ends_at and now < user.grace_period_ends_at:
                                # Already in grace period
                                grace_count += 1
                                continue
                                
                            # Set grace period (3 days from expiry)
                            user.grace_period_ends_at = expiry_date + timedelta(days=3)
                            
                            # Update tier data
                            current_data['patron_status'] = 'grace_period'
                            current_data['grace_period_message'] = "Your subscription has expired, but you're in a grace period. Please renew to maintain access."
                            current_data['grace_period_ends_at'] = user.grace_period_ends_at.isoformat()
                            user.patreon_tier_data = current_data
                            
                            grace_count += 1
                            logger.info(f"Applied grace period for expired user {user.email} until {user.grace_period_ends_at.isoformat()}")
                        
                    except (ValueError, TypeError) as e:
                        logger.error(f"Error parsing payment date for user {user.email}: {str(e)}")
                        continue
                        
                except Exception as e:
                    logger.error(f"Error checking expiration for Ko-fi user {user.email}: {str(e)}")
                    continue
            
            # Check for grace periods that have ended
            for user in kofi_users:
                try:
                    if user.grace_period_ends_at and now > user.grace_period_ends_at:
                        # Grace period has ended, downgrade to free tier
                        current_data = user.patreon_tier_data or {}
                        
                        # Find free tier
                        free_tier = self.db.query(CampaignTier).filter(
                            and_(
                                CampaignTier.creator_id == creator_id,
                                CampaignTier.platform_type == "KOFI",
                                CampaignTier.is_active == True,
                                CampaignTier.amount_cents == 0
                            )
                        ).first()
                        
                        if not free_tier:
                            # Create free tier if none exists
                            free_tier = CampaignTier(
                                creator_id=creator_id,
                                title="Free Ko-fi",
                                description="Free access for Ko-fi subscribers",
                                amount_cents=0,
                                patron_count=0,
                                platform_type="KOFI",
                                is_active=True,
                                album_downloads_allowed=0,
                                track_downloads_allowed=0,
                                max_sessions=1,
                                created_at=datetime.now(timezone.utc),
                                updated_at=datetime.now(timezone.utc)
                            )
                            self.db.add(free_tier)
                            self.db.flush()
                            logger.info(f"Created free Ko-fi tier for creator {creator_id}")
                        
                        # Preserve download counts
                        album_downloads_used = current_data.get('album_downloads_used', 0)
                        track_downloads_used = current_data.get('track_downloads_used', 0)
                        
                        # Set free tier data
                        current_data.update({
                            'title': free_tier.title,
                            'album_downloads_allowed': free_tier.album_downloads_allowed,
                            'track_downloads_allowed': free_tier.track_downloads_allowed,
                            'max_sessions': free_tier.max_sessions,
                            'patron_status': 'expired_patron'
                        })
                        
                        # Remove grace period message
                        if 'grace_period_message' in current_data:
                            del current_data['grace_period_message']
                        if 'grace_period_ends_at' in current_data:
                            del current_data['grace_period_ends_at']
                        
                        user.patreon_tier_data = current_data
                        expired_count += 1
                        logger.info(f"Downgraded user {user.email} to free tier after grace period ended")
                        
                except Exception as e:
                    logger.error(f"Error processing grace period end for user {user.email}: {str(e)}")
                    continue
            
            self.db.commit()
            logger.info(f"Expired user check completed: {grace_count} in grace period, {expired_count} downgraded to free tier")
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error checking expired Ko-fi users: {str(e)}")
            raise

    async def perform_startup_sync(self, creator_id: int) -> List[Dict]:
        """Perform initial sync on startup"""
        if not self._enabled:
            logger.info("Ko-fi sync service is disabled, skipping startup sync")
            return []

        logger.info(f"Performing startup Ko-fi sync for creator {creator_id}")
        if self._sync_worker:
            return await self._sync_worker.queue_sync(creator_id, initial=True)
        return await self.sync_kofi_tiers(creator_id, force=True)

    async def perform_manual_sync(self, creator_id: int, db: Session) -> List[Dict]:
        """Manually trigger a tier sync"""
        if not self._enabled:
            logger.info("Ko-fi sync service is disabled, skipping manual sync")
            return []

        logger.info(f"Performing manual Ko-fi sync for creator {creator_id}")
        self.db = db  # Update database session
        if self._sync_worker:
            return await self._sync_worker.queue_sync(creator_id, force=True)
        return await self.sync_kofi_tiers(creator_id, force=True)

    def _should_sync(self) -> bool:
        """Check if sync is needed based on last sync time"""
        if not self._last_sync:
            logger.info("No previous Ko-fi sync found - initial sync required")
            return True
            
        time_since_last = datetime.now(timezone.utc) - self._last_sync
        time_left = self._sync_interval - time_since_last
        
        should_sync = time_since_last > self._sync_interval
        
        if should_sync:
            logger.info(f"Ko-fi sync interval ({self._sync_interval.total_seconds()}s) exceeded - syncing now")
        else:
            logger.info(f"Next Ko-fi sync in {time_left.total_seconds():.0f} seconds")
            
        return should_sync

    async def get_sync_status(self) -> Dict:
        """Get current sync status"""
        if not self._enabled:
            return {
                "status": "disabled",
                "last_sync": None,
                "sync_in_progress": False,
                "worker_status": None
            }

        status = {
            "status": "enabled",
            "last_sync": self._last_sync.isoformat() if self._last_sync else None,
            "sync_in_progress": self._sync_in_progress,
            "next_sync": None
        }

        if self._last_sync:
            time_since_last = datetime.now(timezone.utc) - self._last_sync
            next_sync = self._sync_interval - time_since_last
            if next_sync.total_seconds() > 0:
                status["next_sync"] = next_sync.total_seconds()

        if self._sync_worker:
            status["worker_status"] = {
                "running": self._sync_worker._worker_task is not None,
                "queue_size": self._sync_worker._sync_queue.qsize(),
                "in_progress": self._sync_worker._sync_in_progress
            }

        return status

    def is_enabled(self) -> bool:
        """Check if sync service is enabled"""
        return self._enabled


class KofiSyncWorker:
    def __init__(self, background_manager, db_factory):
        self.background_manager = background_manager
        self.db_factory = db_factory
        self._sync_queue = asyncio.Queue()
        self._worker_task = None
        self._stop_event = asyncio.Event()
        
        self._sync_interval = timedelta(days=3)  # More frequent than Patreon
        self._lock = asyncio.Lock()
        self._enabled = True
        
        # Store the last sync time per-creator
        self._last_sync = {}
        
        # Track whether a sync operation is currently running
        self._sync_in_progress = False

    async def start(self):
        """Start the sync worker loop in a background task."""
        if self._worker_task:
            logger.warning("⚠️ Ko-fi sync worker already running!")
            return

        self._worker_task = asyncio.create_task(self._sync_worker())
        logger.info("✅ Started Ko-fi sync worker")

    async def stop(self):
        """Stop the sync worker gracefully."""
        if self._worker_task:
            self._stop_event.set()
            try:
                await asyncio.wait_for(self._worker_task, timeout=30.0)
            except asyncio.TimeoutError:
                self._worker_task.cancel()
            self._worker_task = None
            logger.info("✅ Stopped Ko-fi sync worker")

    async def queue_sync(self, creator_id: int, initial: bool = False, force: bool = False) -> Dict:
        """Queue a sync operation for a creator."""
        if not self._enabled:
            logger.warning(f"⚠️ Ko-fi sync disabled, skipping sync for creator {creator_id}")
            return {"status": "disabled", "message": "Ko-fi sync service is disabled"}

        async with self._lock:
            task_id = f"kofi_sync_{creator_id}_{datetime.now(timezone.utc).timestamp()}"

            # Check if we should skip the sync due to interval
            if not force and not initial and not self._should_sync(creator_id):
                logger.info(f"🚫 Skipping Ko-fi sync for creator {creator_id} - not due yet")
                return {
                    "task_id": task_id,
                    "status": "skipped",
                    "reason": "recent_sync"
                }

            # If a sync is already in progress, just queue this behind it
            if self._sync_in_progress:
                position = self._sync_queue.qsize() + 1
                logger.info(f"🔁 Another Ko-fi sync is in progress, queueing sync for creator {creator_id}")
                await self._sync_queue.put({
                    "task_id": task_id,
                    "creator_id": creator_id,
                    "initial": initial,
                    "force": force,
                    "queued_at": datetime.now(timezone.utc)
                })
                return {
                    "task_id": task_id,
                    "status": "queued",
                    "reason": "sync_in_progress",
                    "position": position
                }

            # Otherwise, queue a new sync task
            await self._sync_queue.put({
                "task_id": task_id,
                "creator_id": creator_id,
                "initial": initial,
                "force": force,
                "queued_at": datetime.now(timezone.utc)
            })

            logger.info(f"📌 Queued {'initial' if initial else 'periodic'} Ko-fi sync for creator {creator_id} (Queue size: {self._sync_queue.qsize()})")
            return {
                "task_id": task_id,
                "status": "queued",
                "position": self._sync_queue.qsize()
            }

    def _should_sync(self, creator_id: int) -> bool:
        """Check if a sync is needed based on the last sync time."""
        if not self._enabled:
            return False

        last_sync_time = self._last_sync.get(creator_id)
        if not last_sync_time:
            # No previous sync, must sync
            return True

        time_since_last = datetime.now(timezone.utc) - last_sync_time
        return time_since_last > self._sync_interval

    async def _sync_worker(self):
        """Background worker loop that processes sync operations from the queue."""
        while not self._stop_event.is_set():
            try:
                # Log the queue size for debugging
                logger.info(f"🟢 Ko-fi sync queue size: {self._sync_queue.qsize()}")

                # Wait up to 30 seconds for a sync task
                try:
                    task = await asyncio.wait_for(self._sync_queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    logger.info("🔄 No Ko-fi sync tasks available, waiting...")
                    continue

                # Extract task info
                creator_id = task.get("creator_id")
                task_id = task.get("task_id")
                initial = task.get("initial", False)
                force = task.get("force", False)

                logger.info(f"Processing {'initial' if initial else 'periodic'} Ko-fi sync for creator {creator_id}")

                self._sync_in_progress = True

                # Create a new database session for this task
                db = next(self.db_factory())  
                try:
                    # Verify creator exists and is active
                    creator = db.query(User).filter(
                        User.id == creator_id,
                        User.role == UserRole.CREATOR,
                        User.is_active == True
                    ).first()

                    if not creator:
                        logger.error(f"❌ Creator {creator_id} not found or inactive.")
                        continue

                    # Process Ko-fi users
                    await self._sync_kofi_tiers(creator_id, db)
                    await self._sync_kofi_downloads(creator_id, db)
                    await self._check_expired_kofi_users(creator_id, db)

                    # Mark the last sync time
                    self._last_sync[creator_id] = datetime.now(timezone.utc)

                    # Commit changes
                    db.commit()

                    logger.info(f"✅ Completed Ko-fi sync for creator {creator_id}")

                except Exception as e:
                    db.rollback()
                    logger.error(f"Error in Ko-fi sync worker for creator {creator_id}: {str(e)}")
                    raise
                finally:
                    db.close()

            except asyncio.CancelledError:
                # Worker was stopped
                break
            except Exception as e:
                logger.error(f"Unexpected error in Ko-fi sync worker: {str(e)}")
                await asyncio.sleep(5)
            finally:
                self._sync_in_progress = False
                self._sync_queue.task_done()

        logger.info("Ko-fi sync worker stopped")

    async def _sync_kofi_tiers(self, creator_id: int, db: Session) -> List[Dict]:
        """Sync Ko-fi members with tiers"""
        # This is a wrapper that calls the KofiSyncService's method
        kofi_sync = KofiSyncService(db)
        return await kofi_sync.sync_kofi_tiers(creator_id, force=True)

    async def _sync_kofi_downloads(self, creator_id: int, db: Session) -> Dict[str, int]:
        """Sync download settings for all Ko-fi users"""
        kofi_sync = KofiSyncService(db)
        return await kofi_sync.sync_kofi_downloads(creator_id, db)

    async def _check_expired_kofi_users(self, creator_id: int, db: Session) -> None:
        """Check for expired Ko-fi users and apply grace periods"""
        kofi_sync = KofiSyncService(db)
        return await kofi_sync.check_expired_kofi_users(creator_id)

    def _calculate_kofi_expiry_date(self, payment_date: datetime) -> datetime:
        """Calculate expiry date based on calendar month"""
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