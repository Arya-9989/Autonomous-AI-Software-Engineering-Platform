"""
🔐 Auth Routes - Login, Signup, Logout, Password Reset
These are the "doors" users walk through to get into your app.
"""

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr, validator
from datetime import datetime
from database import get_db
from models import User, SubscriptionTier
from auth import (
    hash_password, authenticate_user, create_access_token,
    get_current_active_user
)

router = APIRouter()


# ==================== REQUEST/RESPONSE SCHEMAS ====================
# These define what data users send and receive (like form fields)

class SignupRequest(BaseModel):
    email: EmailStr
    username: str
    full_name: str
    password: str

    @validator("password")
    def password_must_be_strong(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters!")
        return v

    @validator("username")
    def username_valid(cls, v):
        if len(v) < 3:
            raise ValueError("Username must be at least 3 characters!")
        if not v.isalnum():
            raise ValueError("Username can only contain letters and numbers!")
        return v.lower()

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict

class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


# ==================== ROUTES ====================

@router.post("/signup", status_code=201, summary="Create a new account")
async def signup(
    data: SignupRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    📝 Register a new user.
    Like filling out a sign-up form at school!
    """
    # Check if email already exists
    if db.query(User).filter(User.email == data.email).first():
        raise HTTPException(status_code=400, detail="Email already registered!")

    # Check if username already taken
    if db.query(User).filter(User.username == data.username).first():
        raise HTTPException(status_code=400, detail="Username already taken!")

    # Create new user
    user = User(
        email=data.email,
        username=data.username,
        full_name=data.full_name,
        hashed_password=hash_password(data.password),
        subscription_tier=SubscriptionTier.FREE,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # TODO: background_tasks.add_task(send_welcome_email, user.email)

    return {
        "message": "🎉 Account created successfully! Welcome aboard!",
        "user_id": user.id,
        "email": user.email
    }


@router.post("/login", response_model=TokenResponse, summary="Login to your account")
async def login(data: LoginRequest, db: Session = Depends(get_db)):
    """
    🚪 Login with email and password.
    Returns a JWT token you use for all future requests.
    """
    user = authenticate_user(db, data.email, data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Wrong email or password. Try again!",
        )

    # Update last login time
    user.last_login = datetime.utcnow()
    db.commit()

    # Create JWT token
    token = create_access_token(data={"sub": str(user.id)})

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "username": user.username,
            "full_name": user.full_name,
            "role": user.role,
            "subscription_tier": user.subscription_tier,
        }
    }


@router.get("/me", summary="Get your profile info")
async def get_my_profile(current_user: User = Depends(get_current_active_user)):
    """
    👤 Get info about the currently logged-in user.
    Like looking in the mirror to see who you are!
    """
    return {
        "id": current_user.id,
        "email": current_user.email,
        "username": current_user.username,
        "full_name": current_user.full_name,
        "role": current_user.role,
        "subscription_tier": current_user.subscription_tier,
        "messages_today": current_user.messages_today,
        "total_messages": current_user.total_messages,
        "storage_used_mb": current_user.storage_used_mb,
        "created_at": current_user.created_at,
        "last_login": current_user.last_login,
    }


@router.put("/profile", summary="Update your profile")
async def update_profile(
    full_name: str,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """✏️ Update profile information."""
    current_user.full_name = full_name
    db.commit()
    return {"message": "Profile updated successfully!"}


@router.post("/change-password", summary="Change your password")
async def change_password(
    data: PasswordChangeRequest,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """🔑 Change your password (must know old password first)."""
    from auth import verify_password, hash_password
    
    if not verify_password(data.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is wrong!")
    
    if len(data.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters!")

    current_user.hashed_password = hash_password(data.new_password)
    db.commit()
    return {"message": "✅ Password changed successfully!"}
