#!/bin/bash

DIRS=(
    "/tmp/image_cache"
    "/tmp/media_storage"
    "/tmp/mega_storage"
    "/tmp/mega_upload"
    "/tmp/mega_stream"
    "/tmp/mega_downloads"
    "/tmp/mega_temp"
)

for dir in "${DIRS[@]}"; do
    echo "Setting permissions for $dir"
    sudo rm -rf "$dir"
    sudo mkdir -p "$dir"
    sudo chmod -R 777 "$dir"
    sudo chown -R 1000:1000 "$dir"
    
    # Test write permissions
    if touch "$dir/test_write"; then
        echo "✓ Successfully wrote to $dir"
        rm "$dir/test_write"
    else
        echo "✗ Failed to write to $dir"
    fi
    
    # Show final permissions
    ls -la "$dir"
done
