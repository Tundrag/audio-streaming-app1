from database import SessionLocal
from app import User, UserRole, pwd_context
import getpass
from datetime import datetime, timezone
import re
import secrets

def validate_email(email: str) -> bool:
    """Simple email validation"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))

def validate_password(password: str) -> tuple[bool, str]:
    """Password validation"""
    if len(password) < 8:
        return False, "Password must be at least 8 characters long"
    if not any(c.isupper() for c in password):
        return False, "Password must contain at least one uppercase letter"
    if not any(c.islower() for c in password):
        return False, "Password must contain at least one lowercase letter"
    if not any(c.isdigit() for c in password):
        return False, "Password must contain at least one number"
    return True, "Password is valid"

def validate_pin(pin: str) -> tuple[bool, str]:
    """PIN validation"""
    if not pin.isdigit():
        return False, "PIN must contain only numbers"
    if len(pin) != 6:
        return False, "PIN must be exactly 6 digits"
    return True, "PIN is valid"

def generate_default_pin() -> str:
    """Generate a secure 6-digit PIN"""
    return ''.join(secrets.choice('0123456789') for _ in range(6))

def create_creator():
    db = SessionLocal()
    try:
        print("\nCreate Creator Account")
        print("-" * 50)
        
        # Get user input
        while True:
            email = input("Enter email: ").strip()
            if validate_email(email):
                break
            print("Invalid email format! Please try again.")
        
        username = input("Enter username: ").strip()
        
        while True:
            password = getpass.getpass("Enter password: ")
            is_valid, message = validate_password(password)
            if not is_valid:
                print(message)
                continue
                
            confirm_password = getpass.getpass("Confirm password: ")
            if password == confirm_password:
                break
            print("Passwords do not match! Please try again.")

        # Get PIN
        while True:
            print("\nCreator PIN Setup")
            print("This 6-digit PIN will be used by your Patreon members to access your content.")
            pin = input("Enter 6-digit PIN (press Enter for auto-generated): ").strip()
            
            if not pin:
                pin = generate_default_pin()
                print(f"Generated PIN: {pin}")
                break
            
            is_valid, message = validate_pin(pin)
            if is_valid:
                break
            print(message)
        
        # Validate input
        if not email or not username or not password:
            print("All fields are required!")
            return
        
        # Check if user already exists
        existing_user = db.query(User).filter(
            (User.email == email) | (User.username == username)
        ).first()
        
        if existing_user:
            print(f"User with email {email} or username {username} already exists!")
            return
        
        # Create creator user
        creator = User(
            email=email,
            username=username,
            password_hash=pwd_context.hash(password),
            creator_pin=pin,  # Add the PIN
            role=UserRole.CREATOR,
            is_active=True,
            created_by=None,
            last_login=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        
        # Save to database
        db.add(creator)
        db.commit()
        db.refresh(creator)
        
        print("\nCreator account created successfully!")
        print("-" * 50)
        print(f"ID: {creator.id}")
        print(f"Email: {creator.email}")
        print(f"Username: {creator.username}")
        print(f"Creator PIN: {pin}")
        print(f"Role: {creator.role}")
        print(f"Created At: {creator.created_at}")
        print("\nIMPORTANT: Save your Creator PIN. Your Patreon members will need it to access your content.")
        print("\nYou can now log in using these credentials.")
        
    except Exception as e:
        print(f"Error creating creator user: {str(e)}")
        if db:
            db.rollback()
    finally:
        if db:
            db.close()

if __name__ == "__main__":
    create_creator()