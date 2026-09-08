#!/usr/bin/env python3
"""Generate a bank settlement file (fixed-width) from the live ledger.

The upstream fixed-width layout is defined by recon::FixedWidthLayout
(ref 20 | amount 13 | currency 3 | status 7). Ledger exports are produced the
same way, so matching a freshly generated bank file against the ledger should
produce no exceptions for the included references.

Usage:
    python make_bank_fixture.py [--db postgresql://...] [--out bank.txt] [--orphan 12000]

--orphan <amount> optionally appends one extra line whose reference has no
ledger counterpart, to demo a MISSING_IN_LEDGER exception.
"""
import argparse
import asyncio
import os

import asyncpg

FIXED_WIDTH = dict(ref_start=0, amt_start=20, ccy_start=33, sts_start=36)


def line(ref: str, amount_minor: int, currency: str, status: str) -> str:
    ref = str(ref)[:20].ljust(20)
    amt = str(amount_minor).rjust(13)
    ccy = currency[:3].ljust(3)
    sts = status[:7].ljust(7)
    return f"{ref}{amt}{ccy}{sts}"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--out", default="bank.txt")
    parser.add_argument("--orphan", type=int, default=None)
    args = parser.parse_args()

    conn = await asyncpg.connect(args.db)
    try:
        rows = await conn.fetch(
            """
            SELECT t.transaction_id, t.transaction_type, t.status,
                   le.amount_minor, le.currency
            FROM transactions t
            JOIN ledger_entries le ON le.transaction_id = t.transaction_id
            WHERE t.status = 'POSTED'
            ORDER BY t.created_at
            """
        )
    finally:
        await conn.close()

    seen = set()
    with open(args.out, "w", newline="\n", encoding="ascii") as f:
        for r in rows:
            ref = r["transaction_id"]
            if ref in seen:
                continue
            seen.add(ref)
            f.write(line(ref, r["amount_minor"], r["currency"], r["status"]) + "\n")

        if args.orphan is not None:
            f.write(line(f"ORPHAN-{len(seen):012d}", args.orphan, "INR", "POSTED") + "\n")

    print(f"wrote {len(seen) + (1 if args.orphan is not None else 0)} lines to {args.out}")


if __name__ == "__main__":
    asyncio.run(main())