# cleanup_db.py
from sqlalchemy import text
from database import engine
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def cleanup_database():
    """Clean up database by dropping all tables and enum types"""
    with engine.connect() as connection:
        try:
            logger.info("Starting database cleanup...")
            
            # Drop tables if they exist
            connection.execute(text("""
                DROP TABLE IF EXISTS play_history CASCADE;
                DROP TABLE IF EXISTS access_logs CASCADE;
                DROP TABLE IF EXISTS tracks CASCADE;
                DROP TABLE IF EXISTS albums CASCADE;
                DROP TABLE IF EXISTS users CASCADE;
                DROP TABLE IF EXISTS alembic_version CASCADE;
            """))
            logger.info("Tables dropped successfully")
            
            # Drop enum types if they exist
            connection.execute(text("""
                DO $$ 
                BEGIN
                    DROP TYPE IF EXISTS patreontier CASCADE;
                    DROP TYPE IF EXISTS userrole CASCADE;
                    DROP TYPE IF EXISTS patrontierenum CASCADE;
                EXCEPTION 
                    WHEN others THEN 
                        NULL;
                END $$;
            """))
            
            # Commit the changes
            connection.commit()
            logger.info("Enum types dropped successfully")
            logger.info("Database cleaned successfully!")
            
        except Exception as e:
            logger.error(f"Error during database cleanup: {e}")
            connection.rollback()
            raise

def main():
    try:
        cleanup_database()
    except Exception as e:
        logger.error(f"Database cleanup failed: {e}")
        exit(1)
    logger.info("Database cleanup completed successfully")

if __name__ == "__main__":
    main()