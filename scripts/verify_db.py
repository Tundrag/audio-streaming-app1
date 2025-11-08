# verify_db.py
from sqlalchemy import create_engine, text
import os
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def verify_database_state():
    """Verify the database structure and content after migration"""
    engine = create_engine(os.getenv("DATABASE_URL", "postgresql://tundrag:Tundrag2010!@localhost/audio_streaming_db"))
    
    try:
        with engine.connect() as conn:
            # Check table structure
            logger.info("Checking table structure...")
            
            # Check columns in users table
            result = conn.execute(text("""
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_name = 'users' 
                ORDER BY ordinal_position;
            """))
            
            logger.info("\nUsers table columns:")
            for row in result:
                logger.info(f"Column: {row.column_name}, Type: {row.data_type}")
            
            # Check indexes
            result = conn.execute(text("""
                SELECT indexname, indexdef 
                FROM pg_indexes 
                WHERE tablename = 'users';
            """))
            
            logger.info("\nUsers table indexes:")
            for row in result:
                logger.info(f"Index: {row.indexname}")
                logger.info(f"Definition: {row.indexdef}")
            
            # Check existing creators
            result = conn.execute(text("""
                SELECT email, username, creator_pin, patreon_tier_data
                FROM users 
                WHERE role = 'creator';
            """))
            
            logger.info("\nExisting creators:")
            creators = result.fetchall()
            if creators:
                for creator in creators:
                    logger.info(f"""
                    Creator: {creator.username}
                    Email: {creator.email}
                    PIN: {creator.creator_pin}
                    Tier Data: {creator.patreon_tier_data}
                    """)
            else:
                logger.info("No creators found in database")
            
            # Check if old columns were properly removed
            result = conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'users' 
                AND column_name = 'tier';
            """))
            
            if result.fetchone():
                logger.warning("Warning: Old 'tier' column still exists!")
            else:
                logger.info("\nOld 'tier' column successfully removed")
            
            return True
            
    except Exception as e:
        logger.error(f"Verification failed: {str(e)}")
        return False

if __name__ == "__main__":
    if verify_database_state():
        logger.info("\nDatabase verification completed successfully!")
    else:
        logger.error("\nDatabase verification failed!")