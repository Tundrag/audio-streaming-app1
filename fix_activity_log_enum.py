#!/usr/bin/env python3
"""
Fix lowercase enum values in audit_logs table
"""
import os
from sqlalchemy import create_engine, text

# Database connection
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://tundrag:Tundrag2010!@localhost/audio_streaming_db"
)

engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    # Update lowercase 'create' to uppercase 'CREATE'
    result = conn.execute(text("""
        UPDATE audit_logs
        SET action_type = 'CREATE'
        WHERE action_type = 'create'
    """))
    conn.commit()
    print(f"Updated {result.rowcount} rows from 'create' to 'CREATE'")

    # Update lowercase 'update' to uppercase 'UPDATE' (if any exist)
    result = conn.execute(text("""
        UPDATE audit_logs
        SET action_type = 'UPDATE'
        WHERE action_type = 'update'
    """))
    conn.commit()
    print(f"Updated {result.rowcount} rows from 'update' to 'UPDATE'")

    # Update lowercase 'delete' to uppercase 'DELETE' (if any exist)
    result = conn.execute(text("""
        UPDATE audit_logs
        SET action_type = 'DELETE'
        WHERE action_type = 'delete'
    """))
    conn.commit()
    print(f"Updated {result.rowcount} rows from 'delete' to 'DELETE'")

print("\nDone! All enum values have been converted to uppercase.")
