# pin_management.py
from fastapi import APIRouter, Depends, HTTPException, Request, Form, Body
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import and_
from typing import Optional, Tuple
from database import get_db
from datetime import datetime, timezone
import secrets
import logging
from models import (
    User, 
    UserRole, 
    AuditLog, 
    AuditLogType, 
    Notification, 
    NotificationType, 
    ScheduledTask
)

# Initialize logging and templates
logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")

def validate_pin(pin: str) -> tuple[bool, str]:
    """PIN validation helper."""
    if not pin.isdigit():
        return False, "PIN must contain only numbers"
    if len(pin) != 6:
        return False, "PIN must be exactly 6 digits"
    if len(set(pin)) < 3:
        return False, "PIN must contain at least 3 different digits"
    if pin in ['123456', '654321', '000000', '111111']:
        return False, "PIN is too simple or commonly used"
    return True, "PIN is valid"

def generate_pin() -> str:
    """Generate a secure 6-digit PIN."""
    while True:
        pin = ''.join(secrets.choice('0123456789') for _ in range(6))
        is_valid, _ = validate_pin(pin)
        if is_valid:
            return pin

async def update_creator_pin(
    db: Session, 
    creator_id: int, 
    new_pin: Optional[str] = None
) -> Tuple[bool, str]:
    try:
        creator = db.query(User).filter(User.id == creator_id).first()
        
        if not creator:
            return False, "Creator not found"
            
        if new_pin is None:
            new_pin = generate_pin()
            logger.info(f"Generated new PIN: {new_pin}")

        # Just update the PIN and timestamp - nothing else
        creator.creator_pin = new_pin
        creator.updated_at = datetime.now(timezone.utc)
        
        # Simple commit without notifications
        db.commit()
        return True, new_pin
        
    except Exception as e:
        db.rollback()
        logger.error(f"Exception in update_creator_pin: {str(e)}")
        return False, str(e)


async def get_pin_history(db: Session, creator_id: int, limit: int = 5) -> list:
    """Get PIN history from notifications"""
    try:
        notifications = db.query(Notification).filter(
            and_(
                Notification.user_id == creator_id,
                Notification.type == "system",  # Using string value that matches DB
                Notification.content.like("%PIN%")
            )
        ).order_by(
            Notification.created_at.desc()
        ).limit(limit).all()
        
        history = [
            {
                "date": notification.created_at.isoformat(),
                "description": notification.content,
                "metadata": notification.metadata or {},
                "change_type": notification.metadata.get('change_type', 'manual') if notification.metadata else 'manual'
            }
            for notification in notifications
        ]
        
        return history
        
    except Exception as e:
        logger.error(f"Error getting PIN history: {str(e)}")
        db.rollback()  # Add rollback on error
        return []


async def schedule_pin_rotation(db: Session, creator_id: int) -> Tuple[bool, str]:
    """Schedule automatic PIN rotation"""
    try:
        creator = db.query(User).filter(
            and_(
                User.id == creator_id,
                User.role == UserRole.CREATOR,
                User.is_active.is_(True)
            )
        ).first()
        
        if not creator:
            return False, "Creator not found"
            
        now = datetime.now(timezone.utc)
        if now.month == 12:
            next_month = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            next_month = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
            
        existing_task = db.query(ScheduledTask).filter(
            and_(
                ScheduledTask.user_id == creator_id,
                ScheduledTask.task_type == "pin_rotation",
                ScheduledTask.status == "pending",
                ScheduledTask.scheduled_for > now
            )
        ).first()
        
        if existing_task:
            return False, f"PIN rotation already scheduled for {existing_task.scheduled_for.strftime('%B %d, %Y')}"
            
        scheduled_task = ScheduledTask(
            user_id=creator_id,
            task_type="pin_rotation",
            scheduled_for=next_month,
            status="pending",
            metadata={
                'rotation_type': 'automatic',
                'created_by': 'system',
                'notification_sent': False
            },
            created_at=now
        )
        db.add(scheduled_task)
        
        notification = Notification(
            user_id=creator_id,
            type=NotificationType.SCHEDULED,
            title="PIN Rotation Scheduled",
            content=f"Your creator PIN will automatically rotate on {next_month.strftime('%B 1st, %Y')}.",
            metadata={
                'task_id': str(scheduled_task.id),
                'rotation_date': next_month.isoformat()
            },
            created_at=now,
            is_read=False
        )
        db.add(notification)
        
        db.commit()
        return True, f"PIN rotation scheduled for {next_month.strftime('%B 1st, %Y')}"
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error scheduling PIN rotation: {str(e)}")
        return False, f"Error scheduling PIN rotation: {str(e)}"



class PinManagementRouter:
    def __init__(self, login_required, verify_role_permission, get_user_permissions):
        self.router = APIRouter()  # Remove the prefix to allow different routes
        self.login_required = login_required
        self.verify_role_permission = verify_role_permission
        self.get_user_permissions = get_user_permissions
        self._setup_routes()

    def _setup_routes(self):
        # Add the PIN retrieval endpoint first - this one needs no auth
        @self.router.post("/api/pin/retrieve")
        async def retrieve_pin(
            request: Request,
            data: dict = Body(...),
            db: Session = Depends(get_db)
        ):
            """Direct PIN retrieval endpoint for forgot password"""
            try:
                email = data.get("email", "").strip().lower()
                if not email:
                    return {"status": "error", "error": "Email is required"}
                    
                # Check if this email exists in the database
                user = db.query(User).filter(
                    and_(
                        User.email == email,
                        User.is_active == True
                    )
                ).first()
                
                if not user:
                    return {
                        "status": "error", 
                        "error": "No active account found with this email."
                    }
                
                # Get the creator's PIN
                creator = db.query(User).filter(
                    and_(
                        User.role == UserRole.CREATOR,
                        User.is_active == True
                    )
                ).first()
                
                if not creator or not creator.creator_pin:
                    return {"status": "error", "error": "Creator PIN not available"}
                
                logger.info(f"PIN retrieved successfully for email: {email}")
                # Return the PIN
                return {"status": "success", "pin": creator.creator_pin}
                    
            except Exception as e:
                logger.error(f"Error in PIN retrieval: {str(e)}")
                return {"status": "error", "error": "An internal error occurred."}

        # Continue with creator-specific PIN routes using the /api/creator/pin prefix
        @self.router.post("/api/creator/pin/update")
        async def update_pin(
            current_user: User = Depends(self.login_required),
            db: Session = Depends(get_db)
        ):
            """Update creator PIN manually"""
            try:
                logger.info(f"PIN update request from user ID: {current_user.id}")
                logger.info(f"User details - Role: {current_user.role}")
                
                if current_user.role != UserRole.CREATOR:
                    logger.warning("Non-creator attempted PIN update")
                    raise HTTPException(
                        status_code=403,
                        detail="Only creators can update PINs"
                    )
                    
                success, result = await update_creator_pin(
                    db=db,
                    creator_id=current_user.id
                )
                
                if not success:
                    logger.warning(f"PIN update failed: {result}")
                    raise HTTPException(
                        status_code=400,
                        detail=result
                    )
                    
                return {
                    "status": "success",
                    "message": "PIN updated successfully",
                    "new_pin": result
                }
                
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Unexpected error: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail="Internal server error"
                )


        @self.router.post("/api/creator/pin/schedule-rotation")
        async def schedule_pin_rotation_route(
            current_user: User = Depends(self.login_required),
            db: Session = Depends(get_db)
        ):
            """Schedule automatic PIN rotation"""
            if not current_user.is_creator:
                raise HTTPException(status_code=403, detail="Only creators can schedule PIN rotations")
                
            success, message = await schedule_pin_rotation(db, current_user.id)
            if not success:
                raise HTTPException(status_code=400, detail=message)
                
            return {
                "status": "success",
                "message": message
            }

        @self.router.get("/api/creator/pin/history")
        async def get_pin_history_route(
            current_user: User = Depends(self.login_required),
            db: Session = Depends(get_db)
        ):
            """Get PIN change history"""
            if not current_user.is_creator:
                raise HTTPException(status_code=403, detail="Only creators can view PIN history")
                
            history = await get_pin_history(db, current_user.id)
            return {
                "status": "success",
                "history": history
            }

        @self.router.get("/api/creator/pin/manage")
        @self.verify_role_permission(["creator"])
        async def pin_management_page(
            request: Request,
            current_user: User = Depends(self.login_required),
            db: Session = Depends(get_db)
        ):
            """PIN management page"""
            try:
                history = await get_pin_history(db, current_user.id)
                
                scheduled_rotation = db.query(ScheduledTask).filter(
                    and_(
                        ScheduledTask.user_id == current_user.id,
                        ScheduledTask.task_type == "pin_rotation",
                        ScheduledTask.status == "pending",
                        ScheduledTask.scheduled_for > datetime.now(timezone.utc)
                    )
                ).first()
                
                return templates.TemplateResponse(
                    "creator_management.html",
                    {
                        "request": request,
                        "user": current_user,
                        "permissions": self.get_user_permissions(current_user),
                        "current_pin": current_user.creator_pin,
                        "pin_history": history,
                        "scheduled_rotation": scheduled_rotation.scheduled_for.isoformat() if scheduled_rotation else None
                    }
                )
            except Exception as e:
                logger.error(f"Error loading PIN management page: {str(e)}")
                raise HTTPException(status_code=500, detail="Error loading PIN management page")