# sync/sync_worker.py
from typing import Dict, Optional, List
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from uuid import uuid4
from sqlalchemy import and_, or_, func
from models import User, UserRole, Campaign, CampaignTier
from patreon_client import patreon_client
from dateutil.relativedelta import relativedelta

logger = logging.getLogger(__name__)

class PatreonSyncWorker:
    def __init__(self, background_manager, db_factory):
        self.background_manager = background_manager
        self.db_factory = db_factory
        self._sync_queue = asyncio.Queue()
        self._worker_task = None
        self._stop_event = asyncio.Event()
        
        self._sync_interval = timedelta(hours=5)
        self._lock = asyncio.Lock()
        self._enabled = True
        
        # We store the last sync time per-creator to avoid spamming the API
        self._last_sync: Dict[int, datetime] = {}
        
        # Used to track whether a sync operation is currently running
        self._sync_in_progress = False

    async def start(self):
        """Start the sync worker loop in a background task."""
        if self._worker_task:
            logger.warning("⚠️ Sync worker already running!")
            return

        self._worker_task = asyncio.create_task(self._sync_worker())
        logger.info("✅ Started Patreon sync worker")

    async def stop(self):
        """Stop the sync worker gracefully."""
        if self._worker_task:
            self._stop_event.set()
            try:
                await asyncio.wait_for(self._worker_task, timeout=30.0)
            except asyncio.TimeoutError:
                self._worker_task.cancel()
            self._worker_task = None
            logger.info("✅ Stopped Patreon sync worker")

    async def queue_sync(self, creator_id: int, initial: bool = False, force: bool = False) -> Dict:
        """
        Enqueue a sync operation for a creator.
        
        :param creator_id: The user ID of the creator to sync.
        :param initial: Whether this is an initial sync (ignores usual interval).
        :param force: Whether to force the sync, ignoring the interval check.
        :return: Dictionary with the queue status.
        """
        if not self._enabled:
            logger.warning(f"⚠️ Sync disabled, skipping sync for creator {creator_id}")
            return {"status": "disabled", "message": "Sync service is disabled"}

        async with self._lock:
            task_id = f"sync_{creator_id}_{datetime.now(timezone.utc).timestamp()}"

            # Check if we should skip the sync due to interval
            if not force and not initial and not self._should_sync(creator_id):
                logger.info(f"🚫 Skipping sync for creator {creator_id} - not due yet")
                return {
                    "task_id": task_id,
                    "status": "skipped",
                    "reason": "recent_sync"
                }

            # If a sync is already in progress, just queue this behind it
            if self._sync_in_progress:
                position = self._sync_queue.qsize() + 1
                logger.info(f"🔁 Another sync is in progress for creator {creator_id}, queueing behind it")
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

            logger.info(f"📌 Queued {'initial' if initial else 'periodic'} sync for creator {creator_id} (Queue size: {self._sync_queue.qsize()})")
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
            # No previous sync => must sync
            return True

        time_since_last = datetime.now(timezone.utc) - last_sync_time
        return time_since_last > self._sync_interval

    async def _sync_worker(self):
        """Background worker loop that processes sync operations from the queue."""
        while not self._stop_event.is_set():
            try:
                # Log the queue size for debugging
                logger.info(f"🟢 Sync queue size: {self._sync_queue.qsize()}")

                # Wait up to 30 seconds for a sync task
                try:
                    task = await asyncio.wait_for(self._sync_queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    logger.info("🔄 No sync tasks available, waiting...")
                    continue

                # Extract task info safely using .get()
                creator_id = task.get("creator_id")
                task_id = task.get("task_id")
                initial = task.get("initial", False)
                force = task.get("force", False)

                logger.info(f"Processing {'initial' if initial else 'periodic'} sync for creator {creator_id}")

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

                    # Get the current tiers for logging or debugging
                    current_tiers = db.query(CampaignTier).filter(
                        CampaignTier.creator_id == creator_id
                    ).all()
                    logger.info(f"Found {len(current_tiers)} existing tiers for creator {creator_id}")

                    # Perform the actual sync operations
                    updated_members = await self._sync_campaign_tiers(creator_id, db)
                    await self._sync_patron_downloads(creator_id, db)

                    # Mark the last sync time
                    self._last_sync[creator_id] = datetime.now(timezone.utc)

                    # Commit changes
                    db.commit()

                    logger.info(f"✅ Completed sync for creator {creator_id} - Updated {len(updated_members)} members")

                except Exception as e:
                    db.rollback()
                    logger.error(f"Error in sync worker for creator {creator_id}: {str(e)}")
                    raise
                finally:
                    db.close()

            except asyncio.CancelledError:
                # Worker was stopped
                break
            except Exception as e:
                logger.error(f"Unexpected error in sync worker: {str(e)}")
                await asyncio.sleep(5)
            finally:
                self._sync_in_progress = False
                self._sync_queue.task_done()

        logger.info("Sync worker stopped")

    async def _sync_campaign_tiers(self, creator_id: int, db: Session) -> List[Dict]:
        """Sync members with existing campaign tiers"""
        try:
            primary_campaign = db.query(Campaign).filter(
                Campaign.creator_id == creator_id,
                Campaign.is_active == True,
                Campaign.is_primary == True
            ).first()

            if not primary_campaign:
                logger.error(f"No primary active campaign found for creator {creator_id}")
                return []

            logger.info(f"Starting member sync for primary campaign {primary_campaign.name}")

            # Get existing tiers (no longer creating/updating tiers)
            existing_tiers = db.query(CampaignTier).filter(
                and_(
                    CampaignTier.creator_id == creator_id,
                    func.upper(CampaignTier.platform_type) == "PATREON",
                    CampaignTier.is_active == True
                )
            ).order_by(CampaignTier.amount_cents.desc()).all()

            logger.info(f"Found {len(existing_tiers)} existing tiers for assignment")

            # Make sure we have a free tier
            free_tier = next((t for t in existing_tiers if t.amount_cents == 0), None)
            if not free_tier:
                # Create free tier
                free_tier = CampaignTier(
                    creator_id=creator_id,
                    title="Free Patreon",
                    description="Free access for Patreon subscribers",
                    amount_cents=0,
                    patron_count=0,
                    platform_type="PATREON",
                    is_active=True,
                    album_downloads_allowed=0,
                    track_downloads_allowed=0,
                    max_sessions=1,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc)
                )
                db.add(free_tier)
                db.flush()
                existing_tiers.append(free_tier)
                logger.info(f"Created free tier for creator {creator_id}")

            # Find or create team member tier (using PATREON platform type)
            team_tier = db.query(CampaignTier).filter(
                and_(
                    CampaignTier.creator_id == creator_id,
                    CampaignTier.title == "Team Member",
                    CampaignTier.is_active == True
                )
            ).first()

            if not team_tier:
                # Create team tier with a valid platform type (PATREON)
                team_tier = CampaignTier(
                    creator_id=creator_id,
                    title="Team Member",
                    description="Special access for team members",
                    amount_cents=0,
                    patron_count=0,
                    platform_type="PATREON",  # Using PATREON as the platform type
                    is_active=True,
                    album_downloads_allowed=0,  # Default values - adjust as needed
                    track_downloads_allowed=0,  # Default values - adjust as needed
                    max_sessions=1,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc)
                )
                db.add(team_tier)
                db.flush()
                logger.info(f"Created Team Member tier for creator {creator_id}")

            # Reset tier counts
            for tier in existing_tiers:
                tier.patron_count = 0
            if team_tier and team_tier not in existing_tiers:
                team_tier.patron_count = 0

            # Handle team members first - team is a user role, not a platform type
            team_members = db.query(User).filter(
                and_(
                    User.created_by == creator_id,
                    User.role == UserRole.TEAM,
                    User.is_active == True
                )
            ).all()

            logger.info(f"Found {len(team_members)} team members to process")

            # Update team members
            team_updated_members = []
            for member in team_members:
                try:
                    current_data = member.patreon_tier_data or {}

                    # Preserve download counts
                    album_downloads_used = current_data.get('album_downloads_used', 0)
                    track_downloads_used = current_data.get('track_downloads_used', 0)

                    # Update with team tier data
                    member.patreon_tier_data = {
                        'title': team_tier.title,
                        'amount_cents': 0,
                        'patron_status': 'active_patron',
                        'album_downloads_allowed': team_tier.album_downloads_allowed,
                        'track_downloads_allowed': team_tier.track_downloads_allowed,
                        'album_downloads_used': album_downloads_used,
                        'track_downloads_used': track_downloads_used,
                        'period_start': datetime.now(timezone.utc).isoformat(),
                        'max_sessions': team_tier.max_sessions
                    }

                    member.is_active = True
                    member.last_sync = datetime.now(timezone.utc)

                    # Ensure user has campaign ID
                    if primary_campaign and not member.campaign_id:
                        member.campaign_id = str(primary_campaign.id)

                    team_tier.patron_count += 1
                    team_updated_members.append(member)
                    logger.info(f"Updated team member: {member.email}")
                except Exception as e:
                    logger.error(f"Error processing team member {member.email}: {str(e)}")

            # Retrieve members from Patreon API
            try:
                members = await patreon_client.get_members()
                logger.info(f"Retrieved {len(members)} members from Patreon API")
            except Exception as e:
                logger.error(f"Error fetching members from Patreon: {str(e)}")
                return await self._fallback_member_sync(creator_id, primary_campaign.id, existing_tiers, db)

            # Process each member
            updated_members = []
            for member_data in members:
                try:
                    email = member_data.get("email", "").lower()
                    amount_cents = member_data.get("currently_entitled_amount_cents", 0)
                    patron_status = member_data.get("patron_status")

                    if not email or patron_status != "active_patron":
                        continue

                    # Find or create user
                    user = db.query(User).filter(
                        and_(
                            func.lower(User.email) == email,
                            User.created_by == creator_id
                        )
                    ).first()

                    if not user:
                        user = User(
                            email=email,
                            username=member_data.get("full_name") or email.split('@')[0],
                            role=UserRole.PATREON,
                            created_by=creator_id,
                            campaign_id=primary_campaign.id,
                            is_active=True,
                            created_at=datetime.now(timezone.utc)
                        )
                        db.add(user)
                        db.flush()

                    # Skip team members - they've already been processed
                    if user.role == UserRole.TEAM:
                        logger.info(f"Skipping team member {email} in Patreon processing")
                        continue

                    # Ensure user is PATREON role
                    if user.role != UserRole.PATREON:
                        user.role = UserRole.PATREON

                    # Find matching tier based on amount
                    matching_tier = None
                    for tier in existing_tiers:
                        if amount_cents >= tier.amount_cents:
                            matching_tier = tier
                            break

                    if not matching_tier and existing_tiers:
                        # Use free tier as fallback
                        matching_tier = free_tier

                    if matching_tier:
                        # Update user's tier data
                        current_data = user.patreon_tier_data or {}

                        # Preserve existing download counts
                        album_downloads_used = current_data.get('album_downloads_used', 0)
                        track_downloads_used = current_data.get('track_downloads_used', 0)

                        # Update tier data
                        user.patreon_tier_data = {
                            'title': matching_tier.title,
                            'amount_cents': amount_cents,
                            'patron_status': patron_status,
                            'last_charge_status': member_data.get('last_charge_status'),
                            'last_charge_date': member_data.get('last_charge_date'),
                            'next_charge_date': member_data.get('next_charge_date'),
                            'album_downloads_allowed': matching_tier.album_downloads_allowed,
                            'album_downloads_used': album_downloads_used,
                            'track_downloads_allowed': matching_tier.track_downloads_allowed,
                            'track_downloads_used': track_downloads_used,
                            'period_start': datetime.now(timezone.utc).isoformat(),
                            'max_sessions': matching_tier.max_sessions
                        }

                        user.is_active = True
                        user.last_sync = datetime.now(timezone.utc)

                        # Increment patron count for this tier
                        matching_tier.patron_count += 1

                        updated_members.append(user)
                        logger.info(f"Updated user {email} to tier: {matching_tier.title}")
                    else:
                        logger.warning(f"No matching tier found for {email}")
                except Exception as e:
                    logger.error(f"Error processing member {member_data.get('email', 'unknown')}: {str(e)}")
                    continue

            # Handle members not in the API response
            await self._handle_expired_members(creator_id, db, [m.get('email') for m in members])

            # Log tier counts after processing
            for tier in existing_tiers:
                logger.info(f"Tier '{tier.title}' has {tier.patron_count} patrons after sync")
            if team_tier and team_tier not in existing_tiers:
                logger.info(f"Team Member tier has {team_tier.patron_count} members")

            # Combine team members with updated members
            all_updated_members = team_updated_members + updated_members

            db.commit()
            return all_updated_members

        except Exception as e:
            logger.error(f"Error in tier sync: {str(e)}")
            db.rollback()
            raise
            
    async def _fallback_member_sync(self, creator_id: int, campaign_id: str, existing_tiers: List[CampaignTier], db: Session) -> List[Dict]:
        """Fallback sync when Patreon API fails"""
        try:
            logger.info(f"Using fallback member sync for creator {creator_id}")

            # Make sure we have a free tier
            free_tier = next((t for t in existing_tiers if t.amount_cents == 0), None)
            if not free_tier:
                logger.error("No free tier found and can't create one in fallback sync")
                free_tier = existing_tiers[-1]  # Use any tier as fallback if no free tier

            # Get team member tier or create it using a valid platform type (PATREON)
            team_tier = db.query(CampaignTier).filter(
                and_(
                    CampaignTier.creator_id == creator_id,
                    CampaignTier.title == "Team Member",
                    CampaignTier.is_active == True
                )
            ).first()

            if not team_tier:
                # Create team tier with a valid platform type
                team_tier = CampaignTier(
                    creator_id=creator_id,
                    title="Team Member",
                    description="Special access for team members",
                    amount_cents=0,
                    patron_count=0,
                    platform_type="PATREON",  # Use PATREON as the platform type
                    is_active=True,
                    album_downloads_allowed=10,  # Set appropriate values
                    track_downloads_allowed=50,  # Set appropriate values
                    max_sessions=3,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc)
                )
                db.add(team_tier)
                db.flush()
                logger.info(f"Created Team Member tier for creator {creator_id}")

            # Reset tier counts
            for tier in existing_tiers:
                tier.patron_count = 0
            if team_tier and team_tier not in existing_tiers:
                team_tier.patron_count = 0

            # Handle team members first - team is a user role, not a platform type
            team_members = db.query(User).filter(
                and_(
                    User.created_by == creator_id,
                    User.role == UserRole.TEAM,
                    User.is_active == True
                )
            ).all()

            logger.info(f"Processing {len(team_members)} team members")

            team_updated_members = []
            for member in team_members:
                try:
                    current_data = member.patreon_tier_data or {}

                    # Preserve download counts
                    album_downloads_used = current_data.get('album_downloads_used', 0)
                    track_downloads_used = current_data.get('track_downloads_used', 0)

                    # Update with team tier data
                    member.patreon_tier_data = {
                        'title': team_tier.title,
                        'amount_cents': 0,
                        'patron_status': 'active_patron',
                        'album_downloads_allowed': team_tier.album_downloads_allowed,
                        'album_downloads_used': album_downloads_used,
                        'track_downloads_allowed': team_tier.track_downloads_allowed,
                        'track_downloads_used': track_downloads_used,
                        'period_start': datetime.now(timezone.utc).isoformat(),
                        'max_sessions': team_tier.max_sessions
                    }

                    member.is_active = True
                    member.last_sync = datetime.now(timezone.utc)

                    team_tier.patron_count += 1
                    team_updated_members.append(member)
                    logger.info(f"Updated team member {member.email} using fallback sync")
                except Exception as e:
                    logger.error(f"Error processing team member {member.email} in fallback: {str(e)}")
                    continue

            # Get all existing patrons
            patrons = db.query(User).filter(
                and_(
                    User.created_by == creator_id,
                    User.role == UserRole.PATREON,
                    User.is_active == True
                )
            ).all()

            logger.info(f"Found {len(patrons)} existing patrons to check")

            updated_members = []
            for patron in patrons:
                try:
                    current_data = patron.patreon_tier_data or {}

                    # Check if patron has payment data
                    is_gift = current_data.get('is_gift', False)
                    amount_cents = current_data.get('amount_cents', 0)
                    tier_title = current_data.get('title', '')
                    has_payment_data = False
                    last_payment_date = None

                    # Safely handle last_charge_date
                    if 'last_charge_date' in current_data and current_data['last_charge_date']:
                        try:
                            last_charge_date_str = current_data['last_charge_date']
                            if isinstance(last_charge_date_str, str):
                                if 'Z' in last_charge_date_str:
                                    last_charge_date_str = last_charge_date_str.replace('Z', '+00:00')
                                last_payment_date = datetime.fromisoformat(last_charge_date_str)
                                has_payment_data = True
                                logger.info(f"Found last payment date: {last_payment_date.isoformat()}")
                        except (ValueError, TypeError) as e:
                            logger.error(f"Error parsing last_charge_date: {str(e)}")

                    # Determine if this patron is still active
                    is_active = False

                    # Case 1: Gift member - always active
                    if is_gift:
                        is_active = True
                        logger.info(f"User {patron.email} has gift membership - keeping active")

                    # Case 2: Has payment data - check if still within period
                    elif has_payment_data and last_payment_date:
                        now = datetime.now(timezone.utc)
                        expiry_date = last_payment_date + relativedelta(months=1)
                        is_active = now < expiry_date

                        if is_active:
                            logger.info(f"User subscription still active until {expiry_date.isoformat()}")
                        else:
                            logger.info(f"Patron {patron.email} has expired subscription - using free tier")

                    # Find matching tier
                    matching_tier = None

                    if is_active:
                        # First try to match by title
                        if tier_title:
                            matching_tier = next(
                                (t for t in existing_tiers if t.title.lower() == tier_title.lower()),
                                None
                            )

                        # If no tier by title and has amount, match by amount
                        if not matching_tier and amount_cents > 0:
                            for tier in existing_tiers:
                                if amount_cents >= tier.amount_cents:
                                    matching_tier = tier
                                    break

                    # If not active or no tier found, use free tier
                    if not is_active or not matching_tier:
                        matching_tier = free_tier

                    # Update patron data
                    if matching_tier:
                        # Get current download counts
                        album_downloads_used = current_data.get('album_downloads_used', 0)
                        track_downloads_used = current_data.get('track_downloads_used', 0)

                        # Create updated tier data
                        updated_data = {
                            'title': matching_tier.title,
                            'amount_cents': amount_cents if is_active else 0,
                            'patron_status': 'active_patron' if is_active else 'expired_patron',
                            'album_downloads_allowed': matching_tier.album_downloads_allowed,
                            'album_downloads_used': album_downloads_used,
                            'track_downloads_allowed': matching_tier.track_downloads_allowed,
                            'track_downloads_used': track_downloads_used,
                            'period_start': datetime.now(timezone.utc).isoformat(),
                            'max_sessions': matching_tier.max_sessions,
                            'is_gift': is_gift
                        }

                        # Preserve payment history if we have it
                        if has_payment_data:
                            updated_data['last_charge_date'] = current_data.get('last_charge_date')
                            updated_data['last_charge_status'] = current_data.get('last_charge_status')
                            updated_data['next_charge_date'] = current_data.get('next_charge_date')

                        # Update patron
                        patron.patreon_tier_data = updated_data
                        patron.last_sync = datetime.now(timezone.utc)

                        # Increment patron count for this tier
                        matching_tier.patron_count += 1

                        updated_members.append(patron)

                        if is_active:
                            logger.info(f"Updated patron {patron.email} using fallback sync")
                        else:
                            logger.info(f"Patron {patron.email} has expired subscription - using free tier")
                except Exception as e:
                    logger.error(f"Error processing patron {patron.email} in fallback: {str(e)}")
                    continue

            # Log tier counts after processing
            for tier in existing_tiers:
                logger.info(f"Tier '{tier.title}' has {tier.patron_count} patrons after fallback sync")
            if team_tier and team_tier not in existing_tiers:
                logger.info(f"Team Member tier has {team_tier.patron_count} members")

            # Combine team and patron updates
            all_updated_members = team_updated_members + updated_members

            db.commit()
            logger.info(f"Fallback member sync completed. Updated {len(all_updated_members)} members")

            return all_updated_members

        except Exception as e:
            db.rollback()
            logger.error(f"Error in fallback member sync: {str(e)}")
            return []

    async def _handle_expired_members(self, creator_id: int, db: Session, active_emails: List[str]):
        """Handle members who are no longer in the API response"""
        try:
            # Get users who are not in active emails list
            expired_patrons = db.query(User).filter(
                and_(
                    User.created_by == creator_id,
                    User.role == UserRole.PATREON,
                    User.is_active == True,
                    ~func.lower(User.email).in_([email.lower() for email in active_emails if email])
                )
            ).all()

            logger.info(f"Found {len(expired_patrons)} patrons who are no longer active")

            # Find free tier if exists
            free_tier = db.query(CampaignTier).filter(
                and_(
                    CampaignTier.creator_id == creator_id,
                    CampaignTier.platform_type == "PATREON",
                    CampaignTier.amount_cents == 0,
                    CampaignTier.is_active == True
                )
            ).first()

            for patron in expired_patrons:
                current_data = patron.patreon_tier_data or {}

                # Mark as expired
                current_data['patron_status'] = 'expired_patron'

                # If free tier exists, assign to it
                if free_tier:
                    current_data.update({
                        'title': free_tier.title,
                        'amount_cents': 0,
                        'album_downloads_allowed': free_tier.album_downloads_allowed,
                        'album_downloads_used': current_data.get('album_downloads_used', 0),
                        'track_downloads_allowed': free_tier.track_downloads_allowed,
                        'track_downloads_used': current_data.get('track_downloads_used', 0),
                        'max_sessions': free_tier.max_sessions
                    })

                    # Increment free tier patron count
                    free_tier.patron_count += 1

                patron.patreon_tier_data = current_data
                logger.info(f"Marked patron {patron.email} as expired")
        except Exception as e:
            logger.error(f"Error handling expired members: {str(e)}")

    async def _sync_patron_downloads(self, creator_id: int, db: Session):
        """Sync patron downloads for a creator (example: track/album downloads)."""
        try:
            # Get all tiers for this creator with valid platform types
            all_tiers = db.query(CampaignTier).filter(
                and_(
                    CampaignTier.creator_id == creator_id,
                    CampaignTier.is_active == True,
                    # Only use valid platform types from the enum
                    or_(
                        func.upper(CampaignTier.platform_type) == "PATREON", 
                        func.upper(CampaignTier.platform_type) == "KOFI"
                    )
                )
            ).all()
            
            # Create lookup dictionary by title
            campaign_tiers = {
                tier.title.lower(): {
                    'track_downloads': tier.track_downloads_allowed,
                    'album_downloads': tier.album_downloads_allowed,
                    'max_sessions': tier.max_sessions
                }
                for tier in all_tiers
            }

            # Get team member tier separately
            team_tier = next((t for t in all_tiers if t.title.lower() == "team member"), None)
            
            # Identify all users under this creator
            users = db.query(User).filter(
                and_(
                    User.created_by == creator_id,
                    User.is_active == True
                )
            ).all()

            updated_count = 0
            for user in users:
                try:
                    current_data = user.patreon_tier_data or {}
                    
                    # Determine which tier to use based on user role
                    if user.role == UserRole.TEAM and team_tier:
                        # Use the team tier for team members
                        tier_settings = {
                            'track_downloads': team_tier.track_downloads_allowed,
                            'album_downloads': team_tier.album_downloads_allowed,
                            'max_sessions': team_tier.max_sessions
                        }
                        tier_title = "Team Member"
                    else:
                        # For regular patrons, use their assigned tier
                        tier_title = current_data.get('title', '').lower()
                        tier_settings = campaign_tiers.get(tier_title)
                    
                    # Only update if tier settings were found
                    if tier_settings:
                        # Create updated tier data, preserving other fields
                        updated_data = current_data.copy()
                        
                        # Update only the settings that need updating
                        updated_data.update({
                            'album_downloads_allowed': tier_settings['album_downloads'],
                            'track_downloads_allowed': tier_settings['track_downloads'],
                            'max_sessions': tier_settings.get('max_sessions', 1),
                            'period_start': current_data.get('period_start') or datetime.now(timezone.utc).isoformat()
                        })
                        
                        # Make sure download counts exist
                        if 'album_downloads_used' not in updated_data:
                            updated_data['album_downloads_used'] = 0
                        if 'track_downloads_used' not in updated_data:
                            updated_data['track_downloads_used'] = 0
                        
                        # Update the user's tier data
                        user.patreon_tier_data = updated_data
                        updated_count += 1
                        logger.info(f"Updated download settings for {user.email} (role: {user.role})")

                except Exception as e:
                    logger.error(f"Error updating user {user.email}: {str(e)}")
                    continue

            db.commit()
            logger.info(f"Successfully synced download settings for {updated_count} users")
            return {"updated": updated_count}

        except Exception as e:
            logger.error(f"Error syncing patron downloads: {str(e)}")
            db.rollback()
            raise
            
    async def cleanup_duplicate_tiers(self, db: Session, creator_id: int) -> Dict[str, int]:
        """
        Utility function to clean up duplicate tiers for a given creator.
        Returns a dict with cleanup stats.
        """
        try:
            # Find titles that appear more than once
            duplicates = db.query(CampaignTier.title).filter(
                CampaignTier.creator_id == creator_id
            ).group_by(CampaignTier.title).having(func.count(CampaignTier.id) > 1).all()

            stats = {"cleaned": 0, "errors": 0}

            for (title,) in duplicates:
                try:
                    # Grab all tiers with this title
                    tiers = db.query(CampaignTier).filter(
                        CampaignTier.creator_id == creator_id,
                        CampaignTier.title == title
                    ).order_by(CampaignTier.created_at).all()

                    # Keep the first one, delete the rest
                    if len(tiers) > 1:
                        for tier in tiers[1:]:
                            db.delete(tier)
                            stats["cleaned"] += 1
                            logger.info(f"Deleted duplicate tier: {tier.title} (ID: {tier.id})")

                except Exception as e:
                    logger.error(f"Error cleaning up tier {title}: {str(e)}")
                    stats["errors"] += 1
                    continue

            db.commit()
            logger.info(f"Cleanup complete: {stats}")
            return stats

        except Exception as e:
            logger.error(f"Error in cleanup_duplicate_tiers: {str(e)}")
            db.rollback()
            raise