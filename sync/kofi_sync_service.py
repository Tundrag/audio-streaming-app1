# sync/kofi_sync_service.py - ENHANCED VERSION
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent))

import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from typing import List, Dict, Optional, Callable
from contextlib import contextmanager
import asyncio
import json
import httpx
from models import UserRole, User, CampaignTier, Campaign, KofiSettings
from dateutil.relativedelta import relativedelta
from kofi_service import kofi_service

logger = logging.getLogger(__name__)

class KofiSyncService:
    def __init__(self, db_factory: Optional[Callable] = None):
        # *** ADD THIS LINE AT THE VERY TOP ***
        logger.info("🚀 NEW BULK API Ko-fi Sync Service v2.0 - DEPLOYED AND ACTIVE")

        from database import SessionLocal
        self._db_factory = db_factory or SessionLocal
        self._last_sync = None
        self._sync_interval = timedelta(days=3)  # Every 3 days
        self._sync_in_progress = False
        self._sync_task = None
        self._stop_event = None
        self._sync_worker = None
        self._enabled = False

    @contextmanager
    def _db_session(self):
        """Context manager for database sessions"""
        db = self._db_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

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

                    # Get all active creators with Ko-fi settings
                    with self._db_session() as db:
                        creators = db.query(User).join(KofiSettings).filter(
                            and_(
                                User.role == UserRole.CREATOR,
                                User.is_active == True,
                                KofiSettings.creator_id == User.id
                            )
                        ).all()

                    for creator in creators:
                        if self._should_sync():
                            try:
                                logger.info(f"Running periodic Ko-fi sync for creator: {creator.username}")
                                if self._sync_worker:
                                    await self._sync_worker.queue_sync(creator.id)
                                else:
                                    await self.sync_kofi_users_with_bulk_api(creator.id)
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

    async def sync_kofi_users_with_bulk_api(self, creator_id: int, force: bool = False) -> List[Dict]:
        """
        ENHANCED: Sync Ko-fi users using BULK API calls for maximum efficiency
        This replaces individual API calls with one bulk call to get all users
        """
        try:
            if self._sync_in_progress:
                logger.info("Ko-fi sync already in progress, skipping")
                return []

            if not force and not self._should_sync():
                logger.info("Skipping Ko-fi sync - not due yet")
                return []

            self._sync_in_progress = True
            logger.info(f"🚀 Starting BULK Ko-fi API sync for creator {creator_id}")

            with self._db_session() as db:
                # Get Ko-fi settings for this creator
                kofi_settings = db.query(KofiSettings).filter(
                    KofiSettings.creator_id == creator_id
                ).first()

                if not kofi_settings:
                    logger.warning(f"No Ko-fi settings found for creator {creator_id}")
                    return []

                # Get the creator
                creator = db.query(User).filter(
                    and_(
                        User.id == creator_id,
                        User.role == UserRole.CREATOR,
                        User.is_active == True
                    )
                ).first()

                if not creator:
                    logger.error(f"Creator {creator_id} not found or inactive")
                    return []

                # *** NEW: Make ONE bulk API call to get ALL users ***
                google_sheet_url = kofi_settings.google_sheet_url or "https://curly-cloud-17fc.tkinrinde.workers.dev/webhook"

                logger.info(f"📊 Making BULK API call to: {google_sheet_url}?bulk=true")

                bulk_data = await self._make_bulk_api_call(google_sheet_url)

                if not bulk_data or bulk_data.get("status") != "success":
                    logger.error(f"Bulk API call failed: {bulk_data}")
                    return []

                # Process the bulk response
                api_users = bulk_data.get("users", [])
                total_users = bulk_data.get("total_users", 0)
                active_users = bulk_data.get("active_users", 0)
                expired_users = bulk_data.get("expired_users", 0)

                logger.info(f"📈 Bulk API returned: {total_users} total users ({active_users} active, {expired_users} expired)")

                # Get existing Ko-fi users from database for comparison
                existing_kofi_users = db.query(User).filter(
                    and_(
                        User.created_by == creator_id,
                        User.role == UserRole.KOFI,
                        User.is_active == True
                    )
                ).all()

                # Create email lookup for existing users
                existing_users_by_email = {user.email.lower(): user for user in existing_kofi_users}

                logger.info(f"💾 Found {len(existing_kofi_users)} existing Ko-fi users in database")

                # Process results
                updated_users = []
                processed_count = 0
                reset_count = 0
                expired_count = 0
                new_user_count = 0

                # Process each user from the bulk API response
                for api_user_data in api_users:
                    try:
                        email = api_user_data.get("email")
                        api_status = api_user_data.get("status")

                        if not email:
                            logger.warning("API user missing email, skipping")
                            continue

                        logger.info(f"🔄 Processing {email} - API status: {api_status}")

                        # Find existing user in database
                        existing_user = existing_users_by_email.get(email.lower())

                        if api_status == "active":
                            # User has active Ko-fi subscription/donation
                            if existing_user:
                                # Update existing user with activity-based reset logic
                                logger.info(f"✅ Updating existing active user: {email}")

                                current_data = existing_user.patreon_tier_data or {}

                                # Check for activity-based reset
                                activity_type = "subscription" if api_user_data.get("transactionType") == "subscription" else "donation"

                                # Use Ko-fi service with activity-based reset logic
                                # Convert API user data to patron_data format
                                patron_data = self._convert_api_user_to_patron_data(api_user_data)

                                updated_user = await kofi_service.initialize_patron_data(
                                    existing_user,
                                    patron_data,
                                    db,
                                    activity_type=activity_type
                                )

                                # Check if reset occurred
                                new_tier_data = updated_user.patreon_tier_data or {}
                                if new_tier_data.get('reset_triggered_by'):
                                    reset_count += 1
                                    logger.info(f"✅ Reset triggered for {email} via {activity_type}")

                                updated_users.append({
                                    'email': email,
                                    'tier': new_tier_data.get('title', 'Unknown'),
                                    'status': 'active',
                                    'reset': new_tier_data.get('reset_triggered_by') is not None,
                                    'action': 'updated_existing'
                                })
                            else:
                                # New active user - create them
                                logger.info(f"🆕 Creating new active user: {email}")

                                # Convert API data and create user using Ko-fi service logic
                                patron_data = self._convert_api_user_to_patron_data(api_user_data)
                                activity_type = "subscription" if api_user_data.get("transactionType") == "subscription" else "donation"

                                # Create new user
                                new_user = User(
                                    email=email,
                                    username=email.split('@')[0],
                                    role=UserRole.KOFI,
                                    created_by=creator.id,
                                    is_active=True
                                )
                                db.add(new_user)
                                db.flush()

                                # Initialize with Ko-fi data
                                updated_user = await kofi_service.initialize_patron_data(
                                    new_user,
                                    patron_data,
                                    db,
                                    activity_type=activity_type
                                )

                                new_user_count += 1
                                updated_users.append({
                                    'email': email,
                                    'tier': updated_user.patreon_tier_data.get('title', 'Unknown'),
                                    'status': 'active',
                                    'reset': False,
                                    'action': 'created_new'
                                })

                        elif api_status in ["expired", "not_found"]:
                            # Handle expired/not found users
                            if existing_user:
                                logger.info(f"⏰ Handling expired user: {email}")

                                # Check if user is currently in grace period
                                current_data = existing_user.patreon_tier_data or {}
                                current_status = current_data.get('patron_status')

                                if (existing_user.grace_period_ends_at and
                                    datetime.now(timezone.utc) < existing_user.grace_period_ends_at):
                                    logger.info(f"User {email} still in grace period, preserving")
                                    # Keep existing tier during grace period - no changes
                                    continue
                                else:
                                    # Grace period ended or never existed - assign free tier
                                    logger.info(f"Assigning free tier to fully expired user {email}")

                                    # Use assign_free_tier logic
                                    from kofi_routes import assign_free_tier
                                    await assign_free_tier(email, creator, None, db)

                                    expired_count += 1
                                    updated_users.append({
                                        'email': email,
                                        'tier': 'Free Ko-fi',
                                        'status': 'expired_patron',
                                        'reset': False,
                                        'action': 'moved_to_free'
                                    })
                            # If user doesn't exist in database but API shows expired/not_found, ignore

                        processed_count += 1

                    except Exception as e:
                        logger.error(f"Error processing Ko-fi user {email}: {str(e)}")
                        continue

                # Commit all changes - now within the context manager
                db.commit()
                self._last_sync = datetime.now(timezone.utc)

            logger.info(f"✅ BULK Ko-fi sync completed for creator {creator_id}")
            logger.info(f"   📊 Processed: {processed_count} users from API")
            logger.info(f"   🆕 New users created: {new_user_count}")
            logger.info(f"   🔄 Resets triggered: {reset_count}")
            logger.info(f"   ⏰ Expired to free: {expired_count}")
            logger.info(f"   🚀 Efficiency gain: {len(existing_kofi_users)} individual calls → 1 bulk call")

            return updated_users

        except Exception as e:
            logger.error(f"Error in bulk Ko-fi sync: {str(e)}")
            raise
        finally:
            self._sync_in_progress = False

    async def _make_bulk_api_call(self, google_sheet_url: str) -> Optional[Dict]:
        """Make bulk API call to get all Ko-fi users"""
        try:
            timeout = httpx.Timeout(120.0)  # Longer timeout for bulk call
            
            # Add cache busting parameters
            import time
            import random
            import uuid
            
            cache_buster = f"{int(time.time())}_{random.randint(1000, 9999)}"
            request_id = str(uuid.uuid4())[:8]
            
            params = {
                "bulk": "true",
                "_nocache": cache_buster,
                "debug_id": request_id
            }
            
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                headers = {
                    "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
                    "Pragma": "no-cache",
                    "Expires": "0"
                }
                
                logger.info(f"Making bulk API call with cache-buster: {cache_buster}")
                response = await client.get(google_sheet_url, params=params, headers=headers)
                
                logger.info(f"Bulk API response status: {response.status_code}")
                if response.status_code != 200:
                    logger.error(f"Bulk API error: {response.status_code} - {response.text}")
                    return None
                
                data = response.json()
                logger.info(f"Bulk API response: {json.dumps(data, indent=2)}")
                return data
                
        except Exception as e:
            logger.error(f"Error making bulk API call: {str(e)}")
            return None

    def _convert_api_user_to_patron_data(self, api_user_data: Dict) -> Dict:
        """Convert bulk API user data to patron_data format expected by kofi_service"""
        transaction = api_user_data.get("transaction", {})
        
        patron_data = {
            "email": api_user_data.get("email"),
            "patron_status": "active_patron",
            "from_name": transaction.get("fromName", ""),
            "amount": transaction.get("amount", 0),
            "currency": transaction.get("currency", "USD"),
            "kofi_transaction_id": transaction.get("transactionId", ""),
            "is_subscription": transaction.get("isSubscription", False),
            "is_first_subscription": transaction.get("isFirstSubscription", False),
            "tier_name": transaction.get("tierName", "Ko-fi Supporter"),
            "tier_data": {
                "title": transaction.get("tierName", "Ko-fi Supporter"),
                "amount_cents": int(float(transaction.get("amount", 0)) * 100),
                "description": f"Ko-fi {'Subscription' if transaction.get('isSubscription') else 'Support'}"
            },
            "last_payment_date": transaction.get("timestamp"),
            "days_since_payment": api_user_data.get("daysSincePayment", 0),
            "status": "active",
            "has_donations": api_user_data.get("hasDonations", False),
            "total_donations": api_user_data.get("totalDonations", 0),
            "donation_count": api_user_data.get("donationCount", 0)
        }
        
        return patron_data

    # Legacy method - redirect to new bulk sync
    async def sync_kofi_users_with_api(self, creator_id: int, force: bool = False) -> List[Dict]:
        """Legacy method - redirects to new bulk API sync"""
        logger.info("Redirecting legacy sync_kofi_users_with_api to new bulk sync")
        return await self.sync_kofi_users_with_bulk_api(creator_id, force)

    async def sync_kofi_tiers(self, creator_id: int, force: bool = False) -> List[Dict]:
        """Legacy method - redirects to new bulk API sync"""
        logger.info("Redirecting legacy sync_kofi_tiers to new bulk sync")
        return await self.sync_kofi_users_with_bulk_api(creator_id, force)

    async def sync_kofi_downloads(self, creator_id: int) -> Dict[str, int]:
        """
        SIMPLIFIED: Download sync now handled by Ko-fi service
        This just ensures tier settings are up to date
        """
        try:
            logger.info(f"Syncing Ko-fi download settings for creator {creator_id}")

            with self._db_session() as db:
                # Get all Ko-fi users
                users = db.query(User).filter(
                    and_(
                        User.created_by == creator_id,
                        User.role == UserRole.KOFI,
                        User.is_active == True
                    )
                ).all()

                # Get campaign tiers for reference
                campaign_tiers = {}
                for tier in db.query(CampaignTier).filter(
                    and_(
                        CampaignTier.creator_id == creator_id,
                        CampaignTier.is_active == True,
                        CampaignTier.platform_type == "KOFI"
                    )
                ).all():
                    campaign_tiers[tier.title.lower()] = {
                        'track_downloads': tier.track_downloads_allowed,
                        'album_downloads': tier.album_downloads_allowed,
                        'book_requests': getattr(tier, 'book_requests_allowed', 0),
                        'max_sessions': tier.max_sessions,
                        'chapters_per_request': getattr(tier, 'chapters_allowed_per_book_request', 0)
                    }

                updates = {"successful": 0, "failed": 0, "skipped": 0}

                for user in users:
                    try:
                        current_data = user.patreon_tier_data or {}
                        tier_title = current_data.get('title', '').lower()

                        # Skip users without Ko-fi flag
                        if not current_data.get('kofi_user', False):
                            updates["skipped"] += 1
                            continue

                        # Update tier settings if we have a matching tier
                        tier_settings = campaign_tiers.get(tier_title)
                        if tier_settings:
                            # Only update allowed amounts, preserve used amounts and reset logic
                            updated_data = current_data.copy()
                            updated_data.update({
                                'album_downloads_allowed': tier_settings['album_downloads'],
                                'track_downloads_allowed': tier_settings['track_downloads'],
                                'book_requests_allowed': tier_settings['book_requests'],
                                'max_sessions': tier_settings.get('max_sessions', 1),
                                'chapters_allowed_per_book_request': tier_settings.get('chapters_per_request', 0)
                            })

                            # Ensure used values exist (but don't reset them)
                            if 'album_downloads_used' not in updated_data:
                                updated_data['album_downloads_used'] = 0
                            if 'track_downloads_used' not in updated_data:
                                updated_data['track_downloads_used'] = 0
                            if 'book_requests_used' not in updated_data:
                                updated_data['book_requests_used'] = 0

                            user.patreon_tier_data = updated_data
                            updates["successful"] += 1
                        else:
                            updates["skipped"] += 1

                    except Exception as e:
                        updates["failed"] += 1
                        logger.error(f"Error updating Ko-fi user {user.email}: {str(e)}")
                        continue

                db.commit()
                logger.info(f"Ko-fi download sync completed. Updated: {updates['successful']}, Skipped: {updates['skipped']}, Failed: {updates['failed']}")
                return updates

        except Exception as e:
            logger.error(f"Error syncing Ko-fi downloads: {str(e)}")
            raise

    async def check_expired_kofi_users(self, creator_id: int) -> None:
        """
        SIMPLIFIED: Expiry checking now handled by bulk API calls
        This just handles any edge cases for local-only users
        """
        try:
            logger.info(f"Checking for expired Ko-fi users for creator {creator_id}")

            with self._db_session() as db:
                now = datetime.now(timezone.utc)

                # Get users who might have expired grace periods
                kofi_users = db.query(User).filter(
                    and_(
                        User.created_by == creator_id,
                        User.role == UserRole.KOFI,
                        User.is_active == True,
                        User.grace_period_ends_at.isnot(None),
                        User.grace_period_ends_at < now  # Grace period has ended
                    )
                ).all()

                expired_count = 0

                for user in kofi_users:
                    try:
                        current_data = user.patreon_tier_data or {}

                        # Skip users without Ko-fi flag
                        if not current_data.get('kofi_user', False):
                            continue

                        # Skip if already marked as expired
                        if current_data.get('patron_status') == 'expired_patron':
                            continue

                        logger.info(f"Grace period ended for {user.email}, assigning free tier")

                        # Use assign_free_tier to handle expired users properly
                        from kofi_routes import assign_free_tier
                        creator = db.query(User).filter(User.id == creator_id).first()
                        await assign_free_tier(user.email, creator, None, db)

                        expired_count += 1

                    except Exception as e:
                        logger.error(f"Error processing expired Ko-fi user {user.email}: {str(e)}")
                        continue

                db.commit()
                logger.info(f"Expired Ko-fi user check completed: {expired_count} users moved to free tier")

        except Exception as e:
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
        return await self.sync_kofi_users_with_bulk_api(creator_id, force=True)

    async def perform_manual_sync(self, creator_id: int) -> List[Dict]:
        """Manually trigger a tier sync"""
        if not self._enabled:
            logger.info("Ko-fi sync service is disabled, skipping manual sync")
            return []

        logger.info(f"Performing manual Ko-fi sync for creator {creator_id}")
        if self._sync_worker:
            return await self._sync_worker.queue_sync(creator_id, force=True)
        return await self.sync_kofi_users_with_bulk_api(creator_id, force=True)

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
            "next_sync": None,
            "sync_method": "bulk_api"  # Indicate we're using bulk API
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
        
        self._sync_interval = timedelta(days=3)  # Every 3 days
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

            await self._sync_queue.put({
                "task_id": task_id,
                "creator_id": creator_id,
                "initial": initial,
                "force": force,
                "queued_at": datetime.now(timezone.utc)
            })

            logger.info(f"📌 Queued {'initial' if initial else 'periodic'} Ko-fi BULK sync for creator {creator_id} (Queue size: {self._sync_queue.qsize()})")
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
            return True

        time_since_last = datetime.now(timezone.utc) - last_sync_time
        return time_since_last > self._sync_interval

    async def _sync_worker(self):
        """Background worker loop that processes sync operations from the queue."""
        while not self._stop_event.is_set():
            try:
                try:
                    task = await asyncio.wait_for(self._sync_queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    continue

                creator_id = task.get("creator_id")
                task_id = task.get("task_id")
                initial = task.get("initial", False)
                force = task.get("force", False)

                logger.info(f"🚀 Processing {'initial' if initial else 'periodic'} Ko-fi BULK sync for creator {creator_id}")

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

                    # *** ENHANCED: Use new bulk sync methods ***
                    kofi_sync = KofiSyncService(self.db_factory)
                    await kofi_sync.sync_kofi_users_with_bulk_api(creator_id, force=True)
                    await kofi_sync.sync_kofi_downloads(creator_id)
                    await kofi_sync.check_expired_kofi_users(creator_id)

                    # Mark the last sync time
                    self._last_sync[creator_id] = datetime.now(timezone.utc)

                    # Commit changes
                    db.commit()

                    logger.info(f"✅ Completed Ko-fi BULK sync for creator {creator_id}")

                except Exception as e:
                    db.rollback()
                    logger.error(f"Error in Ko-fi sync worker for creator {creator_id}: {str(e)}")
                    raise
                finally:
                    db.close()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected error in Ko-fi sync worker: {str(e)}")
                await asyncio.sleep(5)
            finally:
                self._sync_in_progress = False
                self._sync_queue.task_done()

        logger.info("Ko-fi sync worker stopped")