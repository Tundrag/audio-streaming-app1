#!/usr/bin/env python3
"""Check visibility_status values in database"""
import sys
import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

db_user = os.getenv("DB_USER", "tundrag")
db_password = os.getenv("DB_PASSWORD", "")
db_host = os.getenv("DB_HOST", "localhost")
db_port = os.getenv("DB_PORT", "5432")
db_name = os.getenv("DB_NAME", "audio_streaming_db")

db_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
engine = create_engine(db_url)

with engine.connect() as conn:
    result = conn.execute(text("""
        SELECT id, title, visibility_status
        FROM tracks
        ORDER BY created_at DESC
        LIMIT 10
    """))

    print("📊 Track visibility_status values in database:")
    print("-" * 80)
    for row in result:
        print(f"ID: {row.id[:20]}... | Title: {row.title:30} | visibility_status: {row.visibility_status}")
