# auth/dependencies.py
from fastapi import Depends, Request, HTTPException
from typing import Optional
from models import User
from patreon_client import patreon_client
async def get_current_user(request: Request) -> Optional[dict]:
    """Get current user from session"""
    session_data = await session_manager.verify_session(request)
    return session_data

async def login_required(request: Request) -> dict:
    """Require authenticated user"""
    session_data = await get_current_user(request)
    if not session_data:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return session_data