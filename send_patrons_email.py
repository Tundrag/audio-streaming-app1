import asyncio
import logging
import os
from sendgrid.helpers.mail import Mail, Email, To, HtmlContent
from patreon_client import PatreonClient
from typing import Dict, List
from dotenv import load_dotenv
from sendgrid import SendGridAPIClient

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

EMAIL_SUBJECT = "[Important] Web Audio Access Information & Platform Changes"

EMAIL_BODY = """We've made important changes to our platforms that affect your Web Audio access.

**What's Happening:**
Our previous platform has undergone some changes. Despite this, we're committed to ensuring you maintain access to your audiobooks.

**Important Information:**

1. **For Existing Web Audio Users:**
   If you've previously logged into Web Audio, you still have access without an active subscription. Your existing credentials continue to work:
   - Login Email: {user_email}
   - Current PIN: 541558
   - Login URL: https://webaudio.me/login

2. **For New or Returning Members:**
   Want to try Web Audio for free? We have a limited-time offer:
   - Join our new Discord server: https://discord.gg/Vz8SzfNPqe
   - Ask about the free trial in the server
   - We'll set you up right away

3. **New Discord Community:**
   Our new Discord server is at: https://discord.gg/Vz8SzfNPqe
   Please join to stay updated and connected with our community.

These changes ensure you can continue enjoying your audiobooks without interruption. Thank you for your ongoing support!"""


def create_html_content(name: str, email: str, body: str) -> str:
    """Create well-formatted HTML email content"""
    formatted_body = body.format(user_email=email)
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{
                font-family: Arial, sans-serif;
                line-height: 1.6;
                color: #333333;
                max-width: 600px;
                margin: 0 auto;
                padding: 20px;
            }}
            .header {{
                margin-bottom: 20px;
            }}
            .content {{
                background-color: #ffffff;
                padding: 20px;
            }}
            .footer {{
                margin-top: 20px;
                font-size: 12px;
                color: #666666;
                text-align: center;
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <h2>Free Audiobook</h2>
        </div>
        <div class="content">
            <p>Dear {name},</p>
            {formatted_body.replace('\n', '<br>')}
            <p>Best regards,<br>Free Audiobook Team</p>
        </div>
        <div class="footer">
            <p>This email was sent to you as a member of our Patreon community.</p>
            <p>© 2025 Free Audiobook. All rights reserved.</p>
        </div>
    </body>
    </html>
    """
    return html

async def get_categorized_patrons() -> Dict[str, List]:
    """Get patrons categorized by their subscription type and status"""
    try:
        logger.info("Initializing Patreon client...")
        client = PatreonClient()
        # Change this line from client.initialize() to:
        if not await client.ensure_initialized():
            logger.error("Failed to initialize Patreon client")
            return {"yearly": [], "paid": [], "other": [], "test": None}
        
        logger.info("Fetching campaign members...")
        members = await client.get_campaign_members()
        
        yearly_patrons = []
        paid_patrons = []
        other_patrons = []
        test_patron = None
        
        for member in members:
            attributes = member.get("attributes", {})
            email = attributes.get("email")
            if email:
                patron_info = {
                    "email": email,
                    "name": attributes.get("full_name", "Valued Patron"),
                    "amount": attributes.get("currently_entitled_amount_cents", 0)/100,
                    "status": attributes.get("patron_status"),
                    "last_charge_status": attributes.get("last_charge_status")
                }
                
                if email == "tkinrinde@gmail.com":
                    test_patron = patron_info
                    logger.info(f"Found test patron: {email}")
                elif patron_info["last_charge_status"] == "Paid" and patron_info["amount"] >= 100:
                    yearly_patrons.append(patron_info)
                    logger.info(f"Found yearly patron: {email} (${patron_info['amount']:.2f})")
                elif patron_info["last_charge_status"] == "Paid":
                    paid_patrons.append(patron_info)
                    logger.info(f"Found paid patron: {email} (${patron_info['amount']:.2f})")
                else:
                    other_patrons.append(patron_info)
                    logger.info(f"Found other patron: {email} (Status: {patron_info['last_charge_status']})")
        
        return {
            "yearly": yearly_patrons,
            "paid": paid_patrons,
            "other": other_patrons,
            "test": test_patron
        }
        
    except Exception as e:
        logger.error(f"Error getting patron info: {str(e)}", exc_info=True)
        return {"yearly": [], "paid": [], "other": [], "test": None}

async def send_bulk_email(recipients: list, subject: str, body: str):
    """Send email to patrons using SendGrid with automatic retry mechanism for rate limits"""
    sg = SendGridAPIClient(os.getenv('SENDGRID_API_KEY'))
    
    # Tracking variables
    successful_sends = []
    failed_sends = []
    batch_size = 50  # Reduced batch size
    delay_between_batches = 3  # seconds
    delay_between_emails = 0.8  # seconds
    
    # Retry settings
    max_retries = 3
    base_retry_delay = 5  # seconds
    
    logger.info(f"Starting bulk send to {len(recipients)} recipients in batches of {batch_size}")
    
    # Process in batches
    for i in range(0, len(recipients), batch_size):
        batch = recipients[i:i + batch_size]
        logger.info(f"\nProcessing batch {(i//batch_size) + 1} ({len(batch)} recipients)")
        
        for recipient in batch:
            # Track retries for this specific recipient
            retries = 0
            sent_successfully = False
            
            while retries <= max_retries and not sent_successfully:
                try:
                    # If this is a retry, log it and wait
                    if retries > 0:
                        retry_delay = base_retry_delay * (2 ** (retries - 1))  # Exponential backoff
                        logger.info(f"Retry #{retries} for {recipient['email']} after {retry_delay}s delay...")
                        print(f"↻ Retrying {recipient['email']} (attempt {retries}/{max_retries})...")
                        await asyncio.sleep(retry_delay)
                    
                    # Create HTML content with proper formatting
                    html_content = create_html_content(recipient['name'], recipient['email'], body)
                    
                    # Create message with proper headers and formatting
                    message = Mail(
                        from_email=('freeaudiobooks176@gmail.com', 'Free Audiobook'),
                        to_emails=(recipient['email'], recipient['name']),
                        subject=subject,
                        html_content=html_content
                    )
                    
                    # Add reply-to header for anti-spam
                    message.reply_to = Email('freeaudiobooks176@gmail.com', 'Free Audiobook Support')
                    
                    logger.info(f"Sending to {recipient['email']}{' (retry #'+str(retries)+')' if retries > 0 else ''}")
                    response = sg.send(message)
                    
                    if response.status_code == 202:
                        logger.info(f"✓ Success: {recipient['email']}{' after '+str(retries)+' retries' if retries > 0 else ''}")
                        successful_sends.append(recipient)
                        sent_successfully = True
                    else:
                        # Non-success status code
                        if response.status_code == 403 and retries < max_retries:
                            # This is a rate limit we can retry
                            logger.warning(f"⚠ Rate limit detected for {recipient['email']} (attempt {retries+1}/{max_retries})")
                            retries += 1
                        else:
                            # Either not a 403 or we've exhausted retries
                            logger.warning(f"✗ Failed ({response.status_code}): {recipient['email']} after {retries} retries")
                            failed_sends.append(recipient)
                            sent_successfully = True  # Mark as "done" to exit retry loop
                
                except Exception as e:
                    error_msg = str(e)
                    
                    # Check if this is a rate limit error we should retry
                    if ("403" in error_msg or "Forbidden" in error_msg) and retries < max_retries:
                        logger.warning(f"⚠ Rate limit exception for {recipient['email']} (attempt {retries+1}/{max_retries}): {error_msg}")
                        retries += 1
                    else:
                        # Either not a rate limit or we've exhausted retries
                        logger.error(f"✗ Error sending to {recipient['email']} after {retries} retries: {error_msg}")
                        failed_sends.append(recipient)
                        sent_successfully = True  # Mark as "done" to exit retry loop
            
            # Only delay between emails if this isn't the last recipient in the batch
            if recipient != batch[-1]:
                await asyncio.sleep(delay_between_emails)
        
        # Save progress after each batch
        with open('successful_sends.txt', 'w') as f:
            for r in successful_sends:
                f.write(f"{r['email']},{r['name']}\n")
        
        with open('failed_sends.txt', 'w') as f:
            for r in failed_sends:
                f.write(f"{r['email']},{r['name']}\n")
        
        # Count how many emails in this batch hit rate limits
        rate_limited_count = sum(1 for r in batch if r in failed_sends)
        
        # If more than half the batch got rate limited, add an extra delay
        if rate_limited_count > len(batch) / 2:
            cooldown = 30  # 30 second cooldown before next batch
            logger.warning(f"High rate limiting detected! Cooling down for {cooldown} seconds before next batch...")
            print(f"\n⚠️ High rate of failures detected. Cooling down for {cooldown} seconds...")
            await asyncio.sleep(cooldown)
        else:
            logger.info(f"\nBatch complete. Waiting {delay_between_batches} seconds before next batch...")
            await asyncio.sleep(delay_between_batches)
    
    # Final summary
    logger.info("\n" + "="*50)
    logger.info("Send Summary:")
    logger.info(f"Total recipients: {len(recipients)}")
    logger.info(f"Successfully sent: {len(successful_sends)}")
    logger.info(f"Failed sends: {len(failed_sends)}")
    logger.info("="*50)
    
    if failed_sends:
        logger.info("\nFailed sends have been saved to 'failed_sends.txt'")
        logger.info("You can retry these sends later using option 6 in the main menu")
    
    return successful_sends, failed_sends

async def get_email_content(group_name: str) -> tuple:
    """Get email content from user input"""
    print("\nEmail Preview:")
    print("-" * 50)
    print(f"To: {group_name} patrons")
    print(f"From: Free Audiobook <freeaudiobooks176@gmail.com>")
    print(f"Subject: {EMAIL_SUBJECT}")
    print("\nBody:")
    print("Dear Patron,")
    print(EMAIL_BODY)
    print("\nBest regards,")
    print("Free Audiobook Team")
    print("-" * 50)
    
    return EMAIL_SUBJECT, EMAIL_BODY

async def main():
    logger.info("Starting email distribution process...")
    
    # Get categorized patrons
    patrons = await get_categorized_patrons()
    
    print("\nPatron Statistics:")
    print(f"Yearly subscribers: {len(patrons['yearly'])}")
    print(f"Paid patrons: {len(patrons['paid'])}")
    print(f"Other patrons: {len(patrons['other'])}")
    if patrons['test']:
        print(f"Test patron email: {patrons['test']['email']}")
    
    while True:
        print("\nChoose an option:")
        print("1. Send email to yearly subscribers")
        print("2. Send email to paid patrons")
        print("3. Send email to other patrons")
        print("4. Send email to all patrons")
        print("5. Send test email to tkinrinde@gmail.com")
        print("6. Retry failed sends")
        print("7. Exit")
        
        choice = input("\nEnter your choice (1-7): ")
        
        if choice == "1" and patrons['yearly']:
            subject, body = await get_email_content("yearly")
            print(f"\nThis will send to {len(patrons['yearly'])} yearly subscribers")
            if input("\nSend this email to yearly subscribers? (yes/no): ").lower() == 'yes':
                await send_bulk_email(patrons['yearly'], subject, body)
                
        elif choice == "2" and patrons['paid']:
            subject, body = await get_email_content("paid")
            print(f"\nThis will send to {len(patrons['paid'])} paid patrons")
            if input("\nSend this email to paid patrons? (yes/no): ").lower() == 'yes':
                await send_bulk_email(patrons['paid'], subject, body)
                
        elif choice == "3" and patrons['other']:
            subject, body = await get_email_content("other")
            print(f"\nThis will send to {len(patrons['other'])} other patrons")
            if input("\nSend this email to other patrons? (yes/no): ").lower() == 'yes':
                await send_bulk_email(patrons['other'], subject, body)
                
        elif choice == "4":
            subject, body = await get_email_content("all")
            all_patrons = patrons['yearly'] + patrons['paid'] + patrons['other']
            print(f"\nThis will send to {len(all_patrons)} total patrons")
            if input("\nSend this email to ALL patrons? (yes/no): ").lower() == 'yes':
                await send_bulk_email(all_patrons, subject, body)

        elif choice == "5":
            # Hardcoded test email to tkinrinde@gmail.com
            test_email = "tkinrinde@gmail.com"
            test_name = "Test User"
            
            print(f"\nSending test email to: {test_email}")
            if input("Proceed with test email? (yes/no): ").lower() == 'yes':
                try:
                    # Create HTML content
                    html_content = create_html_content(test_name, test_email, EMAIL_BODY)
                    
                    # Create message
                    sg = SendGridAPIClient(os.getenv('SENDGRID_API_KEY'))
                    message = Mail(
                        from_email=('freeaudiobooks176@gmail.com', 'Free Audiobook'),
                        to_emails=(test_email, test_name),
                        subject=EMAIL_SUBJECT,
                        html_content=html_content
                    )
                    
                    # Add reply-to header
                    message.reply_to = Email('freeaudiobooks176@gmail.com', 'Free Audiobook Support')
                    
                    # Send the email
                    logger.info(f"Sending test email to {test_email}...")
                    response = sg.send(message)
                    
                    if response.status_code == 202:
                        print(f"✓ Success! Test email sent to {test_email}")
                        logger.info(f"Test email successfully sent to {test_email}")
                    else:
                        print(f"✗ Failed to send test email. Status code: {response.status_code}")
                        logger.error(f"SendGrid returned status code {response.status_code}")
                        
                except Exception as e:
                    print(f"\nError while sending test email: {str(e)}")
                    logger.error(f"Test email failed with error: {str(e)}", exc_info=True)
        
        elif choice == "6":
            # Retry failed sends
            try:
                with open('failed_sends.txt', 'r') as f:
                    failed_recipients = []
                    for line in f:
                        email, name = line.strip().split(',')
                        failed_recipients.append({
                            'email': email,
                            'name': name
                        })
                
                if failed_recipients:
                    print(f"\nFound {len(failed_recipients)} failed sends to retry")
                    if input("Retry these sends? (yes/no): ").lower() == 'yes':
                        await send_bulk_email(failed_recipients, EMAIL_SUBJECT, EMAIL_BODY)
                else:
                    print("No failed sends to retry")
            except FileNotFoundError:
                print("No failed sends file found")
                
        elif choice == "7":
            logger.info("Exiting...")
            break
            
        else:
            print("Invalid choice or no patrons in selected category!")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\nProcess interrupted by user")
    except Exception as e:
        logger.error("Process failed!", exc_info=True)