# guest_trial_routes.py - COMPLETE FIXED: Atomic device registration after OTP + Passkey success

import logging
import secrets
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, Tuple
from fastapi import APIRouter, Request, Depends, HTTPException, BackgroundTasks, Form
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from pydantic import BaseModel, EmailStr, validator
from auth import login_required
import re
from database import get_db
from models import GuestPasskeyCredential
import base64
import json
from models import (
    User, UserRole, CampaignTier, Campaign, UserTier,
    GuestAbuseTracking, GuestOTP, GuestDeviceTracking, 
    GuestTrialSettings, KofiSettings, GuestTrialService
)
from guest_trial_email_service import send_guest_trial_otp_email

logger = logging.getLogger(__name__)

# Create router
router = APIRouter(
    prefix="/api/guest-trial",
    tags=["guest-trial"],
    responses={404: {"description": "Not found"}}
)

# ===========================
# PYDANTIC MODELS
# ===========================

class GuestRegistrationRequest(BaseModel):
    username: str
    email: EmailStr
    
    # Light device characteristics
    screen_width: Optional[int] = None
    screen_height: Optional[int] = None
    color_depth: Optional[int] = None
    hardware_concurrency: Optional[int] = None
    platform: Optional[str] = None
    timezone: Optional[str] = None
    language: Optional[str] = None
    
    # Enhanced with passkey fields
    webauthn_available: Optional[bool] = False
    passkey_check_passed: Optional[bool] = True
    existing_passkey_id: Optional[str] = None
    device_fingerprint: Optional[str] = None
    
    @validator('username')
    def validate_username(cls, v):
        if not v or len(v.strip()) < 2:
            raise ValueError('Username must be at least 2 characters')
        if len(v.strip()) > 30:
            raise ValueError('Username must be no more than 30 characters')
        if not re.match(r'^[a-zA-Z0-9_\-\s]+$', v):
            raise ValueError('Username can only contain letters, numbers, spaces, hyphens and underscores')
        return v.strip()

class OTPVerificationStagedRequest(BaseModel):
    otp_code: str
    otp_id: int
    
    @validator('otp_code')
    def validate_otp_code(cls, v):
        if not v or not re.match(r'^\d{6}$', v):
            raise ValueError('OTP code must be exactly 6 digits')
        return v

class PasskeyData(BaseModel):
    credentialId: str
    publicKey: str
    attestationObject: Optional[str] = None
    clientDataJSON: Optional[str] = None

class AtomicRegistrationRequest(BaseModel):
    otp_id: int
    passkey_data: Optional[PasskeyData] = None
    register_device: bool = True

class AbortRegistrationRequest(BaseModel):
    otp_id: int
    reason: str = "user_cancelled"

class OTPResendRequest(BaseModel):
    otp_id: int

# ===========================
# ABUSE PREVENTION FUNCTIONS
# ===========================

def generate_light_device_signature(device_data: dict) -> str:
    """Generate simple device signature from stable characteristics only"""
    signature_parts = [
        str(device_data.get('screen_width', 0)),
        str(device_data.get('screen_height', 0)),
        str(device_data.get('color_depth', 24)),
        str(device_data.get('hardware_concurrency', 0)),
        device_data.get('platform', 'unknown')[:20],
        device_data.get('timezone', 'unknown')[:30]
    ]
    
    signature_string = '|'.join(signature_parts)
    hash_object = hashlib.md5(signature_string.encode())
    return hash_object.hexdigest()

async def check_email_abuse(email: str, creator_id: int, db: Session) -> dict:
    """Check if email has been used for any trial or existing user"""
    existing_user = db.query(User).filter(
        and_(
            func.lower(User.email) == email.lower(),
            User.created_by == creator_id
        )
    ).first()
    
    if existing_user:
        if existing_user.is_guest_trial:
            if existing_user.trial_active:
                return {
                    "allowed": False,
                    "reason": "You already have an active trial",
                    "code": "TRIAL_ACTIVE",
                    "user_id": existing_user.id
                }
            else:
                return {
                    "allowed": False, 
                    "reason": "This email was already used for a trial",
                    "code": "TRIAL_USED",
                    "user_id": existing_user.id
                }
        else:
            return {
                "allowed": False,
                "reason": "This email is already registered. Please use the regular login",
                "code": "EMAIL_REGISTERED", 
                "user_id": existing_user.id
            }
    
    return {"allowed": True}

async def check_device_signature_abuse(device_signature: str, creator_id: int, db: Session) -> dict:
    """Check device signature abuse"""
    if not device_signature:
        return {"allowed": True}
    
    existing_device = db.query(GuestDeviceTracking).filter(
        and_(
            GuestDeviceTracking.creator_id == creator_id,
            GuestDeviceTracking.device_fingerprint == device_signature,
            GuestDeviceTracking.trial_count > 0
        )
    ).first()
    
    if existing_device:
        return {
            "allowed": False,
            "reason": "This device has already been used for a trial",
            "code": "DEVICE_ALREADY_USED",
            "first_trial_date": existing_device.first_seen.isoformat(),
            "total_trials": existing_device.trial_count
        }
    
    return {"allowed": True}

async def check_passkey_abuse(credential_id: str, device_fingerprint: str, creator_id: int, db: Session) -> dict:
    """Check if a passkey credential already exists for this creator"""
    
    if not credential_id and not device_fingerprint:
        return {"allowed": True}
        
    # Check for existing credential ID
    if credential_id:
        existing_credential = db.query(GuestPasskeyCredential).filter(
            and_(
                GuestPasskeyCredential.credential_id == credential_id,
                GuestPasskeyCredential.creator_id == creator_id,
                GuestPasskeyCredential.is_active == True,
                GuestPasskeyCredential.is_revoked == False
            )
        ).first()
        
        if existing_credential:
            return {
                "allowed": False,
                "reason": "This device already has a trial passkey registered",
                "code": "PASSKEY_EXISTS",
                "credential_id": credential_id,
                "registered_email": existing_credential.email,
                "created_at": existing_credential.created_at.isoformat()
            }
    
    # Check device tracking table for passkey
    if device_fingerprint:
        device_with_passkey = db.query(GuestDeviceTracking).filter(
            and_(
                GuestDeviceTracking.device_fingerprint == device_fingerprint,
                GuestDeviceTracking.creator_id == creator_id,
                GuestDeviceTracking.passkey_credential_id.isnot(None),
                GuestDeviceTracking.trial_count > 0
            )
        ).first()
        
        if device_with_passkey:
            return {
                "allowed": False,
                "reason": "This device already has a trial passkey registered",
                "code": "DEVICE_HAS_PASSKEY",
                "device_passkey_id": device_with_passkey.passkey_credential_id,
                "created_at": device_with_passkey.passkey_created_at.isoformat() if device_with_passkey.passkey_created_at else None
            }
    
    return {"allowed": True}

def record_device_signature_usage(device_signature: str, device_data: dict, creator_id: int, db: Session):
    """Record device signature usage in GuestDeviceTracking table"""
    if not device_signature:
        return
    
    existing_device = db.query(GuestDeviceTracking).filter(
        and_(
            GuestDeviceTracking.creator_id == creator_id,
            GuestDeviceTracking.device_fingerprint == device_signature
        )
    ).first()
    
    if existing_device:
        existing_device.trial_count += 1
        existing_device.last_seen = datetime.now(timezone.utc)
        language_value = device_data.get('language') or existing_device.language or 'unknown'
        existing_device.language = language_value[:10] if language_value else 'unknown'
    else:
        screen_width = device_data.get('screen_width') if device_data.get('screen_width') is not None else 0
        screen_height = device_data.get('screen_height') if device_data.get('screen_height') is not None else 0
        platform_value = device_data.get('platform') or 'unknown'
        language_value = device_data.get('language') or 'unknown'
        
        new_device = GuestDeviceTracking(
            creator_id=creator_id,
            device_fingerprint=device_signature,
            screen_resolution=f"{screen_width}x{screen_height}",
            timezone_offset=None,
            language=language_value[:10] if language_value and language_value != 'unknown' else 'unknown',
            platform=platform_value[:50] if platform_value and platform_value != 'unknown' else 'unknown',
            cookies_enabled=True,
            local_storage_enabled=True,
            session_storage_enabled=True,
            trial_count=1,
            is_suspicious=False
        )
        db.add(new_device)
    
    # Don't commit here - let the calling function handle the transaction

async def comprehensive_abuse_check(email: str, device_data: dict, creator_id: int, existing_passkey_id: str = None, db: Session = None) -> dict:
    """Enhanced abuse checking with both light system and passkey layer"""
    
    # LAYER 1: Light checks
    email_check = await check_email_abuse(email, creator_id, db)
    if not email_check["allowed"]:
        return email_check
    
    device_signature = generate_light_device_signature(device_data)
    device_check = await check_device_signature_abuse(device_signature, creator_id, db)
    if not device_check["allowed"]:
        return device_check
    
    # LAYER 2: Passkey checks (if WebAuthn available)
    if device_data.get('webauthn_available') and existing_passkey_id:
        passkey_check = await check_passkey_abuse(
            credential_id=existing_passkey_id,
            device_fingerprint=device_signature,
            creator_id=creator_id,
            db=db
        )
        
        if not passkey_check["allowed"]:
            return passkey_check
    
    return {
        "allowed": True, 
        "device_signature": device_signature,
        "layers_passed": ["light", "passkey"] if device_data.get('webauthn_available') else ["light"]
    }

# ===========================
# USER LOGIN FUNCTIONS
# ===========================

async def check_trial_user_login(email: str, creator_id: int, db: Session) -> Tuple[Optional[User], Optional[dict]]:
    """Check trial user login and ensure proper Guest Trial tier association"""
    try:
        # Find guest trial user
        trial_user = db.query(User).filter(
            and_(
                func.lower(User.email) == email.lower(),
                User.created_by == creator_id,
                User.role == UserRole.GUEST,
                User.is_guest_trial == True,
                User.is_active == True
            )
        ).first()
        
        if not trial_user:
            return None, None
        
        logger.info(f"Found guest trial user: {email} (ID: {trial_user.id})")
        
        # Ensure Guest Trial tier association using current database settings
        guest_service = GuestTrialService(db)
        guest_tier = await guest_service.get_or_create_guest_tier(creator_id)
        
        logger.info(f"Guest Trial tier: {guest_tier.title} (ID: {guest_tier.id}) - "
                   f"Albums={guest_tier.album_downloads_allowed}, "
                   f"Tracks={guest_tier.track_downloads_allowed}, "
                   f"Books={guest_tier.book_requests_allowed}, "
                   f"Amount={guest_tier.amount_cents} cents")
        
        # Check if trial is still active
        if not trial_user.trial_active:
            logger.info(f"Trial expired for {email} - converting to free Ko-fi tier")
            return await convert_expired_trial_to_free_tier(trial_user, creator_id, db)
        
        # Ensure proper Guest Trial tier association
        user_tier_association = db.query(UserTier).filter(
            and_(
                UserTier.user_id == trial_user.id,
                UserTier.tier_id == guest_tier.id,
                UserTier.is_active == True
            )
        ).first()
        
        if not user_tier_association:
            logger.info(f"Creating missing Guest Trial tier association for user {email}")
            
            trial_start = trial_user.trial_started_at or datetime.now(timezone.utc)
            trial_expires = trial_user.trial_expires_at
            
            user_tier_association = UserTier(
                user_id=trial_user.id,
                tier_id=guest_tier.id,
                joined_at=trial_start,
                expires_at=trial_expires,
                is_active=True,
                payment_status='guest_trial'
            )
            db.add(user_tier_association)
            logger.info(f"✅ Created Guest Trial tier association: User {trial_user.id} → Tier {guest_tier.id}")
        else:
            # Update existing association
            user_tier_association.expires_at = trial_user.trial_expires_at
            user_tier_association.is_active = True
            logger.info(f"✅ Updated existing Guest Trial tier association for user {email}")
        
        # Apply monthly reset for guest trial users
        current_tier_data = trial_user.patreon_tier_data or {}
        from kofi_routes import check_and_reset_monthly_downloads
        album_used, track_used, book_used, was_reset = check_and_reset_monthly_downloads(current_tier_data)
        
        if was_reset:
            logger.info(f"✅ Monthly downloads reset for guest trial user {email}")
        
        # USE DATABASE SETTING: Tier data with current guest tier amount
        current_month = datetime.now(timezone.utc).strftime("%Y-%m")
        
        synced_tier_data = {
            # Core tier identification WITH current database amount
            'title': guest_tier.title,
            'platform': 'kofi',
            'platform_type': 'KOFI',
            'amount_cents': guest_tier.amount_cents,  # Use actual tier amount from database
            'kofi_user': True,
            'guest_trial': True,
            'patron_status': 'active_trial',
            
            # Trial metadata
            'trial_started_at': current_tier_data.get('trial_started_at'),
            'trial_expires_at': current_tier_data.get('trial_expires_at'),
            'trial_duration_hours': current_tier_data.get('trial_duration_hours'),
            
            # Usage tracking (reset monthly)
            'album_downloads_used': album_used,
            'track_downloads_used': track_used,
            'book_requests_used': book_used,
            'last_reset_month': current_month,
            
            # Device and session tracking
            'max_sessions': 1,
            'guest_identifier': current_tier_data.get('guest_identifier'),
            'device_signature': current_tier_data.get('device_signature'),
            'registration_ip': current_tier_data.get('registration_ip'),
        }
        
        # Add reset tracking if reset occurred
        if was_reset:
            synced_tier_data['monthly_reset_date'] = datetime.now(timezone.utc).isoformat()
            synced_tier_data['reset_during_login'] = True
        
        # Save updated user data
        trial_user.patreon_tier_data = synced_tier_data
        trial_user.last_login = datetime.now(timezone.utc)
        
        db.commit()
        db.refresh(trial_user)
        
        logger.info(f"✅ Guest trial user {email} properly associated with Guest Trial tier {guest_tier.id}, amount_cents: {guest_tier.amount_cents}")
        return trial_user, None
        
    except Exception as e:
        logger.error(f"Error in trial user login: {str(e)}")
        return None, {"error": "Internal error during trial login", "code": "INTERNAL_ERROR"}

async def convert_expired_trial_to_free_tier(trial_user: User, creator_id: int, db: Session) -> Tuple[User, Optional[dict]]:
    """Convert expired guest trial user to free Ko-fi tier with proper amount_cents"""
    try:
        # Deactivate Guest Trial tier association
        guest_service = GuestTrialService(db)
        guest_tier = await guest_service.get_or_create_guest_tier(creator_id)
        
        expired_association = db.query(UserTier).filter(
            and_(
                UserTier.user_id == trial_user.id,
                UserTier.tier_id == guest_tier.id
            )
        ).first()
        
        if expired_association:
            expired_association.is_active = False
            expired_association.expires_at = datetime.now(timezone.utc)
            logger.info(f"Deactivated Guest Trial tier association for expired user {trial_user.email}")
        
        # Find or create free Ko-fi tier
        free_tier = db.query(CampaignTier).filter(
            and_(
                CampaignTier.creator_id == creator_id,
                CampaignTier.is_active == True,
                CampaignTier.platform_type == "KOFI",
                or_(
                    func.lower(CampaignTier.title).contains("free"),
                    CampaignTier.amount_cents == 0
                )
            )
        ).first()
        
        if not free_tier:
            logger.info(f"Creating free Ko-fi tier for expired guest trial user")
            free_tier = CampaignTier(
                creator_id=creator_id,
                title="Free Ko-fi",
                description="Free access for expired trial users",
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
        
        # Create free tier association
        free_tier_association = UserTier(
            user_id=trial_user.id,
            tier_id=free_tier.id,
            joined_at=datetime.now(timezone.utc),
            expires_at=None,  # Free tier doesn't expire
            is_active=True,
            payment_status='free'
        )
        db.add(free_tier_association)
        
        # Update user data
        current_month = datetime.now(timezone.utc).strftime("%Y-%m")
        
        # Build free tier data WITH amount_cents
        free_tier_data = {
            'title': free_tier.title,
            'description': 'Expired Guest Trial',
            'tier_description': free_tier.description,
            'amount_cents': free_tier.amount_cents,  # Store free tier amount (0 cents)
            'album_downloads_used': 0,  # Fresh start on free tier
            'track_downloads_used': 0,
            'book_requests_used': 0,
            'kofi_user': True,
            'max_sessions': 1,
            'patron_status': 'expired_trial',
            'last_reset_month': current_month,
            'grace_period_ended_at': datetime.now(timezone.utc).isoformat(),
            'trial_expired_at': trial_user.trial_expires_at.isoformat() if trial_user.trial_expires_at else None
        }
        
        trial_user.patreon_tier_data = free_tier_data
        trial_user.role = UserRole.KOFI  # Change from GUEST to KOFI
        trial_user.grace_period_ends_at = None
        
        db.commit()
        db.refresh(trial_user)
        
        logger.info(f"✅ Converted expired guest trial user {trial_user.email} to free Ko-fi tier with amount_cents: {free_tier.amount_cents}")
        return trial_user, None
        
    except Exception as e:
        logger.error(f"Error converting expired trial user: {str(e)}")
        return None, {"error": "Error converting expired trial user", "code": "CONVERSION_ERROR"}

# ===========================
# HELPER FUNCTIONS
# ===========================

def get_client_ip(request: Request) -> str:
    """Extract client IP address from request"""
    headers_to_check = [
        'x-forwarded-for', 'x-real-ip', 'cf-connecting-ip',
        'x-forwarded', 'forwarded-for', 'forwarded'
    ]
    
    for header in headers_to_check:
        if header in request.headers:
            ip = request.headers[header].split(',')[0].strip()
            if ip and ip != 'unknown':
                return ip
    
    return request.client.host if request.client else 'unknown'

def validate_creator_pin(creator_pin: str, db: Session) -> Optional[User]:
    """Validate creator PIN and return creator user"""
    creator = db.query(User).filter(
        and_(
            User.creator_pin == creator_pin,
            User.role == UserRole.CREATOR,
            User.is_active == True
        )
    ).first()
    
    return creator

async def send_otp_email(email: str, otp_code: str, username: str, creator_name: str, 
                        background_tasks: BackgroundTasks):
    """Send OTP email to user"""
    try:
        background_tasks.add_task(
            send_guest_trial_otp_email,
            email=email,
            otp_code=otp_code,
            username=username,
            creator_name=creator_name
        )
        logger.info(f"OTP email queued for {email}")
    except Exception as e:
        logger.error(f"Error queuing OTP email: {str(e)}")
        raise

# ===========================
# API ENDPOINTS - NEW FIXED FLOW
# ===========================

@router.post("/register")
async def register_guest_trial_staged(
    request: Request,
    registration_data: GuestRegistrationRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    FIXED: Register for guest trial with staged approach.
    This endpoint does NOT record device usage - only validates and sends OTP.
    """
    try:
        client_ip = get_client_ip(request)
        
        # Use default creator (ID=1)
        creator = db.query(User).filter(
            and_(
                User.id == 1,
                User.role == UserRole.CREATOR,
                User.is_active == True
            )
        ).first()
        
        if not creator:
            logger.error(f"Default creator (ID=1) not found")
            raise HTTPException(status_code=500, detail="System configuration error")
        
        logger.info(f"Guest trial registration attempt for creator {creator.id} from {client_ip} - STAGED MODE")
        
        # Check if guest trials are enabled
        guest_service = GuestTrialService(db)
        trial_settings = guest_service.get_or_create_trial_settings(creator.id)
        
        if not trial_settings.is_enabled or trial_settings.guest_tier_amount_cents == 0:
            raise HTTPException(status_code=403, detail="Guest trials are currently disabled")
        
        # Prepare device data
        device_data = {
            'screen_width': registration_data.screen_width,
            'screen_height': registration_data.screen_height,
            'color_depth': registration_data.color_depth,
            'hardware_concurrency': registration_data.hardware_concurrency,
            'platform': registration_data.platform,
            'timezone': registration_data.timezone,
            'language': registration_data.language,
            'webauthn_available': registration_data.webauthn_available,
            'passkey_check_passed': registration_data.passkey_check_passed,
            'device_fingerprint': registration_data.device_fingerprint
        }
        
        # Comprehensive abuse check (both layers) - FOR VALIDATION ONLY
        abuse_check = await comprehensive_abuse_check(
            email=registration_data.email,
            device_data=device_data,
            creator_id=creator.id,
            existing_passkey_id=registration_data.existing_passkey_id,
            db=db
        )
        
        if not abuse_check["allowed"]:
            logger.warning(f"Registration blocked for {registration_data.email}: {abuse_check['reason']}")
            
            error_mapping = {
                "DEVICE_ALREADY_USED": "This device has already been used for a trial. Only one trial per device is allowed.",
                "TRIAL_ACTIVE": "You already have an active trial. Please check your email for login instructions.",
                "TRIAL_USED": "This email has already been used for a trial. Please consider supporting on Ko-fi.",
                "EMAIL_REGISTERED": "This email is already registered. Please use the regular login.",
                "PASSKEY_EXISTS": "This device already has a trial passkey registered. Only one trial per device is allowed.",
                "DEVICE_HAS_PASSKEY": "This device already has a trial passkey registered. Only one trial per device is allowed."
            }
            
            error_message = error_mapping.get(abuse_check["code"], abuse_check["reason"])
            raise HTTPException(status_code=409, detail=error_message)
        
        # Create OTP record with enhanced data but DON'T record device usage yet
        request_data_dict = registration_data.dict()
        request_data_dict['device_signature'] = abuse_check.get('device_signature', '')
        request_data_dict['client_ip'] = client_ip
        request_data_dict['creator_pin'] = creator.creator_pin
        
        guest_otp = guest_service.create_guest_otp_with_passkey_data(
            email=registration_data.email,
            username=registration_data.username,
            creator_pin=creator.creator_pin,
            creator_id=creator.id,
            request_data=request_data_dict
        )
        
        # Send OTP email
        await send_otp_email(
            email=registration_data.email,
            otp_code=guest_otp.otp_code,
            username=registration_data.username,
            creator_name=creator.username,
            background_tasks=background_tasks
        )
        
        db.commit()
        
        logger.info(f"✅ OTP sent to {registration_data.email} for creator {creator.id} - DEVICE NOT RECORDED YET")
        
        return {
            "status": "success",
            "message": "Verification code sent to your email",
            "otp_id": guest_otp.id,
            "expires_in_minutes": trial_settings.otp_expiry_minutes,
            "requires_passkey": guest_otp.webauthn_available,
            "layers_checked": abuse_check.get("layers_passed", ["light"]),
            "device_registered": False  # Important: Device not registered yet
        }
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error in staged trial registration: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An error occurred during registration. Please try again."
        )

@router.post("/verify-otp-staged") 
async def verify_otp_staged(
    request: Request,
    verification_data: OTPVerificationStagedRequest,
    db: Session = Depends(get_db)
):
    """
    FIXED: Verify OTP but DON'T create user or record device yet.
    This is stage 1 of 2-stage atomic registration.
    """
    try:
        client_ip = get_client_ip(request)
        
        # Get OTP record
        guest_otp = db.query(GuestOTP).filter(
            and_(
                GuestOTP.id == verification_data.otp_id,
                GuestOTP.is_used == False
            )
        ).first()
        
        if not guest_otp:
            raise HTTPException(status_code=404, detail="Invalid or expired verification code")
        
        # Check if OTP is expired
        if guest_otp.is_expired:
            raise HTTPException(status_code=410, detail="Verification code has expired. Please request a new one.")
        
        # Check attempt count
        if guest_otp.attempt_count >= guest_otp.max_attempts:
            guest_otp.is_used = True
            db.commit()
            raise HTTPException(status_code=429, detail="Too many verification attempts. Please request a new code.")
        
        # Increment attempt count
        guest_otp.attempt_count += 1
        
        # Verify OTP code
        if guest_otp.otp_code != verification_data.otp_code:
            db.commit()
            remaining_attempts = guest_otp.max_attempts - guest_otp.attempt_count
            if remaining_attempts > 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid verification code. {remaining_attempts} attempts remaining."
                )
            else:
                guest_otp.is_used = True
                db.commit()
                raise HTTPException(
                    status_code=429,
                    detail="Too many failed verification attempts. Please request a new code."
                )
        
        # ✅ OTP IS VALID - Mark as verified but DON'T create user yet
        guest_otp.otp_verified = True  # New field to track OTP verification
        guest_otp.verified_at = datetime.now(timezone.utc)
        
        # DON'T mark as used yet - keep it available for completion
        # DON'T record device usage yet
        
        db.commit()
        
        logger.info(f"✅ OTP verified for {guest_otp.email} - ready for completion (device not recorded)")
        
        return {
            "status": "success",
            "message": "OTP verified successfully",
            "requires_passkey": guest_otp.webauthn_available,
            "next_step": "complete_registration"
        }
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error in staged OTP verification: {str(e)}")
        raise HTTPException(status_code=500, detail="Verification error")

@router.post("/complete-registration")
async def complete_atomic_registration(
    request: Request,
    completion_data: AtomicRegistrationRequest,
    db: Session = Depends(get_db)
):
    """
    FIXED: Atomic registration completion.
    Creates user + Records device + Stores passkey in a single transaction.
    """
    try:
        client_ip = get_client_ip(request)
        
        # Get verified OTP record
        guest_otp = db.query(GuestOTP).filter(
            and_(
                GuestOTP.id == completion_data.otp_id,
                GuestOTP.otp_verified == True,  # Must be verified
                GuestOTP.is_used == False,      # But not used yet
                GuestOTP.registration_completed == False,
                GuestOTP.registration_aborted == False
            )
        ).first()
        
        if not guest_otp:
            raise HTTPException(status_code=404, detail="Invalid or expired registration session")
        
        # Get creator
        creator = db.query(User).filter(User.id == guest_otp.creator_id).first()
        if not creator:
            raise HTTPException(status_code=404, detail="Creator not found")
        
        # Initialize services
        guest_service = GuestTrialService(db)
        trial_settings = guest_service.get_or_create_trial_settings(creator.id)
        
        # ✅ ATOMIC TRANSACTION START
        try:
            logger.info(f"🔄 Starting atomic registration for {guest_otp.email}")
            
            # 1. CREATE USER
            trial_start = datetime.now(timezone.utc)
            trial_expires = trial_start + timedelta(hours=trial_settings.trial_duration_hours)
            guest_identifier = f"guest_{creator.id}_{int(trial_start.timestamp())}_{secrets.token_urlsafe(8)}"
            
            guest_user = User(
                email=guest_otp.email,
                username=guest_otp.username,
                role=UserRole.GUEST,
                created_by=creator.id,
                campaign_id=str(creator.campaigns[0].id) if creator.campaigns else None,
                is_active=True,
                is_guest_trial=True,
                trial_started_at=trial_start,
                trial_expires_at=trial_expires,
                guest_device_fingerprint=guest_otp.device_fingerprint or '',
                guest_ip_address=client_ip,
                guest_identifier=guest_identifier,
                created_at=trial_start
            )
            
            db.add(guest_user)
            db.flush()  # Get user ID
            logger.info(f"✅ User created: {guest_user.id}")
            
            # 2. CREATE TIER ASSOCIATION
            guest_tier = await guest_service.get_or_create_guest_tier(creator.id)
            
            user_tier_association = UserTier(
                user_id=guest_user.id,
                tier_id=guest_tier.id,
                joined_at=trial_start,
                expires_at=trial_expires,
                is_active=True,
                payment_status='guest_trial'
            )
            db.add(user_tier_association)
            logger.info(f"✅ Tier association created: User {guest_user.id} → Tier {guest_tier.id}")
            
            # 3. STORE TIER DATA
            tier_data = {
                'title': guest_tier.title,
                'platform': 'kofi',
                'platform_type': 'KOFI',
                'amount_cents': guest_tier.amount_cents,
                'guest_trial': True,
                'patron_status': 'active_trial',
                'trial_started_at': trial_start.isoformat(),
                'trial_expires_at': trial_expires.isoformat(),
                'trial_duration_hours': trial_settings.trial_duration_hours,
                'album_downloads_used': 0,
                'track_downloads_used': 0,
                'book_requests_used': 0,
                'last_reset_month': datetime.now(timezone.utc).strftime("%Y-%m"),
                'max_sessions': 1,
                'kofi_user': True,
                'guest_identifier': guest_identifier,
                'device_signature': guest_otp.device_fingerprint or '',
                'registration_ip': client_ip,
                'passkey_protected': bool(completion_data.passkey_data)
            }
            
            guest_user.patreon_tier_data = tier_data
            logger.info(f"✅ Tier data stored with amount_cents: {guest_tier.amount_cents}")
            
            # 4. STORE PASSKEY (if provided)
            passkey_stored = False
            if completion_data.passkey_data:
                try:
                    passkey_credential = GuestPasskeyCredential(
                        credential_id=completion_data.passkey_data.credentialId,
                        creator_id=creator.id,
                        user_id=guest_user.id,
                        public_key=completion_data.passkey_data.publicKey,
                        attestation_object=completion_data.passkey_data.attestationObject,
                        client_data_json=completion_data.passkey_data.clientDataJSON,
                        email=guest_otp.email,
                        username=guest_otp.username,
                        device_fingerprint=guest_otp.device_fingerprint,
                        is_active=True
                    )
                    
                    db.add(passkey_credential)
                    passkey_stored = True
                    
                    logger.info(f"✅ Passkey stored: {completion_data.passkey_data.credentialId[:16]}...")
                    
                except Exception as e:
                    logger.error(f"Passkey storage failed: {str(e)}")
                    # Rollback entire transaction if passkey storage fails
                    raise HTTPException(status_code=500, detail="Failed to store device security credential")
            
            # 5. ✅ RECORD DEVICE USAGE (ONLY NOW, AFTER EVERYTHING SUCCEEDS)
            if completion_data.register_device and guest_otp.device_fingerprint:
                device_data = {
                    'screen_width': 0,  # Could be extracted from request_data if needed
                    'screen_height': 0,
                    'platform': 'unknown',
                    'language': guest_otp.username  # Use as identifier
                }
                
                record_device_signature_usage(
                    device_signature=guest_otp.device_fingerprint,
                    device_data=device_data,
                    creator_id=creator.id,
                    db=db
                )
                
                # Update device tracking with passkey info if applicable
                if passkey_stored:
                    device_tracking = db.query(GuestDeviceTracking).filter(
                        and_(
                            GuestDeviceTracking.device_fingerprint == guest_otp.device_fingerprint,
                            GuestDeviceTracking.creator_id == creator.id
                        )
                    ).first()
                    
                    if device_tracking:
                        device_tracking.passkey_credential_id = completion_data.passkey_data.credentialId
                        device_tracking.passkey_created_at = datetime.now(timezone.utc)
                
                logger.info(f"✅ Device signature recorded: {guest_otp.device_fingerprint[:16]}...")
            
            # 6. MARK REGISTRATION AS COMPLETED
            guest_otp.is_used = True
            guest_otp.registration_completed = True
            guest_otp.completed_at = datetime.now(timezone.utc)
            # After successful registration, increment counters
            guest_service = GuestTrialService(db)
            guest_service.increment_registration_count(creator.id, success=True)
            
            # ✅ COMMIT ENTIRE ATOMIC TRANSACTION
            db.commit()
            db.refresh(guest_user)

            
            logger.info(f"✅ ATOMIC REGISTRATION COMPLETED: User {guest_user.id} → "
                       f"Tier {guest_tier.id} → Device recorded → Passkey: {passkey_stored}")
            
            return {
                "status": "success",
                "message": "Trial activated successfully",
                "user_id": guest_user.id,
                "trial_expires_at": trial_expires.isoformat(),
                "trial_duration_hours": trial_settings.trial_duration_hours,
                "access_token": guest_identifier,
                "creator_pin": creator.creator_pin,
                "passkey_protected": passkey_stored,
                "device_registered": completion_data.register_device,
                "tier_benefits": {
                    "album_downloads_allowed": guest_tier.album_downloads_allowed,
                    "track_downloads_allowed": guest_tier.track_downloads_allowed,
                    "book_requests_allowed": guest_tier.book_requests_allowed,
                    "max_sessions": 1,
                    "amount_cents": guest_tier.amount_cents
                }
            }
            
        except Exception as atomic_error:
            # ✅ ROLLBACK ENTIRE TRANSACTION ON ANY FAILURE
            db.rollback()
            logger.error(f"❌ Atomic registration failed: {str(atomic_error)}")
            
            # Mark OTP as aborted to prevent retry issues
            guest_otp.registration_aborted = True
            guest_otp.abort_reason = f"atomic_failure: {str(atomic_error)[:100]}"
            db.commit()
            
            raise HTTPException(
                status_code=500,
                detail="Registration failed. Please try again with a new verification code."
            )
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error in atomic registration: {str(e)}")
        raise HTTPException(status_code=500, detail="Registration error")

@router.post("/abort-registration")
async def abort_guest_registration_enhanced(
    request: Request,
    abort_data: AbortRegistrationRequest,
    db: Session = Depends(get_db)
):
    """
    ENHANCED: Abort registration without recording device usage.
    This prevents device blocking when registration fails.
    """
    try:
        guest_otp = db.query(GuestOTP).filter(
            and_(
                GuestOTP.id == abort_data.otp_id,
                GuestOTP.registration_completed == False
            )
        ).first()
        
        if not guest_otp:
            logger.warning(f"Abort requested for non-existent OTP ID: {abort_data.otp_id}")
            return {"status": "success", "message": "Registration session not found"}
        
        # Mark as aborted (but don't record device usage)
        guest_otp.registration_aborted = True
        guest_otp.abort_reason = abort_data.reason
        guest_otp.is_used = True  # Prevent further use
        guest_otp.aborted_at = datetime.now(timezone.utc)
        
        db.commit()
        
        logger.info(f"✅ Registration aborted for {guest_otp.email}: {abort_data.reason} - Device NOT recorded")
        
        return {
            "status": "success",
            "message": "Registration cancelled successfully",
            "device_protected": True  # Device is not marked as used
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error aborting registration: {str(e)}")
        raise HTTPException(status_code=500, detail="Error cancelling registration")

@router.post("/resend-otp")
async def resend_guest_otp(
    request: Request,
    resend_data: OTPResendRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Resend OTP for guest trial registration"""
    try:
        # Get OTP record
        guest_otp = db.query(GuestOTP).filter(
            and_(
                GuestOTP.id == resend_data.otp_id,
                GuestOTP.is_used == False
            )
        ).first()
        
        if not guest_otp:
            raise HTTPException(status_code=404, detail="Invalid or expired verification request")
        
        # Check if resend is allowed
        if not guest_otp.can_resend:
            if guest_otp.resend_count >= guest_otp.max_resends:
                raise HTTPException(
                    status_code=429,
                    detail="Maximum resend attempts reached. Please start a new registration."
                )
            else:
                raise HTTPException(
                    status_code=429,
                    detail="Please wait before requesting another code"
                )
        
        # Generate new OTP
        guest_service = GuestTrialService(db)
        new_otp = guest_service.generate_otp()
        
        # Update OTP record
        guest_otp.otp_code = new_otp
        guest_otp.resend_count += 1
        guest_otp.last_resend = datetime.now(timezone.utc)
        guest_otp.attempt_count = 0
        guest_otp.otp_verified = False  # Reset verification status
        
        # Extend expiry time
        trial_settings = guest_service.get_or_create_trial_settings(guest_otp.creator_id)
        guest_otp.expires_at = datetime.now(timezone.utc) + timedelta(minutes=trial_settings.otp_expiry_minutes)
        
        # Get creator
        creator = db.query(User).filter(User.id == guest_otp.creator_id).first()
        
        # Send new OTP email
        await send_otp_email(
            email=guest_otp.email,
            otp_code=new_otp,
            username=guest_otp.username,
            creator_name=creator.username if creator else "Creator",
            background_tasks=background_tasks
        )
        
        db.commit()
        
        logger.info(f"Guest trial OTP resent to {guest_otp.email}")
        
        return {
            "status": "success",
            "message": "New verification code sent",
            "resends_remaining": guest_otp.max_resends - guest_otp.resend_count,
            "expires_in_minutes": trial_settings.otp_expiry_minutes
        }
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error resending guest OTP: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An error occurred while resending code. Please try again."
        )

# ===========================
# LEGACY/REDIRECT ENDPOINTS
# ===========================

@router.post("/login")
async def redirect_guest_trial_login(
    request: Request,
    email: str = Form(...),
    creator_pin: str = Form(...),
    db: Session = Depends(get_db)
):
    """
    REDIRECT: Guest trial login now uses the unified Ko-fi login flow.
    This endpoint redirects to maintain backward compatibility.
    """
    try:
        from kofi_routes import handle_kofi_login
        
        logger.info(f"Redirecting guest trial login to unified Ko-fi login for {email}")
        
        # Use the unified login flow
        user, error_response = await handle_kofi_login(email, creator_pin, db, request)
        
        if error_response:
            return error_response
        
        if user:
            if user.is_guest_trial:
                logger.info(f"✅ Guest trial login successful via unified flow: {email} (User ID: {user.id})")
                return JSONResponse(content={
                    "status": "success",
                    "message": "Trial login successful",
                    "user_id": user.id,
                    "trial_expires_at": user.trial_expires_at.isoformat() if user.trial_expires_at else None,
                    "redirect_url": "/dashboard"
                })
            else:
                logger.info(f"✅ Ko-fi user login successful via guest trial endpoint: {email} (User ID: {user.id})")
                return JSONResponse(content={
                    "status": "success",
                    "message": "Login successful",
                    "user_id": user.id,
                    "redirect_url": "/dashboard"
                })
        else:
            return JSONResponse(
                status_code=404,
                content={"error": "User not found", "code": "USER_NOT_FOUND"}
            )
        
    except Exception as e:
        logger.error(f"Error in guest trial login redirect: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error", "code": "INTERNAL_ERROR"}
        )

# ===========================
# STATUS AND UTILITY ENDPOINTS
# ===========================

@router.get("/status/{guest_identifier}")
async def get_guest_trial_status(
    guest_identifier: str,
    db: Session = Depends(get_db)
):
    """Get status of guest trial"""
    try:
        guest_user = db.query(User).filter(
            and_(
                User.guest_identifier == guest_identifier,
                User.role == UserRole.GUEST,
                User.is_guest_trial == True
            )
        ).first()
        
        if not guest_user:
            raise HTTPException(status_code=404, detail="Guest trial not found")
        
        return {
            "status": guest_user.trial_status,
            "trial_active": guest_user.trial_active,
            "hours_remaining": guest_user.trial_hours_remaining,
            "expires_at": guest_user.trial_expires_at.isoformat() if guest_user.trial_expires_at else None,
            "tier_benefits": guest_user.patreon_tier_data
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting guest trial status: {str(e)}")
        raise HTTPException(status_code=500, detail="Error retrieving trial status")

@router.post("/get-pin")
async def get_guest_trial_pin(
    request: Request,
    db: Session = Depends(get_db)
):
    """Get PIN for existing trial user"""
    try:
        data = await request.json()
        email = data.get("email")
        
        if not email:
            raise HTTPException(status_code=400, detail="Email required")
        
        # Find trial user
        trial_user = db.query(User).filter(
            and_(
                func.lower(User.email) == email.lower(),
                User.role == UserRole.GUEST,
                User.is_guest_trial == True
            )
        ).first()
        
        if not trial_user:
            raise HTTPException(status_code=404, detail="Trial user not found")
        
        # Get creator PIN
        creator = db.query(User).filter(User.id == trial_user.created_by).first()
        if not creator:
            raise HTTPException(status_code=404, detail="Creator not found")
        
        return {
            "status": "success",
            "pin": creator.creator_pin
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting trial PIN: {str(e)}")
        raise HTTPException(status_code=500, detail="Error retrieving PIN")

# ===========================
# ADMIN ENDPOINTS
# ===========================

@router.get("/admin/settings")
async def get_guest_trial_settings(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get guest trial settings for creator"""
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Creator access required")
    
    guest_service = GuestTrialService(db)
    settings = guest_service.get_or_create_trial_settings(current_user.id)
    
    return {
        "is_enabled": settings.is_enabled,
        "trial_duration_hours": settings.trial_duration_hours,
        "guest_tier_amount_cents": settings.guest_tier_amount_cents,
        "max_trials_per_ip_per_day": settings.max_trials_per_ip_per_day,
        "max_trials_per_email_per_week": settings.max_trials_per_email_per_week,
        "max_trials_per_device_per_month": settings.max_trials_per_device_per_month,
        "otp_expiry_minutes": settings.otp_expiry_minutes,
        "max_otp_attempts": settings.max_otp_attempts,
        "max_otp_resends": settings.max_otp_resends
    }

@router.post("/admin/settings")
async def update_guest_trial_settings(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Update guest trial settings for creator"""
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Creator access required")
    
    try:
        data = await request.json()
        
        guest_service = GuestTrialService(db)
        settings = guest_service.get_or_create_trial_settings(current_user.id)
        
        # Update settings and guest tier if amount changed
        old_amount = settings.guest_tier_amount_cents
        settings.is_enabled = data.get("is_enabled", settings.is_enabled)
        settings.trial_duration_hours = data.get("trial_duration_hours", settings.trial_duration_hours)
        settings.guest_tier_amount_cents = data.get("guest_tier_amount_cents", settings.guest_tier_amount_cents)
        settings.max_trials_per_ip_per_day = data.get("max_trials_per_ip_per_day", settings.max_trials_per_ip_per_day)
        settings.max_trials_per_email_per_week = data.get("max_trials_per_email_per_week", settings.max_trials_per_email_per_week)
        settings.max_trials_per_device_per_month = data.get("max_trials_per_device_per_month", settings.max_trials_per_device_per_month)
        
        # Update existing guest tier if amount changed
        if old_amount != settings.guest_tier_amount_cents:
            existing_guest_tier = db.query(CampaignTier).filter(
                and_(
                    CampaignTier.creator_id == current_user.id,
                    CampaignTier.title == "Guest Trial",
                    CampaignTier.platform_type == "KOFI",
                    CampaignTier.is_active == True
                )
            ).first()
            
            if existing_guest_tier:
                existing_guest_tier.amount_cents = settings.guest_tier_amount_cents
                existing_guest_tier.updated_at = datetime.now(timezone.utc)
                logger.info(f"Updated existing guest tier amount from {old_amount} to {settings.guest_tier_amount_cents} cents")
        
        db.commit()
        
        return {
            "status": "success", 
            "message": "Settings updated",
            "guest_tier_amount_cents": settings.guest_tier_amount_cents
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating guest trial settings: {str(e)}")
        raise HTTPException(status_code=500, detail="Error updating settings")

@router.post("/admin/test")
async def test_guest_trial_system(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Test guest trial system functionality"""
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Creator access required")
    
    try:
        guest_service = GuestTrialService(db)
        settings = guest_service.get_or_create_trial_settings(current_user.id)
        guest_tier = await guest_service.get_or_create_guest_tier(current_user.id)
        
        return {
            "status": "success",
            "message": "Guest trial system is working correctly",
            "is_enabled": settings.is_enabled,
            "trial_duration_hours": settings.trial_duration_hours,
            "guest_tier_amount_cents": guest_tier.amount_cents,
            "guest_tier_id": guest_tier.id
        }
        
    except Exception as e:
        logger.error(f"Error testing guest trial system: {str(e)}")
        raise HTTPException(status_code=500, detail="Error testing guest trial system")
@router.get("/admin/registration-stats")
async def get_registration_statistics(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get comprehensive registration statistics"""
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Creator access required")
    
    guest_service = GuestTrialService(db)
    stats = guest_service.get_registration_stats(current_user.id)
    
    return {
        "status": "success",
        "stats": stats
    }

@router.post("/admin/reset-counters")
async def reset_registration_counters(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Reset registration counters"""
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Creator access required")
    
    try:
        data = await request.json()
        reset_daily = data.get("reset_daily", True)
        reset_total = data.get("reset_total", False)
        
        guest_service = GuestTrialService(db)
        guest_service.reset_counters(current_user.id, reset_daily, reset_total)
        
        return {
            "status": "success",
            "message": f"Counters reset - Daily: {reset_daily}, Total: {reset_total}"
        }
        
    except Exception as e:
        logger.error(f"Error resetting counters: {str(e)}")
        raise HTTPException(status_code=500, detail="Error resetting counters")

@router.get("/admin/settings")
async def get_guest_trial_settings(
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Get guest trial settings with registration limits"""
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Creator access required")
    
    guest_service = GuestTrialService(db)
    settings = guest_service.get_or_create_trial_settings(current_user.id)
    
    return {
        "is_enabled": settings.is_enabled,
        "trial_duration_hours": settings.trial_duration_hours,
        "guest_tier_amount_cents": settings.guest_tier_amount_cents,
        "max_trials_per_ip_per_day": settings.max_trials_per_ip_per_day,
        "max_trials_per_email_per_week": settings.max_trials_per_email_per_week,
        "max_trials_per_device_per_month": settings.max_trials_per_device_per_month,
        "otp_expiry_minutes": settings.otp_expiry_minutes,
        "max_otp_attempts": settings.max_otp_attempts,
        "max_otp_resends": settings.max_otp_resends,
        # 🚀 NEW FIELDS
        "registration_limit_enabled": settings.registration_limit_enabled,
        "max_daily_registrations": settings.max_daily_registrations,
        "max_total_registrations": settings.max_total_registrations,
        "enable_daily_reset": settings.enable_daily_reset,
        "current_daily_count": settings.current_daily_count,
        "current_total_count": settings.current_total_count,
        "last_daily_reset": settings.last_daily_reset.isoformat() if settings.last_daily_reset else None
    }


@router.post("/admin/settings")
async def update_guest_trial_settings(
    request: Request,
    current_user: User = Depends(login_required),
    db: Session = Depends(get_db)
):
    """Update guest trial settings including registration limits"""
    if not current_user.is_creator:
        raise HTTPException(status_code=403, detail="Creator access required")
    
    try:
        data = await request.json()
        
        guest_service = GuestTrialService(db)
        settings = guest_service.get_or_create_trial_settings(current_user.id)
        
        # Update existing settings
        settings.is_enabled = data.get("is_enabled", settings.is_enabled)
        settings.trial_duration_hours = data.get("trial_duration_hours", settings.trial_duration_hours)
        settings.guest_tier_amount_cents = data.get("guest_tier_amount_cents", settings.guest_tier_amount_cents)
        settings.max_trials_per_ip_per_day = data.get("max_trials_per_ip_per_day", settings.max_trials_per_ip_per_day)
        settings.max_trials_per_email_per_week = data.get("max_trials_per_email_per_week", settings.max_trials_per_email_per_week)
        settings.max_trials_per_device_per_month = data.get("max_trials_per_device_per_month", settings.max_trials_per_device_per_month)
        
        # 🚀 NEW: Update registration limit settings
        settings.registration_limit_enabled = data.get("registration_limit_enabled", settings.registration_limit_enabled)
        settings.max_daily_registrations = data.get("max_daily_registrations", settings.max_daily_registrations)
        settings.max_total_registrations = data.get("max_total_registrations", settings.max_total_registrations)
        settings.enable_daily_reset = data.get("enable_daily_reset", settings.enable_daily_reset)
        
        # Update guest tier amount if changed
        old_amount = settings.guest_tier_amount_cents
        if old_amount != settings.guest_tier_amount_cents:
            existing_guest_tier = db.query(CampaignTier).filter(
                and_(
                    CampaignTier.creator_id == current_user.id,
                    CampaignTier.title == "Guest Trial",
                    CampaignTier.platform_type == "KOFI",
                    CampaignTier.is_active == True
                )
            ).first()
            
            if existing_guest_tier:
                existing_guest_tier.amount_cents = settings.guest_tier_amount_cents
                existing_guest_tier.updated_at = datetime.now(timezone.utc)
        
        db.commit()
        
        return {
            "status": "success", 
            "message": "Settings updated",
            "registration_limits_enabled": settings.registration_limit_enabled
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating guest trial settings: {str(e)}")
        raise HTTPException(status_code=500, detail="Error updating settings")