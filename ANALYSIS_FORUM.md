# Forum WebSocket System Analysis

## Executive Summary

The forum system currently uses a **custom WebSocket manager** (`ForumConnectionManager`) instead of the centralized `WebSocketManager` class. This analysis documents the current implementation and provides a migration plan to use `WebSocketManager(channel="forum")` for multi-replica support with Redis pub/sub.

---

## Current Implementation

### 1. Custom ForumConnectionManager (Lines 112-239)

**Location**: `/home/tundragoon/projects/audio-streaming-appT/forum_routes.py`

**Current Class Definition**:
```python
class ForumConnectionManager:
    def __init__(self):
        # Dictionary of thread_id -> set of WebSocket connections
        self.thread_connections: Dict[int, Set[WebSocket]] = {}
        # Dictionary of user_id -> set of WebSocket connections (for notifications)
        self.user_connections: Dict[int, Set[WebSocket]] = {}
        # Dictionary of WebSocket -> user info for identification
        self.connection_users: Dict[WebSocket, dict] = {}
```

**Key Methods**:
- `connect(websocket, thread_id, user_info)` - Lines 121-155
- `disconnect(websocket, thread_id=None)` - Lines 157-174
- `broadcast_to_thread(thread_id, data)` - Lines 176-188
- `send_to_user(user_id, data)` - Lines 190-209
- `broadcast_to_all_users(data)` - Lines 211-233
- `send_mention_notification(user_id, data)` - Lines 235-237

**Global Instance**:
```python
manager = ForumConnectionManager()  # Line 239
```

### 2. WebSocket Endpoints

#### Thread-Specific WebSocket (Lines 3112-3303)

**Endpoint**: `/api/forum/ws/thread/{thread_id}`

**Handler**: `secure_thread_websocket_endpoint`

**Features**:
- Cookie-based authentication using `WebSocketSessionAuth`
- Per-thread connections
- Typing indicators
- Heartbeat/ping-pong for keepalive
- Thread access validation

**Message Flow**:
```python
# Line 3197: Connect to manager
await manager.connect(websocket, thread_id, user_info)

# Lines 3228-3252: Message types handled
- "typing" → Broadcast typing indicator to thread
- "ping" → Send pong response
- "heartbeat" → Auto-sent every 30s if no messages
```

#### Global WebSocket (Lines 3579-3717)

**Endpoint**: `/api/forum/ws/global`

**Handler**: `secure_global_websocket_endpoint`

**Features**:
- Cookie-based authentication
- User-level connections (not thread-specific)
- Receives global forum notifications
- Heartbeat system

**Connection Management**:
```python
# Lines 3624-3629: Manual connection tracking
if user_info["user_id"] not in manager.user_connections:
    manager.user_connections[user_info["user_id"]] = set()
manager.user_connections[user_info["user_id"]].add(websocket)
manager.connection_users[websocket] = user_info
```

### 3. Real-Time Update Triggers

#### New Message Created (Lines 2147-2154)

```python
# Broadcast to thread participants
await manager.broadcast_to_thread(thread_id, {
    "type": "new_message",
    "message": message_response.dict()
})
```

#### Thread Updated (Lines 2388-2392)

```python
await manager.broadcast_to_thread(thread_id, {
    "type": "thread_updated",
    "thread": updated_thread_data
})
```

#### Message Edited (Lines 3006-3012)

```python
await manager.broadcast_to_thread(message.thread_id, {
    "type": "message_edited",
    "message_id": message.id,
    "content": message.content,
    "content_html": content_html,
    "is_edited": True
})
```

#### Message Deleted (Lines 3095-3100)

```python
await manager.broadcast_to_thread(thread.id, {
    "type": "message_deleted",
    "message_id": message_id
})
```

#### Message Liked/Unliked (Lines 3441-3448, 3514-3520)

```python
# Like
await manager.broadcast_to_thread(message.thread_id, {
    "type": "message_liked",
    "message_id": message_id,
    "like_count": like_count,
    "liked_by_user_id": current_user.id
})

# Unlike
await manager.broadcast_to_thread(message.thread_id, {
    "type": "message_unliked",
    "message_id": message_id,
    "like_count": like_count,
    "unliked_by_user_id": current_user.id
})
```

#### New Sub-Thread Created (Lines 3827-3833)

```python
await manager.broadcast_to_thread(parent_thread.id, broadcast_data)
```

#### @everyone Mention Notifications (Lines 901, 989, 4251, 4279)

```python
# Emergency disable/enable
await manager.broadcast_to_all_users({
    "type": "everyone_mention_disabled",
    "message": "The @everyone mention has been disabled by an administrator"
})
```

#### User Notifications (Lines 1695, 1955, 3890, 3958, 4403, 4437)

```python
# Send to specific user
await manager.send_to_user(user_id, {
    "type": "forum_notification",
    "notification": notification_data
})
```

### 4. Frontend WebSocket Implementation

**Location**: `/home/tundragoon/projects/audio-streaming-appT/static/js/forum-websockets.js`

**Class**: `ForumWebSocketManager`

**Key Features**:
- Manages both global and thread-specific WebSocket connections
- Automatic reconnection with exponential backoff
- Heartbeat system (25s interval)
- Connection status indicators
- Debug tools (window.wsDebug)

**Global WebSocket Connection**:
```javascript
// Line 92
const wsUrl = `${protocol}//${window.location.host}/api/forum/ws/global`;
```

**Thread WebSocket Connection**:
```javascript
// Line 260
const wsUrl = `${protocol}//${window.location.host}/api/forum/ws/thread/${threadId}`;
```

**Message Handlers** (Lines 137-161):
- `connected` - Connection confirmation
- `new_thread_created` - New thread broadcast
- `new_sub_thread_created` - Sub-thread created
- `thread_deleted` - Thread deletion
- `forum_notification` - User notification
- `forum_notification_count` - Update notification count
- `heartbeat` - Server heartbeat
- `pong` - Ping response

---

## Problems with Current Implementation

### 1. **No Multi-Replica Support**
- `ForumConnectionManager` only tracks local connections on a single server instance
- In a multi-container deployment, WebSocket connections on Container A won't receive broadcasts triggered by Container B
- No Redis pub/sub integration for cross-replica messaging

### 2. **Custom Implementation Instead of Centralized Manager**
- Duplicates WebSocket management logic already implemented in `WebSocketManager`
- Harder to maintain and debug
- Missing features like automatic Redis failover

### 3. **Thread-Specific vs. Global Connection Complexity**
- Two different WebSocket endpoints with different purposes
- Both use the same `ForumConnectionManager` but in different ways
- Could be simplified with proper channel-based architecture

### 4. **Inconsistent with Other Features**
- `broadcast_router.py` and `book_request.py` both have custom managers instead of using centralized `WebSocketManager`
- Should standardize across all features

---

## Migration Plan: Use WebSocketManager(channel="forum")

### Architecture Design

#### Option 1: Single Channel with Message Filtering (Recommended)

**Create one WebSocketManager instance**:
```python
from websocket_manager import WebSocketManager

forum_ws = WebSocketManager(channel="forum")
```

**Benefits**:
- Single Redis pub/sub channel
- All forum updates broadcast to all connected users
- Frontend filters messages client-side
- Simpler backend implementation

**Message Format**:
```json
{
  "type": "new_message",
  "thread_id": 123,
  "message": { ... }
}
```

Frontend filters by thread_id if needed.

#### Option 2: Multiple Channels (More Complex)

**Create separate managers**:
```python
forum_global_ws = WebSocketManager(channel="forum:global")
forum_thread_ws = WebSocketManager(channel="forum:threads")
```

**Benefits**:
- More granular control
- Can reduce unnecessary broadcasts
- Better for high-traffic scenarios

**Drawbacks**:
- More complex to manage
- Multiple Redis channels
- Harder to coordinate

**Recommendation**: Start with **Option 1** (single channel) for simplicity.

---

## Migration Checklist

### Phase 1: Backend Changes - Add WebSocketManager

#### Step 1.1: Import WebSocketManager (Line 14)

**Current**:
```python
from websocket_auth import get_websocket_auth, WebSocketSessionAuth
import asyncio
from forum_models import ForumThread, ForumMessage, ForumMention, ForumThreadFollower, ForumNotification
```

**New**:
```python
from websocket_auth import get_websocket_auth, WebSocketSessionAuth
from websocket_manager import WebSocketManager  # ← ADD THIS
import asyncio
from forum_models import ForumThread, ForumMessage, ForumMention, ForumThreadFollower, ForumNotification
```

#### Step 1.2: Create WebSocketManager Instance (After Line 35)

**Current**:
```python
templates = Jinja2Templates(directory="templates")
templates.env.globals['url_for'] = cache_busted_url_for
templates.env.filters['url_for'] = cache_busted_url_for
```

**New**:
```python
templates = Jinja2Templates(directory="templates")
templates.env.globals['url_for'] = cache_busted_url_for
templates.env.filters['url_for'] = cache_busted_url_for

# WebSocket manager for real-time forum updates
forum_ws = WebSocketManager(channel="forum")
```

#### Step 1.3: Keep Old ForumConnectionManager (Temporary)

**Keep Lines 112-239** as-is for now. We'll deprecate it gradually.

**Add comment**:
```python
# DEPRECATED: This class is being replaced by WebSocketManager
# Keep for backward compatibility during migration
class ForumConnectionManager:
    ...
```

#### Step 1.4: Create Wrapper Functions

**Add after Line 239** (after `manager = ForumConnectionManager()`):

```python
# ========================================
# NEW: WebSocketManager Integration Layer
# ========================================

async def broadcast_new_message_to_thread(thread_id: int, message_data: dict):
    """Broadcast new message to thread using WebSocketManager"""
    await forum_ws.broadcast({
        "type": "new_message",
        "thread_id": thread_id,
        "message": message_data
    })

async def broadcast_thread_update(thread_id: int, thread_data: dict):
    """Broadcast thread update using WebSocketManager"""
    await forum_ws.broadcast({
        "type": "thread_updated",
        "thread_id": thread_id,
        "thread": thread_data
    })

async def broadcast_message_edited(thread_id: int, message_id: int, content: str, content_html: str):
    """Broadcast message edit using WebSocketManager"""
    await forum_ws.broadcast({
        "type": "message_edited",
        "thread_id": thread_id,
        "message_id": message_id,
        "content": content,
        "content_html": content_html,
        "is_edited": True
    })

async def broadcast_message_deleted(thread_id: int, message_id: int):
    """Broadcast message deletion using WebSocketManager"""
    await forum_ws.broadcast({
        "type": "message_deleted",
        "thread_id": thread_id,
        "message_id": message_id
    })

async def broadcast_message_liked(thread_id: int, message_id: int, like_count: int, user_id: int):
    """Broadcast message like using WebSocketManager"""
    await forum_ws.broadcast({
        "type": "message_liked",
        "thread_id": thread_id,
        "message_id": message_id,
        "like_count": like_count,
        "liked_by_user_id": user_id
    })

async def broadcast_message_unliked(thread_id: int, message_id: int, like_count: int, user_id: int):
    """Broadcast message unlike using WebSocketManager"""
    await forum_ws.broadcast({
        "type": "message_unliked",
        "thread_id": thread_id,
        "message_id": message_id,
        "like_count": like_count,
        "unliked_by_user_id": user_id
    })

async def send_forum_notification_to_user(user_id: int, notification_data: dict):
    """Send notification to specific user using WebSocketManager"""
    await forum_ws.send_to_user(str(user_id), {
        "type": "forum_notification",
        "notification": notification_data
    })

async def broadcast_to_all_forum_users(data: dict):
    """Broadcast to all connected forum users using WebSocketManager"""
    await forum_ws.broadcast(data)
```

### Phase 2: Backend Changes - Update Broadcast Calls

#### Step 2.1: Update create_message (Line 2149)

**Current**:
```python
# Broadcast to live connections
try:
    await manager.broadcast_to_thread(thread_id, {
        "type": "new_message",
        "message": message_response.dict()
    })
except Exception as e:
    logger.error(f"Error broadcasting message: {str(e)}")
```

**New**:
```python
# Broadcast to live connections (NEW: using WebSocketManager)
try:
    await broadcast_new_message_to_thread(thread_id, message_response.dict())
    # LEGACY: Also broadcast using old manager for clients still connected to it
    await manager.broadcast_to_thread(thread_id, {
        "type": "new_message",
        "message": message_response.dict()
    })
except Exception as e:
    logger.error(f"Error broadcasting message: {str(e)}")
```

#### Step 2.2: Update update_thread (Line 2388)

**Current**:
```python
await manager.broadcast_to_thread(thread_id, {
    "type": "thread_updated",
    "thread": updated_thread_data
})
```

**New**:
```python
# NEW: WebSocketManager + Legacy
await broadcast_thread_update(thread_id, updated_thread_data)
await manager.broadcast_to_thread(thread_id, {
    "type": "thread_updated",
    "thread": updated_thread_data
})
```

#### Step 2.3: Update edit_message (Line 3008)

**Current**:
```python
await manager.broadcast_to_thread(message.thread_id, {
    "type": "message_edited",
    "message_id": message.id,
    "content": message.content,
    "content_html": content_html,
    "is_edited": True
})
```

**New**:
```python
# NEW: WebSocketManager + Legacy
await broadcast_message_edited(message.thread_id, message.id, message.content, content_html)
await manager.broadcast_to_thread(message.thread_id, {
    "type": "message_edited",
    "message_id": message.id,
    "content": message.content,
    "content_html": content_html,
    "is_edited": True
})
```

#### Step 2.4: Update delete_message (Line 3097)

**Current**:
```python
await manager.broadcast_to_thread(thread.id, {
    "type": "message_deleted",
    "message_id": message_id
})
```

**New**:
```python
# NEW: WebSocketManager + Legacy
await broadcast_message_deleted(thread.id, message_id)
await manager.broadcast_to_thread(thread.id, {
    "type": "message_deleted",
    "message_id": message_id
})
```

#### Step 2.5: Update like_message (Line 3443)

**Current**:
```python
await manager.broadcast_to_thread(message.thread_id, {
    "type": "message_liked",
    "message_id": message_id,
    "like_count": like_count,
    "liked_by_user_id": current_user.id
})
```

**New**:
```python
# NEW: WebSocketManager + Legacy
await broadcast_message_liked(message.thread_id, message_id, like_count, current_user.id)
await manager.broadcast_to_thread(message.thread_id, {
    "type": "message_liked",
    "message_id": message_id,
    "like_count": like_count,
    "liked_by_user_id": current_user.id
})
```

#### Step 2.6: Update unlike_message (Line 3516)

**Current**:
```python
await manager.broadcast_to_thread(message.thread_id, {
    "type": "message_unliked",
    "message_id": message_id,
    "like_count": like_count,
    "unliked_by_user_id": current_user.id
})
```

**New**:
```python
# NEW: WebSocketManager + Legacy
await broadcast_message_unliked(message.thread_id, message_id, like_count, current_user.id)
await manager.broadcast_to_thread(message.thread_id, {
    "type": "message_unliked",
    "message_id": message_id,
    "like_count": like_count,
    "unliked_by_user_id": current_user.id
})
```

#### Step 2.7: Update User Notifications (Lines 1695, 1955, 3890, 3958, 4403, 4437)

**Example - Line 1955**:

**Current**:
```python
send_result = await manager.send_to_user(user_id, broadcast_data)
```

**New**:
```python
# NEW: WebSocketManager + Legacy
await send_forum_notification_to_user(user_id, broadcast_data["notification"])
send_result = await manager.send_to_user(user_id, broadcast_data)
```

**Repeat for all user notification calls.**

#### Step 2.8: Update Global Broadcasts (Lines 901, 989, 4251, 4279)

**Example - Line 4251**:

**Current**:
```python
await manager.broadcast_to_all_users({
    "type": "everyone_mention_disabled",
    "message": "The @everyone mention has been disabled by an administrator"
})
```

**New**:
```python
# NEW: WebSocketManager + Legacy
await broadcast_to_all_forum_users({
    "type": "everyone_mention_disabled",
    "message": "The @everyone mention has been disabled by an administrator"
})
await manager.broadcast_to_all_users({
    "type": "everyone_mention_disabled",
    "message": "The @everyone mention has been disabled by an administrator"
})
```

### Phase 3: Backend Changes - New WebSocket Endpoint

#### Step 3.1: Create New Global WebSocket Endpoint

**Add after Line 3717** (after old global WebSocket endpoint):

```python
@forum_router.websocket("/ws/v2")
async def forum_websocket_v2(
    websocket: WebSocket,
    ws_auth: WebSocketSessionAuth = Depends(get_websocket_auth)
):
    """
    New global forum WebSocket endpoint using WebSocketManager.
    Replaces /ws/global with multi-replica support via Redis pub/sub.
    """
    current_user = None

    # Auth phase
    from database import SessionLocal
    db = SessionLocal()

    try:
        # Authenticate
        current_user = await ws_auth.authenticate_websocket(websocket, db, require_session=True)
        if not current_user:
            logger.warning("❌ Forum WebSocket v2 authentication failed")
            return

        logger.info(f"✅ Forum WebSocket v2 authenticated: user {current_user.id}")

    finally:
        db.close()

    # Connect to WebSocketManager
    try:
        await forum_ws.connect(websocket, user_id=str(current_user.id))

        # Keep connection alive - let WebSocketManager handle messages
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_json(), timeout=30)

                # Handle ping/pong
                if data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})

            except asyncio.TimeoutError:
                # Send heartbeat
                try:
                    await websocket.send_json({"type": "heartbeat"})
                except:
                    break
            except WebSocketDisconnect:
                break

    except Exception as e:
        logger.error(f"🚨 Forum WebSocket v2 error: {e}")
    finally:
        forum_ws.disconnect(websocket)
        if ws_auth:
            ws_auth.disconnect_websocket(websocket)
```

### Phase 4: Frontend Changes

#### Step 4.1: Update forum-websockets.js

**File**: `/home/tundragoon/projects/audio-streaming-appT/static/js/forum-websockets.js`

**Line 92** - Update global WebSocket URL:

**Current**:
```javascript
const wsUrl = `${protocol}//${window.location.host}/api/forum/ws/global`;
```

**New**:
```javascript
// Use new v2 endpoint with WebSocketManager
const wsUrl = `${protocol}//${window.location.host}/api/forum/ws/v2`;
```

#### Step 4.2: Update Message Handlers (Lines 137-161)

**Current**:
```javascript
const handlers = {
    'connected': () => { ... },
    'new_thread_created': () => this.forumCore.handleNewThreadCreated(data),
    ...
};
```

**New**:
```javascript
const handlers = {
    'connected': () => {
        console.log('🤝 WebSocket v2 connection confirmed:', data.message);
    },
    // Add thread_id filtering for thread-specific messages
    'new_message': () => {
        // Only handle if we're viewing this thread
        if (this.forumCore.currentThreadId === data.thread_id) {
            this.forumCore.handleNewMessage(data);
        }
    },
    'thread_updated': () => {
        if (this.forumCore.currentThreadId === data.thread_id) {
            this.forumCore.handleThreadUpdated(data);
        }
    },
    'message_edited': () => {
        if (this.forumCore.currentThreadId === data.thread_id) {
            this.forumCore.handleMessageEdited(data);
        }
    },
    'message_deleted': () => {
        if (this.forumCore.currentThreadId === data.thread_id) {
            this.forumCore.handleMessageDeleted(data);
        }
    },
    'message_liked': () => {
        if (this.forumCore.currentThreadId === data.thread_id) {
            this.forumCore.handleMessageLiked(data);
        }
    },
    'message_unliked': () => {
        if (this.forumCore.currentThreadId === data.thread_id) {
            this.forumCore.handleMessageUnliked(data);
        }
    },
    // Global messages (no filtering)
    'new_thread_created': () => this.forumCore.handleNewThreadCreated(data),
    'new_sub_thread_created': () => this.forumCore.handleNewSubThreadCreated(data),
    'thread_deleted': () => this.forumCore.handleThreadDeleted(data),
    'forum_notification': () => this.forumCore.handleForumNotification(data.notification),
    'forum_notification_count': () => this.forumCore.handleNotificationCountUpdate(data.count),
    'everyone_mention_disabled': () => this.forumCore.showToast(data.message, 'warning'),
    'everyone_mention_enabled': () => this.forumCore.showToast(data.message, 'success'),
    'heartbeat': () => this.handleHeartbeat(),
    'pong': () => this.handlePong()
};
```

#### Step 4.3: Remove Thread-Specific WebSocket (Optional)

**Consider removing thread WebSocket** (Lines 254-281) since global WebSocket now handles everything with client-side filtering.

**OR keep it** for reduced bandwidth if only interested in one thread's updates.

### Phase 5: Testing

#### Step 5.1: Test Multi-Replica Broadcast

1. **Start two app instances** (different ports)
2. **Connect User A** to Instance 1
3. **Connect User B** to Instance 2
4. **User A posts message** in thread
5. **Verify User B receives update** in real-time

**Expected**: Both users see the message instantly.

#### Step 5.2: Test Redis Failover

1. **Stop Redis**
2. **Verify**: System continues working in single-replica mode
3. **Start Redis**
4. **Verify**: System reconnects and resumes multi-replica broadcasts

#### Step 5.3: Test All Message Types

- [ ] New message created
- [ ] Message edited
- [ ] Message deleted
- [ ] Message liked/unliked
- [ ] Thread updated
- [ ] New thread created
- [ ] Sub-thread created
- [ ] User notifications
- [ ] @everyone broadcasts

#### Step 5.4: Test Connection Stability

- [ ] Heartbeat working (no disconnects)
- [ ] Reconnection after network interruption
- [ ] Multiple concurrent users (10+ connections)

### Phase 6: Cleanup (After Testing)

#### Step 6.1: Remove Old Manager Calls

**Remove all dual broadcasts** (WebSocketManager + old manager).

**Example**:
```python
# BEFORE (during migration)
await broadcast_new_message_to_thread(thread_id, message_response.dict())
await manager.broadcast_to_thread(thread_id, {...})  # ← Remove this

# AFTER
await broadcast_new_message_to_thread(thread_id, message_response.dict())
```

#### Step 6.2: Deprecate Old Endpoints

**Option A**: Remove old endpoints entirely
- `/api/forum/ws/global` → Removed
- `/api/forum/ws/thread/{thread_id}` → Removed

**Option B**: Redirect to new endpoint
```python
@forum_router.websocket("/ws/global")
async def forum_websocket_deprecated(websocket: WebSocket):
    await websocket.accept()
    await websocket.send_json({
        "type": "deprecated",
        "message": "This endpoint is deprecated. Use /api/forum/ws/v2"
    })
    await websocket.close(code=1001, reason="Endpoint deprecated")
```

#### Step 6.3: Remove ForumConnectionManager Class

**Delete Lines 112-239** (entire `ForumConnectionManager` class).

#### Step 6.4: Update Documentation

- Update API docs
- Update frontend comments
- Add migration notes to CHANGELOG

---

## Summary of Changes

### Files to Modify

1. **`/home/tundragoon/projects/audio-streaming-appT/forum_routes.py`**
   - Import `WebSocketManager`
   - Create `forum_ws = WebSocketManager(channel="forum")`
   - Add wrapper functions
   - Update all broadcast calls (dual mode during migration)
   - Add new `/ws/v2` endpoint
   - Eventually remove `ForumConnectionManager`

2. **`/home/tundragoon/projects/audio-streaming-appT/static/js/forum-websockets.js`**
   - Change global WebSocket URL to `/api/forum/ws/v2`
   - Update message handlers with thread_id filtering
   - Consider removing thread-specific WebSocket

### Code Statistics

- **Lines to add**: ~150 (wrapper functions + new endpoint)
- **Lines to modify**: ~20 (broadcast calls)
- **Lines to remove (eventually)**: ~130 (ForumConnectionManager)

### Migration Timeline

- **Phase 1-2**: 2-3 hours (add WebSocketManager, update calls)
- **Phase 3**: 1 hour (new endpoint)
- **Phase 4**: 1-2 hours (frontend updates)
- **Phase 5**: 2-4 hours (testing)
- **Phase 6**: 1 hour (cleanup)

**Total**: 7-11 hours for complete migration

---

## Redis Pub/Sub Architecture

### Current (Single Replica)

```
┌─────────────┐
│   User A    │──┐
└─────────────┘  │
                 ├──► ForumConnectionManager ──► No Redis
┌─────────────┐  │     (in-memory only)
│   User B    │──┘
└─────────────┘
```

### After Migration (Multi-Replica)

```
┌─────────────┐                    ┌──────────────────┐
│   User A    │──► Container 1 ──► │                  │
└─────────────┘                    │  Redis Pub/Sub   │
                                   │  Channel: forum  │
┌─────────────┐                    │                  │
│   User B    │──► Container 2 ──► │                  │
└─────────────┘                    └──────────────────┘
                                           │
                                           ▼
                                    Both containers
                                    receive all messages
```

### Message Flow Example

1. **User A** (on Container 1) posts message
2. **Container 1** calls `forum_ws.broadcast({...})`
3. **WebSocketManager** publishes to Redis channel "forum"
4. **Redis** broadcasts to all subscribers
5. **Both Container 1 and Container 2** receive message
6. **WebSocketManager** on each container sends to local WebSocket clients
7. **User B** (on Container 2) receives message instantly

---

## Message Format Reference

### Thread Messages

```json
{
  "type": "new_message",
  "thread_id": 123,
  "message": {
    "id": 456,
    "content": "Hello!",
    "content_html": "<p>Hello!</p>",
    "user_id": 789,
    "username": "Alice",
    "created_at": "2025-11-05T12:00:00Z",
    ...
  }
}
```

### User Notifications

```json
{
  "type": "forum_notification",
  "notification": {
    "id": 999,
    "type": "mention",
    "title": "You were mentioned",
    "content": "@Alice mentioned you",
    "thread_id": 123,
    "message_id": 456,
    ...
  }
}
```

### Global Broadcasts

```json
{
  "type": "everyone_mention_disabled",
  "message": "The @everyone mention has been disabled"
}
```

---

## API Endpoints Summary

### Current WebSocket Endpoints

| Endpoint | Purpose | Auth | Status |
|----------|---------|------|--------|
| `/api/forum/ws/thread/{thread_id}` | Thread-specific updates | Cookie | Active |
| `/api/forum/ws/global` | Global forum updates | Cookie | Active |

### New WebSocket Endpoint (After Migration)

| Endpoint | Purpose | Auth | Status |
|----------|---------|------|--------|
| `/api/forum/ws/v2` | Unified forum updates via WebSocketManager | Cookie | New |

### HTTP Endpoints (Not Modified)

- **GET** `/api/forum/threads` - List threads
- **POST** `/api/forum/threads` - Create thread
- **GET** `/api/forum/threads/{thread_id}` - Get thread
- **PATCH** `/api/forum/threads/{thread_id}` - Update thread
- **DELETE** `/api/forum/threads/{thread_id}` - Delete thread
- **POST** `/api/forum/threads/{thread_id}/messages` - Create message
- **PATCH** `/api/forum/messages/{message_id}` - Edit message
- **DELETE** `/api/forum/messages/{message_id}` - Delete message
- **POST** `/api/forum/messages/{message_id}/like` - Like message
- **DELETE** `/api/forum/messages/{message_id}/like` - Unlike message
- **POST** `/api/forum/threads/{thread_id}/follow` - Follow thread
- **DELETE** `/api/forum/threads/{thread_id}/follow` - Unfollow thread
- **GET** `/api/forum/notifications` - Get notifications
- **POST** `/api/forum/notifications/{id}/read` - Mark notification read
- **POST** `/api/forum/notifications/mark-all-read` - Mark all read

Total: **50+ endpoints** (no changes needed for HTTP endpoints)

---

## Comparison: ForumConnectionManager vs WebSocketManager

| Feature | ForumConnectionManager | WebSocketManager |
|---------|------------------------|------------------|
| **Multi-replica support** | ❌ No | ✅ Yes (Redis pub/sub) |
| **Redis failover** | ❌ N/A | ✅ Yes (graceful degradation) |
| **Connection tracking** | ✅ Yes (in-memory) | ✅ Yes (in-memory) |
| **Thread-specific broadcast** | ✅ Yes | ⚠️ Via message filtering |
| **User-specific send** | ✅ Yes | ✅ Yes |
| **Global broadcast** | ✅ Yes | ✅ Yes |
| **Message filtering** | ❌ No | ✅ Yes (optional) |
| **Auto-reconnection** | ❌ Client-side only | ✅ Server + client |
| **Code maintenance** | ⚠️ Custom (harder) | ✅ Centralized (easier) |

---

## Risks & Mitigation

### Risk 1: Breaking Existing Connections

**Mitigation**: Run both old and new systems in parallel during migration.

### Risk 2: Redis Dependency

**Mitigation**: WebSocketManager has built-in fallback to single-replica mode if Redis fails.

### Risk 3: Message Loss During Migration

**Mitigation**: Dual broadcasts ensure all clients receive messages regardless of which endpoint they're connected to.

### Risk 4: Performance Impact

**Mitigation**: Redis pub/sub is extremely fast. Expected latency increase: <10ms.

---

## Next Steps

1. **Review this analysis** with the team
2. **Decide on migration timeline**
3. **Start Phase 1** (add WebSocketManager)
4. **Test in development** environment
5. **Deploy to production** with feature flag
6. **Monitor** for issues
7. **Complete cleanup** after successful migration

---

## Questions?

- How should thread-specific messages be filtered? (Client-side vs server-side)
- Should we keep thread-specific WebSocket endpoint or remove it?
- What's the rollback plan if migration fails?
- Should we migrate broadcast_router.py and book_request.py at the same time?

---

**Generated**: 2025-11-05
**Author**: Claude Code Analysis
**Version**: 1.0
