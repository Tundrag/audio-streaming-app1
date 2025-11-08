#!/usr/bin/env python3
"""
Check PostgreSQL enum type definition
"""
import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://tundrag:Tundrag2010!@localhost/audio_streaming_db"
)

engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    # Check the enum type values
    result = conn.execute(text("""
        SELECT enumlabel
        FROM pg_enum
        WHERE enumtypid = 'auditlogtype'::regtype
        ORDER BY enumsortorder
    """))

    print("PostgreSQL enum 'auditlogtype' values:")
    for row in result:
        print(f"  - '{row[0]}'")

    # Check what's actually stored in the table
    result = conn.execute(text("""
        SELECT DISTINCT action_type::text
        FROM audit_logs
        ORDER BY action_type::text
    """))

    print("\nActual values in audit_logs table:")
    for row in result:
        print(f"  - '{row[0]}'")
