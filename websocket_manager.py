"""
Centralized WebSocket Manager with Redis Pub/Sub
Solves multi-replica WebSocket broadcasting issue by using Redis as message broker.

Usage:
    # In your route file:
    from websocket_manager import WebSocketManager

    # Create manager for your feature
    broadcast_ws = WebSocketManager(channel="broadcasts")

    # WebSocket endpoint
    @app.websocket("/ws/broadcasts")
    async def websocket_endpoint(websocket: WebSocket):
        await broadcast_ws.connect(websocket, user_id="123")
        try:
            while True:
                await websocket.receive_text()  # Keep connection alive
        except WebSocketDisconnect:
            broadcast_ws.disconnect(websocket)

    # Broadcast to all connected users
    @app.post("/api/broadcast")
    async def send_broadcast(message: dict):
        await broadcast_ws.broadcast(message)
"""

import asyncio
import json
import logging
from typing import Dict, Set, Optional, Any, Callable
from fastapi import WebSocket, WebSocketDisconnect
from redis.asyncio import Redis
from redis_state.config import REDIS_URL, FALLBACK_REDIS_URL

logger = logging.getLogger(__name__)


class WebSocketManager:
    """
    Centralized WebSocket manager with Redis pub/sub for multi-replica support.

    Each WebSocketManager instance handles one type of WebSocket connection (e.g., broadcasts, comments).
    Multiple replicas can use the same channel to broadcast messages across all connected clients.
    """

    def __init__(self, channel: str, redis_url: str = None):
        """
        Initialize WebSocket manager.

        Args:
            channel: Redis pub/sub channel name (e.g., "broadcasts", "comments", "forum")
            redis_url: Override default Redis URL (optional)
        """
        self.channel = channel
        self.redis_url = redis_url or REDIS_URL

        # Local connections (this replica only)
        self.active_connections: Dict[str, Set[WebSocket]] = {}  # user_id -> {websockets}
        self.websocket_to_user: Dict[WebSocket, str] = {}  # websocket -> user_id

        # Redis pub/sub
        self._redis_client: Optional[Redis] = None
        self._pubsub = None
        self._listener_task: Optional[asyncio.Task] = None

        # Message filtering (optional)
        self._message_filter: Optional[Callable] = None

        # Lazy initialization flag
        self._initialized = False

        # ✅ FIX: Don't initialize Redis eagerly - wait for first use
        # This prevents 5+ Redis connections/listeners at module import time

    async def _init_redis(self):
        """Initialize Redis connection and start listener"""
        try:
            self._redis_client = Redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=5.0,
                socket_connect_timeout=5.0
            )
            await self._redis_client.ping()
            logger.info(f"✅ WebSocketManager [{self.channel}] connected to Redis")

            # Start pub/sub listener
            await self._start_listener()

        except Exception as e:
            logger.error(f"❌ WebSocketManager [{self.channel}] Redis connection failed: {e}")
            logger.warning(f"⚠️  WebSocketManager [{self.channel}] will operate in single-replica mode")

    async def _start_listener(self):
        """Start listening to Redis pub/sub channel"""
        try:
            self._pubsub = self._redis_client.pubsub()
            await self._pubsub.subscribe(self.channel)
            logger.info(f"✅ WebSocketManager [{self.channel}] subscribed to Redis channel")

            # Start listener task
            self._listener_task = asyncio.create_task(self._listen_redis())

        except Exception as e:
            logger.error(f"❌ WebSocketManager [{self.channel}] failed to subscribe: {e}")

    async def _listen_redis(self):
        """Listen to Redis pub/sub and forward messages to local WebSocket clients"""
        try:
            logger.info(f"🎧 WebSocketManager [{self.channel}] listener started")

            while True:
                try:
                    # ✅ FIX: Use get_message() with timeout instead of async for loop
                    # This prevents tight CPU loops
                    message = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)

                    if message is None:
                        # No message, yield control to event loop
                        await asyncio.sleep(0.01)  # 10ms sleep prevents CPU spin
                        continue

                    if message['type'] == 'message':
                        try:
                            # Parse message
                            data = json.loads(message['data'])

                            # Apply filter if set
                            if self._message_filter and not self._message_filter(data):
                                continue

                            # Broadcast to local clients
                            await self._broadcast_local(data)

                        except json.JSONDecodeError as e:
                            logger.error(f"❌ Invalid JSON in Redis message: {e}")
                        except Exception as e:
                            logger.error(f"❌ Error processing Redis message: {e}")

                    # Yield control after each message
                    await asyncio.sleep(0)

                except asyncio.TimeoutError:
                    # Timeout is normal, just continue
                    await asyncio.sleep(0.01)
                    continue

        except asyncio.CancelledError:
            logger.info(f"🛑 WebSocketManager [{self.channel}] listener cancelled")
            raise
        except Exception as e:
            logger.error(f"❌ WebSocketManager [{self.channel}] listener error: {e}")
            # Try to restart listener after delay
            await asyncio.sleep(5)
            await self._start_listener()

    async def connect(self, websocket: WebSocket, user_id: str = None, **metadata):
        """
        Register WebSocket connection (assumes already accepted by caller).

        Args:
            websocket: FastAPI WebSocket instance
            user_id: User identifier (optional, defaults to connection ID)
            **metadata: Additional metadata to store with connection
        """
        # ✅ FIX: Lazy initialize Redis on first connection
        if not self._initialized:
            self._initialized = True
            asyncio.create_task(self._init_redis())

        # NOTE: WebSocket should already be accepted by the endpoint handler
        # Removed: await websocket.accept() to prevent double-accept error

        # Use websocket ID if user_id not provided
        if not user_id:
            user_id = str(id(websocket))

        # Register connection
        if user_id not in self.active_connections:
            self.active_connections[user_id] = set()

        self.active_connections[user_id].add(websocket)
        self.websocket_to_user[websocket] = user_id

        logger.info(f"✅ WebSocket connected [{self.channel}]: user={user_id}, total={self.get_connection_count()}")

    def disconnect(self, websocket: WebSocket):
        """
        Disconnect WebSocket and clean up.

        Args:
            websocket: WebSocket to disconnect
        """
        user_id = self.websocket_to_user.get(websocket)

        if user_id:
            # Remove from user's connections
            if user_id in self.active_connections:
                self.active_connections[user_id].discard(websocket)

                # Remove user entry if no more connections
                if not self.active_connections[user_id]:
                    del self.active_connections[user_id]

            # Remove from websocket map
            del self.websocket_to_user[websocket]

            logger.info(f"❌ WebSocket disconnected [{self.channel}]: user={user_id}, total={self.get_connection_count()}")

    async def broadcast(self, message: dict, target_user_ids: Set[str] = None):
        """
        Broadcast message to all connected clients across all replicas.

        Args:
            message: Dictionary to broadcast (will be JSON serialized)
            target_user_ids: If provided, only send to these users (optional)
        """
        # ✅ FIX: Lazy initialize Redis on first broadcast
        if not self._initialized:
            self._initialized = True
            asyncio.create_task(self._init_redis())
            # Give Redis a moment to initialize
            await asyncio.sleep(0.1)

        # Add targeting metadata if specified
        if target_user_ids:
            message['_target_users'] = list(target_user_ids)

        # Publish to Redis (will be received by all replicas including this one)
        if self._redis_client:
            try:
                await self._redis_client.publish(
                    self.channel,
                    json.dumps(message)
                )
                logger.debug(f"📤 Broadcast sent to Redis [{self.channel}]")
            except Exception as e:
                logger.error(f"❌ Failed to publish to Redis [{self.channel}]: {e}")
                # Fallback: broadcast locally only
                await self._broadcast_local(message)
        else:
            # No Redis: broadcast locally only (single-replica mode)
            await self._broadcast_local(message)

    async def _broadcast_local(self, message: dict):
        """
        Broadcast message to local WebSocket connections only.

        Args:
            message: Dictionary to send
        """
        # Check if message is targeted to specific users
        target_users = message.get('_target_users')
        if target_users:
            # Remove metadata from message
            message = {k: v for k, v in message.items() if not k.startswith('_')}
            # Send only to targeted users
            users_to_send = set(target_users) & set(self.active_connections.keys())
        else:
            # Remove metadata from message
            message = {k: v for k, v in message.items() if not k.startswith('_')}
            # Send to all users
            users_to_send = self.active_connections.keys()

        # Serialize once
        message_json = json.dumps(message)

        # Send to all connections
        disconnected = []
        sent_count = 0

        for user_id in users_to_send:
            for websocket in list(self.active_connections.get(user_id, [])):
                try:
                    await websocket.send_text(message_json)
                    sent_count += 1
                except Exception as e:
                    logger.warning(f"⚠️  Failed to send to websocket: {e}")
                    disconnected.append(websocket)

        # Clean up disconnected websockets
        for ws in disconnected:
            self.disconnect(ws)

        if sent_count > 0:
            logger.debug(f"✉️  Sent to {sent_count} local connections [{self.channel}]")

    async def send_to_user(self, user_id: str, message: dict):
        """
        Send message to specific user across all replicas.

        Args:
            user_id: Target user ID
            message: Message to send
        """
        await self.broadcast(message, target_user_ids={user_id})

    def get_connection_count(self) -> int:
        """Get number of active WebSocket connections on this replica"""
        return sum(len(connections) for connections in self.active_connections.values())

    def get_user_count(self) -> int:
        """Get number of unique users connected on this replica"""
        return len(self.active_connections)

    def is_user_connected(self, user_id: str) -> bool:
        """Check if user has any active connections on this replica"""
        return user_id in self.active_connections and len(self.active_connections[user_id]) > 0

    def set_message_filter(self, filter_func: Callable[[dict], bool]):
        """
        Set a filter function for incoming messages.

        Args:
            filter_func: Function that takes message dict and returns True to allow, False to block
        """
        self._message_filter = filter_func

    async def close(self):
        """Clean up resources"""
        # Cancel listener task
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass

        # Unsubscribe from Redis
        if self._pubsub:
            try:
                await self._pubsub.unsubscribe(self.channel)
            except Exception as e:
                logger.error(f"Error unsubscribing: {e}")

        # Close Redis connection
        if self._redis_client:
            try:
                await self._redis_client.close()
            except Exception as e:
                logger.error(f"Error closing Redis: {e}")

        logger.info(f"🛑 WebSocketManager [{self.channel}] closed")
