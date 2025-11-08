#!/usr/bin/env python3
"""
Check activity logs for TTS generation
"""
import os
from sqlalchemy import create_engine, text
from datetime import datetime, timezone

# Database connection
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://tundrag:Tundrag2010!@localhost/audio_streaming_db"
)

engine = create_engine(DATABASE_URL)

def check_recent_logs(limit=20):
    """Check the most recent activity logs"""
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT
                al.id,
                al.user_id,
                u.username,
                al.action_type,
                al.table_name,
                al.record_id,
                al.description,
                al.created_at
            FROM audit_logs al
            LEFT JOIN users u ON al.user_id = u.id
            ORDER BY al.created_at DESC
            LIMIT :limit
        """), {"limit": limit})

        print(f"\n{'='*100}")
        print(f"Most Recent {limit} Activity Logs")
        print(f"{'='*100}\n")

        rows = result.fetchall()
        if not rows:
            print("No activity logs found.")
            return

        for row in rows:
            print(f"ID: {row[0]}")
            print(f"User: {row[2]} (ID: {row[1]})")
            print(f"Action: {row[3]}")
            print(f"Table: {row[4]}")
            print(f"Record ID: {row[5]}")
            print(f"Description: {row[6]}")
            print(f"Created: {row[7]}")
            print(f"{'-'*100}\n")

def check_tts_logs():
    """Check activity logs specifically for TTS generation"""
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT
                al.id,
                al.user_id,
                u.username,
                al.action_type,
                al.description,
                al.created_at
            FROM audit_logs al
            LEFT JOIN users u ON al.user_id = u.id
            WHERE al.description LIKE '%TTS generation%'
            ORDER BY al.created_at DESC
            LIMIT 50
        """))

        print(f"\n{'='*100}")
        print(f"TTS Generation Activity Logs")
        print(f"{'='*100}\n")

        rows = result.fetchall()
        if not rows:
            print("No TTS generation logs found yet.")
            return

        for row in rows:
            print(f"ID: {row[0]}")
            print(f"User: {row[2]} (ID: {row[1]})")
            print(f"Action: {row[3]}")
            print(f"Description: {row[4]}")
            print(f"Created: {row[5]}")
            print(f"{'-'*100}\n")

if __name__ == "__main__":
    print("\n" + "="*100)
    print("ACTIVITY LOGS CHECKER")
    print("="*100)

    # Check recent logs
    check_recent_logs(20)

    # Check TTS-specific logs
    check_tts_logs()

    print("\n" + "="*100)
    print("END OF REPORT")
    print("="*100 + "\n")
