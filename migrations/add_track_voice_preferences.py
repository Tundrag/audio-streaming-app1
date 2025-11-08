#!/usr/bin/env python3
"""
Migration: Add user_track_voice_preferences table
Created: 2025-01-08
Description: Adds track-specific voice preferences table for heart-based voice preference system
"""

import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

def get_database_url():
    """Get database URL from environment"""
    db_user = os.getenv('DB_USER', 'postgres')
    db_password = os.getenv('DB_PASSWORD', '')
    db_host = os.getenv('DB_HOST', 'localhost')
    db_port = os.getenv('DB_PORT', '5432')
    db_name = os.getenv('DB_NAME', 'webaudio')

    return f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"


def upgrade():
    """Create user_track_voice_preferences table"""
    engine = create_engine(get_database_url())

    with engine.connect() as conn:
        # Check if table already exists
        result = conn.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'user_track_voice_preferences'
            );
        """))

        if result.scalar():
            logger.info("✓ Table user_track_voice_preferences already exists, skipping...")
            return

        logger.info("Creating user_track_voice_preferences table...")

        conn.execute(text("""
            CREATE TABLE user_track_voice_preferences (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                track_id VARCHAR NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
                voice_id VARCHAR NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                CONSTRAINT uq_user_track_voice UNIQUE (user_id, track_id)
            );
        """))

        logger.info("Creating indexes...")

        conn.execute(text("""
            CREATE INDEX idx_user_track_pref_user ON user_track_voice_preferences(user_id);
        """))

        conn.execute(text("""
            CREATE INDEX idx_user_track_pref_track ON user_track_voice_preferences(track_id);
        """))

        conn.execute(text("""
            CREATE INDEX idx_user_track_pref_composite ON user_track_voice_preferences(user_id, track_id);
        """))

        conn.commit()

        logger.info("✓ Migration completed successfully!")
        logger.info("  - Created table: user_track_voice_preferences")
        logger.info("  - Created indexes for efficient lookups")


def downgrade():
    """Drop user_track_voice_preferences table"""
    engine = create_engine(get_database_url())

    with engine.connect() as conn:
        logger.info("Dropping user_track_voice_preferences table...")

        conn.execute(text("DROP TABLE IF EXISTS user_track_voice_preferences CASCADE;"))
        conn.commit()

        logger.info("✓ Rollback completed successfully!")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Run migration for track voice preferences')
    parser.add_argument('--downgrade', action='store_true', help='Rollback the migration')
    args = parser.parse_args()

    try:
        if args.downgrade:
            downgrade()
        else:
            upgrade()
    except Exception as e:
        logger.error(f"❌ Migration failed: {e}")
        sys.exit(1)
