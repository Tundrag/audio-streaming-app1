"""
Centralized Popular Tracks Service

This service provides a single source of truth for popular track/album logic used across:
- Voice cache manager (for determining 5-voice eligibility)
- API endpoints (for displaying popular content)
- Home page (popular tracks display)

Popular Track Logic (SIMPLE):
1. Get top 25 albums by total play count
2. ALL tracks in those albums are popular (eligible for 5 voices)
3. Track count doesn't matter - could be 1 track or 100 tracks per album
"""

import logging
from typing import List, Dict, Optional
from sqlalchemy import func, desc, select
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from models import Track, Album, TrackPlays
import anyio

logger = logging.getLogger(__name__)

# Configuration - single source of truth
POPULAR_ALBUMS_LIMIT = 25  # Top 25 albums by play count (ALL tracks in these albums get 5 voices)


# Async/sync DB compatibility helpers
def _is_async(db) -> bool:
    """Check if database session is async"""
    return isinstance(db, AsyncSession)


async def _exec(db, stmt):
    """Execute a SQLAlchemy statement on either AsyncSession or sync Session"""
    if _is_async(db):
        return await db.execute(stmt)
    return await anyio.to_thread.run_sync(db.execute, stmt)


class PopularTracksService:
    """Centralized service for popular track/album logic"""

    def __init__(self):
        self.cache = {}  # Simple in-memory cache
        self.cache_ttl = 300  # 5 minutes

    async def get_popular_track_ids(self, creator_id: int, db) -> List[str]:
        """
        Get list of popular track IDs for voice cache eligibility.

        Simple logic:
        1. Get top 25 albums by total play count
        2. ALL tracks in those albums are popular (get 5 voices)

        Returns: List of track IDs that should get 5 voices
        """
        import time
        cache_key = f"popular_track_ids:{creator_id}"

        # Check cache
        if cache_key in self.cache:
            cached_data = self.cache[cache_key]
            if time.time() - cached_data['timestamp'] < self.cache_ttl:
                return cached_data['track_ids']

        try:
            # Step 1: Get top 25 albums by total play count
            if _is_async(db):
                # Async query
                stmt = (
                    select(Album.id)
                    .join(Track, Album.id == Track.album_id)
                    .join(TrackPlays, Track.id == TrackPlays.track_id)
                    .filter(Album.created_by_id == creator_id)
                    .group_by(Album.id)
                    .order_by(desc(func.sum(TrackPlays.play_count)))
                    .limit(POPULAR_ALBUMS_LIMIT)
                )
                result = await db.execute(stmt)
                popular_album_ids = result.scalars().all()
            else:
                # Sync query
                popular_album_ids = (
                    db.query(Album.id)
                    .join(Track, Album.id == Track.album_id)
                    .join(TrackPlays, Track.id == TrackPlays.track_id)
                    .filter(Album.created_by_id == creator_id)
                    .group_by(Album.id)
                    .order_by(desc(func.sum(TrackPlays.play_count)))
                    .limit(POPULAR_ALBUMS_LIMIT)
                    .all()
                )
                popular_album_ids = [aid[0] for aid in popular_album_ids]

            logger.info(f"Found {len(popular_album_ids)} popular albums for creator {creator_id}")

            # Step 2: Get ALL tracks in those popular albums
            if popular_album_ids:
                stmt = (
                    select(Track.id)
                    .filter(
                        Track.album_id.in_(popular_album_ids),
                        Track.track_type == 'tts'
                    )
                )
                result = await _exec(db, stmt)
                track_ids = [str(tid) for tid in result.scalars().all()]
            else:
                track_ids = []

            # Cache result
            self.cache[cache_key] = {
                'track_ids': track_ids,
                'timestamp': time.time()
            }

            logger.info(f"Total popular tracks for creator {creator_id}: {len(track_ids)} (from {len(popular_album_ids)} popular albums)")
            return track_ids

        except Exception as e:
            logger.error(f"Error getting popular track IDs: {e}", exc_info=True)
            return []

    async def get_popular_albums_for_display(self, creator_id: int, db) -> List[Dict]:
        """
        Get popular albums for home page display.

        Returns top N albums by total play count with metadata.
        This matches the /api/popular-tracks endpoint logic.
        """
        try:
            # Query albums with aggregated play counts
            if _is_async(db):
                # Async query
                stmt = (
                    select(
                        Album,
                        func.sum(TrackPlays.play_count).label('total_plays'),
                        func.count(Track.id).label('track_count'),
                        func.avg(TrackPlays.completion_rate).label('avg_completion')
                    )
                    .join(Track, Album.id == Track.album_id)
                    .join(TrackPlays, Track.id == TrackPlays.track_id)
                    .filter(Album.created_by_id == creator_id)
                    .group_by(Album.id)
                    .having(func.sum(TrackPlays.play_count) > 0)
                    .order_by(desc('total_plays'))
                    .limit(POPULAR_ALBUMS_LIMIT)
                )
                result = await db.execute(stmt)
                results = result.all()
            else:
                # Sync query
                results = (
                    db.query(
                        Album,
                        func.sum(TrackPlays.play_count).label('total_plays'),
                        func.count(Track.id).label('track_count'),
                        func.avg(TrackPlays.completion_rate).label('avg_completion')
                    )
                    .join(Track, Album.id == Track.album_id)
                    .join(TrackPlays, Track.id == TrackPlays.track_id)
                    .filter(Album.created_by_id == creator_id)
                    .group_by(Album.id)
                    .having(func.sum(TrackPlays.play_count) > 0)
                    .order_by(desc('total_plays'))
                    .limit(POPULAR_ALBUMS_LIMIT)
                    .all()
                )

            logger.info(f"Found {len(results)} popular albums for creator {creator_id}")

            # Format results for API response
            popular_albums = []
            for album, total_plays, track_count, avg_completion in results:
                album_data = {
                    "id": str(album.id),
                    "album_id": str(album.id),
                    "title": album.title,
                    "album_title": album.title,
                    "cover_path": album.cover_path or '/static/images/default-album.jpg',
                    "visibility_status": album.visibility_status,
                    "total_plays": int(total_plays),
                    "track_count": int(track_count),
                    "avg_completion": round(float(avg_completion), 2) if avg_completion else 0
                }
                popular_albums.append(album_data)

            return popular_albums

        except Exception as e:
            logger.error(f"Error getting popular albums: {e}", exc_info=True)
            return []

    async def is_track_popular(self, track_id: str, creator_id: int, db) -> bool:
        """
        Check if a track is popular (eligible for 5 voices).

        Returns True if track is in popular_track_ids list.
        """
        popular_ids = await self.get_popular_track_ids(creator_id, db)
        return track_id in popular_ids


# Global singleton instance
popular_tracks_service = PopularTracksService()


# Convenience functions for backward compatibility
async def get_popular_track_ids(creator_id: int, db) -> List[str]:
    """Get popular track IDs (for voice cache)"""
    return await popular_tracks_service.get_popular_track_ids(creator_id, db)


async def get_popular_albums(creator_id: int, db) -> List[Dict]:
    """Get popular albums (for home page display)"""
    return await popular_tracks_service.get_popular_albums_for_display(creator_id, db)


async def is_track_popular(track_id: str, creator_id: int, db) -> bool:
    """Check if track is popular"""
    return await popular_tracks_service.is_track_popular(track_id, creator_id, db)
