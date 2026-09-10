from fastapi import APIRouter, Request, HTTPException, status, Header, Depends
import structlog
import hmac
import hashlib

from app.grpc_client import get_grpc_client
import ledger_pb2
from app.auth import verify_webhook_signature, get_current_merchant
from app.config import get_settings
from uuid import UUID

logger = structlog.get_logger()
router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])
settings = get_settings()

@router.post("/payment-gateway")
async def payment_gateway_webhook(
    request: Request,
    x_signature: str = Header(..., alias="X-Signature"),
    merchant_id: str = Depends(get_current_merchant)
):
    payload = await request.body()
    
    webhook_secret = settings.webhook_secret
    if not verify_webhook_signature(payload, x_signature, webhook_secret):
        logger.warning("webhook_invalid_signature", merchant_id=merchant_id)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    try:
        import json
        data = json.loads(payload)
    except json.JSONDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON")

    event_type = data.get("event_type")
    if event_type != "payment.captured":
        logger.info("webhook_ignored_event", event_type=event_type, merchant_id=merchant_id)
        return {"status": "ignored"}
    
    payment_id = data.get("payment_id")
    amount_minor = data.get("amount_minor")
    currency = data.get("currency", "INR")
    account_id = data.get("account_id")
    reference_id = data.get("reference_id")
    
    if not all([payment_id, amount_minor, account_id]):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing required fields")
    
    client = await get_grpc_client()
    
    idempotency_key = f"webhook_{payment_id}"
    
    grpc_req = ledger_pb2.PostTransactionRequest(
        idempotency_key=idempotency_key,
        transaction_type="PAYMENT",
        reference_id=reference_id or payment_id,
        entries=[
            ledger_pb2.LedgerEntry(
                account_id=account_id,
                direction="DEBIT",
                amount_minor=amount_minor,
                currency=currency,
            ),
            ledger_pb2.LedgerEntry(
                account_id="00000000-0000-0000-0000-000000000001",
                direction="CREDIT",
                amount_minor=amount_minor,
                currency=currency,
            ),
        ],
        narrative=f"Payment captured via webhook: {payment_id}",
    )
    
    try:
        resp = await client.post_transaction(grpc_req)
        logger.info("webhook_payment_posted", transaction_id=resp.transaction_id, payment_id=payment_id)
        return {"status": "posted", "transaction_id": resp.transaction_id}
    except Exception as e:
        logger.error("webhook_payment_failed", error=str(e), payment_id=payment_id)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Failed to post transaction: {e}")

@router.post("/reconciliation")
async def reconciliation_webhook(
    request: Request,
    x_signature: str = Header(..., alias="X-Signature"),
    merchant_id: str = Depends(get_current_merchant)
):
    payload = await request.body()

    webhook_secret = settings.webhook_secret
    if not verify_webhook_signature(payload, x_signature, webhook_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    try:
        import json
        data = json.loads(payload)
    except json.JSONDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON")
    
    batch_id = data.get("batch_id")
    exception_count = data.get("exception_count", 0)
    
    logger.info("reconciliation_webhook_received", batch_id=batch_id, exception_count=exception_count, merchant_id=merchant_id)
    
    return {"status": "received", "batch_id": batch_id}