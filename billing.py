"""
💳 Billing Routes - Handle payments with Stripe!
Stripe is like a super-safe cash register for your app.
You never touch real credit card numbers — Stripe handles all that!
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from database import get_db
from models import User, Subscription, SubscriptionTier
from auth import get_current_active_user
from config import settings
import stripe
import json

router = APIRouter()
stripe.api_key = settings.STRIPE_SECRET_KEY

# Map tier names to Stripe Price IDs
TIER_PRICES = {
    "pro": settings.STRIPE_PRICE_ID_PRO,
    "enterprise": settings.STRIPE_PRICE_ID_ENTERPRISE,
}


# ==================== ROUTES ====================

@router.get("/plans", summary="View available subscription plans")
async def get_plans():
    """
    📊 Show all available plans and their features.
    No login required — anyone can see this!
    """
    return {
        "plans": [
            {
                "id": "free",
                "name": "🆓 Free",
                "price": "$0/month",
                "features": [
                    "10 AI messages per day",
                    "5 file uploads",
                    "Basic models only",
                    "Community support"
                ]
            },
            {
                "id": "pro",
                "name": "⭐ Pro",
                "price": "$19/month",
                "features": [
                    "Unlimited AI messages",
                    "100 file uploads/month",
                    "All AI models (GPT-4, Claude, etc.)",
                    "50GB file storage",
                    "Priority support",
                    "API access"
                ]
            },
            {
                "id": "enterprise",
                "name": "🚀 Enterprise",
                "price": "$99/month",
                "features": [
                    "Everything in Pro",
                    "Unlimited file storage",
                    "Custom AI fine-tuning",
                    "Dedicated support",
                    "SLA guarantee",
                    "Team collaboration",
                    "Custom integrations"
                ]
            }
        ]
    }


@router.post("/create-checkout/{tier}", summary="Start a subscription checkout")
async def create_checkout_session(
    tier: str,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    🛒 Create a Stripe checkout session.
    User will be redirected to Stripe's payment page.
    """
    if tier not in TIER_PRICES:
        raise HTTPException(status_code=400, detail="Invalid plan! Choose 'pro' or 'enterprise'")

    price_id = TIER_PRICES[tier]
    if not price_id:
        raise HTTPException(status_code=500, detail="Plan price not configured. Contact support.")

    # Create or get Stripe customer
    if not current_user.stripe_customer_id:
        customer = stripe.Customer.create(
            email=current_user.email,
            name=current_user.full_name,
            metadata={"user_id": str(current_user.id)}
        )
        current_user.stripe_customer_id = customer.id
        db.commit()

    # Create Stripe checkout session
    try:
        session = stripe.checkout.Session.create(
            customer=current_user.stripe_customer_id,
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            success_url="https://yourplatform.com/dashboard?payment=success",
            cancel_url="https://yourplatform.com/billing?payment=canceled",
            metadata={"user_id": str(current_user.id), "tier": tier}
        )
        return {"checkout_url": session.url, "session_id": session.id}

    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=f"Payment error: {str(e)}")


@router.post("/webhook", summary="Stripe webhook receiver", include_in_schema=False)
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """
    🔔 This is called automatically by Stripe when payment events happen.
    Like a doorbell that Stripe rings when something important happens!
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    # Verify the webhook is really from Stripe (security check!)
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    # Handle different event types
    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "checkout.session.completed":
        # 🎉 Payment successful! Upgrade the user's account
        user_id = int(data["metadata"]["user_id"])
        tier = data["metadata"]["tier"]
        subscription_id = data.get("subscription")

        user = db.query(User).filter(User.id == user_id).first()
        if user:
            user.subscription_tier = SubscriptionTier.PRO if tier == "pro" else SubscriptionTier.ENTERPRISE
            
            if subscription_id:
                sub = Subscription(
                    user_id=user_id,
                    stripe_subscription_id=subscription_id,
                    tier=user.subscription_tier,
                    status="active"
                )
                db.add(sub)
            
            db.commit()

    elif event_type == "customer.subscription.deleted":
        # 😢 Subscription cancelled — downgrade to free
        subscription_id = data["id"]
        sub = db.query(Subscription).filter(
            Subscription.stripe_subscription_id == subscription_id
        ).first()
        
        if sub:
            sub.status = "canceled"
            user = db.query(User).filter(User.id == sub.user_id).first()
            if user:
                user.subscription_tier = SubscriptionTier.FREE
            db.commit()

    return {"status": "received"}


@router.post("/cancel", summary="Cancel your subscription")
async def cancel_subscription(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """❌ Cancel the current subscription (at period end)."""
    subscription = db.query(Subscription).filter(
        Subscription.user_id == current_user.id,
        Subscription.status == "active"
    ).first()

    if not subscription:
        raise HTTPException(status_code=404, detail="No active subscription found!")

    try:
        stripe.Subscription.modify(
            subscription.stripe_subscription_id,
            cancel_at_period_end=True  # Don't cancel immediately, let them finish the month
        )
        subscription.status = "canceling"
        db.commit()
        return {"message": "Subscription will be canceled at the end of the billing period."}
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/status", summary="Get your current subscription status")
async def get_subscription_status(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """📊 Check your current plan and usage."""
    subscription = db.query(Subscription).filter(
        Subscription.user_id == current_user.id,
        Subscription.status.in_(["active", "canceling"])
    ).first()

    return {
        "tier": current_user.subscription_tier.value,
        "subscription": {
            "id": subscription.stripe_subscription_id if subscription else None,
            "status": subscription.status if subscription else "none",
            "period_end": str(subscription.current_period_end) if subscription else None,
        } if subscription else None,
        "usage": {
            "messages_today": current_user.messages_today,
            "daily_limit": settings.FREE_TIER_MESSAGES if current_user.subscription_tier == SubscriptionTier.FREE else "unlimited",
            "total_messages": current_user.total_messages,
            "storage_used_mb": current_user.storage_used_mb,
        }
    }
