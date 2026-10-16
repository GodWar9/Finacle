"""Same-origin operator portal. Secrets never enter browser JavaScript."""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

router = APIRouter(prefix="/portal", tags=["portal"])
COOKIE = "finacle_session"
_failures = 0
_reset_at = 0.0


def config():
    password = os.getenv("PORTAL_PASSWORD", "")
    secret = os.getenv("PORTAL_SESSION_SECRET", "")
    merchant = os.getenv("PORTAL_MERCHANT_ID", "")
    origin = os.getenv("PORTAL_ORIGIN", "http://127.0.0.1:8000")
    url = urlsplit(origin)
    if (
        len(password) < 16
        or len(secret) < 32
        or not merchant
        or len(merchant) > 200
        or url.path
        or url.query
        or url.fragment
        or url.username
        or url.password
        or url.scheme not in ("http", "https")
        or (url.scheme == "http" and url.hostname not in ("localhost", "127.0.0.1", "::1"))
    ):
        raise HTTPException(503, "Portal authentication needs configuration")
    return password, secret, merchant, origin


def same_origin(request: Request):
    origin = config()[3]
    if request.headers.get("origin") != origin or request.headers.get("sec-fetch-site") in ("cross-site", "same-site"):
        raise HTTPException(403, "Origin verification failed")


def session_merchant(request: Request):
    token = request.cookies.get(COOKIE, "")
    if not token or len(token) > 1024:
        return None
    try:
        _, secret, merchant, _ = config()
        payload, signature = token.rsplit(".", 1)
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload))
        if data["merchant"] != merchant or not time.time() < data["expires"] <= time.time() + 28800:
            return None
        return merchant
    except (ValueError, KeyError, TypeError, HTTPException):
        return None


class LoginRequest(BaseModel):
    password: str = Field(max_length=1024)


@router.post("/login")
async def login(body: LoginRequest, request: Request):
    global _failures, _reset_at
    password, secret, merchant, origin = config()
    same_origin(request)
    if time.monotonic() >= _reset_at:
        _failures, _reset_at = 0, time.monotonic() + 900
    if _failures >= 10:
        raise HTTPException(429, "Too many attempts; try again in 15 minutes")
    if not hmac.compare_digest(
        hashlib.sha256(body.password.encode()).digest(), hashlib.sha256(password.encode()).digest()
    ):
        _failures += 1
        raise HTTPException(401, "Invalid password")
    _failures = 0
    payload = base64.urlsafe_b64encode(
        json.dumps({"merchant": merchant, "expires": int(time.time()) + 28800, "nonce": secrets.token_hex(16)}).encode()
    ).decode()
    token = payload + "." + hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    response = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
    response.set_cookie(
        COOKIE, token, max_age=28800, httponly=True, secure=origin.startswith("https:"), samesite="strict"
    )
    return response


@router.post("/logout")
async def logout(request: Request):
    same_origin(request)
    response = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
    response.delete_cookie(COOKIE, httponly=True, samesite="strict", secure=config()[3].startswith("https:"))
    return response


@router.get("/session")
async def session(request: Request):
    configured = True
    try:
        config()
    except HTTPException:
        configured = False
    return JSONResponse(
        {"authenticated": bool(session_merchant(request)), "configured": configured},
        headers={"Cache-Control": "no-store"},
    )


async def copilot_proxy(body: dict, request: Request):
    from app.auth import get_current_merchant

    merchant = await get_current_merchant(request)
    key = os.getenv("RAG_SERVICE_KEY", "")
    if not key:
        raise HTTPException(503, "Copilot connection is not configured")
    # Each deployment's service key must map to this operator's merchant in RAG_API_KEYS.
    if merchant != os.getenv("PORTAL_MERCHANT_ID"):
        raise HTTPException(403, "Copilot is unavailable for this merchant")
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            response = await client.post(
                os.getenv("RAG_INTERNAL_URL", "http://127.0.0.1:8001").rstrip("/") + "/api/v1/rag/ask",
                json=body,
                headers={"X-API-Key": key},
            )
        if not response.is_success:
            raise HTTPException(503, "Copilot is unavailable")
        return response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, "Copilot is unavailable")
