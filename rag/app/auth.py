import hmac
import os

from fastapi import Header, HTTPException


async def merchant_identity(x_api_key: str = Header(default="")) -> str:
    for pair in os.getenv("RAG_API_KEYS", "").split(","):
        key, separator, merchant = pair.partition(":")
        if (
            separator
            and key.strip()
            and merchant.strip()
            and hmac.compare_digest(x_api_key, key.strip())
        ):
            return merchant.strip()
    raise HTTPException(401, "Valid RAG API key required")


async def policy_admin(x_api_key: str = Header(default="")):
    expected = os.getenv("RAG_ADMIN_KEY", "")
    if len(expected) < 32 or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(403, "Policy administrator credentials required")
