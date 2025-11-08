#cache_busting.py
import os
from pathlib import Path
import time

# Configuration
STATIC_DIR = Path(__file__).resolve().parent / "static"

# Global app version - updates on server restart
# This ensures all SPA modules use the same version
APP_VERSION = str(int(time.time()))

def cache_busted_url_for(name: str, **path_params) -> str:
    """
    Generate URLs with cache busting query parameters using APP_VERSION

    ALL frontend assets (JS, CSS, images, fonts, etc.) use APP_VERSION
    (server start time) to ensure complete cache consistency across the
    entire application. This is critical for:
    - SPA modules that need consistent versioning
    - CSS files that must match updated JavaScript behavior
    - Preventing partial updates that can break the UI

    Cache invalidation happens on server restart, ensuring all assets
    are refreshed together as a cohesive unit.
    """
    if name == "static" and "path" in path_params:
        path = path_params["path"]
        base_url = f"/static{path}" if not path.startswith('/') else f"/static{path}"

        # ALL static assets use APP_VERSION for complete consistency
        return f"{base_url}?v={APP_VERSION}"

    return f"/{name}" if not name.startswith('/') else name