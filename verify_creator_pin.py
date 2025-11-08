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

def verify_creator_pin_column():
    """Verify that creator_pin column exists with correct properties"""
    engine = create_engine(DATABASE_URL)
    
    # SQL to check column existence and properties
    sql = """
    SELECT 
        column_name,
        data_type,
        is_nullable,
        column_default
    FROM 
        information_schema.columns
    WHERE 
        table_name = 'users'
        AND column_name = 'creator_pin';
    """
    
    try:
        with engine.connect() as conn:
            result = conn.execute(text(sql)).fetchone()
            
            if result is None:
                print("❌ creator_pin column does not exist in users table!")
                return False
                
            print("\n=== Creator PIN Column Verification ===")
            print(f"✓ Column name: {result[0]}")
            print(f"✓ Data type: {result[1]}")
            print(f"✓ Nullable: {result[2]}")
            print(f"✓ Default value: {result[3] or 'None'}")
            print("\nVerification successful! Column exists with correct properties.")
            return True
            
    except Exception as e:
        print(f"Error during verification: {str(e)}")
        return False

if __name__ == "__main__":
    verify_creator_pin_column()