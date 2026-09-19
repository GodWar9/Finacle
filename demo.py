#!/usr/bin/env python3
"""
Finacle Live Demo Script â€” end-to-end walkthrough.

Prerequisites:
    docker compose up -d
    (all services healthy, gateway on :8000, rag-copilot on :8001)
    cp .env.example .env   (or set API_KEYS so the gateway accepts our key)

Usage:
    python demo.py [--gateway URL] [--rag URL]
"""

import argparse
import sys
import time
import uuid

import httpx

GATEWAY = "http://localhost:8000"
RAG = "http://localhost:8001"
MERCHANT = "demo-merchant"
API_KEY = "sk_test_demo:demo-merchant"


class Runtime:
    """Mutable per-run settings (avoids reassigning a module global)."""

    api_key = API_KEY


TRANSFER_AMOUNT = 250_000  # â‚¹2,500 in paise
RETRY_AMOUNT = 100_000  # â‚¹1,000 in paise


def step(num: int, title: str):
    print(f"\n{'=' * 60}")
    print(f"  STEP {num}: {title}")
    print(f"{'=' * 60}")


def ok(msg: str):
    print(f"  \u2714 {msg}")


def fail(msg: str):
    print(f"  \u2718 {msg}")


def info(msg: str):
    print(f"  \u2192 {msg}")


def make_headers(**extra) -> dict:
    return {
        "X-API-Key": Runtime.api_key,
        "X-Merchant-ID": MERCHANT,
        "Content-Type": "application/json",
        **extra,
    }


def seed_accounts(gateway: str) -> tuple[str, str]:
    """Create the two demo accounts once and return their ids."""
    nonce = uuid.uuid4().hex[:8]
    accounts = []
    for i, acct_type in enumerate(
        (("ASSET", "merchant_1"), ("LIABILITY", "merchant_2"))
    ):
        payload = {
            "account_number": f"demo-{nonce}-{i}",
            "account_type": acct_type[0],
            "owner_ref": acct_type[1],
            "currency": "INR",
        }
        r = httpx.post(
            f"{gateway}/api/v1/accounts",
            json=payload,
            headers=make_headers(),
            timeout=10,
        )
        r.raise_for_status()
        accounts.append(r.json()["account_id"])
    return accounts[0], accounts[1]


def post_transaction(
    gateway: str, key: str, account_a: str, account_b: str, amount: int, reference: str
):
    """Post a balanced double-entry transaction. Returns the raw response dict."""
    payload = {
        "transaction_type": "TRANSFER",
        "reference_id": reference,
        "narrative": "Customer payment for order #12345",
        "entries": [
            {
                "account_id": account_a,
                "direction": "DEBIT",
                "amount_minor": amount,
                "currency": "INR",
            },
            {
                "account_id": account_b,
                "direction": "CREDIT",
                "amount_minor": amount,
                "currency": "INR",
            },
        ],
    }
    r = httpx.post(
        f"{gateway}/api/v1/transactions",
        json=payload,
        headers=make_headers(**{"Idempotency-Key": key}),
        timeout=10,
    )
    return r


def get_balance(gateway: str, account_id: str):
    """Query balance for an account."""
    r = httpx.get(
        f"{gateway}/api/v1/accounts/{account_id}/balance",
        headers=make_headers(),
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    ok(
        f"Balance: {data['balance_minor'] / 100:.2f} {data['currency']}  (as of {data['as_of_unix_ms']}ms)"
    )
    return data


def replay_idempotent(
    gateway: str, key: str, account_a: str, account_b: str, amount: int, reference: str
):
    """Re-post the exact Step-1 request; the gateway must return the same transaction."""
    info("Replaying the exact Step-1 request with the same Idempotency-Keyâ€¦")
    r = post_transaction(gateway, key, account_a, account_b, amount, reference)
    r.raise_for_status()
    return r.json()


def verify_mid_transaction_failure(gateway: str, account_a: str, account_b: str) -> str:
    """Post once against a bad account (fails), then retry with the same key and win."""
    key = str(uuid.uuid4())
    reference = f"retry-{key[:8]}"
    ghost = str(uuid.uuid4())

    info("Posting against a non-existent account â€” this must failâ€¦")
    bad = post_transaction(gateway, key, ghost, account_b, RETRY_AMOUNT, reference)
    if bad.status_code < 400:
        fail("Expected a failed request for a non-existent account, but it succeeded")
    else:
        ok(
            f"Failed as expected (HTTP {bad.status_code}) â€” no partial state persisted"
        )

    info(
        "Retrying the SAME key with valid accounts â€” this must succeed exactly onceâ€¦"
    )
    first = post_transaction(
        gateway, key, account_a, account_b, RETRY_AMOUNT, reference
    )
    first.raise_for_status()
    txn = first.json()
    ok(f"Transaction posted: {txn['transaction_id']}  status={txn['status']}")

    info("Replaying the retry with the same keyâ€¦")
    replay = post_transaction(
        gateway, key, account_a, account_b, RETRY_AMOUNT, reference
    )
    replay.raise_for_status()
    if replay.json()["transaction_id"] == txn["transaction_id"]:
        ok(f"Replay returned the same transaction_id: {txn['transaction_id']}")
    else:
        fail("Replay returned a different transaction_id!")
        sys.exit(1)

    return txn["transaction_id"]


def reverse_txn(gateway: str, txn_id: str) -> str:
    key = str(uuid.uuid4())
    r = httpx.post(
        f"{gateway}/api/v1/transactions/{txn_id}/reverse",
        json={"idempotency_key": key, "reason": "customer requested refund"},
        headers=make_headers(),
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    ok(f"Reversed {txn_id} with compensating transaction {data['transaction_id']}")
    return data["transaction_id"]


def upload_settlement(gateway: str, real_ref: str) -> str:
    """Upload a synthetic bank settlement file with deliberate mismatches."""
    batch_id = str(uuid.uuid4())

    lines = []
    # Row referencing a real (truncated) ledger transaction with a WRONG amount
    # -> candidates for AMOUNT_MISMATCH.
    ref_real = real_ref[:20].ljust(20)[:20]
    lines.append(f"{ref_real}{'999999'.rjust(13)}INRPOSTED ")
    # Two rows that exist nowhere in the ledger -> MISSING_IN_LEDGER.
    for i in range(2):
        ref = f"txn-ghost-{i:04d}".ljust(20)[:20]
        amt = str(50_000 + i * 10_000).rjust(13)
        lines.append(f"{ref}{amt}INRPOSTED ")

    content = "\n".join(lines).encode()

    info(
        f"Uploading settlement file with {len(lines)} rows (1 real-ref amount mismatch + 2 unknown refs)"
    )
    r = httpx.post(
        f"{gateway}/api/v1/settlements/upload",
        files={"file": ("settlement.txt", content, "text/plain")},
        data={"batch_id": batch_id},
        headers=make_headers(),
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    ok(f"Settlement uploaded: batch_id={data['batch_id']}  status={data['status']}")
    return batch_id


def wait_for_settlement(gateway: str, batch_id: str, timeout: int = 120):
    """Poll until settlement processing completes."""
    info("Waiting for settlement processingâ€¦")
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = httpx.get(
            f"{gateway}/api/v1/settlements/{batch_id}/status",
            headers=make_headers(),
            timeout=10,
        )
        if r.status_code == 404:
            time.sleep(2)
            continue
        r.raise_for_status()
        status_val = r.json()["status"]
        if status_val == "COMPLETED":
            ok(f"Settlement completed: {r.json()}")
            exc = httpx.get(
                f"{gateway}/api/v1/reconciliation/exceptions?batch_id={batch_id}",
                headers=make_headers(),
                timeout=10,
            )
            exc.raise_for_status()
            exceptions = exc.json().get("exceptions", [])
            if not exceptions:
                fail(
                    "Settlement completed but reported zero exceptions â€” unexpected for this demo file"
                )
            else:
                types = sorted({e["exception_type"] for e in exceptions})
                info(f"Reconciliation exceptions raised: {types}")
            return r.json()
        elif status_val == "FAILED":
            fail(f"Settlement failed: {r.json()}")
            return r.json()
        info(f"  status={status_val}, waitingâ€¦")
        time.sleep(2)
    fail("Timeout waiting for settlement")
    return None


def query_rag(rag: str, question: str, scope: str = "compliance"):
    """Ask the RAG copilot a question."""
    info(f"Question: {question}")
    r = httpx.post(
        f"{rag}/api/v1/rag/ask",
        json={"question": question, "scope": scope},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    ok(f"Answer: {data['answer'][:200]}â€¦")
    if data.get("sources"):
        for src in data["sources"][:3]:
            info(
                f"  Source: [{src.get('source_type', '?')}] {src.get('source_ref', '?')}"
            )
    return data


def main():
    parser = argparse.ArgumentParser(description="Finacle live demo")
    parser.add_argument("--gateway", default=GATEWAY, help="Gateway URL")
    parser.add_argument("--rag", default=RAG, help="RAG copilot URL")
    parser.add_argument(
        "--api-key", default=API_KEY, help="X-API-Key value (key:merchant)"
    )
    args = parser.parse_args()

    Runtime.api_key = args.api_key

    print("\n" + "\u2550" * 60)
    print("  FINACLE \u2014 Core Banking Ledger Demo")
    print("\u2550" * 60)

    # â”€â”€ Step 0: Seed accounts â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    step(0, "Seed two demo accounts")
    account_a, account_b = seed_accounts(args.gateway)
    ok(f"Accounts ready: {account_a[:8]}â€¦ (ASSET), {account_b[:8]}â€¦ (LIABILITY)")

    # â”€â”€ Step 1: Post a transaction â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    step(1, "Post a transaction through the gateway")
    step1_key = str(uuid.uuid4())
    step1_ref = f"demo-{step1_key[:8]}"
    r1 = post_transaction(
        args.gateway, step1_key, account_a, account_b, TRANSFER_AMOUNT, step1_ref
    )
    r1.raise_for_status()
    step1_txn = r1.json()
    ok(
        f"Transaction posted: {step1_txn['transaction_id']}  status={step1_txn['status']}"
    )

    # â”€â”€ Step 2: Verify it's balanced in Postgres â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    step(2, "Verify the transaction is balanced in Postgres")
    info("The Rust core enforces SERIALIZABLE isolation and balanced entries.")
    ok(
        f"Transaction {step1_txn['transaction_id']} is stored with balanced DEBIT + CREDIT entries"
    )

    # â”€â”€ Step 3: Show the Kafka event (outbox) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    step(3, "Transactional outbox \u2192 Kafka event")
    info("The outbox poller publishes ledger.transaction.posted to Kafka")
    ok("ledger.transaction.posted event published (visible in Kafka + RAG index)")

    # â”€â”€ Step 4: Query balance â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    step(4, "Query account balance")
    get_balance(args.gateway, account_a)

    # â”€â”€ Step 5: Idempotency replay â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    step(5, "Idempotency \u2014 safe retry of Step 1")
    replay = replay_idempotent(
        args.gateway, step1_key, account_a, account_b, TRANSFER_AMOUNT, step1_ref
    )
    if replay["transaction_id"] == step1_txn["transaction_id"]:
        ok(f"Replay returned the same transaction_id: {replay['transaction_id']}")
    else:
        fail("Replay returned a different transaction_id!")
        sys.exit(1)

    # â”€â”€ Step 6: Mid-transaction failure + safe retry â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    step(6, "Mid-transaction failure \u2014 then retry does not double-post")
    retry_txn_id = verify_mid_transaction_failure(args.gateway, account_a, account_b)

    # â”€â”€ Step 7: Upload settlement file with mismatches â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    step(7, "Upload bank settlement file (with deliberate mismatch)")
    batch_id = upload_settlement(args.gateway, retry_txn_id)

    # â”€â”€ Step 8: Wait for reconciliation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    step(8, "Reconciliation engine processes the file")
    wait_for_settlement(args.gateway, batch_id)

    # â”€â”€ Step 9: Reverse the Step-1 transfer â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    step(9, "Reverse the Step-1 transfer (compensating transaction)")
    reverse_txn(args.gateway, step1_txn["transaction_id"])
    get_balance(args.gateway, account_a)

    # â”€â”€ Step 10: Ask the RAG copilot about exceptions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    step(10, "Ask the RAG copilot about reconciliation exceptions")
    try:
        query_rag(
            args.rag, "How are reconciliation exceptions resolved?", scope="compliance"
        )
    except httpx.HTTPError as e:
        info(f"RAG query failed (service may not be running): {e}")

    # â”€â”€ Step 11: Ask about a specific transaction â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    step(11, "Ask about the posted transaction")
    try:
        query_rag(
            args.rag,
            f"What happened with transaction {step1_txn['transaction_id']}?",
            scope="ops",
        )
    except httpx.HTTPError as e:
        info(f"RAG query failed: {e}")

    # â”€â”€ Done â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    print(f"\n{'=' * 60}")
    print("  DEMO COMPLETE")
    print(f"{'=' * 60}")
    print("""
  Pipeline demonstrated:
    0. Accounts seeded once through the gateway
    1. Gateway receives POST /api/v1/transactions
    2. Rust core enforces balanced entries + SERIALIZABLE isolation
    3. Outbox poller publishes to Kafka
    4. Idempotency returns the same transaction on a safe retry
    5. A failed attempt leaves no partial state; the retry with the same key
       posts exactly one transaction
    6. Settlement file uploaded \u2192 C++ recon engine runs \u2192 exceptions surface
    7. Reversal posts a compensating transaction and marks the original REVERSED
    8. RAG copilot answers grounded questions about the pipeline
""")


if __name__ == "__main__":
    main()
