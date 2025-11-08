#!/usr/bin/env python3
"""
Complete MEGA to S4 Migration Tool
Migrates existing files from MEGA to S4 object storage
"""

import asyncio
import logging
import os
import subprocess
import tempfile
import argparse
import sys
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import shutil

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('migration.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class MegaToS4Migrator:
    """Complete MEGA to S4 migration tool"""
    
    def __init__(self):
        self.setup_database()
        self.setup_paths()
        self.setup_temp_dir()
        self.stats = {
            'total_audio_files': 0,
            'total_image_files': 0,
            'audio_migrated': 0,
            'images_migrated': 0,
            'failed_migrations': 0,
            'skipped': 0
        }
        
    def setup_database(self):
        """Setup database connection with multiple fallback options"""
        database_url = self._get_database_url()
        
        if not database_url:
            self._show_database_help()
            raise ValueError("Could not determine database configuration")
        
        try:
            self.engine = create_engine(database_url)
            SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
            self.db_session = SessionLocal()
            
            # Test connection
            self.db_session.execute(text("SELECT 1"))
            logger.info("✅ Database connection successful")
            
        except Exception as e:
            logger.error(f"❌ Database connection failed: {e}")
            self._show_database_help()
            raise
            
    def _get_database_url(self) -> Optional[str]:
        """Try multiple ways to get database URL"""
        # Try direct URL first
        database_url = (
            os.getenv('DATABASE_URL') or 
            os.getenv('DB_URL') or 
            os.getenv('POSTGRES_URL') or 
            os.getenv('POSTGRESQL_URL')
        )
        
        if database_url:
            return database_url
            
        # Try to construct from individual components
        db_host = os.getenv('DB_HOST', 'localhost')
        db_port = os.getenv('DB_PORT', '5432')
        db_name = os.getenv('DB_NAME', 'audio_streaming_db')
        db_user = os.getenv('DB_USER', os.getenv('POSTGRES_USER', 'postgres'))
        db_password = os.getenv('DB_PASSWORD', os.getenv('POSTGRES_PASSWORD', ''))
        
        if db_password:
            return f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
        else:
            return f"postgresql://{db_user}@{db_host}:{db_port}/{db_name}"
            
    def _show_database_help(self):
        """Show database configuration help"""
        print("\n" + "="*60)
        print("❌ DATABASE CONFIGURATION ERROR")
        print("="*60)
        print("Please set your database connection using one of these methods:")
        print()
        print("Method 1 - Complete URL:")
        print("  DATABASE_URL=postgresql://user:password@host:port/database")
        print()
        print("Method 2 - Individual components:")
        print("  DB_HOST=localhost")
        print("  DB_PORT=5432") 
        print("  DB_NAME=audio_streaming_db")
        print("  DB_USER=your_username")
        print("  DB_PASSWORD=your_password")
        print()
        print("Add these to your .env file or export as environment variables")
        print("="*60)
    
    def setup_paths(self):
        """Setup MEGA and local paths"""
        # MEGA paths (where files currently are)
        self.mega_base_path = "/audio-streaming-app/media"
        self.mega_audio_path = f"{self.mega_base_path}/audio"
        self.mega_images_path = f"{self.mega_base_path}/images"
        
        # URL patterns in database
        self.audio_url_pattern = "/media/audio%"
        self.image_url_pattern = "/media/images%"
        
    def setup_temp_dir(self):
        """Setup temporary directory for file transfers"""
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mega_s4_migration_"))
        logger.info(f"Using temp directory: {self.temp_dir}")
        
    async def start(self):
        """Initialize S4 client"""
        try:
            # Import models and S4 client
            from models import Track, Album
            self.Track = Track
            self.Album = Album
            
            from mega_s4_client import mega_s4_client
            self.s4_client = mega_s4_client
            
            await self.s4_client.start()
            logger.info("✅ S4 client initialized")
            
        except ImportError as e:
            logger.error(f"❌ Import error: {e}")
            logger.error("Make sure models.py and mega_s4_client.py are in the same directory")
            raise
        except Exception as e:
            logger.error(f"❌ S4 client initialization failed: {e}")
            raise
            
    async def close(self):
        """Cleanup resources"""
        try:
            await self.s4_client.close()
            self.db_session.close()
            
            if self.temp_dir.exists():
                shutil.rmtree(self.temp_dir)
                
            logger.info("✅ Cleanup complete")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

    # ============================================================================
    # PRE-CHECK FUNCTIONALITY
    # ============================================================================
    
    def check_mega_auth(self) -> bool:
        """Check if MEGA CLI is authenticated"""
        try:
            result = subprocess.run(['mega-whoami'], 
                                  capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0:
                email = result.stdout.strip()
                logger.info(f"✅ MEGA authenticated as: {email}")
                return True
            else:
                logger.error("❌ MEGA not authenticated")
                logger.error("Run 'mega-login <email>' to authenticate")
                return False
                
        except FileNotFoundError:
            logger.error("❌ MEGA CLI not found")
            logger.error("Install MEGA CLI tools first")
            return False
        except subprocess.TimeoutExpired:
            logger.error("❌ MEGA CLI timeout")
            return False
        except Exception as e:
            logger.error(f"❌ MEGA CLI error: {e}")
            return False

    def get_database_stats(self) -> Dict:
        """Get statistics from database"""
        stats = {}
        
        try:
            # Count audio tracks 
            audio_count = self.db_session.query(self.Track).filter(
                self.Track.file_path.like(self.audio_url_pattern)
            ).count()
            stats['audio_tracks'] = audio_count
            
            # Count cover images from albums
            image_count = self.db_session.execute(
                text("""
                    SELECT COUNT(DISTINCT cover_path) 
                    FROM albums 
                    WHERE cover_path LIKE :pattern
                """),
                {"pattern": self.image_url_pattern}
            ).scalar()
            stats['cover_images'] = image_count or 0
            
            # Get audio file extensions
            extensions = self.db_session.execute(
                text("""
                    SELECT 
                        LOWER(RIGHT(file_path, 4)) as extension,
                        COUNT(*) as count
                    FROM tracks 
                    WHERE file_path LIKE :pattern
                    GROUP BY LOWER(RIGHT(file_path, 4))
                    ORDER BY count DESC
                """),
                {"pattern": self.audio_url_pattern}
            ).fetchall()
            stats['audio_extensions'] = dict(extensions)
            
            # Total counts
            stats['total_tracks'] = self.db_session.query(self.Track).count()
            stats['total_albums'] = self.db_session.query(self.Album).count()
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting database stats: {e}")
            return {'audio_tracks': 0, 'cover_images': 0, 'audio_extensions': {}, 
                   'total_tracks': 0, 'total_albums': 0}

    def check_mega_folder(self, mega_path: str) -> Dict:
        """Check what files exist in MEGA folder"""
        try:
            result = subprocess.run(['mega-ls', '-l', mega_path], 
                                  capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                logger.warning(f"Could not access MEGA path: {mega_path}")
                return {'count': 0, 'total_size': 0, 'files': []}
            
            files = []
            total_size = 0
            
            # Parse mega-ls output
            for line in result.stdout.strip().split('\n'):
                if line and not line.startswith('d') and len(line.split()) >= 9:
                    parts = line.split()
                    try:
                        size = int(parts[4])
                        filename = ' '.join(parts[8:])
                        files.append({'name': filename, 'size': size})
                        total_size += size
                    except (ValueError, IndexError):
                        continue
            
            return {'count': len(files), 'total_size': total_size, 'files': files}
            
        except subprocess.TimeoutExpired:
            logger.error(f"Timeout checking MEGA folder: {mega_path}")
            return {'count': 0, 'total_size': 0, 'files': []}
        except Exception as e:
            logger.error(f"Error checking MEGA folder: {e}")
            return {'count': 0, 'total_size': 0, 'files': []}

    def format_size(self, size_bytes: int) -> str:
        """Format file size in human readable format"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} PB"

    async def run_precheck(self):
        """Run pre-migration analysis"""
        print("\n" + "="*60)
        print("🔍 PRE-MIGRATION ANALYSIS")
        print("="*60)
        
        # Check MEGA authentication
        if not self.check_mega_auth():
            return False
        
        # Get database statistics
        print("📊 Analyzing database...")
        db_stats = self.get_database_stats()
        
        # Check MEGA folders
        print("📁 Checking MEGA audio folder...")
        mega_audio = self.check_mega_folder(self.mega_audio_path)
        
        print("🖼️  Checking MEGA images folder...")
        mega_images = self.check_mega_folder(self.mega_images_path)
        
        # Print analysis report
        self._print_precheck_report(db_stats, mega_audio, mega_images)
        
        return True

    def _print_precheck_report(self, db_stats: Dict, mega_audio: Dict, mega_images: Dict):
        """Print pre-migration analysis report"""
        print(f"\n📅 Analysis completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print()
        
        # Database summary
        print("📊 DATABASE SUMMARY:")
        print(f"  Total tracks in database: {db_stats['total_tracks']}")
        print(f"  Total albums in database: {db_stats['total_albums']}")
        print(f"  Audio tracks to migrate: {db_stats['audio_tracks']}")
        print(f"  Cover images to migrate: {db_stats['cover_images']}")
        print()
        
        # File types
        if db_stats['audio_extensions']:
            print("🎵 AUDIO FILE TYPES:")
            for ext, count in db_stats['audio_extensions'].items():
                print(f"  {ext}: {count} files")
            print()
        
        # MEGA storage summary
        print("☁️  MEGA STORAGE:")
        print(f"  Audio files: {mega_audio['count']} ({self.format_size(mega_audio['total_size'])})")
        print(f"  Image files: {mega_images['count']} ({self.format_size(mega_images['total_size'])})")
        
        # Show largest files
        if mega_audio['count'] > 0:
            largest = sorted(mega_audio['files'], key=lambda x: x['size'], reverse=True)[:3]
            print("  Largest audio files:")
            for file in largest:
                print(f"    📄 {file['name']}: {self.format_size(file['size'])}")
        print()
        
        # Migration summary
        total_files = mega_audio['count'] + mega_images['count']
        total_size = mega_audio['total_size'] + mega_images['total_size']
        
        print("📋 MIGRATION SUMMARY:")
        print(f"  Total files to migrate: {total_files}")
        print(f"  Total data to transfer: {self.format_size(total_size)}")
        
        if total_size > 0:
            # Estimate time (assuming 10 MB/s transfer rate)
            estimated_seconds = total_size / (10 * 1024 * 1024)
            if estimated_seconds < 3600:
                print(f"  Estimated time: {estimated_seconds/60:.1f} minutes")
            else:
                print(f"  Estimated time: {estimated_seconds/3600:.1f} hours")
        print()
        
        # Recommendations
        print("💡 RECOMMENDATIONS:")
        if total_files == 0:
            print("  ✅ No files need migration")
        elif total_files < 50:
            print("  ✅ Small migration - should complete quickly")
        elif total_files < 500:
            print("  ⚠️  Medium migration - monitor progress")
        else:
            print("  ⚠️  Large migration - consider running during off-peak hours")
        
        if total_size > 10 * 1024 * 1024 * 1024:  # 10 GB
            print("  ⚠️  Large data transfer - ensure stable internet connection")

    # ============================================================================
    # MIGRATION FUNCTIONALITY  
    # ============================================================================
    
    def get_files_to_migrate(self) -> Tuple[List, List]:
        """Get all files that need migration"""
        try:
            # Get audio files (tracks)
            audio_tracks = self.db_session.query(self.Track).filter(
                self.Track.file_path.like(self.audio_url_pattern)
            ).all()
            
            # Get image files (album covers)
            cover_images = self.db_session.execute(
                text("""
                    SELECT DISTINCT cover_path 
                    FROM albums 
                    WHERE cover_path LIKE :pattern
                """),
                {"pattern": self.image_url_pattern}
            ).fetchall()
            
            image_paths = [row[0] for row in cover_images if row[0]]
            
            logger.info(f"Found {len(audio_tracks)} audio files and {len(image_paths)} images to migrate")
            
            self.stats['total_audio_files'] = len(audio_tracks)
            self.stats['total_image_files'] = len(image_paths)
            
            return audio_tracks, image_paths
            
        except Exception as e:
            logger.error(f"Error getting files to migrate: {e}")
            return [], []

    async def download_from_mega(self, mega_path: str, local_path: Path) -> bool:
        """Download file from MEGA"""
        try:
            logger.info(f"⬇️  Downloading: {mega_path}")
            
            # Ensure parent directory exists
            local_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Download using mega-get
            result = subprocess.run([
                'mega-get', mega_path, str(local_path)
            ], capture_output=True, text=True, timeout=300)
            
            if result.returncode == 0 and local_path.exists() and local_path.stat().st_size > 0:
                size = local_path.stat().st_size
                logger.info(f"✅ Downloaded: {local_path.name} ({self.format_size(size)})")
                return True
            else:
                logger.error(f"❌ Download failed: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            logger.error(f"❌ Download timeout: {mega_path}")
            return False
        except Exception as e:
            logger.error(f"❌ Download error: {e}")
            return False

    async def upload_to_s4(self, local_path: Path, filename: str, is_image: bool = False) -> bool:
        """Upload file to S4"""
        try:
            logger.info(f"⬆️  Uploading to S4: {filename}")
            
            # Determine content type and prefix
            if is_image:
                prefix = "images"
                if filename.lower().endswith(('.jpg', '.jpeg')):
                    content_type = "image/jpeg"
                elif filename.lower().endswith('.png'):
                    content_type = "image/png"
                elif filename.lower().endswith('.gif'):
                    content_type = "image/gif"
                else:
                    content_type = "image/jpeg"
            else:
                prefix = "audio"
                if filename.lower().endswith('.mp3'):
                    content_type = "audio/mpeg"
                elif filename.lower().endswith('.m4a'):
                    content_type = "audio/mp4"
                elif filename.lower().endswith('.wav'):
                    content_type = "audio/wav"
                else:
                    content_type = "audio/mpeg"
            
            # Generate S4 object key
            object_key = self.s4_client.generate_object_key(filename, prefix=prefix)
            
            # Upload to S4
            success = await self.s4_client.upload_file(
                local_path=local_path,
                object_key=object_key,
                content_type=content_type
            )
            
            if success:
                logger.info(f"✅ S4 upload successful: {object_key}")
                return True
            else:
                logger.error(f"❌ S4 upload failed: {filename}")
                return False
                
        except Exception as e:
            logger.error(f"❌ S4 upload error: {e}")
            return False

    async def delete_from_mega(self, mega_path: str) -> bool:
        """Delete file from MEGA"""
        try:
            result = subprocess.run([
                'mega-rm', mega_path
            ], capture_output=True, text=True, timeout=60)
            
            if result.returncode == 0:
                logger.info(f"🗑️  Deleted from MEGA: {mega_path}")
                return True
            else:
                logger.warning(f"⚠️  MEGA delete warning: {result.stderr}")
                return True  # Don't fail migration if delete fails
                
        except Exception as e:
            logger.warning(f"⚠️  MEGA delete error: {e}")
            return True  # Don't fail migration if delete fails

    async def migrate_audio_file(self, track) -> bool:
        """Migrate a single audio file"""
        try:
            filename = Path(track.file_path).name
            mega_path = f"{self.mega_audio_path}/{filename}"
            local_path = self.temp_dir / f"audio_{filename}"
            
            logger.info(f"🎵 Migrating audio: {filename} (Track ID: {track.id})")
            
            # Download from MEGA
            if not await self.download_from_mega(mega_path, local_path):
                return False
            
            # Upload to S4
            if not await self.upload_to_s4(local_path, filename, is_image=False):
                return False
            
            # Delete from MEGA
            await self.delete_from_mega(mega_path)
            
            # Cleanup local file
            if local_path.exists():
                local_path.unlink()
            
            self.stats['audio_migrated'] += 1
            logger.info(f"✅ Audio migration complete: {filename}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Audio migration failed: {e}")
            return False

    async def migrate_image_file(self, image_path: str) -> bool:
        """Migrate a single image file"""
        try:
            filename = Path(image_path).name
            mega_path = f"{self.mega_images_path}/{filename}"
            local_path = self.temp_dir / f"image_{filename}"
            
            logger.info(f"🖼️  Migrating image: {filename}")
            
            # Download from MEGA
            if not await self.download_from_mega(mega_path, local_path):
                return False
            
            # Upload to S4
            if not await self.upload_to_s4(local_path, filename, is_image=True):
                return False
            
            # Delete from MEGA
            await self.delete_from_mega(mega_path)
            
            # Cleanup local file
            if local_path.exists():
                local_path.unlink()
            
            self.stats['images_migrated'] += 1
            logger.info(f"✅ Image migration complete: {filename}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Image migration failed: {e}")
            return False

    async def run_migration(self, batch_size: int = 3):
        """Run the complete migration"""
        print("\n" + "="*60)
        print("🚀 STARTING MIGRATION")
        print("="*60)
        
        # Get files to migrate
        audio_tracks, image_paths = self.get_files_to_migrate()
        
        if not audio_tracks and not image_paths:
            print("✅ No files found to migrate!")
            return True
        
        print(f"📊 Found {len(audio_tracks)} audio files and {len(image_paths)} images to migrate")
        print(f"📦 Processing in batches of {batch_size}")
        print()
        
        # Migrate audio files
        if audio_tracks:
            print(f"🎵 Migrating {len(audio_tracks)} audio files...")
            for i in range(0, len(audio_tracks), batch_size):
                batch = audio_tracks[i:i + batch_size]
                batch_num = i // batch_size + 1
                total_batches = (len(audio_tracks) + batch_size - 1) // batch_size
                
                print(f"📦 Processing audio batch {batch_num}/{total_batches}")
                
                # Process batch concurrently
                tasks = [self.migrate_audio_file(track) for track in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                # Count failures
                for result in results:
                    if isinstance(result, Exception) or result is False:
                        self.stats['failed_migrations'] += 1
                
                # Small delay between batches
                await asyncio.sleep(2)
        
        # Migrate image files
        if image_paths:
            print(f"🖼️  Migrating {len(image_paths)} image files...")
            for i in range(0, len(image_paths), batch_size):
                batch = image_paths[i:i + batch_size]
                batch_num = i // batch_size + 1
                total_batches = (len(image_paths) + batch_size - 1) // batch_size
                
                print(f"📦 Processing image batch {batch_num}/{total_batches}")
                
                # Process batch concurrently
                tasks = [self.migrate_image_file(path) for path in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                # Count failures
                for result in results:
                    if isinstance(result, Exception) or result is False:
                        self.stats['failed_migrations'] += 1
                
                # Small delay between batches
                await asyncio.sleep(2)
        
        # Print final summary
        self._print_migration_summary()
        
        return self.stats['failed_migrations'] == 0

    def _print_migration_summary(self):
        """Print migration summary"""
        print("\n" + "="*60)
        print("📊 MIGRATION SUMMARY")
        print("="*60)
        print(f"🎵 Audio files migrated: {self.stats['audio_migrated']}/{self.stats['total_audio_files']}")
        print(f"🖼️  Image files migrated: {self.stats['images_migrated']}/{self.stats['total_image_files']}")
        print(f"❌ Failed migrations: {self.stats['failed_migrations']}")
        print(f"⏭️  Skipped files: {self.stats['skipped']}")
        
        total_migrated = self.stats['audio_migrated'] + self.stats['images_migrated']
        total_files = self.stats['total_audio_files'] + self.stats['total_image_files']
        
        if self.stats['failed_migrations'] == 0:
            print(f"✅ Migration completed successfully! ({total_migrated}/{total_files} files)")
        else:
            print(f"⚠️  Migration completed with {self.stats['failed_migrations']} failures")
        
        print("="*60)

    # ============================================================================
    # VERIFICATION FUNCTIONALITY
    # ============================================================================
    
    async def check_file_in_s4(self, filename: str, is_image: bool = False) -> bool:
        """Check if file exists in S4"""
        try:
            prefix = "images" if is_image else "audio"
            object_key = self.s4_client.generate_object_key(filename, prefix=prefix)
            
            # List objects with this key
            objects = await self.s4_client.list_objects(prefix=object_key, max_keys=1)
            return len(objects) > 0 and objects[0]['key'] == object_key
            
        except Exception as e:
            logger.error(f"Error checking S4 for {filename}: {e}")
            return False

    def check_file_in_mega(self, mega_path: str) -> bool:
        """Check if file still exists in MEGA"""
        try:
            result = subprocess.run([
                'mega-ls', mega_path
            ], capture_output=True, text=True, timeout=30)
            
            return result.returncode == 0
            
        except Exception as e:
            logger.error(f"Error checking MEGA for {mega_path}: {e}")
            return False

    async def verify_files(self) -> Dict:
        """Verify migration status of all files"""
        print("\n" + "="*60)
        print("🔍 VERIFYING MIGRATION")
        print("="*60)
        
        # Get files that should be migrated
        audio_tracks, image_paths = self.get_files_to_migrate()
        
        verification_results = {
            'audio': {'migrated': 0, 'not_migrated': 0, 'duplicated': 0, 'missing': 0, 'details': []},
            'images': {'migrated': 0, 'not_migrated': 0, 'duplicated': 0, 'missing': 0, 'details': []}
        }
        
        # Verify audio files
        print(f"🎵 Verifying {len(audio_tracks)} audio files...")
        for track in audio_tracks:
            filename = Path(track.file_path).name
            mega_path = f"{self.mega_audio_path}/{filename}"
            
            in_s4 = await self.check_file_in_s4(filename, is_image=False)
            in_mega = self.check_file_in_mega(mega_path)
            
            if in_s4 and not in_mega:
                status = "migrated"
                verification_results['audio']['migrated'] += 1
            elif in_s4 and in_mega:
                status = "duplicated"
                verification_results['audio']['duplicated'] += 1
            elif not in_s4 and in_mega:
                status = "not_migrated"
                verification_results['audio']['not_migrated'] += 1
            else:
                status = "missing"
                verification_results['audio']['missing'] += 1
            
            verification_results['audio']['details'].append({
                'filename': filename,
                'track_id': track.id,
                'status': status,
                'in_s4': in_s4,
                'in_mega': in_mega
            })
        
        # Verify image files
        print(f"🖼️  Verifying {len(image_paths)} image files...")
        for image_path in image_paths:
            filename = Path(image_path).name
            mega_path = f"{self.mega_images_path}/{filename}"
            
            in_s4 = await self.check_file_in_s4(filename, is_image=True)
            in_mega = self.check_file_in_mega(mega_path)
            
            if in_s4 and not in_mega:
                status = "migrated"
                verification_results['images']['migrated'] += 1
            elif in_s4 and in_mega:
                status = "duplicated"
                verification_results['images']['duplicated'] += 1
            elif not in_s4 and in_mega:
                status = "not_migrated"
                verification_results['images']['not_migrated'] += 1
            else:
                status = "missing"
                verification_results['images']['missing'] += 1
            
            verification_results['images']['details'].append({
                'filename': filename,
                'status': status,
                'in_s4': in_s4,
                'in_mega': in_mega
            })
        
        # Print verification report
        self._print_verification_report(verification_results)
        
        return verification_results

    def _print_verification_report(self, results: Dict):
        """Print verification report"""
        print(f"\n📅 Verification completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print()
        
        # Audio files summary
        audio = results['audio']
        print("🎵 AUDIO FILES:")
        print(f"  ✅ Migrated: {audio['migrated']}")
        print(f"  ⏳ Not migrated: {audio['not_migrated']}")
        print(f"  ⚠️  Duplicated: {audio['duplicated']}")
        print(f"  ❌ Missing: {audio['missing']}")
        print()
        
        # Image files summary
        images = results['images']
        print("🖼️  IMAGE FILES:")
        print(f"  ✅ Migrated: {images['migrated']}")
        print(f"  ⏳ Not migrated: {images['not_migrated']}")
        print(f"  ⚠️  Duplicated: {images['duplicated']}")
        print(f"  ❌ Missing: {images['missing']}")
        print()
        
        # Show issues if any
        issues = []
        
        # Collect problematic files
        for detail in audio['details']:
            if detail['status'] in ['not_migrated', 'duplicated', 'missing']:
                issues.append(f"🎵 {detail['filename']}: {detail['status']}")
        
        for detail in images['details']:
            if detail['status'] in ['not_migrated', 'duplicated', 'missing']:
                issues.append(f"🖼️  {detail['filename']}: {detail['status']}")
        
        if issues:
            print("⚠️  ISSUES FOUND:")
            for issue in issues[:10]:  # Show first 10 issues
                print(f"  {issue}")
            if len(issues) > 10:
                print(f"  ... and {len(issues) - 10} more issues")
        else:
            print("✅ No issues found! All files migrated successfully.")

def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='MEGA to S4 Migration Tool')
    parser.add_argument('action', choices=['precheck', 'migrate', 'verify', 'menu'], 
                       help='Action to perform')
    parser.add_argument('--batch-size', type=int, default=3, 
                       help='Batch size for migration (default: 3)')
    
    args = parser.parse_args()
    
    async def run_action(action):
        migrator = MegaToS4Migrator()
        
        try:
            await migrator.start()
            
            if action == 'precheck':
                await migrator.run_precheck()
                
            elif action == 'migrate':
                # Show confirmation
                print("\n" + "="*60)
                print("⚠️  MIGRATION CONFIRMATION")
                print("="*60)
                print("This will:")
                print("1. Download files from MEGA")
                print("2. Upload them to S4 object storage")
                print("3. Delete them from MEGA (IRREVERSIBLE)")
                print()
                print("Make sure you have:")
                print("✅ S4 credentials configured")
                print("✅ MEGA CLI authenticated")
                print("✅ Stable internet connection")
                print("="*60)
                
                confirm = input("Continue with migration? (yes/no): ").strip().lower()
                if confirm != 'yes':
                    print("❌ Migration cancelled")
                    return
                
                await migrator.run_migration(batch_size=args.batch_size)
                
            elif action == 'verify':
                await migrator.verify_files()
            
        except KeyboardInterrupt:
            print("\n❌ Operation cancelled by user")
        except Exception as e:
            logger.error(f"❌ Operation failed: {e}")
            raise
        finally:
            await migrator.close()

    def show_menu():
        """Interactive menu"""
        while True:
            print("\n" + "="*50)
            print("🔄 MEGA TO S4 MIGRATION TOOL")
            print("="*50)
            print("1. 🔍 Pre-check (analyze files)")
            print("2. 🚀 Migrate (move files to S4)")
            print("3. ✅ Verify (check migration status)")
            print("4. 🚪 Exit")
            print("="*50)
            
            choice = input("Choose an option (1-4): ").strip()
            
            if choice == '1':
                asyncio.run(run_action('precheck'))
            elif choice == '2':
                asyncio.run(run_action('migrate'))
            elif choice == '3':
                asyncio.run(run_action('verify'))
            elif choice == '4':
                print("👋 Goodbye!")
                break
            else:
                print("❌ Invalid choice. Please try again.")

    if args.action == 'menu':
        show_menu()
    else:
        asyncio.run(run_action(args.action))

if __name__ == "__main__":
    main()