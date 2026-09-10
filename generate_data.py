#!/usr/bin/env python3
"""
Finacle Live Data Generator — simulates realistic merchant activity.

Continuously posts transactions, uploads settlement files, and queries
the RAG copilot. Prints a live dashboard to the terminal.

Usage:
    python generate_data.py [--gateway URL] [--rag URL] [--rate N] [--duration SECS]

Requirements:
    pip install httpx rich
"""

import argparse
import random
import string
import sys
import time
import uuid
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional

import httpx

try:
    from rich.live import Live
    from rich.table import Table
    from rich.panel import Panel
    from rich.layout import Layout
    from rich.text import Text
    HAS_RICH = True
except ImportError:
    HAS_RICH = False


# ── Config ──────────────────────────────────────────────────────────────

MERCHANTS = ["merchant_1", "merchant_2", "merchant_3"]
API_KEY = "sk_test_demo"

# Realistic transaction narratives
NARRATIVES = [
    "Payment for order #{order_id}",
    "Refund for order #{order_id}",
    "Subscription renewal - {merchant}",
    "Settlement for batch #{batch_id}",
    "Fee for transaction processing",
    "Customer transfer to {account}",
    "Vendor payment - Invoice #{invoice_id}",
    "Cashback credited for order #{order_id}",
    "EMI payment received - {account}",
    "Merchant payout - {merchant}",
]

# Indian bank settlement file formats (simplified)
SETTLEMENT_FILE_TEMPLATES = [
    # ref(20) + amount(13) + currency(3) + status(7)
    lambda ref, amt: f"{ref[:20]:<20}{amt:>13}{'INR':<3}{'POSTED ':<7}",
    lambda ref, amt: f"{ref[:20]:<20}{amt:>13}{'INR':<3}{'SETTLED ':<7}",
]


@dataclass
class Stats:
    transactions_posted: int = 0
    transactions_failed: int = 0
    settlements_uploaded: int = 0
    recon_exceptions: int = 0
    rag_queries: int = 0
    balance_checks: int = 0
    accounts_created: int = 0
    start_time: float = field(default_factory=time.time)
    errors: list = field(default_factory=list)
    accounts: list = field(default_factory=list)


def random_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def random_amount() -> int:
    """Random amount in paise (₹1 to ₹50,000)"""
    weights = [
        (100, 1_000, 40),        # ₹1-10: common
        (1_000, 10_000, 35),     # ₹10-100: common
        (10_000, 100_000, 20),   # ₹100-1000: moderate
        (100_000, 5_000_000, 5), # ₹1000-50000: rare
    ]
    lo, hi, weight = random.choices(weights, weights=[w[2] for w in weights])[0]
    return random.randint(lo, hi)


def random_narrative() -> str:
    tpl = random.choice(NARRATIVES)
    return tpl.format(
        order_id=random.randint(10000, 99999),
        merchant=random.choice(MERCHANTS),
        account=f"ACC{random.randint(1000, 9999)}",
        batch_id=uuid.uuid4().hex[:8],
        invoice_id=random.randint(1000, 9999),
    )


class DataGenerator:
    def __init__(self, gateway: str, rag: str, rate: int, duration: int):
        self.gateway = gateway
        self.rag = rag
        self.rate = rate
        self.duration = duration
        self.stats = Stats()
        self.http = httpx.Client(timeout=10)
        self._seed_accounts()

    def _seed_accounts(self):
        """Create initial accounts for each merchant."""
        for merchant in MERCHANTS:
            for i in range(5):
                try:
                    r = self.http.post(
                        f"{self.gateway}/api/v1/accounts",
                        json={
                            "account_number": f"{merchant[:8]}-acc-{i:03d}",
                            "account_type": random.choice(["ASSET", "LIABILITY"]),
                            "owner_ref": merchant,
                            "currency": "INR",
                        },
                        headers={"X-Merchant-ID": merchant, "X-API-Key": API_KEY},
                    )
                    if r.status_code in (201, 409):
                        data = r.json() if r.status_code == 201 else {}
                        account_id = data.get("account_id", str(uuid.uuid4()))
                        self.stats.accounts.append(account_id)
                        self.stats.accounts_created += 1
                except Exception:
                    pass

        # Ensure at least some accounts exist
        if not self.stats.accounts:
            self.stats.accounts = [str(uuid.uuid4()) for _ in range(10)]

    def _pick_accounts(self) -> tuple:
        """Pick two random accounts for a balanced transaction."""
        if len(self.stats.accounts) < 2:
            return str(uuid.uuid4()), str(uuid.uuid4())
        a, b = random.sample(self.stats.accounts, 2)
        return a, b

    def post_transaction(self) -> bool:
        """Post a balanced double-entry transaction."""
        merchant = random.choice(MERCHANTS)
        account_a, account_b = self._pick_accounts()
        amount = random_amount()
        idem_key = str(uuid.uuid4())

        payload = {
            "transaction_type": random.choice(["PAYMENT", "TRANSFER", "REFUND"]),
            "reference_id": random_id("ref_"),
            "narrative": random_narrative(),
            "entries": [
                {"account_id": account_a, "direction": "DEBIT", "amount_minor": amount, "currency": "INR"},
                {"account_id": account_b, "direction": "CREDIT", "amount_minor": amount, "currency": "INR"},
            ],
        }

        try:
            r = self.http.post(
                f"{self.gateway}/api/v1/transactions",
                json=payload,
                headers={
                    "X-Merchant-ID": merchant,
                    "X-API-Key": API_KEY,
                    "Idempotency-Key": idem_key,
                },
            )
            if r.status_code == 201:
                self.stats.transactions_posted += 1
                return True
            else:
                self.stats.transactions_failed += 1
                self.stats.errors.append(f"POST {r.status_code}: {r.text[:100]}")
                return False
        except Exception as e:
            self.stats.transactions_failed += 1
            self.stats.errors.append(str(e)[:100])
            return False

    def upload_settlement(self) -> bool:
        """Upload a synthetic settlement file with deliberate mismatches."""
        merchant = random.choice(MERCHANTS)
        batch_id = str(uuid.uuid4())
        num_rows = random.randint(5, 20)
        mismatch_row = random.randint(0, num_rows - 1)

        lines = []
        for i in range(num_rows):
            ref = f"txn-{uuid.uuid4().hex[:16]}"
            if i == mismatch_row:
                # Deliberate mismatch
                amt = random_amount() + random.randint(100, 5000)
            else:
                amt = random_amount()
            tmpl = random.choice(SETTLEMENT_FILE_TEMPLATES)
            lines.append(tmpl(ref, amt))

        content = "\n".join(lines).encode()

        try:
            r = self.http.post(
                f"{self.gateway}/api/v1/settlements/upload",
                files={"file": ("settlement.txt", content, "text/plain")},
                data={"batch_id": batch_id},
                headers={"X-Merchant-ID": merchant, "X-API-Key": API_KEY},
            )
            if r.status_code == 202:
                self.stats.settlements_uploaded += 1
                return True
            return False
        except Exception:
            return False

    def query_rag(self) -> bool:
        """Ask the RAG copilot a question."""
        questions = [
            ("How are reconciliation exceptions resolved?", "compliance"),
            ("What is the fee policy for merchants?", "compliance"),
            ("Show recent transactions", "ops"),
            ("What happened with the last settlement?", "ops"),
            ("What are the reversal rules?", "compliance"),
        ]
        question, scope = random.choice(questions)

        try:
            r = self.http.post(
                f"{self.rag}/api/v1/rag/ask",
                json={"question": question, "scope": scope},
                headers={"X-API-Key": API_KEY},
            )
            if r.status_code == 200:
                self.stats.rag_queries += 1
                return True
            return False
        except Exception:
            return False

    def check_balance(self) -> bool:
        """Query a random account balance."""
        if not self.stats.accounts:
            return False
        account_id = random.choice(self.stats.accounts)
        merchant = random.choice(MERCHANTS)

        try:
            r = self.http.get(
                f"{self.gateway}/api/v1/accounts/{account_id}/balance",
                headers={"X-Merchant-ID": merchant, "X-API-Key": API_KEY},
            )
            if r.status_code == 200:
                self.stats.balance_checks += 1
                return True
            return False
        except Exception:
            return False

    def check_recon_exceptions(self):
        """Check reconciliation exceptions count."""
        try:
            r = self.http.get(
                f"{self.gateway}/api/v1/reconciliation/exceptions?limit=1",
                headers={"X-Merchant-ID": "demo-merchant", "X-API-Key": API_KEY},
            )
            if r.status_code == 200:
                data = r.json()
                self.stats.recon_exceptions = len(data.get("exceptions", []))
        except Exception:
            pass

    def _render_table(self) -> Table:
        """Build the stats table."""
        table = Table(title="Finacle Live Data", show_header=True, header_style="bold cyan")
        table.add_column("Metric", style="white")
        table.add_column("Count", style="green", justify="right")
        table.add_column("Rate", style="yellow", justify="right")

        elapsed = time.time() - self.stats.start_time
        rate = lambda n: f"{n / max(elapsed, 1):.1f}/s"

        table.add_row("Accounts created", str(self.stats.accounts_created), "-")
        table.add_row("Transactions posted", str(self.stats.transactions_posted), rate(self.stats.transactions_posted))
        table.add_row("Transactions failed", str(self.stats.transactions_failed), rate(self.stats.transactions_failed))
        table.add_row("Settlements uploaded", str(self.stats.settlements_uploaded), rate(self.stats.settlements_uploaded))
        table.add_row("Recon exceptions", str(self.stats.recon_exceptions), "-")
        table.add_row("RAG queries", str(self.stats.rag_queries), rate(self.stats.rag_queries))
        table.add_row("Balance checks", str(self.stats.balance_checks), rate(self.stats.balance_checks))
        table.add_row("Elapsed", f"{elapsed:.0f}s", "-")

        if self.stats.errors:
            table.add_row("", "", "")
            table.add_row("[red]Last error", self.stats.errors[-1][:60], "")

        return table

    def run(self):
        """Main loop — generate data at the configured rate."""
        interval = 1.0 / self.rate if self.rate > 0 else 1.0
        deadline = time.time() + self.duration
        settlement_counter = 0
        rag_counter = 0
        balance_counter = 0

        print(f"\nStarting data generator: {self.rate} txns/s for {self.duration}s")
        print(f"Gateway: {self.gateway}  RAG: {self.rag}")
        print(f"Accounts: {len(self.stats.accounts)}  Merchants: {len(MERCHANTS)}")
        print("Press Ctrl+C to stop\n")

        try:
            if HAS_RICH:
                with Live(self._render_table(), refresh_per_second=2) as live:
                    while time.time() < deadline:
                        t0 = time.time()

                        self.post_transaction()

                        # Periodic tasks
                        settlement_counter += 1
                        if settlement_counter >= 50:
                            self.upload_settlement()
                            self.check_recon_exceptions()
                            settlement_counter = 0

                        rag_counter += 1
                        if rag_counter >= 20:
                            self.query_rag()
                            rag_counter = 0

                        balance_counter += 1
                        if balance_counter >= 10:
                            self.check_balance()
                            balance_counter = 0

                        live.update(self._render_table())

                        elapsed = time.time() - t0
                        sleep_time = max(0, interval - elapsed)
                        if sleep_time > 0:
                            time.sleep(sleep_time)
            else:
                # Fallback without rich
                while time.time() < deadline:
                    t0 = time.time()
                    self.post_transaction()

                    settlement_counter += 1
                    if settlement_counter >= 50:
                        self.upload_settlement()
                        settlement_counter = 0

                    rag_counter += 1
                    if rag_counter >= 20:
                        self.query_rag()
                        rag_counter = 0

                    balance_counter += 1
                    if balance_counter >= 10:
                        self.check_balance()
                        balance_counter = 0

                    if int(time.time()) % 5 == 0:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] "
                              f"posted={self.stats.transactions_posted} "
                              f"failed={self.stats.transactions_failed} "
                              f"settlements={self.stats.settlements_uploaded} "
                              f"rag={self.stats.rag_queries}")

                    elapsed = time.time() - t0
                    sleep_time = max(0, interval - elapsed)
                    if sleep_time > 0:
                        time.sleep(sleep_time)

        except KeyboardInterrupt:
            pass

        # Final summary
        elapsed = time.time() - self.stats.start_time
        print(f"\n{'='*50}")
        print(f"  Generator stopped after {elapsed:.0f}s")
        print(f"  Transactions: {self.stats.transactions_posted} posted, {self.stats.transactions_failed} failed")
        print(f"  Settlements:  {self.stats.settlements_uploaded} uploaded")
        print(f"  RAG queries:  {self.stats.rag_queries}")
        print(f"  Balance checks: {self.stats.balance_checks}")
        print(f"  Throughput:   {self.stats.transactions_posted / max(elapsed, 1):.1f} txns/s")
        print(f"{'='*50}")


def main():
    parser = argparse.ArgumentParser(description="Finacle live data generator")
    parser.add_argument("--gateway", default="http://localhost:8000")
    parser.add_argument("--rag", default="http://localhost:8001")
    parser.add_argument("--rate", type=int, default=5, help="Transactions per second (default: 5)")
    parser.add_argument("--duration", type=int, default=300, help="Run duration in seconds (default: 300)")
    args = parser.parse_args()

    gen = DataGenerator(args.gateway, args.rag, args.rate, args.duration)
    gen.run()


if __name__ == "__main__":
    main()
