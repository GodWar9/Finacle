"""Run isolated real-service acceptance checks; never targets an existing deployment."""

import argparse
import json
import os
import secrets
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def wait_for(check, label, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except (OSError, URLError, subprocess.CalledProcessError):
            pass
        time.sleep(2)
    raise RuntimeError("Timed out: " + label)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate Compose configuration without starting services",
    )
    args = parser.parse_args()
    project = "finacle-smoke-" + secrets.token_hex(6)
    key_a, key_b, token = (secrets.token_hex(24) for _ in range(3))
    db_password = secrets.token_hex(16)
    database = f"postgresql://ledger_app:{db_password}@postgres:5432/ledger_test"
    health = {"interval": "3s", "timeout": "3s", "retries": 40}
    config = {
        "services": {
            "postgres": {
                "image": "pgvector/pgvector:pg16",
                "environment": {
                    "POSTGRES_DB": "ledger_test",
                    "POSTGRES_USER": "ledger_app",
                    "POSTGRES_PASSWORD": db_password,
                },
                "volumes": ["database:/var/lib/postgresql/data"],
                "healthcheck": dict(
                    health,
                    test=["CMD-SHELL", "pg_isready -U ledger_app -d ledger_test"],
                ),
            },
            "redis": {
                "image": "redis:7",
                "healthcheck": dict(health, test=["CMD", "redis-cli", "ping"]),
            },
            "zookeeper": {
                "image": "confluentinc/cp-zookeeper:7.6.0",
                "environment": {"ZOOKEEPER_CLIENT_PORT": "2181"},
            },
            "kafka": {
                "image": "confluentinc/cp-kafka:7.6.0",
                "depends_on": ["zookeeper"],
                "environment": {
                    "KAFKA_BROKER_ID": "1",
                    "KAFKA_ZOOKEEPER_CONNECT": "zookeeper:2181",
                    "KAFKA_ADVERTISED_LISTENERS": "PLAINTEXT://kafka:9092",
                    "KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR": "1",
                    "KAFKA_TRANSACTION_STATE_LOG_MIN_ISR": "1",
                    "KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR": "1",
                },
                "healthcheck": dict(
                    health,
                    timeout="10s",
                    test=[
                        "CMD-SHELL",
                        "kafka-topics --bootstrap-server localhost:9092 --list",
                    ],
                ),
            },
            "ledger-core": {
                "build": {"context": str(ROOT), "dockerfile": "core/Dockerfile"},
                "depends_on": {
                    "postgres": {"condition": "service_healthy"},
                    "kafka": {"condition": "service_healthy"},
                },
                "environment": {
                    "DATABASE_URL": database,
                    "KAFKA_BROKERS": "kafka:9092",
                    "LEDGER_SERVICE_TOKEN": token,
                },
            },
            "gateway": {
                "build": {"context": str(ROOT), "dockerfile": "gateway/Dockerfile"},
                "ports": ["127.0.0.1::8000"],
                "depends_on": {
                    "postgres": {"condition": "service_healthy"},
                    "redis": {"condition": "service_healthy"},
                    "ledger-core": {"condition": "service_started"},
                },
                "environment": {
                    "DATABASE_URL": database,
                    "REDIS_URL": "redis://redis:6379",
                    "LEDGER_CORE_ADDR": "ledger-core:50051",
                    "LEDGER_SERVICE_TOKEN": token,
                    "API_KEYS": f"{key_a}:merchant_a,{key_b}:merchant_b",
                },
            },
        },
        "volumes": {"database": {}},
    }
    report_dir = ROOT / "test-results" / "stack"
    report_dir.mkdir(parents=True, exist_ok=True)
    passed = []
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix=project) as temporary:
        compose_file = Path(temporary) / "compose.json"
        compose_file.write_text(json.dumps(config), encoding="utf-8")
        command = [
            "docker",
            "compose",
            "--project-name",
            project,
            "--file",
            str(compose_file),
        ]

        def compose(*arguments, capture=False, check=True, timeout=180):
            return subprocess.run(
                command + list(arguments),
                check=check,
                text=True,
                capture_output=capture,
                timeout=timeout,
                env=dict(os.environ, BUILDKIT_PROGRESS="plain"),
            )

        def sql(query):
            return compose(
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "ledger_app",
                "-d",
                "ledger_test",
                "-At",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                query,
                capture=True,
            ).stdout.strip()

        compose("config", "--quiet")
        if args.check:
            print("Compose configuration valid; no services started.")
            return
        # Fail before building if the daemon is unavailable.
        subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"], check=True, timeout=30
        )
        result = {"status": "failed", "checks": passed}
        try:
            compose("build", timeout=2400)
            compose("up", "--detach", timeout=300)
            address = compose("port", "gateway", "8000", capture=True).stdout.strip()
            require(
                address.startswith("127.0.0.1:"), "Smoke gateway must be loopback-only"
            )
            base = "http://" + address

            def request(path, body=None, key=key_a, retry_key=None, expected=200):
                headers = {"Content-Type": "application/json"}
                if key:
                    headers["X-API-Key"] = key
                if body is not None:
                    headers["Idempotency-Key"] = retry_key or secrets.token_hex(12)
                req = Request(
                    base + path,
                    data=None if body is None else json.dumps(body).encode(),
                    headers=headers,
                )
                try:
                    response = urlopen(req, timeout=10)
                except HTTPError as error:
                    response = error
                with response:
                    raw = response.read()
                    require(
                        response.status == expected,
                        f"{path}: expected {expected}, got {response.status}: {raw[:300]!r}",
                    )
                    return json.loads(raw)

            def ready():
                try:
                    return request("/health/ready", key=None)["status"] == "ready"
                except RuntimeError:
                    return False

            wait_for(ready, "authenticated ledger readiness")
            passed.append("migrations and authenticated service readiness")
            request("/api/v1/accounts", key=None, expected=401)
            accounts = []
            for merchant, key in (("a", key_a), ("b", key_b)):
                pair = []
                for suffix in ("debit", "credit"):
                    account = request(
                        "/api/v1/accounts",
                        {
                            "account_number": f"smoke-{merchant}-{suffix}",
                            "account_type": "ASSET",
                            "owner_ref": "synthetic",
                            "currency": "INR",
                        },
                        key=key,
                        expected=201,
                    )
                    pair.append(account["account_id"])
                accounts.append(pair)
            require(
                request("/api/v1/accounts")["total"] == 2, "Tenant account list leaked"
            )
            request("/api/v1/accounts/" + accounts[0][0], key=key_b, expected=404)

            def payment(pair):
                return {
                    "transaction_type": "PAYMENT",
                    "narrative": "Synthetic container acceptance",
                    "entries": [
                        {
                            "account_id": pair[0],
                            "direction": "DEBIT",
                            "amount_minor": 1250,
                            "currency": "INR",
                        },
                        {
                            "account_id": pair[1],
                            "direction": "CREDIT",
                            "amount_minor": 1250,
                            "currency": "INR",
                        },
                    ],
                }

            body = payment(accounts[0])
            posted = request(
                "/api/v1/transactions", body, retry_key="same-client-key", expected=201
            )
            replay = request(
                "/api/v1/transactions", body, retry_key="same-client-key", expected=201
            )
            require(replay == posted, "Retry changed the transaction")
            changed = dict(body, narrative="changed")
            request(
                "/api/v1/transactions",
                changed,
                retry_key="same-client-key",
                expected=409,
            )
            foreign = request(
                "/api/v1/transactions",
                payment(accounts[1]),
                key=key_b,
                retry_key="same-client-key",
                expected=201,
            )
            require(
                foreign["transaction_id"] != posted["transaction_id"],
                "Retry key crossed tenants",
            )
            request("/api/v1/transactions", body, key=key_b, expected=404)
            request(
                "/api/v1/accounts/" + accounts[0][0] + "/balance",
                key=key_b,
                expected=404,
            )
            unbalanced = payment(accounts[0])
            unbalanced["entries"][1]["amount_minor"] = 1
            request("/api/v1/transactions", unbalanced, expected=400)
            require(
                sql("SELECT count(*) FROM transactions") == "2",
                "Rejected or replayed requests changed the journal",
            )
            passed.append(
                "tenant isolation, balanced posting and retry conflict handling"
            )

            # Lose the hot cache; PostgreSQL must still replay the committed response.
            compose("exec", "-T", "redis", "redis-cli", "FLUSHALL", capture=True)
            compose("restart", "postgres", "redis")
            wait_for(lambda: sql("SELECT 1") == "1", "database restart")
            compose("restart", "ledger-core", "gateway")
            # Docker can allocate a new host port when restarting an ephemeral mapping.
            address = compose("port", "gateway", "8000", capture=True).stdout.strip()
            require(
                address.startswith("127.0.0.1:"),
                "Smoke gateway must remain loopback-only",
            )
            base = "http://" + address
            wait_for(ready, "restart recovery")
            require(
                request(
                    "/api/v1/transactions",
                    body,
                    retry_key="same-client-key",
                    expected=201,
                )
                == posted,
                "Persistent retry failed after restart",
            )
            balance_path = "/api/v1/accounts/" + accounts[0][0] + "/balance"
            require(
                request(balance_path)["balance_minor"] == 1250,
                "Balance did not survive restart",
            )
            passed.append(
                "PostgreSQL persistence and retry replay after cache loss and service restart"
            )

            compose("stop", "ledger-core")
            wait_for(
                lambda: (
                    request("/health/ready", key=None, expected=503)["detail"][
                        "checks"
                    ]["ledger"]
                    == "unavailable"
                ),
                "ledger outage readiness",
            )
            compose("start", "ledger-core")
            wait_for(ready, "ledger reconnect")
            passed.append("readiness detects ledger outage and recovers")

            # Stop the broker, commit a reversal, then prove queued event recovery.
            compose("stop", "kafka")
            reverse_path = (
                "/api/v1/transactions/" + posted["transaction_id"] + "/reverse"
            )
            reversal_body = {
                "idempotency_key": "reverse-smoke",
                "reason": "acceptance cleanup",
            }
            request(
                reverse_path,
                reversal_body,
                key=key_b,
                retry_key="reverse-smoke",
                expected=404,
            )
            reversal = request(
                reverse_path, reversal_body, retry_key="reverse-smoke", expected=200
            )
            require(
                request(
                    reverse_path, reversal_body, retry_key="reverse-smoke", expected=200
                )
                == reversal,
                "Reversal replay failed",
            )
            require(
                request(balance_path)["balance_minor"] == 0,
                "Reversal did not restore balance",
            )
            require(
                int(sql("SELECT count(*) FROM outbox_events WHERE NOT published")) > 0,
                "Outbox lost pending events during broker outage",
            )
            compose("start", "kafka")
            wait_for(
                lambda: (
                    sql("SELECT count(*) FROM outbox_events WHERE NOT published") == "0"
                ),
                "outbox drains after broker recovery",
                seconds=180,
            )
            event = compose(
                "exec",
                "-T",
                "kafka",
                "kafka-console-consumer",
                "--bootstrap-server",
                "localhost:9092",
                "--topic",
                "ledger.transaction.reversed",
                "--from-beginning",
                "--max-messages",
                "1",
                "--timeout-ms",
                "15000",
                capture=True,
            ).stdout
            require(
                json.loads(event.strip())["transaction_id"]
                == reversal["transaction_id"],
                "Broker reversal event does not match committed journal",
            )
            require(
                sql(
                    "SELECT count(*) FROM (SELECT transaction_id FROM ledger_entries GROUP BY transaction_id HAVING sum(CASE WHEN direction='DEBIT' THEN amount_minor ELSE -amount_minor END) <> 0) unbalanced"
                )
                == "0",
                "Journal invariant failed",
            )
            passed.append(
                "reversal replay, journal invariant and real Kafka outbox recovery"
            )
            result["status"] = "passed"
            print(json.dumps(result, indent=2))
        finally:
            result["seconds"] = round(time.monotonic() - started)
            (report_dir / "summary.json").write_text(
                json.dumps(result, indent=2) + "\n"
            )
            try:
                logs = compose(
                    "logs", "--no-color", "--tail", "150", capture=True, check=False
                )
                log_text = logs.stdout + logs.stderr
                for secret in (key_a, key_b, token, db_password):
                    log_text = log_text.replace(secret, "[redacted]")
                (report_dir / "containers.log").write_text(log_text, encoding="utf-8")
            finally:
                compose("down", "--volumes", "--remove-orphans")


if __name__ == "__main__":
    main()
