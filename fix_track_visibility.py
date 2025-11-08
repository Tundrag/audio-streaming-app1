#!/usr/bin/env python3
"""
Fix existing tracks to ensure they all have visibility_status set to 'visible'
"""

import sys
import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

def main():
    # Load environment variables
    load_dotenv()

    # Build database URL
    db_user = os.getenv("DB_USER", "tundrag")
    db_password = os.getenv("DB_PASSWORD", "")
    db_host = os.getenv("DB_HOST", "localhost")
    db_port = os.getenv("DB_PORT", "5432")
    db_name = os.getenv("DB_NAME", "audio_streaming_db")

    db_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"

    print(f"🔗 Connecting to database: {db_name}...")
    engine = create_engine(db_url)

    try:
        with engine.connect() as conn:
            # Check current state
            result = conn.execute(text("SELECT COUNT(*) FROM tracks"))
            total_tracks = result.scalar()

            result = conn.execute(text("SELECT COUNT(*) FROM tracks WHERE visibility_status IS NULL"))
            tracks_with_null_visibility = result.scalar()

            print(f"📊 Total tracks: {total_tracks}")
            print(f"❌ Tracks with NULL visibility_status: {tracks_with_null_visibility}")

            if tracks_with_null_visibility > 0:
                print(f"\n🔧 Updating {tracks_with_null_visibility} tracks to have visibility_status='visible'...")

                # Update tracks with NULL visibility_status
                result = conn.execute(text("""
                    UPDATE tracks
                    SET visibility_status = 'visible'
                    WHERE visibility_status IS NULL
                """))

                conn.commit()
                print(f"✅ Updated {result.rowcount} tracks successfully!")
            else:
                print("✅ All tracks already have visibility_status set!")

            # Verify
            result = conn.execute(text("SELECT COUNT(*) FROM tracks WHERE visibility_status IS NULL"))
            tracks_with_null_after = result.scalar()
            print(f"\n✅ Verification: {tracks_with_null_after} tracks with NULL visibility_status remaining")

    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        return 1

    return 0

if __name__ == "__main__":
    sys.exit(main())
