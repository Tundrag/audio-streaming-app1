import json
from pathlib import Path

def fix_album_covers():
    """Fix album cover paths in existing JSON files"""
    try:
        base_dir = Path(__file__).resolve().parent
        albums_dir = base_dir / "data" / "albums"
        static_covers = base_dir / "static" / "covers"
        
        print("\nScanning for album files...")
        for album_file in albums_dir.glob("albums_*.json"):
            creator_id = album_file.stem.split('_')[1]
            print(f"\nProcessing creator {creator_id}'s albums")
            
            # Load albums
            with open(album_file, "r") as f:
                albums = json.load(f)
            
            modified = False
            for album in albums:
                old_cover_path = album.get("cover_path", "")
                new_filename = f"album_cover_{creator_id}_{album['id']}.jpg"
                new_path = f"/static/covers/{new_filename}"
                
                # Check if the file exists
                cover_file = static_covers / new_filename
                if cover_file.exists():
                    if album["cover_path"] != new_path:
                        print(f"Updating path for album '{album['title']}'")
                        album["cover_path"] = new_path
                        modified = True
                else:
                    print(f"Cover missing for album '{album['title']}'")
                    print(f"Expected at: {cover_file}")
            
            if modified:
                print(f"Saving updated paths to {album_file}")
                with open(album_file, "w") as f:
                    json.dump(albums, f, indent=2)
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    print("Starting cover path fix process...")
    fix_album_covers()
