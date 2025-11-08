The favicon.ico file needs to be copied from the project root.

Please run this command to copy the logo as the favicon:

cp /home/tundragoon/projects/audio-streaming-appT/logo.jpg /home/tundragoon/projects/audio-streaming-appT/static/images/favicon.ico

Or run the Python script:
python3 /home/tundragoon/projects/audio-streaming-appT/copy_logo_to_favicon.py

The Python script has already been created and will:
1. Create the static/images directory (if needed)
2. Copy logo.jpg to static/images/favicon.ico
3. Report the file size
