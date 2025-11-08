from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

# Database connection settings
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME')

# Create database URL
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

def add_creator_pin_column():
    """Add creator_pin column to users table"""
    engine = create_engine(DATABASE_URL)
    
    # SQL to add the creator_pin column matching the model definition
    sql = """
    ALTER TABLE users
    ADD COLUMN IF NOT EXISTS creator_pin VARCHAR NULL;
    """
    
    try:
        with engine.connect() as conn:
            conn.execute(text(sql))
            conn.commit()
            print("Successfully added creator_pin column to users table")
            
    except Exception as e:
        print(f"Error adding creator_pin column: {str(e)}")
        raise

if __name__ == "__main__":
    add_creator_pin_column()