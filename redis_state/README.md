# Redis State Module

Redis-backed state management for multi-container deployments.

## Overview

This module provides shared state management across multiple containers in load-balanced environments. All state is stored in Redis, ensuring consistency regardless of which container handles a request.

## Structure

```
redis_state/
├── config.py                # Redis connection configuration
├── state_manager.py         # Core RedisStateManager (generic, reusable)
│
├── state/                   # State managers (domain-specific)
│   ├── progress.py          # HLS segment progress tracking
│   ├── conversion.py        # Active conversions + distributed locks
│   ├── upload.py            # Upload state management
│   ├── download.py          # Download state management
│   └── upload_legacy.py     # Legacy upload state (deprecated)
│
└── cache/                   # Caches (performance optimization)
    ├── text.py              # Text storage cache (8GB shared)
    ├── word_timing.py       # Word timing cache (CPU savings)
    ├── voice_access.py      # Voice access tracking
    └── upload_stats.py      # Upload progress stats
```

## Usage

### Quick Start

```python
# Import state managers
from redis_state.state import progress_state, conversion_state

# Import caches
from redis_state.cache import text_cache, word_timing_cache

# Use like regular dicts - Redis backing is transparent
progress_state.segment_progress[track_id] = {
    "total_segments": 100,
    "processed_segments": 50
}

# Read from any container
data = progress_state.segment_progress.get(track_id)
```

### Available State Managers

#### HLS Progress (`progress_state`)
```python
from redis_state.state import progress_state

# Track HLS segmentation progress
progress_state.segment_progress[f"{track_id}:voice:{voice_id}"] = {
    "total_segments": 100,
    "processed_segments": 75,
    "status": "processing"
}
```

#### Conversion Tracking (`conversion_state`)
```python
from redis_state.state import conversion_state

# Track active conversions
conversion_state.active_conversions[conversion_id] = {
    "track_id": track_id,
    "started_at": time.time(),
    "status": "converting"
}
```

#### Upload/Download State
```python
from redis_state.state import upload_state, download_state

# Upload state
upload_state.create_session(upload_data)
session = upload_state.get_session(upload_id)

# Download state
download_state.active_downloads[download_id] = {...}
```

### Available Caches

#### Text Cache (`text_cache`)
```python
from redis_state.cache import text_cache, CacheEntry

# Cache text with metadata
entry = CacheEntry(
    content="Text content",
    size=len(content),
    created_at=time.time(),
    last_accessed=time.time(),
    access_count=1,
    file_mtime_ns=mtime,
    expires_at=time.time() + 3600
)
text_cache.cache[cache_key] = entry
```

#### Word Timing Cache (`word_timing_cache`)
```python
from redis_state.cache import word_timing_cache

# Cache word timings (expensive to compute)
word_timing_cache.cache[f"{track_id}:{voice_id}"] = {
    "timings": [...],
    "computed_at": time.time()
}
```

#### Voice Access Tracking (`voice_access_tracker`)
```python
from redis_state.cache import voice_access_tracker

# Record voice usage
voice_access_tracker.record_segment_access(track_id, voice_id, segment_id)

# Get activity
activity = voice_access_tracker.get_voice_activity(track_id, voice_id)
```

#### Upload Stats (`upload_stats`)
```python
from redis_state.cache import upload_stats, WriteStats

# Track upload progress
stats = WriteStats(
    file_id=file_id,
    status="writing",
    path=path,
    queued_at=time.time(),
    bytes_written=1024,
    chunks_written=1
)
upload_stats.file_stats[file_id] = stats
```

## Benefits

### Memory Savings
- **Before**: 8GB text cache × 3 containers = 24GB
- **After**: 8GB shared cache = 8GB (67% reduction)

### CPU Savings
- Word timings computed once, shared across containers
- Prevents duplicate HLS conversions

### Consistency
- All containers see same state
- Progress consistent across load-balanced requests
- State survives container restarts (TTL-based cleanup)

## Architecture

### RedisStateManager Pattern

All state managers use the same generic `RedisStateManager` underneath:

```python
from redis_state.state_manager import RedisStateManager

# Create a state manager for any domain
manager = RedisStateManager("my_namespace", container_id="container-1")

# Dict-like operations
manager.create_session(key, data, ttl=3600)
manager.get_session(key)
manager.update_session(key, updates)
manager.delete_session(key)
```

### Container Awareness

Each state manager tracks which container owns the state:

```python
# Container 1 writes
container1 = RedisProgressState(container_id="container-1")
container1.segment_progress[key] = data

# Container 2 reads
container2 = RedisProgressState(container_id="container-2")
data = container2.segment_progress.get(key)  # ✅ Visible!
```

### TTL-Based Cleanup

All state has automatic expiration:
- **Progress**: 2 hours
- **Conversions**: 1 hour
- **Text Cache**: Variable (up to 24 hours)
- **Word Timing**: 24 hours
- **Voice Access**: 2 hours
- **Upload Stats**: 2 hours

## Multi-Container Deployment

### Docker Compose Example

```yaml
services:
  app1:
    build: .
    environment:
      - CONTAINER_ID=container-1
      - REDIS_HOST=redis
    depends_on:
      - redis
      - postgres

  app2:
    build: .
    environment:
      - CONTAINER_ID=container-2
      - REDIS_HOST=redis
    depends_on:
      - redis
      - postgres

  app3:
    build: .
    environment:
      - CONTAINER_ID=container-3
      - REDIS_HOST=redis
    depends_on:
      - redis
      - postgres

  nginx:
    image: nginx
    ports:
      - "80:80"
    depends_on:
      - app1
      - app2
      - app3

  redis:
    image: redis:7-alpine
    command: redis-server --maxmemory 4gb --maxmemory-policy allkeys-lru

  postgres:
    image: postgres:15
```

### Environment Variables

```bash
# Redis connection
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=your_password  # Optional

# Container identification (for debugging)
CONTAINER_ID=container-1  # Unique per container
```

## Testing

Run the test suite to verify all state managers:

```bash
python test_redis_migrations.py
```

Expected output:
```
============================================================
✅ ALL TESTS PASSED!
============================================================

Redis migrations successful. All state managers working correctly.
Ready for multi-container deployment! 🚀
```

## Migration from In-Memory

Before:
```python
# Old (in-memory, per-container)
self.segment_progress = {}
self.word_timing_cache = {}
```

After:
```python
# New (Redis-backed, shared)
from redis_state.state import progress_state
from redis_state.cache import word_timing_cache

self.segment_progress = progress_state.segment_progress
self.word_timing_cache = word_timing_cache.cache
```

**Zero breaking changes** - same dict-like interface!

## Performance Considerations

### Redis Connection Pooling

The module uses connection pooling with primary/fallback support:
- Primary Redis for normal operations
- Fallback Redis if primary fails
- Automatic retry with exponential backoff

### Serialization

- Small data (<1KB): JSON serialization
- Large data (>1KB): Compressed with zstd
- Binary data: Base64 encoded

### Network Overhead

Typical overhead per operation:
- **Read**: ~1-2ms
- **Write**: ~2-3ms
- **Bulk read (10 items)**: ~5-10ms

Acceptable for most use cases. For ultra-low latency, use local caching with Redis as backup.

## Troubleshooting

### Import Errors

If you see `ModuleNotFoundError: No module named 'redis_state'`:

```bash
# Ensure you're running from project root
cd /home/tundragoon/projects/audio-streaming-appT
python your_script.py
```

### Redis Connection Errors

Check Redis is running:
```bash
redis-cli ping
# Expected: PONG
```

Check Redis connection in Python:
```python
from redis_state import redis_client
redis_client.ping()  # Should return True
```

### State Not Visible Across Containers

1. Verify containers use same Redis host
2. Check `REDIS_HOST` environment variable
3. Verify no firewall blocking Redis port
4. Check Redis logs for errors

## Contributing

When adding new state managers:

1. Use `RedisStateManager` as base
2. Provide dict-like interface for compatibility
3. Set appropriate TTL for your domain
4. Add tests to `test_redis_migrations.py`
5. Update this README with usage examples

## License

Same as parent project.
