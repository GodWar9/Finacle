# 06 — Consistency & Concurrency Model (reference doc — read before building)

No single agent owns this file's code, but every agent must understand it — it explains *why* doc 01/02/03/04 are shaped the way they are.

---

## 1. Where strong consistency is non-negotiable

**Inside the ledger core, on a single account's balance, at the moment of posting.** This is the one place where "eventually correct" is not acceptable — a double-spend or a lost update here is a real financial bug, not a UX inconvenience.

Mechanism: `SERIALIZABLE` isolation + row lock on `account_balances`, inside one Postgres transaction, per doc 01 §6. Two concurrent postings to the same account either interleave safely or one aborts and retries — never both partially apply.

## 2. Where eventual consistency is not just acceptable but *correct design*

Everything reading from Kafka: reporting, the RAG index, dashboards, the reconciliation engine's *ledger export* (it reads a recent-enough snapshot, not a live lock on the ledger). Forcing these to be strongly consistent with the ledger would mean every dashboard read pays the cost of the ledger's write lock — that's how you turn a fast payments system into a slow one for no correctness benefit, since nobody's *balance* correctness depends on the dashboard being real-time to the millisecond.

The trade-off being made explicitly: **bounded staleness (seconds, via Kafka lag) in exchange for read-path scalability**, everywhere except the one place (account balance at write time) where staleness would be a bug rather than a UX detail.

## 3. Idempotency key design — the full reasoning, not just the code

Three properties an idempotency layer must have, and why each matters:

1. **Same key + same body → replay the cached response, don't reprocess.** This is what makes network-level retries (timeout, load balancer retry, mobile client retry-on-reconnect) safe. Without this, a client that times out waiting for a response — even though the server actually succeeded — has no safe way to know whether to retry.
2. **Same key + different body → reject with 409.** This catches a *client bug* (reusing a UUID) before it causes a wrong transaction to be silently accepted under a key that "looks like" a retry. Skipping this check is a common shortcut that turns idempotency into a false sense of safety.
3. **Concurrent requests with the same key → one proceeds, the other waits/rejects, neither double-posts.** This is the part people forget: two retries can arrive *at the same time* (e.g., client retry racing the original request's slow response), not just sequentially. The short-lived Redis lock in doc 03 §3 exists specifically for this case.

## 4. Why reconciliation is *not* just "trust the ledger"

A tempting shortcut: since the ledger enforces balance invariants, why re-derive truth externally at all? Because the ledger can be **internally consistent and still externally wrong** — e.g., a payment gateway webhook that never arrived means the ledger never recorded a transaction the bank actually settled. Internal consistency (debits==credits) says nothing about whether the ledger's *view of the world* matches reality. That's what the three-way match in doc 02 is for — it's an intentionally adversarial process that doesn't assume the ledger is complete, only that it's internally coherent.

## 5. Failure modes worth being able to discuss

- **Rust core crashes mid-transaction:** Postgres transaction rolls back atomically — no partial ledger entries ever visible. Nothing to recover; the client's retry (same idempotency key) is handled safely on restart.
- **Outbox poller falls behind or crashes:** ledger writes are unaffected (they don't depend on the poller); events are simply delayed, not lost — `outbox_events.published=false` rows are replayed on poller restart. This is the whole point of the outbox pattern over a direct dual-write.
- **Redis goes down:** idempotency checks fall through to the Postgres `idempotency_records` table (doc 03 §3) — slower, but correct. This is why the durable fallback exists instead of treating Redis as a single point of failure for correctness.
- **Reconciliation finds a mismatch:** never auto-corrected. Written to `reconciliation_exceptions`, surfaced to ops/compliance (optionally via the RAG copilot), resolved by an explicit, audited follow-up transaction (a reversal + correction, never an edit).
