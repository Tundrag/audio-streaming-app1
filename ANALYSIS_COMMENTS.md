# Track Comment WebSocket System Analysis

**Date:** 2025-11-05
**File Analyzed:** `/home/tundragoon/projects/audio-streaming-appT/comment_routes.py`
**Status:** Custom WebSocket Manager (Not Using Centralized WebSocketManager)

---

## Executive Summary

The track comment system in `comment_routes.py` currently uses a **custom `CommentConnectionManager` class** for WebSocket functionality. This is a legacy implementation that does **NOT** use the centralized `WebSocketManager` (with Redis pub/sub support). This means the comment system will **NOT work correctly in multi-replica/multi-container deployments** because each replica only knows about its own WebSocket connections.

**Critical Issue:** In a load-balanced environment, users connected to different replicas will not receive real-time updates from each other.

---

## Current Implementation Details

### 1. WebSocket Connection Manager

**Location:** Lines 32-89 in `comment_routes.py`

```python
class CommentConnectionManager:
    def __init__(self):
        # Dictionary of track_id -> set of WebSocket connections
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        # Dictionary of WebSocket -> user info for identification
        self.connection_users: Dict[WebSocket, dict] = {}
```

**Key Methods:**
- `connect()` (lines 39-53): Accepts WebSocket connection and registers it by track_id
- `disconnect()` (lines 55-62): Removes WebSocket connection
- `broadcast_to_track()` (lines 64-75): Sends message to all users watching a specific track
- `send_to_user()` (lines 77-86): Sends message to specific user across all connections

**Problem:** All connections are stored in-memory only. No Redis pub/sub integration.

---

### 2. WebSocket Endpoint

**Location:** Lines 92-156 in `comment_routes.py`

```python
@comment_router.websocket("/ws/track/{track_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    track_id: str,
    user_id: int = Query(..., description="User ID for authentication")
):
```

**Authentication Flow:**
1. Creates manual session (line 100)
2. Validates user by ID (lines 103-106)
3. Validates track exists (lines 109-112)
4. Closes DB session before entering message loop (line 123) ✅ **Good pattern**
5. Connects user to track (line 127)
6. Listens for incoming messages (typing indicators)

**Message Format:**
```json
{
  "type": "connected",
  "track_id": "track123",
  "message": "Connected to comment updates"
}
```

**Typing Indicators:**
```json
{
  "type": "user_typing",
  "user_id": 42,
  "username": "john_doe",
  "is_typing": true
}
```

---

### 3. Comment API Endpoints

#### 3.1 GET `/api/tracks/{track_id}/comments`
**Location:** Lines 809-868
**Purpose:** Fetch all comments for a track
**Returns:** Array of comment objects with user info, like counts, edit status

#### 3.2 POST `/api/tracks/{track_id}/comments` (Lines 870-987)
**Purpose:** Create a new comment or reply
**WebSocket Broadcasts:**
- Line 929: Broadcasts "new_comment" to track
- Lines 935-961: Sends mention notifications via WebSocket
- Lines 964-979: Background tasks for database notifications

**Critical Code:**
```python
# Line 929: Broadcast new comment via WebSocket
await comment_manager.broadcast_to_track(track_id, {
    "type": "new_comment",
    "comment": comment_data
})
```

**Problem:** `broadcast_to_track()` only sends to local connections on this replica. ❌

#### 3.3 DELETE `/api/comments/{comment_id}` (Lines 989-1054)
**Purpose:** Delete a comment
**WebSocket Broadcasts:**
- Line 1040: Broadcasts "comment_deleted" to track

```python
# Line 1040: Broadcast the deletion via WebSocket
await comment_manager.broadcast_to_track(track_id, {
    "type": "comment_deleted",
    "comment_id": comment_id
})
```

**Problem:** Same issue - only local broadcast. ❌

#### 3.4 PUT `/api/comments/{comment_id}` (Lines 1056-1102)
**Purpose:** Edit existing comment
**WebSocket Broadcasts:**
- Line 1086: Broadcasts "comment_edited" to track

```python
# Line 1086: Broadcast the edit via WebSocket
await comment_manager.broadcast_to_track(str(comment.track_id), {
    "type": "comment_edited",
    "comment_id": comment.id,
    "content": comment.content,
    "timestamp": comment.timestamp,
    "is_edited": True,
    "last_edited_at": comment.last_edited_at.isoformat()
})
```

**Problem:** Same issue - only local broadcast. ❌

#### 3.5 POST `/api/comments/{comment_id}/like` (Lines 1103-1146)
**Purpose:** Like a comment
**WebSocket:** No real-time broadcast (only background notification)

#### 3.6 DELETE `/api/comments/{comment_id}/like` (Lines 1147-1175)
**Purpose:** Unlike a comment
**WebSocket:** No real-time broadcast

---

### 4. Notification System

**Background Tasks:**
- `create_comment_notifications()` (lines 402-509): Notifies creator, team members, and parent comment authors
- `process_mentions()` (lines 158-323): Processes @mentions and sends notifications
- `create_comment_like_notification()` (lines 511-560): Notifies when comment is liked

**Notification Creation:**
- Uses raw SQL via `create_notification()` (lines 328-384)
- Avoids enum validation issues
- Commits immediately to database

---

### 5. Track Metrics & Likes

**Endpoints:**
- `GET /api/tracks/{track_id}/metrics` (lines 668-706): Returns likes, comments, shares counts
- `POST /api/tracks/{track_id}/like` (lines 708-743): Like a track
- `DELETE /api/tracks/{track_id}/like` (lines 745-767): Unlike a track
- `POST /api/tracks/{track_id}/share` (lines 769-803): Track sharing

**Storage:** Uses Redis for like/share counts:
```python
track_likes_users_key = f"track:{track_id}:likes:users"
track_shares_key = f"track:{track_id}:shares"
```

---

## Migration to WebSocketManager

### Why Migrate?

1. **Multi-replica support:** Current system fails in load-balanced deployments
2. **Consistency:** Uses same pattern as broadcasts, forum, book requests
3. **Maintainability:** Centralized WebSocket logic
4. **Reliability:** Redis pub/sub ensures message delivery across replicas

---

## Migration Checklist

### Phase 1: Replace Connection Manager

**File:** `comment_routes.py`

#### Step 1.1: Update Imports (Line 8-19)
**Change:**
```python
# OLD (line 8)
from fastapi import WebSocket, WebSocketDisconnect, Query

# NEW (add this import)
from websocket_manager import WebSocketManager
```

#### Step 1.2: Replace CommentConnectionManager (Lines 32-89)
**Delete:** Entire `CommentConnectionManager` class (lines 32-89)

**Replace with:**
```python
# Create singleton WebSocket manager for track comments
comment_manager = WebSocketManager(channel="track_comments")
```

**Lines to delete:** 32-89 (58 lines)
**Lines to add:** 1-2 (2 lines)
**Net change:** -56 lines ✅

---

### Phase 2: Update WebSocket Endpoint

**Location:** Lines 92-156

#### Step 2.1: Simplify Connection Logic
**OLD CODE (lines 92-156):**
```python
@comment_router.websocket("/ws/track/{track_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    track_id: str,
    user_id: int = Query(..., description="User ID for authentication")
):
    """WebSocket endpoint for live comment updates"""
    # Create manual session for auth only
    db = SessionLocal()
    try:
        # Get user by ID
        current_user = db.query(User).filter(User.id == user_id).first()
        if not current_user:
            await websocket.close(code=1008, reason="User not found")
            return

        # Verify track exists
        track = db.query(Track).filter(Track.id == track_id).first()
        if not track:
            await websocket.close(code=1008, reason="Track not found")
            return

        # Auth complete - prepare user info
        user_info = {
            "user_id": current_user.id,
            "username": current_user.username,
            "is_creator": current_user.is_creator,
            "is_team": current_user.is_team
        }
    finally:
        # Close DB BEFORE entering loop
        db.close()

    # NOW enter message loop WITHOUT db
    try:
        await comment_manager.connect(websocket, track_id, user_info)

        # Keep connection alive and handle incoming messages
        while True:
            try:
                # Listen for any client messages (like typing indicators)
                data = await websocket.receive_json()

                # Handle typing indicators
                if data.get("type") == "typing":
                    await comment_manager.broadcast_to_track(track_id, {
                        "type": "user_typing",
                        "user_id": user_info["user_id"],
                        "username": user_info["username"],
                        "is_typing": data.get("is_typing", False)
                    })

            except WebSocketDisconnect:
                break
            except Exception as e:
                logging.error(f"WebSocket message error: {e}")
                break

    except Exception as e:
        logging.error(f"WebSocket connection error: {e}")
        await websocket.close(code=1011, reason="Internal error")

    finally:
        comment_manager.disconnect(websocket, track_id)
```

**NEW CODE:**
```python
@comment_router.websocket("/ws/track/{track_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    track_id: str,
    user_id: int = Query(..., description="User ID for authentication")
):
    """WebSocket endpoint for live comment updates on a specific track"""
    # Create manual session for auth only
    db = SessionLocal()
    try:
        # Get user by ID
        current_user = db.query(User).filter(User.id == user_id).first()
        if not current_user:
            await websocket.close(code=1008, reason="User not found")
            return

        # Verify track exists
        track = db.query(Track).filter(Track.id == track_id).first()
        if not track:
            await websocket.close(code=1008, reason="Track not found")
            return
    finally:
        # Close DB BEFORE entering loop
        db.close()

    # Connect using centralized WebSocket manager
    # Use composite key "track:{track_id}:user:{user_id}" to support multiple tracks per user
    user_key = f"track:{track_id}:user:{user_id}"

    try:
        await comment_manager.connect(websocket, user_id=user_key)

        # Send initial connection message with track context
        await websocket.send_json({
            "type": "connected",
            "track_id": track_id,
            "message": "Connected to comment updates"
        })

        # Keep connection alive and handle incoming messages
        while True:
            try:
                data = await websocket.receive_json()

                # Handle typing indicators
                if data.get("type") == "typing":
                    await comment_manager.broadcast({
                        "type": "user_typing",
                        "track_id": track_id,
                        "user_id": current_user.id,
                        "username": current_user.username,
                        "is_typing": data.get("is_typing", False)
                    })

            except WebSocketDisconnect:
                break
            except Exception as e:
                logging.error(f"WebSocket message error: {e}")
                break

    except Exception as e:
        logging.error(f"WebSocket connection error: {e}")
        try:
            await websocket.close(code=1011, reason="Internal error")
        except:
            pass
    finally:
        comment_manager.disconnect(websocket)
```

**Key Changes:**
1. Removed `user_info` dict - not needed
2. Changed `connect()` signature: `connect(websocket, user_id=user_key)` instead of `connect(websocket, track_id, user_info)`
3. Changed broadcasts from `broadcast_to_track()` to `broadcast()` with track_id in message
4. Simplified error handling

**Note:** The centralized WebSocketManager doesn't have track-specific broadcasting. Instead, we include `track_id` in every message and let clients filter on the frontend.

---

### Phase 3: Update Comment CRUD Endpoints

#### Step 3.1: Update Create Comment (Lines 870-987)
**Location:** Line 929

**OLD:**
```python
# Broadcast new comment via WebSocket
await comment_manager.broadcast_to_track(track_id, {
    "type": "new_comment",
    "comment": comment_data
})
```

**NEW:**
```python
# Broadcast new comment via WebSocket to all replicas
await comment_manager.broadcast({
    "type": "new_comment",
    "track_id": track_id,
    "comment": comment_data
})
```

**Changes:**
- Replace `broadcast_to_track(track_id, data)` with `broadcast(data)`
- Add `"track_id": track_id` to message payload

#### Step 3.2: Update Mention Notifications (Lines 945-961)
**OLD:**
```python
# Line 945: Send WebSocket notifications for mentions
await comment_manager.send_to_user(album.created_by_id, {
    "type": "mention",
    "comment": comment_data,
    "from_user": current_user.username
})
```

**NEW:**
```python
# Send WebSocket notifications for mentions
await comment_manager.send_to_user(
    user_id=str(album.created_by_id),
    message={
        "type": "mention",
        "track_id": track_id,
        "comment": comment_data,
        "from_user": current_user.username
    }
)
```

**Changes:**
- Use named parameters: `user_id=str(...)` and `message={...}`
- Convert user_id to string
- Add `"track_id"` to message

#### Step 3.3: Update Delete Comment (Lines 989-1054)
**Location:** Line 1040

**OLD:**
```python
# Broadcast the deletion via WebSocket
await comment_manager.broadcast_to_track(track_id, {
    "type": "comment_deleted",
    "comment_id": comment_id
})
```

**NEW:**
```python
# Broadcast the deletion via WebSocket to all replicas
await comment_manager.broadcast({
    "type": "comment_deleted",
    "track_id": track_id,
    "comment_id": comment_id
})
```

#### Step 3.4: Update Edit Comment (Lines 1056-1102)
**Location:** Line 1086

**OLD:**
```python
# Broadcast the edit via WebSocket
await comment_manager.broadcast_to_track(str(comment.track_id), {
    "type": "comment_edited",
    "comment_id": comment.id,
    "content": comment.content,
    "timestamp": comment.timestamp,
    "is_edited": True,
    "last_edited_at": comment.last_edited_at.isoformat()
})
```

**NEW:**
```python
# Broadcast the edit via WebSocket to all replicas
await comment_manager.broadcast({
    "type": "comment_edited",
    "track_id": str(comment.track_id),
    "comment_id": comment.id,
    "content": comment.content,
    "timestamp": comment.timestamp,
    "is_edited": True,
    "last_edited_at": comment.last_edited_at.isoformat() if comment.last_edited_at else None
})
```

---

### Phase 4: Frontend Updates

The frontend JavaScript will need to filter messages by `track_id`:

**Location:** Wherever WebSocket messages are received (likely in a JS file)

**OLD (example):**
```javascript
socket.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.type === 'new_comment') {
        addCommentToUI(data.comment);
    }
};
```

**NEW:**
```javascript
const currentTrackId = "track123"; // Get from page context

socket.onmessage = (event) => {
    const data = JSON.parse(event.data);

    // Filter by track_id
    if (data.track_id && data.track_id !== currentTrackId) {
        return; // Ignore messages for other tracks
    }

    if (data.type === 'new_comment') {
        addCommentToUI(data.comment);
    }
};
```

---

## Complete Code Replacement Summary

### Files to Modify
1. `/home/tundragoon/projects/audio-streaming-appT/comment_routes.py`
2. Frontend JavaScript file(s) handling comment WebSockets

### Lines to Change in comment_routes.py

| Line Range | Action | Description |
|------------|--------|-------------|
| 8 | Add import | Add `from websocket_manager import WebSocketManager` |
| 32-89 | Delete | Remove entire `CommentConnectionManager` class |
| 90-91 | Replace | Replace with `comment_manager = WebSocketManager(channel="track_comments")` |
| 92-156 | Replace | Update `websocket_endpoint()` function (see Step 2.1) |
| 929 | Modify | Change `broadcast_to_track()` to `broadcast()` with track_id |
| 945-961 | Modify | Update `send_to_user()` calls with named parameters |
| 1040 | Modify | Change `broadcast_to_track()` to `broadcast()` with track_id |
| 1086 | Modify | Change `broadcast_to_track()` to `broadcast()` with track_id |

### Estimated Changes
- **Lines deleted:** ~58
- **Lines added:** ~45
- **Net change:** -13 lines (simpler code!)

---

## Testing Steps

### 1. Unit Testing
```bash
# Start single replica
uvicorn app:app --host 0.0.0.0 --port 8000 --reload

# Open browser console
# Connect to WebSocket
const ws = new WebSocket('ws://localhost:8000/api/ws/track/test-track-123?user_id=1');

ws.onmessage = (event) => {
    console.log('Received:', JSON.parse(event.data));
};

# Create comment via API
fetch('/api/tracks/test-track-123/comments', {
    method: 'POST',
    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: 'content=Test comment&timestamp=0'
});

# Verify WebSocket receives new_comment message
```

### 2. Multi-Replica Testing
```bash
# Terminal 1 - Replica 1
uvicorn app:app --port 8001

# Terminal 2 - Replica 2
uvicorn app:app --port 8002

# Browser 1: Connect to replica 1
const ws1 = new WebSocket('ws://localhost:8001/api/ws/track/test-track?user_id=1');

# Browser 2: Connect to replica 2
const ws2 = new WebSocket('ws://localhost:8002/api/ws/track/test-track?user_id=2');

# Create comment via replica 1 API
fetch('http://localhost:8001/api/tracks/test-track/comments', {...});

# VERIFY: Both ws1 AND ws2 receive the new_comment message ✅
```

### 3. Load Testing
```bash
# Use tool like `ws` npm package
npm install -g wscat

# Connect 100 WebSocket clients
for i in {1..100}; do
    wscat -c "ws://localhost:8000/api/ws/track/test-track?user_id=$i" &
done

# Monitor Redis pub/sub
redis-cli MONITOR | grep track_comments
```

---

## Message Format Reference

### WebSocket Messages (Server → Client)

#### Connection Confirmation
```json
{
  "type": "connected",
  "track_id": "track-uuid",
  "message": "Connected to comment updates"
}
```

#### New Comment
```json
{
  "type": "new_comment",
  "track_id": "track-uuid",
  "comment": {
    "id": 42,
    "user_id": 1,
    "username": "john_doe",
    "author_is_creator": false,
    "author_is_team": false,
    "track_id": "track-uuid",
    "parent_id": null,
    "content": "Great track!",
    "timestamp": 0,
    "is_edited": false,
    "created_at": "2025-11-05T10:00:00Z",
    "like_count": 0,
    "user_has_liked": false
  }
}
```

#### Comment Edited
```json
{
  "type": "comment_edited",
  "track_id": "track-uuid",
  "comment_id": 42,
  "content": "Updated comment text",
  "timestamp": 0,
  "is_edited": true,
  "last_edited_at": "2025-11-05T10:05:00Z"
}
```

#### Comment Deleted
```json
{
  "type": "comment_deleted",
  "track_id": "track-uuid",
  "comment_id": 42
}
```

#### User Typing
```json
{
  "type": "user_typing",
  "track_id": "track-uuid",
  "user_id": 1,
  "username": "john_doe",
  "is_typing": true
}
```

#### Mention Notification
```json
{
  "type": "mention",
  "track_id": "track-uuid",
  "comment": { /* comment object */ },
  "from_user": "john_doe"
}
```

### WebSocket Messages (Client → Server)

#### Typing Indicator
```json
{
  "type": "typing",
  "is_typing": true
}
```

---

## Rollback Plan

If migration fails:

1. **Revert Git Commit:**
   ```bash
   git revert HEAD
   ```

2. **Or manually restore:**
   ```bash
   # Restore old CommentConnectionManager class
   # Revert lines 32-89, 92-156, 929, 945-961, 1040, 1086
   ```

3. **Restart application:**
   ```bash
   uvicorn app:app --reload
   ```

---

## Performance Considerations

### Before Migration
- **In-memory only:** Fast, but limited to single replica
- **No network overhead:** Direct method calls
- **Connection tracking:** Per-track dictionary lookups

### After Migration
- **Redis pub/sub:** ~1-2ms latency per message
- **Network overhead:** Serialization + Redis RTT
- **Scalability:** Unlimited replicas
- **Connection tracking:** Flat dictionary (no track separation)

### Optimization Tips
1. **Message batching:** Group multiple updates in single broadcast
2. **Message filtering:** Add client-side filtering by track_id
3. **Connection pooling:** Reuse Redis connections
4. **Compression:** Enable Redis compression for large messages

---

## Security Considerations

### Current Implementation
- ✅ Authentication via user_id query parameter
- ✅ Track existence validation
- ✅ User existence validation
- ✅ Database session closed before message loop
- ⚠️ User permissions not checked in WebSocket endpoint

### After Migration
- ✅ All existing security maintained
- ✅ Redis pub/sub channel isolation
- ⚠️ Consider adding permission checks in WebSocket endpoint
- ⚠️ Rate limiting for broadcasts (prevent spam)

### Recommendations
1. Add permission check in WebSocket endpoint:
   ```python
   # Verify user has access to track
   # (tier check, private track check, etc.)
   ```

2. Add rate limiting for typing indicators:
   ```python
   # Limit typing broadcasts to 1 per second per user
   ```

---

## Additional Notes

### Duplicate create_comment Function
**Issue:** Lines 870-987 and 1300-1378 both define `create_comment()`
**Resolution:** Remove duplicate (lines 1300-1378) or merge logic

### Forum System Reference
The forum system (`forum_routes.py`) has a similar `ForumConnectionManager` that also needs migration. Consider migrating both systems together for consistency.

### Related Files to Check
- Frontend JavaScript files handling comment WebSockets
- Integration tests for comment system
- Load balancer configuration (if applicable)

---

## References

- **WebSocketManager Source:** `/home/tundragoon/projects/audio-streaming-appT/websocket_manager.py`
- **Migration Guide:** `/home/tundragoon/projects/audio-streaming-appT/WEBSOCKET_MIGRATION_GUIDE.md`
- **Broadcast Router Example:** `/home/tundragoon/projects/audio-streaming-appT/broadcast_router.py`
- **Book Request Example:** `/home/tundragoon/projects/audio-streaming-appT/book_request.py`

---

## Conclusion

The track comment WebSocket system uses a legacy custom connection manager that is **incompatible with multi-replica deployments**. Migration to the centralized `WebSocketManager` is **strongly recommended** to ensure consistent real-time updates across all users, regardless of which replica they're connected to.

The migration is straightforward and involves:
1. Replacing the custom manager with `WebSocketManager(channel="track_comments")`
2. Updating broadcast calls to use the new API
3. Adding `track_id` to all messages for client-side filtering
4. Testing with multiple replicas

**Estimated Time:** 2-3 hours (including testing)
**Risk Level:** Low (easy rollback, well-documented pattern)
**Priority:** High (critical for production scalability)
