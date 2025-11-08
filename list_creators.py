# list_creators.py
from database import SessionLocal
from app import User, PatreonTier
from datetime import datetime

def list_creators():
    db = SessionLocal()
    try:
        print("\nList of Creators")
        print("-" * 50)
        
        # Query all creator users
        creators = db.query(User).filter(
            User.is_creator == True
        ).all()
        
        if not creators:
            print("No creators found in database!")
            return
        
        print(f"Found {len(creators)} creator(s):")
        print("-" * 50)
        
        for creator in creators:
            print(f"ID: {creator.id}")
            print(f"Email: {creator.email}")
            print(f"Username: {creator.username}")
            print(f"Tier: {creator.tier.value}")
            print(f"Active: {creator.is_active}")
            print(f"Last Login: {creator.last_login}")
            print(f"Created At: {creator.created_at}")
            print("-" * 50)
            
    except Exception as e:
        print(f"Error listing creators: {str(e)}")
    finally:
        if db:
            db.close()

if __name__ == "__main__":
    list_creators()