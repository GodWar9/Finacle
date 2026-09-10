#!/usr/bin/env python3
"""
Generate synthetic fixed-width files and benchmark the C++ recon engine.

Usage:
    python benchmark_recon.py [--rows N] [--exception-rate FLOAT] [--runs N]

Generates a ledger export and a matching bank settlement file, runs
recon_engine, and reports wall-clock time + throughput.
"""

import argparse
import os
import random
import string
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RECON_ENGINE = os.environ.get("RECON_ENGINE_PATH", "./recon_engine")

# Fixed-width layout (matches recon FixedWidthLayout defaults):
#   ref: 20 chars @ 0,  amt: 13 chars @ 20,  ccy: 3 chars @ 33,  sts: 7 chars @ 36
TOTAL_WIDTH = 43


def random_ref() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=20))


def write_line(f, ref: str, amount: int, currency: str = "INR", status: str = "POSTED"):
    amt_str = str(amount).rjust(13)
    ccy_str = currency.ljust(3)
    sts_str = status.ljust(7)
    f.write(f"{ref}{amt_str}{ccy_str}{sts_str}\n")


def generate_files(rows: int, exception_rate: float, tmpdir: str):
    """Generate ledger + bank files. Returns (ledger_path, bank_path, expected_exceptions)."""
    ledger_path = os.path.join(tmpdir, "ledger_export.txt")
    bank_path = os.path.join(tmpdir, "bank_file.txt")

    refs = [random_ref() for _ in range(rows)]
    amounts = [random.randint(100, 10_000_000) for _ in range(rows)]

    # Write ledger
    with open(ledger_path, "w") as f:
        for ref, amt in zip(refs, amounts):
            write_line(f, ref, amt)

    # Write bank file — mostly matching, with some exceptions injected
    expected_exceptions = 0
    with open(bank_path, "w") as f:
        for i, (ref, amt) in enumerate(zip(refs, amounts)):
            r = random.random()
            if r < exception_rate:
                # Pick an exception type
                exc_type = random.choice(["missing", "mismatch", "extra"])
                if exc_type == "missing":
                    # Skip this row → MISSING_IN_BANK_FILE
                    expected_exceptions += 1
                    continue
                elif exc_type == "mismatch":
                    # Alter the amount → AMOUNT_MISMATCH
                    bank_amt = amt + random.randint(-500, 500)
                    if bank_amt <= 0:
                        bank_amt = amt + abs(bank_amt) + 1
                    write_line(f, ref, bank_amt)
                    expected_exceptions += 1
                else:
                    # Extra row in bank → MISSING_IN_LEDGER
                    write_line(f, random_ref(), amt)
                    expected_exceptions += 1
            else:
                write_line(f, ref, amt)

    return ledger_path, bank_path, expected_exceptions


def run_recon(ledger_path: str, bank_path: str, batch_id: str, tmpdir: str) -> dict:
    """Run recon_engine and return timing + output stats."""
    cmd = [
        RECON_ENGINE,
        f"--ledger-export={ledger_path}",
        f"--bank-file={bank_path}",
        f"--batch-id={batch_id}",
    ]

    env = os.environ.copy()
    # Use a temp database URL or the real one if set
    if "DATABASE_URL" not in env:
        env["DATABASE_URL"] = "postgresql://ledger_app:dev_only@localhost:5432/ledger"

    t0 = time.perf_counter()
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=600)
    elapsed = time.perf_counter() - t0

    return {
        "elapsed": elapsed,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def main():
    parser = argparse.ArgumentParser(description="Benchmark the C++ recon engine")
    parser.add_argument("--rows", type=int, default=1_000_000, help="Number of ledger rows (default: 1M)")
    parser.add_argument("--exception-rate", type=float, default=0.02, help="Fraction of rows with exceptions (default: 0.02)")
    parser.add_argument("--runs", type=int, default=3, help="Number of runs to average (default: 3)")
    args = parser.parse_args()

    print(f"Recon engine benchmark: {args.rows:,} rows, {args.exception_rate:.1%} exception rate, {args.runs} runs")
    print(f"Engine: {RECON_ENGINE}")
    print("-" * 60)

    times = []
    for i in range(args.runs):
        batch_id = f"bench-{i:03d}"
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path, bank_path, expected_exc = generate_files(args.rows, args.exception_rate, tmpdir)
            stats = run_recon(ledger_path, bank_path, batch_id, tmpdir)

            times.append(stats["elapsed"])
            throughput = args.rows / stats["elapsed"] if stats["elapsed"] > 0 else 0

            print(f"  Run {i+1}: {stats['elapsed']:.2f}s  ({throughput:,.0f} rows/s)  rc={stats['returncode']}")
            if stats["stderr"]:
                for line in stats["stderr"].split("\n")[:3]:
                    print(f"    stderr: {line}")

    avg = sum(times) / len(times)
    p50 = sorted(times)[len(times) // 2]
    print("-" * 60)
    print(f"  Average: {avg:.2f}s  ({args.rows / avg:,.0f} rows/s)")
    print(f"  Median:  {p50:.2f}s  ({args.rows / p50:,.0f} rows/s)")
    print(f"  Min:     {min(times):.2f}s  Max: {max(times):.2f}s")


if __name__ == "__main__":
    main()
