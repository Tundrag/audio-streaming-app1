# schemas.py
from pydantic import BaseModel, EmailStr
from datetime import datetime
from typing import Optional
from models import PatreonTier

class UserBase(BaseModel):
    email: EmailStr
    patreon_username: str
    tier: PatreonTier

class UserCreate(UserBase):
    patreon_id: str

class UserInDB(UserBase):
    id: int
    patreon_id: str
    is_active: bool
    last_login: Optional[datetime]
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True