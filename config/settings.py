"""
Environment-Specific Settings

Settings that change between development, staging, and production.
Uses environment variables with sensible defaults.

Usage:
    from config import settings

    print(settings.DATABASE_URL)
    print(settings.DEBUG)
"""

import os
from typing import Optional


class Settings:
    """
    Application settings loaded from environment variables.

    These values change between environments (dev/staging/prod).
    For constants that never change, use config/constants.py instead.
    """

    # ========================================================================
    # ENVIRONMENT
    # ========================================================================

    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    DEBUG: bool = os.getenv("DEBUG", "False").lower() == "true"


    # ========================================================================
    # DATABASE
    # ========================================================================

    DB_USER: str = os.getenv("DB_USER", "postgres")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")
    DB_HOST: str = os.getenv("DB_HOST", "localhost")
    DB_PORT: str = os.getenv("DB_PORT", "5432")
    DB_NAME: str = os.getenv("DB_NAME", "audio_streaming")

    @property
    def DATABASE_URL(self) -> str:
        """Construct database URL from components"""
        return (
            f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )


    # ========================================================================
    # REDIS
    # ========================================================================

    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_PASSWORD: Optional[str] = os.getenv("REDIS_PASSWORD")
    REDIS_DB: int = int(os.getenv("REDIS_DB", "0"))


    # ========================================================================
    # SECURITY
    # ========================================================================

    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-this-in-production")
    GRANT_TOKEN_SECRET: str = os.getenv("GRANT_TOKEN_SECRET", "your-secret-key-change-in-production")


    # ========================================================================
    # PATREON
    # ========================================================================

    PATREON_CLIENT_ID: Optional[str] = os.getenv("PATREON_CLIENT_ID")
    PATREON_CLIENT_SECRET: Optional[str] = os.getenv("PATREON_CLIENT_SECRET")
    PATREON_ACCESS_TOKEN: Optional[str] = os.getenv("PATREON_ACCESS_TOKEN")
    PATREON_WEBHOOK_SECRET: Optional[str] = os.getenv("PATREON_WEBHOOK_SECRET")

    ENABLE_PATREON_SYNC: bool = os.getenv("ENABLE_PATREON_SYNC", "False").lower() == "true"


    # ========================================================================
    # KO-FI
    # ========================================================================

    KOFI_VERIFICATION_TOKEN: Optional[str] = os.getenv("KOFI_VERIFICATION_TOKEN")

    ENABLE_KOFI_SYNC: bool = os.getenv("ENABLE_KOFI_SYNC", "False").lower() == "true"


    # ========================================================================
    # CLOUD STORAGE (MEGA/S4)
    # ========================================================================

    MEGA_EMAIL: Optional[str] = os.getenv("MEGA_EMAIL")
    MEGA_PASSWORD: Optional[str] = os.getenv("MEGA_PASSWORD")

    S4_ACCESS_KEY: Optional[str] = os.getenv("S4_ACCESS_KEY")
    S4_SECRET_KEY: Optional[str] = os.getenv("S4_SECRET_KEY")
    S4_BUCKET: Optional[str] = os.getenv("S4_BUCKET")


    # ========================================================================
    # EMAIL (for notifications)
    # ========================================================================

    SMTP_HOST: Optional[str] = os.getenv("SMTP_HOST")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: Optional[str] = os.getenv("SMTP_USER")
    SMTP_PASSWORD: Optional[str] = os.getenv("SMTP_PASSWORD")
    SMTP_FROM_EMAIL: Optional[str] = os.getenv("SMTP_FROM_EMAIL")


    # ========================================================================
    # DISCORD WEBHOOKS
    # ========================================================================

    DISCORD_WEBHOOK_URL: Optional[str] = os.getenv("DISCORD_WEBHOOK_URL")


    # ========================================================================
    # SERVER
    # ========================================================================

    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))


    # ========================================================================
    # CORS
    # ========================================================================

    ALLOWED_ORIGINS: list = os.getenv(
        "ALLOWED_ORIGINS",
        "*"
    ).split(",")


    # ========================================================================
    # HELPER METHODS
    # ========================================================================

    @property
    def is_production(self) -> bool:
        """Check if running in production"""
        return self.ENVIRONMENT.lower() == "production"

    @property
    def is_development(self) -> bool:
        """Check if running in development"""
        return self.ENVIRONMENT.lower() == "development"

    def validate(self):
        """Validate required settings are present"""
        if self.is_production:
            assert self.SECRET_KEY != "change-this-in-production", \
                "SECRET_KEY must be changed in production!"
            assert self.GRANT_TOKEN_SECRET != "your-secret-key-change-in-production", \
                "GRANT_TOKEN_SECRET must be changed in production!"


# Create singleton instance
settings = Settings()

# Validate on import
if settings.ENVIRONMENT != "test":
    settings.validate()


__all__ = ['settings', 'Settings']
