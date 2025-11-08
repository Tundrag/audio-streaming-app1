# guest_trial_email_service.py - Updated for SendGrid (keeping all original names)

import logging
import smtplib
import secrets
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.utils import formataddr, formatdate
from typing import Optional
from fastapi import BackgroundTasks
import os
from datetime import datetime

logger = logging.getLogger(__name__)

class GuestTrialEmailService:
    def __init__(self):
        # Email configuration from environment variables - Updated for SendGrid
        self.smtp_host = os.getenv("SMTP_HOST", "smtp.sendgrid.net")  # Changed default to SendGrid
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_username = os.getenv("SMTP_USERNAME", "apikey")  # Changed default for SendGrid
        self.smtp_password = os.getenv("SMTP_PASSWORD")  # Your SendGrid API key
        self.smtp_use_tls = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
        self.from_email = os.getenv("FROM_EMAIL", "noreply@yourdomain.com")  # Changed default
        self.from_name = os.getenv("FROM_NAME", "Web Audio")
        self.domain = os.getenv("EMAIL_DOMAIN", "yourdomain.com")
        
        # Validate SendGrid configuration
        if not self.smtp_password:
            logger.error("SendGrid API key not configured. Please set SMTP_PASSWORD to your SendGrid API key")
        if self.smtp_password and not self.smtp_password.startswith("SG."):
            logger.warning("SendGrid API key should start with 'SG.' - please verify your API key")
    
    def generate_message_id(self) -> str:
        """Generate unique message ID for email tracking"""
        timestamp = str(int(datetime.now().timestamp()))
        random_part = secrets.token_urlsafe(8)
        return f"<{timestamp}.{random_part}@{self.domain}>"
        
    def create_otp_email_html(self, otp_code: str, username: str, creator_name: str) -> str:
        """Create HTML email template for OTP - Updated for better deliverability"""
        return f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Web Audio Verification Code</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    line-height: 1.6;
                    color: #333333;
                    margin: 0;
                    padding: 20px;
                    background-color: #f9f9f9;
                }}
                .container {{
                    max-width: 600px;
                    margin: 0 auto;
                    background-color: #ffffff;
                    border-radius: 8px;
                    padding: 30px;
                    box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
                }}
                .header {{
                    text-align: center;
                    border-bottom: 2px solid #e0e0e0;
                    padding-bottom: 20px;
                    margin-bottom: 30px;
                }}
                .header h1 {{
                    color: #2563eb;
                    margin: 0;
                    font-size: 24px;
                }}
                .verification-code {{
                    font-size: 32px;
                    font-weight: bold;
                    color: #2563eb;
                    text-align: center;
                    background: #f0f7ff;
                    padding: 20px;
                    border-radius: 8px;
                    letter-spacing: 4px;
                    margin: 30px 0;
                    border: 2px solid #e0f2fe;
                }}
                .content {{
                    margin: 20px 0;
                    font-size: 16px;
                }}
                .content p {{
                    margin: 15px 0;
                }}
                .instructions {{
                    background: #f8f9fa;
                    padding: 20px;
                    border-radius: 6px;
                    margin: 20px 0;
                }}
                .instructions h3 {{
                    margin: 0 0 15px 0;
                    color: #374151;
                    font-size: 16px;
                }}
                .instructions ol {{
                    margin: 10px 0;
                    padding-left: 20px;
                }}
                .instructions li {{
                    margin-bottom: 8px;
                }}
                .trial-info {{
                    background: #f0fff4;
                    border: 1px solid #c6f6d5;
                    border-radius: 8px;
                    padding: 20px;
                    margin: 25px 0;
                }}
                .trial-info h3 {{
                    color: #2f855a;
                    margin: 0 0 10px;
                    font-size: 16px;
                }}
                .trial-info ul {{
                    margin: 10px 0;
                    padding-left: 20px;
                    color: #2f855a;
                }}
                .trial-info li {{
                    margin-bottom: 5px;
                }}
                .footer {{
                    margin-top: 30px;
                    padding-top: 20px;
                    border-top: 1px solid #e0e0e0;
                    font-size: 12px;
                    color: #666666;
                    text-align: center;
                }}
                .support-link {{
                    color: #2563eb;
                    text-decoration: none;
                }}
                .support-link:hover {{
                    text-decoration: underline;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>Web Audio</h1>
                    <p>Account Verification</p>
                </div>
                
                <div class="content">
                    <p>Hello {username},</p>
                    
                    <p>Welcome to Web Audio! Please use the verification code below to complete your account setup and start your access to {creator_name}'s content.</p>
                    
                    <div class="verification-code">{otp_code}</div>
                    
                    <div class="instructions">
                        <h3>How to complete verification:</h3>
                        <ol>
                            <li>Return to the Web Audio registration page</li>
                            <li>Enter the verification code above</li>
                            <li>Complete your account setup</li>
                            <li>Begin accessing content</li>
                        </ol>
                    </div>
                    
                    <div class="trial-info">
                        <h3>What's included in your access:</h3>
                        <ul>
                            <li>Full access to all audio content</li>
                            <li>High-quality streaming</li>
                            <li>Download capabilities (as configured by creator)</li>
                            <li>Book request features</li>
                            <li>48 hours of unlimited access</li>
                        </ul>
                    </div>
                    
                    <p><strong>Important:</strong> This verification code expires in 10 minutes for security purposes.</p>
                    
                    <p>If you did not request this verification, you can safely ignore this email.</p>
                    
                    <p>Need assistance? Contact our support team via <a href="https://discord.gg/NHjG6mQdmN" class="support-link">Discord</a>.</p>
                </div>
                
                <div class="footer">
                    <p>This email was sent to {username} because you requested verification at Web Audio.</p>
                    <p>&copy; {datetime.now().year} Web Audio. All rights reserved.</p>
                    <p>
                        <a href="https://discord.gg/NHjG6mQdmN" class="support-link">Support</a> | 
                        <a href="mailto:{self.from_email}?subject=Unsubscribe" class="support-link">Unsubscribe</a>
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
    
    def create_otp_email_text(self, otp_code: str, username: str, creator_name: str) -> str:
        """Create plain text email for OTP (fallback) - Updated for better deliverability"""
        return f"""
Web Audio - Account Verification

Hello {username},

Welcome to Web Audio! Please use the verification code below to complete your account setup and start your access to {creator_name}'s content.

VERIFICATION CODE: {otp_code}

How to complete verification:
1. Return to the Web Audio registration page
2. Enter the verification code above
3. Complete your account setup
4. Begin accessing content

What's included in your access:
- Full access to all audio content
- High-quality streaming
- Download capabilities (as configured by creator)
- Book request features
- 48 hours of unlimited access

IMPORTANT: This verification code expires in 10 minutes for security purposes.

If you did not request this verification, you can safely ignore this email.

Need assistance? Contact our support team via Discord: https://discord.gg/NHjG6mQdmN

This email was sent to {username} because you requested verification at Web Audio.

© {datetime.now().year} Web Audio. All rights reserved.

Support: https://discord.gg/NHjG6mQdmN
To unsubscribe: mailto:{self.from_email}?subject=Unsubscribe
        """.strip()
    
    async def send_email(self, to_email: str, subject: str, html_content: str, text_content: str) -> bool:
        """Send email using SMTP - Updated for SendGrid with better deliverability"""
        try:
            if not self.smtp_username or not self.smtp_password:
                logger.error("SMTP credentials not configured")
                return False
            
            if self.smtp_password and not self.smtp_password.startswith("SG."):
                logger.error("Invalid SendGrid API key format - must start with 'SG.'")
                return False
            
            # Create message with improved headers for deliverability
            msg = MIMEMultipart('alternative')
            
            # Basic headers
            msg['Subject'] = subject
            msg['From'] = formataddr((self.from_name, self.from_email))
            msg['To'] = to_email
            msg['Date'] = formatdate(localtime=True)
            
            # Deliverability headers
            msg['Reply-To'] = self.from_email
            msg['Return-Path'] = self.from_email
            msg['Message-ID'] = self.generate_message_id()
            
            # Additional headers to improve deliverability
            msg['X-Mailer'] = 'Web Audio Verification System v1.0'
            msg['X-Priority'] = '3'  # Normal priority
            msg['X-MSMail-Priority'] = 'Normal'
            msg['Precedence'] = 'bulk'
            
            # Anti-spam headers
            msg['X-Auto-Response-Suppress'] = 'OOF'  # Suppress out-of-office replies
            
            # List management headers (helps with spam filters)
            unsubscribe_email = f"mailto:{self.from_email}?subject=Unsubscribe"
            msg['List-Unsubscribe'] = f"<{unsubscribe_email}>"
            msg['List-Unsubscribe-Post'] = "List-Unsubscribe=One-Click"
            
            # Add both plain text and HTML versions
            text_part = MIMEText(text_content, 'plain', 'utf-8')
            html_part = MIMEText(html_content, 'html', 'utf-8')
            
            msg.attach(text_part)
            msg.attach(html_part)
            
            # Send email via SendGrid SMTP
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                if self.smtp_use_tls:
                    server.starttls()
                # For SendGrid, username is always "apikey"
                server.login(self.smtp_username, self.smtp_password)
                server.send_message(msg)
            
            logger.info(f"✅ OTP email sent successfully via SendGrid to {to_email}")
            return True
            
        except smtplib.SMTPAuthenticationError:
            logger.error("❌ SendGrid authentication failed - check your API key")
            return False
        except smtplib.SMTPRecipientsRefused:
            logger.error(f"❌ SendGrid rejected recipient: {to_email}")
            return False
        except Exception as e:
            logger.error(f"❌ Error sending email to {to_email}: {str(e)}")
            return False

# Global email service instance
email_service = GuestTrialEmailService()

async def send_guest_trial_otp_email(email: str, otp_code: str, username: str, creator_name: str) -> bool:
    """Send OTP email for guest trial registration - Now uses SendGrid internally"""
    try:
        # UPDATED: Clean subject line without emojis or promotional language
        subject = "Web Audio Verification Code"
        
        html_content = email_service.create_otp_email_html(otp_code, username, creator_name)
        text_content = email_service.create_otp_email_text(otp_code, username, creator_name)
        
        success = await email_service.send_email(
            to_email=email,
            subject=subject,
            html_content=html_content,
            text_content=text_content
        )
        
        if success:
            logger.info(f"✅ Guest trial OTP email sent to {email}")
        else:
            logger.error(f"❌ Failed to send guest trial OTP email to {email}")
        
        return success
        
    except Exception as e:
        logger.error(f"❌ Error in send_guest_trial_otp_email: {str(e)}")
        return False

async def send_trial_expiry_notification(email: str, username: str, creator_name: str, hours_remaining: int) -> bool:
    """Send notification when trial is about to expire"""
    try:
        if hours_remaining <= 0:
            subject = "Web Audio Trial Has Ended"  # Removed emoji
            message = f"""
            <h2>Thank you for trying Web Audio!</h2>
            <p>Your access with {creator_name} has ended.</p>
            <p>To continue enjoying premium content, consider supporting {creator_name} on Ko-fi.</p>
            """
        else:
            subject = f"Web Audio Trial Expires in {hours_remaining} Hour{'s' if hours_remaining != 1 else ''}"  # Removed emoji
            message = f"""
            <h2>Your access is ending soon!</h2>
            <p>You have {hours_remaining} hour{'s' if hours_remaining != 1 else ''} left in your access with {creator_name}.</p>
            <p>To continue your access, consider supporting {creator_name} on Ko-fi for full ongoing access.</p>
            """
        
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: #2563eb; color: white; padding: 20px; border-radius: 8px; text-align: center; }}
                .content {{ padding: 20px; }}
                .btn {{ display: inline-block; background: #FF5E5B; color: white; padding: 12px 24px; border-radius: 8px; text-decoration: none; margin: 15px 0; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>Web Audio</h1>
                </div>
                <div class="content">
                    <p>Hello {username}!</p>
                    {message}
                    <a href="https://ko-fi.com/webaudio" class="btn">Support on Ko-fi</a>
                    <p>Thank you for trying Web Audio!</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        # Extract newline to variable (f-strings cannot contain backslashes in expressions)
        newline = '\n'
        cleaned_message = message.replace('<h2>', '').replace('</h2>', '').replace('<p>', '').replace('</p>', newline)

        text_content = f"""
        Web Audio - Trial Notification

        Hello {username}!

        {cleaned_message}

        Support on Ko-fi: https://ko-fi.com/webaudio

        Thank you for trying Web Audio!
        """
        
        return await email_service.send_email(
            to_email=email,
            subject=subject,
            html_content=html_content,
            text_content=text_content
        )
        
    except Exception as e:
        logger.error(f"Error sending trial expiry notification: {str(e)}")
        return False

# Test function to check spam score
async def test_email_spam_score(test_email: str = "test@mail-tester.com") -> dict:
    """Send test email to check spam score"""
    try:
        result = await send_guest_trial_otp_email(
            email=test_email,
            otp_code="123456",
            username="Test User",
            creator_name="Test Creator"
        )
        
        return {
            "test_sent": result,
            "message": "Test email sent via SendGrid to mail-tester.com. Check your spam score at https://www.mail-tester.com/",
            "provider": "SendGrid"
        }
        
    except Exception as e:
        return {"test_sent": False, "error": str(e)}

# Environment setup instructions - Updated for SendGrid
ENVIRONMENT_SETUP = """
# Add these environment variables to your .env file:

# SendGrid SMTP Configuration
SMTP_HOST=smtp.sendgrid.net
SMTP_PORT=587
SMTP_USERNAME=apikey
SMTP_PASSWORD=SG.your_sendgrid_api_key_here
SMTP_USE_TLS=true
FROM_EMAIL=noreply@yourdomain.com
FROM_NAME=Web Audio
EMAIL_DOMAIN=yourdomain.com

# How to get SendGrid API key:
# 1. Log into your SendGrid account
# 2. Go to Settings → API Keys
# 3. Create API Key with "Mail Send" permission
# 4. Copy the key (starts with "SG.")

# How to verify your domain in SendGrid:
# 1. Go to Settings → Sender Authentication
# 2. Authenticate Your Domain
# 3. Add the DNS records SendGrid provides
# 4. Wait for verification (10-15 minutes)

# Benefits of this setup:
# - 3,000 free emails/month
# - Better deliverability than Gmail SMTP
# - Professional sender address
# - Improved spam scores
"""

if __name__ == "__main__":
    print("Guest Trial Email Service - SendGrid Edition")
    print("=" * 50)
    print(ENVIRONMENT_SETUP)
    print("\nTo test spam score:")
    print("1. Run: await test_email_spam_score()")
    print("2. Check results at: https://www.mail-tester.com/")
    print("3. Aim for a score of 8/10 or higher")