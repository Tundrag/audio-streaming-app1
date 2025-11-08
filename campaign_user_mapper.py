import asyncio
import logging
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_
from models import User, UserRole

logger = logging.getLogger(__name__)

async def link_users_to_campaigns(db: Session):
    """Link users to their correct campaigns based on Patreon tier data"""
    try:
        from patreon_client import patreon_client
        
        # Initialize client with all campaigns
        if not await patreon_client.initialize_from_db(db, creator_id=1):
            raise Exception("Failed to initialize Patreon client")

        # Get all tiers from all campaigns
        all_tiers = await patreon_client.get_campaign_tiers()
        
        # Create lookup dictionary for faster matching
        tier_lookup = {
            f"{tier['title'].lower()}_{tier['amount_cents']}": tier 
            for tier in all_tiers
        }

        # Get all patron users
        users = db.query(User).filter(
            and_(
                User.role == UserRole.PATREON,
                User.patreon_tier_data.is_not(None)
            )
        ).all()

        # Track users by campaign
        campaign_stats = {}
        updates = 0
        errors = []
        details = []

        for user in users:
            tier_title = user.patreon_tier_data.get('title')
            amount_cents = user.patreon_tier_data.get('amount_cents')
            
            if not tier_title or not amount_cents:
                logger.warning(f"User {user.email} missing tier info")
                errors.append(f"User {user.email} missing tier info")
                continue

            # Look up matching tier
            lookup_key = f"{tier_title.lower()}_{amount_cents}"
            matching_tier = tier_lookup.get(lookup_key)
            
            if matching_tier:
                # Update user's campaign_id using the matching tier's campaign info
                user.campaign_id = matching_tier['campaign']['id']
                updates += 1
                
                # Update campaign stats
                campaign_name = matching_tier['campaign']['name']
                if campaign_name not in campaign_stats:
                    campaign_stats[campaign_name] = {
                        'name': campaign_name,
                        'users_matched': 0
                    }
                campaign_stats[campaign_name]['users_matched'] += 1
                
                # Add to details
                details.append({
                    'email': user.email,
                    'tier_title': tier_title,
                    'matched_campaign': campaign_name
                })

                logger.info(
                    f"Matched user {user.email} to campaign "
                    f"{campaign_name} via tier {tier_title}"
                )

        db.commit()
        logger.info(f"Updated {updates} users with campaign associations")
        
        return {
            "success": True,
            "total_users": len(users),
            "users_updated": updates,
            "users_by_campaign": campaign_stats,
            "errors": errors,
            "details": details
        }

    except Exception as e:
        logger.error(f"Error linking users to campaigns: {str(e)}")
        db.rollback()
        return {
            "success": False,
            "error": str(e),
            "total_users": 0,
            "users_updated": 0,
            "users_by_campaign": {},
            "errors": [str(e)],
            "details": []
        }
# Script to run the update
async def main():
    """Run the campaign user mapping script"""
    from database import SessionLocal
    
    db = SessionLocal()
    try:
        logger.info("Starting campaign user mapping process...")
        results = await link_users_to_campaigns(db)
        
        # Print results
        print("\n=== Campaign User Mapping Results ===")
        print(f"Success: {results['success']}")
        print(f"Total Users Processed: {results['total_users']}")
        print(f"Users Updated: {results['users_updated']}")
        print("\nUsers by Campaign:")
        for campaign_id, data in results['users_by_campaign'].items():
            print(f"- {data['name']}: {data['users_matched']} users")
        
        if results["errors"]:
            print("\nErrors encountered:")
            for error in results["errors"]:
                print(f"- {error}")
                
        print("\nDetailed Matches:")
        for detail in results.get("details", []):
            print(f"- {detail['email']}: {detail['tier_title']} -> {detail['matched_campaign']}")
            
    finally:
        db.close()

if __name__ == "__main__":
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Run the script
    asyncio.run(main())