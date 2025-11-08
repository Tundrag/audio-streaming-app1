# database.py - ORIGINAL generous pool configuration

import os
import asyncio
from typing import Union, AsyncGenerator, Generator, Optional
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import QueuePool
from sqlalchemy.ext.declarative import declarative_base
import logging

logger = logging.getLogger(__name__)

# Environment
POSTGRES_USER = os.getenv("DB_USER", "tundrag")
POSTGRES_PASSWORD = os.getenv("DB_PASSWORD", "Tundrag2010!")
POSTGRES_DB = os.getenv("DB_NAME", "audio_streaming_db")
POSTGRES_HOST = os.getenv("DB_HOST", "localhost")
POSTGRES_PORT = os.getenv("DB_PORT", "5432")

# Database URLs
SYNC_DATABASE_URL = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
ASYNC_DATABASE_URL = f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

# Shared Base (works with both sync and async)
Base = declarative_base()

# --- SYNC ENGINE (original generous pools) ---
sync_engine = create_engine(
    SYNC_DATABASE_URL,
    poolclass=QueuePool,
    pool_size=250,
    max_overflow=500,
    pool_timeout=30,
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_use_lifo=True,
    connect_args={
        "keepalives": 1,
        "keepalives_idle": 150,
        "keepalives_interval": 30,
        "keepalives_count": 5,
        "options": "-c statement_timeout=30000 -c lock_timeout=10000",
        "application_name": "audio_streaming_app_sync",
    },
)

# --- ASYNC ENGINE (original generous pools) ---
async_engine = create_async_engine(
    ASYNC_DATABASE_URL,
    # NOTE: poolclass=QueuePool is not valid for async engines, so it was left out
    pool_size=250,
    max_overflow=500,
    pool_timeout=30,
    pool_pre_ping=True,
    pool_recycle=1800,
    connect_args={
        "server_settings": {
            "application_name": "audio_streaming_app_async",
            "statement_timeout": "30000",
            "lock_timeout": "10000",
        }
    },
    echo=False,  # set to True only for debugging
)

# --- Session makers ---
SyncSessionLocal = sessionmaker(
    autocommit=False, autoflush=False, bind=sync_engine, expire_on_commit=False
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

# --- Sync dependency ---
def get_db() -> Generator[Session, None, None]:
    """Original sync dependency"""
    db = SyncSessionLocal()
    try:
        db.execute(text("SELECT 1"))
        yield db
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        raise
    finally:
        db.close()

# --- Async dependency ---
async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """Original async dependency"""
    async with AsyncSessionLocal() as session:
        try:
            await session.execute(text("SELECT 1"))
            yield session
        except Exception as e:
            await session.rollback()
            logger.error(f"Async database connection error: {e}")
            raise
        finally:
            await session.close()

async def close_async_engine():
    """Cleanup async engine on shutdown"""
    await async_engine.dispose()

# --- Unified manager ---
class DatabaseManager:
    """Unified manager for sync + async"""

    def __init__(self):
        self.sync_engine = sync_engine
        self.async_engine = async_engine
        self.sync_session_local = SyncSessionLocal
        self.async_session_local = AsyncSessionLocal

    def get_sync_session(self) -> Session:
        return self.sync_session_local()

    async def get_async_session(self) -> AsyncSession:
        return self.async_session_local()

    def is_async_session(self, session) -> bool:
        return isinstance(session, AsyncSession)

    async def execute_query(self, session: Union[Session, AsyncSession], query):
        if self.is_async_session(session):
            return await session.execute(query)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: session.execute(query))

    async def commit_session(self, session: Union[Session, AsyncSession]):
        if self.is_async_session(session):
            await session.commit()
        else:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, session.commit)

    async def rollback_session(self, session: Union[Session, AsyncSession]):
        if self.is_async_session(session):
            await session.rollback()
        else:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, session.rollback)

# Global db manager
db_manager = DatabaseManager()

# Backward compat aliases
engine = sync_engine
SessionLocal = SyncSessionLocal

# Pool status helper
def get_pool_status():
    sync_pool_info = {
        "size": sync_engine.pool.size(),
        "checked_in": sync_engine.pool.checkedin(),
        "checked_out": sync_engine.pool.checkedout(),
        "overflow": sync_engine.pool.overflow(),
    }
    try:
        async_pool_info = {
            "size": async_engine.pool.size(),
            "checked_in": async_engine.pool.checkedin(),
            "checked_out": async_engine.pool.checkedout(),
            "overflow": async_engine.pool.overflow(),
        }
    except AttributeError:
        async_pool_info = {
            "size": "N/A (async pool)",
            "checked_in": "N/A",
            "checked_out": "N/A",
            "overflow": "N/A",
        }
    return {"sync_pool": sync_pool_info, "async_pool": async_pool_info}
