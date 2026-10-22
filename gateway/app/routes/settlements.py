import json
import uuid
from typing import Annotated

import asyncpg
import structlog
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status

from app.auth import get_current_merchant

logger = structlog.get_logger()
router = APIRouter(prefix="/api/v1", tags=["settlements"])


async def _get_pool():
    from app.main import pg_pool

    return pg_pool


@router.post("/settlements/upload", status_code=status.HTTP_202_ACCEPTED)
async def upload_settlement_file(
    file: UploadFile = File(...),
    batch_id: uuid.UUID | None = Form(None),
    merchant_id: str = Depends(get_current_merchant),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    """Upload a bank settlement file for processing"""
    if not batch_id:
        if not idempotency_key or len(idempotency_key) > 200:
            raise HTTPException(status_code=400, detail="A valid Idempotency-Key is required")
        # Survives gateway failure between the INSERT and HTTP cache write.
        batch_id = uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(["finacle-settlement", merchant_id, idempotency_key]))
    current_status = "UPLOADED"

    content = await file.read(10 * 1024 * 1024 + 1)
    if not content or len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Settlement file must contain 1 byte to 10 MiB")
    pool = await _get_pool()

    try:
        await pool.execute(
            """INSERT INTO settlement_files (batch_id, filename, content, status, uploaded_by)
               VALUES ($1, $2, $3, 'UPLOADED', $4)""",
            batch_id,
            file.filename or "settlement.txt",
            content,
            merchant_id,
        )
    except asyncpg.UniqueViolationError:
        existing = await pool.fetchrow(
            "SELECT content,filename,status FROM settlement_files WHERE batch_id=$1 AND uploaded_by=$2",
            batch_id,
            merchant_id,
        )
        if (
            not existing
            or existing["content"] != content
            or existing["filename"] != (file.filename or "settlement.txt")
        ):
            raise HTTPException(
                status_code=409, detail="Settlement batch already exists with different content or ownership"
            )

        current_status = existing["status"]

    logger.info("settlement_file_uploaded", batch_id=batch_id, filename=file.filename, merchant_id=merchant_id)

    return {
        "batch_id": str(batch_id),
        "filename": file.filename,
        "status": current_status,
        "message": "Settlement file accepted. Check batch status for processing results.",
    }


@router.get("/settlements/{batch_id}/status")
async def get_settlement_status(
    batch_id: uuid.UUID,
    merchant_id: str = Depends(get_current_merchant),
):
    """Get settlement processing status"""
    pool = await _get_pool()
    row = await pool.fetchrow(
        "SELECT batch_id, filename, status, processed_count, "
        "failed_count, created_at, completed_at "
        "FROM settlement_files WHERE batch_id = $1 AND uploaded_by = $2",
        batch_id,
        merchant_id,
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
        "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
    }
