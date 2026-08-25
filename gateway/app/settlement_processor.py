import asyncio
import asyncpg
import structlog
import tempfile
import os
import subprocess
import uuid
from datetime import datetime

from app.config import get_settings

logger = structlog.get_logger()
settings = get_settings()

RECON_ENGINE_PATH = settings.recon_engine_path


class SettlementProcessor:
    def __init__(self, pg_pool: asyncpg.Pool, poll_interval: int = 10):
        self.pg_pool = pg_pool
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
                await self.run_reconciliation(batch_id, content)
                await conn.execute(
                    "UPDATE settlement_files SET status = 'COMPLETED', completed_at = NOW() WHERE batch_id = $1",
                    batch_id
                )
                logger.info("settlement_completed", batch_id=batch_id)
            except Exception as e:
                await conn.execute(
                    "UPDATE settlement_files SET status = 'FAILED', error_message = $1, completed_at = NOW() WHERE batch_id = $2",
                    str(e), batch_id
                )
                logger.error("settlement_failed", batch_id=batch_id, error=str(e))
                raise

    async def run_reconciliation(self, batch_id: str, bank_file_content: bytes):
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

        finally:
            if os.path.exists(bank_file_path):
                os.unlink(bank_file_path)
            if "ledger_export_path" in locals() and os.path.exists(ledger_export_path):
                os.unlink(ledger_export_path)

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
                # Fixed-width format matching recon engine's FixedWidthLayout:
                # ref: 20 chars at pos 0, amt: 13 chars at pos 20, ccy: 3 chars at pos 33, sts: 7 chars at pos 36
                ref = row['transaction_id'][:20].ljust(20)
                amt = str(row['amount_minor']).rjust(13)
                ccy = row['currency'][:3].ljust(3)
                sts = row['status'][:7].ljust(7)
                line = f"{ref}{amt}{ccy}{sts}"
                f.write(line + "\n")
            ledger_export_path = f.name

        return ledger_export_path


async def run_settlement_processor(poll_interval: int = 10):
    pg_pool = await asyncpg.create_pool(settings.database_url, min_size=2, max_size=5)
    processor = SettlementProcessor(pg_pool, poll_interval)
    try:
        await processor.start()
    finally:
        await pg_pool.close()


if __name__ == "__main__":
    import os
    poll_interval = int(os.getenv("POLL_INTERVAL", "10"))
    asyncio.run(run_settlement_processor(poll_interval))