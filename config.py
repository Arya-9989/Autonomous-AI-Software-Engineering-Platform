"""
⚙️ Config - All your secret settings live here!
Think of this like a locked diary where you keep all important passwords & keys.
NEVER share your .env file with anyone! 🔒
"""

from pydantic_settings import BaseSettings
from typing import List
import os


class Settings(BaseSettings):
    # 🗄️ DATABASE (PostgreSQL on AWS RDS)
    DATABASE_URL: str = "postgresql://user:password@localhost:5432/aiplatform"

    # 🔑 JWT SECRET - Like the master key to your whole app
    # Generate a strong one: python -c "import secrets; print(secrets.token_hex(32))"
    SECRET_KEY: str = "your-super-secret-key-change-this-in-production!"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours

    # 🤖 AI PROVIDER KEYS
    OPENAI_API_KEY: str = ""          # Get from: https://platform.openai.com
    ANTHROPIC_API_KEY: str = ""       # Get from: https://console.anthropic.com

    # ☁️ AWS SETTINGS (for file storage with S3)
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"
    S3_BUCKET_NAME: str = "my-ai-platform-files"

    # 💳 STRIPE (for payments)
    STRIPE_SECRET_KEY: str = ""          # Get from: https://dashboard.stripe.com
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PRICE_ID_PRO: str = ""        # Your Pro plan price ID
    STRIPE_PRICE_ID_ENTERPRISE: str = "" # Your Enterprise plan price ID

    # 🌐 ALLOWED WEBSITES (that can talk to your backend)
    ALLOWED_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:5500",
        "https://yourdomain.com",  # Replace with your actual domain!
    ]

    # 📧 EMAIL (for welcome emails, password reset, etc.)
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    FROM_EMAIL: str = "noreply@yourplatform.com"

    # 📊 APP SETTINGS
    APP_NAME: str = "My AI Platform"
    MAX_FILE_SIZE_MB: int = 50          # Max file upload size
    MAX_CHAT_HISTORY: int = 50          # Messages to keep in memory
    FREE_TIER_MESSAGES: int = 10        # Free messages per day

    class Config:
        env_file = ".env"               # Reads from your .env file automatically!
        case_sensitive = True


# Create a global settings object (used everywhere in the app)
settings = Settings()
