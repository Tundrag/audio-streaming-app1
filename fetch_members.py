import asyncio
from patreon_client import PatreonClient
import logging
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def fetch_members():
    try:
        # Debug: Print environment variables
        logger.info(f"Access Token: {os.getenv('PATREON_ACCESS_TOKEN')[:10]}...")  # Only show first 10 chars for security
        logger.info(f"Campaign ID: {os.getenv('PATREON_CAMPAIGN_ID')}")
        
        # Initialize client
        client = PatreonClient()
        client.initialize()
        
        # Get all members
        logger.info("Fetching campaign members...")
        members = await client.get_campaign_members()
        
        # Display member information
        logger.info(f"\nTotal members found: {len(members)}")
        
        for member in members:
            attributes = member.get("attributes", {})
            print("\nMember Details:")
            print(f"Name: {attributes.get('full_name', 'N/A')}")
            print(f"Email: {attributes.get('email', 'N/A')}")
            print(f"Status: {attributes.get('patron_status', 'N/A')}")
            print(f"Amount: ${attributes.get('currently_entitled_amount_cents', 0)/100:.2f}")
            print(f"Last Charge Status: {attributes.get('last_charge_status', 'N/A')}")
            if 'tier_info' in attributes:
                print(f"Tier: {attributes['tier_info'].get('title', 'N/A')}")
            print("-" * 50)
            
    except Exception as e:
        logger.error(f"Error fetching members: {str(e)}")

if __name__ == "__main__":
    asyncio.run(fetch_members())