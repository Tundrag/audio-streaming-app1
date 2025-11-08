# New enhanced_tts_voice_service.py - Fully memory-optimized with streaming and bounded concurrency

import asyncio
import aiofiles
import edge_tts
import tempfile
import os
import uuid
import logging
import subprocess
import anyio
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, AsyncGenerator, Generator, Set
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, desc, and_, or_, text, select, delete
from datetime import datetime, timezone
from models import Track, TTSTextSegment, TTSWordTiming, TTSTrackMeta, AvailableVoice, User
from contextlib import asynccontextmanager
from collections import deque
import json
import re
import time
import random
from text_storage_service import text_storage_service, TextStorageError
from track_status_manager import TrackStatusManager
import gc
import psutil

# Import Redis state manager for multi-container support
from redis_state.state_manager import RedisStateManager

logger = logging.getLogger(__name__)

# ===========================
# GLOBAL POOL CONFIGURATION
# ===========================
GLOBAL_MAX_CHUNK_SLOTS = 30        # system-wide concurrent Edge TTS limit
PER_USER_HARD_CAP = 6             # max workers per user

if PER_USER_HARD_CAP > GLOBAL_MAX_CHUNK_SLOTS:
    raise ValueError(
        f"Invalid config: PER_USER_HARD_CAP ({PER_USER_HARD_CAP}) "
        f"cannot exceed GLOBAL_MAX_CHUNK_SLOTS ({GLOBAL_MAX_CHUNK_SLOTS})"
    )

EDGE_TTS_SEMAPHORE = asyncio.Semaphore(GLOBAL_MAX_CHUNK_SLOTS)
FFMPEG_SEMAPHORE   = asyncio.Semaphore(6)


# ===========================================================
# NON-PREEMPTIVE FAIR LIMITER (global + per-user foundations)
# ===========================================================
class NonPreemptiveFairLimiter:
    """
    Fair, queue-based limiter over a fixed pool of `max_slots`.
    - Non-preemptive: never interrupts in-flight work.
    - Fair: distributes per-job quotas with round-robin extras.
    - Discrete: base = floor(max_slots / N), leftovers rotate.
    """

    def __init__(self, max_slots: int, *, name: str = "POOL"):
        if max_slots < 0:
            raise ValueError("max_slots must be >= 0")
        self.name = name
        self.max_slots = max_slots
        self.available_slots = max_slots

        # Per job: desired concurrent slots (quota) and current inflight count
        self.desired: Dict[str, int] = {}
        self.inflight: Dict[str, int] = {}

        # Round-robin cursor to rotate leftover `+1` allocations
        self._rr_cursor = 0

        # Concurrency primitives
        self._lock = asyncio.Lock()
        self._condition = asyncio.Condition(self._lock)

    async def add_job(self, job_id: str):
        """Register a job into the pool and rebalance quotas."""
        async with self._condition:
            if job_id not in self.desired:
                self.inflight[job_id] = 0
                self.desired[job_id] = 0  # will be set by _rebalance()
                await self._rebalance()
                logger.debug(f"[{self.name}] add {job_id} | jobs={len(self.desired)}")
            self._condition.notify_all()

    async def remove_job(self, job_id: str):
        """Unregister a job and rebalance quotas."""
        async with self._condition:
            if job_id in self.desired:
                self.desired.pop(job_id, None)
                self.inflight.pop(job_id, None)
                if self.desired:
                    await self._rebalance()
                logger.debug(f"[{self.name}] remove {job_id} | jobs={len(self.desired)}")
            self._condition.notify_all()

    async def update_max_slots(self, new_max: int):
        """
        Change total capacity and rebalance quotas.
        Allows 0 for oversubscription scenarios (everyone may be at 0 or rotating +1).
        """
        if new_max < 0:
            raise ValueError("new_max must be >= 0")

        async with self._condition:
            used = sum(self.inflight.values())
            self.max_slots = int(new_max)
            self.available_slots = max(0, self.max_slots - used)
            await self._rebalance()
            logger.debug(f"[{self.name}] resize -> {self.max_slots} | used={used} avail={self.available_slots}")
            self._condition.notify_all()

    async def rebalance(self):
        """
        Public trigger to recompute per-job desired quotas using round-robin leftovers.
        Returned mapping is a snapshot for observability/debugging.
        """
        async with self._condition:
            await self._rebalance()
            self._condition.notify_all()
            return dict(self.desired)

    async def _rebalance(self):
        """Compute per-job desired quotas with base+round-robin(+1) extras."""
        jobs = list(self.desired.keys())
        n = len(jobs)
        if n == 0:
            return

        # Non-preemptive: respect current inflight as a lower bound
        used = sum(self.inflight.values())
        self.available_slots = max(0, self.max_slots - used)

        base = 0 if n == 0 else self.max_slots // n           # may be 0 if oversubscribed
        leftover = max(0, self.max_slots - base * n)          # number of +1 to distribute

        ordered = sorted(jobs)                                 # stable order
        start = self._rr_cursor % n
        rotated = ordered[start:] + ordered[:start]
        bonus_set = set(rotated[:leftover]) if leftover > 0 else set()

        # Assign base (+1 for first `leftover` jobs), but never below inflight
        for jid in jobs:
            target = base + (1 if jid in bonus_set else 0)
            self.desired[jid] = max(target, self.inflight.get(jid, 0))

        # Advance rotation by 1 so extras rotate next time
        if n > 0:
            self._rr_cursor = (self._rr_cursor + 1) % n

        logger.debug(
            f"[{self.name}] rebalance | jobs={n} base={base} +{leftover} "
            f"used={used} avail={self.available_slots}"
        )

    @asynccontextmanager
    async def slot(self, job_id: str):
        """
        Acquire one slot fairly for `job_id`.
        Waits until both: pool has capacity AND job is under its quota.
        """
        async with self._condition:
            # Defensive: if job arrives here unregistered, register it
            if job_id not in self.desired:
                # This path should be rare; prefer calling add_job() first.
                self.inflight.setdefault(job_id, 0)
                self.desired.setdefault(job_id, 0)
                await self._rebalance()

            while True:
                job_limit = self.desired.get(job_id, 0)
                job_current = self.inflight.get(job_id, 0)

                if self.available_slots > 0 and job_current < job_limit:
                    self.available_slots -= 1
                    self.inflight[job_id] = job_current + 1
                    break

                await self._condition.wait()

        try:
            yield
        finally:
            async with self._condition:
                self.available_slots += 1
                self.inflight[job_id] = max(0, self.inflight.get(job_id, 1) - 1)
                self._condition.notify_all()


# ===========================================================
# TWO-LEVEL USER JOB MANAGER (global + per-user/job)
# ===========================================================

# Level-1 limiter: shared across all users (job_id = "user_{id}")
GLOBAL_USER_LIMITER = NonPreemptiveFairLimiter(GLOBAL_MAX_CHUNK_SLOTS, name="GLOBAL")


class UserJobManager:
    """
    Two-level fairness:
      1) Global limiter across users (round-robin extras among users).
      2) Per-user limiter across that user's jobs (round-robin extras among jobs).
    Non-preemptive, queueing (no rejections), thread-safe.
    """

    def __init__(self, max_per_user: int = PER_USER_HARD_CAP):
        self.max_per_user = max_per_user
        self.global_limiter = GLOBAL_USER_LIMITER
        self._lock = asyncio.Lock()
        self._user_limiters: Dict[int, NonPreemptiveFairLimiter] = {}
        self._user_jobs: Dict[int, Dict[str, Dict]] = {}  # user_id -> {job_id -> info}

    async def _get_user_limiter(self, user_id: int) -> NonPreemptiveFairLimiter:
        """Get/create the per-user limiter."""
        async with self._lock:
            if user_id not in self._user_limiters:
                self._user_limiters[user_id] = NonPreemptiveFairLimiter(
                    self.max_per_user, name=f"U{user_id}"
                )
                self._user_jobs[user_id] = {}
            return self._user_limiters[user_id]

    async def add_job(self, user_id: int, job_id: str, job_info: Optional[Dict] = None):
        """
        Register a new job for a user.
        First job for a user adds that user to the GLOBAL limiter.
        """
        new_user = False
        async with self._lock:
            if user_id not in self._user_limiters:
                self._user_limiters[user_id] = NonPreemptiveFairLimiter(
                    self.max_per_user, name=f"U{user_id}"
                )
                self._user_jobs[user_id] = {}
                new_user = True

            meta = dict(job_info or {})
            meta.update({
                "job_id": job_id,
                "user_id": user_id,
                "started_at": time.time(),
                "status": "active",
            })
            self._user_jobs[user_id][job_id] = meta

        if new_user:
            await self.global_limiter.add_job(f"user_{user_id}")
            logger.info(f"[POOL] user+ U{user_id} (added to GLOBAL)")

        # Add the job at per-user level
        limiter = await self._get_user_limiter(user_id)
        await limiter.add_job(job_id)

        # Recompute both levels
        await self._recompute_fair_distribution()

        # Short status log
        async with self._lock:
            active_users = len(self._user_jobs)
            user_job_count = len(self._user_jobs[user_id])
        global_avail = self.global_limiter.available_slots
        logger.info(
            f"[POOL] status | global avail={global_avail}/{self.global_limiter.max_slots} "
            f"| users={active_users} | U{user_id} jobs={user_job_count}"
        )

    async def remove_job(self, user_id: int, job_id: str, success: bool = True):
        """Mark a job complete/failed, clean up, and rebalance."""
        limiter = self._user_limiters.get(user_id)
        if limiter:
            await limiter.remove_job(job_id)

        remove_from_global = False
        async with self._lock:
            if user_id in self._user_jobs and job_id in self._user_jobs[user_id]:
                info = self._user_jobs[user_id][job_id]
                info.update({
                    "completed_at": time.time(),
                    "status": "completed" if success else "failed",
                    "success": success,
                })
                self._user_jobs[user_id].pop(job_id, None)
                remaining = len(self._user_jobs[user_id])
                logger.info(f"[POOL] job- U{user_id}:{job_id} | remain={remaining}")

            # If last job, tear down per-user limiter and remove from GLOBAL
            if user_id in self._user_jobs and not self._user_jobs[user_id]:
                remove_from_global = True
                self._user_jobs.pop(user_id, None)
                self._user_limiters.pop(user_id, None)

        if remove_from_global:
            await self.global_limiter.remove_job(f"user_{user_id}")
            ga = self.global_limiter.available_slots
            gt = self.global_limiter.max_slots
            logger.info(f"[POOL] user- U{user_id} | GLOBAL avail={ga}/{gt}")

        await self._recompute_fair_distribution()

    async def _recompute_fair_distribution(self):
        """
        Rebalance per-user shares based on active users (round-robin extras).
        Then set each user's per-user limiter capacity to that user's current global share.
        """
        # Step 1: ensure GLOBAL quotas are up to date
        global_desired = await self.global_limiter.rebalance()

        # Step 2: propagate each user's global share to their per-user limiter (capped by PER_USER_HARD_CAP)
        async with self._lock:
            active_users = list(self._user_jobs.keys())

        n = len(active_users)
        if n == 0:
            return

        # Logging aids
        base = (self.global_limiter.max_slots // n) if n > 0 else 0
        leftover = max(0, self.global_limiter.max_slots - base * n)

        # Track actual allocations for logging
        user_allocations = {}
        total_allocated = 0
        
        for uid in active_users:
            per_user_global = int(global_desired.get(f"user_{uid}", 0))
            per_user_cap = min(self.max_per_user, per_user_global)
            limiter = self._user_limiters.get(uid)
            if limiter:
                await limiter.update_max_slots(per_user_cap)
                user_allocations[uid] = {
                    'global_share': per_user_global,
                    'actual_cap': per_user_cap,
                    'jobs': len(self._user_jobs.get(uid, {}))
                }
                total_allocated += per_user_cap

        # Enhanced logging with actual allocations
        logger.info(
            f"[POOL] REBALANCE | users={n} base={base} +{leftover} "
            f"| total_allocated={total_allocated}/{self.global_limiter.max_slots} "
            f"| available={self.global_limiter.max_slots - total_allocated}"
        )
        
        # Log per-user details
        for uid, alloc in user_allocations.items():
            logger.info(
                f"[POOL] └─ U{uid}: global_share={alloc['global_share']} → "
                f"capped={alloc['actual_cap']} (jobs={alloc['jobs']})"
            )

    @asynccontextmanager
    async def slot(self, user_id: int, job_id: str):
        """
        Acquire one slot across both levels (GLOBAL then per-user).
        Idempotently re-adds the GLOBAL user bucket to close race windows.
        """
        await self.global_limiter.add_job(f"user_{user_id}")  # safe if already present
        async with self.global_limiter.slot(f"user_{user_id}"):
            limiter = await self._get_user_limiter(user_id)
            async with limiter.slot(job_id):
                yield

    async def get_user_status(self, user_id: int) -> Dict:
        """Return a snapshot of one user's limiter + job info."""
        async with self._lock:
            limiter = self._user_limiters.get(user_id)
            jobs = self._user_jobs.get(user_id, {})

        if limiter:
            return {
                "user_id": user_id,
                "max_chunks": limiter.max_slots,
                "available_chunks": limiter.available_slots,
                "active_jobs": len(jobs),
                "job_details": {
                    jid: {
                        "desired_slots": limiter.desired.get(jid, 0),
                        "inflight_slots": limiter.inflight.get(jid, 0),
                        **info,
                    }
                    for jid, info in jobs.items()
                },
            }
        else:
            return {
                "user_id": user_id,
                "max_chunks": self.max_per_user,
                "available_chunks": self.max_per_user,
                "active_jobs": 0,
                "job_details": {},
            }

    async def get_system_status(self) -> Dict:
        """Return a snapshot of GLOBAL + all per-user limiters."""
        async with self._lock:
            active_users = list(self._user_jobs.keys())
            total_active_jobs = sum(len(jobs) for jobs in self._user_jobs.values())

        status = {
            "max_slots": self.global_limiter.max_slots,
            "available_slots": self.global_limiter.available_slots,
            "active_users": len(active_users),
            "active_jobs": total_active_jobs,
            "is_oversubscribed": len(active_users) > self.global_limiter.max_slots,
            "users": {},
        }

        for uid in active_users:
            limiter = self._user_limiters.get(uid)
            jobs = self._user_jobs.get(uid, {})
            if limiter:
                status["users"][uid] = {
                    "max_slots": limiter.max_slots,
                    "available_slots": limiter.available_slots,
                    "active_jobs": len(jobs),
                    "jobs": {
                        jid: {
                            "desired": limiter.desired.get(jid, 0),
                            "inflight": limiter.inflight.get(jid, 0),
                            "track_id": info.get("track_id"),
                            "voice_id": info.get("voice_id"),
                            "started_at": info.get("started_at"),
                        }
                        for jid, info in jobs.items()
                    },
                }

        return status

    async def get_queue_depth(self) -> int:
        """Approximate number of users that may be queued globally."""
        async with self._lock:
            active_users = len(self._user_jobs.keys())
        return max(0, active_users - self.global_limiter.max_slots)

# Memory management constants
MAX_MEMORY_PERCENT = 85
CHUNK_BATCH_SIZE = 10
MAX_WORKERS_PER_JOB = PER_USER_HARD_CAP  # Workers scale with user pool allocation
GC_FREQUENCY = 5  # Run GC every N chunks per worker
GC_THRESHOLD_PERCENT = 75  # Only run GC if memory > this %

# Async/sync compatibility helpers
def _is_async(db) -> bool:
    return isinstance(db, AsyncSession)

async def _exec(db, stmt):
    if _is_async(db):
        return await db.execute(stmt)
    return await anyio.to_thread.run_sync(db.execute, stmt)

async def _commit(db):
    if _is_async(db):
        await db.commit()
    else:
        await anyio.to_thread.run_sync(db.commit)

async def _rollback(db):
    if _is_async(db):
        await db.rollback()
    else:
        await anyio.to_thread.run_sync(db.rollback)

async def _flush(db):
    if _is_async(db):
        await db.flush()
    else:
        await anyio.to_thread.run_sync(db.flush)

# Async filesystem helpers
async def _amkdir(p: Path):
    return await anyio.to_thread.run_sync(lambda: p.mkdir(parents=True, exist_ok=True))

async def _arename(src: Path, dst: Path):
    return await anyio.to_thread.run_sync(src.rename, dst)

async def _areplace(src: Path, dst: Path):
    return await anyio.to_thread.run_sync(src.replace, dst)

async def _aexists(p: Path) -> bool:
    return await anyio.to_thread.run_sync(p.exists)

async def _aunlink(p: Path):
    """Atomic check and unlink in single thread call"""
    def _safe_unlink():
        if p.exists():
            p.unlink(missing_ok=True)
            return True
        return False
    return await anyio.to_thread.run_sync(_safe_unlink)

async def _armtree(p: Path):
    import shutil
    return await anyio.to_thread.run_sync(shutil.rmtree, p, True)

async def _astat_size(p: Path) -> int:
    return await anyio.to_thread.run_sync(lambda: p.stat().st_size)

def _check_memory_usage() -> Tuple[float, bool]:
    """Check current memory usage and return (usage_percent, should_cleanup)"""
    try:
        process = psutil.Process(os.getpid())
        memory_percent = process.memory_percent()
        should_cleanup = memory_percent > MAX_MEMORY_PERCENT
        return memory_percent, should_cleanup
    except Exception:
        return 0.0, False

async def _force_garbage_collection():
    """Force garbage collection in thread pool only if memory is high"""
    def _gc_collect_conditional():
        process = psutil.Process(os.getpid())
        memory_percent = process.memory_percent()
        if memory_percent > GC_THRESHOLD_PERCENT:
            collected = gc.collect()
            return collected, memory_percent
        return 0, memory_percent
    return await anyio.to_thread.run_sync(_gc_collect_conditional)

async def get_available_voices_from_db(db) -> List[str]:
    try:
        res = await _exec(db, select(AvailableVoice.voice_id).where(AvailableVoice.is_active.is_(True)))
        return res.scalars().all()
    except Exception as e:
        logger.error(f"Error fetching voices from DB: {e}")
        return []

class EnhancedVoiceAwareTTSService:
    """Enhanced TTS service with streaming, bounded concurrency, and optimal memory usage"""
    
    def __init__(self):
        self.temp_dir = Path("/tmp/media_storage/tts")
        self.temp_dir.mkdir(exist_ok=True)

        # REPLACED: In-memory dicts with Redis for multi-container support
        # self.voice_switch_locks = {}
        # self.voice_switch_progress = {}

        # Initialize Redis state manager for TTS operations
        self.tts_state = RedisStateManager("tts")
        
        self.max_words_per_chunk = 320
        self.min_words_per_chunk = 120
        self.words_per_minute = 180
        
        self.max_concurrent_chunks_per_user = PER_USER_HARD_CAP
        self.user_job_manager = UserJobManager(max_per_user=PER_USER_HARD_CAP)
        
        self.max_retry_attempts = 3
        self.initial_retry_delay = 1.0
        self.retry_backoff_multiplier = 2.0
        
        self.user_active_generations = {}
        self.max_concurrent_per_user = 1
        self.max_concurrent_per_creator = 5
        
        self.text_service = text_storage_service

        # REPLACED: In-memory bulk job tracking with Redis
        # self.bulk_jobs: Dict[str, Dict] = {}
        # self._bulk_lock = asyncio.Lock()  # Still needed for local async coordination
        # self._cancelled_jobs = set()  # Now in Redis
        # self._job_tasks: Dict[str, asyncio.Task] = {}  # Keep local (non-serializable)

        self._bulk_lock = asyncio.Lock()  # Local lock for async operations
        self._job_tasks: Dict[str, asyncio.Task] = {}  # Local task tracking (can't serialize asyncio.Task)

        self._memory_check_interval = 50
        self._operation_count = 0

        logger.info("EnhancedVoiceAwareTTSService initialized with Redis state management")

    # ===== Redis-Backed State Properties (Backward Compatibility) =====

    @property
    def voice_switch_locks(self) -> Dict:
        """
        Compatibility property for voice_switch_locks.
        Now backed by Redis instead of in-memory dict.
        Returns dict-like interface for existing code.
        """
        class RedisVoiceSwitchLocks:
            def __init__(self, tts_state):
                self.tts_state = tts_state

            def __contains__(self, lock_key: str) -> bool:
                """Check if lock exists (lock_key format: 'track_id:voice_id')"""
                return self.tts_state.is_locked(f"voice_switch:{lock_key}")

            def __getitem__(self, lock_key: str) -> Dict:
                """Get lock data"""
                data = self.tts_state.get_session(f"voice_switch_lock:{lock_key}")
                return data if data else {}

            def __setitem__(self, lock_key: str, value: Dict):
                """Set lock data"""
                self.tts_state.create_session(f"voice_switch_lock:{lock_key}", value, ttl=1800)

            def get(self, lock_key: str, default=None):
                """Get with default"""
                data = self.tts_state.get_session(f"voice_switch_lock:{lock_key}")
                return data if data else default

            def keys(self):
                """Get all lock keys"""
                sessions = self.tts_state.get_all_sessions()
                return [s.get("lock_key") for s in sessions if "lock_key" in s]

            def pop(self, lock_key: str, default=None):
                """Pop (get and delete) lock data"""
                data = self.tts_state.get_session(f"voice_switch_lock:{lock_key}")
                if data:
                    self.tts_state.delete_session(f"voice_switch_lock:{lock_key}")
                    return data
                return default

        return RedisVoiceSwitchLocks(self.tts_state)

    @property
    def voice_switch_progress(self) -> Dict:
        """
        Compatibility property for voice_switch_progress.
        Now backed by Redis instead of in-memory dict.
        """
        class ProgressDict(dict):
            """Dict subclass that writes back to Redis on update()"""
            def __init__(self, parent, lock_key: str, data: Dict):
                super().__init__(data)
                self.parent = parent
                self.lock_key = lock_key

            def update(self, other: Dict):
                """Override update to write back to Redis"""
                super().update(other)
                # Write the updated dict back to Redis
                self.parent.tts_state.set_progress(
                    f"voice_switch:{self.lock_key}",
                    dict(self),
                    ttl=1800
                )

        class RedisVoiceSwitchProgress:
            def __init__(self, tts_state):
                self.tts_state = tts_state

            def __contains__(self, lock_key: str) -> bool:
                """Check if progress exists"""
                return self.tts_state.get_progress(f"voice_switch:{lock_key}") is not None

            def __getitem__(self, lock_key: str) -> Dict:
                """Get progress data - returns special dict that can write back"""
                data = self.tts_state.get_progress(f"voice_switch:{lock_key}")
                if data is None:
                    data = {}
                # Return ProgressDict that can write back on .update()
                return ProgressDict(self, lock_key, data)

            def __setitem__(self, lock_key: str, value: Dict):
                """Set progress data"""
                self.tts_state.set_progress(f"voice_switch:{lock_key}", value, ttl=1800)

            def get(self, lock_key: str, default=None):
                """Get with default"""
                data = self.tts_state.get_progress(f"voice_switch:{lock_key}")
                return data if data else default

            def pop(self, lock_key: str, default=None):
                """Pop (get and delete) progress data"""
                data = self.tts_state.get_progress(f"voice_switch:{lock_key}")
                if data:
                    self.tts_state.delete_progress(f"voice_switch:{lock_key}")
                    return data
                return default

        return RedisVoiceSwitchProgress(self.tts_state)

    @property
    def bulk_jobs(self) -> Dict:
        """
        Compatibility property for bulk_jobs.
        Now backed by Redis instead of in-memory dict.
        """
        class RedisBulkJobs:
            def __init__(self, tts_state):
                self.tts_state = tts_state

            def __contains__(self, job_id: str) -> bool:
                """Check if bulk job exists"""
                return self.tts_state.get_session(f"bulk_job:{job_id}") is not None

            def __getitem__(self, job_id: str) -> Dict:
                """Get bulk job data"""
                data = self.tts_state.get_session(f"bulk_job:{job_id}")
                if data is None:
                    raise KeyError(f"Bulk job {job_id} not found")
                return data

            def __setitem__(self, job_id: str, value: Dict):
                """Set bulk job data"""
                self.tts_state.create_session(f"bulk_job:{job_id}", value, ttl=7200)

            def get(self, job_id: str, default=None):
                """Get with default"""
                data = self.tts_state.get_session(f"bulk_job:{job_id}")
                return data if data else default

        return RedisBulkJobs(self.tts_state)

    @property
    def _cancelled_jobs(self) -> Set:
        """
        Compatibility property for _cancelled_jobs.
        Now backed by Redis set instead of in-memory set.
        """
        class RedisCancelledJobs:
            def __init__(self, tts_state):
                self.tts_state = tts_state

            def add(self, job_id: str):
                """Add job to cancelled set"""
                self.tts_state.add_to_set("cancelled_jobs", job_id)

            def discard(self, job_id: str):
                """Remove job from cancelled set"""
                self.tts_state.remove_from_set("cancelled_jobs", job_id)

            def __contains__(self, job_id: str) -> bool:
                """Check if job is cancelled"""
                return self.tts_state.is_in_set("cancelled_jobs", job_id)

        return RedisCancelledJobs(self.tts_state)

    # ===== End Compatibility Properties =====

    async def _check_and_cleanup_memory(self):
        """Periodically check memory and cleanup if needed"""
        self._operation_count += 1
        if self._operation_count % self._memory_check_interval == 0:
            memory_percent, should_cleanup = _check_memory_usage()
            if should_cleanup:
                logger.warning(f"Memory usage high ({memory_percent:.1f}%), triggering cleanup")
                collected, mem_pct = await _force_garbage_collection()
                logger.info(f"Garbage collection freed {collected} objects (mem: {mem_pct:.1f}%)")

    def _generate_job_id(self, user: User) -> str:
        return f"{user.id}_{uuid.uuid4().hex[:8]}"

    @asynccontextmanager
    async def tts_job(self, user: User, track_id: str, voice_id: str):
        job_id = self._generate_job_id(user)
        job_info = {
            'track_id': track_id,
            'voice_id': voice_id,
            'user_id': user.id,
            'is_creator': user.is_creator
        }
        
        await self.user_job_manager.add_job(user.id, job_id, job_info)
        
        try:
            yield job_id
            await self.user_job_manager.remove_job(user.id, job_id, success=True)
        except Exception as e:
            logger.error(f"Job {job_id} failed: {str(e)}")
            await self.user_job_manager.remove_job(user.id, job_id, success=False)
            raise

    async def get_bulk_job_status(self, bulk_queue_id: str, user: User) -> Dict:
        async with self._bulk_lock:
            meta = self.bulk_jobs.get(bulk_queue_id)
            if not meta:
                raise ValueError("Bulk job not found")

            if meta.get('user_id') != getattr(user, 'id', None) and not getattr(user, 'is_creator', False) and not getattr(user, 'is_team', False):
                raise ValueError("Not authorized to view this bulk job")

            total = max(1, int(meta.get('total_segments', 0) or 0))
            completed = int(meta.get('completed_segments', 0) or 0)
            failed = int(meta.get('failed_segments', 0) or 0)
            done = completed + failed
            progress = int(100 * done / total)

            eta_seconds = None
            started_at = meta.get('started_at')
            if started_at and done > 0:
                elapsed = max(0.0, time.time() - float(started_at))
                eta_seconds = max(0, int(elapsed * (total - done) / done))

            return {
                "bulk_queue_id": meta["bulk_queue_id"],
                "series_title": meta.get("series_title"),
                "status": meta.get("status", "queued"),
                "voice": meta.get("voice"),
                "total_segments": total,
                "completed_segments": completed,
                "failed_segments": failed,
                "progress_percentage": progress,
                "started_at": meta.get("started_at"),
                "completed_at": meta.get("completed_at"),
                "eta_seconds": eta_seconds,
                "user_id": meta.get("user_id"),
            }

    async def _get_first_available_voice(self, db) -> Optional[str]:
        try:
            available_voices = await get_available_voices_from_db(db)
            return available_voices[0] if available_voices else None
        except Exception as e:
            logger.error(f"Error getting first available voice: {e}")
            return None

    async def _validate_voice_with_db(self, voice: str, db, track_default_voice: str = None) -> str:
        try:
            if not db:
                return track_default_voice if track_default_voice else voice
            
            available_voices = await get_available_voices_from_db(db)
            
            if voice in available_voices:
                return voice
            
            if track_default_voice and track_default_voice in available_voices:
                logger.warning(f"Voice {voice} not found, using track default: {track_default_voice}")
                return track_default_voice
            
            if track_default_voice:
                logger.error(f"Track default voice {track_default_voice} not in database!")
                return track_default_voice
            else:
                logger.error(f"Voice {voice} not found and no track default provided!")
                return voice
                
        except Exception as e:
            logger.error(f"Error validating voice {voice}: {e}")
            return track_default_voice if track_default_voice else voice

    async def cancel_job(self, job_id: str, track_id: str):
        logger.info(f"Cancelling job {job_id} for track {track_id}")
        
        self._cancelled_jobs.add(job_id)
        
        if job_id in self._job_tasks:
            task = self._job_tasks[job_id]
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            del self._job_tasks[job_id]
        
        try:
            # Add both patterns:
            for pattern in (f"tts_{track_id}*", f"voice_switch_{track_id}*"):
                for session_dir in self.temp_dir.glob(pattern):
                    if await _aexists(session_dir):
                        await _armtree(session_dir)
        except Exception as e:
            logger.error(f"Error cleaning up session for {track_id}: {e}")
        
        self._cancelled_jobs.discard(job_id)

    def _word_count(self, text: str) -> int:
        """Offloaded to thread pool for large texts"""
        return len(re.findall(r'\S+', text))

    async def _split_text_into_chunks(self, text: str) -> AsyncGenerator[str, None]:
        """
        MODIFIED: Now streams chunks instead of returning list.
        Memory: O(1) instead of O(num_chunks).
        """
        try:
            text = await anyio.to_thread.run_sync(lambda: re.sub(r'\s+', ' ', text.strip()))
            if not text:
                return
            
            def chunk_generator():
                return self._natural_chunk_split(
                    text, 
                    self.max_words_per_chunk, 
                    self.min_words_per_chunk
                )
            
            chunks_gen = await anyio.to_thread.run_sync(chunk_generator)
            
            for chunk in chunks_gen:
                yield chunk
                
        except Exception as e:
            logger.error(f"Error splitting text: {str(e)}")
            words = text.split()
            current_chunk = []
            
            for word in words:
                current_chunk.append(word)
                if len(current_chunk) >= self.max_words_per_chunk:
                    yield ' '.join(current_chunk)
                    current_chunk = []
            
            if current_chunk:
                yield ' '.join(current_chunk)


    def _natural_chunk_split(self, text: str, max_words_per_chunk: int, min_words_per_chunk: int) -> List[str]:
        """Synchronous chunk splitting - called in thread pool"""
        chunks: List[str] = []
        current_chunk: List[str] = []
        current_word_count = 0
        
        sentences = re.split(r'(?<=[.!?])\s+', text)

        for sentence in sentences:
            sentence_words = sentence.split()
            sentence_word_count = len(sentence_words)

            if current_word_count + sentence_word_count <= max_words_per_chunk:
                current_chunk.extend(sentence_words)
                current_word_count += sentence_word_count
            else:
                if current_chunk:
                    chunks.append(' '.join(current_chunk))
                    current_chunk = []
                    current_word_count = 0

                if sentence_word_count > max_words_per_chunk:
                    while sentence_words:
                        sub_chunk = sentence_words[:max_words_per_chunk]
                        chunks.append(' '.join(sub_chunk))
                        sentence_words = sentence_words[max_words_per_chunk:]
                else:
                    current_chunk = sentence_words
                    current_word_count = sentence_word_count

        if current_chunk:
            chunks.append(' '.join(current_chunk))

        merged_chunks: List[str] = []
        temp_chunk: List[str] = []
        temp_word_count = 0
        
        for chunk in chunks:
            chunk_word_count = self._word_count(chunk)
            
            if temp_word_count + chunk_word_count <= max_words_per_chunk:
                temp_chunk.append(chunk)
                temp_word_count += chunk_word_count
            else:
                if temp_word_count >= min_words_per_chunk:
                    merged_chunks.append(' '.join(temp_chunk))
                else:
                    merged_chunks.extend(temp_chunk)
                
                temp_chunk = [chunk]
                temp_word_count = chunk_word_count

        if temp_chunk:
            if temp_word_count >= min_words_per_chunk:
                merged_chunks.append(' '.join(temp_chunk))
            else:
                merged_chunks.extend(temp_chunk)

        return merged_chunks

    async def _smart_split_for_bulk_streaming(
        self, 
        text: str, 
        target_segments: int
    ) -> AsyncGenerator[Tuple[int, str], None]:
        """
        TRUE STREAMING: Yields segments one at a time from thread pool generator.
        OPTIMIZATION #1: Memory stays O(1) instead of O(text_size).
        MICRO-OPT: Iterative pull from generator instead of materializing list.
        """
        if not text or target_segments <= 1:
            yield 0, text
            return

        # Queue for streaming segments
        queue = asyncio.Queue(maxsize=5)
        done_event = asyncio.Event()
        error_container = []

        async def producer():
            """Iteratively pull from generator in thread pool"""
            try:
                def create_generator():
                    """Create generator that yields segments"""
                    return self._smart_split_for_bulk_sync(text, target_segments)
                
                # Run generator creation in thread
                gen = await anyio.to_thread.run_sync(create_generator)
                
                # Iteratively pull segments
                idx = 0
                for segment in gen:
                    await queue.put((idx, segment))
                    idx += 1
                    # Yield control periodically
                    if idx % 5 == 0:
                        await asyncio.sleep(0)
                    
            except Exception as e:
                error_container.append(e)
            finally:
                done_event.set()

        producer_task = asyncio.create_task(producer())
        
        try:
            segment_count = 0
            while True:
                try:
                    idx, segment = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield idx, segment
                    segment_count += 1
                    
                    # Periodic memory check
                    if segment_count % 10 == 0:
                        await self._check_and_cleanup_memory()
                        
                except asyncio.TimeoutError:
                    if done_event.is_set() and queue.empty():
                        break
                    if error_container:
                        raise error_container[0]
                    continue
                    
        finally:
            if not producer_task.done():
                producer_task.cancel()
                try:
                    await producer_task
                except asyncio.CancelledError:
                    pass

    def _smart_split_for_bulk_sync(self, text: str, target_segments: int) -> Generator[str, None, None]:
        """
        Returns generator instead of list.
        Yields segments one at a time to avoid holding all in memory.
        """
        import re

        text = (text or "").strip()
        if not text:
            return
        if target_segments <= 1:
            yield text
            return

        para_re = re.compile(r'\n{2,}')
        sent_split_re = re.compile(r'(?<=[.!?])\s+(?=[""\'(\[]?[A-Z0-9])')

        ABBREV = {
            "mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "st.", "vs.",
            "etc.", "e.g.", "i.e.", "fig.", "no.", "pp.", "vol.",
            "jan.", "feb.", "mar.", "apr.", "jun.", "jul.", "aug.", "sep.", "sept.", "oct.", "nov.", "dec."
        }

        def is_abbrev_end(fragment: str) -> bool:
            frag = fragment.strip().lower().rstrip('""\'\'")]}')
            parts = frag.split()
            return bool(parts) and parts[-1] in ABBREV

        def sentence_tokenize(paragraph: str) -> List[str]:
            if not paragraph:
                return []
            pieces = sent_split_re.split(paragraph.strip())
            if len(pieces) <= 1:
                return [paragraph.strip()]
            fixed: List[str] = []
            for piece in pieces:
                piece = piece.strip()
                if not fixed:
                    if piece:
                        fixed.append(piece)
                    continue
                if is_abbrev_end(fixed[-1]):
                    fixed[-1] = (fixed[-1] + " " + piece).strip()
                else:
                    fixed.append(piece)
            return [p for p in fixed if p]

        def _pack_by_chars(chunks: List[str], max_chars: int) -> List[str]:
            out: List[str] = []
            cur: List[str] = []
            cur_len = 0
            for c in chunks:
                c = c.strip()
                if not c:
                    continue
                add = (1 if cur else 0) + len(c)
                if cur_len + add > max_chars and cur:
                    out.append(' '.join(cur).strip())
                    cur = [c]
                    cur_len = len(c)
                else:
                    cur.append(c)
                    cur_len += add
            if cur:
                out.append(' '.join(cur).strip())
            return out

        def chunk_on_word_boundaries(s: str, max_chars: int, backtrack: int = 40, lookahead: int = 20) -> List[str]:
            out: List[str] = []
            i, n = 0, len(s)
            while i < n:
                end = min(i + max_chars, n)
                cut = None

                j = end
                while j > i and j > end - backtrack and not s[j - 1].isspace():
                    j -= 1
                if j > i and s[j - 1].isspace():
                    cut = j

                if cut is None:
                    j = end
                    lim = min(n, end + lookahead)
                    while j < lim and not s[j:j+1].isspace():
                        j += 1
                    if j < n and s[j:j+1].isspace():
                        cut = j

                if cut is None:
                    cut = end

                piece = s[i:cut].strip()
                if piece:
                    out.append(piece)
                i = cut
                while i < n and s[i].isspace():
                    i += 1
            return out

        def gentle_subsplit(long_sentence: str, max_chars: int) -> List[str]:
            s = long_sentence.strip()
            if len(s) <= max_chars:
                return [s]

            paras = re.split(r'\n{2,}', s)
            if len(paras) > 1:
                return _pack_by_chars(paras, max_chars)

            lines = re.split(r'\n+', s)
            if len(lines) > 1:
                return _pack_by_chars(lines, max_chars)

            parts = re.split(r'(?<=[;:—–\-])\s+', s)
            if len(parts) > 1:
                return _pack_by_chars(parts, max_chars)

            parts = re.split(r'(?<=,)\s+', s)
            if len(parts) > 1:
                return _pack_by_chars(parts, max_chars)

            return chunk_on_word_boundaries(s, max_chars)

        def choose_cut_idx(start: int, target_idx: int, end: int, window: int,
                           para_ends: set, sentences: List[str]) -> int:
            lo = max(start, target_idx - window)
            hi = min(end - 1, target_idx + window)

            best_idx = target_idx
            best_score = -1
            best_dist = 10**9

            for i in range(lo, hi + 1):
                sent = sentences[i].rstrip()
                last = sent[-1] if sent else ''
                dist = abs(i - target_idx)

                if i in para_ends:
                    score = 5
                elif last in ('?', '!'):
                    score = 4
                elif last == '.':
                    score = 3
                elif len(sent) > 0 and re.search(r'[;:,—–-]\s*$', sent):
                    score = 2
                else:
                    score = 1

                if (score > best_score or
                    (score == best_score and dist < best_dist) or
                    (score == best_score and dist == best_dist and i <= best_idx)):
                    best_score = score
                    best_idx = i
                    best_dist = dist

            return best_idx

        paragraphs = [p.strip() for p in para_re.split(text) if p.strip()]
        sentences: List[str] = []
        para_ends: set = set()

        for p in paragraphs:
            sents = sentence_tokenize(p)
            if not sents:
                continue
            start_idx = len(sentences)
            sentences.extend(sents)
            para_ends.add(start_idx + len(sents) - 1)

        if not sentences:
            yield text
            return

        k = max(1, min(target_segments, len(sentences)))

        sent_lens = [len(s) for s in sentences]
        total_chars = sum(sent_lens) + (len(sentences) - 1)
        target_chars = total_chars / k
        min_seg = int(target_chars * 0.70)
        max_seg = int(target_chars * 1.45)

        rebuilt = False
        new_sentences: List[str] = []
        for s in sentences:
            if len(s) > max_seg * 1.5:
                parts = gentle_subsplit(s, max_seg)
                new_sentences.extend(parts)
                rebuilt = True
            else:
                new_sentences.append(s)

        if rebuilt:
            sentences = new_sentences
            sent_lens = [len(s) for s in sentences]
            total_chars = sum(sent_lens) + (len(sentences) - 1)
            k = max(1, min(k, len(sentences)))
            target_chars = total_chars / k
            min_seg = int(target_chars * 0.70)
            max_seg = int(target_chars * 1.45)

        prefix = [0]
        for i, L in enumerate(sent_lens):
            add = L + (1 if i > 0 else 0)
            prefix.append(prefix[-1] + add)

        def seg_len(i: int, j: int) -> int:
            if i > j:
                return 0
            return prefix[j + 1] - prefix[i]

        segments: List[str] = []
        cur_start = 0

        for cut_idx in range(1, k):
            j = cur_start
            last_pos_for_cut = len(sentences) - (k - cut_idx)
            while j < last_pos_for_cut and seg_len(cur_start, j) < target_chars:
                j += 1
            j = max(cur_start, min(j, last_pos_for_cut))

            window = 3
            cut_at = choose_cut_idx(cur_start, j, len(sentences) - (k - cut_idx) + 1, window, para_ends, sentences)

            seg_chars = seg_len(cur_start, cut_at)
            if seg_chars < min_seg:
                while cut_at + 1 <= last_pos_for_cut and seg_len(cur_start, cut_at) < min_seg:
                    cut_at += 1
            elif seg_chars > max_seg:
                while cut_at - 1 >= cur_start and seg_len(cur_start, cut_at) > max_seg:
                    cut_at -= 1

            segments.append(' '.join(sentences[cur_start:cut_at + 1]).strip())
            cur_start = cut_at + 1

        if cur_start < len(sentences):
            segments.append(' '.join(sentences[cur_start:]).strip())

        if len(segments) < k:
            while len(segments) < k:
                largest_idx = max(range(len(segments)), key=lambda i: len(segments[i]))
                largest = segments[largest_idx]
                para_splits = [m.start() for m in re.finditer(r'\n\s*\n', largest)]
                if para_splits:
                    mid = para_splits[len(para_splits) // 2]
                    left, right = largest[:mid].strip(), largest[mid:].strip()
                    if left and right:
                        segments[largest_idx] = left
                        segments.insert(largest_idx + 1, right)
                        continue
                largest_sents = sent_split_re.split(largest)
                if len(largest_sents) > 1:
                    mid_s = len(largest_sents) // 2
                    left = ' '.join(largest_sents[:mid_s]).strip()
                    right = ' '.join(largest_sents[mid_s:]).strip()
                    if left and right:
                        segments[largest_idx] = left
                        segments.insert(largest_idx + 1, right)
                        continue
                parts = chunk_on_word_boundaries(largest, max_seg)
                if len(parts) > 1:
                    segments[largest_idx] = parts[0]
                    segments.insert(largest_idx + 1, parts[1])
                else:
                    break

        elif len(segments) > k:
            while len(segments) > k:
                sizes = [len(s) for s in segments]
                idx = min(range(len(segments) - 1), key=lambda i: sizes[i] + sizes[i + 1])
                segments[idx:idx + 2] = [' '.join(segments[idx:idx + 2]).strip()]

        # Yield instead of return
        for segment in segments:
            if segment:
                yield segment

    def can_user_start_generation(self, user: User, track_id: str, voice_id: str) -> Tuple[bool, Optional[str]]:
        user_id = user.id
        current_active = self.user_active_generations.get(user_id, set())
        user_limit = self.max_concurrent_per_creator if user.is_creator else self.max_concurrent_per_user
        
        generation_key = f"{track_id}:{voice_id}"
        
        if generation_key in current_active:
            return False, "voice generating. Please wait for it to finish."
        
        if len(current_active) >= user_limit:
            if user.is_creator:
                return False, f"You have reached your limit of {user_limit} concurrent voice generations. Please wait for one to complete."
            else:
                return False, "You already have a voice generating. Please wait for it to finish, then pick another."
        
        return True, None

    def start_user_generation(self, user_id: int, track_id: str, voice_id: str):
        generation_key = f"{track_id}:{voice_id}"
        
        if user_id not in self.user_active_generations:
            self.user_active_generations[user_id] = set()
        
        self.user_active_generations[user_id].add(generation_key)

    def complete_user_generation(self, user_id: int, track_id: str, voice_id: str):
        generation_key = f"{track_id}:{voice_id}"
        
        if user_id in self.user_active_generations:
            self.user_active_generations[user_id].discard(generation_key)
            
            if len(self.user_active_generations[user_id]) == 0:
                del self.user_active_generations[user_id]

    def try_start_generation_atomic(self, user: User, track_id: str, voice_id: str) -> Tuple[bool, Optional[str]]:
        can_start, error_msg = self.can_user_start_generation(user, track_id, voice_id)
        if not can_start:
            return False, error_msg
        
        self.start_user_generation(user.id, track_id, voice_id)
        return True, None

    async def _generate_chunk_audio_to_file(
        self,
        chunk_text: str,
        voice: str,
        output_path: Path,
        chunk_index: int
    ) -> Tuple[float, Path, int]:
        """Generate audio with Edge TTS - streaming to file (strict, original logs)"""
        await _amkdir(output_path.parent)

        chunk_words = len(chunk_text.split())
        logger.info(f"CHUNK-START [{chunk_index}]: {chunk_words} words → Edge TTS ({voice})")

        for attempt in range(self.max_retry_attempts):
            try:
                await asyncio.sleep(0.15)

                async with EDGE_TTS_SEMAPHORE:
                    t0 = time.time()

                    # Keep the exact casing that works in your 7.2.1 env
                    communicate = edge_tts.Communicate(
                        chunk_text.strip(),
                        voice,
                        boundary="WordBoundary"
                    )

                    word_boundaries: List[Dict] = []
                    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
                    file_size = 0

                    async with aiofiles.open(tmp_path, "wb") as f:
                        async for ev in communicate.stream():
                            ev_type = ev.get("type")
                            # Uncomment if you want raw event visibility per chunk:
                            # logger.debug(f"EVENT [{chunk_index}]: {ev_type}")

                            if ev_type == "audio":
                                data = ev.get("data")
                                if data:
                                    await f.write(data)
                                    file_size += len(data)

                            elif ev_type == "WordBoundary":
                                start = ev["offset"] / 10_000_000
                                dur   = ev["duration"] / 10_000_000
                                end   = start + dur
                                word_boundaries.append({
                                    "word": ev.get("text", ""),
                                    "start_time": start,
                                    "end_time": end,
                                    "duration": dur,
                                    "chunk_index": chunk_index,
                                    "voice": voice,
                                    "boundary_type": "real_word",
                                    "timing_source": "edge_tts_native_word",
                                })

                    if file_size == 0:
                        raise RuntimeError(f"No audio data returned for chunk {chunk_index}")

                    # STRICT: must have real word boundaries
                    if not word_boundaries:
                        raise RuntimeError(f"No word boundaries returned for chunk {chunk_index}")

                    # Move temp → final atomically
                    try:
                        await _areplace(tmp_path, output_path)
                    except Exception:
                        if await _aexists(output_path):
                            await _aunlink(output_path)
                        await _arename(tmp_path, output_path)

                    actual_duration = await self._measure_audio_duration(output_path)
                    if actual_duration <= 0:
                        raise RuntimeError(f"Duration measurement failed for chunk {chunk_index}")

                    # Normalize & clamp to file duration
                    wb = []
                    last_end = -1.0
                    for w in word_boundaries:
                        if w["end_time"] >= w["start_time"] and w["start_time"] >= last_end - 1e-6:
                            wb.append(w)
                            last_end = w["end_time"]
                    word_boundaries = wb

                    eps = 0.050
                    pruned = []
                    for w in word_boundaries:
                        if w["end_time"] > actual_duration + eps:
                            continue
                        if w["end_time"] > actual_duration:
                            w["end_time"] = actual_duration
                            w["duration"] = max(0.0, w["end_time"] - w["start_time"])
                        if w["duration"] > 0:
                            pruned.append(w)
                    word_boundaries = pruned

                    if not word_boundaries:
                        raise RuntimeError(f"Word boundaries invalid after normalization for chunk {chunk_index}")

                    # Write timings JSON
                    timing_data = {
                        "chunk_index": chunk_index,
                        "duration": actual_duration,
                        "word_boundaries": word_boundaries
                    }
                    temp_timings = output_path.with_suffix(".timings.json.tmp")
                    async with aiofiles.open(temp_timings, "w") as tf:
                        await tf.write(json.dumps(timing_data))
                        await tf.flush()
                    final_timings = output_path.with_suffix(".timings.json")
                    await _arename(temp_timings, final_timings)
                    if not await _aexists(final_timings):
                        raise RuntimeError(f"Timing file not found after creation: {final_timings}")

                    wall = time.time() - t0
                    logger.info(
                        f"CHUNK-DONE  [{chunk_index}]: "
                        f"{actual_duration:.2f}s audio, {len(word_boundaries)} word boundaries, "
                        f"{file_size/1024:.1f} KiB, {wall:.2f}s wall"
                    )
                    return actual_duration, final_timings, file_size

            except Exception as e:
                msg = str(e).lower()
                # Same retry semantics you had before
                retryable_markers = [
                    "rate limit", "throttle", "too many requests", "quota", "429",
                    "service unavailable", "connection", "timeout", "temporary",
                    "busy", "overload", "tls", "reset by peer"
                ]
                is_retryable = any(k in msg for k in retryable_markers)

                if is_retryable and attempt < self.max_retry_attempts - 1:
                    base = self.initial_retry_delay * (self.retry_backoff_multiplier ** attempt)
                    delay = base * (0.85 + 0.30 * random.random())
                    logger.warning(
                        f"CHUNK-RETRY [{chunk_index}] {attempt+1}/{self.max_retry_attempts}: {e} → sleep {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
                    continue

                logger.error(f"CHUNK-FAIL  [{chunk_index}] after {attempt+1} attempts: {e}")
                # Cleanup partial artifacts
                try:
                    if await _aexists(output_path): await _aunlink(output_path)
                    t = output_path.with_suffix(output_path.suffix + ".tmp")
                    if await _aexists(t): await _aunlink(t)
                    tjs = output_path.with_suffix(".timings.json.tmp")
                    if await _aexists(tjs): await _aunlink(tjs)
                    fjs = output_path.with_suffix(".timings.json")
                    if await _aexists(fjs): await _aunlink(fjs)
                except Exception:
                    pass
                raise

    async def _measure_audio_duration(self, audio_path: Path) -> float:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path),
                stdout=asyncio.subprocess.PIPE, 
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await proc.communicate()
            
            if proc.returncode == 0:
                duration_str = stdout.decode().strip()
                if duration_str:
                    return float(duration_str)
            else:
                logger.warning(f"ffprobe failed for {audio_path}: {stderr.decode()}")
                
        except Exception as e:
            logger.warning(f"Error measuring audio duration for {audio_path}: {e}")
        
        try:
            file_size = await _astat_size(audio_path)
            # Fixed bitrate estimate to match 64 kbps
            estimated_duration = file_size / 8000  # 64 kbps = 8000 bytes/sec
            return max(estimated_duration, 0.5)
        except Exception:
            return 1.0

    async def _generate_chunks_in_parallel(
        self,
        chunks: str,
        voice: str,
        session_dir: Path,
        user: User,
        job_id: str,
        progress_callback=None,
        lock_key: Optional[str] = None
    ) -> Tuple[List[Path], float, Path]:
        """
        MODIFIED: Dynamic worker count based on job's allocated quota.
        Workers scale with user pool size (determined by fair limiter).
        """
        try:
            text_content = chunks
            track_id = session_dir.name.split('_')[1] if '_' in session_dir.name else "unknown"
            
            estimated_chunks = max(1, len(text_content) // 2400)
            
            # ✅ Get job's actual allocated quota from limiter
            user_limiter = await self.user_job_manager._get_user_limiter(user.id)
            job_quota = user_limiter.desired.get(job_id, 1)
            
            # ✅ Workers = quota (capped at MAX for safety)
            # No artificial minimum - trust the fair limiter to allocate resources
            worker_count = min(MAX_WORKERS_PER_JOB, max(1, job_quota))
            
            logger.info(
                f"TTS-START [{track_id}]: ~{estimated_chunks} chunks | {voice} | "
                f"{worker_count} workers (allocated: {job_quota})"
            )

            completed_chunks = 0
            failed_chunks = 0
            generation_start = time.time()

            chunk_queue = asyncio.Queue(maxsize=CHUNK_BATCH_SIZE)
            
            async def producer():
                chunk_index = 0
                async for chunk_text in self._split_text_into_chunks(text_content):
                    await chunk_queue.put((chunk_index, chunk_text))
                    chunk_index += 1
                    del chunk_text
                
                # ✅ Send None signals based on actual worker count
                for _ in range(worker_count):
                    await chunk_queue.put(None)
                
                return chunk_index
            
            producer_task = asyncio.create_task(producer())

            results = []
            results_lock = asyncio.Lock()
            total_chunks_ref = [0]

            async def worker(worker_id: int):
                nonlocal completed_chunks, failed_chunks
                processed_count = 0

                while True:
                    item = await chunk_queue.get()
                    
                    if item is None:
                        break
                    
                    chunk_index, chunk_text = item

                    if job_id in self._cancelled_jobs:
                        raise asyncio.CancelledError(f"Job {job_id} was cancelled")

                    chunk_start = time.time()

                    async with self.user_job_manager.slot(user.id, job_id):
                        if job_id in self._cancelled_jobs:
                            raise asyncio.CancelledError(f"Job {job_id} was cancelled")

                        try:
                            chunk_file = session_dir / f"chunk_{chunk_index:04d}_{voice}.mp3"

                            duration, timings_path, file_size = await self._generate_chunk_audio_to_file(
                                chunk_text=chunk_text,
                                voice=voice,
                                output_path=chunk_file,
                                chunk_index=chunk_index
                            )

                            chunk_time = time.time() - chunk_start
                            completed_chunks += 1
                            processed_count += 1

                            del chunk_text
                            await asyncio.sleep(0)

                            if total_chunks_ref[0] > 0:
                                progress_pct = completed_chunks / total_chunks_ref[0]
                                elapsed = time.time() - generation_start
                                avg_per_chunk = elapsed / completed_chunks
                                eta = avg_per_chunk * (total_chunks_ref[0] - completed_chunks)
                                
                                if chunk_index % 5 == 0 or chunk_index < 3:
                                    logger.info(
                                        f"TTS [{track_id}] {completed_chunks}/{total_chunks_ref[0]} ({int(progress_pct*100)}%) | "
                                        f"ETA: {int(eta)}s | Worker-{worker_id}"
                                    )

                            if progress_callback:
                                progress_callback(completed_chunks, total_chunks_ref[0] or estimated_chunks)

                            if lock_key and total_chunks_ref[0] > 0:
                                chunk_progress = 20 + (60 * completed_chunks / total_chunks_ref[0])
                                self._update_voice_progress(
                                    lock_key,
                                    chunk_progress,
                                    'generating',
                                    f'Generated {completed_chunks}/{total_chunks_ref[0]} chunks...',
                                    chunks_completed=completed_chunks,
                                    total_chunks=total_chunks_ref[0]
                                )

                            async with results_lock:
                                results.append({
                                    'index': chunk_index,
                                    'file': chunk_file,
                                    'duration': duration,
                                    'timings_path': timings_path,
                                    'size': file_size
                                })

                            if processed_count % GC_FREQUENCY == 0:
                                collected, mem_pct = await _force_garbage_collection()
                                if collected > 0:
                                    logger.debug(f"Worker-{worker_id}: GC freed {collected} objects (mem: {mem_pct:.1f}%)")

                        except asyncio.CancelledError:
                            raise
                        except Exception as chunk_error:
                            failed_chunks += 1
                            logger.error(f"Chunk {chunk_index} FAILED: {str(chunk_error)}")
                            raise

                logger.info(f"Worker-{worker_id} finished, processed {processed_count} chunks")

            # ✅ Spawn dynamic number of workers based on quota
            workers = [
                asyncio.create_task(worker(i), name=f"chunk_worker_{i}")
                for i in range(worker_count)
            ]

            try:
                total_chunks = await producer_task
                total_chunks_ref[0] = total_chunks
                logger.info(f"TTS [{track_id}]: Producer finished, {total_chunks} chunks total")
                
                await asyncio.gather(*workers)

                if lock_key:
                    self._update_voice_progress(
                        lock_key,
                        80,
                        'concatenating',
                        'All chunks generated, combining audio...',
                        chunks_completed=total_chunks,
                        total_chunks=total_chunks
                    )

            except asyncio.CancelledError:
                logger.warning(f"TTS-CANCELLED [{track_id}]")
                for i in range(total_chunks_ref[0] or estimated_chunks):
                    chunk_file = session_dir / f"chunk_{i:04d}_{voice}.mp3"
                    if await _aexists(chunk_file):
                        try:
                            await _aunlink(chunk_file)
                        except Exception:
                            pass
                raise

            except Exception as gather_error:
                logger.error(f"TTS-GATHER-ERROR [{track_id}]: {str(gather_error)}")
                for i in range(total_chunks_ref[0] or estimated_chunks):
                    chunk_file = session_dir / f"chunk_{i:04d}_{voice}.mp3"
                    if await _aexists(chunk_file):
                        try:
                            await _aunlink(chunk_file)
                        except Exception:
                            pass
                raise
            finally:
                for w in workers:
                    if not w.done():
                        w.cancel()
                await asyncio.gather(*workers, return_exceptions=True)

            results.sort(key=lambda x: x['index'])
            chunk_files = [r['file'] for r in results]

            if lock_key:
                self._update_voice_progress(
                    lock_key,
                    85,
                    'organizing',
                    'Organizing timing data...'
                )

            timings_dir = session_dir / "timings"
            await _amkdir(timings_dir)

            total_duration = sum(r['duration'] for r in results)

            for idx, result in enumerate(results):
                if lock_key and idx % 10 == 0:
                    timing_progress = 85 + (5 * idx / len(results))
                    self._update_voice_progress(
                        lock_key,
                        timing_progress,
                        'organizing',
                        f'Organizing timing files ({idx}/{len(results)})...'
                    )

                timing_src = result.get('timings_path')

                if not timing_src or not isinstance(timing_src, Path):
                    raise TypeError(f"Invalid timings_path in result {idx}")

                if not await _aexists(timing_src):
                    raise FileNotFoundError(f"Timing file missing: {timing_src}")

                timing_dst = timings_dir / f"chunk_{result['index']:04d}.json"
                await _arename(timing_src, timing_dst)

                if idx % 20 == 0:
                    await _force_garbage_collection()

            if lock_key:
                self._update_voice_progress(
                    lock_key,
                    90,
                    'finalizing',
                    'Timing data organized, finalizing...'
                )

            generation_time = time.time() - generation_start
            speed_ratio = total_duration / generation_time if generation_time > 0 else 0

            if lock_key:
                self._update_voice_progress(
                    lock_key,
                    95,
                    'finalizing',
                    'Generation complete, preparing output...'
                )

            logger.info(
                f"TTS-DONE [{track_id}]: {total_chunks} chunks | "
                f"{total_duration:.1f}s audio in {generation_time:.1f}s ({speed_ratio:.1f}x) | "
                f"{worker_count} workers"
            )

            if not await _aexists(timings_dir):
                raise RuntimeError(f"Timings directory not created: {timings_dir}")

            timing_files = list(timings_dir.glob("chunk_*.json"))
            if len(timing_files) != total_chunks:
                raise RuntimeError(
                    f"Timing file count mismatch: expected {total_chunks}, "
                    f"found {len(timing_files)}"
                )

            if lock_key:
                self._update_voice_progress(
                    lock_key,
                    100,
                    'complete',
                    'Voice generation complete'
                )

            return chunk_files, total_duration, timings_dir

        except asyncio.CancelledError:
            logger.info(f"TTS generation cancelled for {track_id}")
            raise
        except Exception as e:
            logger.error(f"TTS-FAIL: {str(e)}")
            raise


    async def _concatenate_audio_files(
        self, 
        chunk_files: List[Path], 
        output_path: Path,
        voice: str
    ) -> int:
        """Concatenate audio files using ffmpeg"""
        try:
            missing_files = []
            for i, chunk_file in enumerate(chunk_files):
                if not await _aexists(chunk_file):
                    missing_files.append(i)
            
            if missing_files:
                raise RuntimeError(f"Missing {len(missing_files)} chunk files")
            
            concat_list_path = output_path.parent / f"concat_list_{voice}_{uuid.uuid4().hex[:8]}.txt"
            
            # Properly escape paths for ffmpeg concat demuxer
            async with aiofiles.open(concat_list_path, 'w') as f:
                for chunk_file in chunk_files:
                    safe_path = str(chunk_file.absolute())
                    # Escape backslashes first, then single quotes
                    safe_path = safe_path.replace('\\', '\\\\').replace("'", r"'\''")
                    await f.write(f"file '{safe_path}'\n")
            
            file_count = len(chunk_files)
            timeout = max(60, min(1200, (60 + file_count * 0.5) * 2))
            
            cmd = [
                'ffmpeg', '-y',
                '-f', 'concat',
                '-safe', '0',
                '-i', str(concat_list_path),
                '-c:a', 'copy',
                '-avoid_negative_ts', 'make_zero',
                str(output_path)
            ]
            
            async with FFMPEG_SEMAPHORE:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE
                )
                
                try:
                    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
                    
                    if process.returncode != 0:
                        error_msg = stderr.decode() if stderr else "Unknown ffmpeg error"
                        logger.error(f"FFmpeg failed: {error_msg}")
                        raise RuntimeError(f"FFmpeg concatenation failed: {error_msg}")
                
                except asyncio.TimeoutError:
                    logger.error(f"FFmpeg concatenation timeout after {timeout:.0f}s")
                    process.kill()
                    raise RuntimeError(f"FFmpeg concatenation timed out after {timeout:.0f}s")
            
            if not await _aexists(output_path):
                raise RuntimeError("FFmpeg did not create output file")
            
            final_size = await _astat_size(output_path)
            
            try:
                await _aunlink(concat_list_path)
            except Exception:
                pass
            
            # GC after concat
            collected, mem_pct = await _force_garbage_collection()
            if collected > 0:
                logger.debug(f"Post-concat: GC freed {collected} objects (mem: {mem_pct:.1f}%)")
            
            return final_size
        
        except Exception as e:
            logger.error(f"Error in concatenation: {str(e)}")
            
            if 'concat_list_path' in locals() and await _aexists(concat_list_path):
                try:
                    await _aunlink(concat_list_path)
                except Exception:
                    pass
            
            raise

    async def _store_text_chunks(self, track_id: str, chunks: List[str], full_text: str, voice: str, db):
        """Store text in file storage, update minimal track meta in DB"""
        try:
            await self.text_service.store_source_text(track_id, full_text, db)

            if not db:
                return

            res = await _exec(db, select(TTSTrackMeta).where(TTSTrackMeta.track_id == track_id))
            track_meta = res.scalar_one_or_none()

            if not track_meta:
                word_count = await anyio.to_thread.run_sync(lambda: len(full_text.split()))
                estimated_duration = (word_count / self.words_per_minute) * 60.0
                words_per_seg = word_count // max(1, len(chunks))
                
                track_meta = TTSTrackMeta(
                    track_id=track_id,
                    default_voice=voice,
                    available_voices=[voice],
                    total_words=word_count,
                    total_characters=len(full_text),
                    total_segments=len(chunks),
                    total_duration=estimated_duration,
                    segment_duration=30.0,
                    words_per_segment=words_per_seg,
                    processing_status="processing",
                    started_at=datetime.now(timezone.utc),
                )
                db.add(track_meta)
                await _flush(db)
            else:
                if track_meta.total_words:
                    word_count = track_meta.total_words
                else:
                    word_count = await anyio.to_thread.run_sync(lambda: len(full_text.split()))
                    track_meta.total_words = word_count
                
                track_meta.total_segments = len(chunks)
                track_meta.words_per_segment = word_count // max(1, len(chunks))

            await _commit(db)

        except TextStorageError as e:
            logger.error(f"File storage failed for {track_id}: {e}")
            if db:
                await _rollback(db)
            raise ValueError(f"Text storage failed: {e}")
        except Exception as e:
            logger.error(f"Error storing text chunks: {str(e)}")
            if db:
                await _rollback(db)
            raise

    async def _get_stored_text_chunks(self, track_id: str, db, bypass_cache: bool = False) -> List[str]:
        """Get text chunks from file storage"""
        try:
            full_text = await self.text_service.get_source_text(track_id, db, bypass_cache=bypass_cache)
            if full_text:
                return await self._split_text_into_chunks(full_text)
            
            raise ValueError(f"No text found in file storage for track {track_id}")
            
        except TextStorageError as e:
            logger.error(f"Text storage error for {track_id}: {e}")
            raise ValueError(f"Text retrieval failed: {e}")
        except Exception as e:
            logger.error(f"Error retrieving text chunks for {track_id}: {str(e)}")
            raise ValueError(f"Text retrieval failed: {str(e)}")

    async def _store_voice_word_timings(self, track_id: str, voice: str, word_timings: List[Dict], duration: float, db):
        """
        FIX #6: Store word timings without full verification.
        Trust atomic storage and only update DB metadata.
        """
        try:
            if not word_timings:
                raise ValueError(f"Cannot store empty word timings for {track_id}:{voice}")
            
            # Single write; storage layer should be atomic (tmp + replace) and raise on failure
            await self.text_service.store_word_timings(track_id, voice, word_timings, db)
            
            # Optional: very cheap existence check (no content read)
            # if hasattr(self.text_service, "word_timings_exists"):
            #     ok = await self.text_service.word_timings_exists(track_id, voice, db)
            #     if not ok:
            #         raise ValueError("Word timings file not found after store")
            
            # DB metadata only (no large reads)
            if not db:
                return
            
            res = await _exec(db, select(TTSTrackMeta).where(TTSTrackMeta.track_id == track_id))
            track_meta = res.scalar_one_or_none()
            
            if track_meta:
                if voice not in (track_meta.available_voices or []):
                    # ensure list exists
                    track_meta.available_voices = (track_meta.available_voices or []) + [voice]
                
                if not track_meta.default_voice:
                    track_meta.default_voice = voice
                
                # Keep duration up to date and mark ready
                track_meta.total_duration = max(track_meta.total_duration or 0, float(duration or 0))
                track_meta.processing_status = 'ready'
                track_meta.completed_at = datetime.now(timezone.utc)
                track_meta.progress_percentage = 100.0
                
                # Optional: store lightweight stats for observability (no heavy reads)
                # track_meta.word_timings_count = len(word_timings)
                
                await _commit(db)
        
        except TextStorageError as e:
            logger.error(f"Word timing storage failed for {track_id}:{voice}: {e}")
            if db:
                await _rollback(db)
            raise ValueError(f"Word timing storage failed: {e}")
        except Exception as e:
            logger.error(f"Error storing word timings for {track_id}:{voice}: {e}")
            if db:
                await _rollback(db)
            raise

    async def get_voice_word_timings(self, track_id: str, voice: str, db = None) -> List[Dict]:
        """Get word timings from file storage"""
        try:
            word_timings = await self.text_service.get_word_timings(track_id, voice, db)
            return word_timings
            
        except TextStorageError as e:
            logger.error(f"Could not retrieve word timings from files for {track_id}:{voice}: {e}")
            raise ValueError(f"Word timing retrieval failed: {e}")

    async def _create_bulk_tts_series(
        self,
        base_track_id: str,
        series_title: str,
        text_content: str,
        voice: str,
        split_count: int,
        db,
        user: User,
        album_id: Optional[str] = None,
        starting_order: int = 0,
        visibility_status: str = "visible"
    ) -> Dict:
        """Bulk TTS with streaming segments and batch DB operations"""
        import threading
        import asyncio
        from database import AsyncSessionLocal
        
        char_count = len(text_content or "")
        logger.info(f"BULK-START: {base_track_id} | {split_count} parts | {char_count:,} chars | {voice}")

        if char_count > 5_000_000:
            raise ValueError("Text content exceeds 5 million character limit")
        if char_count < 10_000:
            raise ValueError("Text too short for bulk generation (minimum 10k characters)")

        bulk_queue_id = f"bulk_{user.id}_{uuid.uuid4().hex[:8]}"
        bulk_metadata = {
            "bulk_queue_id": bulk_queue_id,
            "series_title": series_title,
            "total_segments": split_count,
            "completed_segments": 0,
            "failed_segments": 0,
            "voice": voice,
            "started_at": time.time(),
            "status": "queued",
            "user_id": user.id,
        }

        async with self._bulk_lock:
            self.bulk_jobs[bulk_queue_id] = bulk_metadata

        starting_order_calculated = starting_order
        if starting_order_calculated == 0 and album_id:
            try:
                from models import Track
                async with AsyncSessionLocal() as order_db:
                    res = await order_db.execute(
                        select(func.max(Track.order)).where(Track.album_id == album_id)
                    )
                    max_order = res.scalar()
                    starting_order_calculated = (max_order + 1) if max_order is not None else 0
            except Exception as e:
                logger.error(f"Error calculating starting order: {e}")
                starting_order_calculated = 0

        pad = len(str(split_count))
        
        # Batch DB operations
        batch_size = 10
        created_track_ids = []
        
        async def create_track_batch(batch_start: int, batch_end: int):
            """Create a batch of track records"""
            batch_placeholders = []
            
            for i in range(batch_start, min(batch_end, split_count)):
                part_num = i + 1
                part_str = f"{part_num:0{pad}d}"
                segment_track_id = f"{base_track_id}_part_{part_str}"
                segment_title = f"{series_title} - Part {part_str} of {split_count}"
                
                placeholder = {
                    "id": segment_track_id,
                    "title": segment_title,
                    "album_id": album_id,
                    "created_by_id": user.id,
                    "track_type": 'tts',
                    "has_read_along": True,
                    "default_voice": voice,
                    "file_path": f"/tts/{segment_track_id}/voice-{voice}/complete.mp3",
                    "source_text_path": f"/text/{segment_track_id}.txt",
                    "upload_status": 'processing',
                    "status": 'processing',
                    "segmentation_status": 'incomplete',
                    "tts_status": 'queued',
                    "tts_progress": 0,
                    "format": 'mp3',
                    "codec": 'mp3',
                    "bit_rate": 64000,
                    "sample_rate": 24000,
                    "channels": 1,
                    "visibility_status": visibility_status,
                    "audio_metadata": {
                        'approach': 'bulk_generation',
                        'voice': voice,
                        'voice_directory': f"voice-{voice}",
                        'supports_voice_switching': True,
                        'is_bulk_part': True,
                        'bulk_queue_id': bulk_queue_id,
                        'segment_index': i,
                        'bulk_total_parts': split_count,
                        'bulk_sort_key': part_str,
                    },
                    "tier_requirements": {
                        "is_public": True,
                        "minimum_cents": 0,
                        "allowed_tier_ids": []
                    },
                    "access_count": 0,
                    "order": starting_order_calculated + i,
                }
                
                batch_placeholders.append((segment_track_id, placeholder))
            
            # Batch insert with expunge
            async with AsyncSessionLocal() as batch_db:
                from models import Track
                
                track_objects = []
                for track_id, placeholder in batch_placeholders:
                    try:
                        track_obj = await anyio.to_thread.run_sync(
                            lambda p=placeholder: Track(**p)
                        )
                        track_objects.append(track_obj)
                    except Exception as e:
                        logger.error(f"Failed to create Track object for {track_id}: {e}")
                        raise
                
                try:
                    batch_db.add_all(track_objects)
                    await batch_db.commit()
                    logger.info(f"BULK: Committed batch {batch_start//batch_size + 1}: {len(track_objects)} tracks")
                except Exception as e:
                    logger.error(f"Database commit failed for batch: {e}")
                    await batch_db.rollback()
                    raise
                
                # Expunge to free ORM memory
                batch_db.expunge_all()
            
            return [track_id for track_id, _ in batch_placeholders]

        # Parallel track creation
        batch_tasks = []
        for i in range(0, split_count, batch_size):
            task = create_track_batch(i, i + batch_size)
            batch_tasks.append(task)
        
        max_parallel_batches = 3
        for i in range(0, len(batch_tasks), max_parallel_batches):
            batch_group = batch_tasks[i:i + max_parallel_batches]
            results = await asyncio.gather(*batch_group, return_exceptions=True)
            
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Batch creation failed: {result}")
                    raise result
                created_track_ids.extend(result)
            
            await _force_garbage_collection()
        
        logger.info(f"BULK: Created {len(created_track_ids)} track records")

        # Stream and store text segments
        async def store_text_segment(segment_index: int, segment_text: str, segment_track_id: str):
            """Store a single text segment"""
            try:
                async with AsyncSessionLocal() as text_db:
                    await self.text_service.store_source_text(segment_track_id, segment_text, text_db)
                    return segment_track_id
            except Exception as e:
                logger.error(f"Failed to store text for segment {segment_index}: {e}")
                raise

        storage_tasks = []
        segment_count = 0
        max_parallel_storage = 5
        
        # TRUE streaming - segments yielded one at a time
        async for segment_index, segment_text in self._smart_split_for_bulk_streaming(text_content, split_count):
            if segment_index >= len(created_track_ids):
                break
            
            segment_track_id = created_track_ids[segment_index]
            
            task = store_text_segment(segment_index, segment_text, segment_track_id)
            storage_tasks.append(task)
            
            # Free segment text immediately
            del segment_text
            
            if len(storage_tasks) >= max_parallel_storage:
                await asyncio.gather(*storage_tasks)
                segment_count += len(storage_tasks)
                logger.info(f"BULK: {segment_count}/{split_count} text segments stored")
                storage_tasks = []
                
                await _force_garbage_collection()
        
        if storage_tasks:
            await asyncio.gather(*storage_tasks)
            segment_count += len(storage_tasks)
            logger.info(f"BULK: {segment_count}/{split_count} text segments stored")
        
        # Free large text content
        del text_content
        collected, mem_pct = await _force_garbage_collection()
        logger.debug(f"Bulk text freed: GC collected {collected} objects (mem: {mem_pct:.1f}%)")

        track_jobs = []
        for i, track_id in enumerate(created_track_ids):
            part_num = i + 1
            part_str = f"{part_num:0{pad}d}"
            segment_title = f"{series_title} - Part {part_str} of {split_count}"
            
            job = {
                "track_id": track_id,
                "title": segment_title,
                "voice": voice,
                "segment_index": i,
                "track_order": starting_order_calculated + i,
                "bulk_queue_id": bulk_queue_id,
                "bulk_total_parts": split_count,
                "bulk_sort_key": part_str,
                "album_id": album_id,
            }
            
            track_jobs.append(job)
        
        if not hasattr(self, '_bulk_tasks'):
            self._bulk_tasks = {}
        
        try:
            task = asyncio.create_task(
                self._process_bulk_queue(
                    bulk_queue_id=bulk_queue_id,
                    track_jobs=track_jobs,
                    db=None,
                    user=user,
                ),
                name=f"bulk_tts_{bulk_queue_id}"
            )
            
            self._bulk_tasks[bulk_queue_id] = task
            
            def task_done_callback(completed_task):
                try:
                    completed_task.result()
                    logger.info(f"Bulk task {bulk_queue_id} completed")
                except asyncio.CancelledError:
                    logger.warning(f"Bulk task {bulk_queue_id} was cancelled")
                except Exception as e:
                    logger.error(f"Bulk task {bulk_queue_id} failed: {e}")
                finally:
                    if bulk_queue_id in self._bulk_tasks:
                        del self._bulk_tasks[bulk_queue_id]

            task.add_done_callback(task_done_callback)
            
        except Exception as e:
            logger.error(f"Failed to create background task: {e}")
            raise ValueError(f"Failed to spawn background processing task: {e}")
        
        # ✅ FETCH CREATED TRACKS TO RETURN THEIR DATA
        # Give DB a moment to ensure all commits are complete
        await asyncio.sleep(0.15)
        
        tracks_data = []
        try:
            logger.info(f"BULK: Attempting to fetch {len(created_track_ids)} tracks for response")
            logger.info(f"BULK: Searching for track IDs: {created_track_ids[:3]}... (showing first 3)")
            
            async with AsyncSessionLocal() as response_db:
                from models import Track as TrackModel
                
                result = await response_db.execute(
                    select(TrackModel).where(TrackModel.id.in_(created_track_ids))
                )
                created_tracks = result.scalars().all()
                
                logger.info(f"BULK: Database returned {len(created_tracks)} tracks")
                
                if not created_tracks:
                    logger.error(f"BULK: ⚠️ No tracks found in database! Expected {len(created_track_ids)}")
                    # Try to fetch one by one to debug
                    for tid in created_track_ids[:3]:
                        check = await response_db.get(TrackModel, tid)
                        logger.info(f"BULK: Individual check for {tid}: {check is not None}")
                else:
                    logger.info(f"BULK: ✅ Successfully fetched {len(created_tracks)} tracks from DB")
                
                tracks_data = [
                    {
                        "id": track.id,
                        "title": track.title,
                        "duration": track.duration or 0,
                        "tts_status": track.tts_status,
                        "upload_status": track.upload_status,
                        "status": "processing",
                        "track_type": "tts",
                        "has_read_along": track.has_read_along,
                        "default_voice": track.default_voice,
                        "order": track.order,
                        "file_path": track.file_path,
                        "is_tts_track": True,
                    }
                    for track in created_tracks
                ]
                
                logger.info(f"BULK: Successfully formatted {len(tracks_data)} track records for response")
                
        except Exception as e:
            logger.error(f"BULK: ❌ Failed to fetch track data for response: {e}", exc_info=True)
            # Continue anyway - tracks are created, just can't return data
        
        logger.info(f"BULK: Returning response with {len(tracks_data)} tracks in 'tracks' array")
        
        response = {
            "status": "bulk_queued",
            "bulk_queue_id": bulk_queue_id,
            "total_tracks": split_count,
            "series_title": series_title,
            "tracks_queued": created_track_ids,
            "tracks": tracks_data,  # ✅ ADD THIS
        }
        
        logger.info(f"BULK-COMPLETE: Created {split_count} tracks for {base_track_id}, returning {len(tracks_data)} in response")
        
        return response
    async def _process_bulk_queue(self, bulk_queue_id: str, track_jobs: List[Dict], db, user: User):
        """Process bulk TTS queue with memory optimization"""
        from database import AsyncSessionLocal, get_db
        from storage import storage
        from sqlalchemy import select
        from track_status_manager import TrackStatusManager

        logger.info(f"BULK-PROCESS: {bulk_queue_id} | {len(track_jobs)} tracks")

        async with self._bulk_lock:
            bulk_metadata = self.bulk_jobs.get(bulk_queue_id)
            if not bulk_metadata:
                logger.error(f"Bulk queue {bulk_queue_id} not found")
                return
            bulk_metadata['status'] = 'processing'

        max_workers = min(
            len(track_jobs),
            self.max_concurrent_per_creator if getattr(user, "is_creator", False) else self.max_concurrent_per_user
        ) or 1

        logger.info(f"BULK-PROCESS: Starting {max_workers} workers")

        job_queue = asyncio.Queue()
        for job in track_jobs:
            await job_queue.put(job)

        async def worker(worker_id: int):
            processed_count = 0
            
            while True:
                try:
                    track_job = await asyncio.wait_for(job_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    if job_queue.empty():
                        break
                    continue

                track_id = track_job['track_id']
                voice = track_job['voice']
                title = track_job['title']
                album_id = track_job.get('album_id')

                try:
                    from models import Track

                    async with AsyncSessionLocal() as check_db:
                        existing = await check_db.get(Track, track_id)
                        if not existing:
                            logger.error(f"Track {track_id} not found! Skipping.")
                            async with self._bulk_lock:
                                if bulk_queue_id in self.bulk_jobs:
                                    self.bulk_jobs[bulk_queue_id]['failed_segments'] += 1
                            continue

                    from status_lock import status_lock
                    lock_db = next(get_db())
                    try:
                        locked, reason = await status_lock.try_lock_voice(
                            track_id=track_id,
                            voice_id=voice,
                            process_type='tts_bulk',
                            db=lock_db
                        )
                        if not locked:
                            logger.error(f"Could not lock {track_id}: {reason}")
                            async with self._bulk_lock:
                                if bulk_queue_id in self.bulk_jobs:
                                    self.bulk_jobs[bulk_queue_id]['failed_segments'] += 1
                            continue
                    finally:
                        lock_db.close()

                    async with AsyncSessionLocal() as worker_db:
                        segment_text = await self.text_service.get_source_text(track_id, worker_db)
                        if not segment_text:
                            raise ValueError(f"No stored text for {track_id}")

                        from models import Track as TrackModel
                        track_obj = await worker_db.get(TrackModel, track_id)
                        if track_obj:
                            await TrackStatusManager.mark_generating(track_obj, worker_db, process_type='tts_bulk', voice=voice)
                            await worker_db.commit()

                        result = await self.create_tts_track_with_voice(
                            track_id=track_id,
                            title=title,
                            text_content=segment_text,
                            voice=voice,
                            db=worker_db,
                            user=user,
                            bulk_split_count=1,
                            bulk_series_title=None,
                            bulk_queue_id=bulk_queue_id
                        )
                        
                        # Free segment text
                        del segment_text
                        
                        if result.get('status') != 'success':
                            raise ValueError(f"TTS generation failed: {result}")

                        if track_obj:
                            await TrackStatusManager.mark_segmenting(track_obj, worker_db, voice=voice)
                            await worker_db.commit()

                        file_url, upload_metadata = await storage.upload_tts_media_with_voice(
                            audio_file_path=Path(result['audio_file_path']),
                            track_id=track_id,
                            voice=voice,
                            creator_id=user.id,
                            db=worker_db,
                            word_timings=None,  # Fixed: Pass None instead of dict
                            word_timings_path=Path(result.get('word_timings_path')) if result.get('word_timings_path') else None,  # New parameter
                            session_dir=result.get('session_dir'),
                            lock_already_held=True  # ✅ TTS worker already holds the lock
                        )
                        
                        tres = await worker_db.execute(select(TrackModel).where(TrackModel.id == track_id))
                        t = tres.scalar_one_or_none()
                        if t:
                            t.file_path = file_url
                            t.default_voice = voice
                            t.track_type = 'tts'
                            t.duration = result['duration']
                            t.updated_at = datetime.now(timezone.utc)
                            if upload_metadata and 'voice_directory' in upload_metadata:
                                t.voice_directory = upload_metadata['voice_directory']
                            available_voices = getattr(t, 'available_voices', []) or []
                            if voice not in available_voices:
                                available_voices.append(voice)
                                t.available_voices = available_voices
                            await worker_db.commit()

                    async with self._bulk_lock:
                        if bulk_queue_id in self.bulk_jobs:
                            self.bulk_jobs[bulk_queue_id]['completed_segments'] += 1

                    logger.info(f"Track {track_id} done (worker {worker_id})")
                    processed_count += 1
                    
                    # GC every N tracks
                    if processed_count % GC_FREQUENCY == 0:
                        collected, mem_pct = await _force_garbage_collection()
                        if collected > 0:
                            logger.debug(f"Bulk worker-{worker_id}: GC freed {collected} objects (mem: {mem_pct:.1f}%)")

                except Exception as e:
                    logger.error(f"Track {track_id} processing failed: {e}")

                    try:
                        async with AsyncSessionLocal() as fail_db:
                            from models import Track as TrackModel
                            track_fail = await fail_db.get(TrackModel, track_id)
                            if track_fail:
                                await TrackStatusManager.mark_failed(track_fail, fail_db, e, 'bulk_generation')
                                await fail_db.commit()
                    except Exception:
                        pass

                    from status_lock import status_lock
                    unlock_db = next(get_db())
                    try:
                        await status_lock.unlock_voice(track_id, voice, success=False, db=unlock_db)
                    finally:
                        unlock_db.close()

                    async with self._bulk_lock:
                        if bulk_queue_id in self.bulk_jobs:
                            self.bulk_jobs[bulk_queue_id]['failed_segments'] += 1

                finally:
                    job_queue.task_done()

            logger.info(f"Worker {worker_id} finished, processed {processed_count} tracks")

        workers = [asyncio.create_task(worker(i)) for i in range(max_workers)]
        await job_queue.join()
        
        for w in workers:
            w.cancel()
            try:
                await w
            except asyncio.CancelledError:
                pass
        
        async with self._bulk_lock:
            if bulk_queue_id in self.bulk_jobs:
                meta = self.bulk_jobs[bulk_queue_id]
                done = meta['completed_segments']
                fail = meta['failed_segments']
                meta['status'] = 'completed' if fail == 0 else ('partial_success' if done > 0 else 'failed')
                meta['completed_at'] = time.time()
                logger.info(f"BULK-DONE: {bulk_queue_id} | {done} success | {fail} failed")

    async def create_tts_track_with_voice(
        self,
        track_id: str,
        title: str,
        text_content: str,
        voice: str = None,
        db = None,
        user: User = None,
        album_id: str = None,
        bulk_split_count: int = 1,
        bulk_series_title: str = None,
        bulk_queue_id: str = None,
        starting_order: int = 0,
        visibility_status: str = "visible"
    ) -> Dict:
        """Pure TTS generation - NO status management"""
        from sqlalchemy import select

        if not user:
            raise ValueError("User is required for job-based resource management")

        char_count = len(text_content or "")
        logger.info(f"TTS: {track_id} | {voice} | {char_count:,} chars")

        if bulk_split_count > 1:
            if not album_id:
                raise ValueError("album_id is required for bulk generation")
            return await self._create_bulk_tts_series(
                base_track_id=track_id,
                series_title=bulk_series_title or title,
                text_content=text_content,
                voice=voice,
                split_count=bulk_split_count,
                album_id=album_id,
                db=db,
                user=user,
                starting_order=starting_order,
                visibility_status=visibility_status
            )

        async with self.tts_job(user, track_id, voice or "default") as job_id:
            session_dir = None
            start_time = time.time()

            current_task = asyncio.current_task()
            if current_task:
                self._job_tasks[job_id] = current_task

            try:
                if not text_content or not text_content.strip():
                    raise ValueError("Text content cannot be empty")

                if not voice:
                    voice = await self._get_first_available_voice(db)
                    if not voice:
                        raise ValueError("No voices available in database")

                validated_voice = await self._validate_voice_with_db(voice, db)
                if validated_voice != voice:
                    voice = validated_voice

                timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)
                session_id = uuid.uuid4().hex[:8]
                session_dir = self.temp_dir / f"tts_{track_id}_{voice}_{timestamp}_{session_id}"
                await _amkdir(session_dir)

                # Store text for recovery
                logger.info(f"TTS-TEXT: Storing source text for {track_id}")
                await text_storage_service.store_source_text(track_id, text_content, db)
                
                # Update DB metadata
                if db:
                    res = await _exec(db, select(TTSTrackMeta).where(TTSTrackMeta.track_id == track_id))
                    track_meta = res.scalar_one_or_none()
                    
                    if not track_meta:
                        word_count = await anyio.to_thread.run_sync(lambda: len(text_content.split()))
                        estimated_duration = (word_count / self.words_per_minute) * 60.0
                        estimated_chunks = max(1, len(text_content) // 2400)
                        
                        track_meta = TTSTrackMeta(
                            track_id=track_id,
                            default_voice=voice,
                            available_voices=[voice],
                            total_words=word_count,
                            total_characters=len(text_content),
                            total_segments=estimated_chunks,
                            total_duration=estimated_duration,
                            segment_duration=30.0,
                            words_per_segment=word_count // estimated_chunks,
                            processing_status="processing",
                            started_at=datetime.now(timezone.utc),
                        )
                        db.add(track_meta)
                        await _flush(db)
                    
                    await _commit(db)

                # Free text content after storage
                source_text = text_content
                del text_content
                collected, mem_pct = await _force_garbage_collection()
                logger.debug(f"Text freed: GC collected {collected} objects (mem: {mem_pct:.1f}%)")

                # MODIFIED: Pass text directly to _generate_chunks_in_parallel (now streaming)
                chunk_files, total_duration, timings_dir = await self._generate_chunks_in_parallel(
                    chunks=source_text,  # Now accepts text string
                    voice=voice,
                    session_dir=session_dir,
                    user=user,
                    job_id=job_id
                )
                
                del source_text
                await _force_garbage_collection()

                final_audio_path = session_dir / f"tts_{track_id}_{voice}.mp3"
                final_size = await self._concatenate_audio_files(chunk_files, final_audio_path, voice)

                # MODIFIED: _load_and_merge_timings now returns summary dict
                timing_summary = await self._load_and_merge_timings(
                    timings_dir, track_id, voice, db, session_dir=session_dir
                )

                try:
                    await _armtree(timings_dir)
                except Exception:
                    pass

                if db:
                    try:
                        res = await _exec(db, select(Track).where(Track.id == track_id))
                        track_record = res.scalar_one_or_none()
                        if track_record:
                            track_record.track_type = 'tts'
                            track_record.default_voice = voice
                            track_record.source_text = ""
                            track_record.has_read_along = True
                            track_record.tts_progress = 100
                            await _commit(db)
                    except Exception as track_update_error:
                        logger.error(f"Failed to update Track {track_id}: {track_update_error}")
                        try:
                            await _rollback(db)
                        except Exception:
                            pass

                total_words = timing_summary.get('total_words', 0) if isinstance(timing_summary, dict) else 0
                processing_time = time.time() - start_time
                logger.info(
                    f"TTS-DONE: {track_id} | {voice} | {total_duration:.1f}s | "
                    f"{total_words} words | {processing_time:.1f}s"
                )

                return {
                    'status': 'success',
                    'audio_file_path': final_audio_path,
                    'duration': total_duration,
                    'voice': voice,
                    'word_count': total_words,
                    'track_id': track_id,
                    'chunks_processed': len(chunk_files),
                    'final_file_size': final_size,
                    'session_dir': session_dir,
                    'voice_directory': f"voice-{voice}",
                    'is_voice_track': True,
                    'word_timings': timing_summary,
                    'word_timings_path': timing_summary.get('timings_file_path') if isinstance(timing_summary, dict) else None,
                    'job_id': job_id
                }

            except asyncio.CancelledError:
                if session_dir and await _aexists(session_dir):
                    try:
                        await self._cleanup_session_directory(session_dir)
                    except Exception:
                        pass
                raise

            except Exception as e:
                logger.error(f"TTS-FAILED: {track_id} | {str(e)}")

                if session_dir and await _aexists(session_dir):
                    try:
                        await self._cleanup_session_directory(session_dir)
                    except Exception:
                        pass
                raise
            finally:
                self._job_tasks.pop(job_id, None)


    async def _load_and_merge_timings_streaming(self, timings_dir: Path, track_id: str, voice: str, db, *, session_dir: Optional[Path] = None) -> Dict:
        """
        FIX #3: Stream timing merge directly to storage instead of building huge list.
        Memory stays O(1) instead of O(total_words).
        Returns a summary dict instead of full timings.
        """
        if not isinstance(timings_dir, Path):
            raise TypeError(f"Expected Path for timings_dir, got {type(timings_dir)}")

        if not await _aexists(timings_dir):
            raise FileNotFoundError(f"Timings directory not found: {timings_dir}")

        timing_files = sorted(timings_dir.glob("chunk_*.json"))
        if not timing_files:
            raise ValueError(f"No timing files found in {timings_dir}")

        # Choose output location
        stream_dir = session_dir or timings_dir.parent
        timings_stream_path = stream_dir / f"word-timings_{track_id}_{voice}.jsonl"

        cumulative_time = 0.0
        batch_size = 50
        word_batch = []
        total_words = 0
        total_duration = 0.0

        # Open NDJSON file for streaming write
        async with aiofiles.open(timings_stream_path, "w") as out_f:
            for i, tf_path in enumerate(timing_files):
                if not await _aexists(tf_path):
                    continue
                
                async with aiofiles.open(tf_path, "r") as f:
                    content = await f.read()
                
                if not content:
                    continue

                shard = await anyio.to_thread.run_sync(json.loads, content)
                chunk_duration = float(shard.get("duration", 0.0))
                word_boundaries = shard.get("word_boundaries", []) or []
                
                for timing in word_boundaries:
                    t = dict(timing)
                    
                    original_start = float(t.get("start_time", 0))
                    original_end = float(t.get("end_time", 0))
                    
                    t["start_time"] = original_start + cumulative_time
                    t["end_time"] = original_end + cumulative_time
                    t["chunk_cumulative_offset"] = cumulative_time
                    t["global_word_index"] = total_words
                    
                    word_batch.append(t)
                    total_words += 1

                cumulative_time += max(0.0, chunk_duration)
                
                # Stream to storage + NDJSON file
                if len(word_batch) >= batch_size * 20:
                    await self.text_service.append_word_timings(track_id, voice, word_batch, db)
                    
                    # Write to NDJSON file
                    lines = await anyio.to_thread.run_sync(
                        lambda b=word_batch: "\n".join(json.dumps(x) for x in b) + "\n"
                    )
                    await out_f.write(lines)
                    
                    word_batch = []
                    await _force_garbage_collection()

            # Store remaining batch
            if word_batch:
                await self.text_service.append_word_timings(track_id, voice, word_batch, db)
                
                # Write final batch to NDJSON
                lines = await anyio.to_thread.run_sync(
                    lambda b=word_batch: "\n".join(json.dumps(x) for x in b) + "\n"
                )
                await out_f.write(lines)

        if total_words == 0:
            raise ValueError(f"No usable timings loaded from {len(timing_files)} files")

        total_duration = cumulative_time
        logger.info(f"Merged {total_words} words (streamed), total duration: {total_duration:.3f}s")
        
        # Add DB update from BUG #3 here
        if db:
            try:
                res = await _exec(db, select(TTSTrackMeta).where(TTSTrackMeta.track_id == track_id))
                tm = res.scalar_one_or_none()
                if tm:
                    if voice not in (tm.available_voices or []):
                        tm.available_voices = (tm.available_voices or []) + [voice]
                    if not tm.default_voice:
                        tm.default_voice = voice
                    tm.total_duration = float(total_duration)
                    tm.processing_status = 'ready'
                    tm.completed_at = datetime.now(timezone.utc)
                    tm.progress_percentage = 100.0
                    await _commit(db)
            except Exception:
                try: 
                    await _rollback(db)
                except Exception: 
                    pass
        
        # Return summary dict instead of full timings
        return {
            'total_words': total_words,
            'total_duration': total_duration,
            'track_id': track_id,
            'voice': voice,
            'chunks_processed': len(timing_files),
            'timings_file_path': str(timings_stream_path)  # New
        }

    async def _load_and_merge_timings(
        self, 
        timings_dir: Path, 
        track_id: str, 
        voice: str, 
        db, 
        *, 
        session_dir: Optional[Path] = None
    ) -> Dict:
        """
        MODIFIED: Now streams to storage and returns summary dict.
        Memory: O(1) instead of O(total_words).
        Returns: Summary dict with file path, not full timings list.
        """
        if not isinstance(timings_dir, Path):
            raise TypeError(f"Expected Path for timings_dir, got {type(timings_dir)}")

        if not await _aexists(timings_dir):
            raise FileNotFoundError(f"Timings directory not found: {timings_dir}")

        timing_files = sorted(timings_dir.glob("chunk_*.json"))
        if not timing_files:
            raise ValueError(f"No timing files found in {timings_dir}")

        stream_dir = session_dir or timings_dir.parent
        timings_stream_path = stream_dir / f"word-timings_{track_id}_{voice}.jsonl"

        cumulative_time = 0.0
        batch_size = 50
        word_batch = []
        total_words = 0
        total_duration = 0.0

        async with aiofiles.open(timings_stream_path, "w") as out_f:
            for i, tf_path in enumerate(timing_files):
                if not await _aexists(tf_path):
                    continue
                
                async with aiofiles.open(tf_path, "r") as f:
                    content = await f.read()
                
                if not content:
                    continue

                shard = await anyio.to_thread.run_sync(json.loads, content)
                chunk_duration = float(shard.get("duration", 0.0))
                word_boundaries = shard.get("word_boundaries", []) or []
                
                for timing in word_boundaries:
                    t = dict(timing)
                    
                    original_start = float(t.get("start_time", 0))
                    original_end = float(t.get("end_time", 0))
                    
                    t["start_time"] = original_start + cumulative_time
                    t["end_time"] = original_end + cumulative_time
                    t["chunk_cumulative_offset"] = cumulative_time
                    t["global_word_index"] = total_words
                    
                    word_batch.append(t)
                    total_words += 1

                cumulative_time += max(0.0, chunk_duration)
                
                if len(word_batch) >= batch_size * 20:
                    await self.text_service.append_word_timings(track_id, voice, word_batch, db)
                    
                    lines = await anyio.to_thread.run_sync(
                        lambda b=word_batch: "\n".join(json.dumps(x) for x in b) + "\n"
                    )
                    await out_f.write(lines)
                    
                    word_batch = []
                    await _force_garbage_collection()

            if word_batch:
                await self.text_service.append_word_timings(track_id, voice, word_batch, db)
                
                lines = await anyio.to_thread.run_sync(
                    lambda b=word_batch: "\n".join(json.dumps(x) for x in b) + "\n"
                )
                await out_f.write(lines)

        if total_words == 0:
            raise ValueError(f"No usable timings loaded from {len(timing_files)} files")

        total_duration = cumulative_time
        logger.info(f"Merged {total_words} words (streamed), total duration: {total_duration:.3f}s")
        
        if db:
            try:
                res = await _exec(db, select(TTSTrackMeta).where(TTSTrackMeta.track_id == track_id))
                tm = res.scalar_one_or_none()
                if tm:
                    if voice not in (tm.available_voices or []):
                        tm.available_voices = (tm.available_voices or []) + [voice]
                    if not tm.default_voice:
                        tm.default_voice = voice
                    tm.total_duration = float(total_duration)
                    tm.processing_status = 'ready'
                    tm.completed_at = datetime.now(timezone.utc)
                    tm.progress_percentage = 100.0
                    await _commit(db)
            except Exception:
                try: 
                    await _rollback(db)
                except Exception: 
                    pass
        
        return {
            'total_words': total_words,
            'total_duration': total_duration,
            'track_id': track_id,
            'voice': voice,
            'chunks_processed': len(timing_files),
            'timings_file_path': str(timings_stream_path)
        }


    async def switch_voice_efficiently(
        self,
        track_id: str,
        new_voice: str,
        db,
        user: User,
        already_locked: bool = False
    ) -> Dict:
        """Efficient voice switching with S4 backup check"""
        logger.info(f"VOICE-SWITCH: {track_id} | {new_voice}")

        if not already_locked:
            can_start, err = self.can_user_start_generation(user, track_id, new_voice)
            if not can_start:
                raise ValueError(err)
            self.start_user_generation(user.id, track_id, new_voice)

        async with self.tts_job(user, track_id, new_voice) as job_id:
            start_time = time.time()
            lock_key = f"{track_id}:{new_voice}"

            try:
                logger.info(f"Checking S3/S4 for voice: {new_voice}")

                from storage import storage
                voice_package = await storage.download_complete_voice_package(
                    track_id, new_voice, db
                )

                if voice_package:
                    logger.info(f"Found voice in S3/S4: {new_voice} ({voice_package['duration']:.2f}s)")

                    await self._store_voice_word_timings(
                        track_id, new_voice,
                        voice_package['word_timings'],
                        voice_package['duration'],
                        db
                    )

                    # ✅ FIX: Convert list to temp .jsonl file (same as storage.py does)
                    word_timings_list = voice_package['word_timings']
                    timings_file = None
                    
                    if word_timings_list:
                        timings_file = self.temp_dir / f"s4_restored_{track_id}_{new_voice}.jsonl"
                        async with aiofiles.open(timings_file, 'w') as f:
                            for word_timing in word_timings_list:
                                await f.write(json.dumps(word_timing) + '\n')
                        logger.info(f"Converted {len(word_timings_list)} S4 timings to streaming file: {timings_file}")

                    processing_time = time.time() - start_time

                    if not already_locked:
                        self.complete_user_generation(user.id, track_id, new_voice)

                    # ✅ Return dict format (not list)
                    return {
                        'status': 'success',
                        'track_id': track_id,
                        'new_voice': new_voice,
                        'audio_file_path': voice_package['audio_file_path'],
                        'duration': voice_package['duration'],
                        'processing_time': processing_time,
                        'speed_ratio': 0,
                        'session_dir': voice_package['audio_file_path'].parent,
                        'voice_directory': f"voice-{new_voice}",
                        'chunks_processed': 0,
                        'final_file_size': await _astat_size(voice_package['audio_file_path']),
                        'ready_for_segmentation': True,
                        'word_timings': {  # ✅ Dict format
                            'total_words': len(word_timings_list) if word_timings_list else 0,
                            'timings_file_path': str(timings_file) if timings_file else None
                        },
                        'word_timings_path': str(timings_file) if timings_file else None,  # ✅ Explicit path
                        'job_id': job_id,
                        'source': 's3_backup'
                    }

                logger.info(f"Voice not in S3/S4, generating: {new_voice}")

                full_text = await self.text_service.get_source_text(track_id, db, bypass_cache=True)
                if not full_text:
                    raise ValueError(f"No source text found for {track_id}")

                session_dir = self.temp_dir / f"voice_switch_{track_id}_{new_voice}_{uuid.uuid4().hex[:8]}"
                await _amkdir(session_dir)

                def progress_callback(completed, total):
                    progress = 20 + (60 * completed / total)
                    self._update_voice_progress(
                        lock_key, progress, 'generating',
                        f'Generating {new_voice}: {completed}/{total} chunks',
                        chunks_completed=completed, total_chunks=total
                    )

                # MODIFIED: Pass text directly to _generate_chunks_in_parallel
                chunk_files, total_duration, timings_dir = await self._generate_chunks_in_parallel(
                    chunks=full_text,  # Now accepts text string
                    voice=new_voice,
                    session_dir=session_dir,
                    user=user,
                    job_id=job_id,
                    progress_callback=progress_callback,
                    lock_key=lock_key
                )
                
                del full_text
                await _force_garbage_collection()

                final_audio_path = session_dir / f"complete_{track_id}_{new_voice}.mp3"
                final_size = await self._concatenate_audio_files(chunk_files, final_audio_path, new_voice)

                # MODIFIED: _load_and_merge_timings now returns summary dict
                timing_summary = await self._load_and_merge_timings(
                    timings_dir, track_id, new_voice, db, session_dir=session_dir
                )

                try:
                    await _armtree(timings_dir)
                except Exception:
                    pass

                processing_time = time.time() - start_time
                speed_ratio = total_duration / processing_time if processing_time > 0 else 0
                logger.info(
                    f"VOICE-SWITCH-DONE: {track_id} | {new_voice} | "
                    f"{total_duration:.1f}s | {processing_time:.1f}s | {speed_ratio:.1f}x"
                )

                if not already_locked:
                    self.complete_user_generation(user.id, track_id, new_voice)

                return {
                    'status': 'success',
                    'track_id': track_id,
                    'new_voice': new_voice,
                    'audio_file_path': final_audio_path,
                    'duration': total_duration,
                    'processing_time': processing_time,
                    'speed_ratio': speed_ratio,
                    'session_dir': session_dir,
                    'voice_directory': f"voice-{new_voice}",
                    'chunks_processed': len(chunk_files),
                    'final_file_size': final_size,
                    'ready_for_segmentation': True,
                    'word_timings': timing_summary,
                    'word_timings_path': timing_summary.get('timings_file_path') if isinstance(timing_summary, dict) else None,
                    'job_id': job_id,
                    'source': 'tts_generation'
                }

            except Exception:
                if not already_locked:
                    self.complete_user_generation(user.id, track_id, new_voice)
                raise

    async def get_available_voices_for_track(self, track_id: str, db) -> Dict:
        try:
            res = await _exec(db, select(TTSTrackMeta).where(TTSTrackMeta.track_id == track_id))
            track_meta = res.scalar_one_or_none()
            
            available_voices = await get_available_voices_from_db(db)
            first_available = available_voices[0] if available_voices else None
            
            if not track_meta:
                return {
                    'default_voice': first_available,
                    'available_voices': [],
                    'can_add_voices': True,
                    'has_text_chunks': False,
                    'all_voices': available_voices
                }
            
            has_text = False
            try:
                text_file = self.text_service._get_text_file_path(track_id)
                has_text = await _aexists(text_file)
            except Exception:
                has_text = False
            
            return {
                'default_voice': track_meta.default_voice or first_available,
                'available_voices': track_meta.available_voices or [],
                'can_add_voices': has_text,
                'has_text_chunks': has_text,
                'all_voices': available_voices
            }
            
        except Exception as e:
            logger.error(f"Error getting available voices: {str(e)}")
            available_voices = await get_available_voices_from_db(db)
            first_available = available_voices[0] if available_voices else None
            return {
                'default_voice': first_available,
                'available_voices': [],
                'can_add_voices': False,
                'has_text_chunks': False,
                'all_voices': available_voices
            }

    async def get_user_job_status(self, user: User) -> Dict:
        return await self.user_job_manager.get_user_status(user.id)

    async def _cleanup_session_directory(self, session_dir: Path):
        try:
            if await _aexists(session_dir):
                await _armtree(session_dir)
        except Exception as e:
            logger.error(f"Error cleaning up session directory: {str(e)}")

    def _update_voice_progress(self, lock_key: str, progress: float, phase: str, message: str, **kwargs):
        if lock_key in self.voice_switch_progress:
            if progress >= 100:
                status = 'complete'
            elif phase in ['generating', 'concatenating', 'organizing', 'finalizing']:
                status = 'generating'
            elif phase == 'segmenting':
                status = 'segmenting'
            else:
                status = 'generating'

            self.voice_switch_progress[lock_key].update({
                'status': status,
                'progress': min(100, max(0, progress)),
                'phase': phase,
                'message': message,
                'updated_at': time.time(),
                **kwargs
            })

            # Schedule WebSocket broadcast if we're in an async context
            try:
                track_id, voice_id = lock_key.split(':', 1)
                asyncio.create_task(self._broadcast_progress_update(
                    track_id, voice_id, progress, phase, message, status, **kwargs
                ))
            except Exception:
                pass  # Silently fail if not in async context

    async def _broadcast_progress_update(self, track_id: str, voice_id: str, progress: float,
                                        phase: str, message: str, status: str, **kwargs):
        """Broadcast progress update via WebSocket"""
        try:
            from tts_websocket import broadcast_tts_progress, broadcast_segmentation_progress, notify_tts_complete

            if status == 'complete':
                await notify_tts_complete(track_id, voice_id, success=True)
            elif phase == 'segmenting':
                segments_completed = kwargs.get('segments_completed', 0)
                total_segments = kwargs.get('total_segments', 0)
                await broadcast_segmentation_progress(
                    track_id, voice_id, progress,
                    segments_completed, total_segments, message
                )
            else:
                chunks_completed = kwargs.get('chunks_completed', 0)
                total_chunks = kwargs.get('total_chunks', 0)
                await broadcast_tts_progress(
                    track_id, voice_id, progress, phase, message,
                    chunks_completed, total_chunks, **kwargs
                )
        except Exception as e:
            logger.debug(f"Could not broadcast progress update: {str(e)}")

    async def check_storage_health(self) -> Dict[str, Any]:
        """Check health of file storage system"""
        try:
            file_health = await self.text_service.health_check()
            
            return {
                'file_storage': {
                    'status': 'healthy' if file_health['storage_accessible'] else 'unhealthy',
                    'details': file_health
                },
                'overall_status': 'healthy' if file_health['storage_accessible'] else 'unhealthy',
                'storage_type': 'file_only'
            }
            
        except Exception as e:
            return {
                'file_storage': {'status': 'unhealthy', 'error': str(e)},
                'overall_status': 'unhealthy',
                'storage_type': 'file_only',
                'error': str(e)
            }

    async def get_storage_statistics(self) -> Dict[str, Any]:
        """Get storage performance statistics"""
        stats = {
            'storage_type': 'file_only',
            'database_text_storage': False
        }
        
        try:
            file_stats = await self.text_service.get_statistics()
            stats['file_storage'] = file_stats
        except Exception as e:
            stats['file_storage_error'] = str(e)
        
        return stats

    async def create_tts_track(self, track_id: str, title: str, text_content: str, voice: str = None, db = None, user: User = None) -> Dict:
        return await self.create_tts_track_with_voice(
            track_id=track_id, title=title, text_content=text_content, voice=voice, db=db, user=user
        )

    async def switch_voice(self, track_id: str, new_voice: str, db, user: User = None) -> Dict:
        return await self.switch_voice_efficiently(track_id=track_id, new_voice=new_voice, db=db, user=user)

# Global enhanced TTS service instance
enhanced_voice_tts_service = EnhancedVoiceAwareTTSService()

__all__ = ['enhanced_voice_tts_service', 'EnhancedVoiceAwareTTSService', 'get_available_voices_from_db']