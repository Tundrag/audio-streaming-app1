# check_db.py
from sqlalchemy import create_engine, text
import os
from dotenv import load_dotenv
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

def check_db_connection():
    # Print current settings
    logger.info("Current Database Settings:")
    logger.info(f"DB_USER: {os.getenv('DB_USER')}")
    logger.info(f"DB_NAME: {os.getenv('DB_NAME')}")
    logger.info(f"DB_HOST: {os.getenv('DB_HOST')}")
    logger.info(f"DB_PORT: {os.getenv('DB_PORT')}")
    
    try:
        # Create connection URL
        db_url = f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
        engine = create_engine(db_url)
        
        # Test connection
        with engine.connect() as conn:
            result = conn.execute(text("SELECT current_user, current_database()"))
            user, database = result.fetchone()
            logger.info(f"Successfully connected as: {user}")
            logger.info(f"Current database: {database}")
            
    except Exception as e:
        logger.error(f"Connection failed: {str(e)}")

if __name__ == "__main__":
    check_db_connection()