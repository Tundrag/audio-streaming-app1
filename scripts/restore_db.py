# restore_db.py
import os
import subprocess
from dotenv import load_dotenv
import logging
from sqlalchemy import create_engine, text
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Database connection settings
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME')

def run_psql_command(command, as_postgres=True):
    """Run a PostgreSQL command"""
    try:
        if as_postgres:
            full_command = f'sudo -u postgres psql -c "{command}"'
        else:
            full_command = f'PGPASSWORD="{DB_PASSWORD}" psql -U {DB_USER} -h {DB_HOST} -p {DB_PORT} -c "{command}"'
        
        result = subprocess.run(full_command, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(f"Command failed: {result.stderr}")
            raise Exception(result.stderr)
        return result.stdout
    except Exception as e:
        logger.error(f"Error running command: {str(e)}")
        raise

def restore_backup():
    """Restore the latest database backup using pg_restore"""
    try:
        # Get backup directory and file
        backup_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), 'backups'))
        backups = sorted([
            f for f in os.listdir(backup_dir) 
            if f.startswith('backup_') and f.endswith('.sql')
        ])
        
        if not backups:
            logger.error("No backup files found!")
            return False
            
        latest_backup = os.path.join(backup_dir, backups[-1])
        logger.info(f"Found latest backup: {latest_backup}")

        # Terminate existing connections
        logger.info("Terminating existing connections...")
        disconnect_command = f"""
        SELECT pg_terminate_backend(pid) 
        FROM pg_stat_activity 
        WHERE datname = '{DB_NAME}' 
        AND pid <> pg_backend_pid();
        """
        run_psql_command(disconnect_command)

        # Drop and recreate database
        logger.info("Dropping and recreating database...")
        run_psql_command(f"DROP DATABASE IF EXISTS {DB_NAME};")
        run_psql_command(f"CREATE DATABASE {DB_NAME} OWNER {DB_USER};")

        # Restore the backup using pg_restore
        logger.info("Restoring from backup...")
        restore_command = f'sudo -u postgres pg_restore -d {DB_NAME} "{latest_backup}"'
        result = subprocess.run(restore_command, shell=True, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"Restore failed: {result.stderr}")
            raise Exception(result.stderr)

        logger.info("Database restore completed successfully!")
        return True

    except Exception as e:
        logger.error(f"Error restoring backup: {str(e)}")
        return False

def main():
    try:
        # List available backups
        backup_dir = os.path.join(os.path.dirname(__file__), 'backups')
        backups = sorted([
            f for f in os.listdir(backup_dir) 
            if f.startswith('backup_') and f.endswith('.sql')
        ])
        
        print("\nAvailable backups:")
        for i, backup in enumerate(backups, 1):
            print(f"{i}. {backup}")
        
        # Ask for confirmation
        confirm = input("\nAre you sure you want to restore the latest backup? This will DELETE all current data! (y/N): ")
        
        if confirm.lower() != 'y':
            print("Restore cancelled.")
            return

        # Test connection
        try:
            engine = create_engine(f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}")
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Database connection test successful")
        except Exception as e:
            logger.error(f"Database connection test failed: {e}")
            return

        # Proceed with restore
        if restore_backup():
            logger.info("Database has been successfully restored!")
        else:
            logger.error("Database restore failed!")
            
    except Exception as e:
        logger.error(f"Restore process failed: {e}")

if __name__ == "__main__":
    main()