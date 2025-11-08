# session_manager.py
import secrets
from uuid import uuid4
from fastapi import Request, Response, HTTPException
from sqlalchemy import and_, func, text
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any
from datetime import datetime, timedelta, timezone
import logging
from models import User, UserSession, UserRole, CampaignTier
from patreon_client import patreon_client

logger = logging.getLogger(__name__)

class SessionManager:
    def __init__(
        self,
        secret_key: str,
        max_regular_sessions: int = 2,
        max_creator_sessions: int = 5,
        session_expire: int = 7 * 24 * 3600,
        extended_session_expire: int = 30 * 24 * 3600,  # 30 days
        cookie_settings: Optional[Dict] = None
    ):
        self.secret_key = secret_key
        self.max_regular_sessions = max_regular_sessions
        self.max_creator_sessions = max_creator_sessions
        self.session_expire = session_expire
        self.extended_session_expire = extended_session_expire
        
        self.cookie_settings = {
            "httponly": True,
            "secure": True,
            "samesite": "lax",
            "path": "/"
        }
        if cookie_settings:
            self.cookie_settings.update(cookie_settings)


    async def create_session(
        self,
        db: Session,
        user: User,
        request: Request,
        response: Response,
        remember_me: bool = False
    ) -> Dict[str, Any]:
        """Create new session with proper cookie handling"""
        try:
            logger.info(f"Starting session creation for user: {user.email}")
            
            # Check session limits first
            limits = await self.check_session_limits(user.id, db)
            if not limits["allowed"]:
                raise HTTPException(
                    status_code=400, 
                    detail=limits["reason"]
                )
            
            # Create session with explicit timestamp
            current_time = datetime.now(timezone.utc)
            new_session = UserSession(
                uuid=str(uuid4()),
                session_id=str(uuid4()),
                user_id=user.id,
                device_id=request.headers.get("X-Device-ID", "web"),
                device_type=request.headers.get("X-Device-Type", "web"),
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                is_active=True,
                is_extended=bool(remember_me),
                expires_at=current_time + timedelta(
                    seconds=self.extended_session_expire if remember_me else self.session_expire
                ),
                last_active=current_time,
                created_at=current_time,
                session_data={
                    "user_id": user.id,
                    "email": user.email,
                    "role": user.role.value,
                    "authenticated": True
                }
            )

            db.add(new_session)
            
            try:
                db.flush()
                db.refresh(new_session)
                logger.info(f"Created session ID: {new_session.session_id}")

                # Set the cookie with proper settings
                cookie_max_age = self.extended_session_expire if remember_me else self.session_expire
                response.set_cookie(
                    key="session_id",
                    value=new_session.session_id,
                    max_age=cookie_max_age,
                    **self.cookie_settings
                )

                # Session state is in PostgreSQL (new_session.session_data)
                # No need to update request.session - we removed SessionMiddleware

                db.commit()
                logger.info(f"Session created and cookie set for {user.email}")

                return {
                    "session": new_session,
                    "session_id": new_session.session_id
                }

            except Exception as e:
                db.rollback()
                logger.error(f"Session creation failed: {str(e)}")
                raise

        except Exception as e:
            logger.error(f"Session creation error: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to create session: {str(e)}")


    async def verify_session(self, request: Request, response: Response, db: Session) -> Optional[User]:
        """Verify session and return user if valid"""
        try:
            session_id = request.cookies.get("session_id")
            if not session_id:
                logger.info("No session ID found in cookies")
                return None

            # Query both session and user in one go to avoid multiple database hits
            user_session = (
                db.query(UserSession)
                .filter(
                    and_(
                        UserSession.session_id == session_id,
                        UserSession.is_active == True,
                        UserSession.expires_at > datetime.now(timezone.utc)
                    )
                )
                .first()
            )

            if not user_session:
                logger.warning(f"Invalid or expired session {session_id}")
                await self.invalidate_session(session_id, request, response, db)
                return None

            # Get user using the same db session
            user = (
                db.query(User)
                .filter(User.id == user_session.user_id)
                .first()
            )

            if not user or not user.is_active:
                logger.warning(f"User not found or inactive for session {session_id}")
                await self.invalidate_session(session_id, request, response, db)
                return None

            # Update last_active
            user_session.last_active = datetime.now(timezone.utc)
            db.commit()

            return user

        except Exception as e:
            logger.error(f"Session verification error: {str(e)}")
            # On error, clear session to be safe
            await self.invalidate_session(session_id, request, response, db) if session_id else None
            db.rollback()
            return None

    async def invalidate_session(self, session_id: str, request: Request, response: Response, db: Session) -> None:
        """Invalidate a session and clear related cookies and data"""
        try:
            # 1. Update database session record
            session = (
                db.query(UserSession)
                .filter(UserSession.session_id == session_id)
                .first()
            )

            if session:
                session.is_active = False
                session.ended_at = datetime.now(timezone.utc)
                session.updated_at = datetime.now(timezone.utc)
                db.commit()
                logger.info(f"Marked session {session_id} as inactive in database")

            # 2. Clear session cookie with all security flags
            response.delete_cookie(
                key="session_id",
                path="/",
                secure=True,
                httponly=True,
                samesite="lax"
            )

            # 3. Session data already cleared in PostgreSQL (marked inactive above)
            # No need to clear request.session - we removed SessionMiddleware

            # 4. Clear any other related cookies
            for cookie_name in ['remember_token', 'user_preferences']:  # Add any other related cookies
                if cookie_name in request.cookies:
                    response.delete_cookie(
                        key=cookie_name,
                        path="/",
                        secure=True,
                        httponly=True,
                        samesite="lax"
                    )

            logger.info(f"Successfully invalidated session {session_id}")

        except Exception as e:
            logger.error(f"Error invalidating session {session_id}: {str(e)}")
            db.rollback()
            # Still try to clear cookies even if DB operation failed
            response.delete_cookie(
                key="session_id",
                path="/",
                secure=True,
                httponly=True,
                samesite="lax"
            )
    async def rotate_sessions(
        self,
        user_id: int,
        db: Session,
        current_session_id: Optional[str] = None
    ) -> bool:
        """Remove oldest session when at limit to make room for new one"""
        try:
            active_sessions = db.query(UserSession).filter(
                and_(
                    UserSession.user_id == user_id,
                    UserSession.is_active == True,
                    UserSession.expires_at > datetime.now(timezone.utc)
                )
            ).order_by(UserSession.last_active.asc()).all()  # Ordered by oldest first

            if not active_sessions:
                return True

            # If current session is in the list, don't count it toward limit
            if current_session_id:
                active_sessions = [s for s in active_sessions if s.session_id != current_session_id]

            # Get oldest session and deactivate it
            if active_sessions:
                oldest_session = active_sessions[0]
                oldest_session.is_active = False
                oldest_session.ended_at = datetime.now(timezone.utc)
                oldest_session.updated_at = datetime.now(timezone.utc)
                db.commit()
                logger.info(f"Rotated out oldest session {oldest_session.session_id} for user {user_id}")
                return True

            return True

        except Exception as e:
            logger.error(f"Error rotating sessions: {str(e)}")
            return False

    async def check_session_limits(
        self,
        user_id: int,
        db: Session,
        current_session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Check session limits and rotate if needed"""
        try:
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                return {
                    "allowed": False,
                    "reason": "User not found",
                    "max_sessions": 0,
                    "active_sessions": 0
                }

            active_sessions = db.query(UserSession).filter(
                and_(
                    UserSession.user_id == user_id,
                    UserSession.is_active == True,
                    UserSession.expires_at > datetime.now(timezone.utc)
                )
            ).order_by(UserSession.last_active.desc()).all()

            # Determine max sessions
            if user.is_creator:
                max_sessions = self.max_creator_sessions
            elif user.is_team:
                max_sessions = self.max_regular_sessions
            else:
                tier_data = user.patreon_tier_data or {}
                max_sessions = tier_data.get('max_sessions', self.max_regular_sessions)

            current_count = len(active_sessions)
            logger.info(f"User {user.email} has {current_count} active sessions (max: {max_sessions})")

            # If this is an existing session, always allow
            if current_session_id and any(s.session_id == current_session_id for s in active_sessions):
                return {
                    "allowed": True,
                    "max_sessions": max_sessions,
                    "active_sessions": current_count,
                    "message": "Existing session allowed"
                }

            # If at limit, rotate out oldest session
            if current_count >= max_sessions:
                if await self.rotate_sessions(user_id, db, current_session_id):
                    return {
                        "allowed": True,
                        "max_sessions": max_sessions,
                        "active_sessions": current_count,
                        "message": "Rotated out oldest session"
                    }
                else:
                    return {
                        "allowed": False,
                        "reason": "Failed to rotate sessions",
                        "max_sessions": max_sessions,
                        "active_sessions": current_count
                    }

            # Under limit, allow new session
            return {
                "allowed": True,
                "max_sessions": max_sessions,
                "active_sessions": current_count
            }

        except Exception as e:
            logger.error(f"Error checking session limits: {str(e)}")
            return {
                "allowed": False,
                "reason": "Error checking session limits",
                "error": str(e)
            }

    async def cleanup_stale_sessions(self, db: Session) -> int:  # Added self parameter
        """Only clean up truly expired sessions"""
        try:
            now = datetime.now(timezone.utc)

            # Only clean up sessions that have actually expired
            expired_sessions = db.query(UserSession).filter(
                UserSession.expires_at < now  # Only check expiration time
            ).all()

            cleaned = 0
            for session in expired_sessions:
                session.is_active = False
                session.ended_at = now
                session.updated_at = now
                cleaned += 1
                logger.info(
                    f"Cleaned up expired session {session.session_id} - "
                    f"expired_at={session.expires_at}"
                )

            if cleaned > 0:
                db.commit()
            return cleaned

        except Exception as e:
            logger.error(f"Error cleaning up expired sessions: {str(e)}")
            db.rollback()
            return 0


    def schedule_cleanup(self, app):
        """Schedule periodic cleanup of expired sessions"""
        @app.on_event("startup")
        async def start_cleanup():
            while True:
                try:
                    db = SessionLocal()  # Use sync session
                    try:
                        cleaned = await self.cleanup_stale_sessions(db)  # Use self
                        if cleaned > 0:
                            logger.info(f"Cleaned up {cleaned} expired sessions")
                    finally:
                        db.close()
                except Exception as e:
                    logger.error(f"Session cleanup error: {str(e)}")
                await asyncio.sleep(3600)  # Run every hour instead of 5 minutes

    async def end_session(
        self,
        request: Request,
        response: Response,
        db: Session
    ):
        """End session"""
        try:
            session_id = request.cookies.get("session_id")
            if session_id:
                session = db.query(UserSession).filter(
                    UserSession.session_id == session_id  # Changed this line
                ).first()

                if session:
                    session.is_active = False
                    session.ended_at = datetime.now(timezone.utc)
                    session.updated_at = datetime.now(timezone.utc)
                    db.commit()

            # Clear cookie
            response.delete_cookie(
                key="session_id",
                path="/",
                **self.cookie_settings
            )

            # Session data already cleared in PostgreSQL
            # No need to clear request.session - we removed SessionMiddleware

        except Exception as e:
            logger.error(f"Error ending session: {str(e)}")
            raise HTTPException(status_code=500, detail="Failed to end session")


    async def _get_session_data(self, user: User) -> Dict[str, Any]:
        """Get session data"""
        session_data = {
            "user_id": user.id,
            "email": user.email,
            "username": user.username,
            "role": user.role.value,
            "is_creator": user.is_creator,
            "is_team": user.is_team,
            "is_patreon": user.is_patreon,
            "authenticated": True,
            "created_by": user.created_by
        }

        if user.is_patreon and user.patreon_tier_data:
            session_data["tier_data"] = user.patreon_tier_data

        return session_data

    def get_session_config(self) -> Dict[str, Any]:
        """Get session configuration"""
        return {
            "session_expire": self.session_expire,
            "extended_session_expire": self.extended_session_expire,
            "max_regular_sessions": self.max_regular_sessions,
            "max_creator_sessions": self.max_creator_sessions,
            "cookie_settings": self.cookie_settings.copy()
        }

    def set_flash(self, request: Request, db: Session, message: str, message_type: str = "error") -> None:
        """
        Store a flash message in the user's session data.
        Works across all containers since stored in PostgreSQL.

        Args:
            request: FastAPI request object
            db: Database session
            message: The message to display
            message_type: Type of message (error, success, info, warning)
        """
        try:
            session_id = request.cookies.get("session_id")
            if not session_id:
                logger.warning("Cannot set flash message: No session ID in cookies")
                return

            user_session = (
                db.query(UserSession)
                .filter(
                    and_(
                        UserSession.session_id == session_id,
                        UserSession.is_active == True,
                        UserSession.expires_at > datetime.now(timezone.utc)
                    )
                )
                .first()
            )

            if not user_session:
                logger.warning(f"Cannot set flash message: Invalid session {session_id}")
                return

            # Store flash message in session_data
            if not user_session.session_data:
                user_session.session_data = {}

            user_session.session_data["flash_message"] = message
            user_session.session_data["flash_type"] = message_type
            db.commit()
            logger.debug(f"Flash message set for session {session_id}: {message}")

        except Exception as e:
            logger.error(f"Error setting flash message: {str(e)}")
            db.rollback()

    def get_flash(self, request: Request, db: Session) -> Optional[Dict[str, str]]:
        """
        Retrieve and clear a flash message from the user's session data.
        Works across all containers since stored in PostgreSQL.

        Args:
            request: FastAPI request object
            db: Database session

        Returns:
            Dict with 'message' and 'type' keys, or None if no flash message
        """
        try:
            session_id = request.cookies.get("session_id")
            if not session_id:
                return None

            user_session = (
                db.query(UserSession)
                .filter(
                    and_(
                        UserSession.session_id == session_id,
                        UserSession.is_active == True,
                        UserSession.expires_at > datetime.now(timezone.utc)
                    )
                )
                .first()
            )

            if not user_session or not user_session.session_data:
                return None

            # Get flash message
            message = user_session.session_data.get("flash_message")
            message_type = user_session.session_data.get("flash_type", "error")

            if not message:
                return None

            # Clear flash message after reading (flash messages are one-time)
            user_session.session_data.pop("flash_message", None)
            user_session.session_data.pop("flash_type", None)
            db.commit()

            logger.debug(f"Flash message retrieved for session {session_id}: {message}")
            return {"message": message, "type": message_type}

        except Exception as e:
            logger.error(f"Error getting flash message: {str(e)}")
            db.rollback()
            return None