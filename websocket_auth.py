# websocket_auth.py - Complete version with init function
from fastapi import WebSocket, HTTPException, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any
import logging
from urllib.parse import parse_qs
from models import User
from database import get_db
from session_manager import SessionManager

logger = logging.getLogger(__name__)

class WebSocketSessionAuth:
    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager
        self.active_connections: Dict[str, set] = {}  # session_id -> websockets
        self.connection_sessions: Dict[WebSocket, str] = {}  # websocket -> session_id
        
    async def authenticate_websocket(
        self, 
        websocket: WebSocket, 
        db: Session,
        require_session: bool = True
    ) -> Optional[User]:
        """
        Authenticate WebSocket connection using session cookie
        Returns User if authenticated, None otherwise
        """
        try:
            # Extract cookies from WebSocket headers
            cookie_header = None
            for name, value in websocket.headers.items():
                if name.lower() == 'cookie':
                    cookie_header = value
                    break
            
            if not cookie_header:
                logger.warning("No cookie header found in WebSocket request")
                if require_session:
                    await websocket.close(code=1008, reason="No authentication")
                    return None
                return None

            # Parse cookies
            cookies = self._parse_cookie_header(cookie_header)
            session_id = cookies.get('session_id')
            
            if not session_id:
                logger.warning("No session_id found in WebSocket cookies")
                if require_session:
                    await websocket.close(code=1008, reason="No session ID")
                    return None
                return None

            # Validate session using your existing session manager
            user = await self._verify_websocket_session(session_id, db)
            
            if not user:
                logger.warning(f"Invalid session {session_id} for WebSocket")
                if require_session:
                    await websocket.close(code=1008, reason="Invalid session")
                    return None
                return None

            # Track this connection
            if session_id not in self.active_connections:
                self.active_connections[session_id] = set()
            self.active_connections[session_id].add(websocket)
            self.connection_sessions[websocket] = session_id
            
            logger.info(f"WebSocket authenticated for user {user.id} (session: {session_id})")
            return user

        except Exception as e:
            logger.error(f"WebSocket authentication error: {str(e)}")
            if require_session:
                try:
                    await websocket.close(code=1011, reason="Authentication error")
                except:
                    pass
            return None

    async def _verify_websocket_session(self, session_id: str, db: Session) -> Optional[User]:
        """Verify session using session manager logic"""
        try:
            from models import UserSession
            from datetime import datetime, timezone
            from sqlalchemy import and_
            
            # Query session (similar to session_manager.verify_session)
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
                return None

            # Get user
            user = (
                db.query(User)
                .filter(User.id == user_session.user_id)
                .first()
            )

            if not user or not user.is_active:
                return None

            # Update last_active (optional for WebSocket)
            user_session.last_active = datetime.now(timezone.utc)
            db.commit()

            return user

        except Exception as e:
            logger.error(f"Session verification error: {str(e)}")
            db.rollback()
            return None

    def _parse_cookie_header(self, cookie_header: str) -> Dict[str, str]:
        """Parse cookie header string into dict"""
        cookies = {}
        try:
            for cookie in cookie_header.split(';'):
                cookie = cookie.strip()
                if '=' in cookie:
                    name, value = cookie.split('=', 1)
                    cookies[name.strip()] = value.strip()
        except Exception as e:
            logger.error(f"Error parsing cookies: {str(e)}")
        return cookies

    def disconnect_websocket(self, websocket: WebSocket):
        """Clean up WebSocket connection tracking"""
        try:
            session_id = self.connection_sessions.get(websocket)
            if session_id:
                if session_id in self.active_connections:
                    self.active_connections[session_id].discard(websocket)
                    if not self.active_connections[session_id]:
                        del self.active_connections[session_id]
                del self.connection_sessions[websocket]
                logger.info(f"Cleaned up WebSocket for session {session_id}")
        except Exception as e:
            logger.error(f"Error cleaning up WebSocket: {str(e)}")

    def get_session_connections(self, session_id: str) -> set:
        """Get all WebSocket connections for a session"""
        return self.active_connections.get(session_id, set())

    async def broadcast_to_session(self, session_id: str, data: dict) -> int:
        """Broadcast message to all connections for a session"""
        connections = self.get_session_connections(session_id)
        sent_count = 0
        disconnected = set()
        
        for ws in connections.copy():
            try:
                await ws.send_json(data)
                sent_count += 1
            except Exception as e:
                logger.warning(f"Failed to send to WebSocket: {str(e)}")
                disconnected.add(ws)
        
        # Clean up disconnected sockets
        for ws in disconnected:
            self.disconnect_websocket(ws)
        
        return sent_count

# Global instance - will be initialized in app.py
websocket_auth: Optional[WebSocketSessionAuth] = None

def get_websocket_auth() -> WebSocketSessionAuth:
    """Dependency to get WebSocket auth instance"""
    if websocket_auth is None:
        raise HTTPException(status_code=500, detail="WebSocket auth not initialized")
    return websocket_auth

def init_websocket_auth(session_manager: SessionManager) -> WebSocketSessionAuth:
    """Initialize the global WebSocket auth instance"""
    global websocket_auth
    websocket_auth = WebSocketSessionAuth(session_manager)
    logger.info("WebSocket authentication initialized successfully")
    return websocket_auth