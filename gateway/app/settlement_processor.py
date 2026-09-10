import asyncio
import json
import asyncpg
import structlog
import tempfile
import os
import subprocess
import uuid
from datetime import datetime

from aiokafka import AIOKafkaProducer

from app.config import get_settings

logger = structlog.get_logger()
settings = get_settings()

RECON_ENGINE_PATH = settings.recon_engine_path


class SettlementProcessor:
    def __init__(self, pg_pool: asyncpg.Pool, kafka_producer: AIOKafkaProducer, poll_interval: int = 10):
        self.pg_pool = pg_pool
        self.kafka_producer = kafka_producer
        self.poll_interval = poll_interval
        self.running = False

    async def start(self):
        self.running = True
        logger.info("settlement_processor_started", poll_interval=self.poll_interval)
        while self.running:
            try:
                await self.process_pending()
            except Exception as e:
                logger.error("settlement_processor_error", error=str(e))
            await asyncio.sleep(self.poll_interval)

    def stop(self):
        self.running = False
        logger.info("settlement_processor_stopped")

    async def process_pending(self):
        async with self.pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT batch_id, filename, content
                FROM settlement_files
                WHERE status = 'UPLOADED'
                ORDER BY created_at
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """
            )
            if not row:
                return

            batch_id = row["batch_id"]
            filename = row["filename"]
            content = row["content"]

            logger.info("processing_settlement", batch_id=batch_id, filename=filename)

            await conn.execute(
                "UPDATE settlement_files SET status = 'PROCESSING', started_at = NOW() WHERE batch_id = $1",
                batch_id
            )

            try:
                exception_count = await self.run_reconciliation(batch_id, content)
                await conn.execute(
                    "UPDATE settlement_files SET status = 'COMPLETED', completed_at = NOW() WHERE batch_id = $1",
                    batch_id
                )
                logger.info("settlement_completed", batch_id=batch_id, exceptions=exception_count)
            except Exception as e:
                await conn.execute(
                    "UPDATE settlement_files SET status = 'FAILED', error_message = $1, completed_at = NOW() WHERE batch_id = $2",
                    str(e), batch_id
                )
                logger.error("settlement_failed", batch_id=batch_id, error=str(e))
                raise

    async def run_reconciliation(self, batch_id: str, bank_file_content: bytes) -> int:
        """Run the C++ recon engine, then publish Kafka events for any exceptions."""
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".txt", delete=False) as bank_file:
            bank_file.write(bank_file_content)
            bank_file_path = bank_file.name

        try:
            ledger_export_path = await self.generate_ledger_export(batch_id)

            cmd = [
                RECON_ENGINE_PATH,
                f"--ledger-export={ledger_export_path}",
                f"--bank-file={bank_file_path}",
                f"--batch-id={batch_id}",
            ]

            env = os.environ.copy()
            env["DATABASE_URL"] = settings.database_url

            logger.info("running_recon_engine", batch_id=batch_id, cmd=" ".join(cmd))

            result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=300)

            if result.returncode != 0:
                logger.error("recon_engine_failed", batch_id=batch_id, stderr=result.stderr)
                raise RuntimeError(f"Recon engine failed: {result.stderr}")

            logger.info("recon_engine_completed", batch_id=batch_id, stdout=result.stdout)

            exception_count = await self.publish_recon_events(batch_id)
            return exception_count

        finally:
            if os.path.exists(bank_file_path):
                os.unlink(bank_file_path)
            if "ledger_export_path" in locals() and os.path.exists(ledger_export_path):
                os.unlink(ledger_export_path)

    async def publish_recon_events(self, batch_id: str) -> int:
        """Query exceptions written by the recon engine and publish Kafka events."""
        async with self.pg_pool.acquire() as conn:
            summary = await conn.fetchrow(
                "SELECT matched_count, exception_count FROM reconciliation_batch_summary WHERE batch_id = $1",
                batch_id
            )
            exceptions = await conn.fetch(
                "SELECT exception_id, exception_type, ledger_transaction_id FROM reconciliation_exceptions WHERE batch_id = $1",
                batch_id
            )

        for exc in exceptions:
            event = {
                "exception_id": str(exc["exception_id"]),
                "exception_type": exc["exception_type"],
                "transaction_id": str(exc["ledger_transaction_id"]) if exc["ledger_transaction_id"] else None,
                "batch_id": batch_id,
            }
            await self.kafka_producer.send_and_wait(
                "reconciliation.exception.raised",
                key=str(exc["exception_id"]).encode(),
                value=json.dumps(event).encode(),
            )

        if summary:
            batch_event = {
                "batch_id": batch_id,
                "matched_count": summary["matched_count"],
                "exception_count": summary["exception_count"],
            }
            await self.kafka_producer.send_and_wait(
                "reconciliation.batch.completed",
                key=batch_id.encode(),
                value=json.dumps(batch_event).encode(),
            )

        logger.info("recon_events_published",
                     batch_id=batch_id,
                     exceptions=len(exceptions),
                     has_summary=summary is not None)
        return len(exceptions)

    async def generate_ledger_export(self, batch_id: str) -> str:
        async with self.pg_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT t.transaction_id, t.idempotency_key, t.transaction_type, t.reference_id,
                       t.status, t.created_at,
                       le.account_id, le.direction, le.amount_minor, le.currency
                FROM transactions t
                JOIN ledger_entries le ON le.transaction_id = t.transaction_id
                WHERE t.status = 'POSTED'
                ORDER BY t.created_at
                """
            )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            for row in rows:
                ref = str(row['transaction_id'])[:20].ljust(20)
                amt = str(row['amount_minor']).rjust(13)
                ccy = row['currency'][:3].ljust(3)
                sts = row['status'][:7].ljust(7)
                line = f"{ref}{amt}{ccy}{sts}"
                f.write(line + "\n")
            ledger_export_path = f.name

        return ledger_export_path


async def run_settlement_processor(poll_interval: int = 10):
    pg_pool = await asyncpg.create_pool(settings.database_url, min_size=2, max_size=5)
    kafka_producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_brokers)
    await kafka_producer.start()
    processor = SettlementProcessor(pg_pool, kafka_producer, poll_interval)
    try:
        await processor.start()
    finally:
        processor.stop()
        await kafka_producer.stop()
        await pg_pool.close()


if __name__ == "__main__":
    import os
    poll_interval = int(os.getenv("POLL_INTERVAL", "10"))
    asyncio.run(run_settlement_processor(poll_interval))
