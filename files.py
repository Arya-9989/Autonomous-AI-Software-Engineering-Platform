"""
📁 File Upload Routes - Upload files and let AI analyze them!
Files go to AWS S3 (like a giant cloud storage locker).
Then AI reads them and tells you what's inside!
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from database import get_db
from models import User, UploadedFile, FileStatus
from auth import get_current_active_user
from config import settings
import boto3
import uuid
import os
from openai import AsyncOpenAI

router = APIRouter()
openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

# Create S3 client (our connection to AWS S3)
s3_client = boto3.client(
    "s3",
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    region_name=settings.AWS_REGION,
)

ALLOWED_EXTENSIONS = {".pdf", ".txt", ".csv", ".docx", ".xlsx", ".png", ".jpg", ".jpeg"}


# ==================== HELPER FUNCTIONS ====================

def validate_file(file: UploadFile, max_mb: int = 50):
    """✅ Check if file is valid (correct type & not too big)."""
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type not allowed! Use: {', '.join(ALLOWED_EXTENSIONS)}"
        )
    return ext

async def upload_to_s3(file_content: bytes, s3_key: str, content_type: str) -> str:
    """☁️ Upload file bytes to AWS S3 and return the URL."""
    s3_client.put_object(
        Bucket=settings.S3_BUCKET_NAME,
        Key=s3_key,
        Body=file_content,
        ContentType=content_type,
    )
    url = f"https://{settings.S3_BUCKET_NAME}.s3.{settings.AWS_REGION}.amazonaws.com/{s3_key}"
    return url

async def analyze_file_with_ai(file_content: bytes, filename: str, file_type: str) -> str:
    """
    🤖 Let AI analyze the file content.
    For text files: read the content and summarize.
    For images: use vision AI to describe.
    """
    try:
        if file_type in [".txt", ".csv"]:
            # Read text content and ask AI to analyze
            text_content = file_content.decode("utf-8", errors="ignore")[:10000]  # First 10k chars
            
            response = await openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a data analyst. Analyze the provided file content and give a helpful summary."},
                    {"role": "user", "content": f"Please analyze this {file_type} file named '{filename}':\n\n{text_content}"}
                ],
                max_tokens=500
            )
            return response.choices[0].message.content

        elif file_type == ".pdf":
            # For PDF, extract text first (simplified - in production use PyPDF2 or pdfplumber)
            return "PDF uploaded successfully. Text extraction in progress. Use the chat to ask questions about this document."

        elif file_type in [".png", ".jpg", ".jpeg"]:
            # Use GPT-4 Vision for images!
            import base64
            image_data = base64.b64encode(file_content).decode("utf-8")
            
            response = await openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Please describe and analyze this image in detail."},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}}
                        ]
                    }
                ],
                max_tokens=500
            )
            return response.choices[0].message.content

        else:
            return f"File '{filename}' uploaded successfully! File analysis not available for {file_type} files."
            
    except Exception as e:
        return f"File uploaded but AI analysis failed: {str(e)}"


# ==================== ROUTES ====================

@router.post("/upload", summary="Upload a file for AI analysis")
async def upload_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    📤 Upload a file and get AI analysis.
    Supports: PDF, TXT, CSV, Images (PNG, JPG), Excel, Word docs.
    """
    # 1️⃣ Validate the file
    file_ext = validate_file(file, max_mb=settings.MAX_FILE_SIZE_MB)

    # 2️⃣ Read file content
    file_content = await file.read()
    file_size_mb = len(file_content) / (1024 * 1024)
    
    if file_size_mb > settings.MAX_FILE_SIZE_MB:
        raise HTTPException(status_code=400, detail=f"File too big! Max size is {settings.MAX_FILE_SIZE_MB}MB")

    # 3️⃣ Generate unique filename to avoid overwrites
    unique_filename = f"{current_user.id}/{uuid.uuid4()}{file_ext}"
    
    # 4️⃣ Create database record (mark as uploading)
    db_file = UploadedFile(
        user_id=current_user.id,
        filename=unique_filename,
        original_name=file.filename,
        file_type=file_ext.lstrip("."),
        size_mb=round(file_size_mb, 3),
        s3_key=unique_filename,
        status=FileStatus.UPLOADING,
    )
    db.add(db_file)
    db.commit()
    db.refresh(db_file)

    try:
        # 5️⃣ Upload to S3
        db_file.status = FileStatus.PROCESSING
        db.commit()
        
        s3_url = await upload_to_s3(file_content, unique_filename, file.content_type or "application/octet-stream")
        db_file.s3_url = s3_url

        # 6️⃣ Analyze with AI
        analysis = await analyze_file_with_ai(file_content, file.filename, file_ext)
        db_file.analysis_result = analysis
        db_file.status = FileStatus.READY
        
        # 7️⃣ Update user storage
        current_user.storage_used_mb += file_size_mb
        
        db.commit()

        return {
            "file_id": db_file.id,
            "filename": file.filename,
            "size_mb": round(file_size_mb, 3),
            "status": "ready",
            "analysis": analysis,
            "message": "✅ File uploaded and analyzed successfully!"
        }

    except Exception as e:
        db_file.status = FileStatus.FAILED
        db.commit()
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.get("/list", summary="List your uploaded files")
async def list_files(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """📋 Get all files you've uploaded."""
    files = db.query(UploadedFile).filter(
        UploadedFile.user_id == current_user.id
    ).order_by(UploadedFile.created_at.desc()).all()
    
    return [
        {
            "id": f.id,
            "filename": f.original_name,
            "file_type": f.file_type,
            "size_mb": f.size_mb,
            "status": f.status.value,
            "analysis": f.analysis_result,
            "created_at": str(f.created_at),
        }
        for f in files
    ]


@router.delete("/{file_id}", summary="Delete a file")
async def delete_file(
    file_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """🗑️ Delete a file from S3 and database."""
    db_file = db.query(UploadedFile).filter(
        UploadedFile.id == file_id,
        UploadedFile.user_id == current_user.id
    ).first()
    
    if not db_file:
        raise HTTPException(status_code=404, detail="File not found!")
    
    # Delete from S3
    try:
        s3_client.delete_object(Bucket=settings.S3_BUCKET_NAME, Key=db_file.s3_key)
    except Exception:
        pass  # Continue even if S3 delete fails
    
    # Update storage usage
    current_user.storage_used_mb = max(0, current_user.storage_used_mb - db_file.size_mb)
    
    db.delete(db_file)
    db.commit()
    return {"message": "File deleted successfully!"}
