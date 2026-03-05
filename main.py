"""
🚀 AI Platform - Main Backend Entry Point
Think of this like the FRONT DOOR of your app!
Everyone who visits your website talks to this file first.
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn
import logging

from config import settings
from database import engine, Base
from routes import auth, chat, files, billing, admin

# Create all database tables automatically (like setting up your desk before work!)
Base.metadata.create_all(bind=engine)

# Create the main app object (this IS your backend!)
app = FastAPI(
    title="🤖 My AI Platform",
    description="A full-stack cloud AI platform with chat, file analysis, billing, and admin!",
    version="1.0.0",
    docs_url="/api/docs",       # Visit this URL to test your API!
    redoc_url="/api/redoc",
)

# ✅ CORS - This lets your frontend website talk to your backend
# Think of it like giving your frontend a "guest pass" to enter the backend building
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 📦 Register all the route "modules" (each one handles a different part of the app)
app.include_router(auth.router,    prefix="/api/auth",    tags=["🔐 Authentication"])
app.include_router(chat.router,    prefix="/api/chat",    tags=["🤖 AI Chat"])
app.include_router(files.router,   prefix="/api/files",   tags=["📁 File Upload"])
app.include_router(billing.router, prefix="/api/billing", tags=["💳 Billing"])
app.include_router(admin.router,   prefix="/api/admin",   tags=["🛡️ Admin"])

# 🏠 Health check - like asking "are you alive?" to the server
@app.get("/", tags=["Health"])
async def root():
    return {"status": "✅ AI Platform is running!", "version": "1.0.0"}

@app.get("/api/health", tags=["Health"])
async def health_check():
    return {"status": "healthy", "message": "All systems operational!"}

# 🚨 Global error handler - catches any unexpected errors so the app doesn't crash
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logging.error(f"Unhandled error: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong on our end. Please try again!"}
    )

# 🏃 Run the server when you execute this file directly
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",    # Listen on all network interfaces
        port=8000,          # Port number (like a door number)
        reload=True,        # Auto-restart when you change code (super useful!)
        log_level="info"
    )
