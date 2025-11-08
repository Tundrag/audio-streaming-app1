# Book Request WebSocket System Analysis & Migration Guide

**Analysis Date**: 2025-11-05
**File Analyzed**: `/home/tundragoon/projects/audio-streaming-appT/book_request.py`
**Target Migration**: Custom `BookRequestWebSocketManager` → Centralized `WebSocketManager`

---

## Table of Contents
1. [Current Implementation Overview](#current-implementation-overview)
2. [WebSocket Endpoints](#websocket-endpoints)
3. [Connection Management](#connection-management)
4. [Message Formats](#message-formats)
5. [Notification Logic](#notification-logic)
6. [API Endpoints](#api-endpoints)
7. [Migration Checklist](#migration-checklist)
8. [Code Changes Required](#code-changes-required)
9. [Testing Plan](#testing-plan)

---

## Current Implementation Overview

### Custom Manager Class: `BookRequestWebSocketManager`

**Location**: Lines 114-299 in `book_request.py`

The current implementation uses a custom WebSocket manager specifically for book requests. This manager handles:
- User connections (regular users viewing their own requests)
- Admin connections (creators and team members managing all requests)
- Redis pub/sub for cross-container broadcasting
- Targeted messaging (user-specific and admin-specific)

**Key Features**:
- Dual connection tracking (users + admins)
- Redis pub/sub integration for multi-container support
- Hierarchical notification system (user-level and admin-level)
- Connection state tracking with user metadata

---

## WebSocket Endpoints

### 1. Main WebSocket Endpoint

**Location**: Lines 303-391

```python
@book_request_router.websocket("/ws")
async def book_request_websocket(
    websocket: WebSocket,
    user_id: int = Query(..., description="User ID for authentication")
)
```

**Endpoint URL**: `/api/book-requests/ws?user_id={user_id}`

**Authentication**: Query parameter-based (user_id)

**Connection Flow**:
1. Lines 312-329: User authentication and info preparation
2. Lines 332-335: Initial data fetching (quota, pending count)
3. Lines 339: Close database session before entering message loop
4. Lines 344: Connect to WebSocket manager
5. Lines 347-351: Send initial data
6. Lines 354-379: Message loop with ping/pong keepalive

**Initial Data Sent** (Lines 347-351):
```json
{
  "type": "initial_data",
  "quota": {
    "requests_allowed": 5,
    "requests_used": 2,
    "requests_remaining": 3,
    "current_month": "2025-11",
    "chapters_allowed_per_book_request": 10
  },
  "pending_count": 3  // Only for admins (creators/team)
}
```

**Message Handling**: Lines 393-431 (`handle_book_request_websocket_message`)

---

## Connection Management

### User Connections (Lines 117-119)

```python
# User ID -> Set of WebSocket connections
self.user_connections: Dict[int, Set[WebSocket]] = {}

# WebSocket -> User info for cleanup
self.connection_users: Dict[WebSocket, dict] = {}
```

**Stored User Info** (Lines 322-329):
```python
user_info = {
    'user_id': user.id,
    'username': user.username,
    'is_creator': user.is_creator,
    'is_team': user.is_team,
    'created_by': user.created_by
}
```

### Admin Connections (Lines 120-121)

```python
# Admin connections (creators and team members)
self.admin_connections: Dict[int, Set[WebSocket]] = {}
```

**Admin Registration Logic** (Lines 139-148):
- If user is creator: `creator_id = user_id`
- If user is team member: `creator_id = user.created_by`
- Admin connections are keyed by `creator_id`
- Allows one creator to receive notifications from all their team members' actions

### Connection Method (Lines 127-156)

```python
async def connect(self, websocket: WebSocket, user_id: int, user_info: dict):
    await websocket.accept()

    # Add to user's connections
    if user_id not in self.user_connections:
        self.user_connections[user_id] = set()
    self.user_connections[user_id].add(websocket)

    # Store user info
    self.connection_users[websocket] = user_info

    # If admin, add to admin connections
    if user_info.get('is_creator') or user_info.get('is_team'):
        creator_id = user_id if user_info.get('is_creator') else user_info.get('created_by')
        if creator_id:
            if creator_id not in self.admin_connections:
                self.admin_connections[creator_id] = set()
            self.admin_connections[creator_id].add(websocket)
```

### Disconnect Method (Lines 158-178)

- Removes from `user_connections`
- Removes from `admin_connections` if applicable
- Cleans up `connection_users` mapping

---

## Message Formats

### 1. Connection Confirmation (Lines 153-156)

```json
{
  "type": "connected",
  "message": "Connected to book requests live updates"
}
```

### 2. Initial Data (Lines 347-351)

```json
{
  "type": "initial_data",
  "quota": {
    "requests_allowed": 5,
    "requests_used": 2,
    "requests_remaining": 3,
    "current_month": "2025-11",
    "chapters_allowed_per_book_request": 10
  },
  "pending_count": 3
}
```

### 3. Book Request Update (Lines 232-237)

**Sent on**: Create, status change, fulfill, reply

```json
{
  "type": "book_request_update",
  "book_request": {
    "id": 123,
    "user_id": 456,
    "title": "Book Title",
    "author": "Author Name",
    "link": "https://...",
    "description": "Description",
    "status": "pending",
    "created_at": "2025-11-05T10:00:00",
    "updated_at": "2025-11-05T10:00:00",
    "responded_by_id": null,
    "response_message": null,
    "response_date": null,
    "month_year": "2025-11",
    "accepted_by_id": null,
    "accepted_at": null,
    "user_reply": null,
    "responder": {
      "id": 789,
      "username": "admin_user"
    }
  },
  "action": "created",  // or "status_changed", "reply_added"
  "timestamp": "2025-11-05T10:00:00"
}
```

### 4. Quota Update (Lines 1000-1004)

**Sent after**: Request creation, rejection (refund)

```json
{
  "type": "quota_update",
  "quota": {
    "requests_allowed": 5,
    "requests_used": 3,
    "requests_remaining": 2,
    "current_month": "2025-11",
    "chapters_allowed_per_book_request": 10
  }
}
```

### 5. Pending Count Update (Lines 1548-1551)

**Sent to**: Admins only

```json
{
  "type": "pending_count_update",
  "pending_count": 5
}
```

### 6. Client Messages

**Refresh Quota** (Lines 403-415):
```json
{
  "type": "refresh_quota"
}
```

**Refresh Pending Count** (Lines 417-431):
```json
{
  "type": "refresh_pending"
}
```

**Ping/Pong** (Lines 359-360):
- Client sends: `"ping"`
- Server responds: `"pong"`

---

## Notification Logic

### Who Gets Notified When

#### 1. Request Creation (Lines 882-1034)

**Endpoint**: `POST /api/book-requests/`

**Notifications**:
- **User** (Line 991): Gets the created request via `book_request_update`
- **Admins** (Line 992): All admins under `creator_id` get the new request
- **User** (Lines 1010-1016): Gets quota update

**Code** (Lines 987-993):
```python
await book_request_ws_manager.broadcast_book_request_update(
    book_request=book_request_dict,
    action="created",
    user_id=current_user.id,
    creator_id=creator_id
)
```

#### 2. Status Change (Admin Response) (Lines 1319-1602)

**Endpoint**: `POST /api/book-requests/{request_id}/respond`

**Valid Statuses**: `pending`, `approved`, `rejected`, `fulfilled`

**Notifications**:
- **User** (Line 1540): Gets the status change via `book_request_update`
- **Admins** (Line 1541): All admins get the update
- **User** (Lines 864-874): Gets standard notification
- **Admins** (Lines 1545-1564): Get pending count update
- **User** (Lines 1566-1588): Gets quota update if rejected (refund)

**Code** (Lines 1536-1542):
```python
await book_request_ws_manager.broadcast_book_request_update(
    book_request=book_request_dict,
    action="status_changed",
    user_id=book_request.user_id,
    creator_id=creator_id
)
```

**Refund Logic** (Lines 1432-1496):
- Only on `rejected` status
- Uses distributed lock to prevent race conditions
- Decrements `book_requests_used` counter
- Sends quota update to user

#### 3. User Reply (Lines 2140-2263)

**Endpoint**: `POST /api/book-requests/{request_id}/reply`

**Notifications**:
- **Admin** (Lines 2198-2211): Original responder gets notification
- **User** (Line 2248): Gets own reply confirmation via `book_request_update`
- **Admins** (Line 2249): All admins get the reply update

**Code** (Lines 2244-2250):
```python
await book_request_ws_manager.broadcast_book_request_update(
    book_request=book_request_dict,
    action="reply_added",
    user_id=current_user.id,
    creator_id=creator_id
)
```

#### 4. Accept Request (Lines 1832-1884)

**Endpoint**: `POST /api/book-requests/{request_id}/accept`

**Notifications**:
- **User** (Lines 1859-1864): Gets notification that request was accepted
- No WebSocket broadcast (uses standard notification system only)

#### 5. Fulfill Request (Lines 1887-2028)

**Endpoint**: `POST /api/book-requests/{request_id}/fulfill`

**Notifications**:
- **User** (Lines 1978-1983): Gets notification that request was fulfilled
- No WebSocket broadcast (uses standard notification system only)

### Redis Pub/Sub Integration

**Channel**: `"book_request:notifications"` (Line 125)

**Broadcast Method** (Lines 230-260):
```python
async def broadcast_book_request_update(self, book_request, action, user_id=None, creator_id=None):
    message = {
        "type": "book_request_update",
        "book_request": book_request,
        "action": action,
        "timestamp": datetime.utcnow().isoformat()
    }

    # ✅ ALWAYS broadcast to local connections first
    if user_id:
        await self.send_to_user(user_id, message)

    if creator_id:
        await self.send_to_admins(creator_id, message)

    # ✅ Also publish to Redis (for multi-container environments)
    redis_message = {
        "user_id": user_id,
        "creator_id": creator_id,
        "payload": message
    }
    redis_client.publish(self.redis_channel, json.dumps(redis_message))
```

**Redis Listener** (Lines 273-297):
- Subscribes to channel on startup (Line 708 in app.py)
- Forwards messages to local connections
- Handles both user-targeted and admin-targeted messages

---

## API Endpoints

### 1. Create Book Request

**Method**: `POST`
**Path**: `/api/book-requests/`
**Lines**: 882-1034
**Auth**: `@login_required`

**Form Parameters**:
- `title` (required)
- `author` (required)
- `link` (optional)
- `description` (optional)

**Logic**:
1. Check quota (Lines 895-905)
2. Insert request using raw SQL (Lines 910-935)
3. Increment usage counter (Lines 938-955)
4. Broadcast via WebSocket (Lines 987-993)
5. Send quota update (Lines 996-1016)

**WebSocket Notifications**:
- User: `book_request_update` (action: "created")
- Admins: `book_request_update` (action: "created")
- User: `quota_update`

---

### 2. Get Quota

**Method**: `GET`
**Path**: `/api/book-requests/quota`
**Lines**: 1038-1049
**Auth**: `@login_required`

**Returns**: Quota object

---

### 3. Get User's Requests

**Method**: `GET`
**Path**: `/api/book-requests/`
**Lines**: 1052-1130
**Auth**: `@login_required`

**Query Parameters**:
- `status` (optional): Filter by status
- `month_year` (optional): Filter by month (format: YYYY-MM)

**Returns**: List of user's book requests + quota

---

### 4. Get All Requests (Admin)

**Method**: `GET`
**Path**: `/api/book-requests/admin`
**Lines**: 1133-1316
**Auth**: `@login_required` + `@verify_role_permission(["creator", "team"])`

**Query Parameters**:
- `status` (optional)
- `user_id` (optional)
- `month_year` (optional)

**Returns**: All requests for creator's users + metadata

---

### 5. Respond to Request (Admin)

**Method**: `POST`
**Path**: `/api/book-requests/{request_id}/respond`
**Lines**: 1319-1602
**Auth**: `@login_required` + `@verify_role_permission(["creator", "team"])`

**Form Parameters**:
- `status` (required): `pending`, `approved`, `rejected`, `fulfilled`
- `response_message` (optional)

**Logic**:
1. Validate status (Lines 1337-1343)
2. Check permissions (Lines 1360-1374)
3. Update request (Lines 1380-1398)
4. Log activity (Lines 1401-1418)
5. Send notification (Lines 1421-1430)
6. Process refund if rejected (Lines 1432-1496)
7. Broadcast via WebSocket (Lines 1536-1542)
8. Update pending count for admins (Lines 1545-1564)
9. Update quota if refunded (Lines 1566-1588)

**WebSocket Notifications**:
- User: Standard notification + `book_request_update` (action: "status_changed")
- Admins: `book_request_update` (action: "status_changed") + `pending_count_update`
- User (if rejected): `quota_update`

---

### 6. Accept Request (Admin)

**Method**: `POST`
**Path**: `/api/book-requests/{request_id}/accept`
**Lines**: 1832-1884
**Auth**: `@login_required` + `@verify_role_permission(["creator", "team"])`

**Logic**:
1. Check if already accepted (Lines 1849-1853)
2. Set `accepted_by_id` and `accepted_at` (Lines 1856-1858)
3. Send notification (Lines 1859-1864)

**Notifications**: Standard notification only (no WebSocket broadcast)

---

### 7. Fulfill Request (Admin)

**Method**: `POST`
**Path**: `/api/book-requests/{request_id}/fulfill`
**Lines**: 1887-2028
**Auth**: `@login_required` + `@verify_role_permission(["creator", "team"])`

**Logic**:
1. Validate status is "approved" (Lines 1919-1925)
2. Update status to "fulfilled" (Lines 1948-1958)
3. Log activity (Lines 1961-1976)
4. Send notification (Lines 1978-1983)

**Notifications**: Standard notification only (no WebSocket broadcast)

---

### 8. Reply to Admin Response

**Method**: `POST`
**Path**: `/api/book-requests/{request_id}/reply`
**Lines**: 2140-2263
**Auth**: `@login_required`

**Form Parameters**:
- `user_reply` (required)

**Validation** (Lines 2169-2179):
- User must own the request
- Admin must have responded first
- Request cannot be rejected
- User cannot have already replied

**Logic**:
1. Validate (Lines 2169-2179)
2. Update `user_reply` field (Lines 2184-2195)
3. Send notification to admin (Lines 2198-2213)
4. Broadcast via WebSocket (Lines 2244-2250)

**WebSocket Notifications**:
- Admin: Standard notification
- User: `book_request_update` (action: "reply_added")
- Admins: `book_request_update` (action: "reply_added")

---

### 9. Update Book Request Settings (Admin)

**Method**: `POST`
**Path**: `/api/book-requests/settings`
**Lines**: 1723-1829
**Auth**: `@login_required` + `@verify_role_permission(["creator"])`

**JSON Body**:
```json
{
  "tier_id": "Tier Name",
  "book_requests_allowed": 5
}
```

**Logic**: Updates `CampaignTier` and all users in that tier

---

### 10. Update Chapters Settings (Admin)

**Method**: `POST`
**Path**: `/api/book-requests/chapters-settings`
**Lines**: 2030-2138
**Auth**: `@login_required` + `@verify_role_permission(["creator"])`

**JSON Body**:
```json
{
  "tier_id": "Tier Name",
  "chapters_allowed_per_book_request": 10
}
```

**Logic**: Updates `CampaignTier` and all users in that tier

---

## Migration Checklist

### Pre-Migration Analysis
- [x] Document current WebSocket endpoints
- [x] Document message formats
- [x] Document notification logic
- [x] Identify all API endpoints that trigger WebSocket messages
- [x] Map user/admin targeting requirements
- [ ] Review frontend code that connects to WebSocket
- [ ] Identify any custom Redis pub/sub logic that differs from centralized manager

### Code Changes

#### Phase 1: Setup (Lines to Change)
- [ ] **Line 45**: Remove `BookRequestWebSocketManager` class definition (Lines 114-299)
- [ ] **Line 47**: Import centralized `WebSocketManager`
  ```python
  from websocket_manager import WebSocketManager
  ```
- [ ] **Line 300**: Replace manager instantiation
  ```python
  # OLD:
  book_request_ws_manager = BookRequestWebSocketManager()

  # NEW:
  book_request_ws_manager = WebSocketManager(channel="book_requests")
  ```
- [ ] **app.py Line 45**: Update import
  ```python
  # Remove: from book_request import book_request_ws_manager
  # Add: from websocket_manager import WebSocketManager
  ```
- [ ] **app.py Line 708**: Remove Redis subscriber startup
  ```python
  # REMOVE:
  await book_request_ws_manager.start_redis_subscriber()
  ```

#### Phase 2: WebSocket Endpoint (Lines 303-391)
- [ ] **Line 344**: Update `connect` method signature
  ```python
  # OLD:
  await book_request_ws_manager.connect(websocket, user_info['user_id'], user_info)

  # NEW:
  await book_request_ws_manager.connect(
      websocket,
      user_id=str(user_info['user_id']),
      **user_info  # Pass as metadata
  )
  ```

- [ ] **Line 347-351**: Update initial data sending
  ```python
  # OLD:
  await websocket.send_json({
      "type": "initial_data",
      "quota": quota,
      "pending_count": pending_count
  })

  # NEW: (same, no change needed)
  await websocket.send_json({
      "type": "initial_data",
      "quota": quota,
      "pending_count": pending_count
  })
  ```

#### Phase 3: Message Broadcasting

**Challenge**: The centralized `WebSocketManager` uses string user IDs, but book requests use integer user IDs.

**Solution**: Convert all user_id parameters to strings when calling WebSocketManager methods.

##### A. Request Creation (Lines 987-1016)

- [ ] **Lines 987-993**: Update `broadcast_book_request_update` calls
  ```python
  # REPLACE broadcast_book_request_update with broadcast

  # OLD:
  await book_request_ws_manager.broadcast_book_request_update(
      book_request=book_request_dict,
      action="created",
      user_id=current_user.id,
      creator_id=creator_id
  )

  # NEW:
  message = {
      "type": "book_request_update",
      "book_request": book_request_dict,
      "action": "created",
      "timestamp": datetime.now(timezone.utc).isoformat()
  }

  # Send to user and admins
  target_users = {str(current_user.id)}

  # Add all admins under this creator
  # NOTE: Need to track admin users separately or send to all admins
  await book_request_ws_manager.broadcast(message, target_user_ids=target_users)

  # For admin notifications, we need a separate admin channel or modify approach
  ```

**CRITICAL ISSUE IDENTIFIED**: The centralized `WebSocketManager` doesn't have the concept of "admin connections" grouped by creator_id.

**Solutions**:
1. **Option A**: Create separate managers for users and admins
2. **Option B**: Extend `WebSocketManager` with admin grouping
3. **Option C**: Track admin user IDs in book_request.py and send to all admins individually

**Recommended**: **Option C** - Track admin user IDs and use targeted broadcasting

- [ ] **Lines 987-1016**: Implement admin tracking and targeted broadcasting
  ```python
  # Add at module level:
  _admin_user_cache: Dict[int, Set[int]] = {}  # creator_id -> set of admin user_ids

  async def get_admin_user_ids(creator_id: int, db: Session) -> Set[str]:
      """Get all admin user IDs for a creator (cached)"""
      if creator_id not in _admin_user_cache:
          admin_users = db.query(User.id).filter(
              or_(
                  User.id == creator_id,
                  and_(User.created_by == creator_id, User.is_team == True)
              ),
              User.is_active == True
          ).all()
          _admin_user_cache[creator_id] = {str(u.id) for u in admin_users}

      return _admin_user_cache[creator_id]

  # In broadcast code:
  message = {
      "type": "book_request_update",
      "book_request": book_request_dict,
      "action": "created",
      "timestamp": datetime.now(timezone.utc).isoformat()
  }

  # Get all target users (requestor + admins)
  admin_ids = await get_admin_user_ids(creator_id, db)
  target_users = admin_ids | {str(current_user.id)}

  await book_request_ws_manager.broadcast(message, target_user_ids=target_users)
  ```

##### B. Quota Updates (Lines 1000-1016)

- [ ] **Lines 1010-1016**: Replace with centralized manager
  ```python
  # OLD:
  await book_request_ws_manager.send_to_user(
      current_user.id,
      {
          "type": "quota_update",
          "quota": updated_quota
      }
  )

  # NEW:
  await book_request_ws_manager.send_to_user(
      str(current_user.id),
      {
          "type": "quota_update",
          "quota": updated_quota
      }
  )
  ```

##### C. Status Changes (Lines 1536-1564)

- [ ] **Lines 1536-1542**: Update broadcast
  ```python
  # OLD:
  await book_request_ws_manager.broadcast_book_request_update(
      book_request=book_request_dict,
      action="status_changed",
      user_id=book_request.user_id,
      creator_id=creator_id
  )

  # NEW:
  message = {
      "type": "book_request_update",
      "book_request": book_request_dict,
      "action": "status_changed",
      "timestamp": datetime.now(timezone.utc).isoformat()
  }

  admin_ids = await get_admin_user_ids(creator_id, db)
  target_users = admin_ids | {str(book_request.user_id)}

  await book_request_ws_manager.broadcast(message, target_user_ids=target_users)
  ```

- [ ] **Lines 1548-1564**: Update pending count broadcast
  ```python
  # OLD:
  pending_count_message = {
      "type": "pending_count_update",
      "pending_count": pending_count
  }
  await book_request_ws_manager.send_to_admins(creator_id, pending_count_message)

  # NEW:
  message = {
      "type": "pending_count_update",
      "pending_count": pending_count
  }

  admin_ids = await get_admin_user_ids(creator_id, db)
  await book_request_ws_manager.broadcast(message, target_user_ids=admin_ids)
  ```

##### D. User Reply (Lines 2244-2250)

- [ ] **Lines 2244-2250**: Update broadcast
  ```python
  # OLD:
  await book_request_ws_manager.broadcast_book_request_update(
      book_request=book_request_dict,
      action="reply_added",
      user_id=current_user.id,
      creator_id=creator_id
  )

  # NEW:
  message = {
      "type": "book_request_update",
      "book_request": book_request_dict,
      "action": "reply_added",
      "timestamp": datetime.now(timezone.utc).isoformat()
  }

  admin_ids = await get_admin_user_ids(creator_id, db)
  target_users = admin_ids | {str(current_user.id)}

  await book_request_ws_manager.broadcast(message, target_user_ids=target_users)
  ```

#### Phase 4: Message Handling (Lines 393-431)

- [ ] **Lines 403-415**: Update `refresh_quota` handler
  ```python
  # No changes needed - still uses websocket.send_json directly
  ```

- [ ] **Lines 417-431**: Update `refresh_pending` handler
  ```python
  # No changes needed - still uses websocket.send_json directly
  ```

#### Phase 5: Cleanup

- [ ] Remove all Redis pub/sub code specific to book requests
  - Lines 122-125: Redis pub/sub variables
  - Lines 262-297: Redis listener methods

- [ ] Update any direct calls to `send_to_user` or `send_to_admins`
  ```python
  # Search for: book_request_ws_manager.send_to_user
  # Replace with: book_request_ws_manager.send_to_user (with str user_id)

  # Search for: book_request_ws_manager.send_to_admins
  # Replace with: admin targeting logic
  ```

- [ ] Remove `BookRequestWebSocketManager` class entirely (Lines 114-299)

### New Code Required

#### 1. Admin User ID Cache (Add after imports)

```python
# Add after Line 43 (after redis_client import)

# Cache for admin user IDs to avoid repeated DB queries
_admin_user_cache: Dict[int, Set[str]] = {}  # creator_id -> set of admin user_ids (as strings)
_cache_lock = asyncio.Lock()

async def get_admin_user_ids(creator_id: int, db: Session) -> Set[str]:
    """
    Get all admin user IDs (creator + team members) for a creator.
    Results are cached to avoid repeated DB queries.

    Args:
        creator_id: The creator's user ID
        db: Database session

    Returns:
        Set of admin user IDs as strings (for WebSocketManager compatibility)
    """
    async with _cache_lock:
        if creator_id not in _admin_user_cache:
            try:
                admin_users = db.query(User.id).filter(
                    or_(
                        User.id == creator_id,
                        and_(
                            User.created_by == creator_id,
                            User.is_team == True
                        )
                    ),
                    User.is_active == True
                ).all()

                _admin_user_cache[creator_id] = {str(u.id) for u in admin_users}
                logger.info(f"Cached {len(_admin_user_cache[creator_id])} admin user IDs for creator {creator_id}")

            except Exception as e:
                logger.error(f"Error fetching admin user IDs for creator {creator_id}: {e}")
                return set()

        return _admin_user_cache[creator_id]

def invalidate_admin_cache(creator_id: int = None):
    """
    Invalidate admin user cache.

    Args:
        creator_id: If provided, only invalidate for this creator. Otherwise clear all.
    """
    if creator_id:
        _admin_user_cache.pop(creator_id, None)
        logger.info(f"Invalidated admin cache for creator {creator_id}")
    else:
        _admin_user_cache.clear()
        logger.info("Cleared entire admin cache")
```

#### 2. Helper Function for Broadcasting (Add before API endpoints)

```python
# Add after get_admin_user_ids function

async def broadcast_book_request_update(
    book_request_dict: dict,
    action: str,
    user_id: int,
    creator_id: int,
    db: Session
):
    """
    Broadcast book request update to user and admins using centralized WebSocketManager.

    Args:
        book_request_dict: Serialized book request data
        action: Action type ("created", "status_changed", "reply_added")
        user_id: Requesting user's ID
        creator_id: Creator's ID (for admin notifications)
        db: Database session
    """
    message = {
        "type": "book_request_update",
        "book_request": book_request_dict,
        "action": action,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    # Get all admin user IDs
    admin_ids = await get_admin_user_ids(creator_id, db)

    # Target: user who made the request + all admins
    target_users = admin_ids | {str(user_id)}

    # Broadcast using centralized manager
    await book_request_ws_manager.broadcast(message, target_user_ids=target_users)

    logger.info(
        f"Broadcast book request update: action={action}, "
        f"user_id={user_id}, creator_id={creator_id}, "
        f"targets={len(target_users)}"
    )

async def broadcast_pending_count_update(
    creator_id: int,
    pending_count: int,
    db: Session
):
    """
    Broadcast pending count update to all admins.

    Args:
        creator_id: Creator's ID
        pending_count: Number of pending requests
        db: Database session
    """
    message = {
        "type": "pending_count_update",
        "pending_count": pending_count
    }

    # Get all admin user IDs
    admin_ids = await get_admin_user_ids(creator_id, db)

    # Broadcast to admins only
    await book_request_ws_manager.broadcast(message, target_user_ids=admin_ids)

    logger.info(
        f"Broadcast pending count update: creator_id={creator_id}, "
        f"pending_count={pending_count}, targets={len(admin_ids)}"
    )
```

### Specific Line Changes Summary

| Line Range | Old Code | New Code | Reason |
|------------|----------|----------|--------|
| 114-299 | `BookRequestWebSocketManager` class | **DELETE** | Replaced by centralized manager |
| 45 | Import statement | `from websocket_manager import WebSocketManager` | Use centralized manager |
| 300 | `book_request_ws_manager = BookRequestWebSocketManager()` | `book_request_ws_manager = WebSocketManager(channel="book_requests")` | Use centralized manager |
| 344 | `connect(websocket, user_id, user_info)` | `connect(websocket, user_id=str(user_id), **user_info)` | String user_id + metadata |
| 987-993 | `broadcast_book_request_update(...)` | `await broadcast_book_request_update(book_request_dict, "created", current_user.id, creator_id, db)` | Use new helper |
| 1010-1016 | `send_to_user(current_user.id, {...})` | `send_to_user(str(current_user.id), {...})` | String user_id |
| 1536-1542 | `broadcast_book_request_update(...)` | `await broadcast_book_request_update(book_request_dict, "status_changed", book_request.user_id, creator_id, db)` | Use new helper |
| 1548-1564 | `send_to_admins(creator_id, {...})` + Redis | `await broadcast_pending_count_update(creator_id, pending_count, db)` | Use new helper |
| 1585 | `send_to_user(book_request.user_id, {...})` | `send_to_user(str(book_request.user_id), {...})` | String user_id |
| 2244-2250 | `broadcast_book_request_update(...)` | `await broadcast_book_request_update(book_request_dict, "reply_added", current_user.id, creator_id, db)` | Use new helper |
| app.py:45 | Import `book_request_ws_manager` | Update to import from `websocket_manager` | Module change |
| app.py:708 | `await book_request_ws_manager.start_redis_subscriber()` | **DELETE** | Centralized manager handles Redis |

---

## Testing Plan

### Unit Tests

#### 1. WebSocket Connection Tests
- [ ] Test user connection (regular user)
- [ ] Test admin connection (creator)
- [ ] Test admin connection (team member)
- [ ] Test connection with invalid user_id
- [ ] Test disconnect cleanup

#### 2. Message Broadcasting Tests
- [ ] Test broadcast to single user
- [ ] Test broadcast to admins only
- [ ] Test broadcast to user + admins
- [ ] Test targeted messaging with user_id set
- [ ] Test message format preservation

#### 3. Admin Caching Tests
- [ ] Test admin user ID cache population
- [ ] Test cache invalidation
- [ ] Test cache with creator only
- [ ] Test cache with creator + team members
- [ ] Test cache update when team members added/removed

### Integration Tests

#### 1. Request Creation Flow
- [ ] User creates request
- [ ] Verify user receives `book_request_update` (action: "created")
- [ ] Verify admins receive `book_request_update` (action: "created")
- [ ] Verify user receives `quota_update`
- [ ] Verify quota reflects new usage

#### 2. Status Change Flow
- [ ] Admin approves request
- [ ] Verify user receives notification
- [ ] Verify user receives `book_request_update` (action: "status_changed")
- [ ] Verify admins receive `book_request_update`
- [ ] Verify admins receive `pending_count_update`

#### 3. Rejection + Refund Flow
- [ ] Admin rejects request
- [ ] Verify refund is processed
- [ ] Verify user receives `quota_update` with incremented remaining count
- [ ] Verify `book_requests_used` counter is decremented

#### 4. User Reply Flow
- [ ] User replies to admin response
- [ ] Verify original admin receives notification
- [ ] Verify user receives `book_request_update` (action: "reply_added")
- [ ] Verify all admins receive `book_request_update`

#### 5. Multi-Container Tests
- [ ] Start two instances of the application
- [ ] Connect users to different instances
- [ ] Create request on instance 1
- [ ] Verify admins on instance 2 receive update
- [ ] Change status on instance 2
- [ ] Verify user on instance 1 receives update

### Load Tests

#### 1. Concurrent Connections
- [ ] Test 100 concurrent WebSocket connections
- [ ] Test 1000 concurrent WebSocket connections
- [ ] Measure connection overhead
- [ ] Measure memory usage

#### 2. Message Broadcasting
- [ ] Broadcast to 100 users
- [ ] Broadcast to 1000 users
- [ ] Measure latency
- [ ] Verify all users receive message

#### 3. Cache Performance
- [ ] Test admin cache with 10 creators
- [ ] Test admin cache with 100 team members
- [ ] Measure cache hit rate
- [ ] Measure query reduction

### Manual Testing Checklist

#### User Perspective
- [ ] Connect to WebSocket
- [ ] Receive initial data (quota, pending count if admin)
- [ ] Create new book request
- [ ] Verify real-time update appears
- [ ] Verify quota updates immediately
- [ ] Reply to admin response
- [ ] Verify reply appears in UI
- [ ] Receive notification when admin responds

#### Admin Perspective
- [ ] Connect to WebSocket as creator
- [ ] Connect to WebSocket as team member
- [ ] Verify initial pending count
- [ ] Receive notification when user creates request
- [ ] Verify pending count updates
- [ ] Approve/reject request
- [ ] Verify user receives update
- [ ] Verify pending count decreases
- [ ] Receive notification when user replies

#### Multi-Tab Testing
- [ ] Open user tab
- [ ] Open admin tab (same browser)
- [ ] Create request in user tab
- [ ] Verify appears in admin tab
- [ ] Respond in admin tab
- [ ] Verify appears in user tab

### Rollback Plan

If migration fails:

1. **Immediate Rollback**:
   - Revert to previous commit
   - Restore `BookRequestWebSocketManager` class
   - Restore Redis subscriber startup in app.py

2. **Partial Rollback**:
   - Keep centralized manager for new features
   - Maintain custom manager for book requests
   - Document why rollback was necessary

3. **Data Integrity**:
   - Verify no database changes were made
   - Verify quota counters are accurate
   - Verify no orphaned connections

---

## Summary

### Current Architecture
- **Custom Manager**: `BookRequestWebSocketManager` with dual connection tracking
- **Connection Types**: User connections + admin connections (grouped by creator_id)
- **Redis Integration**: Custom pub/sub for cross-container support
- **Message Types**: 6 types (connected, initial_data, book_request_update, quota_update, pending_count_update, client messages)
- **Notification Targets**: User-specific and admin-specific (grouped by creator)

### Migration Strategy
1. Replace custom manager with centralized `WebSocketManager`
2. Add admin user ID caching layer
3. Create helper functions for targeted broadcasting
4. Convert all user IDs to strings for compatibility
5. Remove custom Redis pub/sub code

### Key Challenges
1. **Admin Grouping**: Centralized manager doesn't have creator-based grouping
   - **Solution**: Track admin user IDs and use targeted broadcasting
2. **User ID Types**: Centralized manager uses strings, book requests use integers
   - **Solution**: Convert to strings when calling manager methods
3. **Dual Notification System**: Some endpoints use WebSocket + standard notifications
   - **Solution**: Keep both systems, migrate only WebSocket part

### Benefits of Migration
- Unified WebSocket infrastructure
- Reduced code duplication
- Better Redis connection management
- Standardized message format
- Easier debugging and monitoring
- Future-proof for new features

### Risks
- Potential performance impact from admin user lookups
- Cache invalidation complexity
- Testing complexity for multi-container scenarios
- Backward compatibility with frontend code

---

## Appendix: Code Snippets

### Complete Helper Functions

```python
# Add after imports in book_request.py

from websocket_manager import WebSocketManager
from typing import Dict, Set
import asyncio

# Replace custom manager instantiation
book_request_ws_manager = WebSocketManager(channel="book_requests")

# Admin user cache
_admin_user_cache: Dict[int, Set[str]] = {}
_cache_lock = asyncio.Lock()

async def get_admin_user_ids(creator_id: int, db: Session) -> Set[str]:
    """Get all admin user IDs for a creator (cached)"""
    async with _cache_lock:
        if creator_id not in _admin_user_cache:
            try:
                from sqlalchemy import or_, and_

                admin_users = db.query(User.id).filter(
                    or_(
                        User.id == creator_id,
                        and_(User.created_by == creator_id, User.is_team == True)
                    ),
                    User.is_active == True
                ).all()

                _admin_user_cache[creator_id] = {str(u.id) for u in admin_users}
                logger.info(f"Cached {len(_admin_user_cache[creator_id])} admin IDs for creator {creator_id}")

            except Exception as e:
                logger.error(f"Error fetching admin IDs: {e}")
                return set()

        return _admin_user_cache[creator_id]

def invalidate_admin_cache(creator_id: int = None):
    """Invalidate admin user cache"""
    if creator_id:
        _admin_user_cache.pop(creator_id, None)
    else:
        _admin_user_cache.clear()

async def broadcast_book_request_update(
    book_request_dict: dict,
    action: str,
    user_id: int,
    creator_id: int,
    db: Session
):
    """Broadcast book request update using centralized manager"""
    message = {
        "type": "book_request_update",
        "book_request": book_request_dict,
        "action": action,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    admin_ids = await get_admin_user_ids(creator_id, db)
    target_users = admin_ids | {str(user_id)}

    await book_request_ws_manager.broadcast(message, target_user_ids=target_users)

    logger.info(f"Broadcast: action={action}, user={user_id}, creator={creator_id}, targets={len(target_users)}")

async def broadcast_pending_count_update(
    creator_id: int,
    pending_count: int,
    db: Session
):
    """Broadcast pending count to admins"""
    message = {
        "type": "pending_count_update",
        "pending_count": pending_count
    }

    admin_ids = await get_admin_user_ids(creator_id, db)
    await book_request_ws_manager.broadcast(message, target_user_ids=admin_ids)

    logger.info(f"Pending count broadcast: creator={creator_id}, count={pending_count}, targets={len(admin_ids)}")
```

### WebSocket Endpoint Migration

```python
# OLD (Lines 303-391)
@book_request_router.websocket("/ws")
async def book_request_websocket(
    websocket: WebSocket,
    user_id: int = Query(..., description="User ID for authentication")
):
    from database import SessionLocal
    db = SessionLocal()
    user_info = None

    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            await websocket.close(code=1008, reason="User not found")
            return

        user_info = {
            'user_id': user.id,
            'username': user.username,
            'is_creator': user.is_creator,
            'is_team': user.is_team,
            'created_by': user.created_by
        }

        quota = await get_user_book_request_quota(user, db)
        pending_count = 0
        if user.is_creator or user.is_team:
            pending_count = await get_pending_book_request_count(user, db)

    finally:
        db.close()

    try:
        await book_request_ws_manager.connect(websocket, user_info['user_id'], user_info)

        await websocket.send_json({
            "type": "initial_data",
            "quota": quota,
            "pending_count": pending_count
        })

        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                if data == "ping":
                    await websocket.send_text("pong")
                else:
                    try:
                        message = json.loads(data)
                        await handle_book_request_websocket_message(websocket, user_info, message)
                    except json.JSONDecodeError:
                        pass
            except asyncio.TimeoutError:
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
        logger.info(f"WebSocket disconnected: {user_info['username'] if user_info else 'unknown'}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        try:
            await websocket.close(code=1011, reason="Internal error")
        except:
            pass

    finally:
        book_request_ws_manager.disconnect(websocket)

# NEW
@book_request_router.websocket("/ws")
async def book_request_websocket(
    websocket: WebSocket,
    user_id: int = Query(..., description="User ID for authentication")
):
    from database import SessionLocal
    db = SessionLocal()
    user_info = None

    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            await websocket.close(code=1008, reason="User not found")
            return

        user_info = {
            'user_id': user.id,
            'username': user.username,
            'is_creator': user.is_creator,
            'is_team': user.is_team,
            'created_by': user.created_by
        }

        quota = await get_user_book_request_quota(user, db)
        pending_count = 0
        if user.is_creator or user.is_team:
            pending_count = await get_pending_book_request_count(user, db)

    finally:
        db.close()

    try:
        # ✅ CHANGED: Use string user_id and pass metadata
        await book_request_ws_manager.connect(
            websocket,
            user_id=str(user_info['user_id']),
            **user_info  # Pass as metadata
        )

        await websocket.send_json({
            "type": "initial_data",
            "quota": quota,
            "pending_count": pending_count
        })

        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                if data == "ping":
                    await websocket.send_text("pong")
                else:
                    try:
                        message = json.loads(data)
                        await handle_book_request_websocket_message(websocket, user_info, message)
                    except json.JSONDecodeError:
                        pass
            except asyncio.TimeoutError:
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
        logger.info(f"WebSocket disconnected: {user_info['username'] if user_info else 'unknown'}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        try:
            await websocket.close(code=1011, reason="Internal error")
        except:
            pass

    finally:
        book_request_ws_manager.disconnect(websocket)
```

---

**End of Analysis**
