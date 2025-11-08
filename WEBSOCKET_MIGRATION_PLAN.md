# WebSocket Migration Plan - Multi-Replica Support

## Executive Summary

**Goal**: Migrate all WebSocket systems to use centralized `WebSocketManager` with Redis pub/sub for multi-replica support while maintaining single-instance backwards compatibility.

**Timeline**: Phased migration with testing at each stage

**Risk Mitigation**:
- WebSocketManager falls back to single-replica mode if Redis unavailable ✅
- Existing code continues to work as-is until migration ✅
- Can test each feature independently ✅

---

## Current State Analysis

### WebSocket Features to Migrate

1. **Broadcasts** (`broadcast_router.py`)
   - Current: `BroadcastWebSocketManager` (in-memory only)
   - Endpoint: `/ws/broadcasts`
   - Users: All authenticated users
   - Messages: New broadcasts, updates, deletions

2. **Track Comments** (`comment_routes.py`)
   - Current: WebSocket connections per track
   - Endpoint: `/ws/comments/{track_id}` (assumed)
   - Users: Users viewing specific tracks
   - Messages: New comments, edits, deletions

3. **Forum** (`forum_routes.py`)
   - Current: WebSocket for forum threads
   - Endpoint: `/ws/forum/{thread_id}` (assumed)
   - Users: Users viewing forum threads
   - Messages: New posts, replies, updates

4. **Book Requests** (`book_request.py`)
   - Current: WebSocket for book request updates
   - Endpoint: `/ws/book-requests` (assumed)
   - Users: Users tracking their requests
   - Messages: Status updates, new requests

5. **Manage Book Requests** (admin side)
   - Current: Admin dashboard WebSocket
   - Endpoint: `/ws/book-requests/admin` (assumed)
   - Users: Creators/admins
   - Messages: All book request activity

---

## Migration Strategy

### Phase 1: Foundation & Analysis ✅ DONE
- [x] Create `WebSocketManager` class
- [x] Create migration guide
- [x] Copy to audio-streaming-app1
- [ ] Analyze existing WebSocket implementations

### Phase 2: Core Migrations (Parallel)
Execute these migrations in parallel using sub-agents:

#### Agent 1: Broadcast System
- File: `broadcast_router.py`
- Tasks:
  1. Analyze existing `BroadcastWebSocketManager`
  2. Create new manager: `broadcast_ws_manager = WebSocketManager(channel="broadcasts")`
  3. Update WebSocket endpoint to use new manager
  4. Update broadcast creation to use `broadcast()`
  5. Test single-instance mode
  6. Test multi-replica mode

#### Agent 2: Track Comments
- File: `comment_routes.py`
- Tasks:
  1. Analyze existing WebSocket implementation
  2. Create manager: `comment_ws_manager = WebSocketManager(channel="track_comments")`
  3. Update endpoints (new, edit, delete)
  4. Add track_id to messages for filtering
  5. Test single-instance mode
  6. Test multi-replica mode

#### Agent 3: Forum System
- File: `forum_routes.py`
- Tasks:
  1. Analyze existing WebSocket implementation
  2. Create manager: `forum_ws_manager = WebSocketManager(channel="forum")`
  3. Update endpoints (new posts, replies)
  4. Add thread_id to messages for filtering
  5. Test single-instance mode
  6. Test multi-replica mode

#### Agent 4: Book Requests (User)
- File: `book_request.py`
- Tasks:
  1. Analyze existing WebSocket implementation
  2. Create manager: `book_request_ws_manager = WebSocketManager(channel="book_requests")`
  3. Update endpoints (status updates, new requests)
  4. Use `send_to_user()` for targeted updates
  5. Test single-instance mode
  6. Test multi-replica mode

#### Agent 5: Book Requests (Admin)
- File: Same as Agent 4
- Tasks:
  1. Create manager: `book_request_admin_ws_manager = WebSocketManager(channel="book_requests_admin")`
  2. Update admin endpoints
  3. Broadcast all activity to admins
  4. Test single-instance mode
  5. Test multi-replica mode

### Phase 3: Integration & Testing
- [ ] Add cleanup handlers to `app.py` lifespan
- [ ] Test all features in single-instance mode
- [ ] Test all features in multi-replica mode (3 replicas)
- [ ] Load testing with Redis
- [ ] Failover testing (Redis goes down, comes back up)

### Phase 4: Documentation & Deployment
- [ ] Update API documentation
- [ ] Update deployment documentation
- [ ] Create monitoring/alerting for Redis pub/sub
- [ ] Deploy to staging
- [ ] Deploy to production

---

## Technical Requirements

### Backwards Compatibility

**Single-Instance Mode** (no Redis or Redis down):
```python
# WebSocketManager automatically falls back
if not self._redis_client:
    # Broadcast locally only (works like before)
    await self._broadcast_local(message)
```

**Requirements**:
- ✅ Works without Redis
- ✅ No breaking changes to existing code
- ✅ Graceful degradation

### Multi-Replica Mode

**Requirements**:
- ✅ Messages propagate to all replicas
- ✅ Low latency (<100ms)
- ✅ Handles Redis reconnections
- ✅ No message duplication

---

## Agent Task Assignments

### Agent 1: Analyze Broadcast System
**Input**: `broadcast_router.py`
**Output**:
- Current implementation details
- List of endpoints to update
- Migration code snippets
- Testing checklist

### Agent 2: Analyze Comment System
**Input**: `comment_routes.py`
**Output**:
- Current implementation details
- List of endpoints to update
- Migration code snippets
- Testing checklist

### Agent 3: Analyze Forum System
**Input**: `forum_routes.py`
**Output**:
- Current implementation details
- List of endpoints to update
- Migration code snippets
- Testing checklist

### Agent 4: Analyze Book Request System
**Input**: `book_request.py`
**Output**:
- Current implementation details
- List of endpoints to update (user + admin)
- Migration code snippets
- Testing checklist

---

## Testing Strategy

### Single-Instance Testing
```bash
# Start single instance
uvicorn app:app --port 8000

# Test each WebSocket feature:
# 1. Connect WebSocket client
# 2. Send message via API
# 3. Verify WebSocket receives message
# 4. Verify no errors in logs
```

### Multi-Replica Testing
```bash
# Start 3 replicas
uvicorn app:app --port 8001 &
uvicorn app:app --port 8002 &
uvicorn app:app --port 8003 &

# Test each WebSocket feature:
# 1. Connect client A to replica 1
# 2. Connect client B to replica 2
# 3. Connect client C to replica 3
# 4. Send message via replica 1 API
# 5. Verify ALL clients receive message
# 6. Check Redis pub/sub stats
```

### Failover Testing
```bash
# Test Redis failure
# 1. Start replicas with Redis
# 2. Connect clients
# 3. Stop Redis
# 4. Send message (should still work locally)
# 5. Start Redis
# 6. Verify system recovers
```

---

## Rollback Plan

If issues occur:

1. **Immediate Rollback**: Keep old code commented out, can revert quickly
2. **Feature Flag**: Add environment variable to enable/disable new manager
3. **Gradual Migration**: Migrate one feature at a time, not all at once

```python
# Feature flag approach
USE_REDIS_WEBSOCKET = os.getenv('USE_REDIS_WEBSOCKET', 'false').lower() == 'true'

if USE_REDIS_WEBSOCKET:
    ws_manager = WebSocketManager(channel="broadcasts")
else:
    ws_manager = OldBroadcastWebSocketManager()
```

---

## Success Criteria

✅ All WebSocket features work in single-instance mode
✅ All WebSocket features work in multi-replica mode
✅ No performance degradation
✅ Redis failure handled gracefully
✅ No breaking changes to client code
✅ All tests passing
✅ Documentation updated

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Redis connection failure | Medium | Auto-fallback to local mode |
| Message duplication | Low | WebSocketManager prevents this |
| Breaking existing code | High | Backwards compatible design |
| Performance degradation | Medium | Redis is fast, test thoroughly |
| Complex debugging | Medium | Add detailed logging |

---

## Timeline Estimate

- **Phase 1** (Foundation): ✅ DONE
- **Phase 2** (Migrations): 2-4 hours (parallel agents)
- **Phase 3** (Testing): 2-3 hours
- **Phase 4** (Documentation): 1 hour
- **Total**: 5-8 hours

---

## Next Steps

1. Launch sub-agents to analyze existing implementations
2. Review analysis reports
3. Execute migrations in parallel
4. Test each feature individually
5. Integration testing
6. Deploy

---

## Questions to Address

- [ ] Do we need message filtering per track/thread? (Yes - can use message metadata)
- [ ] Do we need message persistence? (No - WebSocket is real-time only)
- [ ] Do we need rate limiting? (Consider adding later)
- [ ] Do we need authentication per WebSocket? (Already using `Depends(login_required)`)
- [ ] Badge icon transition needed? (User unsure - investigate during migration)

---

## Notes

- WebSocketManager is production-ready
- Designed for zero-downtime migration
- Can migrate one feature at a time
- No client-side changes required (same WebSocket endpoints)
