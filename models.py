"""
📋 Models - The blueprints for your database tables!
Each class here = one table in your database.
Think of each class like a form you fill out (User form, Message form, etc.)
"""

from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, Float, ForeignKey, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base
import enum


# 📊 Enums - predefined choices (like a dropdown menu)
class UserRole(str, enum.Enum):
    USER = "user"
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"

class SubscriptionTier(str, enum.Enum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"

class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"

class FileStatus(str, enum.Enum):
    UPLOADING = "uploading"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


# 👤 USER TABLE - stores all registered users
class User(Base):
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, index=True)
    email         = Column(String(255), unique=True, index=True, nullable=False)
    username      = Column(String(100), unique=True, index=True, nullable=False)
    full_name     = Column(String(200), nullable=True)
    hashed_password = Column(String(255), nullable=False)
    
    # Account status
    is_active     = Column(Boolean, default=True)
    is_verified   = Column(Boolean, default=False)      # Email verified?
    role          = Column(Enum(UserRole), default=UserRole.USER)
    
    # Subscription
    subscription_tier = Column(Enum(SubscriptionTier), default=SubscriptionTier.FREE)
    stripe_customer_id = Column(String(255), nullable=True)
    
    # Usage tracking
    messages_today     = Column(Integer, default=0)
    total_messages     = Column(Integer, default=0)
    storage_used_mb    = Column(Float, default=0.0)
    
    # Timestamps (auto-set!)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    updated_at    = Column(DateTime(timezone=True), onupdate=func.now())
    last_login    = Column(DateTime(timezone=True), nullable=True)
    
    # Relationships (like links between tables)
    conversations = relationship("Conversation", back_populates="user", cascade="all, delete-orphan")
    files         = relationship("UploadedFile", back_populates="user", cascade="all, delete-orphan")
    subscriptions = relationship("Subscription", back_populates="user")
    api_keys      = relationship("APIKey", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User {self.email}>"


# 💬 CONVERSATION TABLE - groups messages into conversations
class Conversation(Base):
    __tablename__ = "conversations"

    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False)
    title      = Column(String(500), default="New Conversation")
    model_used = Column(String(100), default="gpt-4o")
    is_archived = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user     = relationship("User", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")


# 📩 MESSAGE TABLE - individual chat messages
class Message(Base):
    __tablename__ = "messages"

    id              = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False)
    role            = Column(Enum(MessageRole), nullable=False)   # "user" or "assistant"
    content         = Column(Text, nullable=False)
    tokens_used     = Column(Integer, default=0)    # Track API token cost
    model           = Column(String(100), nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    conversation = relationship("Conversation", back_populates="messages")


# 📁 FILE TABLE - uploaded files by users
class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id           = Column(Integer, primary_key=True, index=True)
    user_id      = Column(Integer, ForeignKey("users.id"), nullable=False)
    filename     = Column(String(500), nullable=False)
    original_name = Column(String(500), nullable=False)
    file_type    = Column(String(100), nullable=False)   # "pdf", "csv", "txt", etc.
    size_mb      = Column(Float, default=0.0)
    s3_key       = Column(String(1000), nullable=False)   # Path in S3 bucket
    s3_url       = Column(String(2000), nullable=True)    # Public URL (if public)
    status       = Column(Enum(FileStatus), default=FileStatus.UPLOADING)
    analysis_result = Column(Text, nullable=True)        # AI analysis of the file
    created_at   = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="files")


# 💳 SUBSCRIPTION TABLE - payment subscriptions
class Subscription(Base):
    __tablename__ = "subscriptions"

    id                    = Column(Integer, primary_key=True, index=True)
    user_id               = Column(Integer, ForeignKey("users.id"), nullable=False)
    stripe_subscription_id = Column(String(255), unique=True, nullable=False)
    tier                  = Column(Enum(SubscriptionTier), nullable=False)
    status                = Column(String(50), default="active")  # active, canceled, past_due
    current_period_start  = Column(DateTime(timezone=True), nullable=True)
    current_period_end    = Column(DateTime(timezone=True), nullable=True)
    created_at            = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="subscriptions")


# 🔑 API KEY TABLE - for developers accessing your platform
class APIKey(Base):
    __tablename__ = "api_keys"

    id          = Column(Integer, primary_key=True, index=True)
    user_id     = Column(Integer, ForeignKey("users.id"), nullable=False)
    name        = Column(String(200), nullable=False)       # e.g. "My App Key"
    key_hash    = Column(String(255), unique=True, nullable=False)  # Store hashed!
    key_prefix  = Column(String(20), nullable=False)        # e.g. "aip_abc123" (shown to user)
    is_active   = Column(Boolean, default=True)
    last_used   = Column(DateTime(timezone=True), nullable=True)
    requests_count = Column(Integer, default=0)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="api_keys")


# 📊 USAGE LOG - track every API call (for billing & analytics)
class UsageLog(Base):
    __tablename__ = "usage_logs"

    id           = Column(Integer, primary_key=True, index=True)
    user_id      = Column(Integer, ForeignKey("users.id"), nullable=False)
    endpoint     = Column(String(200), nullable=False)   # Which API was used
    tokens_used  = Column(Integer, default=0)
    cost_usd     = Column(Float, default=0.0)
    response_ms  = Column(Integer, default=0)            # How fast was the response?
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
