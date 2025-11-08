# broadcast_router.py - Updated with WebSocket integration

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, Query
from sqlalchemy import and_, or_, desc, func, text
from sqlalchemy.orm import Session
from typing import Dict, Any, Optional, Set
from datetime import datetime, timezone
import json
import logging
import uuid
import asyncio
from fastapi.websockets import WebSocketDisconnect

from models import User, Broadcast
from database import get_db
from auth import login_required
from redis_state.config import redis_client
from websocket_manager import WebSocketManager

# Create a router for broadcast-related endpoints
broadcast_router = APIRouter(prefix="/api/creator")

logger = logging.getLogger(__name__)

# Global broadcast WebSocket manager instance using centralized WebSocketManager
broadcast_ws_manager = WebSocketManager(channel="broadcasts")

# WebSocket endpoint for broadcasts
@broadcast_router.websocket("/broadcast/ws")
async def broadcast_websocket(
    websocket: WebSocket,
    user_id: int = Query(..., description="User ID for authentication")
):
    """WebSocket endpoint for broadcast real-time updates"""
    from database import SessionLocal

    # Create manual session ONLY for auth/initial data
    db = SessionLocal()
    user_info = None

    try:
        # Get user by ID
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            await websocket.close(code=1008, reason="User not found")
            return

        # Store user ID as string (WebSocketManager expects string)
        user_id_str = str(user.id)
        user_info = {
            'user_id': user.id,
            'username': user.username,
            'is_creator': user.is_creator,
            'is_team': user.is_team,
            'created_by': user.created_by
        }
    finally:
        # Close db session BEFORE entering message loop
        db.close()

    # Now enter WebSocket loop WITHOUT db session
    try:
        await websocket.accept()

        # Connect to broadcast WebSocket
        await broadcast_ws_manager.connect(websocket, user_id=user_id_str)

        # Send connection confirmation
        await websocket.send_text(json.dumps({
            "type": "connected",
            "message": "Connected to broadcast live updates"
        }))

        # Send active broadcast if exists
        try:
            broadcast_data = redis_client.get("current_broadcast")
            if broadcast_data:
                data = json.loads(broadcast_data)
                broadcast_id = data.get("id")

                # Check if user has acknowledged
                user_key = f"broadcast:{broadcast_id}:ack:{user_info['user_id']}"
                acknowledged = redis_client.get(user_key) is not None

                if not acknowledged:
                    await websocket.send_text(json.dumps({
                        "type": "active_broadcast",
                        "broadcast": {
                            "id": broadcast_id,
                            "message": data.get("message"),
                            "message_type": data.get("type", "info"),
                            "created_by": data.get("created_by"),
                            "created_at": data.get("created_at")
                        }
                    }))
        except Exception as e:
            logger.error(f"Error sending active broadcast: {e}")

        # Keep connection alive and handle messages
        while True:
            try:
                # Listen for messages
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)

                if data == "ping":
                    await websocket.send_text("pong")
                elif data == "pong":
                    pass  # Heartbeat response
                else:
                    # Handle other message types
                    try:
                        message = json.loads(data)
                        message_type = message.get("type")

                        if message_type == "acknowledge_broadcast":
                            # Handle broadcast acknowledgment
                            broadcast_id = message.get("broadcast_id")
                            if broadcast_id:
                                user_key = f"broadcast:{broadcast_id}:ack:{user_info['user_id']}"
                                redis_client.set(user_key, "1")
                                logger.info(f"User {user_info['user_id']} acknowledged broadcast {broadcast_id}")

                        elif message_type == "get_active_broadcast":
                            # Resend active broadcast
                            broadcast_data = redis_client.get("current_broadcast")
                            if broadcast_data:
                                data_obj = json.loads(broadcast_data)
                                broadcast_id = data_obj.get("id")

                                user_key = f"broadcast:{broadcast_id}:ack:{user_info['user_id']}"
                                acknowledged = redis_client.get(user_key) is not None

                                if not acknowledged:
                                    await websocket.send_text(json.dumps({
                                        "type": "active_broadcast",
                                        "broadcast": {
                                            "id": broadcast_id,
                                            "message": data_obj.get("message"),
                                            "message_type": data_obj.get("type", "info"),
                                            "created_by": data_obj.get("created_by"),
                                            "created_at": data_obj.get("created_at")
                                        }
                                    }))

                    except json.JSONDecodeError:
                        pass

            except asyncio.TimeoutError:
                # Send ping to keep connection alive
                try:
                    await websocket.send_text("ping")
                except:
                    break
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error(f"WebSocket message error: {e}")
                break

    except WebSocketDisconnect:
        logger.info(f"Broadcast WebSocket client disconnected: user={user_info['username'] if user_info else 'unknown'}")
    except Exception as e:
        logger.error(f"Broadcast WebSocket error: {e}")
        try:
            await websocket.close(code=1011, reason="Internal error")
        except:
            pass

    finally:
        broadcast_ws_manager.disconnect(websocket)

# Helper function to verify creator permissions
def verify_creator(user: User):
    """Verify that the user is a creator"""
    if not user.is_creator:
        raise HTTPException(
            status_code=403,
            detail="Only creators can perform this action"
        )
    return user

@broadcast_router.post("/broadcast")
async def send_broadcast(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Create and send a broadcast message to all users via WebSocket"""
    # Verify the user is a creator
    verify_creator(current_user)
    
    try:
        data = await request.json()
        message = data.get("message", "").strip()
        message_type = data.get("type", "info")
        
        if not message:
            return {"status": "error", "message": "Broadcast message cannot be empty"}
        
        # CHARACTER LIMIT: Enforce a reasonable limit for banner display
        MAX_BROADCAST_LENGTH = 280  # Twitter-like limit
        if len(message) > MAX_BROADCAST_LENGTH:
            return {
                "status": "error", 
                "message": f"Broadcast message too long. Maximum {MAX_BROADCAST_LENGTH} characters allowed. Current: {len(message)}"
            }
        
        # Generate a unique ID for this broadcast
        broadcast_id = str(uuid.uuid4())
        
        # Store broadcast in the database
        try:
            broadcast = Broadcast(
                id=broadcast_id,
                created_by_id=current_user.id,
                message=message,
                type=message_type,
                is_active=True,
                created_at=datetime.now(timezone.utc)
            )
            
            db.add(broadcast)
            db.commit()
            logger.info(f"📢 Broadcast stored in database: {broadcast_id}")
        except Exception as e:
            logger.error(f"Error storing broadcast: {str(e)}")
            db.rollback()
            return {"status": "error", "message": f"Database error: {str(e)}"}
        
        # Store the current broadcast in Redis for new connections
        try:
            broadcast_data = {
                "id": broadcast_id,
                "message": message,
                "type": message_type,
                "created_by": current_user.id,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            redis_client.set("current_broadcast", json.dumps(broadcast_data))
            logger.info(f"📢 Broadcast stored in Redis: {broadcast_id}")
        except Exception as e:
            logger.error(f"Error storing broadcast in Redis: {str(e)}")
        
        # 🚀 WEBSOCKET BROADCASTING: Send to all connected users across ALL replicas
        try:
            websocket_message = {
                "type": "new_broadcast",
                "broadcast": {
                    "id": broadcast_id,
                    "message": message,
                    "message_type": message_type,
                    "created_by": current_user.username,
                    "created_at": datetime.now(timezone.utc).isoformat()
                }
            }

            # Send via WebSocket to ALL replicas and their connected users
            await broadcast_ws_manager.broadcast(websocket_message)

            # Get local connection count for response
            sent_count = broadcast_ws_manager.get_connection_count()

            logger.info(f"📢 Broadcast sent via WebSocket to {sent_count} local users (+ other replicas)")

        except Exception as e:
            logger.error(f"Error sending WebSocket broadcast: {str(e)}")
            sent_count = 0
        
        return {
            "status": "success", 
            "id": broadcast_id,
            "message": f"Broadcast sent successfully to {sent_count} connected users.",
            "character_count": len(message),
            "max_characters": MAX_BROADCAST_LENGTH,
            "sent_to_users": sent_count
        }
        
    except Exception as e:
        logger.error(f"Error sending broadcast: {str(e)}")
        return {"status": "error", "message": f"Failed to send broadcast: {str(e)}"}

@broadcast_router.post("/broadcast/clear")
async def clear_broadcast(
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Clear the current active broadcast via WebSocket"""
    # Verify the user is a creator
    verify_creator(current_user)
    
    try:
        # Update all active broadcasts to inactive
        db.query(Broadcast).filter(Broadcast.is_active == True).update(
            {"is_active": False, "updated_at": datetime.now(timezone.utc)}
        )
        db.commit()
        
        # Clear from Redis
        redis_client.delete("current_broadcast")

        # Send clear message via WebSocket to ALL replicas
        clear_message = {
            "type": "broadcast_cleared",
            "message": "Active broadcast has been cleared"
        }

        await broadcast_ws_manager.broadcast(clear_message)
        sent_count = broadcast_ws_manager.get_connection_count()

        logger.info(f"📢 Broadcast cleared by {current_user.username}, sent to {sent_count} local users (+ other replicas)")
        
        return {
            "status": "success",
            "message": "Broadcast cleared successfully",
            "cleared_for_users": sent_count
        }
        
    except Exception as e:
        logger.error(f"Error clearing broadcast: {str(e)}")
        return {"status": "error", "message": f"Failed to clear broadcast: {str(e)}"}

@broadcast_router.post("/broadcast/acknowledge")
async def acknowledge_broadcast(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Mark a broadcast as acknowledged by the current user"""
    try:
        data = await request.json()
        broadcast_id = data.get("broadcast_id")
        
        if not broadcast_id:
            return {"status": "error", "message": "Broadcast ID is required"}
        
        # Check if the broadcast exists
        broadcast = db.query(Broadcast).filter(Broadcast.id == broadcast_id).first()
        if not broadcast:
            return {"status": "error", "message": "Broadcast not found"}
        
        # Store acknowledgment in Redis
        user_key = f"broadcast:{broadcast_id}:ack:{current_user.id}"
        redis_client.set(user_key, "1")
        
        logger.info(f"📢 User {current_user.id} acknowledged broadcast {broadcast_id}")
        
        return {"status": "success"}
        
    except Exception as e:
        logger.error(f"Error acknowledging broadcast: {str(e)}")
        return {"status": "error", "message": f"Failed to acknowledge broadcast: {str(e)}"}

@broadcast_router.get("/broadcast/active")
async def get_active_broadcast(
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Get the current active broadcast if the user hasn't acknowledged it yet"""
    try:
        # Try to get from Redis first
        broadcast_data = redis_client.get("current_broadcast")
        
        if not broadcast_data:
            # Fallback to database
            broadcast = db.query(Broadcast).filter(Broadcast.is_active == True).order_by(desc(Broadcast.created_at)).first()
            if not broadcast:
                return {"broadcast": None}
                
            broadcast_id = broadcast.id
            message = broadcast.message
            message_type = broadcast.type
        else:
            # Parse Redis data
            data = json.loads(broadcast_data)
            broadcast_id = data.get("id")
            message = data.get("message")
            message_type = data.get("type", "info")
            
        # Check if user has acknowledged this broadcast
        user_key = f"broadcast:{broadcast_id}:ack:{current_user.id}"
        acknowledged = redis_client.get(user_key) is not None
        
        if acknowledged:
            return {"broadcast": None}
            
        return {
            "broadcast": {
                "id": broadcast_id,
                "message": message,
                "type": message_type
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting active broadcast: {str(e)}")
        return {"broadcast": None}

# Function to get broadcast character limits
@broadcast_router.get("/broadcast/limits")
async def get_broadcast_limits(current_user: User = Depends(login_required)):
    """Get broadcast character limits and current stats"""
    verify_creator(current_user)
    
    return {
        "max_characters": 280,
        "recommended_length": 120,
        "current_active_broadcasts": 1 if redis_client.exists("current_broadcast") else 0
    }

# Get connection statistics
@broadcast_router.get("/broadcast/stats")
async def get_broadcast_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(login_required)
):
    """Get broadcast statistics for creators"""
    verify_creator(current_user)

    try:
        # Count recent broadcasts
        recent_broadcasts = db.query(Broadcast).filter(
            Broadcast.created_by_id == current_user.id,
            Broadcast.created_at >= datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)
        ).count()

        # Get connected users count (LOCAL replica only)
        connected_users = broadcast_ws_manager.get_user_count()
        
        return {
            "status": "success",
            "stats": {
                "broadcasts_today": recent_broadcasts,
                "active_broadcast": redis_client.exists("current_broadcast"),
                "connected_users": connected_users,
                "connected_users_note": "Count is for this replica only",
                "max_characters": 280
            }
        }
    except Exception as e:
        logger.error(f"Error getting broadcast stats: {str(e)}")
        return {
            "status": "error", 
            "stats": {
                "broadcasts_today": 0,
                "active_broadcast": False,
                "connected_users": 0,
                "max_characters": 280
            }
        }
