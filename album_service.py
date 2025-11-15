from typing import Optional, List, Dict, Union
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from pathlib import Path
import tempfile
import zipfile
import shutil
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, desc, Boolean, Integer, text, func
from fastapi import HTTPException
from models import (
    UserRole, Album, Track, User, UserAlbumManagement,
    CampaignTier, TrackPlays, PlaybackProgress
)
from storage import storage  # Import the storage handler
from hls_streaming import stream_manager
from duration_manager import duration_manager
from discord_integration import (
    on_album_created, on_album_updated, on_album_deleted, on_bulk_update
)
import logging

logger = logging.getLogger(__name__)

class AlbumService:
    def __init__(self, db: Session):
        self.db = db

    async def create_album(
        self,
        title: str,
        cover_path: str,
        creator_id: int,
        tier_data: Optional[Dict] = None,
        visibility_status: str = "visible"
    ) -> Album:
        """Create a new album with optional tier restrictions"""
        try:
            # Use UserRole enum values instead of strings
            creator = self.db.query(User).filter(
                and_(
                    User.id == creator_id,
                    or_(User.role == UserRole.CREATOR, User.role == UserRole.TEAM)
                )
            ).first()

            if not creator:
                raise HTTPException(status_code=403, detail="Invalid creator")

            # Create new album
            new_album = Album(
                id=str(uuid4()),
                title=title,
                cover_path=cover_path,
                created_by_id=creator_id,
                created_at=datetime.now(timezone.utc),
                visibility_status=visibility_status
            )

            if tier_data and tier_data.get('minimum_tier'):
                tier = self.db.query(CampaignTier).filter(
                    and_(
                        CampaignTier.creator_id == creator_id,
                        CampaignTier.title == tier_data['minimum_tier'],
                        CampaignTier.is_active == True
                    )
                ).first()

                if tier:
                    new_album.tier_restrictions = {
                        "is_restricted": True,
                        "creator_id": creator_id,
                        "minimum_tier": tier.title,
                        "minimum_tier_amount": tier.amount_cents,
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }
                else:
                    logger.warning(f"Tier not found: {tier_data['minimum_tier']}")

            self.db.add(new_album)
            self.db.commit()
            self.db.refresh(new_album)

            # Notify Discord about the new album
            await on_album_created(self.db, str(new_album.id))

            return new_album

        except Exception as e:
            self.db.rollback()
            logger.error(f"Error creating album: {str(e)}")
            raise


    async def delete_all_tts_voices_for_track(self, track_id: str, track, storage, db=None) -> bool:
        """Delete ALL voice files for a TTS track from S4 storage"""
        deleted_count = 0
        total_attempts = 0

        try:
            available_voices = []

            # Strategy 1: Get voices from track metadata
            if hasattr(track, 'available_voices') and track.available_voices:
                if isinstance(track.available_voices, list):
                    available_voices = track.available_voices
                elif isinstance(track.available_voices, str):
                    available_voices = [track.available_voices]
                logger.info(f"🎤 Found voices in track metadata: {available_voices}")

            # Strategy 2: Check default_voice
            if hasattr(track, 'default_voice') and track.default_voice:
                if track.default_voice not in available_voices:
                    available_voices.append(track.default_voice)
                logger.info(f"🎤 Added default voice: {track.default_voice}")

            # Strategy 3: Query database for TTS voices
            if db and not available_voices:
                try:
                    from sqlalchemy import text
                    voice_query = text("""
                        SELECT DISTINCT voice_id 
                        FROM tts_voice_segments vs
                        JOIN tts_text_segments ts ON vs.text_segment_id = ts.id
                        WHERE ts.track_id = :track_id
                    """)
                    voice_results = db.execute(voice_query, {"track_id": track_id}).fetchall()
                    db_voices = [row[0] for row in voice_results if row[0]]
                    available_voices.extend(db_voices)
                    logger.info(f"🎤 Found voices in database: {db_voices}")
                except Exception as db_error:
                    logger.warning(f"Could not query database for voices: {db_error}")

            # Strategy 4: Fallback to common voices if none found
            if not available_voices:
                logger.warning(f"🎤 No voices found for track {track_id}, trying common voices")
                available_voices = [
                    'en-US-AvaNeural', 'en-US-AriaNeural', 'en-US-GuyNeural',
                    'en-US-JennyNeural', 'en-US-ChristopherNeural'
                ]

            # Remove duplicates
            available_voices = list(set(available_voices))
            logger.info(f"🎤 Will attempt to delete {len(available_voices)} voice files for track {track_id}")

            # Delete each voice file from S4
            for voice in available_voices:
                try:
                    total_attempts += 1
                    s4_filename = f"tts_{track_id}_{voice}.mp3"
                    s4_path = f"/media/audio/{s4_filename}"

                    logger.info(f"🎤 Attempting to delete voice file: {s4_path}")
                    await storage.delete_media(s4_path)
                    deleted_count += 1
                    logger.info(f"✅ Successfully deleted voice file: {s4_filename}")

                except Exception as voice_error:
                    logger.error(f"❌ Error deleting voice {voice}: {str(voice_error)}")
                    continue

            logger.info(f"🎤 TTS deletion summary: {deleted_count}/{total_attempts} voice files attempted")
            return total_attempts > 0

        except Exception as e:
            logger.error(f"❌ Error in delete_all_tts_voices_for_track: {str(e)}")
            return False

    async def get_album(
        self,
        album_id: str,
        user_id: int,
        check_access: bool = True
    ) -> Optional[Album]:
        """Get album with proper tier access check"""
        try:
            album = self.db.query(Album).filter(Album.id == album_id).first()
            if not album:
                return None

            if check_access:
                user = self.db.query(User).filter(User.id == user_id).first()
                if not user:
                    return None

                # Creators and team members always have access
                if user.is_creator or user.is_team:
                    logger.info(f"User {user.email} is creator/team - granted access")
                    return album

                # Check if album has tier restrictions
                restrictions = album.tier_restrictions or {}

                # Check if is_restricted is explicitly True
                is_restricted = restrictions.get("is_restricted")
                if is_restricted is not True:  # Only restrict if explicitly True
                    logger.info(f"Album {album_id} is not restricted - granted access to {user.email}")
                    return album

                # At this point, we know the album is restricted
                logger.info(f"Album {album_id} is restricted - checking tier criteria for {user.email}")

                # Get user's tier data
                tier_data = user.patreon_tier_data or {}

                # Get user amount and required amount - simple amount comparison
                user_amount = tier_data.get("amount_cents", 0)
                required_amount = restrictions.get("minimum_tier_amount", 0)

                logger.info(f"Access check: User amount={user_amount}, Required amount={required_amount}")

                # Direct amount comparison instead of tier name comparison
                if user_amount >= required_amount:
                    logger.info(f"User {user.email} meets tier amount criteria - granted access")
                    return album

                # Access denied
                logger.info(f"User {user.email} does not meet tier criteria - denied access")
                return None

            return album

        except Exception as e:
            logger.error(f"Error getting album: {str(e)}", exc_info=True)
            raise
    async def get_creator_albums(
        self,
        creator_id: int,
        user_id: Optional[int] = None,
        include_restricted: bool = True,
        page: int = 1,
        per_page: int = 20
    ) -> Dict:
        """Get paginated albums for creator with optional access filtering"""
        try:
            # Base query
            query = self.db.query(Album).filter(
                Album.created_by_id == creator_id
            )

            # Filter restricted content if needed
            if not include_restricted and user_id:
                user = self.db.query(User).filter(User.id == user_id).first()
                if user and not (user.is_creator or user.is_team):
                    user_amount = user.patreon_tier_data.get("amount_cents", 0) if user.patreon_tier_data else 0
                    query = query.filter(
                        or_(
                            Album.tier_restrictions.is_(None),
                            Album.tier_restrictions['is_restricted'].astext.cast(Boolean) == False,
                            Album.tier_restrictions['minimum_tier_amount'].astext.cast(Integer) <= user_amount
                        )
                    )

            # Get total count
            total = query.count()

            # Get paginated albums
            albums = query.order_by(desc(Album.created_at))\
                         .offset((page - 1) * per_page)\
                         .limit(per_page)\
                         .all()

            return {
                "items": [album.to_dict() for album in albums],
                "total": total,
                "page": page,
                "per_page": per_page,
                "pages": (total + per_page - 1) // per_page
            }

        except Exception as e:
            logger.error(f"Error getting albums: {str(e)}")
            raise

    async def add_track(
        self,
        album_id: str,
        title: str,
        file_path: str,
        creator_id: int
    ) -> Track:
        """Add track to album"""
        try:
            # Verify album exists and creator has access
            album = self.db.query(Album).filter(
                and_(
                    Album.id == album_id,
                    Album.created_by_id == creator_id
                )
            ).first()

            if not album:
                raise HTTPException(status_code=404, detail="Album not found")

            # Create track
            track = Track(
                id=str(uuid4()),
                title=title,
                file_path=file_path,
                album_id=album_id,
                created_by_id=creator_id,
                created_at=datetime.now(timezone.utc)
            )

            self.db.add(track)
            self.db.commit()
            self.db.refresh(track)

            return track

        except Exception as e:
            self.db.rollback()
            logger.error(f"Error adding track: {str(e)}")
            raise

    async def update_tier_restrictions(
        self,
        album_id: str,
        tier_data: Dict,
        creator_id: int
    ) -> Album:
        """Update album tier restrictions"""
        try:
            # Get album
            album = self.db.query(Album).filter(
                and_(
                    Album.id == album_id,
                    Album.created_by_id == creator_id
                )
            ).first()

            if not album:
                raise HTTPException(status_code=404, detail="Album not found")

            # Verify tier if provided
            minimum_tier = tier_data.get('minimum_tier')
            if minimum_tier:
                tier = self.db.query(CampaignTier).filter(
                    and_(
                        CampaignTier.creator_id == creator_id,
                        CampaignTier.title == minimum_tier,
                        CampaignTier.is_active == True
                    )
                ).first()

                if tier:
                    album.tier_restrictions = {
                        "is_restricted": True,
                        "creator_id": creator_id,
                        "minimum_tier": tier.title,
                        "minimum_tier_amount": tier.amount_cents,
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }
                else:
                    raise HTTPException(status_code=400, detail="Invalid tier")
            else:
                # Remove restrictions
                album.tier_restrictions = {
                    "is_restricted": False,
                    "creator_id": creator_id,
                    "minimum_tier": None,
                    "minimum_tier_amount": 0,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }

            album.updated_at = datetime.now(timezone.utc)

            # ✅ SECURITY: Bump content_version on all tracks when tier restrictions change
            # This invalidates all existing authorization tokens
            tracks = self.db.query(Track).filter(Track.album_id == album_id).all()
            for track in tracks:
                old_version = track.content_version or 0
                track.content_version = old_version + 1

            self.db.commit()
            self.db.refresh(album)

            # ✅ Invalidate all authorization grants for this album
            try:
                from authorization_service import invalidate_on_tier_change
                import asyncio
                asyncio.create_task(invalidate_on_tier_change(album_id, self.db))
            except Exception as e:
                logger.warning(f"Failed to invalidate grants for album {album_id}: {e}")

            return album

        except Exception as e:
            self.db.rollback()
            logger.error(f"Error updating tier restrictions: {str(e)}")
            raise

    async def delete_album(self, album_id: str, creator_id: int, requesting_user: User = None) -> Dict:
        """
        Delete album and all associated data with proper transaction handling and TTS support
        """
        from sqlalchemy import text

        try:
            # Get album with tracks
            album = self.db.query(Album).options(
                joinedload(Album.tracks)
            ).filter(Album.id == album_id).first()

            if not album:
                raise HTTPException(status_code=404, detail="Album not found")

            # Permission check
            if requesting_user:
                if requesting_user.is_creator:
                    if album.created_by_id != creator_id:
                        raise HTTPException(status_code=403, detail="Not authorized to delete this album")
                elif requesting_user.is_team:
                    if album.created_by_id != requesting_user.created_by:
                        raise HTTPException(status_code=403, detail="Not authorized to delete this album")
                else:
                    raise HTTPException(status_code=403, detail="Not authorized to delete albums")

            # Store album data for notification
            album_data = {
                "id": str(album.id),
                "title": album.title,
                "track_count": len(album.tracks) if album.tracks else 0,
                "deleted_by": requesting_user.username if requesting_user else "system"
            }

            deletion_report = {
                "tracks_cleaned": [],
                "storage_deleted": [],
                "hls_cleaned": [],
                "database_cleanup": [],
                "errors": []
            }

            track_ids = [str(track.id) for track in album.tracks]
            logger.info(f"Starting album deletion: {album_id} with {len(track_ids)} tracks")

            # 1. TTS CLEANUP FOR ALL TRACKS
            try:
                tts_tracks = [track for track in album.tracks if hasattr(track, 'track_type') and track.track_type == 'tts']

                if tts_tracks:
                    tts_track_ids = [track.id for track in tts_tracks]
                    logger.info(f"Found {len(tts_tracks)} TTS tracks to clean up")
                    
                    # CANCEL ACTIVE TTS GENERATION
                    for tts_track in tts_tracks:
                        try:
                            from enhanced_tts_voice_service import enhanced_voice_tts_service
                            
                            # Find and cancel any active jobs for this track
                            for user_id, limiter in enhanced_voice_tts_service.user_job_manager._user_limiters.items():
                                async with limiter._condition:
                                    for job_id in list(limiter.desired.keys()):
                                        job_info = enhanced_voice_tts_service.user_job_manager._user_jobs.get(user_id, {}).get(job_id)
                                        if job_info and job_info.get('track_id') == tts_track.id:
                                            logger.info(f"Cancelling TTS for track {tts_track.id} during album deletion")
                                            await enhanced_voice_tts_service.cancel_job(job_id, tts_track.id)
                        except Exception as cancel_error:
                            logger.warning(f"Could not cancel TTS job for track {tts_track.id}: {cancel_error}")
                    
                    # DATABASE CLEANUP - Delete in correct order (foreign key dependencies)
                    word_timings_deleted = self.db.execute(
                        text("""
                            DELETE FROM tts_word_timings 
                            WHERE segment_id IN (
                                SELECT id FROM tts_text_segments WHERE track_id = ANY(:track_ids)
                            )
                        """), {"track_ids": tts_track_ids}
                    ).rowcount

                    voice_segments_deleted = self.db.execute(
                        text("""
                            DELETE FROM tts_voice_segments 
                            WHERE text_segment_id IN (
                                SELECT id FROM tts_text_segments WHERE track_id = ANY(:track_ids)
                            )
                        """), {"track_ids": tts_track_ids}
                    ).rowcount

                    text_segments_deleted = self.db.execute(
                        text("DELETE FROM tts_text_segments WHERE track_id = ANY(:track_ids)"),
                            {"track_ids": tts_track_ids}
                    ).rowcount

                    meta_deleted = self.db.execute(
                        text("DELETE FROM tts_track_meta WHERE track_id = ANY(:track_ids)"),
                        {"track_ids": tts_track_ids}
                    ).rowcount

                    self.db.flush()

                    deletion_report["tts_cleanup"] = {
                        "tracks": len(tts_tracks),
                        "word_timings": word_timings_deleted,
                        "voice_segments": voice_segments_deleted,
                        "text_segments": text_segments_deleted,
                        "track_meta": meta_deleted
                    }

                    logger.info(f"TTS cleanup complete: {meta_deleted} meta, {text_segments_deleted} segments, {voice_segments_deleted} voice_segments, {word_timings_deleted} word_timings")

            except Exception as tts_error:
                logger.error(f"TTS cleanup error: {tts_error}")
                self.db.rollback()
                deletion_report["errors"].append(f"TTS cleanup error: {str(tts_error)}")
                raise

            # 2. COMMENTS CLEANUP
            try:
                if track_ids:
                    comment_ids_result = self.db.execute(
                        text("SELECT id FROM comments WHERE track_id = ANY(:track_ids)"),
                        {"track_ids": track_ids}
                    )
                    comment_ids = [row[0] for row in comment_ids_result.fetchall()]

                    if comment_ids:
                        like_count = self.db.execute(
                            text("DELETE FROM comment_likes WHERE comment_id = ANY(:comment_ids)"),
                            {"comment_ids": comment_ids}
                        ).rowcount

                        report_count = self.db.execute(
                            text("DELETE FROM comment_reports WHERE comment_id = ANY(:comment_ids)"),
                            {"comment_ids": comment_ids}
                        ).rowcount

                        comment_count = self.db.execute(
                            text("DELETE FROM comments WHERE track_id = ANY(:track_ids)"),
                            {"track_ids": track_ids}
                        ).rowcount

                        deletion_report["comments_deleted"] = comment_count
                        deletion_report["comment_likes_deleted"] = like_count
                        deletion_report["comment_reports_deleted"] = report_count

                        self.db.flush()
                        logger.info(f"Comments cleanup: {comment_count} comments, {like_count} likes, {report_count} reports")

            except Exception as comment_error:
                logger.error(f"Comment cleanup error: {comment_error}")
                deletion_report["errors"].append(f"Comment cleanup error: {str(comment_error)}")

            # 3. OTHER DATABASE CLEANUP
            try:
                if track_ids:
                    plays_deleted = self.db.execute(
                        text("DELETE FROM track_plays WHERE track_id = ANY(:track_ids)"),
                        {"track_ids": track_ids}
                    ).rowcount

                    progress_deleted = self.db.execute(
                        text("DELETE FROM playback_progress WHERE track_id = ANY(:track_ids)"),
                        {"track_ids": track_ids}
                    ).rowcount

                    track_downloads_deleted = self.db.execute(
                        text("DELETE FROM user_downloads WHERE track_id = ANY(:track_ids)"),
                        {"track_ids": track_ids}
                    ).rowcount

                    deletion_report["track_plays_deleted"] = plays_deleted
                    deletion_report["progress_deleted"] = progress_deleted
                    deletion_report["track_downloads_deleted"] = track_downloads_deleted

                    self.db.flush()

                album_downloads_deleted = self.db.execute(
                    text("DELETE FROM user_downloads WHERE album_id = :album_id"),
                    {"album_id": album_id}
                ).rowcount

                management_deleted = self.db.execute(
                    text("DELETE FROM user_album_management WHERE album_id = :album_id"),
                    {"album_id": album_id}
                ).rowcount

                deletion_report["album_downloads_deleted"] = album_downloads_deleted
                deletion_report["management_deleted"] = management_deleted

                self.db.flush()

            except Exception as db_error:
                logger.error(f"Database cleanup error: {db_error}")
                deletion_report["errors"].append(f"Database cleanup error: {str(db_error)}")

            # 4. DELETE TRACKS
            try:
                if track_ids:
                    tracks_deleted = self.db.execute(
                        text("DELETE FROM tracks WHERE album_id = :album_id"),
                        {"album_id": album_id}
                    ).rowcount

                    deletion_report["tracks_deleted"] = tracks_deleted
                    self.db.flush()
                    logger.info(f"Deleted {tracks_deleted} tracks")

            except Exception as tracks_error:
                logger.error(f"Tracks deletion error: {tracks_error}")
                self.db.rollback()
                deletion_report["errors"].append(f"Tracks deletion error: {str(tracks_error)}")
                raise

            # 5. DELETE ALBUM
            try:
                album_deleted = self.db.execute(
                    text("DELETE FROM albums WHERE id = :album_id"),
                    {"album_id": album_id}
                ).rowcount

                if album_deleted == 0:
                    raise Exception("Album deletion failed - no rows affected")

                self.db.flush()
                deletion_report["album_deleted"] = True
                logger.info(f"Album {album_id} deleted from database")

            except Exception as album_error:
                logger.error(f"Album deletion error: {album_error}")
                self.db.rollback()
                deletion_report["errors"].append(f"Album deletion error: {str(album_error)}")
                raise

            # 6. EXTERNAL CLEANUP (HLS, Storage)
            try:
                for track in album.tracks:
                    try:
                        # HLS cleanup
                        await stream_manager.cleanup_stream(str(track.id))
                        deletion_report["hls_cleaned"].append(track.id)

                        # Storage cleanup
                        if track.file_path:
                            track_type = getattr(track, 'track_type', 'audio')

                            if track_type == 'tts':
                                logger.info(f"Album deletion - TTS track: deleting ALL voices for track {track.id}")

                                tts_deletion_success = await storage.delete_all_tts_voices_for_track(
                                    track_id=str(track.id),
                                    track=track,
                                    db=self.db
                                )

                                if tts_deletion_success:
                                    deletion_report["storage_deleted"].append(f"TTS voices for {track.id}")
                                else:
                                    deletion_report["errors"].append(f"TTS voice deletion attempted for {track.id}")
                            else:
                                await storage.delete_media(track.file_path)
                                deletion_report["storage_deleted"].append(track.file_path)

                        deletion_report["tracks_cleaned"].append(str(track.id))

                    except Exception as track_cleanup_error:
                        logger.warning(f"Track {track.id} cleanup error: {track_cleanup_error}")
                        deletion_report["errors"].append(f"Track {track.id} cleanup: {str(track_cleanup_error)}")

                # Delete album cover
                if album.cover_path:
                    try:
                        await storage.delete_media(album.cover_path)
                        deletion_report["storage_deleted"].append(album.cover_path)
                    except Exception as cover_error:
                        logger.warning(f"Cover cleanup error: {cover_error}")

            except Exception as external_error:
                logger.warning(f"External cleanup error (non-critical): {external_error}")

            # COMMIT TRANSACTION
            self.db.commit()

            # Log activity after successful deletion
            try:
                from activity_logs_router import log_activity_isolated
                from models import AuditLogType

                user_id = requesting_user.id if requesting_user else creator_id
                await log_activity_isolated(
                    user_id=user_id,
                    action_type=AuditLogType.DELETE,
                    table_name='albums',
                    record_id=album_id,
                    description=f"Deleted album '{album_data['title']}' ({album_data['track_count']} tracks)"
                )
            except Exception as e:
                logger.warning(f"Failed to log album deletion activity: {e}")

            # Discord notification
            try:
                await on_album_deleted(self.db, album_data)
            except Exception as discord_error:
                logger.warning(f"Discord notification failed: {discord_error}")

            logger.info(f"Successfully deleted album {album_id} and all associated data")
            logger.info(f"Cleaned up {len(deletion_report['tracks_cleaned'])} tracks")

            return {
                "status": "success",
                "message": "Album and all associated data deleted successfully",
                "deletion_report": deletion_report
            }

        except Exception as e:
            self.db.rollback()
            logger.error(f"Album deletion failed: {str(e)}")
            raise

    async def bulk_update_tiers(
        self,
        album_ids: List[str],
        tier_data: Dict,
        creator_id: int
    ) -> Dict:
        """Bulk update tier restrictions for multiple albums"""
        try:
            updated_count = 0

            # Verify tier if provided
            minimum_tier = tier_data.get('minimum_tier')
            tier = None
            if minimum_tier:
                tier = self.db.query(CampaignTier).filter(
                    and_(
                        CampaignTier.creator_id == creator_id,
                        CampaignTier.title == minimum_tier,
                        CampaignTier.is_active == True
                    )
                ).first()

                if not tier:
                    raise HTTPException(status_code=400, detail="Invalid tier")

            # Update albums
            albums = self.db.query(Album).filter(
                and_(
                    Album.id.in_(album_ids),
                    Album.created_by_id == creator_id
                )
            ).all()

            updated_albums = []
            for album in albums:
                if minimum_tier and tier:
                    album.tier_restrictions = {
                        "is_restricted": True,
                        "creator_id": creator_id,
                        "minimum_tier": tier.title,
                        "minimum_tier_amount": tier.amount_cents,
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }
                else:
                    album.tier_restrictions = {
                        "is_restricted": False,
                        "creator_id": creator_id,
                        "minimum_tier": None,
                        "minimum_tier_amount": 0,
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }

                album.updated_at = datetime.now(timezone.utc)
                updated_count += 1
                updated_albums.append({
                    "id": str(album.id),
                    "title": album.title
                })

                # ✅ SECURITY: Bump content_version on all tracks when tier restrictions change
                # This invalidates all existing authorization tokens
                tracks = self.db.query(Track).filter(Track.album_id == album.id).all()
                for track in tracks:
                    old_version = track.content_version or 0
                    track.content_version = old_version + 1

            self.db.commit()

            # Get creator name for notification
            creator_obj = self.db.query(User).filter(User.id == creator_id).first()
            creator_name = creator_obj.username if creator_obj else None

            # Notify Discord about bulk update
            await on_bulk_update(self.db, updated_albums, "tier_change", creator_name)

            return {
                "status": "success",
                "updated_count": updated_count,
                "message": f"Successfully updated {updated_count} albums"
            }

        except Exception as e:
            self.db.rollback()
            logger.error(f"Error in bulk tier update: {str(e)}")
            raise

    async def update_album(
        self,
        album_id: str,
        title: Optional[str] = None,
        cover_path: Optional[str] = None,
        tier_data: Optional[Dict] = None,
        visibility_status: Optional[str] = None,
        creator_id: int = None
    ) -> Album:
        """
        Update album details including title, cover, and tier restrictions.

        Args:
            album_id: UUID of album to update
            title: Optional new title
            cover_path: Optional new cover image path
            tier_data: Optional tier restriction data
            creator_id: ID of creator making the update

        Returns:
            Updated Album instance

        Raises:
            HTTPException: For validation or permission errors
        """
        try:
            # Get existing album with creator check
            album = self.db.query(Album).filter(
                and_(
                    Album.id == album_id,
                    Album.created_by_id == creator_id
                )
            ).first()

            if not album:
                raise HTTPException(status_code=404, detail="Album not found")

            # Update basic fields if provided
            if title is not None:
                album.title = title

            if cover_path is not None:
                album.cover_path = cover_path

            if visibility_status is not None:
                album.visibility_status = visibility_status

            # Track if this is a tier change
            is_tier_change = tier_data is not None

            # Handle tier restrictions
            if tier_data is not None:
                minimum_tier = tier_data.get('minimum_tier')
                if minimum_tier:
                    # Verify tier exists
                    tier = self.db.query(CampaignTier).filter(
                        and_(
                            CampaignTier.creator_id == creator_id,
                            CampaignTier.title == minimum_tier,
                            CampaignTier.is_active == True
                        )
                    ).first()

                    if tier:
                        album.tier_restrictions = {
                            "is_restricted": True,
                            "creator_id": creator_id,
                            "minimum_tier": tier.title,
                            "minimum_tier_amount": tier.amount_cents,
                            "updated_at": datetime.now(timezone.utc).isoformat()
                        }
                    else:
                        raise HTTPException(status_code=400, detail=f"Invalid tier: {minimum_tier}")
                else:
                    # Remove restrictions
                    album.tier_restrictions = {
                        "is_restricted": False,
                        "creator_id": creator_id,
                        "minimum_tier": None,
                        "minimum_tier_amount": 0,
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }

            # Update timestamp
            album.updated_at = datetime.now(timezone.utc)

            # Save changes
            self.db.commit()
            self.db.refresh(album)

            # Notify Discord about the album update
            await on_album_updated(self.db, album_id, is_tier_change)

            logger.info(f"Successfully updated album {album_id}")
            return album

        except HTTPException:
            self.db.rollback()
            raise
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error updating album: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Error updating album: {str(e)}")

    async def get_popular_albums(
        self,
        creator_id: int,
        limit: int = 10
    ) -> List[Dict]:
        """Get most popular albums based on total play counts"""
        try:
            # Query albums with joined play counts
            results = (
                self.db.query(
                    Album,
                    func.sum(TrackPlays.play_count).label('total_plays'),
                    func.avg(TrackPlays.completion_rate).label('avg_completion'),
                    func.max(TrackPlays.last_played).label('last_played'),
                    func.count(Track.id).label('track_count')
                )
                .join(Track, Album.id == Track.album_id)
                .join(TrackPlays, Track.id == TrackPlays.track_id)
                .filter(Album.created_by_id == creator_id)
                .group_by(Album.id)
                .order_by(text('total_plays DESC'))
                .limit(limit)
                .all()
            )

            # Format results
            popular_albums = []
            for album, total_plays, avg_completion, last_played, track_count in results:
                album_dict = {
                    "id": str(album.id),
                    "title": album.title,
                    "cover_path": album.cover_path or "/media/images/default-album.jpg",
                    "total_plays": int(total_plays) if total_plays else 0,
                    "avg_completion": round(float(avg_completion), 2) if avg_completion else 0.0,
                    "last_played": last_played.isoformat() if last_played else None,
                    "track_count": track_count,
                    "created_at": album.created_at.isoformat() if album.created_at else None,
                    "updated_at": album.updated_at.isoformat() if album.updated_at else None
                }
                popular_albums.append(album_dict)

            return popular_albums

        except Exception as e:
            logger.error(f"Error getting popular albums: {str(e)}")
            return []

    async def increment_track_plays(
        self, 
        track_id: str,
        user_id: int,
        completion_rate: float = None,
        play_time: float = None,
        device_info: dict = None,
        ip_address: str = None,
        user_agent: str = None
    ) -> bool:
        """Increment play count for a track"""
        try:
            # Find existing play record
            play_record = self.db.query(TrackPlays).filter(
                and_(
                    TrackPlays.track_id == track_id,
                    TrackPlays.user_id == user_id
                )
            ).first()

            if play_record:
                # Update existing record
                play_record.increment_play(
                    completion_rate=completion_rate,
                    play_time=play_time,
                    device_info=device_info,
                    ip_address=ip_address,
                    user_agent=user_agent
                )
            else:
                # Create new play record
                play_record = TrackPlays(
                    track_id=track_id,
                    user_id=user_id,
                    play_count=1,
                    completion_rate=completion_rate if completion_rate else 0.0,
                    last_played=datetime.now(timezone.utc),
                    total_play_time=play_time if play_time else 0.0,
                    device_info=device_info or {},
                    ip_address=ip_address,
                    user_agent=user_agent
                )
                self.db.add(play_record)

            self.db.commit()
            return True

        except Exception as e:
            logger.error(f"Error incrementing track plays: {str(e)}")
            self.db.rollback()
            return False

    async def get_recently_added_albums(
        self,
        limit: int = 25,
        user_id: Optional[int] = None,
        days: int = 30  # Look back period
    ) -> List[Dict]:
        """Get recently added albums within specified days"""
        try:
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)

            # Base query for recently added albums
            query = (
                self.db.query(Album)
                .filter(Album.created_at >= cutoff_date)
                .options(joinedload(Album.tracks))  # Eager load tracks
            )

            # Filter based on user access if user_id provided
            if user_id:
                user = self.db.query(User).filter(User.id == user_id).first()
                if user and not (user.is_creator or user.is_team):
                    user_amount = user.patreon_tier_data.get("amount_cents", 0) if user.patreon_tier_data else 0
                    query = query.filter(
                        or_(
                            Album.tier_restrictions.is_(None),
                            Album.tier_restrictions['is_restricted'].astext.cast(Boolean) == False,
                            Album.tier_restrictions['minimum_tier_amount'].astext.cast(Integer) <= user_amount
                        )
                    )

            albums = (
                query.order_by(desc(Album.created_at))
                .limit(limit)
                .all()
            )

            return [{
                **album.to_dict(),
                "track_count": len(album.tracks),
                "latest_track": max([t.created_at for t in album.tracks]) if album.tracks else None,
                "duration": sum([t.duration or 0 for t in album.tracks])
            } for album in albums]

        except Exception as e:
            logger.error(f"Error getting recently added albums: {str(e)}")
            return []

    async def get_recently_updated_albums(
        self,
        limit: int = 25,
        user_id: Optional[int] = None,
        days: int = 30
    ) -> List[Dict]:
        """Get albums with recent track additions"""
        try:
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)

            # Subquery to get latest track creation date per album
            latest_track_dates = (
                self.db.query(
                    Track.album_id,
                    func.max(Track.created_at).label('latest_track_date')
                )
                .group_by(Track.album_id)
                .having(func.max(Track.created_at) >= cutoff_date)
                .subquery()
            )

            # Main query joining with latest track dates
            query = (
                self.db.query(Album)
                .join(latest_track_dates, Album.id == latest_track_dates.c.album_id)
                .options(joinedload(Album.tracks))
            )

            # Apply user access filters
            if user_id:
                user = self.db.query(User).filter(User.id == user_id).first()
                if user and not (user.is_creator or user.is_team):
                    user_amount = user.patreon_tier_data.get("amount_cents", 0) if user.patreon_tier_data else 0
                    query = query.filter(
                        or_(
                            Album.tier_restrictions.is_(None),
                            Album.tier_restrictions['is_restricted'].astext.cast(Boolean) == False,
                            Album.tier_restrictions['minimum_tier_amount'].astext.cast(Integer) <= user_amount
                        )
                    )

            albums = (
                query.order_by(desc(latest_track_dates.c.latest_track_date))
                .limit(limit)
                .all()
            )

            return [{
                **album.to_dict(),
                "track_count": len(album.tracks),
                "latest_track": max([t.created_at for t in album.tracks]) if album.tracks else None,
                "duration": sum([t.duration or 0 for t in album.tracks])
            } for album in albums]

        except Exception as e:
            logger.error(f"Error getting recently updated albums: {str(e)}")
            return []
