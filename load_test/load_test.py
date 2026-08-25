import asyncio
import aiohttp
import uuid
import time
import statistics
import argparse
from typing import List, Dict
from dataclasses import dataclass

@dataclass
class LoadTestResult:
    total_requests: int
    successful: int
    failed: int
    errors: Dict[str, int]
    latencies_ms: List[float]
    duration_seconds: float
    requests_per_second: float

async def post_transaction(session: aiohttp.ClientSession, base_url: str, account_ids: List[str], idempotency_key: str = None) -> Dict:
    if idempotency_key is None:
        idempotency_key = str(uuid.uuid4())
    
    payload = {
        "transaction_type": "PAYMENT",
        "reference_id": f"order_{uuid.uuid4().hex[:8]}",
        "entries": [
            {"account_id": account_ids[0], "direction": "DEBIT", "amount_minor": 10000, "currency": "INR"},
            {"account_id": account_ids[1], "direction": "CREDIT", "amount_minor": 10000, "currency": "INR"}
        ],
        "narrative": "Load test transaction"
    }
    
    headers = {
        "Idempotency-Key": idempotency_key,
        "X-API-Key": "test-key:merchant_1",
        "Content-Type": "application/json"
    }
    
    start = time.perf_counter()
    try:
        async with session.post(f"{base_url}/api/v1/transactions", json=payload, headers=headers) as resp:
            await resp.text()
            latency = (time.perf_counter() - start) * 1000
            return {"status": resp.status, "latency_ms": latency, "idempotency_key": idempotency_key}
    except Exception as e:
        latency = (time.perf_counter() - start) * 1000
        return {"status": 0, "latency_ms": latency, "error": str(e), "idempotency_key": idempotency_key}

async def run_load_test(
    base_url: str,
    account_ids: List[str],
    num_requests: int,
    concurrency: int,
    reuse_keys: bool = False
) -> LoadTestResult:
    semaphore = asyncio.Semaphore(concurrency)
    latencies = []
    successful = 0
    failed = 0
    errors = {}
    
    idempotency_keys = [str(uuid.uuid4()) for _ in range(num_requests)] if not reuse_keys else [str(uuid.uuid4())] * num_requests
    
    async def bounded_post(i):
        nonlocal successful, failed
        async with semaphore:
            key = idempotency_keys[i] if reuse_keys else idempotency_keys[i]
            result = await post_transaction(session, base_url, account_ids, key)
            
            if result["status"] in (200, 201):
                successful += 1
                latencies.append(result["latency_ms"])
            else:
                failed += 1
                error_key = f"status_{result['status']}" if result["status"] != 0 else result.get("error", "connection_error")
                errors[error_key] = errors.get(error_key, 0) + 1
            
            return result
    
    connector = aiohttp.TCPConnector(limit=concurrency)
    timeout = aiohttp.ClientTimeout(total=30)
    
    start_time = time.perf_counter()
    
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        tasks = [bounded_post(i) for i in range(num_requests)]
        await asyncio.gather(*tasks)
    
    duration = time.perf_counter() - start_time
    
    return LoadTestResult(
        total_requests=num_requests,
        successful=successful,
        failed=failed,
        errors=errors,
        latencies_ms=latencies,
        duration_seconds=duration,
        requests_per_second=num_requests / duration if duration > 0 else 0
    )

def print_results(result: LoadTestResult):
    print(f"\n{'='*60}")
    print(f"LOAD TEST RESULTS")
    print(f"{'='*60}")
    print(f"Total Requests: {result.total_requests}")
    print(f"Successful: {result.successful}")
    print(f"Failed: {result.failed}")
    print(f"Duration: {result.duration_seconds:.2f}s")
    print(f"Throughput: {result.requests_per_second:.2f} req/s")
    
    if result.latencies_ms:
        latencies = sorted(result.latencies_ms)
        print(f"\nLatency (ms):")
        print(f"  Min: {min(latencies):.2f}")
        print(f"  Max: {max(latencies):.2f}")
        print(f"  Mean: {statistics.mean(latencies):.2f}")
        print(f"  Median: {statistics.median(latencies):.2f}")
        print(f"  P95: {latencies[int(len(latencies)*0.95)]:.2f}")
        print(f"  P99: {latencies[int(len(latencies)*0.99)]:.2f}")
    
    if result.errors:
        print(f"\nErrors:")
        for error, count in result.errors.items():
            print(f"  {error}: {count}")

async def test_idempotency_replay(base_url: str, account_ids: List[str]):
    print("\nTesting idempotency replay...")
    idempotency_key = str(uuid.uuid4())
    
    async with aiohttp.ClientSession() as session:
        for i in range(3):
            result = await post_transaction(session, base_url, account_ids, idempotency_key)
            print(f"  Attempt {i+1}: status={result['status']}, latency={result['latency_ms']:.2f}ms")
            assert result["status"] in (200, 201), f"Expected success, got {result['status']}"
    
    print("  Idempotency replay test PASSED")

async def test_concurrent_same_account(base_url: str, account_ids: List[str], num_concurrent: int):
    print(f"\nTesting {num_concurrent} concurrent transactions to same account...")
    
    async with aiohttp.ClientSession() as session:
        tasks = [
            post_transaction(session, base_url, account_ids, str(uuid.uuid4()))
            for _ in range(num_concurrent)
        ]
        results = await asyncio.gather(*tasks)
    
    successful = sum(1 for r in results if r["status"] in (200, 201))
    print(f"  Successful: {successful}/{num_concurrent}")
    assert successful == num_concurrent, "All concurrent transactions should succeed"
    print("  Concurrent same-account test PASSED")

async def main():
    parser = argparse.ArgumentParser(description="Ledger Core Load Test")
    parser.add_argument("--url", default="http://localhost:8000", help="Gateway base URL")
    parser.add_argument("--requests", type=int, default=1000, help="Number of requests")
    parser.add_argument("--concurrency", type=int, default=50, help="Concurrent requests")
    parser.add_argument("--reuse-keys", action="store_true", help="Reuse idempotency keys (test replay)")
    parser.add_argument("--account1", help="First account UUID")
    parser.add_argument("--account2", help="Second account UUID")
    args = parser.parse_args()
    
    if not args.account1 or not args.account2:
        print("Error: --account1 and --account2 are required")
        return
    
    account_ids = [args.account1, args.account2]
    
    print(f"Starting load test against {args.url}")
    print(f"Requests: {args.requests}, Concurrency: {args.concurrency}")
    
    result = await run_load_test(args.url, account_ids, args.requests, args.concurrency, args.reuse_keys)
    print_results(result)
    
    await test_idempotency_replay(args.url, account_ids)
    await test_concurrent_same_account(args.url, account_ids, 10)
    
    if result.failed > 0:
        print(f"\nWARNING: {result.failed} requests failed!")
        exit(1)

if __name__ == "__main__":
    asyncio.run(main())