# WebSocket Migration Guide

This guide shows how to migrate existing WebSocket endpoints to use the centralized `WebSocketManager` with Redis pub/sub for multi-replica support.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        Redis Pub/Sub                         │
│                    Channel: "broadcasts"                     │
└───────────┬──────────────┬──────────────┬──────────────────┘
            │              │              │
    ┌───────▼──────┐ ┌────▼──────┐ ┌─────▼──────┐
    │  Replica 1   │ │ Replica 2 │ │ Replica 3 │
    │  WS: A, B    │ │ WS: C, D  │ │ WS: E, F  │
    └──────────────┘ └───────────┘ └────────────┘
```

**Before:** Each replica only knows about its own WebSocket connections
**After:** All replicas receive messages via Redis and forward to their local clients

---

## 1. Broadcast System

### Before (broadcast_router.py)
```python
class BroadcastWebSocketManager:
    def __init__(self):
        self.active_connections = {}  # Only local connections

    async def broadcast(self, message: dict):
        # Only sends to THIS replica's connections ❌
        for connections in self.active_connections.values():
            for websocket in connections:
                await websocket.send_json(message)
```

### After (Using WebSocketManager)
```python
from websocket_manager import WebSocketManager

# Create singleton manager
broadcast_ws_manager = WebSocketManager(channel="broadcasts")

@app.websocket("/ws/broadcasts")
async def broadcast_websocket(websocket: WebSocket, current_user: User = Depends(get_current_user)):
    await broadcast_ws_manager.connect(websocket, user_id=str(current_user.id))
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        broadcast_ws_manager.disconnect(websocket)

@app.post("/api/broadcasts")
async def create_broadcast(message: BroadcastCreate, current_user: User = Depends(login_required)):
    # Save to database
    broadcast = create_broadcast_in_db(message, current_user)

    # Broadcast to ALL replicas ✅
    await broadcast_ws_manager.broadcast({
        "type": "new_broadcast",
        "data": {
            "id": broadcast.id,
            "title": broadcast.title,
            "content": broadcast.content,
            "author": current_user.username,
            "created_at": broadcast.created_at.isoformat()
        }
    })

    return {"success": True}
```

---

## 2. Track Comments System

### Migration
```python
from websocket_manager import WebSocketManager

# Create manager for comments
comment_ws_manager = WebSocketManager(channel="track_comments")

@app.websocket("/ws/comments/{track_id}")
async def comment_websocket(
    websocket: WebSocket,
    track_id: str,
    current_user: User = Depends(get_current_user)
):
    await comment_ws_manager.connect(websocket, user_id=str(current_user.id))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        comment_ws_manager.disconnect(websocket)

@app.post("/api/comments")
async def create_comment(comment: CommentCreate, current_user: User = Depends(login_required)):
    # Save comment
    new_comment = save_comment_to_db(comment, current_user)

    # Broadcast new comment to all users watching this track
    await comment_ws_manager.broadcast({
        "type": "new_comment",
        "track_id": comment.track_id,
        "data": {
            "id": new_comment.id,
            "content": new_comment.content,
            "author": current_user.username,
            "timestamp": comment.timestamp,
            "created_at": new_comment.created_at.isoformat()
        }
    })

    return {"success": True, "comment": new_comment}

@app.put("/api/comments/{comment_id}")
async def update_comment(comment_id: int, update: CommentUpdate):
    # Update in DB
    updated_comment = update_comment_in_db(comment_id, update)

    # Broadcast update
    await comment_ws_manager.broadcast({
        "type": "comment_updated",
        "track_id": updated_comment.track_id,
        "data": {
            "id": updated_comment.id,
            "content": updated_comment.content
        }
    })

    return {"success": True}

@app.delete("/api/comments/{comment_id}")
async def delete_comment(comment_id: int):
    comment = get_comment_from_db(comment_id)
    delete_comment_from_db(comment_id)

    # Broadcast deletion
    await comment_ws_manager.broadcast({
        "type": "comment_deleted",
        "track_id": comment.track_id,
        "data": {"id": comment_id}
    })

    return {"success": True}
```

---

## 3. Forum System

### Migration
```python
from websocket_manager import WebSocketManager

# Create manager for forum
forum_ws_manager = WebSocketManager(channel="forum")

@app.websocket("/ws/forum/{thread_id}")
async def forum_websocket(
    websocket: WebSocket,
    thread_id: str,
    current_user: User = Depends(get_current_user)
):
    await forum_ws_manager.connect(websocket, user_id=str(current_user.id))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        forum_ws_manager.disconnect(websocket)

@app.post("/api/forum/posts")
async def create_forum_post(post: ForumPostCreate, current_user: User = Depends(login_required)):
    new_post = save_forum_post(post, current_user)

    # Broadcast to all forum users
    await forum_ws_manager.broadcast({
        "type": "new_post",
        "thread_id": post.thread_id,
        "data": {
            "id": new_post.id,
            "content": new_post.content,
            "author": current_user.username,
            "created_at": new_post.created_at.isoformat()
        }
    })

    return {"success": True, "post": new_post}
```

---

## 4. Book Request System

### Migration
```python
from websocket_manager import WebSocketManager

# Create managers for book requests
book_request_ws_manager = WebSocketManager(channel="book_requests")  # For users
book_request_admin_ws_manager = WebSocketManager(channel="book_requests_admin")  # For admins

@app.websocket("/ws/book-requests")
async def book_request_websocket(
    websocket: WebSocket,
    current_user: User = Depends(get_current_user)
):
    """User WebSocket for their own book requests"""
    await book_request_ws_manager.connect(websocket, user_id=str(current_user.id))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        book_request_ws_manager.disconnect(websocket)

@app.websocket("/ws/book-requests/admin")
async def book_request_admin_websocket(
    websocket: WebSocket,
    current_user: User = Depends(verify_creator)
):
    """Admin WebSocket for all book requests"""
    await book_request_admin_ws_manager.connect(websocket, user_id=str(current_user.id))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        book_request_admin_ws_manager.disconnect(websocket)

@app.post("/api/book-requests")
async def create_book_request(request: BookRequestCreate, current_user: User = Depends(login_required)):
    new_request = save_book_request(request, current_user)

    # Notify the user who made the request
    await book_request_ws_manager.send_to_user(
        user_id=str(current_user.id),
        message={
            "type": "request_created",
            "data": {
                "id": new_request.id,
                "title": new_request.title,
                "status": "pending"
            }
        }
    )

    # Notify all admins
    await book_request_admin_ws_manager.broadcast({
        "type": "new_request",
        "data": {
            "id": new_request.id,
            "title": new_request.title,
            "user": current_user.username,
            "created_at": new_request.created_at.isoformat()
        }
    })

    return {"success": True}

@app.put("/api/book-requests/{request_id}/status")
async def update_request_status(
    request_id: int,
    update: StatusUpdate,
    current_user: User = Depends(verify_creator)
):
    book_request = update_request_status_in_db(request_id, update.status)

    # Notify the user who made the request
    await book_request_ws_manager.send_to_user(
        user_id=str(book_request.user_id),
        message={
            "type": "status_updated",
            "data": {
                "id": book_request.id,
                "status": book_request.status,
                "message": f"Your request '{book_request.title}' is now {book_request.status}"
            }
        }
    )

    # Notify all admins
    await book_request_admin_ws_manager.broadcast({
        "type": "request_updated",
        "data": {
            "id": book_request.id,
            "status": book_request.status
        }
    })

    return {"success": True}
```

---

## 5. Advanced Features

### Targeted Broadcasting
```python
# Send only to specific users
await ws_manager.send_to_user("user123", {"type": "private_message", "text": "Hello!"})

# Send to multiple users
await ws_manager.broadcast(
    {"type": "team_notification", "text": "Meeting in 5 min"},
    target_user_ids={"user1", "user2", "user3"}
)
```

### Message Filtering
```python
# Only process messages for specific track
def filter_track_messages(message):
    return message.get('track_id') == '12345'

comment_ws_manager.set_message_filter(filter_track_messages)
```

### Connection Statistics
```python
# Get stats
connection_count = ws_manager.get_connection_count()  # Connections on THIS replica
user_count = ws_manager.get_user_count()  # Unique users on THIS replica
is_connected = ws_manager.is_user_connected("user123")  # Is user connected to THIS replica
```

---

## 6. Testing Multi-Replica Setup

### Local Testing
```bash
# Terminal 1 - Replica 1
uvicorn app:app --port 8001

# Terminal 2 - Replica 2
uvicorn app:app --port 8002

# Terminal 3 - Replica 3
uvicorn app:app --port 8003
```

### Connect WebSocket Clients
```javascript
// Client A connects to Replica 1
const wsA = new WebSocket('ws://localhost:8001/ws/broadcasts');

// Client B connects to Replica 2
const wsB = new WebSocket('ws://localhost:8002/ws/broadcasts');

// Send broadcast via Replica 1's API
fetch('http://localhost:8001/api/broadcasts', {
    method: 'POST',
    body: JSON.stringify({title: 'Test', content: 'Hello!'})
});

// BOTH clients A and B should receive the message! ✅
```

---

## 7. Cleanup on Application Shutdown

Add to `app.py`:
```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    yield
    # Shutdown - cleanup all WebSocket managers
    await broadcast_ws_manager.close()
    await comment_ws_manager.close()
    await forum_ws_manager.close()
    await book_request_ws_manager.close()
    await book_request_admin_ws_manager.close()

app = FastAPI(lifespan=lifespan)
```

---

## Migration Checklist

- [ ] Replace in-memory WebSocket managers with `WebSocketManager` instances
- [ ] Update WebSocket endpoints to use `connect()` and `disconnect()`
- [ ] Update broadcast calls to use `broadcast()` or `send_to_user()`
- [ ] Test with multiple replicas
- [ ] Add cleanup to application shutdown
- [ ] Monitor Redis pub/sub performance
- [ ] Add error handling for Redis failures

---

## Benefits

✅ **Multi-replica support** - Works across load-balanced deployments
✅ **Centralized** - One manager for all WebSocket types
✅ **Resilient** - Gracefully handles Redis failures
✅ **Scalable** - Add unlimited replicas
✅ **Simple API** - Easy to integrate
✅ **Background task support** - Any process can publish to Redis
