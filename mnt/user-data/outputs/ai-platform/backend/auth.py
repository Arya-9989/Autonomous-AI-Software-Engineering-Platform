"""
🔐 Auth - The Security Guard of your app!
JWT = JSON Web Token. It's like a STAMP on your hand at an event.
When you login, you get a stamp. Show the stamp → get access. Simple!
"""

from datetime import datetime, timedelta
from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from jose import JWTError, jwt
from passlib.context import CryptContext
from database import get_db
from models import User, UserRole
from config import settings

# 🔒 Password hasher (never store plain text passwords!)
# bcrypt scrambles your password into a random-looking string
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# 🎫 Bearer token scheme (reads "Authorization: Bearer <token>" from request headers)
security = HTTPBearer()


# ==================== PASSWORD FUNCTIONS ====================

def hash_password(password: str) -> str:
    """Turn a plain password into a scrambled hash. One-way only!"""
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Check if a plain password matches its hash."""
    return pwd_context.verify(plain_password, hashed_password)


# ==================== JWT TOKEN FUNCTIONS ====================

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    🎟️ Create a JWT token (like making a stamped ticket).
    The token contains the user's ID and an expiry time.
    """
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire, "iat": datetime.utcnow()})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def decode_token(token: str) -> dict:
    """
    🔍 Decode and verify a JWT token.
    Returns the data inside if valid, raises error if expired or fake.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session has expired. Please login again!",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ==================== USER AUTHENTICATION ====================

def authenticate_user(db: Session, email: str, password: str) -> Optional[User]:
    """
    🚪 Check if email + password are correct.
    Returns the User object if correct, None if wrong.
    """
    user = db.query(User).filter(User.email == email).first()
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


# ==================== DEPENDENCY INJECTION (FastAPI magic!) ====================

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """
    👮 Get the currently logged-in user from their JWT token.
    FastAPI calls this automatically on protected routes.
    """
    token = credentials.credentials
    payload = decode_token(token)
    
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token!")
    
    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found!")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Your account has been disabled!")
    
    return user

async def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    """Shortcut to get active user (commonly used dependency)."""
    return current_user

async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """🛡️ Only allow admins! Blocks regular users."""
    if current_user.role not in [UserRole.ADMIN, UserRole.SUPER_ADMIN]:
        raise HTTPException(status_code=403, detail="Admin access required!")
    return current_user

async def require_pro_subscription(current_user: User = Depends(get_current_user)) -> User:
    """💎 Only allow Pro/Enterprise users!"""
    from models import SubscriptionTier
    if current_user.subscription_tier == SubscriptionTier.FREE:
        raise HTTPException(
            status_code=403,
            detail="This feature requires a Pro or Enterprise subscription. Please upgrade!"
        )
    return current_user
