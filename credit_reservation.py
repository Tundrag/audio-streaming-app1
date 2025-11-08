# credit_reservation.py

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import text, and_
from models import User, DownloadReservation
from redis_state import RedisStateManager

logger = logging.getLogger(__name__)

# Initialize Redis state manager for distributed locking
credit_state = RedisStateManager("credit_reservation")

class CreditReservationService:
    """Service for managing credit reservations atomically"""
    
    @staticmethod
    def get_user_limits(user: User) -> Dict[str, int]:
        """Get user's download limits"""
        if user.is_creator:
            return {
                'album': float('inf'),
                'track': float('inf'), 
                'book': float('inf')
            }
            
        if not user.patreon_tier_data:
            return {'album': 0, 'track': 0, 'book': 0}
            
        return {
            'album': user.patreon_tier_data.get('album_downloads_allowed', 0),
            'track': user.patreon_tier_data.get('track_downloads_allowed', 0),
            'book': user.patreon_tier_data.get('book_requests_allowed', 0)
        }
    
    @staticmethod
    def get_user_usage(user: User) -> Dict[str, int]:
        """Get user's current usage"""
        if not user.patreon_tier_data:
            return {'album': 0, 'track': 0, 'book': 0}
            
        return {
            'album': user.patreon_tier_data.get('album_downloads_used', 0),
            'track': user.patreon_tier_data.get('track_downloads_used', 0),
            'book': user.patreon_tier_data.get('book_requests_used', 0)
        }
    
    @staticmethod
    def reserve_credit(db: Session, user: User, download_id: str, download_type: str, 
                      expiry_hours: int = 2) -> Tuple[bool, str]:
        """
        Atomically reserve a credit for download
        
        Returns:
            Tuple[bool, str]: (success, message)
        """
        try:
            # Check if reservation already exists
            existing = db.query(DownloadReservation).filter(
                DownloadReservation.download_id == download_id
            ).first()
            
            if existing:
                if existing.status == 'reserved' and existing.expires_at > datetime.now(timezone.utc):
                    return True, "Credit already reserved"
                elif existing.status == 'confirmed':
                    return False, "Download already completed"
                else:
                    # Remove expired/failed reservation
                    db.delete(existing)
                    db.flush()
            
            # For creators, always allow
            if user.is_creator:
                expiry = datetime.now(timezone.utc) + timedelta(hours=expiry_hours)
                reservation = DownloadReservation(
                    user_id=user.id,
                    download_id=download_id,
                    download_type=download_type,
                    expires_at=expiry,
                    status='reserved'
                )
                db.add(reservation)
                db.commit()
                return True, "Credit reserved (creator unlimited)"
            
            # Get limits and current usage
            limits = CreditReservationService.get_user_limits(user)
            usage = CreditReservationService.get_user_usage(user)
            
            limit = limits.get(download_type, 0)
            used = usage.get(download_type, 0)
            
            # Count active reservations
            active_reservations = db.execute(text("""
                SELECT COUNT(*) FROM download_reservations 
                WHERE user_id = :user_id 
                AND download_type = :download_type 
                AND status = 'reserved' 
                AND expires_at > NOW()
            """), {
                "user_id": user.id,
                "download_type": download_type
            }).scalar() or 0
            
            total_used = used + active_reservations
            
            # Check if user has enough credits
            if total_used >= limit:
                return False, f"Insufficient {download_type} credits: {used} used + {active_reservations} reserved = {total_used}/{limit}"
            
            # Create reservation
            expiry = datetime.now(timezone.utc) + timedelta(hours=expiry_hours)
            reservation = DownloadReservation(
                user_id=user.id,
                download_id=download_id,
                download_type=download_type,
                expires_at=expiry,
                status='reserved'
            )
            
            db.add(reservation)
            db.commit()
            
            logger.info(f"Reserved {download_type} credit for user {user.id}, download {download_id}")
            return True, f"Credit reserved: {total_used + 1}/{limit} used"
            
        except Exception as e:
            db.rollback()
            logger.error(f"Error reserving credit: {str(e)}")
            return False, f"Error reserving credit: {str(e)}"
    
    @staticmethod
    def confirm_reservation(db: Session, download_id: str) -> Tuple[bool, str]:
        """
        Confirm reservation and convert to actual usage
        
        Returns:
            Tuple[bool, str]: (success, message)
        """
        try:
            # Find reservation
            reservation = db.query(DownloadReservation).filter(
                DownloadReservation.download_id == download_id,
                DownloadReservation.status == 'reserved'
            ).first()
            
            if not reservation:
                return False, f"No reservation found for {download_id}"
            
            # Check if expired
            if reservation.expires_at <= datetime.now(timezone.utc):
                reservation.status = 'expired'
                db.commit()
                return False, f"Reservation expired for {download_id}"
            
            # Get user
            user = db.query(User).filter(User.id == reservation.user_id).first()
            if not user:
                return False, f"User not found for reservation {download_id}"
            
            # Skip credit deduction for creators
            if user.is_creator:
                reservation.status = 'confirmed'
                db.commit()
                return True, "Reservation confirmed (creator unlimited)"

            # 🔒 CRITICAL: Acquire user-level lock to prevent usage counter race conditions
            # This prevents concurrent confirmations from corrupting the usage counters
            usage_lock_key = f"user_usage_{user.id}"

            if not credit_state.acquire_lock(usage_lock_key, timeout=10):
                logger.warning(f"Could not acquire usage lock for user {user.id}, reservation {download_id}")
                return False, "System busy, please retry"

            try:
                # Re-fetch user with fresh tier data under lock
                db.refresh(user)

                # Update user's actual usage
                if not user.patreon_tier_data:
                    user.patreon_tier_data = {}

                download_type = reservation.download_type
                usage_key = f"{download_type}_downloads_used" if download_type != 'book' else 'book_requests_used'

                current_used = user.patreon_tier_data.get(usage_key, 0)
                user.patreon_tier_data[usage_key] = current_used + 1

                # Mark reservation as confirmed
                reservation.status = 'confirmed'

                # Set period_start if not set
                if 'period_start' not in user.patreon_tier_data:
                    user.patreon_tier_data['period_start'] = datetime.now(timezone.utc).isoformat()

                db.commit()

            finally:
                # 🔓 Always release usage lock
                credit_state.release_lock(usage_lock_key)
            
            logger.info(f"Confirmed {download_type} credit for user {user.id}, download {download_id}")
            return True, f"Credit confirmed: {current_used + 1} {download_type} downloads used"
            
        except Exception as e:
            db.rollback()
            logger.error(f"Error confirming reservation: {str(e)}")
            return False, f"Error confirming reservation: {str(e)}"
    
    @staticmethod
    def release_reservation(db: Session, download_id: str, reason: str = "failed") -> Tuple[bool, str]:
        """
        Release reservation without charging credit
        
        Returns:
            Tuple[bool, str]: (success, message)
        """
        try:
            reservation = db.query(DownloadReservation).filter(
                DownloadReservation.download_id == download_id,
                DownloadReservation.status == 'reserved'
            ).first()
            
            if not reservation:
                return True, f"No active reservation found for {download_id}"
            
            reservation.status = reason
            db.commit()
            
            logger.info(f"Released reservation for download {download_id}, reason: {reason}")
            return True, f"Reservation released: {reason}"
            
        except Exception as e:
            db.rollback()
            logger.error(f"Error releasing reservation: {str(e)}")
            return False, f"Error releasing reservation: {str(e)}"
    
    @staticmethod
    def cleanup_expired_reservations(db: Session) -> int:
        """Clean up expired reservations"""
        try:
            result = db.execute(text("""
                UPDATE download_reservations 
                SET status = 'expired' 
                WHERE status = 'reserved' 
                AND expires_at < NOW()
                RETURNING id
            """))
            
            expired_count = len(result.fetchall())
            db.commit()
            
            if expired_count > 0:
                logger.info(f"Cleaned up {expired_count} expired reservations")
                
            return expired_count
            
        except Exception as e:
            db.rollback()
            logger.error(f"Error cleaning up expired reservations: {str(e)}")
            return 0
    
    @staticmethod
    def get_user_reservation_status(db: Session, user_id: int) -> Dict:
        """Get user's current reservation status"""
        try:
            # Get active reservations by type
            reservations = db.execute(text("""
                SELECT download_type, COUNT(*) as count
                FROM download_reservations 
                WHERE user_id = :user_id 
                AND status = 'reserved' 
                AND expires_at > NOW()
                GROUP BY download_type
            """), {"user_id": user_id}).fetchall()
            
            reserved = {row.download_type: row.count for row in reservations}
            
            return {
                'album_reserved': reserved.get('album', 0),
                'track_reserved': reserved.get('track', 0),
                'book_reserved': reserved.get('book', 0)
            }
            
        except Exception as e:
            logger.error(f"Error getting reservation status: {str(e)}")
            return {'album_reserved': 0, 'track_reserved': 0, 'book_reserved': 0}