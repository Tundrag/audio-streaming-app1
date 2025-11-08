#!/usr/bin/env python3
"""
Script to copy logo to favicon using subprocess
"""
import subprocess
import sys

try:
    # Method 1: Try using cp command
    result = subprocess.run(
        ['cp', '/home/tundragoon/projects/audio-streaming-appT/logo.jpg',
         '/home/tundragoon/projects/audio-streaming-appT/static/images/favicon.ico'],
        capture_output=True,
        text=True
    )

    if result.returncode == 0:
        print("Successfully copied logo.jpg to favicon.ico using cp command")

        # Get file size
        result2 = subprocess.run(
            ['ls', '-lh', '/home/tundragoon/projects/audio-streaming-appT/static/images/favicon.ico'],
            capture_output=True,
            text=True
        )
        print(result2.stdout)
    else:
        print(f"cp command failed: {result.stderr}")
        print("Trying Python method instead...")

        # Method 2: Use Python's shutil
        import shutil
        import os
        from pathlib import Path

        source = Path('/home/tundragoon/projects/audio-streaming-appT/logo.jpg')
        dest = Path('/home/tundragoon/projects/audio-streaming-appT/static/images/favicon.ico')

        shutil.copy2(source, dest)
        file_size = dest.stat().st_size
        print(f"Successfully copied using Python shutil")
        print(f"Destination: {dest}")
        print(f"File size: {file_size} bytes ({file_size/1024:.2f} KB)")

except Exception as e:
    print(f"Error occurred: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
