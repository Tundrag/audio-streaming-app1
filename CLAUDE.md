# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python-based audio streaming platform with text-to-speech (TTS) generation, HLS streaming, multi-platform payment integration (Patreon/Ko-fi), and community features. Built on FastAPI with PostgreSQL and Redis.

## Development Commands

### Running the Application

```bash
# Activate virtual environment
source venv/bin/activate

# Run with uvicorn (development with auto-reload)
uvicorn app:app --host 0.0.0.0 --port 8000 --reload

# Run with Docker Compose
docker-compose up --build
```

### Testing

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_workers.py

# Run with verbose output
pytest -v
```

### Database Management

```bash
# Initial database setup
python setup_db.py

# Create new migration
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head

# Verify database integrity
python verify_db.py
python check_db.py

# Database cleanup/restore
python cleanup_db.py
python restore_db.py
```

## High-Level Architecture

### Application Entry Point

The main FastAPI application is in `app.py` which:
- Uses `@asynccontextmanager` for application lifecycle (startup/shutdown)
- Registers 25+ routers for different features
- Initializes background workers and cleanup tasks
- Sets up CORS middleware and health check endpoints

### Database Architecture

- **ORM**: SQLAlchemy 2.0 with dual sync/async support
- **Connection Pooling**: Aggressive tuning (250 pool size + 500 overflow) for high concurrency
- **Session Management**: `DatabaseManager` in `database.py` provides unified interface for both sync/async sessions
- **Models**: Defined in `models.py` - all SQLAlchemy models with relationships
- **Migrations**: Alembic-based in `/alembic/` directory

### Core Service Layers

#### 1. Text-to-Speech Pipeline
- **Service**: `EnhancedVoiceAwareTTSService` in `enhanced_tts_voice_service.py`
- **Concurrency**: 30 global Edge TTS slots, 6 per-user cap, fair non-preemptive limiter
- **API**: `enhanced_tts_api_voice.py` for REST endpoints
- **Models**: `Track`, `TTSTrackMeta`, `TTSTextSegment`, `TTSWordTiming`, `AvailableVoice`

#### 2. HLS Streaming
- **Manager**: `StreamManager` in `hls_streaming.py`
- **Core**: `EnterpriseHLSManager` and `BaseHLSManager` in `hls_core.py`
- **Features**: Voice-aware streaming, deadlock prevention with per-track locks
- **Endpoints**: Master playlist → Variant playlist → Segments

#### 3. Download/Upload Management
- **Downloads**:
  - `core/download_workers.py` - Album/batch downloads with stages
  - `core/track_download_workers.py` - Individual track downloads
  - `core/download_cleanup_service.py` - Garbage collection
- **Uploads**:
  - `chunked_upload.py` - Resumable chunked uploads
  - `mega_upload_manager.py` - Cloud upload to MEGA/S4
  - `upload_queue.py` - Queue with status tracking

#### 4. Background Processing
- **Manager**: `BackgroundPreparationManager` in `background_preparation.py`
- **Pattern**: Non-blocking task processing with phases (INIT → METADATA → SEGMENTING → FINALIZING)
- **Workers**: Configured via `WorkerConfig` singleton in `worker_config.py`
- **Scaling**: Dynamic CPU-aware scaling (up to 3× CPU count)

### Worker System Architecture

The `WorkerConfig` singleton manages all background workers:
- **Worker Types**: track_downloaders, disk_io, download_worker, background, metadata, mega_upload, downloads
- **Dynamic Scaling**: Min/max workers per type with cooldown periods
- **Priority Levels**: Different worker types have different priorities
- **Location**: `worker_config.py`

### State Management Patterns

#### Redis Caching
- **Client**: `ResilientRedisClient` in `redis_config.py`
- **Resilience**: Primary + fallback connections with graceful degradation
- **Usage**: Caching, session management, rate limiting

#### Lock Management
- **Simple Track Lock**: `simple_track_lock.py` - Per-track locking to prevent concurrent processing
- **Cleanup**: Orphaned lock cleanup on startup + periodic cleanup tasks

#### Session Management
- **Handler**: `session_manager.py` - Cookie-based sessions with HTTPOnly flags
- **Authentication**: Decorator pattern with `@login_required`

### Authentication & Authorization

- **User Roles**: PATREON, TEAM, CREATOR, KOFI, GUEST
- **Permissions**: Flag-based system in `permissions.py` (VIEW, CREATE, RENAME, DELETE)
- **Tier-Based Access**: Content access verification based on Patreon/Ko-fi tiers
- **Auth Routes**: `auth.py` - Patreon/Ko-fi login + PIN verification

### Payment Platform Integration

#### Patreon
- **Sync Service**: `sync/sync_service.py` - `PatreonSyncService`
- **Sync Worker**: `sync/sync_worker.py` - Background sync every 5 hours
- **Client**: `patreon_client.py` - Patreon API wrapper
- **Routes**: `patreon_routes.py`

#### Ko-fi
- **Sync Service**: `sync/kofi_sync_service.py` - `KofiSyncService`
- **Routes**: `kofi_routes.py` + `kofi_service.py`
- **Webhooks**: Real-time updates on payments

### Major Router Modules

- `platform_tiers.py` - Tier management
- `book_request.py` - User-requested content queue
- `forum_routes.py` - Community forums with WebSocket support
- `comment_routes.py` - Track comments with real-time notifications
- `notifications.py` - Notification system
- `broadcast_router.py` - Mass messaging
- `discord_routes.py` - Discord webhook integration
- `activity_logs_router.py` - Audit logging
- `document_extraction_service.py` - PDF/document text extraction

### Storage Architecture

- **Cloud Storage**: MEGA via `mega_s4_client.py` with CLI integration (`mega-put`, `mega-mkdir`)
- **Local Storage**: `/media` and `/blobs` directories for working files
- **Text Storage**: `text_storage_service.py` - 15GB TTL cache for track texts

### Critical Dependencies

- **PostgreSQL**: Primary database
- **Redis**: Caching and state management
- **Edge-TTS**: Microsoft TTS engine (asyncio-based)
- **FFMPEG**: Audio transcoding (semaphore-controlled, max 6 concurrent)
- **megatools**: CLI tools for MEGA cloud storage

## Important Conventions

### Async/Sync Compatibility
- Functions often support both sync and async modes
- Use `DatabaseManager` for session management to avoid blocking
- Use `anyio.to_thread` for filesystem operations in async contexts
- `aiofiles` for async file I/O

### Error Handling
- Graceful degradation for external services (Redis, MEGA)
- Fallback mechanisms for critical infrastructure
- Detailed error logging with context

### Concurrency Patterns
- Fair limiters with non-preemptive queuing for TTS
- Semaphores for FFMPEG and other resource-intensive operations
- Per-user caps to prevent resource monopolization
- Global caps for system-wide resource limits

### File Operations
- Always use async file operations in request handlers
- Use `anyio.to_thread` for blocking I/O in async contexts
- Proper cleanup with context managers

## Key Data Flows

### TTS Generation Flow
1. Request received at `enhanced_tts_api_voice.py`
2. Fair limiter queues job (respecting caps)
3. Text segmented for streaming
4. `EnhancedVoiceAwareTTSService` generates audio via Edge-TTS
5. Segments streamed back via HLS endpoints
6. Word timings calculated if needed

### HLS Streaming Flow
1. Master playlist: `/hls/{track_id}/master.m3u8`
2. Variant playlist: `/hls/{track_id}/{quality}/playlist.m3u8`
3. Media segments: `/hls/{track_id}/{quality}/segment_{n}.ts`
4. Voice-aware variants: `/hls/{track_id}/voice/{voice_id}/...`
5. Progress tracking with deadlock prevention

### Upload Flow
1. Chunked upload handler receives file parts
2. Upload queued via `upload_queue.py`
3. `mega_upload_manager.py` processes upload to cloud
4. Metadata extracted asynchronously via `metadata_extraction.py`
5. Track stored in PostgreSQL with initial state

### Sync Flow (Patreon/Ko-fi)
1. Periodic task triggers (5-hour intervals)
2. Sync worker retrieves current patrons from API
3. Campaign tiers updated in PostgreSQL
4. User tier assignments updated
5. Notifications sent to creators

## Environment Configuration

Key environment variables (from `.env` - never commit this file):
- `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD` - Redis connection
- `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_NAME` - PostgreSQL
- Patreon API credentials
- Ko-fi API credentials
- Storage credentials (MEGA/S4)

## Files to Read First

When starting work on this codebase:
1. `app.py` - Application structure and router registration
2. `models.py` - Data models and relationships
3. `database.py` - Session management patterns
4. `worker_config.py` - Worker system architecture
5. `background_preparation.py` - Task queueing paradigm
6. `enhanced_tts_voice_service.py` - Advanced concurrency patterns
- in destop mode the bage can have full text mobile mode the track will have icon but collection have the full text baaage abnd yes badge persists now