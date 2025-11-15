"""
Token-based authorization service for HLS streaming
Reduces DB queries from ~60 per track to 1-2 by issuing grant tokens
"""

import hashlib
import hmac
import json
import time
from typing import Optional, Tuple
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from models import User, Track, Album, CampaignTier
from redis_config import redis_client

# Secret for signing tokens (in production, use env variable)
import os
TOKEN_SECRET = os.getenv('GRANT_TOKEN_SECRET', 'your-secret-key-change-in-production')

# Token TTL in seconds (10 minutes)
TOKEN_TTL = 600

class AuthorizationService:
    """Unified authorization service with token caching"""

    @staticmethod
    def evaluate_access(
        user: User,
        track: Track,
        voice_id: Optional[str] = None,
        db: Session = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Unified access evaluation covering album, track, and voice rules.

        Returns:
            (has_access: bool, error_message: Optional[str])
        """
        # Creator and team bypass all restrictions
        if user.is_creator or user.is_team:
            return True, None

        # Get album restrictions
        album = track.album
        if not album:
            return True, None

        restrictions = album.tier_restrictions

        # If no restrictions or not explicitly restricted, grant access
        if not restrictions or restrictions.get("is_restricted") is not True:
            return True, None

        # Get required tier info for error message
        required_tier = restrictions.get("minimum_tier", "").strip()
        tier_message = f"the {required_tier} tier or above" if required_tier else "a higher tier subscription"

        # Get user's tier data
        tier_data = user.patreon_tier_data if user.patreon_tier_data else {}
        user_amount = tier_data.get("amount_cents", 0)
        required_amount = restrictions.get("minimum_tier_amount", 0)

        # Check Patreon, Ko-fi, and guest trial users
        if (user.is_patreon or user.is_kofi or user.is_guest_trial) and tier_data:
            # Simple amount check
            if user_amount >= required_amount:
                return True, None

            # Special case for Ko-fi users with donations
            if user.is_kofi and tier_data.get('has_donations', False):
                donation_amount = tier_data.get('donation_amount_cents', 0)
                total_amount = user_amount + donation_amount

                if total_amount >= required_amount:
                    return True, None

        # Access denied
        error_msg = f"This content requires {tier_message}"
        return False, error_msg

    @staticmethod
    def create_grant_token(
        session_id: str,
        track_id: str,
        voice_id: Optional[str],
        content_version: int,
        user_id: int,
        ttl: int = TOKEN_TTL
    ) -> str:
        """
        Create a signed grant token valid for TTL seconds.

        Token includes content_version, so if track content changes,
        old tokens become invalid automatically.

        Format: {payload}.{signature}
        Payload: base64({session_id, track_id, voice_id, content_version, user_id, exp})
        """
        import base64

        expiry = int(time.time()) + ttl

        payload = {
            'sid': session_id,
            'tid': track_id,
            'vid': voice_id,
            'cv': content_version,  # Content version for cache invalidation
            'uid': user_id,
            'exp': expiry
        }

        # Encode payload
        payload_str = base64.b64encode(json.dumps(payload).encode()).decode()

        # Sign with HMAC-SHA256
        signature = hmac.new(
            TOKEN_SECRET.encode(),
            payload_str.encode(),
            hashlib.sha256
        ).hexdigest()

        return f"{payload_str}.{signature}"

    @staticmethod
    def validate_grant_token(
        token: str,
        track_id: str,
        voice_id: Optional[str],
        current_content_version: int
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate grant token.

        Returns:
            (is_valid: bool, reason: Optional[str])
        """
        import base64

        if not token:
            return False, "No token provided"

        try:
            # Split token
            parts = token.split('.')
            if len(parts) != 2:
                return False, "Invalid token format"

            payload_str, signature = parts

            # Verify signature
            expected_sig = hmac.new(
                TOKEN_SECRET.encode(),
                payload_str.encode(),
                hashlib.sha256
            ).hexdigest()

            if not hmac.compare_digest(signature, expected_sig):
                return False, "Invalid signature"

            # Decode payload
            payload = json.loads(base64.b64decode(payload_str))

            # Check expiry
            if time.time() > payload['exp']:
                return False, "Token expired"

            # Check track ID match
            if payload['tid'] != track_id:
                return False, "Track ID mismatch"

            # Check voice ID match
            if payload['vid'] != voice_id:
                return False, "Voice ID mismatch"

            # ✅ CRITICAL: Check content version match
            # If content_version has been bumped, token is invalid
            if payload['cv'] != current_content_version:
                return False, f"Content updated (v{payload['cv']} -> v{current_content_version})"

            return True, None

        except Exception as e:
            return False, f"Token validation error: {str(e)}"

    @staticmethod
    async def cache_grant_in_redis(
        session_id: str,
        track_id: str,
        voice_id: Optional[str],
        content_version: int,
        ttl: int = TOKEN_TTL
    ):
        """
        Store grant in Redis for fast validation.

        Key format: grant:{session_id}:{track_id}:{voice_id}
        Value: content_version
        """
        try:
            if not redis_client:
                return

            key = f"grant:{session_id}:{track_id}:{voice_id or 'default'}"
            redis_client.setex(key, ttl, str(content_version))
        except Exception:
            # Redis failure is not fatal - we can still use tokens
            pass

    @staticmethod
    async def check_redis_grant(
        session_id: str,
        track_id: str,
        voice_id: Optional[str],
        current_content_version: int
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if grant exists in Redis and content version matches.

        Returns:
            (is_valid: bool, reason: Optional[str])
        """
        try:
            redis = get_redis_client()
            if not redis:
                return False, "Redis unavailable"

            key = f"grant:{session_id}:{track_id}:{voice_id or 'default'}"
            cached_version = redis.get(key)

            if not cached_version:
                return False, "Grant not found"

            if int(cached_version) != current_content_version:
                # Content changed - invalidate cache
                redis.delete(key)
                return False, f"Content updated (cached v{cached_version} != v{current_content_version})"

            return True, None

        except Exception as e:
            return False, f"Redis error: {str(e)}"

    @staticmethod
    async def invalidate_track_grants(track_id: str):
        """
        Invalidate all grants for a track.
        Called when track content changes.
        """
        try:
            redis = get_redis_client()
            if not redis:
                return

            # Delete all grant keys for this track
            pattern = f"grant:*:{track_id}:*"
            keys = redis.keys(pattern)
            if keys:
                redis.delete(*keys)
        except Exception:
            pass

    @staticmethod
    async def invalidate_album_grants(album_id: str, db: Session):
        """
        Invalidate all grants for all tracks in an album.
        Called when album tier restrictions change.
        """
        try:
            redis = get_redis_client()
            if not redis:
                return

            # Get all track IDs in album
            tracks = db.query(Track).filter(Track.album_id == album_id).all()

            for track in tracks:
                pattern = f"grant:*:{track.id}:*"
                keys = redis.keys(pattern)
                if keys:
                    redis.delete(*keys)
        except Exception:
            pass


# Helper functions for easy integration

def issue_grant_token(
    session_id: str,
    track_id: str,
    voice_id: Optional[str],
    content_version: int,
    user_id: int,
    ttl: int = TOKEN_TTL
) -> str:
    """Create and cache a grant token"""
    token = AuthorizationService.create_grant_token(
        session_id, track_id, voice_id, content_version, user_id, ttl
    )

    # Also cache in Redis for faster validation
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(
            AuthorizationService.cache_grant_in_redis(
                session_id, track_id, voice_id, content_version, ttl
            )
        )
    except:
        pass

    return token


def validate_grant(
    token: str,
    track_id: str,
    voice_id: Optional[str],
    current_content_version: int
) -> Tuple[bool, Optional[str]]:
    """Validate a grant token"""
    return AuthorizationService.validate_grant_token(
        token, track_id, voice_id, current_content_version
    )


async def invalidate_on_content_change(track_id: str):
    """Call this when track content changes"""
    await AuthorizationService.invalidate_track_grants(track_id)


async def invalidate_on_tier_change(album_id: str, db: Session):
    """Call this when album tier restrictions change"""
    await AuthorizationService.invalidate_album_grants(album_id, db)
