from fastapi import APIRouter, Request, HTTPException, Depends, Header
from sqlalchemy.orm import Session
from typing import Optional
import hmac
import hashlib
import json
from datetime import datetime, timezone

router = APIRouter(prefix="/auth", tags=["auth"])

PATREON_CLIENT_ID = os.getenv("PATREON_CLIENT_ID")
PATREON_CLIENT_SECRET = os.getenv("PATREON_CLIENT_SECRET")
PATREON_REDIRECT_URI = "https://arriving-cool-condor.ngrok-free.app/auth/patreon/callback"

class PatreonOAuth:
    def __init__(self):
        self.client_id = PATREON_CLIENT_ID
        self.client_secret = PATREON_CLIENT_SECRET
        self.redirect_uri = PATREON_REDIRECT_URI
        self.oauth_url = "https://www.patreon.com/oauth2/authorize"
        self.token_url = "https://www.patreon.com/api/oauth2/token"
        self.identity_url = "https://www.patreon.com/api/oauth2/v2/identity"

    def get_oauth_url(self, state: str = None) -> str:
        """Generate OAuth URL for Patreon login"""
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": "identity identity[email] identity.memberships campaigns campaigns.members",
        }
        if state:
            params["state"] = state
        return f"{self.oauth_url}?{urlencode(params)}"

    async def get_tokens(self, code: str) -> dict:
        """Exchange authorization code for tokens"""
        data = {
            "code": code,
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(self.token_url, data=data)
            if response.status_code != 200:
                raise HTTPException(status_code=400, detail="Failed to get tokens")
            return response.json()

    async def get_user_info(self, access_token: str) -> dict:
        """Get Patreon user information"""
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json"
        }
        params = {
            "include": "memberships,memberships.currently_entitled_tiers",
            "fields[user]": "email,full_name,is_email_verified",
            "fields[tier]": "title,amount_cents"
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                self.identity_url,
                headers=headers,
                params=params
            )
            if response.status_code != 200:
                raise HTTPException(status_code=400, detail="Failed to get user info")
            return response.json()

patreon_oauth = PatreonOAuth()

@router.get("/patreon/login")
async def patreon_login(request: Request):
    """Initialize Patreon OAuth flow"""
    # Generate state token to prevent CSRF
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state
    
    # Redirect to Patreon OAuth URL
    return RedirectResponse(patreon_oauth.get_oauth_url(state))

@router.get("/patreon/callback")
async def patreon_callback(
    request: Request,
    code: str,
    state: str,
    db: Session = Depends(get_db)
):
    """Handle Patreon OAuth callback"""
    # Verify state token
    stored_state = request.session.get("oauth_state")
    if not stored_state or stored_state != state:
        raise HTTPException(status_code=400, detail="Invalid state token")
    
    try:
        # Exchange code for tokens
        tokens = await patreon_oauth.get_tokens(code)
        access_token = tokens["access_token"]
        refresh_token = tokens.get("refresh_token")
        
        # Get user info from Patreon
        user_data = await patreon_oauth.get_user_info(access_token)
        
        # Extract user details
        patreon_id = user_data["data"]["id"]
        attributes = user_data["data"]["attributes"]
        email = attributes.get("email")
        
        # Get membership info
        memberships = [
            item for item in user_data.get("included", [])
            if item["type"] == "member"
        ]
        
        if not memberships:
            raise HTTPException(status_code=400, detail="No active Patreon membership found")
            
        # Find or create user
        user = db.query(User).filter(User.patreon_id == patreon_id).first()
        if not user:
            user = User(
                email=email,
                username=f"patron_{patreon_id}",
                patreon_id=patreon_id,
                role=UserRole.PATREON,
                is_active=True
            )
            db.add(user)
        
        # Update user's Patreon data
        membership = memberships[0]
        user.patreon_tier_data = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "tier_info": membership.get("relationships", {})
                                    .get("currently_entitled_tiers", {})
                                    .get("data", [])
        }
        
        user.last_login = datetime.now(timezone.utc)
        db.commit()
        
        # Set session data
        request.session["user"] = user.email
        request.session["role"] = UserRole.PATREON.value
        request.session["patreon_access_token"] = access_token
        
        return RedirectResponse(url="/home", status_code=303)
        
    except Exception as e:
        print(f"Patreon callback error: {str(e)}")
        raise HTTPException(status_code=500, detail="Error processing Patreon login")

# Add refresh token endpoint
@router.post("/patreon/refresh")
async def refresh_patreon_token(
    request: Request,
    db: Session = Depends(get_db)
):
    """Refresh Patreon access token"""
    user_email = request.session.get("user")
    if not user_email:
        raise HTTPException(status_code=401, detail="Not authenticated")
        
    user = db.query(User).filter(User.email == user_email).first()
    if not user or not user.patreon_tier_data:
        raise HTTPException(status_code=401, detail="No Patreon data found")
        
    refresh_token = user.patreon_tier_data.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=401, detail="No refresh token found")
        
    try:
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": PATREON_CLIENT_ID,
            "client_secret": PATREON_CLIENT_SECRET
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(PATREON_OAUTH_TOKEN_URL, data=data)
            tokens = response.json()
            
            # Update user's tokens
            user.patreon_tier_data["access_token"] = tokens["access_token"]
            user.patreon_tier_data["refresh_token"] = tokens.get("refresh_token", refresh_token)
            db.commit()
            
            request.session["patreon_access_token"] = tokens["access_token"]
            return {"status": "success"}
            
    except Exception as e:
        print(f"Token refresh error: {str(e)}")
        raise HTTPException(status_code=500, detail="Error refreshing token")