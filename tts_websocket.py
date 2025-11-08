"""
WebSocket support for real-time TTS generation status updates
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from typing import Dict, Set, Optional, Any
import asyncio
import logging
import json
from datetime import datetime

from database import get_db
from models import User
from websocket_auth import get_websocket_auth, WebSocketSessionAuth

logger = logging.getLogger(__name__)

# Create router
tts_websocket_router = APIRouter(prefix="/api/tts", tags=["tts-websocket"])

class TTSWebSocketManager:
    """Manager for TTS WebSocket connections and status broadcasting"""

    def __init__(self):
        # Track connections by user ID
        self.user_connections: Dict[int, Set[WebSocket]] = {}
        # Track which tracks/voices each connection is interested in
        self.connection_subscriptions: Dict[WebSocket, Set[str]] = {}
        # WebSocket -> User mapping for cleanup
        self.connection_users: Dict[WebSocket, dict] = {}
        # Store latest status for each track:voice combination
        self.status_cache: Dict[str, dict] = {}

    async def connect(self, websocket: WebSocket, user_id: int, user_info: dict):
        """Connect a new WebSocket client"""
        await websocket.accept()

        # Add to user's connections
        if user_id not in self.user_connections:
            self.user_connections[user_id] = set()
        self.user_connections[user_id].add(websocket)

        # Initialize subscription set for this connection
        self.connection_subscriptions[websocket] = set()

        # Store user info
        self.connection_users[websocket] = user_info

        logger.info(f"User {user_info.get('username', user_id)} connected to TTS WebSocket")

        # Send connection confirmation
        await self._safe_send(websocket, {
            "type": "connected",
            "message": "Connected to TTS status updates",
            "timestamp": datetime.utcnow().isoformat()
        })

    def disconnect(self, websocket: WebSocket):
        """Disconnect a WebSocket client"""
        user_info = self.connection_users.get(websocket)
        if user_info:
            user_id = user_info['user_id']

            # Remove from user connections
            if user_id in self.user_connections:
                self.user_connections[user_id].discard(websocket)
                if not self.user_connections[user_id]:
                    del self.user_connections[user_id]

            # Clear subscriptions
            if websocket in self.connection_subscriptions:
                del self.connection_subscriptions[websocket]

            del self.connection_users[websocket]
            logger.info(f"User {user_info.get('username', user_id)} disconnected from TTS WebSocket")

    async def subscribe(self, websocket: WebSocket, track_id: str, voice_id: str):
        """Subscribe a connection to updates for a specific track/voice combination"""
        key = f"{track_id}:{voice_id}"

        if websocket in self.connection_subscriptions:
            self.connection_subscriptions[websocket].add(key)

            # Send current status if available
            if key in self.status_cache:
                await self._safe_send(websocket, {
                    "type": "status_update",
                    "track_id": track_id,
                    "voice_id": voice_id,
                    **self.status_cache[key]
                })

            await self._safe_send(websocket, {
                "type": "subscribed",
                "track_id": track_id,
                "voice_id": voice_id,
                "message": f"Subscribed to updates for {track_id}:{voice_id}"
            })

    async def unsubscribe(self, websocket: WebSocket, track_id: str, voice_id: str):
        """Unsubscribe a connection from updates for a specific track/voice combination"""
        key = f"{track_id}:{voice_id}"

        if websocket in self.connection_subscriptions:
            self.connection_subscriptions[websocket].discard(key)

            await self._safe_send(websocket, {
                "type": "unsubscribed",
                "track_id": track_id,
                "voice_id": voice_id,
                "message": f"Unsubscribed from updates for {track_id}:{voice_id}"
            })

    async def broadcast_tts_status(self, track_id: str, voice_id: str, status_data: dict):
        """Broadcast TTS status update to all subscribed connections"""
        key = f"{track_id}:{voice_id}"

        # Update cache
        self.status_cache[key] = {
            **status_data,
            "timestamp": datetime.utcnow().isoformat()
        }

        # Build message
        message = {
            "type": "tts_progress",
            "track_id": track_id,
            "voice_id": voice_id,
            **status_data
        }

        # Send to all subscribed connections
        disconnected = set()
        sent_count = 0

        for websocket, subscriptions in self.connection_subscriptions.items():
            if key in subscriptions:
                if await self._safe_send(websocket, message):
                    sent_count += 1
                else:
                    disconnected.add(websocket)

        # Clean up disconnected sockets
        for ws in disconnected:
            self.disconnect(ws)

        if sent_count > 0:
            logger.debug(f"Broadcast TTS status for {key} to {sent_count} connections")

        return sent_count

    async def broadcast_segmentation_status(self, track_id: str, voice_id: str, status_data: dict):
        """Broadcast segmentation status update to all subscribed connections"""
        key = f"{track_id}:{voice_id}"

        # Update cache with segmentation specific data
        self.status_cache[key] = {
            **status_data,
            "phase": "segmentation",
            "timestamp": datetime.utcnow().isoformat()
        }

        message = {
            "type": "segmentation_progress",
            "track_id": track_id,
            "voice_id": voice_id,
            **status_data
        }

        # Send to all subscribed connections
        disconnected = set()
        sent_count = 0

        for websocket, subscriptions in self.connection_subscriptions.items():
            if key in subscriptions:
                if await self._safe_send(websocket, message):
                    sent_count += 1
                else:
                    disconnected.add(websocket)

        # Clean up disconnected sockets
        for ws in disconnected:
            self.disconnect(ws)

        if sent_count > 0:
            logger.debug(f"Broadcast segmentation status for {key} to {sent_count} connections")

        return sent_count

    async def notify_completion(self, track_id: str, voice_id: str, success: bool = True):
        """Notify all subscribed connections that TTS generation is complete"""
        key = f"{track_id}:{voice_id}"

        # Clear from cache after completion
        if key in self.status_cache:
            del self.status_cache[key]

        message = {
            "type": "generation_complete",
            "track_id": track_id,
            "voice_id": voice_id,
            "success": success,
            "status": "completed" if success else "failed",
            "timestamp": datetime.utcnow().isoformat()
        }

        # Send to all subscribed connections
        disconnected = set()
        sent_count = 0

        for websocket, subscriptions in self.connection_subscriptions.items():
            if key in subscriptions:
                if await self._safe_send(websocket, message):
                    sent_count += 1
                    # Auto-unsubscribe on completion
                    subscriptions.discard(key)
                else:
                    disconnected.add(websocket)

        # Clean up disconnected sockets
        for ws in disconnected:
            self.disconnect(ws)

        if sent_count > 0:
            logger.info(f"Notified {sent_count} connections of completion for {key}")

        return sent_count

    async def _safe_send(self, websocket: WebSocket, data: dict) -> bool:
        """Safely send data to a WebSocket connection"""
        try:
            await websocket.send_json(data)
            return True
        except Exception as e:
            logger.warning(f"Failed to send to WebSocket: {str(e)}")
            return False

    async def handle_client_message(self, websocket: WebSocket, message: dict):
        """Handle incoming messages from WebSocket clients"""
        msg_type = message.get("type")

        if msg_type == "subscribe":
            track_id = message.get("track_id")
            voice_id = message.get("voice_id")
            if track_id and voice_id:
                await self.subscribe(websocket, track_id, voice_id)

        elif msg_type == "unsubscribe":
            track_id = message.get("track_id")
            voice_id = message.get("voice_id")
            if track_id and voice_id:
                await self.unsubscribe(websocket, track_id, voice_id)

        elif msg_type == "ping":
            await self._safe_send(websocket, {"type": "pong"})

        else:
            await self._safe_send(websocket, {
                "type": "error",
                "message": f"Unknown message type: {msg_type}"
            })

# Create global manager instance
tts_websocket_manager = TTSWebSocketManager()

@tts_websocket_router.websocket("/ws")
async def tts_status_websocket(
    websocket: WebSocket,
    websocket_auth: WebSocketSessionAuth = Depends(get_websocket_auth),
    db: Session = Depends(get_db)
):
    """WebSocket endpoint for real-time TTS status updates"""
    user = await websocket_auth.authenticate_websocket(websocket, db, require_session=True)

    if not user:
        return  # Authentication failed, connection already closed

    user_info = {
        "user_id": user.id,
        "username": user.username,
        "is_creator": user.is_creator,
        "is_team": user.is_team
    }

    try:
        # Connect the client
        await tts_websocket_manager.connect(websocket, user.id, user_info)

        # Listen for client messages
        while True:
            try:
                data = await websocket.receive_text()
                message = json.loads(data)
                await tts_websocket_manager.handle_client_message(websocket, message)

            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "message": "Invalid JSON format"
                })

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for user {user.id}")
    except Exception as e:
        logger.error(f"WebSocket error for user {user.id}: {str(e)}")
    finally:
        tts_websocket_manager.disconnect(websocket)
        websocket_auth.disconnect_websocket(websocket)

# Helper function to be called from TTS service
async def broadcast_tts_progress(track_id: str, voice_id: str, progress: int,
                                phase: str, message: str, chunks_completed: int = 0,
                                total_chunks: int = 0, **kwargs):
    """Helper function to broadcast TTS progress from the TTS service"""
    status_data = {
        "progress": progress,
        "phase": phase,
        "message": message,
        "chunks_completed": chunks_completed,
        "total_chunks": total_chunks,
        "status": "processing",
        **kwargs
    }

    await tts_websocket_manager.broadcast_tts_status(track_id, voice_id, status_data)

async def broadcast_segmentation_progress(track_id: str, voice_id: str, progress: int,
                                         segments_completed: int, total_segments: int,
                                         message: str = "Segmenting audio...", **kwargs):
    """Helper function to broadcast segmentation progress"""
    status_data = {
        "progress": progress,
        "segments_completed": segments_completed,
        "total_segments": total_segments,
        "message": message,
        **kwargs
    }

    await tts_websocket_manager.broadcast_segmentation_status(track_id, voice_id, status_data)

async def notify_tts_complete(track_id: str, voice_id: str, success: bool = True):
    """Helper function to notify completion"""
    await tts_websocket_manager.notify_completion(track_id, voice_id, success)