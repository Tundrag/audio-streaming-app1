from sqlalchemy import create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv
from app import Base, PatreonTier, UserRole
import subprocess

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

def create_enum_types():
    """Create enum types using psql"""
    enum_commands = """
    DO $$
    BEGIN
        -- Drop existing enum types if they exist
        DROP TYPE IF EXISTS patreontier CASCADE;
        DROP TYPE IF EXISTS userrole CASCADE;
        
        -- Create PatreonTier enum
        CREATE TYPE patreontier AS ENUM (
            'support', 'great', 'king', 'emperor', 'god', 'architech', 'monarchs'
        );
        
        -- Create UserRole enum
        CREATE TYPE userrole AS ENUM (
            'patreon', 'team', 'creator'
        );
    END$$;
    """
    
    # Write the commands to a temporary file
    with open('temp_enum.sql', 'w') as f:
        f.write(enum_commands)
    
    try:
        # Execute the commands as postgres user
        subprocess.run([
            'sudo', '-u', 'postgres', 
            'psql', '-d', DB_NAME, 
            '-f', 'temp_enum.sql'
        ], check=True)
        print("Enum types created successfully")
    except subprocess.CalledProcessError as e:
        print(f"Error creating enum types: {e}")
        raise
    finally:
        # Clean up the temporary file
        if os.path.exists('temp_enum.sql'):
            os.remove('temp_enum.sql')

def drop_and_create_tables():
    """Drop all tables and recreate them"""
    try:
        # Create engine
        engine = create_engine(DATABASE_URL)
        
        # Create enum types first
        create_enum_types()
        
        # Drop all tables
        Base.metadata.drop_all(bind=engine)
        print("Existing tables dropped.")
        
        # Create all tables
        Base.metadata.create_all(bind=engine)
        print("Database tables created successfully.")
        
    except Exception as e:
        print(f"Error setting up database: {str(e)}")
        raise

if __name__ == "__main__":
    drop_and_create_tables()