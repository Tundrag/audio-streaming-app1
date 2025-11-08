import json
from pathlib import Path
from database import SessionLocal
from app import User, PatreonTier
import argparse
import shutil

def get_creators():
    """Get list of all creators from database"""
    db = SessionLocal()
    try:
        creators = db.query(User).filter(User.is_creator == True).all()
        return [(c.id, c.username, c.email) for c in creators]
    finally:
        db.close()

def list_creators():
    """Print list of available creators"""
    creators = get_creators()
    if not creators:
        print("No creators found in database")
        return
    
    print("\nAvailable creators:")
    print("-" * 50)
    print(f"{'ID':<5} {'Username':<20} {'Email':<30}")
    print("-" * 50)
    for id, username, email in creators:
        print(f"{id:<5} {username:<20} {email:<30}")

def migrate_albums(creator_id: int):
    """Migrate existing albums.json to specified creator"""
    db = None
    try:
        # Setup paths
        base_dir = Path(__file__).resolve().parent
        old_albums_file = base_dir / "albums.json.bak"  # Try backup first
        if not old_albums_file.exists():
            old_albums_file = base_dir / "albums.json"  # Try original file
        new_albums_dir = base_dir / "data" / "albums"
        static_covers = base_dir / "static" / "covers"
        static_audio = base_dir / "static" / "audio"
        
        # Create directories if they don't exist
        new_albums_dir.mkdir(parents=True, exist_ok=True)
        static_covers.mkdir(parents=True, exist_ok=True)
        static_audio.mkdir(parents=True, exist_ok=True)
        
        if not old_albums_file.exists():
            print(f"No albums file found at: {old_albums_file}")
            print("Please ensure albums.json or albums.json.bak exists")
            return
        
        print(f"Using albums file: {old_albums_file}")
        
        # Read existing albums
        with open(old_albums_file, "r") as f:
            old_albums = json.load(f)
            
        print(f"Found {len(old_albums)} albums to migrate")
        
        # Get the specified creator from the database
        db = SessionLocal()
        creator = db.query(User).filter(
            User.id == creator_id,
            User.is_creator == True
        ).first()
        
        if not creator:
            print(f"No creator found with ID {creator_id}")
            return
            
        print(f"Migrating albums to creator: {creator.username} (ID: {creator.id})")
        
        # Create new albums file for creator
        new_albums_file = new_albums_dir / f"albums_{creator.id}.json"
        
        # Check if creator already has albums
        if new_albums_file.exists():
            with open(new_albums_file, "r") as f:
                existing_albums = json.load(f)
            response = input(f"Creator already has {len(existing_albums)} albums. Merge with existing? (y/n): ")
            if response.lower() != 'y':
                print("Migration cancelled")
                return
            max_album_id = max([album["id"] for album in existing_albums], default=0)
        else:
            existing_albums = []
            max_album_id = 0
        
        # Process each album
        migrated_albums = []
        for index, album in enumerate(old_albums, 1):
            try:
                # Update album ID to avoid conflicts
                old_album_id = album["id"]
                album["id"] = max_album_id + index
                album["creator_id"] = creator.id
                
                # Update cover path and copy file
                if "cover_path" in album:
                    new_filename = f"album_cover_{creator.id}_{album['id']}.jpg"
                    album["cover_path"] = f"/static/covers/{new_filename}"
                    
                    # Copy cover file from any existing location
                    found_cover = False
                    possible_covers = [
                        static_covers / new_filename,  # New location
                        static_covers / f"album_cover_{old_album_id}.jpg",  # Old location
                        base_dir / "static" / "covers" / f"album_cover_{old_album_id}.jpg"  # Original location
                    ]
                    
                    for old_cover in possible_covers:
                        if old_cover.exists():
                            new_cover = static_covers / new_filename
                            shutil.copy2(old_cover, new_cover)
                            print(f"Copied cover: {old_cover.name} -> {new_filename}")
                            found_cover = True
                            break
                    
                    if not found_cover:
                        print(f"Warning: Could not find cover for album '{album['title']}'")
                
                # Update track paths and copy files
                for track in album.get("tracks", []):
                    if "file_path" in track:
                        new_filename = f"track_{creator.id}_{album['id']}_{track['id']}.mp3"
                        track["file_path"] = f"/static/audio/{new_filename}"
                        track["creator_id"] = creator.id
                        
                        # Copy audio file from any existing location
                        found_track = False
                        possible_tracks = [
                            static_audio / new_filename,  # New location
                            static_audio / f"track_{old_album_id}_{track['id']}.mp3",  # Old location
                            base_dir / "static" / "audio" / f"track_{old_album_id}_{track['id']}.mp3"  # Original location
                        ]
                        
                        for old_track in possible_tracks:
                            if old_track.exists():
                                new_track = static_audio / new_filename
                                shutil.copy2(old_track, new_track)
                                print(f"Copied track: {old_track.name} -> {new_filename}")
                                found_track = True
                                break
                        
                        if not found_track:
                            print(f"Warning: Could not find audio for track '{track['title']}'")
                
                migrated_albums.append(album)
                print(f"Migrated album {index}/{len(old_albums)}: {album['title']}")
                
            except Exception as e:
                print(f"Error migrating album {album.get('title', 'Unknown')}: {str(e)}")
                continue
        
        # Merge with existing albums if any
        all_albums = existing_albums + migrated_albums
        
        # Save new albums file
        with open(new_albums_file, "w") as f:
            json.dump(all_albums, f, indent=2)
        
        print(f"""
Migration completed successfully:
- Migrated {len(migrated_albums)} albums
- Total albums for creator: {len(all_albums)}
- Albums file: {new_albums_file}
- Covers directory: {static_covers}
- Audio directory: {static_audio}
        """)
            
    except Exception as e:
        print(f"Error during migration: {str(e)}")
    finally:
        if db:
            db.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Migrate albums.json to a specific creator')
    parser.add_argument('--list', action='store_true', help='List available creators')
    parser.add_argument('--creator-id', type=int, help='ID of the creator to migrate albums to')
    
    args = parser.parse_args()
    
    if args.list:
        list_creators()
    elif args.creator_id:
        migrate_albums(args.creator_id)
    else:
        print("Please specify either --list to see available creators or --creator-id to migrate albums")
        print("\nExample usage:")
        print("  List creators:        python migrate_albums.py --list")
        print("  Migrate to creator:   python migrate_albums.py --creator-id 1")