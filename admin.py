"""
🛡️ Admin Routes - The Control Room!
Only admins can access these endpoints.
Think of it like the principal's office — only the boss can go in!
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from database import get_db
from models import User, Conversation, Message, UploadedFile, Subscription, UsageLog, UserRole, SubscriptionTier
from auth import require_admin
from datetime import datetime, timedelta

router = APIRouter()


# ==================== DASHBOARD OVERVIEW ====================

@router.get("/dashboard", summary="Admin dashboard overview")
async def admin_dashboard(
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """
    📊 Get platform-wide statistics.
    Like a report card for your whole app!
    """
    today = datetime.utcnow().date()
    last_30_days = datetime.utcnow() - timedelta(days=30)

    # Count totals
    total_users      = db.query(func.count(User.id)).scalar()
    active_users     = db.query(func.count(User.id)).filter(User.is_active == True).scalar()
    total_messages   = db.query(func.count(Message.id)).scalar()
    total_files      = db.query(func.count(UploadedFile.id)).scalar()
    
    # New users in last 30 days
    new_users_30d = db.query(func.count(User.id)).filter(
        User.created_at >= last_30_days
    ).scalar()

    # Subscription breakdown
    free_users       = db.query(func.count(User.id)).filter(User.subscription_tier == SubscriptionTier.FREE).scalar()
    pro_users        = db.query(func.count(User.id)).filter(User.subscription_tier == SubscriptionTier.PRO).scalar()
    enterprise_users = db.query(func.count(User.id)).filter(User.subscription_tier == SubscriptionTier.ENTERPRISE).scalar()

    # Revenue estimate
    estimated_mrr = (pro_users * 19) + (enterprise_users * 99)  # Monthly Recurring Revenue!

    # Total tokens & estimated cost
    total_tokens = db.query(func.sum(UsageLog.tokens_used)).scalar() or 0
    total_cost   = db.query(func.sum(UsageLog.cost_usd)).scalar() or 0.0

    # Messages today
    messages_today = db.query(func.count(Message.id)).filter(
        func.date(Message.created_at) == today
    ).scalar()

    return {
        "users": {
            "total": total_users,
            "active": active_users,
            "new_last_30_days": new_users_30d,
        },
        "subscriptions": {
            "free": free_users,
            "pro": pro_users,
            "enterprise": enterprise_users,
            "estimated_mrr_usd": estimated_mrr,
        },
        "usage": {
            "total_messages": total_messages,
            "messages_today": messages_today,
            "total_files": total_files,
            "total_tokens_used": total_tokens,
            "estimated_ai_cost_usd": round(total_cost, 2),
        }
    }


# ==================== USER MANAGEMENT ====================

@router.get("/users", summary="List all users")
async def list_users(
    page: int = 1,
    per_page: int = 50,
    search: str = None,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """👥 List all users with pagination and search."""
    query = db.query(User)
    
    if search:
        query = query.filter(
            (User.email.contains(search)) |
            (User.username.contains(search)) |
            (User.full_name.contains(search))
        )
    
    total = query.count()
    users = query.order_by(desc(User.created_at)).offset((page - 1) * per_page).limit(per_page).all()
    
    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "users": [
            {
                "id": u.id,
                "email": u.email,
                "username": u.username,
                "full_name": u.full_name,
                "role": u.role.value,
                "subscription_tier": u.subscription_tier.value,
                "is_active": u.is_active,
                "total_messages": u.total_messages,
                "storage_used_mb": u.storage_used_mb,
                "created_at": str(u.created_at),
                "last_login": str(u.last_login),
            }
            for u in users
        ]
    }


@router.put("/users/{user_id}/ban", summary="Ban a user")
async def ban_user(
    user_id: int,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """🚫 Disable a user's account."""
    if user_id == current_admin.id:
        raise HTTPException(status_code=400, detail="Can't ban yourself!")
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found!")
    
    user.is_active = False
    db.commit()
    return {"message": f"User {user.email} has been banned."}


@router.put("/users/{user_id}/unban", summary="Unban a user")
async def unban_user(
    user_id: int,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """✅ Re-enable a banned user's account."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found!")
    
    user.is_active = True
    db.commit()
    return {"message": f"User {user.email} has been unbanned."}


@router.put("/users/{user_id}/change-tier", summary="Change user subscription tier")
async def change_user_tier(
    user_id: int,
    new_tier: str,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """💎 Manually change a user's subscription tier."""
    valid_tiers = {t.value for t in SubscriptionTier}
    if new_tier not in valid_tiers:
        raise HTTPException(status_code=400, detail=f"Invalid tier! Use: {valid_tiers}")
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found!")
    
    user.subscription_tier = SubscriptionTier(new_tier)
    db.commit()
    return {"message": f"User {user.email} upgraded to {new_tier}!"}


# ==================== SYSTEM STATS ====================

@router.get("/usage-stats", summary="Platform usage over time")
async def usage_stats(
    days: int = 7,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """📈 Get usage stats for the last N days."""
    start_date = datetime.utcnow() - timedelta(days=days)
    
    daily_messages = db.query(
        func.date(Message.created_at).label("date"),
        func.count(Message.id).label("count")
    ).filter(
        Message.created_at >= start_date
    ).group_by(func.date(Message.created_at)).all()

    return {
        "period_days": days,
        "daily_messages": [
            {"date": str(row.date), "messages": row.count}
            for row in daily_messages
        ]
    }
