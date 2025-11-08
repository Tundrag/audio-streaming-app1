#!/usr/bin/env python3
"""
Simple script to copy logo.jpg to static/images/favicon.ico
"""
import os
import shutil
from pathlib import Path

# Define paths
project_root = Path('/home/tundragoon/projects/audio-streaming-appT')
source_file = project_root / 'logo.jpg'
dest_dir = project_root / 'static' / 'images'
dest_file = dest_dir / 'favicon.ico'

try:
    # Create destination directory if it doesn't exist
    dest_dir.mkdir(parents=True, exist_ok=True)
    print(f"Created directory: {dest_dir}")

    # Copy the file
    shutil.copy2(str(source_file), str(dest_file))
    print(f"Copied {source_file} to {dest_file}")

    # Get file size
    file_size = dest_file.stat().st_size
    file_size_kb = file_size / 1024

    print(f"\nSuccess!")
    print(f"Destination: {dest_file}")
    print(f"File size: {file_size} bytes ({file_size_kb:.2f} KB)")

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
