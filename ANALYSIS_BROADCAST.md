# Broadcast WebSocket System - Detailed Analysis & Migration Guide

**File:** `/home/tundragoon/projects/audio-streaming-appT/broadcast_router.py`
**Lines:** 534 total
**Current Status:** Uses custom `BroadcastWebSocketManager` class (needs migration to centralized `WebSocketManager`)

---

## Table of Contents

1. [Current Implementation Overview](#current-implementation-overview)
2. [WebSocket Manager Class Analysis](#websocket-manager-class-analysis)
3. [WebSocket Endpoint Analysis](#websocket-endpoint-analysis)
4. [API Endpoints That Trigger Broadcasts](#api-endpoints-that-trigger-broadcasts)
5. [Message Format Documentation](#message-format-documentation)
6. [Broadcasting Logic](#broadcasting-logic)
7. [Filtering and Targeting Logic](#filtering-and-targeting-logic)
8. [Migration Checklist](#migration-checklist)
9. [Code Changes Required](#code-changes-required)
10. [Testing Steps](#testing-steps)

---

## 1. Current Implementation Overview

### Architecture
The broadcast system currently uses a **custom WebSocket manager** that stores connections in-memory. This creates issues in multi-replica deployments where each container only knows about its own WebSocket connections.

### Key Components
- **WebSocket Manager:** `BroadcastWebSocketManager` (lines 24-161)
- **WebSocket Endpoint:** `/api/creator/broadcast/ws` (lines 167-241)
- **Message Handler:** `handle_broadcast_websocket_message()` (lines 243-261)
- **Global Instance:** `broadcast_ws_manager` (line 164)

### Current Limitations
- ❌ **Single-replica only** - doesn't work across load-balanced containers
- ❌ **No Redis pub/sub** - broadcasts only reach local connections
- ❌ **Inconsistent delivery** - users connected to different replicas won't receive messages

---

## 2. WebSocket Manager Class Analysis

### Class: `BroadcastWebSocketManager` (Lines 24-161)

#### Data Structures (Lines 27-33)

```python
# Line 29: User ID -> Set of WebSocket connections
self.user_connections: Dict[int, Set[WebSocket]] = {}

# Line 31: WebSocket -> User info for cleanup
self.connection_users: Dict[WebSocket, dict] = {}

# Line 33: Admin connections (creators and team members)
self.admin_connections: Dict[int, Set[WebSocket]] = {}
```

**Purpose:**
- `user_connections`: Maps user IDs to their WebSocket connections (supports multiple connections per user)
- `connection_users`: Reverse mapping for cleanup when WebSocket disconnects
- `admin_connections`: Special tracking for creator/team member connections

#### Connection Management

##### `connect()` Method (Lines 35-64)

```python
async def connect(self, websocket: WebSocket, user_id: int, user_info: dict):
    """Connect a user to broadcast WebSocket"""
    await websocket.accept()

    # Add to user's connections (line 40-42)
    if user_id not in self.user_connections:
        self.user_connections[user_id] = set()
    self.user_connections[user_id].add(websocket)

    # Store user info (line 45)
    self.connection_users[websocket] = user_info

    # If admin, add to admin connections (lines 48-53)
    if user_info.get('is_creator') or user_info.get('is_team'):
        creator_id = user_id if user_info.get('is_creator') else user_info.get('created_by')
        if creator_id:
            if creator_id not in self.admin_connections:
                self.admin_connections[creator_id] = set()
            self.admin_connections[creator_id].add(websocket)

    # Send connection confirmation (lines 58-61)
    await websocket.send_json({
        "type": "connected",
        "message": "Connected to broadcast live updates"
    })

    # Send any active broadcast immediately (line 64)
    await self.send_active_broadcast_to_user(websocket, user_info)
```

**Key Features:**
- Accepts WebSocket connection
- Tracks both regular users and admin users separately
- Sends confirmation message
- Immediately sends active broadcast if one exists

##### `disconnect()` Method (Lines 66-87)

```python
def disconnect(self, websocket: WebSocket):
    """Disconnect user from WebSocket"""
    user_info = self.connection_users.get(websocket)
    if user_info:
        user_id = user_info['user_id']

        # Remove from user connections (lines 73-76)
        if user_id in self.user_connections:
            self.user_connections[user_id].discard(websocket)
            if not self.user_connections[user_id]:
                del self.user_connections[user_id]

        # Remove from admin connections if applicable (lines 79-84)
        if user_info.get('is_creator') or user_info.get('is_team'):
            creator_id = user_id if user_info.get('is_creator') else user_info.get('created_by')
            if creator_id and creator_id in self.admin_connections:
                self.admin_connections[creator_id].discard(websocket)
                if not self.admin_connections[creator_id]:
                    del self.admin_connections[creator_id]

        del self.connection_users[websocket]
        logger.info(f"User {user_info['username']} disconnected from broadcast WebSocket")
```

**Key Features:**
- Cleans up both user and admin connection tracking
- Removes empty sets to prevent memory leaks
- Logs disconnection

#### Broadcasting Methods

##### `send_active_broadcast_to_user()` (Lines 89-114)

```python
async def send_active_broadcast_to_user(self, websocket: WebSocket, user_info: dict):
    """Send active broadcast to a specific user"""
    try:
        # Get active broadcast from Redis (line 93)
        broadcast_data = redis_client.get("current_broadcast")
        if broadcast_data:
            data = json.loads(broadcast_data)
            broadcast_id = data.get("id")

            # Check if user has acknowledged this broadcast (lines 98-100)
            user_key = f"broadcast:{broadcast_id}:ack:{user_info['user_id']}"
            acknowledged = redis_client.get(user_key) is not None

            if not acknowledged:
                await websocket.send_json({
                    "type": "active_broadcast",
                    "broadcast": {
                        "id": broadcast_id,
                        "message": data.get("message"),
                        "message_type": data.get("type", "info"),
                        "created_by": data.get("created_by"),
                        "created_at": data.get("created_at")
                    }
                })
    except Exception as e:
        logger.error(f"Error sending active broadcast to user: {e}")
```

**Key Features:**
- Retrieves active broadcast from Redis
- Checks acknowledgment status
- Only sends if not already acknowledged
- Handles errors gracefully

##### `broadcast_to_all_users()` (Lines 116-139)

```python
async def broadcast_to_all_users(self, message: dict, exclude_user_id: Optional[int] = None):
    """Broadcast message to all connected users"""
    sent_count = 0
    disconnected = set()

    # Send to all user connections (lines 122-132)
    for user_id, connections in self.user_connections.items():
        if exclude_user_id and user_id == exclude_user_id:
            continue

        for websocket in connections.copy():
            try:
                await websocket.send_json(message)
                sent_count += 1
            except Exception as e:
                logger.error(f"Error sending broadcast to user {user_id}: {e}")
                disconnected.add(websocket)

    # Clean up disconnected sockets (lines 135-136)
    for websocket in disconnected:
        self.disconnect(websocket)

    logger.info(f"Broadcast sent to {sent_count} users")
    return sent_count
```

**Key Features:**
- Iterates through all connected users
- Optional exclusion of specific user ID
- Tracks disconnected sockets and cleans them up
- Returns count of successful sends
- **LIMITATION:** Only sends to this replica's connections ❌

##### `send_to_admins()` (Lines 141-161)

```python
async def send_to_admins(self, creator_id: int, message: dict):
    """Send message to all admins of a creator"""
    if creator_id not in self.admin_connections:
        return False

    disconnected = set()
    sent = False

    for websocket in self.admin_connections[creator_id].copy():
        try:
            await websocket.send_json(message)
            sent = True
        except Exception as e:
            logger.error(f"Error sending to admin: {e}")
            disconnected.add(websocket)

    # Clean up disconnected
    for websocket in disconnected:
        self.disconnect(websocket)

    return sent
```

**Key Features:**
- Sends to admin connections only
- Returns boolean indicating success
- Cleans up disconnected sockets

---

## 3. WebSocket Endpoint Analysis

### Endpoint: `/api/creator/broadcast/ws` (Lines 167-241)

```python
@broadcast_router.websocket("/broadcast/ws")
async def broadcast_websocket(
    websocket: WebSocket,
    user_id: int = Query(..., description="User ID for authentication")
):
    """WebSocket endpoint for broadcast real-time updates"""
```

#### Authentication Flow (Lines 173-196)

```python
from database import SessionLocal

# Create manual session ONLY for auth/initial data
db = SessionLocal()
user_info = None

try:
    # Get user by ID (line 181)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        await websocket.close(code=1008, reason="User not found")
        return

    # Prepare user info (lines 187-193)
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
```

**Key Points:**
- Uses query parameter for authentication (not secure, should use cookies/tokens)
- Creates temporary database session
- Closes DB session before entering message loop (prevents session leaks)
- Stores user info as dict

#### Message Loop (Lines 199-241)

```python
try:
    # Connect to broadcast WebSocket (line 201)
    await broadcast_ws_manager.connect(websocket, user_info['user_id'], user_info)

    # Keep connection alive and handle messages (lines 204-229)
    while True:
        try:
            # Listen for messages with timeout (line 207)
            data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)

            if data == "ping":
                await websocket.send_text("pong")
            else:
                # Handle other message types (lines 213-217)
                try:
                    message = json.loads(data)
                    await handle_broadcast_websocket_message(websocket, user_info, message)
                except json.JSONDecodeError:
                    pass

        except asyncio.TimeoutError:
            # Send ping to keep connection alive (lines 220-224)
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
    logger.info(f"Broadcast WebSocket client disconnected: user={user_info['username']}")
except Exception as e:
    logger.error(f"Broadcast WebSocket error: {e}")
    try:
        await websocket.close(code=1011, reason="Internal error")
    except:
        pass

finally:
    broadcast_ws_manager.disconnect(websocket)
```

**Key Features:**
- 30-second timeout for keeping connection alive
- Ping/pong heartbeat mechanism
- Handles both text and JSON messages
- Graceful disconnect in finally block
- Comprehensive error handling

---

## 4. API Endpoints That Trigger Broadcasts

### Endpoint 1: POST `/api/creator/broadcast` (Lines 273-371)

**Purpose:** Create and send a new broadcast message

#### Request Processing (Lines 283-297)

```python
data = await request.json()
message = data.get("message", "").strip()
message_type = data.get("type", "info")

if not message:
    return {"status": "error", "message": "Broadcast message cannot be empty"}

# CHARACTER LIMIT: Enforce a reasonable limit for banner display (line 292)
MAX_BROADCAST_LENGTH = 280  # Twitter-like limit
if len(message) > MAX_BROADCAST_LENGTH:
    return {
        "status": "error",
        "message": f"Broadcast message too long. Maximum {MAX_BROADCAST_LENGTH} characters..."
    }

# Generate a unique ID for this broadcast (line 300)
broadcast_id = str(uuid.uuid4())
```

#### Database Storage (Lines 303-319)

```python
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
```

#### Redis Storage (Lines 322-333)

```python
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
```

**Redis Key:** `current_broadcast`
**Purpose:** Store active broadcast for new connections

#### WebSocket Broadcasting (Lines 336-358)

```python
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

    # Send via WebSocket to all connected users (including the creator)
    sent_count = await broadcast_ws_manager.broadcast_to_all_users(
        websocket_message
        # Removed exclude_user_id parameter so creator also receives the broadcast
    )

    logger.info(f"📢 Broadcast sent via WebSocket to {sent_count} users (including creator)")

except Exception as e:
    logger.error(f"Error sending WebSocket broadcast: {str(e)}")
    sent_count = 0
```

**Key Point:** Creator receives their own broadcast (note on line 351)

#### Response (Lines 360-367)

```python
return {
    "status": "success",
    "id": broadcast_id,
    "message": f"Broadcast sent successfully to {sent_count} connected users.",
    "character_count": len(message),
    "max_characters": MAX_BROADCAST_LENGTH,
    "sent_to_users": sent_count
}
```

### Endpoint 2: POST `/api/creator/broadcast/clear` (Lines 373-410)

**Purpose:** Clear the current active broadcast

#### Database Update (Lines 383-387)

```python
# Update all active broadcasts to inactive
db.query(Broadcast).filter(Broadcast.is_active == True).update(
    {"is_active": False, "updated_at": datetime.now(timezone.utc)}
)
db.commit()
```

#### Redis Cleanup (Line 390)

```python
# Clear from Redis
redis_client.delete("current_broadcast")
```

#### WebSocket Notification (Lines 393-398)

```python
# Send clear message via WebSocket
clear_message = {
    "type": "broadcast_cleared",
    "message": "Active broadcast has been cleared"
}

sent_count = await broadcast_ws_manager.broadcast_to_all_users(clear_message)
```

#### Response (Lines 400-406)

```python
return {
    "status": "success",
    "message": "Broadcast cleared successfully",
    "cleared_for_users": sent_count
}
```

### Endpoint 3: POST `/api/creator/broadcast/acknowledge` (Lines 412-441)

**Purpose:** Mark a broadcast as acknowledged by the current user

#### Request Processing (Lines 419-429)

```python
data = await request.json()
broadcast_id = data.get("broadcast_id")

if not broadcast_id:
    return {"status": "error", "message": "Broadcast ID is required"}

# Check if the broadcast exists
broadcast = db.query(Broadcast).filter(Broadcast.id == broadcast_id).first()
if not broadcast:
    return {"status": "error", "message": "Broadcast not found"}
```

#### Redis Acknowledgment (Lines 432-433)

```python
# Store acknowledgment in Redis
user_key = f"broadcast:{broadcast_id}:ack:{current_user.id}"
redis_client.set(user_key, "1")
```

**Redis Key Pattern:** `broadcast:{broadcast_id}:ack:{user_id}`
**Purpose:** Track which users have acknowledged each broadcast

### Endpoint 4: GET `/api/creator/broadcast/active` (Lines 443-486)

**Purpose:** Get the current active broadcast if user hasn't acknowledged it

#### Redis Retrieval (Lines 450-467)

```python
# Try to get from Redis first
broadcast_data = redis_client.get("current_broadcast")

if not broadcast_data:
    # Fallback to database
    broadcast = db.query(Broadcast).filter(
        Broadcast.is_active == True
    ).order_by(desc(Broadcast.created_at)).first()

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
```

#### Acknowledgment Check (Lines 470-474)

```python
# Check if user has acknowledged this broadcast
user_key = f"broadcast:{broadcast_id}:ack:{current_user.id}"
acknowledged = redis_client.get(user_key) is not None

if acknowledged:
    return {"broadcast": None}
```

#### Response (Lines 476-482)

```python
return {
    "broadcast": {
        "id": broadcast_id,
        "message": message,
        "type": message_type
    }
}
```

### Endpoint 5: GET `/api/creator/broadcast/limits` (Lines 489-498)

**Purpose:** Get broadcast character limits and current stats

```python
return {
    "max_characters": 280,
    "recommended_length": 120,
    "current_active_broadcasts": 1 if redis_client.exists("current_broadcast") else 0
}
```

### Endpoint 6: GET `/api/creator/broadcast/stats` (Lines 501-535)

**Purpose:** Get broadcast statistics for creators

**NOTE:** Has a bug - `db` variable is not defined (line 508)

```python
try:
    # Count recent broadcasts (lines 508-511)
    recent_broadcasts = db.query(Broadcast).filter(
        Broadcast.created_by_id == current_user.id,
        Broadcast.created_at >= datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)
    ).count()

    # Get connected users count (line 514)
    connected_users = len(broadcast_ws_manager.user_connections)

    return {
        "status": "success",
        "stats": {
            "broadcasts_today": recent_broadcasts,
            "active_broadcast": redis_client.exists("current_broadcast"),
            "connected_users": connected_users,
            "max_characters": 280
        }
    }
except Exception as e:
    # Returns default stats on error
    ...
```

**BUG:** Missing `db: Session = Depends(get_db)` parameter

---

## 5. Message Format Documentation

### Server → Client Messages

#### 1. Connection Confirmation (Line 58-61)

```json
{
  "type": "connected",
  "message": "Connected to broadcast live updates"
}
```

**Sent when:** User first connects to WebSocket

#### 2. New Broadcast (Lines 337-346)

```json
{
  "type": "new_broadcast",
  "broadcast": {
    "id": "uuid-string",
    "message": "The broadcast message content",
    "message_type": "info|warning|alert",
    "created_by": "username",
    "created_at": "2025-11-05T12:34:56.789Z"
  }
}
```

**Sent when:** Creator sends a new broadcast (POST `/api/creator/broadcast`)

#### 3. Active Broadcast (Lines 103-112)

```json
{
  "type": "active_broadcast",
  "broadcast": {
    "id": "uuid-string",
    "message": "The broadcast message content",
    "message_type": "info|warning|alert",
    "created_by": "user_id",
    "created_at": "2025-11-05T12:34:56.789Z"
  }
}
```

**Sent when:**
- User first connects (if active broadcast exists)
- User requests active broadcast via WebSocket message

#### 4. Broadcast Cleared (Lines 393-396)

```json
{
  "type": "broadcast_cleared",
  "message": "Active broadcast has been cleared"
}
```

**Sent when:** Creator clears the broadcast (POST `/api/creator/broadcast/clear`)

#### 5. Heartbeat (Lines 210, 222)

```
"ping"
```

**Sent when:** Server sends ping every 30 seconds to keep connection alive

### Client → Server Messages

#### 1. Heartbeat Response (Line 209-210)

```
"ping"
```

**Purpose:** Client can send ping to server, server responds with "pong"

#### 2. Acknowledge Broadcast (Lines 251-257)

```json
{
  "type": "acknowledge_broadcast",
  "broadcast_id": "uuid-string"
}
```

**Purpose:** User dismisses/acknowledges a broadcast

**Effect:** Sets Redis key `broadcast:{broadcast_id}:ack:{user_id}` to "1"

#### 3. Get Active Broadcast (Lines 259-261)

```json
{
  "type": "get_active_broadcast"
}
```

**Purpose:** Request current active broadcast

**Response:** Sends `active_broadcast` message if one exists and user hasn't acknowledged it

---

## 6. Broadcasting Logic

### Broadcast Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                     Creator sends broadcast                      │
│              POST /api/creator/broadcast                         │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
                    ┌────────────────────┐
                    │  Generate UUID ID  │
                    └────────┬───────────┘
                             │
                ┌────────────┼────────────┐
                ▼            ▼            ▼
         ┌──────────┐  ┌─────────┐  ┌──────────────────────┐
         │PostgreSQL│  │  Redis  │  │  WebSocket Manager   │
         │Broadcast │  │current_ │  │broadcast_to_all_users│
         │  Table   │  │broadcast│  └──────────┬───────────┘
         └──────────┘  └─────────┘             │
                                                ▼
                                    ┌───────────────────────┐
                                    │ Iterate user_connections│
                                    └──────────┬────────────┘
                                               │
                          ┌────────────────────┼────────────────────┐
                          ▼                    ▼                    ▼
                    ┌──────────┐         ┌──────────┐        ┌──────────┐
                    │User 1 WS │         │User 2 WS │        │User N WS │
                    │(This     │         │(This     │        │(This     │
                    │ Replica) │         │ Replica) │        │ Replica) │
                    └──────────┘         └──────────┘        └──────────┘

                    ❌ Users on other replicas DON'T receive message
```

### Key Issues with Current Implementation

1. **No Cross-Replica Broadcasting**
   - `broadcast_to_all_users()` only sends to `self.user_connections`
   - Users connected to other replicas won't receive the broadcast
   - No Redis pub/sub integration

2. **Acknowledgment System**
   - Uses Redis for acknowledgments: `broadcast:{id}:ack:{user_id}`
   - Works across replicas ✅
   - But broadcast delivery doesn't work across replicas ❌

3. **Active Broadcast Storage**
   - Stored in Redis key `current_broadcast` ✅
   - New connections can retrieve it ✅
   - But real-time delivery fails for other replicas ❌

---

## 7. Filtering and Targeting Logic

### Current Filtering Capabilities

#### 1. Exclude User (Lines 123-124)

```python
async def broadcast_to_all_users(self, message: dict, exclude_user_id: Optional[int] = None):
    for user_id, connections in self.user_connections.items():
        if exclude_user_id and user_id == exclude_user_id:
            continue
```

**Usage:** Can exclude a specific user from receiving broadcast
**Note:** Currently NOT used (creator receives their own broadcasts)

#### 2. Admin-Only Broadcasting (Lines 141-161)

```python
async def send_to_admins(self, creator_id: int, message: dict):
    """Send message to all admins of a creator"""
    if creator_id not in self.admin_connections:
        return False

    for websocket in self.admin_connections[creator_id].copy():
        await websocket.send_json(message)
```

**Usage:** Send messages only to creator and their team members
**Note:** Currently NOT used in any endpoint

#### 3. Acknowledgment-Based Filtering (Lines 98-112)

```python
user_key = f"broadcast:{broadcast_id}:ack:{user_info['user_id']}"
acknowledged = redis_client.get(user_key) is not None

if not acknowledged:
    await websocket.send_json({...})
```

**Usage:** Don't send broadcast to users who already acknowledged it
**Applies to:** `send_active_broadcast_to_user()` and GET `/api/creator/broadcast/active`

### No Advanced Filtering

The current implementation does NOT support:
- Filtering by user role/tier
- Filtering by subscription status
- Geographic filtering
- Time-based filtering
- Topic-based filtering

---

## 8. Migration Checklist

### Phase 1: Preparation

- [ ] **Verify Redis connectivity** in all environments
  - [ ] Check Redis URL in environment variables
  - [ ] Test Redis connection from each replica
  - [ ] Verify Redis pub/sub is enabled

- [ ] **Review centralized WebSocketManager**
  - [ ] Read `/home/tundragoon/projects/audio-streaming-appT/websocket_manager.py`
  - [ ] Understand Redis pub/sub pattern
  - [ ] Review migration guide

- [ ] **Backup current implementation**
  - [ ] Create branch: `git checkout -b backup/broadcast-pre-migration`
  - [ ] Commit current state
  - [ ] Document current behavior in tests

### Phase 2: Code Changes

- [ ] **Update imports** (Line 1-17)
  - [ ] Add: `from websocket_manager import WebSocketManager`
  - [ ] Remove custom `BroadcastWebSocketManager` class (Lines 24-161)

- [ ] **Create new WebSocketManager instance** (Line 164)
  - [ ] Replace: `broadcast_ws_manager = BroadcastWebSocketManager()`
  - [ ] With: `broadcast_ws_manager = WebSocketManager(channel="broadcasts")`

- [ ] **Update WebSocket endpoint** (Lines 167-241)
  - [ ] Change `connect()` signature
  - [ ] Update `disconnect()` call
  - [ ] Simplify message handling

- [ ] **Update broadcast endpoints**
  - [ ] POST `/broadcast` - Line 349
  - [ ] POST `/broadcast/clear` - Line 398
  - [ ] GET `/broadcast/stats` - Add missing `db` parameter

- [ ] **Remove unused methods**
  - [ ] `send_active_broadcast_to_user()` (Lines 89-114)
  - [ ] `send_to_admins()` (Lines 141-161)
  - [ ] `handle_broadcast_websocket_message()` (Lines 243-261)

- [ ] **Update authentication**
  - [ ] Consider using cookie-based auth instead of query parameter
  - [ ] Review security implications

### Phase 3: Testing

- [ ] **Single-replica testing**
  - [ ] Start single instance
  - [ ] Connect WebSocket client
  - [ ] Send broadcast
  - [ ] Verify receipt
  - [ ] Test acknowledgment
  - [ ] Test clear broadcast

- [ ] **Multi-replica testing**
  - [ ] Start 3 replicas (ports 8001, 8002, 8003)
  - [ ] Connect clients to different replicas
  - [ ] Send broadcast from replica 1
  - [ ] Verify ALL clients receive it
  - [ ] Test acknowledgments across replicas

- [ ] **Load testing**
  - [ ] Connect 100+ WebSocket clients
  - [ ] Send broadcasts
  - [ ] Monitor Redis performance
  - [ ] Check for message delivery failures

- [ ] **Error handling testing**
  - [ ] Stop Redis temporarily
  - [ ] Verify graceful degradation
  - [ ] Restart Redis
  - [ ] Verify recovery

### Phase 4: Deployment

- [ ] **Update documentation**
  - [ ] Update README
  - [ ] Update API documentation
  - [ ] Add troubleshooting guide

- [ ] **Monitoring setup**
  - [ ] Add metrics for Redis pub/sub
  - [ ] Monitor WebSocket connection counts
  - [ ] Track broadcast delivery rates

- [ ] **Deploy to staging**
  - [ ] Test with production-like load
  - [ ] Verify multi-replica behavior
  - [ ] Performance testing

- [ ] **Deploy to production**
  - [ ] Rolling deployment
  - [ ] Monitor error rates
  - [ ] Be ready to rollback

---

## 9. Code Changes Required

### Change 1: Update Imports (Lines 1-17)

**Current:**
```python
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
```

**New:**
```python
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
from websocket_manager import WebSocketManager  # ADD THIS LINE
```

### Change 2: Remove BroadcastWebSocketManager Class (Lines 24-161)

**Current:** 163 lines of custom WebSocket manager code

**New:** DELETE ENTIRELY (Lines 24-161)

### Change 3: Create WebSocketManager Instance (Line 164)

**Current:**
```python
# Global broadcast WebSocket manager instance
broadcast_ws_manager = BroadcastWebSocketManager()
```

**New:**
```python
# Global broadcast WebSocket manager instance
broadcast_ws_manager = WebSocketManager(channel="broadcasts")
```

### Change 4: Update WebSocket Endpoint (Lines 167-241)

**Current:**
```python
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

        # Prepare user info
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
        # Connect to broadcast WebSocket
        await broadcast_ws_manager.connect(websocket, user_info['user_id'], user_info)

        # Keep connection alive and handle messages
        while True:
            try:
                # Listen for messages
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)

                if data == "ping":
                    await websocket.send_text("pong")
                else:
                    # Handle other message types
                    try:
                        message = json.loads(data)
                        await handle_broadcast_websocket_message(websocket, user_info, message)
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
```

**New:**
```python
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
        # Connect to broadcast WebSocket (CHANGED: simplified signature)
        await broadcast_ws_manager.connect(websocket, user_id=user_id_str)

        # Send active broadcast if exists (ADDED: handle manually now)
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
                                data = json.loads(broadcast_data)
                                broadcast_id = data.get("id")

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
```

**Key Changes:**
- Line 201: `connect()` now only takes `user_id` (as string)
- Lines 204-226: Moved active broadcast logic into endpoint (was in manager)
- Lines 238-264: Inlined message handling (was in separate function)
- Removed references to `user_info` parameter in `connect()`

### Change 5: Remove handle_broadcast_websocket_message Function (Lines 243-261)

**Current:**
```python
async def handle_broadcast_websocket_message(websocket: WebSocket, user_info: dict,
                                           message: dict):
    """Handle incoming WebSocket messages for broadcasts

    Note: Changed from User object + db to user_info dict
    """
    message_type = message.get("type")

    if message_type == "acknowledge_broadcast":
        # Handle broadcast acknowledgment
        broadcast_id = message.get("broadcast_id")
        if broadcast_id:
            user_key = f"broadcast:{broadcast_id}:ack:{user_info['user_id']}"
            redis_client.set(user_key, "1")
            logger.info(f"User {user_info['user_id']} acknowledged broadcast {broadcast_id}")

    elif message_type == "get_active_broadcast":
        # Send active broadcast to user
        await broadcast_ws_manager.send_active_broadcast_to_user(websocket, user_info)
```

**New:** DELETE ENTIRELY (now inlined in WebSocket endpoint)

### Change 6: Update POST /api/creator/broadcast (Line 349)

**Current:**
```python
# Send via WebSocket to all connected users (including the creator)
sent_count = await broadcast_ws_manager.broadcast_to_all_users(
    websocket_message
    # Removed exclude_user_id parameter so creator also receives the broadcast
)
```

**New:**
```python
# Send via WebSocket to ALL replicas and their connected users
await broadcast_ws_manager.broadcast(websocket_message)

# Get local connection count for response
sent_count = broadcast_ws_manager.get_connection_count()
```

**Note:** Return value changes - `broadcast()` doesn't return count, use `get_connection_count()` instead

### Change 7: Update POST /api/creator/broadcast/clear (Line 398)

**Current:**
```python
sent_count = await broadcast_ws_manager.broadcast_to_all_users(clear_message)
```

**New:**
```python
await broadcast_ws_manager.broadcast(clear_message)
sent_count = broadcast_ws_manager.get_connection_count()
```

### Change 8: Fix GET /api/creator/broadcast/stats (Lines 501-535)

**Current (BROKEN):**
```python
@broadcast_router.get("/broadcast/stats")
async def get_broadcast_stats(current_user: User = Depends(login_required)):
    """Get broadcast statistics for creators"""
    verify_creator(current_user)

    try:
        # Count recent broadcasts
        recent_broadcasts = db.query(Broadcast).filter(  # ❌ db not defined!
            Broadcast.created_by_id == current_user.id,
            Broadcast.created_at >= datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)
        ).count()

        # Get connected users count
        connected_users = len(broadcast_ws_manager.user_connections)

        return {
            "status": "success",
            "stats": {
                "broadcasts_today": recent_broadcasts,
                "active_broadcast": redis_client.exists("current_broadcast"),
                "connected_users": connected_users,
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
```

**New (FIXED):**
```python
@broadcast_router.get("/broadcast/stats")
async def get_broadcast_stats(
    db: Session = Depends(get_db),  # ✅ ADD THIS
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
        connected_users = broadcast_ws_manager.get_user_count()  # ✅ CHANGED

        return {
            "status": "success",
            "stats": {
                "broadcasts_today": recent_broadcasts,
                "active_broadcast": redis_client.exists("current_broadcast"),
                "connected_users": connected_users,
                "connected_users_note": "Count is for this replica only",  # ✅ ADD NOTE
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
```

### Change 9: Add Cleanup to app.py Lifespan

**Location:** `/home/tundragoon/projects/audio-streaming-appT/app.py`

**Find the lifespan context manager and add:**

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    # ... existing startup code ...

    yield

    # Shutdown logic
    # ... existing shutdown code ...

    # ADD THIS: Cleanup broadcast WebSocket manager
    from broadcast_router import broadcast_ws_manager
    await broadcast_ws_manager.close()
```

---

## 10. Testing Steps

### Test 1: Single Replica - Basic Functionality

**Setup:**
```bash
# Terminal 1: Start single instance
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

**Test Steps:**

1. **Connect WebSocket Client**
   ```javascript
   // Browser console
   const ws = new WebSocket('ws://localhost:8000/api/creator/broadcast/ws?user_id=1');

   ws.onopen = () => console.log('Connected');
   ws.onmessage = (e) => console.log('Message:', e.data);
   ws.onerror = (e) => console.error('Error:', e);
   ```

2. **Verify Connection Confirmation**
   - Should receive: `{"type": "connected", "message": "Connected to broadcast live updates"}`

3. **Send Broadcast**
   ```bash
   curl -X POST http://localhost:8000/api/creator/broadcast \
     -H "Content-Type: application/json" \
     -H "Cookie: session=YOUR_SESSION_COOKIE" \
     -d '{"message": "Test broadcast", "type": "info"}'
   ```

4. **Verify WebSocket Receives Broadcast**
   - Should receive:
   ```json
   {
     "type": "new_broadcast",
     "broadcast": {
       "id": "...",
       "message": "Test broadcast",
       "message_type": "info",
       "created_by": "...",
       "created_at": "..."
     }
   }
   ```

5. **Test Acknowledgment**
   ```javascript
   // Send via WebSocket
   ws.send(JSON.stringify({
     type: "acknowledge_broadcast",
     broadcast_id: "BROADCAST_ID_FROM_PREVIOUS_MESSAGE"
   }));
   ```

6. **Verify Acknowledgment Stored**
   ```bash
   redis-cli GET "broadcast:BROADCAST_ID:ack:1"
   # Should return: "1"
   ```

7. **Test Clear Broadcast**
   ```bash
   curl -X POST http://localhost:8000/api/creator/broadcast/clear \
     -H "Cookie: session=YOUR_SESSION_COOKIE"
   ```

8. **Verify Clear Message**
   - WebSocket should receive:
   ```json
   {
     "type": "broadcast_cleared",
     "message": "Active broadcast has been cleared"
   }
   ```

### Test 2: Multi-Replica - Cross-Replica Broadcasting

**Setup:**
```bash
# Terminal 1: Replica 1
uvicorn app:app --host 0.0.0.0 --port 8001

# Terminal 2: Replica 2
uvicorn app:app --host 0.0.0.0 --port 8002

# Terminal 3: Replica 3
uvicorn app:app --host 0.0.0.0 --port 8003
```

**Test Steps:**

1. **Connect Clients to Different Replicas**
   ```javascript
   // Browser window 1
   const ws1 = new WebSocket('ws://localhost:8001/api/creator/broadcast/ws?user_id=1');
   ws1.onmessage = (e) => console.log('[Replica 1]', e.data);

   // Browser window 2
   const ws2 = new WebSocket('ws://localhost:8002/api/creator/broadcast/ws?user_id=2');
   ws2.onmessage = (e) => console.log('[Replica 2]', e.data);

   // Browser window 3
   const ws3 = new WebSocket('ws://localhost:8003/api/creator/broadcast/ws?user_id=3');
   ws3.onmessage = (e) => console.log('[Replica 3]', e.data);
   ```

2. **Send Broadcast via Replica 1**
   ```bash
   curl -X POST http://localhost:8001/api/creator/broadcast \
     -H "Content-Type: application/json" \
     -H "Cookie: session=YOUR_SESSION_COOKIE" \
     -d '{"message": "Multi-replica test", "type": "info"}'
   ```

3. **Verify ALL Clients Receive Broadcast** ✅
   - ws1 (connected to replica 1) should receive message
   - ws2 (connected to replica 2) should receive message ← **This is the critical test**
   - ws3 (connected to replica 3) should receive message ← **This is the critical test**

4. **Monitor Redis Pub/Sub**
   ```bash
   # Terminal 4: Monitor Redis
   redis-cli SUBSCRIBE broadcasts

   # Should see published message when broadcast is sent
   ```

5. **Test from Different Replica**
   ```bash
   # Send via Replica 2
   curl -X POST http://localhost:8002/api/creator/broadcast \
     -H "Content-Type: application/json" \
     -H "Cookie: session=YOUR_SESSION_COOKIE" \
     -d '{"message": "From replica 2", "type": "warning"}'
   ```

6. **Verify ALL Clients Still Receive It**
   - All three WebSocket clients should receive the message regardless of which replica sent it

### Test 3: Connection Statistics

**Test Steps:**

1. **Connect Multiple Clients to Different Replicas**
   - 2 clients to replica 1
   - 3 clients to replica 2
   - 1 client to replica 3
   - Total: 6 clients

2. **Check Stats from Replica 1**
   ```bash
   curl http://localhost:8001/api/creator/broadcast/stats \
     -H "Cookie: session=YOUR_SESSION_COOKIE"
   ```

   **Expected Response:**
   ```json
   {
     "status": "success",
     "stats": {
       "broadcasts_today": 0,
       "active_broadcast": false,
       "connected_users": 2,
       "connected_users_note": "Count is for this replica only",
       "max_characters": 280
     }
   }
   ```

3. **Check Stats from Replica 2**
   - Should show `"connected_users": 3`

4. **Check Stats from Replica 3**
   - Should show `"connected_users": 1`

**Note:** Connection counts are per-replica (this is expected behavior)

### Test 4: Error Handling - Redis Failure

**Test Steps:**

1. **Start with Working System**
   - Connect WebSocket clients
   - Verify broadcasts work

2. **Stop Redis**
   ```bash
   # If using Docker
   docker stop redis-container

   # If using systemd
   sudo systemctl stop redis
   ```

3. **Try to Send Broadcast**
   ```bash
   curl -X POST http://localhost:8000/api/creator/broadcast \
     -H "Content-Type: application/json" \
     -H "Cookie: session=YOUR_SESSION_COOKIE" \
     -d '{"message": "Test during Redis failure", "type": "alert"}'
   ```

4. **Verify Graceful Degradation**
   - Broadcast should still work for LOCAL connections
   - Check logs for error messages about Redis
   - Application should NOT crash

5. **Restart Redis**
   ```bash
   docker start redis-container
   # or
   sudo systemctl start redis
   ```

6. **Verify Recovery**
   - Send another broadcast
   - Should work across all replicas again
   - Check logs for recovery messages

### Test 5: Load Testing

**Setup:**
```bash
# Install websocket-client if needed
pip install websocket-client
```

**Load Test Script:**
```python
# test_load.py
import websocket
import json
import threading
import time

def connect_client(client_id):
    def on_message(ws, message):
        print(f"[Client {client_id}] Received: {message}")

    def on_error(ws, error):
        print(f"[Client {client_id}] Error: {error}")

    def on_close(ws, close_status_code, close_msg):
        print(f"[Client {client_id}] Closed")

    def on_open(ws):
        print(f"[Client {client_id}] Connected")

    ws = websocket.WebSocketApp(
        f"ws://localhost:8000/api/creator/broadcast/ws?user_id={client_id}",
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )

    ws.run_forever()

# Connect 100 clients
threads = []
for i in range(100):
    t = threading.Thread(target=connect_client, args=(i,))
    t.daemon = True
    t.start()
    threads.append(t)
    time.sleep(0.1)  # Stagger connections

print("All clients connected. Press Ctrl+C to exit.")

# Keep main thread alive
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("Shutting down...")
```

**Test Steps:**

1. **Run Load Test**
   ```bash
   python test_load.py
   ```

2. **Wait for All Connections**
   - Should see 100 "Connected" messages

3. **Send Broadcast**
   ```bash
   curl -X POST http://localhost:8000/api/creator/broadcast \
     -H "Content-Type: application/json" \
     -H "Cookie: session=YOUR_SESSION_COOKIE" \
     -d '{"message": "Load test broadcast", "type": "info"}'
   ```

4. **Verify All Clients Receive Message**
   - Should see 100 "Received" messages
   - Check for any errors or timeouts

5. **Monitor Redis Performance**
   ```bash
   redis-cli INFO stats | grep total_commands_processed
   redis-cli INFO stats | grep instantaneous_ops_per_sec
   ```

6. **Check Application Metrics**
   - Monitor CPU usage
   - Monitor memory usage
   - Check for any connection drops

### Test 6: Acknowledgment Persistence

**Test Steps:**

1. **Connect Client and Receive Broadcast**
   ```javascript
   const ws = new WebSocket('ws://localhost:8000/api/creator/broadcast/ws?user_id=1');
   ```

2. **Send Broadcast**
   ```bash
   curl -X POST http://localhost:8000/api/creator/broadcast \
     -H "Cookie: session=YOUR_SESSION_COOKIE" \
     -d '{"message": "Acknowledgment test", "type": "info"}'
   ```

3. **Client Acknowledges**
   ```javascript
   ws.send(JSON.stringify({
     type: "acknowledge_broadcast",
     broadcast_id: "BROADCAST_ID"
   }));
   ```

4. **Disconnect Client**
   ```javascript
   ws.close();
   ```

5. **Reconnect Same Client**
   ```javascript
   const ws2 = new WebSocket('ws://localhost:8000/api/creator/broadcast/ws?user_id=1');
   ```

6. **Verify Broadcast NOT Resent**
   - Client should NOT receive the active broadcast
   - Because acknowledgment was stored in Redis

7. **Connect Different Client**
   ```javascript
   const ws3 = new WebSocket('ws://localhost:8000/api/creator/broadcast/ws?user_id=2');
   ```

8. **Verify Broadcast IS Sent**
   - Client 2 should receive active broadcast
   - Because they haven't acknowledged it yet

---

## Summary

### Current State
- ❌ **Single-replica only** - broadcasts don't work across load-balanced containers
- ✅ Database storage works
- ✅ Redis acknowledgment tracking works
- ✅ REST API endpoints work
- ❌ WebSocket broadcasting broken in multi-replica setup

### Migration Required
- Replace `BroadcastWebSocketManager` with centralized `WebSocketManager`
- Enable Redis pub/sub for cross-replica broadcasting
- Simplify code (reduce from 534 lines)
- Fix bugs (missing `db` parameter in stats endpoint)

### Expected Outcome
- ✅ Broadcasts work across all replicas
- ✅ Users receive messages regardless of which container they're connected to
- ✅ Scalable to unlimited replicas
- ✅ Graceful degradation if Redis fails
- ✅ Simpler, more maintainable code

### Files to Change
1. `/home/tundragoon/projects/audio-streaming-appT/broadcast_router.py` - Main changes
2. `/home/tundragoon/projects/audio-streaming-appT/app.py` - Add cleanup to lifespan

### Testing Priority
1. **Critical:** Multi-replica broadcasting (Test 2)
2. **High:** Single replica functionality (Test 1)
3. **Medium:** Error handling (Test 4)
4. **Medium:** Load testing (Test 5)
5. **Low:** Statistics (Test 3)
6. **Low:** Acknowledgment persistence (Test 6)

---

**Document Created:** 2025-11-05
**Author:** Claude Code Analysis
**Last Updated:** 2025-11-05
