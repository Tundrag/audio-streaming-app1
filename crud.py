# crud.py
from sqlalchemy.orm import Session
from sqlalchemy import update
from datetime import datetime
from . import models, schemas

def get_user_by_email(db: Session, email: str):
    return db.query(models.User).filter(models.User.email == email).first()

def get_user_by_patreon_id(db: Session, patreon_id: str):
    return db.query(models.User).filter(models.User.patreon_id == patreon_id).first()

def create_user(db: Session, user: schemas.UserCreate):
    db_user = models.User(
        email=user.email,
        patreon_username=user.patreon_username,
        patreon_id=user.patreon_id,
        tier=user.tier
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

def update_user_login(db: Session, user_id: int):
    db.execute(
        update(models.User)
        .where(models.User.id == user_id)
        .values(last_login=datetime.utcnow())
    )
    db.commit()

def update_user_tier(db: Session, user_id: int, new_tier: PatreonTier):
    db.execute(
        update(models.User)
        .where(models.User.id == user_id)
        .values(tier=new_tier)
    )
    db.commit()