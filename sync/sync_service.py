# sync/sync_service.py
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent))

import logging
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import and_, func
from typing import Callable, List, Dict, Optional
from models import UserRole, User, CampaignTier
import asyncio
from .sync_worker import PatreonSyncWorker
from patreon_client import patreon_client
from models import Campaign
from sqlalchemy import and_, or_, func
from dateutil.relativedelta import relativedelta
from database import SessionLocal


logger = logging.getLogger(__name__)

class PatreonSyncService:
    def __init__(self, db_factory: Optional[Callable] = None):
        self._db_factory = db_factory or SessionLocal
        self._last_sync = None
        self._sync_interval = timedelta(hours=5)
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
        """Initialize the sync service"""
        self._enabled = enabled
        self._worker = sync_worker
        if not enabled:
            logger.info("Sync service disabled")
            return
        logger.info("Initialized sync service")

    async def start_periodic_task(self):
        """Start the periodic sync task"""
        if not self._enabled:
            logger.info("Sync service is disabled, skipping periodic task")
            return

        if self._sync_task is not None:
            logger.warning("Periodic sync already running")
            return

        self._stop_event = asyncio.Event()
        self._sync_task = asyncio.create_task(self._periodic_sync())
        logger.info("Started periodic sync task")

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
            logger.info("Stopped periodic sync task")

    async def _periodic_sync(self):
        """Periodic sync background task"""
        try:
            while not self._stop_event.is_set():
                try:
                    if not self._enabled:
                        await asyncio.sleep(60)
                        continue

                    # Get all active creators with campaigns
                    with self._db_session() as db:
                        creators = db.query(User).filter(
                            and_(
                                User.role == UserRole.CREATOR,
                                User.is_active == True,
                                User.campaign_id.isnot(None)
                            )
                        ).all()

                    for creator in creators:
                        if self._should_sync():
                            try:
                                logger.info(f"Running periodic sync for creator: {creator.username}")
                                if self._sync_worker:
                                    await self._sync_worker.queue_sync(creator.id)
                                else:
                                    await self.sync_campaign_tiers(creator.id)
                                    await self.sync_patron_downloads(creator.id)
                            except Exception as e:
                                logger.error(f"Error in sync for creator {creator.username}: {str(e)}")
                                continue

                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(),
                            timeout=self._sync_interval.total_seconds()
                        )
                    except asyncio.TimeoutError:
                        continue

                except Exception as e:
                    logger.error(f"Error in periodic sync: {str(e)}")
                    await asyncio.sleep(60)

        except asyncio.CancelledError:
            logger.info("Periodic sync task cancelled")
        except Exception as e:
            logger.error(f"Fatal error in periodic sync: {str(e)}")
        finally:
            self._sync_task = None
            logger.info("Periodic sync task stopped")

    async def sync_campaign_tiers(self, creator_id: int, force: bool = False) -> List[Dict]:
        """Sync Patreon members with existing campaign tiers"""
        try:
            if self._sync_in_progress:
                logger.info("Sync already in progress, skipping")
                return []

            if not force and not self._should_sync():
                logger.info("Skipping member sync - not due yet")
                return []

            self._sync_in_progress = True
            logger.info(f"Starting Patreon member sync for creator {creator_id}")

            with self._db_session() as db:
                # Get the primary campaign
                primary_campaign = db.query(Campaign).filter(
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

                # Get existing tiers from the database (ONLY fetch, don't create or modify)
                existing_tiers = db.query(CampaignTier).filter(
                    and_(
                        CampaignTier.creator_id == creator_id,
                        CampaignTier.platform_type == "PATREON",
                        CampaignTier.is_active == True
                    )
                ).order_by(CampaignTier.amount_cents).all()

                logger.info(f"Found {len(existing_tiers)} existing Patreon tiers in database")

                # Sort tiers by amount in descending order for proper assignment
                # (we want to assign the highest qualifying tier)
                existing_tiers.sort(key=lambda x: x.amount_cents, reverse=True)

                # Fetch members from Patreon API
                try:
                    members = await patreon_client.get_members()
                    logger.info(f"Fetched {len(members)} members from Patreon API")
                except Exception as e:
                    logger.error(f"Error fetching members from Patreon API: {str(e)}")
                    # Use fallback if Patreon API fails
                    return await self._fallback_member_sync(creator_id, primary_campaign.id, existing_tiers)

                # Process each member
                updated_members = []
                for member_data in members:
                    try:
                        email = member_data.get("email", "").lower()
                        amount_cents = member_data.get("currently_entitled_amount_cents", 0)
                        patron_status = member_data.get("patron_status")

                        if not email or patron_status != "active_patron":
                            logger.info(f"Skipping inactive/invalid member: {email} (Status: {patron_status})")
                            continue

                        # Find user in database
                        user = db.query(User).filter(
                            and_(
                                func.lower(User.email) == email,
                                User.created_by == creator_id
                            )
                        ).first()

                        # Create user if doesn't exist
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
                            db.flush()  # Get user ID
                            logger.info(f"Created new user: {email}")

                        # Ensure correct role
                        if user.role != UserRole.PATREON:
                            user.role = UserRole.PATREON

                        # Find matching tier based on amount paid
                        matching_tier = None
                        for tier in existing_tiers:
                            if amount_cents >= tier.amount_cents:
                                matching_tier = tier
                                break

                        if not matching_tier and existing_tiers:
                            # Use the lowest tier as fallback if no tier matches
                            lowest_tier = sorted(existing_tiers, key=lambda x: x.amount_cents)[0]
                            if lowest_tier.amount_cents == 0:
                                matching_tier = lowest_tier
                                logger.info(f"Using free tier for {email} - no matching tier found")

                        if matching_tier:
                            # Update user's tier data
                            current_data = user.patreon_tier_data or {}

                            # Keep track of existing download usage
                            album_downloads_used = current_data.get('album_downloads', {}).get('used', 0)
                            track_downloads_used = current_data.get('track_downloads', {}).get('used', 0)

                            user.patreon_tier_data = {
                                'title': matching_tier.title,
                                'amount_cents': amount_cents,
                                'patron_status': patron_status,
                                'last_charge_status': member_data.get('last_charge_status'),
                                'last_charge_date': member_data.get('last_charge_date'),
                                'track_downloads': {
                                    'allowed': matching_tier.track_downloads_allowed,
                                    'used': track_downloads_used
                                },
                                'album_downloads': {
                                    'allowed': matching_tier.album_downloads_allowed,
                                    'used': album_downloads_used
                                },
                                'period_start': datetime.now(timezone.utc).isoformat(),
                                'max_sessions': matching_tier.max_sessions
                            }

                            # Set campaign ID if not set
                            if not user.campaign_id:
                                user.campaign_id = primary_campaign.id

                            user.is_active = True
                            user.last_sync = datetime.now(timezone.utc)

                            updated_members.append({
                                'email': user.email,
                                'tier': matching_tier.title,
                                'amount_cents': amount_cents
                            })

                            logger.info(f"Updated user {email} to tier: {matching_tier.title}")
                        else:
                            logger.warning(f"No matching tier found for {email} (amount: {amount_cents})")

                    except Exception as e:
                        logger.error(f"Error processing member {member_data.get('email', 'unknown')}: {str(e)}")
                        continue

                # Handle members not in API response (expired/cancelled)
                await self._handle_expired_members(creator_id, [m.get('email') for m in members])

                # Handle team members separately by creating/updating Team Member tier for them
                await self._handle_team_members(creator_id, primary_campaign.id)

                # commit happens in context manager
                self._last_sync = datetime.now(timezone.utc)
                logger.info(f"Member sync completed. Updated {len(updated_members)} members")

                return updated_members

        except Exception as e:
            logger.error(f"Error in member sync: {str(e)}")
            raise
        finally:
            self._sync_in_progress = False

    async def _handle_team_members(self, creator_id: int, campaign_id: str):
        """Handle team members with a dedicated tier"""
        try:
            with self._db_session() as db:
                # Get team members for this creator
                team_members = db.query(User).filter(
                    and_(
                        User.created_by == creator_id,
                        User.role == UserRole.TEAM,
                        User.is_active == True
                    )
                ).all()

                if not team_members:
                    logger.info(f"No team members found for creator {creator_id}")
                    return

                logger.info(f"Found {len(team_members)} team members to process")

                # Find or create team member tier (using PATREON as platform type)
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
                        album_downloads_allowed=10,  # Default values, adjust as needed
                        track_downloads_allowed=50,  # Default values, adjust as needed
                        max_sessions=3,
                        created_at=datetime.now(timezone.utc),
                        updated_at=datetime.now(timezone.utc)
                    )
                    db.add(team_tier)
                    db.flush()
                    logger.info(f"Created Team Member tier for creator {creator_id}")

                # Reset team tier count
                team_tier.patron_count = 0

                # Update each team member's tier data
                for member in team_members:
                    try:
                        current_data = member.patreon_tier_data or {}

                        # Preserve existing download counts
                        album_downloads_used = 0
                        track_downloads_used = 0

                        # Check for old and new format
                        if 'album_downloads' in current_data and isinstance(current_data['album_downloads'], dict):
                            album_downloads_used = current_data['album_downloads'].get('used', 0)
                            track_downloads_used = current_data['track_downloads'].get('used', 0)
                        else:
                            album_downloads_used = current_data.get('album_downloads_used', 0)
                            track_downloads_used = current_data.get('track_downloads_used', 0)

                        # Update to use flat format
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

                        # Set campaign ID if not set
                        if not member.campaign_id:
                            member.campaign_id = campaign_id

                        member.is_active = True
                        member.last_sync = datetime.now(timezone.utc)

                        # Increment team tier count
                        team_tier.patron_count += 1

                        logger.info(f"Updated team member {member.email}")

                    except Exception as e:
                        logger.error(f"Error processing team member {member.email}: {str(e)}")
                        continue

                logger.info(f"Processed {team_tier.patron_count} team members")

        except Exception as e:
            logger.error(f"Error handling team members: {str(e)}")

    async def _fallback_member_sync(self, creator_id: int, campaign_id: str, existing_tiers: List[CampaignTier]) -> List[Dict]:
        """Fallback sync when Patreon API fails - uses calendar month logic for expiration"""
        try:
            logger.info(f"Using fallback member sync for creator {creator_id}")

            with self._db_session() as db:
                # Make sure we have a free tier
                free_tier = next((t for t in existing_tiers if t.amount_cents == 0), None)
                if not free_tier:
                    # Try to find or create a free tier
                    free_tier = db.query(CampaignTier).filter(
                        and_(
                            CampaignTier.creator_id == creator_id,
                            CampaignTier.platform_type == "PATREON",
                            CampaignTier.amount_cents == 0,
                            CampaignTier.is_active == True
                        )
                    ).first()

                    if not free_tier:
                        logger.error("No free tier found and can't create one in fallback sync")
                        free_tier = sorted(existing_tiers, key=lambda x: x.amount_cents)[0] if existing_tiers else None
                        if not free_tier:
                            logger.error("No tiers available for fallback sync")
                            return []

                # Get team member tier or create it with a valid platform type
                team_tier = db.query(CampaignTier).filter(
                    and_(
                        CampaignTier.creator_id == creator_id,
                        CampaignTier.title == "Team Member",
                        CampaignTier.is_active == True
                    )
                ).first()

                # Create team tier if it doesn't exist
                if not team_tier:
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

                # Get all existing patrons for this creator
                patrons = db.query(User).filter(
                    and_(
                        User.created_by == creator_id,
                        User.role == UserRole.PATREON,
                        User.is_active == True
                    )
                ).all()

                # Get team members too
                team_members = db.query(User).filter(
                    and_(
                        User.created_by == creator_id,
                        User.role == UserRole.TEAM,
                        User.is_active == True
                    )
                ).all()
            
                # Reset tier counts
                for tier in existing_tiers:
                    tier.patron_count = 0
                team_tier.patron_count = 0

                # Handle team members first
                updated_team_members = []
                for member in team_members:
                    try:
                        current_data = member.patreon_tier_data or {}

                        # Preserve existing download counts
                        album_downloads_used = 0
                        track_downloads_used = 0

                        # Check for old and new format
                        if 'album_downloads' in current_data and isinstance(current_data['album_downloads'], dict):
                            album_downloads_used = current_data['album_downloads'].get('used', 0)
                            track_downloads_used = current_data['track_downloads'].get('used', 0)
                        else:
                            album_downloads_used = current_data.get('album_downloads_used', 0)
                            track_downloads_used = current_data.get('track_downloads_used', 0)

                        # Update with flat format
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
                        updated_team_members.append(member)
                        logger.info(f"Updated team member {member.email} in fallback sync")
                    except Exception as e:
                        logger.error(f"Error processing team member {member.email}: {str(e)}")

                logger.info(f"Found {len(patrons)} existing patrons to check")

                updated_members = []
                for patron in patrons:
                    try:
                        current_data = patron.patreon_tier_data or {}

                        # Check if patron has payment data
                        has_payment_data = False
                        last_payment_date = None
                        amount_cents = current_data.get('amount_cents', 0)
                        is_gift = current_data.get('is_gift', False)

                        # Safely handle last_charge_date - checking for None
                        if 'last_charge_date' in current_data and current_data['last_charge_date']:
                            try:
                                last_charge_date_str = current_data['last_charge_date']
                                if isinstance(last_charge_date_str, str):
                                    if 'Z' in last_charge_date_str:
                                        last_charge_date_str = last_charge_date_str.replace('Z', '+00:00')
                                    last_payment_date = datetime.fromisoformat(last_charge_date_str)
                                    has_payment_data = True
                                    logger.info(f"Found valid last payment date for {patron.email}: {last_payment_date.isoformat()}")
                            except (ValueError, TypeError) as e:
                                logger.error(f"Error parsing date for patron {patron.email}: {str(e)}")

                        # Determine if still active
                        is_active = False

                        # Case 1: Gift membership or specifically marked as gift
                        if is_gift:
                            is_active = True
                            logger.info(f"User {patron.email} has gift membership - keeping active")

                        # Case 2: Has valid payment data - check if payment is recent enough
                        elif has_payment_data and last_payment_date:
                            now = datetime.now(timezone.utc)
                            expiry_date = last_payment_date + relativedelta(months=1)
                            is_active = now < expiry_date

                            if is_active:
                                logger.info(f"User {patron.email} subscription still active until {expiry_date.isoformat()}")
                            else:
                                logger.info(f"Patron {patron.email} has expired subscription (last charge: {last_payment_date})")

                        # Find matching tier
                        matching_tier = None
                        if is_active:
                            # Keep current tier
                            tier_title = current_data.get('title', '').lower()

                            # First try to find by title
                            if tier_title:
                                matching_tier = next(
                                    (t for t in existing_tiers if t.title.lower() == tier_title.lower()),
                                    None
                                )

                            # If no match by title, try to match by amount
                            if not matching_tier and amount_cents > 0:
                                for tier in existing_tiers:
                                    if amount_cents >= tier.amount_cents:
                                        matching_tier = tier
                                        break

                        # If expired or no matching tier, use free tier
                        if not is_active or not matching_tier:
                            matching_tier = free_tier

                        if matching_tier:
                            # Preserve existing download counts
                            album_downloads_used = 0
                            track_downloads_used = 0

                            # Check for old nested format vs new flat format
                            if 'album_downloads' in current_data and isinstance(current_data['album_downloads'], dict):
                                album_downloads_used = current_data['album_downloads'].get('used', 0)
                                track_downloads_used = current_data['track_downloads'].get('used', 0)
                            else:
                                album_downloads_used = current_data.get('album_downloads_used', 0)
                                track_downloads_used = current_data.get('track_downloads_used', 0)

                            # Update to use flat format
                            patron.patreon_tier_data = {
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

                            # Preserve payment data if available
                            if has_payment_data:
                                patron.patreon_tier_data['last_charge_date'] = current_data.get('last_charge_date')
                                patron.patreon_tier_data['next_charge_date'] = current_data.get('next_charge_date')
                                patron.patreon_tier_data['last_charge_status'] = current_data.get('last_charge_status')

                            # Update patron data
                            patron.last_sync = datetime.now(timezone.utc)

                            # Increment patron count for this tier
                            matching_tier.patron_count += 1

                            updated_data = {
                                'email': patron.email,
                                'tier': matching_tier.title,
                                'amount_cents': amount_cents
                            }

                            updated_members.append(updated_data)

                            if is_active:
                                logger.info(f"Updated patron {patron.email} using fallback sync")
                            else:
                                logger.info(f"Patron {patron.email} has expired subscription - using free tier")
                        else:
                            logger.warning(f"No matching tier found for {patron.email} in fallback sync")
                    except Exception as e:
                        logger.error(f"Error processing patron {patron.email} in fallback: {str(e)}")
                        continue

                # commit happens in context manager
                self._last_sync = datetime.now(timezone.utc)
                logger.info(f"Fallback member sync completed. Updated {len(updated_members)} members")

                # Log tier counts
                for tier in existing_tiers:
                    logger.info(f"Tier '{tier.title}' has {tier.patron_count} patrons after fallback sync")
                logger.info(f"Team Member tier has {team_tier.patron_count} members")

                return updated_members

        except Exception as e:
            logger.error(f"Error in fallback member sync: {str(e)}")
            return []

    async def _handle_expired_members(self, creator_id: int, active_emails: List[str]):
        """Handle members who are no longer in the API response"""
        try:
            with self._db_session() as db:
                # Get all users for this creator who are not in the active list
                expired_patrons = db.query(User).filter(
                    and_(
                        User.created_by == creator_id,
                        User.role == UserRole.PATREON,
                        User.is_active == True,
                        ~func.lower(User.email).in_([email.lower() for email in active_emails if email])
                    )
                ).all()

                logger.info(f"Found {len(expired_patrons)} patrons not in API response")

                # Look for free tier
                free_tier = db.query(CampaignTier).filter(
                    and_(
                        CampaignTier.creator_id == creator_id,
                        CampaignTier.platform_type == "PATREON",
                        CampaignTier.is_active == True,
                        CampaignTier.amount_cents == 0
                    )
                ).first()

                for patron in expired_patrons:
                    try:
                        current_data = patron.patreon_tier_data or {}

                        # Mark as expired
                        current_data['patron_status'] = 'expired_patron'

                        # If we have a free tier, move them to it
                        if free_tier:
                            current_data['title'] = free_tier.title
                            current_data['amount_cents'] = 0

                            # Handle both formats (nested and flat)
                            if 'track_downloads' in current_data and isinstance(current_data['track_downloads'], dict):
                                current_data['track_downloads'] = {
                                    'allowed': free_tier.track_downloads_allowed,
                                    'used': current_data.get('track_downloads', {}).get('used', 0)
                                }
                                current_data['album_downloads'] = {
                                    'allowed': free_tier.album_downloads_allowed,
                                    'used': current_data.get('album_downloads', {}).get('used', 0)
                                }
                            else:
                                current_data['track_downloads_allowed'] = free_tier.track_downloads_allowed
                                current_data['album_downloads_allowed'] = free_tier.album_downloads_allowed

                            current_data['max_sessions'] = free_tier.max_sessions

                        patron.patreon_tier_data = current_data
                        logger.info(f"Marked patron {patron.email} as expired")

                    except Exception as e:
                        logger.error(f"Error handling expired patron {patron.email}: {str(e)}")
                        continue

        except Exception as e:
            logger.error(f"Error handling expired members: {str(e)}")

    async def perform_startup_sync(self, creator_id: int) -> List[Dict]:
        """Perform initial sync on startup"""
        if not self._enabled:
            logger.info("Sync service is disabled, skipping startup sync")
            return []

        logger.info(f"Performing startup tier sync for creator {creator_id}")
        if self._sync_worker:
            return await self._sync_worker.queue_sync(creator_id, initial=True)
        return await self.sync_campaign_tiers(creator_id, force=True)

    async def perform_manual_sync(self, creator_id: int, db: Session = None) -> List[Dict]:
        """Manually trigger a tier sync"""
        if not self._enabled:
            logger.info("Sync service is disabled, skipping manual sync")
            return []

        logger.info(f"Performing manual tier sync for creator {creator_id}")
        # No longer storing db session - using db_factory pattern
        if self._sync_worker:
            return await self._sync_worker.queue_sync(creator_id, force=True)
        return await self.sync_campaign_tiers(creator_id, force=True)

    def _should_sync(self) -> bool:
        """Check if sync is needed based on last sync time"""
        if not self._last_sync:
            logger.info("No previous sync found - initial sync required")
            return True
            
        time_since_last = datetime.now(timezone.utc) - self._last_sync
        time_left = self._sync_interval - time_since_last
        
        should_sync = time_since_last > self._sync_interval
        
        if should_sync:
            logger.info(f"Sync interval ({self._sync_interval.total_seconds()}s) exceeded - syncing now")
        else:
            logger.info(f"Next sync in {time_left.total_seconds():.0f} seconds")
            
        return should_sync

    async def sync_patron_downloads(self, creator_id: int, db: Session = None) -> Dict[str, int]:
        """Sync download settings for all patrons of a creator"""
        try:
            with self._db_session() as db:
                # Get all tiers with valid platform types
                campaign_tiers = {}
                for tier in db.query(CampaignTier).filter(
                    and_(
                        CampaignTier.creator_id == creator_id,
                        CampaignTier.is_active == True,
                        or_(
                            func.upper(CampaignTier.platform_type) == "PATREON",
                            func.upper(CampaignTier.platform_type) == "KOFI"
                        )
                    )
                ).all():
                    # Store tier settings by title
                    campaign_tiers[tier.title.lower()] = {
                        'track_downloads': tier.track_downloads_allowed,
                        'album_downloads': tier.album_downloads_allowed,
                        'max_sessions': tier.max_sessions
                    }

                # Check for team member tier
                team_tier_settings = campaign_tiers.get('team member')
                if not team_tier_settings:
                    # Find team member tier separately by title
                    team_tier = db.query(CampaignTier).filter(
                        and_(
                            CampaignTier.creator_id == creator_id,
                            CampaignTier.title == "Team Member",
                            CampaignTier.is_active == True
                        )
                    ).first()

                    if team_tier:
                        team_tier_settings = {
                            'track_downloads': team_tier.track_downloads_allowed,
                            'album_downloads': team_tier.album_downloads_allowed,
                            'max_sessions': team_tier.max_sessions
                        }
                        campaign_tiers['team member'] = team_tier_settings

                # Process all users with the creator
                users = db.query(User).filter(
                    and_(
                        User.created_by == creator_id,
                        User.is_active == True
                    )
                ).all()

                updates = {"successful": 0, "failed": 0, "skipped": 0}

                for user in users:
                    try:
                        current_data = user.patreon_tier_data or {}
                        tier_title = current_data.get('title', '').lower()

                        # Special handling for team members
                        tier_settings = None
                        if user.role == UserRole.TEAM and team_tier_settings:
                            tier_settings = team_tier_settings
                            tier_title = "team member"
                        elif tier_title in campaign_tiers:
                            tier_settings = campaign_tiers[tier_title]

                        # Only update if we have a matching tier
                        if tier_settings:
                            # Create a copy of current data to update
                            updated_data = current_data.copy()

                            # Update tier-based settings (using flat format)
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
                        logger.error(f"Error updating user {user.email}: {str(e)}")
                        continue

                # commit happens in context manager
                logger.info(f"Sync patron downloads completed. Updated: {updates['successful']}, Skipped: {updates['skipped']}, Failed: {updates['failed']}")
                return updates

        except Exception as e:
            logger.error(f"Error syncing patron downloads: {str(e)}")
            raise
            
    async def get_tier_settings(self, creator_id: int, tier_title: str) -> Optional[Dict]:
        """Get settings for a specific tier"""
        try:
            with self._db_session() as db:
                # First try exact match
                tier = db.query(CampaignTier).filter(
                    and_(
                        CampaignTier.creator_id == creator_id,
                        CampaignTier.title == tier_title,
                        CampaignTier.is_active == True
                    )
                ).first()

                # If not found, try case-insensitive match
                if not tier:
                    tier = db.query(CampaignTier).filter(
                        and_(
                            CampaignTier.creator_id == creator_id,
                            CampaignTier.title.ilike(tier_title),
                            CampaignTier.is_active == True
                        )
                    ).first()

                if tier:
                    return {
                        "title": tier.title,
                        "amount_cents": tier.amount_cents,
                        "patron_count": tier.patron_count,
                        "downloads_allowed": tier.downloads_allowed,
                        "description": tier.description
                    }

                return None

        except Exception as e:
            logger.error(f"Error getting tier settings: {str(e)}")
            return None

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
