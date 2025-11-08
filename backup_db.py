# backup_db.py
import os
import subprocess
from datetime import datetime
from dotenv import load_dotenv
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Database connection settings
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', '')
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'audio_streaming')

def create_backup():
    """Create a backup of the database"""
    try:
        # Create backups directory if it doesn't exist
        backup_dir = os.path.join(os.path.dirname(__file__), 'backups')
        os.makedirs(backup_dir, exist_ok=True)

        # Generate backup filename with timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_file = os.path.join(backup_dir, f'backup_{DB_NAME}_{timestamp}.sql')

        # Set PostgreSQL password environment variable
        env = os.environ.copy()
        env['PGPASSWORD'] = DB_PASSWORD

        # Create backup using pg_dump
        command = [
            'pg_dump',
            '-h', DB_HOST,
            '-p', DB_PORT,
            '-U', DB_USER,
            '-F', 'c',  # Custom format
            '-b',  # Include large objects
            '-v',  # Verbose mode
            '-f', backup_file,
            DB_NAME
        ]

        logger.info(f"Starting backup of database {DB_NAME}")
        logger.info(f"Backup will be saved to: {backup_file}")

        process = subprocess.Popen(
            command,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        stdout, stderr = process.communicate()

        if process.returncode == 0:
            logger.info(f"Backup completed successfully: {backup_file}")
            return True
        else:
            logger.error(f"Backup failed: {stderr.decode()}")
            return False

    except Exception as e:
        logger.error(f"Error creating backup: {e}")
        return False

def main():
    success = create_backup()
    if success:
        logger.info("Database backup completed successfully")
    else:
        logger.error("Database backup failed")

if __name__ == "__main__":
    main()