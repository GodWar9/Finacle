from fastapi import APIRouter, HTTPException, status, Depends, UploadFile, File, Form
from uuid import UUID
from typing import Optional
import structlog

from app.auth import get_current_merchant
from app.config import get_settings

import asyncpg
import uuid

logger = structlog.get_logger()
router = APIRouter(prefix="/api/v1", tags=["settlements"])
settings = get_settings()

async def get_pg_pool():
    pool = await asyncpg.create_pool(settings.database_url, min_size=2, max_size=10)
    return pool

@router.post("/settlements/upload", status_code=status.HTTP_202_ACCEPTED)
async def upload_settlement_file(
    file: UploadFile = File(...),
    batch_id: Optional[str] = Form(None),
    merchant_id: str = Depends(get_current_merchant),
):
    """Upload a bank settlement file for processing"""
    if not batch_id:
        batch_id = str(uuid.uuid4())

    content = await file.read()

    # Parse the settlement file (fixed-width format)
    # For now, just store the file info
    pool = await get_pg_pool()
    try:
        await pool.execute(
            """INSERT INTO settlement_files (batch_id, filename, content, status, uploaded_by)
               VALUES ($1, $2, $3, 'UPLOADED', $4)""",
            batch_id, file.filename, content, merchant_id
        )

        logger.info("settlement_file_uploaded", batch_id=batch_id, filename=file.filename, merchant_id=merchant_id)

        return {
            "batch_id": batch_id,
            "filename": file.filename,
            "status": "UPLOADED",
            "message": "Settlement file uploaded successfully. Processing will begin shortly."
        }
    except Exception as e:
        logger.error("settlement_upload_failed", error=str(e))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Upload failed: {e}")
    finally:
        await pool.close()

@router.get("/settlements/{batch_id}/status")
async def get_settlement_status(
    batch_id: str,
    merchant_id: str = Depends(get_current_merchant),
):
    """Get settlement processing status"""
    pool = await get_pg_pool()
    try:
        row = await pool.fetchrow(
            "SELECT batch_id, filename, status, processed_count, failed_count, created_at, completed_at FROM settlement_files WHERE batch_id = $1",
            batch_id
        )
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Settlement batch not found")

        return {
            "batch_id": row["batch_id"],
            "filename": row["filename"],
            "status": row["status"],
            "processed_count": row["processed_count"],
            "failed_count": row["failed_count"],
            "created_at": row["created_at"].isoformat(),
            "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None
        }
    finally:
        await pool.close()
