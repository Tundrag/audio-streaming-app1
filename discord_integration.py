import aiohttp
import asyncio
import logging
import json
import time
from typing import List, Dict, Optional, Any, Union
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import desc

# Configure logging
logger = logging.getLogger(__name__)

# Discord API constants
DISCORD_CONTENT_LIMIT = 2000
DISCORD_EMBED_LIMIT = 6000
DISCORD_FIELDS_PER_EMBED = 25
DISCORD_EMBEDS_PER_MESSAGE = 10
DISCORD_RATE_LIMIT_DELAY = 1.5  # seconds between messages to avoid rate limits

class DiscordIntegration:
    """
    Complete Discord integration for audiobook management.
    Handles webhook notifications and audiobook list synchronization with optimized batching.
    Loads configuration from database instead of environment variables.
    """

    def __init__(self):
        """Initialize Discord integration with default values"""
        self.webhook_url = None
        self.webhook_id = None
        self.webhook_token = None
        self.bot_token = None
        self.initialized = False
        self.last_message_time = 0  # Used for rate limiting
        self.sync_message_ids = []  # Store message IDs from sync operations
        self.creator_id = None
        self.db_settings = None

        # Configure colors for different notification types
        self.colors = {
            "new_album": 0x3498db,    # Blue
            "update_album": 0x2ecc71, # Green
            "delete_album": 0xe74c3c, # Red
            "tier_change": 0x9b59b6,  # Purple
        }

    async def load_settings_from_db(self, db: Session, creator_id: int) -> bool:
        """Load Discord settings from database for a specific creator"""
        try:
            # Import models only when needed to avoid circular imports
            from models import DiscordSettings
            
            # Find settings for this creator
            settings = db.query(DiscordSettings).filter(DiscordSettings.creator_id == creator_id).first()
            
            if not settings:
                logger.info(f"No Discord settings found for creator {creator_id}")
                return False
                
            # Store reference to db_settings
            self.db_settings = settings
            self.creator_id = creator_id
            
            # Load settings
            self.webhook_url = settings.webhook_url
            self.webhook_id = settings.webhook_id
            self.webhook_token = settings.webhook_token
            self.bot_token = settings.bot_token
            
            # Load message IDs for cleanup
            if settings.sync_message_ids:
                self.sync_message_ids = settings.sync_message_ids
            
            # Check webhook URL validity
            if not self.webhook_url:
                logger.warning(f"Discord webhook URL not configured for creator {creator_id}")
                return False
                
            # Initialize is successful if we have a webhook URL
            self.initialized = bool(self.webhook_url)
            
            logger.info(f"Loaded Discord settings for creator {creator_id}. Initialized: {self.initialized}")
            return self.initialized
        
        except Exception as e:
            logger.error(f"Error loading Discord settings from DB: {str(e)}")
            return False

    async def save_settings_to_db(self, db: Session) -> bool:
        """Save current Discord settings to the database"""
        if not self.creator_id or not self.db_settings:
            logger.error("Cannot save Discord settings: no creator_id or db_settings available")
            return False
            
        try:
            # Update db_settings with current values
            self.db_settings.webhook_url = self.webhook_url
            self.db_settings.webhook_id = self.webhook_id
            self.db_settings.webhook_token = self.webhook_token
            self.db_settings.bot_token = self.bot_token
            self.db_settings.is_active = self.initialized
            self.db_settings.last_synced = datetime.now(timezone.utc)
            
            # Save sync message IDs for later cleanup
            self.db_settings.sync_message_ids = self.sync_message_ids
            
            # Commit changes
            db.commit()
            logger.info(f"Saved Discord settings for creator {self.creator_id}")
            return True
        
        except Exception as e:
            db.rollback()
            logger.error(f"Error saving Discord settings to DB: {str(e)}")
            return False

    async def initialize(self, db: Session = None, creator_id: int = None) -> bool:
        """
        Initialize the Discord integration.
        If db and creator_id are provided, loads from database, otherwise 
        initializes with default settings for backward compatibility.
        """
        # If database params are provided, load from database
        if db is not None and creator_id is not None:
            success = await self.load_settings_from_db(db, creator_id)
            
            if success:
                logger.info(f"Discord integration initialized for creator {creator_id}")
            else:
                logger.warning(f"Discord integration initialization failed for creator {creator_id}")
                
            return success
        
        # Backwards compatibility mode - just set initialized based on webhook URL
        else:
            logger.info("Discord integration initialized in backwards-compatible mode")
            self.initialized = bool(self.webhook_url)
            return self.initialized

    async def cleanup(self, db: Session) -> bool:
        """Cleanup resources and save settings to database"""
        result = await self.save_settings_to_db(db)
        logger.info(f"Discord integration cleanup completed, saved to DB: {result}")
        return result

    # ===== DATABASE QUERIES =====

    def get_album_list_from_db(self, db: Session) -> List[Dict]:
        """
        Query the database for all audiobooks with relevant information.
        """
        try:
            # Import models here to avoid circular imports
            from models import Album, Track, User

            # Query all albums with related information
            albums = db.query(Album).order_by(Album.title).all()

            result = []
            for album in albums:
                # Get track count for this album
                track_count = len(album.tracks) if hasattr(album, 'tracks') and album.tracks else db.query(Track).filter(Track.album_id == album.id).count()

                # Get creator username
                creator = None
                if album.created_by_id:
                    creator = db.query(User).filter(User.id == album.created_by_id).first()

                # Build album info dictionary
                album_info = {
                    "id": str(album.id),
                    "title": album.title,
                    "cover_path": album.cover_path,
                    "created_at": album.created_at.isoformat() if album.created_at else None,
                    "updated_at": album.updated_at.isoformat() if album.updated_at else None,
                    "track_count": track_count,
                    "creator": creator.username if creator else "Unknown Creator",
                    "creator_id": album.created_by_id,
                    "access_level": "Public"
                }

                # Add tier restriction info if present
                if album.tier_restrictions and album.tier_restrictions.get("is_restricted"):
                    album_info["access_level"] = f"{album.tier_restrictions.get('minimum_tier', 'Unknown')} and above"

                result.append(album_info)

            return result
        except Exception as e:
            logger.error(f"Error fetching audiobooks from database: {str(e)}")
            return []

    def get_album_by_id(self, db: Session, album_id: str) -> Optional[Dict]:
        """
        Get a single audiobook by ID with all related information.
        """
        try:
            # Import models here to avoid circular imports
            from models import Album, Track, User

            album = db.query(Album).filter(Album.id == album_id).first()
            if not album:
                return None

            # Get track count
            track_count = len(album.tracks) if hasattr(album, 'tracks') and album.tracks else db.query(Track).filter(Track.album_id == album.id).count()

            # Get creator
            creator = None
            if album.created_by_id:
                creator = db.query(User).filter(User.id == album.created_by_id).first()

            # Build result
            result = {
                "id": str(album.id),
                "title": album.title,
                "cover_path": album.cover_path,
                "created_at": album.created_at.isoformat() if album.created_at else None,
                "updated_at": album.updated_at.isoformat() if album.updated_at else None,
                "track_count": track_count,
                "creator_name": creator.username if creator else "Unknown Creator",
                "creator_id": album.created_by_id,
                "tier_restrictions": album.tier_restrictions
            }

            # Add access level
            if album.tier_restrictions and album.tier_restrictions.get("is_restricted"):
                result["access_level"] = f"{album.tier_restrictions.get('minimum_tier', 'Unknown')} and above"
            else:
                result["access_level"] = "Public"

            return result
        except Exception as e:
            logger.error(f"Error getting audiobook {album_id}: {str(e)}")
            return None

    # ===== RATE LIMITING =====

    async def respect_rate_limit(self):
        """
        Ensure we don't exceed Discord's rate limits by adding delays between messages.
        """
        now = time.time()
        elapsed = now - self.last_message_time
        
        if elapsed < DISCORD_RATE_LIMIT_DELAY:
            delay = DISCORD_RATE_LIMIT_DELAY - elapsed
            logger.debug(f"Rate limiting: waiting {delay:.2f}s before next message")
            await asyncio.sleep(delay)
        
        self.last_message_time = time.time()

    # ===== DISCORD API METHODS =====

    async def send_discord_message(self, payload: Dict, track_for_sync: bool = False) -> Union[bool, str]:
        """
        Send a message to Discord via webhook with size limit handling and rate limiting.
        Returns message ID if successful and tracking is enabled.
        """
        if not self.webhook_url:
            logger.warning("Discord webhook URL not configured, skipping message")
            return False

        try:
            # Apply rate limiting
            await self.respect_rate_limit()
            
            # Check content size limits
            if "content" in payload and len(payload["content"]) > DISCORD_CONTENT_LIMIT:
                logger.warning(f"Content exceeds Discord's {DISCORD_CONTENT_LIMIT} character limit. Truncating...")
                payload["content"] = payload["content"][:DISCORD_CONTENT_LIMIT-3] + "..."
            
            # Check embed size limits
            if "embeds" in payload and payload["embeds"]:
                # Ensure no more than 10 embeds
                if len(payload["embeds"]) > DISCORD_EMBEDS_PER_MESSAGE:
                    logger.warning(f"Too many embeds ({len(payload['embeds'])}). Limiting to {DISCORD_EMBEDS_PER_MESSAGE}.")
                    payload["embeds"] = payload["embeds"][:DISCORD_EMBEDS_PER_MESSAGE]
                
                # Check each embed's size
                for i, embed in enumerate(payload["embeds"]):
                    embed_json = json.dumps(embed)
                    if len(embed_json) > DISCORD_EMBED_LIMIT:
                        logger.warning(f"Embed {i+1} exceeds size limit. Simplifying...")
                        
                        # Simplify by reducing fields
                        if "fields" in embed and len(embed["fields"]) > 0:
                            # Keep reducing fields until it fits or we have just one field
                            while len(json.dumps(embed)) > DISCORD_EMBED_LIMIT and len(embed["fields"]) > 1:
                                embed["fields"].pop()
                            
                            # If still too big, simplify the remaining field
                            if len(json.dumps(embed)) > DISCORD_EMBED_LIMIT and len(embed["fields"]) == 1:
                                embed["fields"][0]["value"] = "Content too large to display fully"
                        
                        # If still too big after all that, replace with a simple embed
                        if len(json.dumps(embed)) > DISCORD_EMBED_LIMIT:
                            payload["embeds"][i] = {
                                "title": embed.get("title", "Audiobook Information"),
                                "description": "Content too large to display fully in Discord."
                            }

            # Log message details
            payload_size = len(json.dumps(payload))
            logger.info(f"Sending Discord message, payload size: {payload_size} bytes")
            
            # Redact sensitive parts of the webhook URL
            webhook_parts = self.webhook_url.split('/')
            safe_url = f"{'/'.join(webhook_parts[:-2])}/XXXXX/XXXXX"
            logger.info(f"Using webhook URL: {safe_url}")

            # Send the message
            async with aiohttp.ClientSession() as session:
                async with session.post(self.webhook_url, json=payload) as response:
                    if response.status == 204:
                        logger.info("Discord message sent successfully (no content returned)")
                        return True
                    elif response.status == 200:
                        # If we get a 200 response, Discord returns the message data
                        message_data = await response.json()
                        message_id = message_data.get("id")
                        
                        logger.info(f"Discord message sent successfully - ID: {message_id}")
                        
                        # If tracking is enabled for sync, store the ID
                        if track_for_sync and message_id:
                            self.sync_message_ids.append(message_id)
                            logger.info(f"Added message ID to sync tracking: {message_id}")
                        
                        return message_id if message_id else True
                    elif response.status == 429:  # Rate limited
                        # Parse rate limit info
                        rate_data = await response.json()
                        retry_after = rate_data.get('retry_after', 5)  # Default to 5s if not specified
                        logger.warning(f"Rate limited by Discord. Retrying after {retry_after}s")
                        
                        # Wait and retry
                        await asyncio.sleep(retry_after)
                        return await self.send_discord_message(payload, track_for_sync)
                    else:
                        error_text = await response.text()
                        logger.error(f"Discord webhook error: {response.status} - {error_text}")
                        
                        # Try to recover from certain errors
                        if response.status == 400 and ("content" in error_text.lower() or "embed" in error_text.lower()):
                            # Simplify the message and try again
                            simplified_payload = {
                                "content": "Audiobook information was too large to display fully. Please check the web interface."
                            }
                            logger.info("Retrying with simplified message")
                            return await self.send_simplified_message(simplified_payload, track_for_sync)
                            
                        return False
        except Exception as e:
            logger.error(f"Error sending Discord message: {str(e)}")
            return False

    async def send_simplified_message(self, payload: Dict, track_for_sync: bool = False) -> Union[bool, str]:
        """Send a simplified message when regular message fails"""
        try:
            # Apply rate limiting
            await self.respect_rate_limit()
            
            async with aiohttp.ClientSession() as session:
                async with session.post(self.webhook_url, json=payload) as response:
                    if response.status == 204:
                        logger.info("Simplified Discord message sent successfully")
                        return True
                    elif response.status == 200:
                        # If we get a 200 response, Discord returns the message data
                        message_data = await response.json()
                        message_id = message_data.get("id")
                        
                        logger.info(f"Simplified Discord message sent successfully - ID: {message_id}")
                        
                        # If tracking is enabled for sync, store the ID
                        if track_for_sync and message_id:
                            self.sync_message_ids.append(message_id)
                            logger.info(f"Added message ID to sync tracking: {message_id}")
                        
                        return message_id if message_id else True
                    else:
                        error_text = await response.text()
                        logger.error(f"Simplified message failed: {response.status} - {error_text}")
                        return False
        except Exception as e:
            logger.error(f"Error sending simplified message: {str(e)}")
            return False

    async def clean_old_sync_messages(self, db: Session) -> bool:
        """
        Clean up old sync messages from Discord webhook.
        Uses the tracked message IDs to delete each message.
        """
        if not self.webhook_url or not self.webhook_id or not self.webhook_token:
            logger.warning("Discord webhook details not available, cannot clean messages")
            return False
            
        logger.info(f"Starting Discord message cleanup for webhook ID: {self.webhook_id}")
        
        # If we don't have any tracked message IDs, try bot-based cleanup
        if not self.sync_message_ids and self.bot_token:
            logger.info("No tracked message IDs found, attempting bot-based cleanup")
            return await self.clean_all_webhook_messages(db)
        
        if not self.sync_message_ids:
            logger.warning("No tracked message IDs found and no bot token available. Cleanup may be incomplete.")
            return False
            
        try:
            deleted_count = 0
            failed_count = 0
            
            logger.info(f"Attempting to delete {len(self.sync_message_ids)} tracked messages")
            
            async with aiohttp.ClientSession() as session:
                for message_id in self.sync_message_ids.copy():
                    # Delete the message using webhook API
                    delete_url = f"https://discord.com/api/webhooks/{self.webhook_id}/{self.webhook_token}/messages/{message_id}"
                    
                    # Add rate limiting
                    await self.respect_rate_limit()
                    
                    try:
                        async with session.delete(delete_url) as response:
                            if response.status == 204:
                                logger.info(f"Successfully deleted message ID: {message_id}")
                                deleted_count += 1
                                self.sync_message_ids.remove(message_id)
                            else:
                                error_text = await response.text()
                                logger.warning(f"Failed to delete message {message_id}: {response.status} - {error_text}")
                                failed_count += 1
                                # If message not found (probably already deleted), remove from tracking
                                if response.status == 404:
                                    self.sync_message_ids.remove(message_id)
                    except Exception as e:
                        logger.error(f"Error deleting message {message_id}: {str(e)}")
                        failed_count += 1
            
            logger.info(f"Cleanup completed: {deleted_count} messages deleted, {failed_count} failed")
            
            # If we couldn't delete some messages and have a bot token, try bot-based cleanup
            if failed_count > 0 and self.bot_token:
                logger.info("Some messages could not be deleted with webhook, trying bot-based cleanup")
                await self.clean_all_webhook_messages(db)
            
            # Save updated message IDs to database
            await self.save_settings_to_db(db)
            
            return deleted_count > 0 or failed_count == 0
            
        except Exception as e:
            logger.error(f"Error cleaning up Discord messages: {str(e)}")
            return False

    async def clean_all_webhook_messages(self, db: Session) -> bool:
        """
        Clean up all messages from this webhook in the channel.
        Uses a bot token to get all messages and delete those from our webhook.
        """
        if not self.webhook_url or not self.webhook_id or not self.webhook_token:
            logger.warning("Discord webhook details not available, cannot clean messages")
            return False
            
        if not self.bot_token:
            logger.warning("Bot token not available. Cannot perform full cleanup.")
            return False
        
        logger.info(f"Starting full Discord message cleanup for webhook ID: {self.webhook_id}")
        
        try:
            # First, get webhook info to find the channel ID
            webhook_info_url = f"https://discord.com/api/v10/webhooks/{self.webhook_id}"
            
            async with aiohttp.ClientSession() as session:
                # Set up bot authorization headers
                headers = {
                    "Authorization": f"Bot {self.bot_token}",
                    "Content-Type": "application/json"
                }
                
                # Get webhook info
                async with session.get(webhook_info_url, headers=headers) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Failed to get webhook info: {response.status} - {error_text}")
                        return False
                    
                    webhook_data = await response.json()
                    channel_id = webhook_data.get("channel_id")
                    
                    if not channel_id:
                        logger.error("Could not determine channel ID from webhook")
                        return False
                    
                    logger.info(f"Found webhook channel ID: {channel_id}")
                
                # Now we have the channel ID, we can get messages from it
                messages_url = f"https://discord.com/api/v10/channels/{channel_id}/messages?limit=100"
                
                async with session.get(messages_url, headers=headers) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Failed to get channel messages: {response.status} - {error_text}")
                        return False
                    
                    messages = await response.json()
                    logger.info(f"Retrieved {len(messages)} messages from channel")
                    
                    # Filter for messages from our webhook
                    webhook_messages = [msg for msg in messages if msg.get("webhook_id") == self.webhook_id]
                    
                    if not webhook_messages:
                        logger.info("No webhook messages found to delete")
                        # Clear tracked message IDs since no messages exist
                        self.sync_message_ids = []
                        await self.save_settings_to_db(db)
                        return True
                    
                    logger.info(f"Found {len(webhook_messages)} webhook messages to delete")
                    
                    # For bulk delete to work, messages must be less than 2 weeks old
                    # To be safe, we'll check if they're less than 13 days old
                    current_time = datetime.now(timezone.utc)
                    two_weeks_ago = current_time - timedelta(days=13)
                    
                    # Check if all messages are new enough for bulk delete
                    all_recent = True
                    for msg in webhook_messages:
                        created_at = msg.get("timestamp")
                        if created_at:
                            try:
                                # Parse ISO timestamp
                                msg_time = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                                if msg_time < two_weeks_ago:
                                    all_recent = False
                                    break
                            except Exception:
                                all_recent = False
                                break
                        else:
                            all_recent = False
                            break
                    
                    # Try bulk delete if all messages are recent enough
                    message_ids = [msg["id"] for msg in webhook_messages]
                    
                    # Update our tracked IDs to match what we found
                    self.sync_message_ids = message_ids.copy()
                    
                    # If all messages are recent enough and we have more than 2 messages, use bulk delete
                    if all_recent and len(message_ids) > 1:
                        bulk_delete_url = f"https://discord.com/api/v10/channels/{channel_id}/messages/bulk-delete"
                        bulk_payload = {"messages": message_ids}
                        
                        try:
                            async with session.post(bulk_delete_url, headers=headers, json=bulk_payload) as bulk_response:
                                if bulk_response.status == 204:
                                    logger.info(f"Successfully bulk deleted {len(message_ids)} messages")
                                    # Clear tracking since we've deleted everything
                                    self.sync_message_ids = []
                                    await self.save_settings_to_db(db)
                                    return True
                                else:
                                    error_text = await bulk_response.text()
                                    logger.warning(f"Bulk delete failed: {bulk_response.status} - {error_text}")
                                    logger.info("Falling back to individual message deletion")
                        except Exception as e:
                            logger.error(f"Error in bulk delete: {str(e)}")
                            logger.info("Falling back to individual message deletion")
                    
                    # If bulk delete failed or wasn't attempted, delete messages individually
                    deleted_count = 0
                    for message in webhook_messages:
                        message_id = message["id"]
                        delete_url = f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}"
                        
                        try:
                            # Apply rate limiting to avoid Discord limits
                            await self.respect_rate_limit()
                            
                            async with session.delete(delete_url, headers=headers) as delete_response:
                                if delete_response.status == 204:
                                    deleted_count += 1
                                    # Remove from tracked IDs
                                    if message_id in self.sync_message_ids:
                                        self.sync_message_ids.remove(message_id)
                                else:
                                    error_text = await delete_response.text()
                                    logger.warning(f"Failed to delete message {message_id}: {delete_response.status} - {error_text}")
                        except Exception as e:
                            logger.error(f"Error deleting message {message_id}: {str(e)}")
                    
                    logger.info(f"Successfully deleted {deleted_count} messages individually")
                    
                    # Save updated message IDs to database
                    await self.save_settings_to_db(db)
                    
                    return deleted_count > 0
                    
        except Exception as e:
            logger.error(f"Error in full Discord message cleanup: {str(e)}")
            return False
            
    def _save_sync_message_ids(self, db: Session, message_ids: List[str]) -> bool:
        """
        Save message IDs for future reference/cleanup.
        Store IDs both in memory and database.
        """
        try:
            # Update in-memory list
            self.sync_message_ids = message_ids.copy()
            
            # Update database if we have db_settings
            if self.db_settings:
                self.db_settings.sync_message_ids = message_ids
                db.commit()
                
            logger.info(f"Saved {len(message_ids)} message IDs for tracking")
            return True
        except Exception as e:
            logger.error(f"Error saving message IDs: {str(e)}")
            
            # Try to rollback if possible
            try:
                db.rollback()
            except:
                pass
                
            return False

    # ===== BATCHED SYNC METHODS =====

    async def sync_album_list(self, db: Session) -> bool:
        """
        Sync the full audiobook list to Discord using batched messages for reliable delivery.
        First cleans up any existing sync messages before sending new ones.
        """
        if not self.initialized or not self.webhook_url:
            logger.warning("Discord integration not initialized, skipping sync")
            return False
        
        # First, clean up existing sync messages
        logger.info("Cleaning up existing Discord messages before sync")
        await self.clean_old_sync_messages(db)
        
        # Reset sync message IDs list since we're starting fresh
        self.sync_message_ids = []
            
        # Get albums from database
        albums = self.get_album_list_from_db(db)
        
        if not albums:
            # Simple case: no albums
            message_payload = {
                "content": "**📚 Audiobook Collection**\nNo audiobooks found in the database."
            }
            message_id = await self.send_discord_message(message_payload, track_for_sync=True)
            if message_id and isinstance(message_id, str):
                self.sync_message_ids.append(message_id)
                await self.save_settings_to_db(db)
            return bool(message_id)
        
        # Split albums into manageable chunks - this is key to reliable delivery
        # We'll use 15 albums per chunk to stay well within Discord's limits
        chunk_size = 15
        success = True
        
        # First, send a summary message
        summary = {
            "content": f"**📚 Audiobook Collection Summary**\n\n"
                      f"Total Audiobooks: {len(albums)}\n"
                      f"Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        }
        
        summary_result = await self.send_discord_message(summary, track_for_sync=True)
        if not summary_result:
            logger.warning("Failed to send summary message")
        
        # Send album lists in chunks
        for i in range(0, len(albums), chunk_size):
            chunk = albums[i:i+chunk_size]
            
            # Create message for this chunk
            content = f"**Audiobooks {i+1}-{i+len(chunk)}**\n\n"
            
            for j, album in enumerate(chunk, 1):
                album_title = album['title']
                # Truncate very long titles to avoid hitting message limits
                if len(album_title) > 50:
                    album_title = album_title[:47] + "..."
                    
                content += f"{i+j}. **{album_title}** - {album['track_count']} tracks"
                content += f" ({album['access_level']})\n"
            
            # Send this chunk
            chunk_payload = {"content": content}
            chunk_result = await self.send_discord_message(chunk_payload, track_for_sync=True)
            
            if not chunk_result:
                success = False
                logger.error(f"Failed to send audiobook chunk {i+1}-{i+len(chunk)}")
        
        # Save message IDs to database
        await self.save_settings_to_db(db)
        
        logger.info(f"Sync completed with {len(self.sync_message_ids)} tracked messages")
        return success

    # ===== NOTIFICATIONS =====

    async def notify_album_created(self, db: Session, album_id: str) -> bool:
        """
        Send notification about a new audiobook
        """
        if not self.initialized or not self.webhook_url:
            return False
            
        # Get album details
        album_data = self.get_album_by_id(db, album_id)
        if not album_data:
            logger.error(f"Audiobook {album_id} not found for Discord notification")
            return False
        
        # Get creator name
        creator_name = album_data.get("creator_name", "Unknown Creator")
        
        # Create simple text message
        message = f"**📚 New Audiobook Added**\n\n"
        message += f"**{album_data['title']}**\n"
        message += f"Tracks: {album_data.get('track_count', 0)}\n"
        message += f"Creator: {creator_name}\n"
        message += f"Access Level: {album_data.get('access_level', 'Public')}\n"
        message += f"Added: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        # Send notification
        payload = {"content": message}
        return bool(await self.send_discord_message(payload))

    async def notify_album_updated(self, db: Session, album_id: str, is_tier_change: bool = False) -> bool:
        """
        Send notification about an audiobook update
        """
        if not self.initialized or not self.webhook_url:
            return False
            
        # Get album details
        album_data = self.get_album_by_id(db, album_id)
        if not album_data:
            logger.error(f"Audiobook {album_id} not found for Discord notification")
            return False
        
        # Get creator name
        creator_name = album_data.get("creator_name", "Unknown Creator")
        
        # Create simple text message
        icon = "👑" if is_tier_change else "🔄"
        action = "Tier Access Changed" if is_tier_change else "Updated"
        
        message = f"**{icon} Audiobook {action}**\n\n"
        message += f"**{album_data['title']}**\n"
        message += f"Tracks: {album_data.get('track_count', 0)}\n"
        message += f"Creator: {creator_name}\n"
        message += f"Access Level: {album_data.get('access_level', 'Public')}\n"
        message += f"Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        # Send notification
        payload = {"content": message}
        return bool(await self.send_discord_message(payload))

    async def notify_album_deleted(self, db: Session, album_data: Dict) -> bool:
        """
        Send notification about audiobook deletion
        """
        if not self.initialized or not self.webhook_url:
            return False
        
        # Create simple text message
        message = f"**🗑️ Audiobook Deleted**\n\n"
        message += f"**{album_data['title']}**\n"
        message += f"Tracks: {album_data.get('track_count', 0)}\n"
        message += f"Creator ID: {album_data.get('creator_id', 'Unknown')}\n"
        message += f"Deleted: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        # Send notification
        payload = {"content": message}
        return bool(await self.send_discord_message(payload))

    async def notify_bulk_update(self, db: Session, albums_data: List[Dict], update_type: str, creator_name: str = None) -> bool:
        """
        Send notification about multiple audiobooks being updated
        """
        if not self.initialized or not self.webhook_url or not albums_data:
            return False
        
        try:
            # Create title based on update type
            if update_type == "tier_change":
                title = "👑 Multiple Audiobook Tiers Updated"
            elif update_type == "deleted":
                title = "🗑️ Multiple Audiobooks Deleted"
            else:
                title = "🔄 Multiple Audiobooks Updated"
            
            # Create simple text message
            message = f"**{title}**\n\n"
            message += f"Updated {len(albums_data)} audiobooks\n"
            
            if creator_name:
                message += f"Creator: {creator_name}\n"
            
            message += f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            
            # Add album list (limited to first 15)
            for i, album in enumerate(albums_data[:15], 1):
                message += f"{i}. {album.get('title', 'Unknown Audiobook')}\n"
            
            if len(albums_data) > 15:
                message += f"...and {len(albums_data) - 15} more audiobooks"
            
            # Send notification
            payload = {"content": message}
            return bool(await self.send_discord_message(payload))
        except Exception as e:
            logger.error(f"Error sending bulk Discord notification: {str(e)}")
            return False

# Create a singleton instance
discord = DiscordIntegration()

# ===== ALBUM SERVICE INTEGRATION HELPERS =====

async def on_album_created(db: Session, album_id: str, creator_id: int = None):
    """Notify Discord when an audiobook is created"""
    # Get creator_id if not provided
    if not creator_id:
        from models import Album
        album = db.query(Album).filter(Album.id == album_id).first()
        if album:
            creator_id = album.created_by_id
    
    # Initialize Discord for this creator
    if creator_id:
        await discord.initialize(db, creator_id)
        
    # Send notification if initialized
    if discord.initialized:
        await discord.notify_album_created(db, album_id)

async def on_album_updated(db: Session, album_id: str, is_tier_change: bool = False, creator_id: int = None):
    """Notify Discord when an audiobook is updated"""
    # Get creator_id if not provided
    if not creator_id:
        from models import Album
        album = db.query(Album).filter(Album.id == album_id).first()
        if album:
            creator_id = album.created_by_id
    
    # Initialize Discord for this creator
    if creator_id:
        await discord.initialize(db, creator_id)
        
    # Send notification if initialized
    if discord.initialized:
        await discord.notify_album_updated(db, album_id, is_tier_change)

async def on_album_deleted(db: Session, album_data: Dict, creator_id: int = None):
    """Notify Discord when an audiobook is deleted"""
    # Get creator_id from album_data if not provided
    if not creator_id and 'created_by_id' in album_data:
        creator_id = album_data['created_by_id']
    
    # Initialize Discord for this creator
    if creator_id:
        await discord.initialize(db, creator_id)
        
    # Send notification if initialized
    if discord.initialized:
        await discord.notify_album_deleted(db, album_data)

async def on_bulk_update(db: Session, albums_data: List[Dict], update_type: str, creator_id: int = None, creator_name: str = None):
    """Notify Discord when multiple audiobooks are updated"""
    # Get creator_id from the first album if not provided
    if not creator_id and albums_data and 'created_by_id' in albums_data[0]:
        creator_id = albums_data[0]['created_by_id']
    
    # Initialize Discord for this creator
    if creator_id:
        await discord.initialize(db, creator_id)
        
    # Send notification if initialized
    if discord.initialized:
        await discord.notify_bulk_update(db, albums_data, update_type, creator_name)