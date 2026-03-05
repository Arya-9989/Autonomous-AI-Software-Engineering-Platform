"""
🤖 Chat Routes - The heart of your AI platform!
This is where users talk to the AI.
Think of it like a walkie-talkie: user sends message → AI responds!
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from database import get_db
from models import User, Conversation, Message, MessageRole, SubscriptionTier, UsageLog
from auth import get_current_active_user
from config import settings
from openai import AsyncOpenAI
import time

router = APIRouter()
client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

# How many free messages per day?
FREE_DAILY_LIMIT = settings.FREE_TIER_MESSAGES


# ==================== SCHEMAS ====================

class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[int] = None   # None = start new conversation
    model: str = "gpt-4o"                    # Which AI model to use
    system_prompt: Optional[str] = None      # Optional custom system instructions

class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: str


# ==================== HELPER: Check daily limit ====================

def check_daily_limit(user: User, db: Session):
    """⚠️ Free users can only send X messages per day."""
    if user.subscription_tier == SubscriptionTier.FREE:
        if user.messages_today >= FREE_DAILY_LIMIT:
            raise HTTPException(
                status_code=429,
                detail=f"Daily limit reached! Free plan allows {FREE_DAILY_LIMIT} messages/day. Upgrade to Pro for unlimited!"
            )


# ==================== ROUTES ====================

@router.post("/send", summary="Send a message to the AI")
async def send_message(
    data: ChatRequest,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    💬 Send a message and get an AI response.
    This is the main chat endpoint!
    """
    # 1️⃣ Check if user hit their daily limit
    check_daily_limit(current_user, db)

    # 2️⃣ Get or create a conversation
    if data.conversation_id:
        conversation = db.query(Conversation).filter(
            Conversation.id == data.conversation_id,
            Conversation.user_id == current_user.id
        ).first()
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found!")
    else:
        # Auto-generate a title from the first message (first 50 chars)
        title = data.message[:50] + ("..." if len(data.message) > 50 else "")
        conversation = Conversation(
            user_id=current_user.id,
            title=title,
            model_used=data.model
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)

    # 3️⃣ Get conversation history (last 20 messages for context)
    history = db.query(Message).filter(
        Message.conversation_id == conversation.id
    ).order_by(Message.created_at.asc()).limit(20).all()

    # 4️⃣ Build the messages list for OpenAI
    messages_for_ai = []
    
    # Add system prompt
    system_content = data.system_prompt or "You are a helpful, friendly AI assistant."
    messages_for_ai.append({"role": "system", "content": system_content})
    
    # Add conversation history
    for msg in history:
        messages_for_ai.append({"role": msg.role.value, "content": msg.content})
    
    # Add the new user message
    messages_for_ai.append({"role": "user", "content": data.message})

    # 5️⃣ Save user's message to database
    user_message = Message(
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content=data.message
    )
    db.add(user_message)

    # 6️⃣ Call OpenAI API! 🚀
    start_time = time.time()
    try:
        response = await client.chat.completions.create(
            model=data.model,
            messages=messages_for_ai,
            max_tokens=2000,
            temperature=0.7,  # 0 = robotic, 1 = very creative, 0.7 = balanced
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI error: {str(e)}")
    
    response_time_ms = int((time.time() - start_time) * 1000)

    # 7️⃣ Extract the AI's response
    ai_content = response.choices[0].message.content
    tokens_used = response.usage.total_tokens
    cost_usd = tokens_used * 0.000003  # Approximate cost (varies by model)

    # 8️⃣ Save AI response to database
    ai_message = Message(
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content=ai_content,
        tokens_used=tokens_used,
        model=data.model
    )
    db.add(ai_message)

    # 9️⃣ Update user stats
    current_user.messages_today += 1
    current_user.total_messages += 1

    # 🔟 Log usage for analytics
    usage_log = UsageLog(
        user_id=current_user.id,
        endpoint="chat",
        tokens_used=tokens_used,
        cost_usd=cost_usd,
        response_ms=response_time_ms
    )
    db.add(usage_log)
    db.commit()

    return {
        "conversation_id": conversation.id,
        "message": ai_content,
        "tokens_used": tokens_used,
        "model": data.model,
        "response_time_ms": response_time_ms,
    }


@router.get("/conversations", summary="List all your conversations")
async def list_conversations(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """📋 Get all your past conversations."""
    conversations = db.query(Conversation).filter(
        Conversation.user_id == current_user.id,
        Conversation.is_archived == False
    ).order_by(Conversation.updated_at.desc()).limit(50).all()
    
    return [
        {
            "id": c.id,
            "title": c.title,
            "model_used": c.model_used,
            "created_at": str(c.created_at),
            "updated_at": str(c.updated_at),
        }
        for c in conversations
    ]


@router.get("/conversations/{conv_id}/messages", summary="Get messages in a conversation")
async def get_messages(
    conv_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """📜 Get all messages in a specific conversation."""
    conversation = db.query(Conversation).filter(
        Conversation.id == conv_id,
        Conversation.user_id == current_user.id
    ).first()
    
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found!")
    
    messages = db.query(Message).filter(
        Message.conversation_id == conv_id
    ).order_by(Message.created_at.asc()).all()
    
    return [
        {
            "id": m.id,
            "role": m.role.value,
            "content": m.content,
            "tokens_used": m.tokens_used,
            "created_at": str(m.created_at),
        }
        for m in messages
    ]


@router.delete("/conversations/{conv_id}", summary="Delete a conversation")
async def delete_conversation(
    conv_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """🗑️ Delete a conversation and all its messages."""
    conversation = db.query(Conversation).filter(
        Conversation.id == conv_id,
        Conversation.user_id == current_user.id
    ).first()
    
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found!")
    
    db.delete(conversation)
    db.commit()
    return {"message": "Conversation deleted!"}
