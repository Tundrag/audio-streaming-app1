# hls_storage_config.py

# GLOBAL STORAGE CONFIGURATION - EASILY MODIFIABLE
HLS_STORAGE_LIMIT_GB = 2000.0  #  limit for testing - change this value as needed

import asyncio
import logging
import shutil
import os
import stat
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import func
from storage_reader import AsyncStorageReader

logger = logging.getLogger(__name__)


async def check_hls_storage_before_track_creation(
    hls_base_dir: Path,
    db: Session,
    track_id: str,
    file_url: Optional[str] = None  # Add file_url for S4 cleanup
) -> Dict:
    """
    Check HLS storage before creating track folder. Uses global HLS_STORAGE_LIMIT_GB.
    
    Args:
        hls_base_dir: HLS base directory path
        db: Database session
        track_id: Track ID for logging
        file_url: File URL for S4 cleanup if storage fails
        
    Returns:
        Dict with check results and cleanup info
    """
    storage_reader = AsyncStorageReader()
    
    try:
        # Get current HLS directory size
        hls_info = await storage_reader.get_hls_directory_info(hls_base_dir)
        current_size_gb = hls_info['total_size_gb']
        usage_percent = (current_size_gb / HLS_STORAGE_LIMIT_GB) * 100
        
        logger.info(f"HLS Storage Check for {track_id}: {current_size_gb:.1f}GB / {HLS_STORAGE_LIMIT_GB}GB ({usage_percent:.1f}%)")
        
        result = {
            'can_create': True,
            'current_size_gb': current_size_gb,
            'limit_gb': HLS_STORAGE_LIMIT_GB,
            'usage_percent': usage_percent,
            'cleanup_performed': False,
            'tracks_removed': [],
            'space_freed_gb': 0.0,
            's4_cleanup_performed': False
        }
        
        # If under limit, good to go
        if current_size_gb < HLS_STORAGE_LIMIT_GB:
            logger.info(f"✅ Storage OK for {track_id} - under limit")
            return result
        
        logger.warning(f"🚨 Storage over limit for {track_id} - starting cleanup")
        
        # Enhanced cleanup until under limit
        tracks_removed = []
        space_freed = 0.0
        failed_removals = []
        max_cleanup_attempts = 15  # Increased attempts
        cleanup_attempts = 0
        
        while current_size_gb >= HLS_STORAGE_LIMIT_GB and cleanup_attempts < max_cleanup_attempts:
            cleanup_attempts += 1
            logger.info(f"Cleanup attempt {cleanup_attempts}/{max_cleanup_attempts}")
            
            # Find worst track
            worst_track = await _find_worst_track(hls_base_dir, db, storage_reader)
            
            if not worst_track:
                logger.error(f"❌ No more tracks available for cleanup for {track_id}")
                break
            
            # Skip tracks we already failed to remove
            if worst_track['track_id'] in failed_removals:
                logger.warning(f"⚠️ Skipping previously failed track: {worst_track['track_id']}")
                continue
            
            # Remove the track
            removed = await _remove_track(hls_base_dir, worst_track['track_id'], db)
            
            if removed['success']:
                tracks_removed.append({
                    'track_id': worst_track['track_id'],
                    'size_gb': worst_track['size_gb'],
                    'play_count': worst_track['play_count'],
                    'age_days': worst_track['age_days']
                })
                space_freed += worst_track['size_gb']
                
                logger.info(f"🗑️ Removed {worst_track['track_id']} ({worst_track['size_gb']:.2f}GB, {worst_track['play_count']} plays)")
                
                # Check new size
                hls_info = await storage_reader.get_hls_directory_info(hls_base_dir)
                current_size_gb = hls_info['total_size_gb']
                logger.info(f"📊 New HLS size: {current_size_gb:.1f}GB / {HLS_STORAGE_LIMIT_GB}GB")
                
            else:
                # Track removal failed - add to failed list and continue with next track
                failed_removals.append(worst_track['track_id'])
                logger.warning(f"⚠️ Failed to remove track {worst_track['track_id']}, continuing with next candidate")
                continue
        
        # Check final status
        if current_size_gb < HLS_STORAGE_LIMIT_GB:
            result['can_create'] = True
            if failed_removals:
                logger.info(f"✅ Storage under limit after cleanup, despite {len(failed_removals)} failed removals")
        else:
            # Still over limit - cleanup failed
            logger.error(f"❌ Could not free enough space after {cleanup_attempts} attempts")
            result['can_create'] = False
            result['error'] = f'Could not free enough space. Removed {len(tracks_removed)} tracks, failed to remove {len(failed_removals)} tracks.'
            
            # Clean up S4 file if provided and cleanup completely failed
            if file_url and len(tracks_removed) == 0:  # Only if we couldn't remove ANY tracks
                logger.warning(f"🧹 Attempting S4 cleanup for failed upload: {file_url}")
                s4_cleanup_result = await _cleanup_s4_file(file_url)
                result['s4_cleanup_performed'] = s4_cleanup_result['success']
                if s4_cleanup_result['success']:
                    logger.info(f"✅ Successfully cleaned up S4 file: {file_url}")
                else:
                    logger.error(f"❌ Failed to cleanup S4 file: {s4_cleanup_result.get('error', 'Unknown error')}")
        
        # Update result with cleanup details
        result.update({
            'current_size_gb': current_size_gb,
            'usage_percent': (current_size_gb / HLS_STORAGE_LIMIT_GB) * 100,
            'cleanup_performed': len(tracks_removed) > 0,
            'tracks_removed': tracks_removed,
            'space_freed_gb': space_freed,
            'cleanup_attempts': cleanup_attempts
        })
        
        # Add information about failed removals
        if failed_removals:
            result['failed_removals'] = failed_removals
            result['cleanup_notes'] = f'Successfully removed {len(tracks_removed)} tracks, failed to remove {len(failed_removals)} tracks'
        
        if result['can_create']:
            logger.info(f"✅ Storage OK for {track_id} after cleanup - {len(tracks_removed)} tracks removed, {space_freed:.2f}GB freed")
        
        return result
        
    except Exception as e:
        logger.error(f"❌ Error in HLS storage check for {track_id}: {str(e)}")
        return {
            'can_create': False,
            'error': str(e),
            'cleanup_performed': False,
            'tracks_removed': [],
            'space_freed_gb': 0.0
        }
    finally:
        await storage_reader.cleanup()


async def _cleanup_s4_file(file_url: str) -> Dict:
    """Clean up S4 file when HLS storage cleanup fails completely"""
    try:
        # Extract filename from file_url
        if not file_url:
            return {'success': False, 'error': 'No file URL provided'}
        
        # Get filename from URL (e.g., "/media/audio/filename.mp3" -> "filename.mp3")
        filename = Path(file_url).name
        
        # Import and use S4 client
        try:
            from mega_s4_client import mega_s4_client
            
            if not mega_s4_client._started:
                await mega_s4_client.start()
            
            # Generate object key for audio file
            object_key = mega_s4_client.generate_object_key(filename, prefix="audio")
            
            # Delete from S4
            success = await mega_s4_client.delete_object(object_key)
            
            if success:
                logger.info(f"✅ Successfully deleted S4 object: {object_key}")
                return {'success': True, 'object_key': object_key}
            else:
                logger.error(f"❌ Failed to delete S4 object: {object_key}")
                return {'success': False, 'error': f'S4 deletion failed for {object_key}'}
                
        except ImportError:
            logger.error("❌ Could not import mega_s4_client for cleanup")
            return {'success': False, 'error': 'S4 client not available'}
        except Exception as s4_error:
            logger.error(f"❌ S4 cleanup error: {str(s4_error)}")
            return {'success': False, 'error': str(s4_error)}
            
    except Exception as e:
        logger.error(f"❌ Error in S4 cleanup: {str(e)}")
        return {'success': False, 'error': str(e)}


async def _find_worst_track(hls_base_dir: Path, db: Session, storage_reader: AsyncStorageReader) -> Optional[Dict]:
    """Find the worst track (oldest + least popular) for removal"""
    try:
        # Get all tracks with sizes from HLS directory
        hls_info = await storage_reader.get_hls_directory_info(hls_base_dir)
        
        if not hls_info['tracks']:
            return None
        
        # Import models (exactly as defined in your models.py)
        from models import Track, TrackPlays
        
        # Get track stats from database with correct relationships
        tracks_query = (
            db.query(
                Track.id,
                Track.created_at,
                func.coalesce(func.sum(TrackPlays.play_count), 0).label('total_plays'),
                func.max(TrackPlays.last_played).label('last_played')
            )
            .outerjoin(TrackPlays)  # LEFT JOIN to include tracks with 0 plays
            .group_by(Track.id, Track.created_at)
            .all()
        )
        
        # Create lookup dictionary for database info
        db_tracks = {str(track.id): track for track in tracks_query}
        
        # Score each track that exists in both HLS directory and database
        candidates = []
        current_time = datetime.now(timezone.utc)
        
        for track in hls_info['tracks']:
            track_id = track['track_id']
            
            # Skip tracks not found in database
            if track_id not in db_tracks:
                logger.warning(f"Track {track_id} found in HLS but not in database")
                continue
                
            db_track = db_tracks[track_id]
            
            # Calculate track age in days
            age_days = (current_time - db_track.created_at).total_seconds() / 86400
            
            # Calculate days since last played (for tracks that have been played)
            if db_track.last_played:
                days_since_played = (current_time - db_track.last_played).total_seconds() / 86400
            else:
                days_since_played = age_days  # Never played = as old as creation
            
            # Scoring algorithm - higher score = better candidate for removal
            # Factor 1: Age score (older = higher score)
            age_score = min(age_days / 365, 1.0)  # Normalize to max 1 year
            
            # Factor 2: Play score (fewer plays = higher score)
            play_score = 1 / (db_track.total_plays + 1)  # +1 to avoid division by zero
            
            # Factor 3: Recent activity score (not played recently = higher score)
            recency_score = min(days_since_played / 90, 1.0)  # Normalize to 90 days
            
            # Factor 4: Size score (larger files = slightly higher score for cleanup)
            size_score = min(track['size_gb'] / 5.0, 0.2)  # Small weight, max 0.2
            
            # Combined score (range: 0-3.2, higher = worse track)
            total_score = age_score + play_score + recency_score + size_score
            
            candidates.append({
                'track_id': track_id,
                'size_gb': track['size_gb'],
                'play_count': db_track.total_plays,
                'age_days': age_days,
                'days_since_played': days_since_played,
                'score': total_score
            })
        
        if not candidates:
            logger.warning("No valid candidates found for cleanup")
            return None
        
        # Sort by score (highest first = worst tracks first)
        candidates.sort(key=lambda x: x['score'], reverse=True)
        
        # Return the worst track
        worst_track = candidates[0]
        
        logger.info(
            f"Worst track selected: {worst_track['track_id']} "
            f"(plays: {worst_track['play_count']}, age: {worst_track['age_days']:.1f} days, "
            f"size: {worst_track['size_gb']:.2f}GB, score: {worst_track['score']:.2f})"
        )
        
        return worst_track
        
    except Exception as e:
        logger.error(f"Error finding worst track: {str(e)}")
        return None


async def _remove_track(hls_base_dir: Path, track_id: str, db: Session) -> Dict:
    """Remove track files and database records with robust error handling"""
    try:
        # Remove HLS files from filesystem
        track_dir = hls_base_dir / "segments" / track_id
        removal_success = False
        
        if track_dir.exists():
            # Strategy 1: Normal removal
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, 
                    shutil.rmtree, 
                    str(track_dir)
                )
                removal_success = True
                logger.info(f"✅ Removed HLS directory: {track_dir}")
            except Exception as e1:
                logger.warning(f"⚠️ Normal removal failed for {track_dir}: {e1}")
                
                # Strategy 2: Force removal with ignore_errors
                try:
                    await asyncio.get_event_loop().run_in_executor(
                        None, 
                        shutil.rmtree, 
                        str(track_dir),
                        True  # ignore_errors=True
                    )
                    removal_success = True
                    logger.info(f"✅ Force removed HLS directory: {track_dir}")
                except Exception as e2:
                    logger.warning(f"⚠️ Force removal failed for {track_dir}: {e2}")
                    
                    # Strategy 3: Manual recursive removal with permission fixing
                    try:
                        def force_remove_directory(path):
                            def handle_remove_readonly(func, path, exc):
                                try:
                                    # Handle readonly files by changing permissions
                                    if os.path.exists(path):
                                        os.chmod(path, stat.S_IWRITE)
                                        func(path)
                                except Exception:
                                    pass  # Ignore permission errors
                            
                            if os.path.exists(path):
                                shutil.rmtree(path, onerror=handle_remove_readonly)
                        
                        await asyncio.get_event_loop().run_in_executor(
                            None,
                            force_remove_directory,
                            str(track_dir)
                        )
                        
                        # Check if removal was successful
                        if not track_dir.exists():
                            removal_success = True
                            logger.info(f"✅ Manual force removed HLS directory: {track_dir}")
                        else:
                            logger.error(f"❌ Directory still exists after all removal attempts: {track_dir}")
                            
                    except Exception as e3:
                        logger.error(f"❌ All removal strategies failed for {track_dir}: {e3}")
        else:
            removal_success = True  # Directory doesn't exist, consider it removed
        
        # Remove database records (SegmentMetadata for HLS segments)
        # Always attempt database cleanup even if file removal failed
        db_cleanup_success = False
        try:
            from models import SegmentMetadata
            
            # Delete all segment metadata for this track
            deleted_count = db.query(SegmentMetadata).filter(
                SegmentMetadata.track_id == track_id
            ).delete(synchronize_session=False)
            
            db.commit()
            db_cleanup_success = True
            logger.info(f"✅ Removed {deleted_count} segment metadata records for track {track_id}")
            
        except Exception as db_error:
            logger.error(f"❌ Database cleanup error for track {track_id}: {db_error}")
            try:
                db.rollback()
            except Exception:
                pass
        
        # Consider success if either file removal OR database cleanup worked
        # This prevents blocking the entire cleanup process
        overall_success = removal_success or db_cleanup_success
        
        if overall_success:
            return {'success': True}
        else:
            return {
                'success': False, 
                'error': f'Both file and database removal failed for {track_id}',
                'partial': True
            }
        
    except Exception as e:
        logger.error(f"❌ Error removing track {track_id}: {str(e)}")
        return {'success': False, 'error': str(e)}


# Utility functions for easy configuration management
def set_hls_storage_limit(limit_gb: float):
    """Set the global HLS storage limit"""
    global HLS_STORAGE_LIMIT_GB
    HLS_STORAGE_LIMIT_GB = limit_gb
    logger.info(f"HLS storage limit updated to {limit_gb}GB")


def get_hls_storage_limit() -> float:
    """Get the current HLS storage limit"""
    return HLS_STORAGE_LIMIT_GB


async def get_hls_storage_status(hls_base_dir: Path) -> Dict:
    """Get current HLS storage status"""
    storage_reader = AsyncStorageReader()
    
    try:
        hls_info = await storage_reader.get_hls_directory_info(hls_base_dir)
        
        current_size_gb = hls_info['total_size_gb']
        usage_percent = (current_size_gb / HLS_STORAGE_LIMIT_GB) * 100
        
        return {
            'current_size_gb': current_size_gb,
            'limit_gb': HLS_STORAGE_LIMIT_GB,
            'usage_percent': usage_percent,
            'free_gb': HLS_STORAGE_LIMIT_GB - current_size_gb,
            'track_count': hls_info['track_count'],
            'over_limit': current_size_gb >= HLS_STORAGE_LIMIT_GB,
            'status': 'over_limit' if current_size_gb >= HLS_STORAGE_LIMIT_GB else 'ok'
        }
    except Exception as e:
        return {'error': str(e)}
    finally:
        await storage_reader.cleanup()