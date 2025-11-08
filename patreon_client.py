# patreon_client.py
import httpx
import logging
from typing import Optional, Dict, List, Any
from functools import lru_cache
from sqlalchemy.orm import Session
from sqlalchemy import and_
from models import Campaign
from datetime import datetime, timezone
import os
import asyncio
import json


logger = logging.getLogger(__name__)

class PatreonClient:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(PatreonClient, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, '_initialized'):
            self._initialized = False
            self.base_url = "https://www.patreon.com/api/oauth2/v2"
            self.timeout = httpx.Timeout(30.0, connect=30.0)
            self._credentials = None
            self._current_campaign_id = None

    def _get_db(self) -> Session:
        """Get a new database session"""
        from database import SessionLocal
        return SessionLocal()

    async def initialize_from_db(self, db: Session, creator_id: int) -> bool:
        """Initialize with credentials from primary campaign in database"""
        try:
            campaign = (
                db.query(Campaign)
                .filter(
                    Campaign.creator_id == creator_id,
                    Campaign.is_active == True,
                    Campaign.is_primary == True
                )
                .first()
            )
            if not campaign:
                logger.warning("No primary active campaign found during initialization")
                return False
            return await self.set_active_campaign(campaign)
        except Exception as e:
            logger.error(f"Error initializing from db: {str(e)}")
            return False

    async def _ensure_credentials(self, db_campaign_id: Optional[str] = None) -> bool:
        if db_campaign_id:
            db = self._get_db()
            try:
                campaign = db.query(Campaign).filter(Campaign.id == db_campaign_id).first()
                if campaign:
                    logger.info(f"Found campaign by ID {db_campaign_id}: {campaign.name}")
                    return await self.set_active_campaign(campaign)
            finally:
                db.close()

        # Log current state
        logger.info(f"Current state - initialized: {self._initialized}, has_credentials: {bool(self._credentials)}")

        if self._initialized and self._credentials:
            self._credentials_source = f"db_campaign_{self._current_campaign_id}"
            logger.info(f"Using existing credentials for campaign {self._current_campaign_id}")
            return True

        # Try loading primary campaign
        success = await self._load_primary_campaign()
        if success:
            logger.info(f"Loaded primary campaign: {self._current_campaign_id}")
            return True

        # Fallback to env vars
        env_creds = await self._get_env_credentials()
        if env_creds:
            self._credentials = env_creds
            self._initialized = True
            self._credentials_source = "environment"
            logger.info("Using credentials from environment variables")
            return True

        logger.error("No valid credentials available from any source")
        return False


    async def ensure_initialized(self, db_campaign_id: Optional[str] = None) -> bool:
        if db_campaign_id:
            db = self._get_db()
            try:
                campaign = db.query(Campaign).filter(Campaign.id == db_campaign_id).first()
                if campaign:
                    return await self.set_active_campaign(campaign)
            finally:
                db.close()
        elif self._initialized and self._credentials:
            return True
        return await self._load_primary_campaign()

    async def _get_env_credentials(self) -> Optional[Dict]:
        """Get credentials from environment variables"""
        access_token = os.getenv('PATREON_ACCESS_TOKEN')
        campaign_id = os.getenv('PATREON_CAMPAIGN_ID')
        if access_token and campaign_id:
            return {
                'access_token': access_token,
                'campaign_id': campaign_id,
                'name': 'ENV_DEFAULT',
                'db_id': None
            }
        return None

    async def _load_primary_campaign(self) -> bool:
        """Load credentials from primary campaign in database"""
        db = self._get_db()
        try:
            campaign = (
                db.query(Campaign)
                .filter(
                    and_(
                        Campaign.is_active == True,
                        Campaign.is_primary == True
                    )
                )
                .first()
            )
            if not campaign:
                logger.warning("No primary active campaign found")
                return False
            return await self.set_active_campaign(campaign)
        except Exception as e:
            logger.error(f"Error loading primary campaign: {str(e)}")
            return False
        finally:
            db.close()

    async def set_active_campaign(self, campaign: Campaign) -> bool:
        """Set the active campaign and load its credentials"""
        try:
            if not campaign.access_token:
                logger.error("Campaign has no access token")
                return False
                
            # Store both internal ID and Patreon campaign ID
            self._credentials = {
                'access_token': campaign.access_token,
                'refresh_token': campaign.refresh_token,
                'internal_id': str(campaign.id),  # Our internal DB id
                'campaign_id': campaign.id,  # Patreon's ID for API calls
                'webhook_secret': campaign.webhook_secret,
                'client_id': campaign.client_id,
                'client_secret': campaign.client_secret,
                'name': campaign.name,
                'db_id': str(campaign.id)
            }
            
            # Store internal ID as current_campaign_id
            self._current_campaign_id = str(campaign.id)
            self._initialized = True
            logger.info(f"Set active campaign: {campaign.name} (ID: {campaign.id})")
            return True
            
        except Exception as e:
            logger.error(f"Error setting active campaign: {str(e)}")
            return False

    async def switch_campaign(self, db: Session, db_campaign_id: str) -> bool:
        """Switch to a different campaign"""
        try:
            campaign = (
                db.query(Campaign)
                .filter(Campaign.id == db_campaign_id)
                .first()
            )
            if not campaign:
                logger.error(f"Campaign not found with ID: {db_campaign_id}")
                return False
            return await self.set_active_campaign(campaign)
        except Exception as e:
            logger.error(f"Error switching campaign: {str(e)}")
            return False

    async def set_campaign_as_primary(self, db: Session, db_campaign_id: str) -> bool:
        """Set a campaign as primary, updating all other campaigns"""
        try:
            new_primary = (
                db.query(Campaign)
                .filter(Campaign.id == db_campaign_id)
                .first()
            )
            if not new_primary:
                logger.error(f"Campaign not found with ID: {db_campaign_id}")
                return False
            db.query(Campaign).filter(
                Campaign.creator_id == new_primary.creator_id,
                Campaign.id != db_campaign_id
            ).update({
                "is_primary": False,
                "updated_at": datetime.now(timezone.utc)
            })
            new_primary.is_primary = True
            new_primary.is_active = True
            new_primary.updated_at = datetime.now(timezone.utc)
            db.commit()
            logger.info(f"Set campaign {db_campaign_id} as primary")
            return await self.set_active_campaign(new_primary)
        except Exception as e:
            db.rollback()
            logger.error(f"Error setting primary campaign: {str(e)}")
            return False

    async def get_campaign_members(self, db_campaign_id: Optional[str] = None) -> List[Dict]:
        if not await self._ensure_credentials(db_campaign_id):
            logger.error("Failed to get valid credentials")
            return []
        
        headers = {
            "Authorization": f"Bearer {self._credentials['access_token']}",
            "Accept": "application/json"
        }
        
        params = {
            "include": "user,currently_entitled_tiers",
            "fields[member]": "full_name,email,patron_status,currently_entitled_amount_cents,last_charge_status",
            "fields[user]": "email,full_name",
            "fields[tier]": "title,amount_cents",
            "page[count]": "100"
        }
        
        all_members = []
        next_url = f"{self.base_url}/campaigns/{self._credentials['campaign_id']}/members"
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                while next_url:
                    logger.info(f"Fetching members from: {next_url}")
                    response = await client.get(next_url, headers=headers, params=params)
                    
                    if response.status_code == 429:
                        # Get retry after time from response
                        retry_after = int(response.json()
                            .get("errors", [{}])[0]
                            .get("retry_after_seconds", 30))
                        logger.info(f"Rate limited. Waiting {retry_after} seconds...")
                        await asyncio.sleep(retry_after)
                        continue
                    
                    if response.status_code != 200:
                        logger.error(f"Failed to get members: Status {response.status_code}")
                        logger.error(f"Response: {response.text}")
                        break
                    
                    data = response.json()
                    all_members.extend(data.get("data", []))
                    next_url = data.get("links", {}).get("next")
                    params = None if next_url else params
                    
                    # Add a small delay between pages to avoid rate limiting
                    if next_url:
                        await asyncio.sleep(2)  # Wait 2 seconds between pages
                
                return all_members
            
        except Exception as e:
            logger.error(f"Error fetching members: {str(e)}")
            return []

    async def verify_patron(self, email: str) -> Optional[Dict]:
        """Verify patron with Patreon API"""
        logger.info(f"Starting verify_patron for email: {email}")
        
        if not await self._ensure_credentials():
            logger.error("Failed to get valid credentials")
            return None

        headers = {
            "Authorization": f"Bearer {self._credentials['access_token']}",
            "Accept": "application/json"
        }
        
        patreon_campaign_id = self._credentials.get('campaign_id')
        if not patreon_campaign_id:
            logger.error("No campaign ID found in credentials")
            return None

        try:
            # Get campaign tiers first
            tier_params = {
                "fields[tier]": "title,amount_cents,description,patron_count,published",
                "include": "tiers"
            }
            
            campaign_url = f"{self.base_url}/campaigns/{patreon_campaign_id}"
            logger.info("Fetching campaign tiers...")

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                # Get campaign tiers
                response = await client.get(campaign_url, headers=headers, params=tier_params)
                if response.status_code != 200:
                    logger.error(f"Failed to get campaign tiers: Status {response.status_code}")
                    return None

                campaign_data = response.json()
                campaign_tiers = {tier["id"]: tier["attributes"]
                                  for tier in campaign_data.get("included", [])
                                  if tier["type"] == "tier" and tier["attributes"].get("published", True)}
                logger.info(f"Campaign tiers found: {json.dumps(campaign_tiers, indent=2)}")

                # Now get member details with corrected parameters
                params = {
                    "fields[member]": (
                        "full_name,email,patron_status,currently_entitled_amount_cents,"
                        "last_charge_status,last_charge_date,next_charge_date"
                    ),
                    "fields[tier]": "title,amount_cents,description",
                    "include": "currently_entitled_tiers",
                    "page[count]": "100"
                }

                url = f"{self.base_url}/campaigns/{patreon_campaign_id}/members"
                email = email.lower()
                
                all_members = []
                next_url = url
                
                while next_url:
                    logger.info(f"Fetching members from: {next_url}")
                    response = await client.get(next_url, headers=headers, params=params)
                    if response.status_code != 200:
                        logger.error(f"Failed to get patron details: Status {response.status_code}")
                        logger.error(f"Response body: {response.text}")
                        return None

                    data = response.json()
                    all_members.extend(data.get("data", []))
                    next_url = data.get("links", {}).get("next")
                    if next_url:
                        params = None

                    logger.info(f"Processing {len(all_members)} members")
                    for member in all_members:
                        attributes = member.get("attributes", {})
                        member_email = attributes.get("email", "").lower()

                        if member_email == email:
                            patron_status = attributes.get("patron_status")
                            logger.info(f"Found member with matching email: {email}")
                            logger.info(f"Member raw data: {json.dumps(member, indent=2)}")
                            
                            if patron_status != "active_patron":
                                logger.warning(f"Found member but status is not active: {patron_status}")
                                return None

                            # Get all attributes directly
                            current_amount = attributes.get("currently_entitled_amount_cents", 0)
                            last_charge_date = attributes.get("last_charge_date")
                            next_charge_date = attributes.get("next_charge_date")

                            # Get tier from relationships
                            relationships = member.get("relationships", {})
                            entitled_tiers = relationships.get("currently_entitled_tiers", {}).get("data", [])
                            logger.info(f"All entitled tiers: {json.dumps(entitled_tiers, indent=2)}")

                            # Find current tier info
                            current_tier = None
                            max_amount = 0
                            
                            # Look through all entitled tiers to find highest one
                            for tier_entry in entitled_tiers:
                                tier_id = tier_entry.get("id")
                                if tier_id in campaign_tiers:
                                    tier_data = campaign_tiers[tier_id]
                                    tier_amount = tier_data.get("amount_cents", 0)
                                    if tier_amount >= max_amount:  # Use >= to keep latest highest tier
                                        current_tier = tier_data
                                        max_amount = tier_amount
                                        logger.info(f"Found higher tier: {tier_data['title']} ({tier_amount} cents)")

                            # If we found a tier in campaign tiers, use that
                            if current_tier:
                                logger.info(f"Using highest entitled tier: {current_tier['title']}")
                                # Normalize tier title
                                if 'title' in current_tier:
                                    current_tier['title'] = current_tier['title'].strip()
                            else:
                                # Fallback to included data
                                included = data.get("included", [])
                                for inc in included:
                                    if inc.get("type") == "tier" and inc.get("id") == tier_id:
                                        current_tier = inc.get("attributes", {})
                                        # Normalize tier title
                                        if 'title' in current_tier:
                                            current_tier['title'] = current_tier['title'].strip()
                                        break
                                    
                                # If still no tier, use campaign tiers
                                if not current_tier and tier_id in campaign_tiers:
                                    current_tier = campaign_tiers[tier_id]
                                    # Normalize tier title
                                    if 'title' in current_tier:
                                        current_tier['title'] = current_tier['title'].strip()

                                # Last resort - create basic info
                                if not current_tier:
                                    current_tier = {
                                        "title": "Free" if current_amount == 0 else f"${current_amount/100:.2f} Patron",
                                        "amount_cents": current_amount,
                                        "description": f"Custom pledge amount: ${current_amount/100:.2f}"
                                    }

                            logger.info(
                                f"Final patron details:\n"
                                f"Current tier: {current_tier['title']} (${current_amount/100:.2f})\n"
                                f"Last charge: {last_charge_date}\n"
                                f"Next charge: {next_charge_date}"
                            )

                            return {
                                "patron_id": member.get("id"),
                                "full_name": attributes.get("full_name"),
                                "email": member_email,
                                # Root level patron data
                                "patron_status": patron_status,
                                "last_charge_status": attributes.get("last_charge_status"),
                                "last_charge_date": last_charge_date,
                                "next_charge_date": next_charge_date,
                                "currently_entitled_amount_cents": current_amount,
                                # Tier data
                                "tier_data": {
                                    "title": current_tier["title"].strip(),  # Add .strip() here
                                    "amount_cents": current_tier["amount_cents"],
                                    "description": current_tier.get("description", "")
                                },
                                "campaign_id": patreon_campaign_id
                            }

                logger.warning(f"No patron found with email: {email}")
                return None

        except Exception as e:
            logger.error(f"Error getting patron details: {str(e)}", exc_info=True)
            return None
    def _process_tier_info(self, member: Dict, included: List[Dict]) -> Dict:
        """Helper method to process tier information from member data"""
        tier_info = {"title": "Patron", "amount_cents": 0}
        
        try:
            relationships = member.get("relationships", {})
            entitled_tiers = relationships.get("currently_entitled_tiers", {}).get("data", [])
            
            if entitled_tiers:
                tier_id = entitled_tiers[0]["id"]
                for included_item in included:
                    if included_item.get("type") == "tier" and included_item.get("id") == tier_id:
                        attributes = included_item.get("attributes", {})
                        tier_info = {
                            "title": attributes.get("title", "Patron").strip(),  # Add .strip() here
                            "amount_cents": attributes.get("amount_cents", 0)
                        }
                        break
        except Exception as e:
            logger.error(f"Error processing tier info: {str(e)}")
            
        return tier_info

    async def get_campaign_tiers(self, db_campaign_id: Optional[str] = None) -> List[Dict]:
        if not await self.ensure_initialized(db_campaign_id):
            logger.error("PatreonClient not initialized with campaign")
            return []
        try:
            headers = {
                "Authorization": f"Bearer {self._credentials['access_token']}",
                "Accept": "application/json"
            }
            members = await self.get_campaign_members(db_campaign_id)
            tier_counts = {}
            for member in members:
                attributes = member.get("attributes", {})
                if attributes.get("patron_status") == "active_patron":
                    relationships = member.get("relationships", {})
                    entitled_tiers = relationships.get("currently_entitled_tiers", {}).get("data", [])
                    if entitled_tiers:
                        tier_id = entitled_tiers[0]["id"]
                        tier_counts[tier_id] = tier_counts.get(tier_id, 0) + 1
            params = {
                "fields[tier]": "title,amount_cents,description,patron_count,published,created_at,edited_at",
                "include": "tiers"
            }
            url = f"{self.base_url}/campaigns/{self._credentials['campaign_id']}"
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, headers=headers, params=params)
                if response.status_code != 200:
                    logger.error(f"Failed to get tiers: Status {response.status_code}")
                    logger.error(f"Response: {response.text}")
                    return []
                data = response.json()
                included = data.get("included", [])
                tiers = []
                for item in included:
                    if item["type"] == "tier":
                        attrs = item["attributes"]
                        if attrs.get("published", True):
                            tier_data = {
                                "id": item["id"],
                                "title": attrs.get("title", "").strip(),  # Add .strip() here
                                "amount_cents": attrs.get("amount_cents", 0),
                                "description": attrs.get("description", ""),
                                "patron_count": tier_counts.get(item["id"], 0)
                            }
                            tiers.append(tier_data)
                tiers.sort(key=lambda x: x["amount_cents"])
                for i, tier in enumerate(tiers):
                    tier["level"] = i + 1
                logger.info(f"Successfully fetched {len(tiers)} tiers with actual patron counts")
                return tiers
        except Exception as e:
            logger.error(f"Error getting campaign tiers: {str(e)}")
            return []

    def _process_member_data(self, member: Dict, included: List[Dict]) -> Optional[Dict]:
        try:
            attributes = member.get("attributes", {})
            patron_status = attributes.get("patron_status")
            if patron_status != "active_patron":
                logger.warning(f"Found member but status is not active: {patron_status}")
                return None
            tier_info = {}
            relationships = member.get("relationships", {})
            entitled_tiers = relationships.get("currently_entitled_tiers", {}).get("data", [])
            if entitled_tiers:
                tier_id = entitled_tiers[0]["id"]
                for inc in included:
                    if inc["type"] == "tier" and inc["id"] == tier_id:
                        tier_info = {
                            "title": inc["attributes"]["title"].strip(),  # Add .strip() here
                            "amount_cents": inc["attributes"]["amount_cents"]
                        }
                        break
            return {
                "patron_id": member.get("id"),
                "full_name": attributes.get("full_name"),
                "tier_data": {
                    "amount_cents": attributes.get("currently_entitled_amount_cents", 0),
                    "patron_status": patron_status,
                    "last_charge_status": attributes.get("last_charge_status"),
                    "last_charge_date": attributes.get("last_charge_date"),
                    "next_charge_date": attributes.get("next_charge_date"),
                    "title": tier_info.get("title", "Patron").strip()  # Add .strip() here
                }
            }
        except Exception as e:
            logger.error(f"Error processing member data: {str(e)}")
            return None

    @property
    def current_campaign_id(self) -> Optional[str]:
        return self._current_campaign_id

    @property
    def is_ready(self) -> bool:
        return (
            self._initialized and 
            self._credentials and 
            self._credentials.get('campaign_id') and
            self._credentials.get('access_token')
        )

    @property
    def current_status(self) -> dict:
        base_status = {
            "status": "not_initialized" if not self._initialized else
                     "no_credentials" if not self._credentials else
                     "ready" if self.is_ready else "needs_sync",
            "campaign_name": self._credentials.get('name') if self._credentials else None,
            "has_patreon_id": bool(self._credentials.get('campaign_id')) if self._credentials else False,
            "db_campaign_id": self._current_campaign_id,
            "credentials_source": self._credentials_source
        }
        if self._credentials_source == "environment":
            base_status["using_fallback"] = True
            base_status["campaign_id_source"] = "PATREON_CAMPAIGN_ID env var"
        elif self._credentials_source and self._credentials_source.startswith("db_campaign_"):
            base_status["using_fallback"] = False
            base_status["campaign_id_source"] = f"Database campaign {self._current_campaign_id}"
        return base_status

    async def get_patron_details(self, email: str, db_campaign_id: Optional[str] = None) -> Optional[Dict]:
        if not await self.ensure_initialized(db_campaign_id):
            logger.error("Failed to initialize PatreonClient")
            return None
        try:
            headers = {
                "Authorization": f"Bearer {self._credentials['access_token']}",
                "Accept": "application/json"
            }
            params = {
                "fields[member]": "full_name,email,patron_status,currently_entitled_amount_cents,last_charge_status,last_charge_date,next_charge_date",
                "fields[tier]": "title,amount_cents",
                "include": "currently_entitled_tiers",
                "filter[email]": email.lower()
            }
            url = f"{self.base_url}/campaigns/{self._credentials['campaign_id']}/members"
            email = email.lower()
            logger.info(f"Looking for patron with email: {email}")
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                while url:
                    logger.info(f"Fetching URL: {url}")
                    response = await client.get(url, headers=headers, params=params)
                    if response.status_code != 200:
                        logger.error(f"Failed to get patron details: Status {response.status_code}")
                        return None
                    data = response.json()
                    members = data.get("data", [])
                    for member in members:
                        attributes = member.get("attributes", {})
                        member_email = attributes.get("email", "").lower()
                        if member_email == email:
                            patron_status = attributes.get("patron_status")
                            logger.info(f"Found member with matching email: {email}")
                            logger.info(f"Patron status: {patron_status}")
                            if patron_status != "active_patron":
                                logger.warning(f"Found member but status is not active: {patron_status}")
                                return None
                            tier_info = {}
                            relationships = member.get("relationships", {})
                            entitled_tiers = relationships.get("currently_entitled_tiers", {}).get("data", [])
                            if entitled_tiers:
                                tier_id = entitled_tiers[0]["id"]
                                for included in data.get("included", []):
                                    if included["type"] == "tier" and included["id"] == tier_id:
                                        tier_info = {
                                            "title": included["attributes"]["title"].strip(),  # Add .strip() here
                                            "amount_cents": included["attributes"]["amount_cents"]
                                        }
                                        break
                            return {
                                "patron_id": member.get("id"),
                                "full_name": attributes.get("full_name"),
                                "email": member_email,
                                "tier_data": {
                                    "amount_cents": attributes.get("currently_entitled_amount_cents", 0),
                                    "patron_status": patron_status,
                                    "last_charge_status": attributes.get("last_charge_status"),
                                    "last_charge_date": attributes.get("last_charge_date"),
                                    "next_charge_date": attributes.get("next_charge_date"),
                                    "title": tier_info.get("title", "Patron").strip(),  # Add .strip() here
                                    "tier_amount_cents": tier_info.get("amount_cents", 0)
                                },
                                "campaign_id": self._credentials['campaign_id']
                            }
                    logger.warning(f"No patron found with email: {email}")
                    return None
        except Exception as e:
            logger.error(f"Error getting patron details: {str(e)}")
            return None

    async def get_creator_campaigns(self, db: Session, creator_id: int) -> List[Dict]:
        """Get all active campaigns for a creator with their Patreon campaign IDs"""
        try:
            campaigns = (
                db.query(Campaign)
                .filter(
                    Campaign.creator_id == creator_id,
                    Campaign.is_active == True
                )
                .order_by(Campaign.is_primary.desc(), Campaign.created_at.desc())
                .all()
            )
            
            return [{
                "id": str(c.id),  # Internal database ID
                "name": c.name,
                "patreon_campaign_id": c.id,  # This is the actual Patreon campaign ID
                "is_primary": c.is_primary,
                "is_active": c.is_active,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None
            } for c in campaigns]
            
        except Exception as e:
            logger.error(f"Error getting creator campaigns: {str(e)}")
            return []
# Create singleton instance
patreon_client = PatreonClient()