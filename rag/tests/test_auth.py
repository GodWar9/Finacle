from unittest.mock import AsyncMock

import httpx
import pytest
from app import main


@pytest.mark.asyncio
async def test_rag_identity_and_admin_boundaries(monkeypatch):
    monkeypatch.setenv(
        "RAG_API_KEYS", "tenant-a-key:merchant_a,tenant-b-key:merchant_b"
    )
    monkeypatch.setenv("RAG_ADMIN_KEY", "policy-admin-secret-at-least-32-characters")
    answer = AsyncMock(return_value={"answer": "Scoped response", "sources": []})
    monkeypatch.setattr(main, "answer_question", answer)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app), base_url="http://test"
    ) as client:
        assert (
            await client.post("/api/v1/rag/ask", json={"question": "hello"})
        ).status_code == 401
        assert (
            await client.post(
                "/api/v1/rag/ask",
                headers={"X-API-Key": "tenant-a-key"},
                json={"question": "hello"},
            )
        ).status_code == 200
        assert answer.await_args.args[1] == "merchant_a"
        assert (
            await client.post(
                "/api/v1/rag/ask",
                headers={"X-API-Key": "tenant-b-key"},
                json={"question": "hello"},
            )
        ).status_code == 200
        assert answer.await_args.args[1] == "merchant_b"
        policy = {
            "source_type": "POLICY_DOC",
            "source_ref": "policy",
            "content": "approved content",
        }
        assert (
            await client.post(
                "/api/v1/rag/ingest-policy",
                headers={"X-API-Key": "tenant-a-key"},
                json=policy,
            )
        ).status_code == 403
        policy["source_type"] = "TXN_NARRATIVE"
        assert (
            await client.post(
                "/api/v1/rag/ingest-policy",
                headers={"X-API-Key": "policy-admin-secret-at-least-32-characters"},
                json=policy,
            )
        ).status_code == 422
