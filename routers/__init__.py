# routers/__init__.py

from fastapi import APIRouter
from . import progress

# Create a root router to combine all routers
api_router = APIRouter()

# Include individual routers with their prefixes
api_router.include_router(progress.router, prefix="/api", tags=["progress"])

# Export what we want available when importing from routers
__all__ = ["api_router", "progress"]