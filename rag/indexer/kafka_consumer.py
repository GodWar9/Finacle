import asyncio
import json

import structlog
from aiokafka import AIOKafkaConsumer
from app.config import get_settings
from app.db import get_pool, index_document
from app.embedding import embed_text

logger = structlog.get_logger()
settings = get_settings()


def render_transaction_narrative(event: dict) -> str:
    entries = event.get("entries", [])
    entry_strs = []
    for e in entries:
        entry_strs.append(
            f"{e['direction']} {e['amount_minor']} {e['currency']} to account {e['account_id']}"
        )

    return f"""Transaction {event["transaction_id"]} ({event["transaction_type"]})
Reference: {event.get("reference_id", "N/A")}
Entries:
{chr(10).join(entry_strs)}
Narrative: {event.get("narrative", "N/A")}
Posted at: {event.get("posted_at", "N/A")}"""


def render_exception_narrative(event: dict) -> str:
    return f"""Reconciliation Exception {event["exception_id"]}
Type: {event["exception_type"]}
Transaction: {event.get("transaction_id", "N/A")}
Bank Reference: {event.get("bank_reference", "N/A")}
Ledger Amount: {event.get("ledger_amount_minor", 0)} minor units
Bank Amount: {event.get("bank_amount_minor", 0)} minor units
Detected at: {event.get("detected_at", "N/A")}"""


async def index_document_from_event(
    pool, event: dict, source_type: str, source_ref: str
):
    if source_type == "TXN_NARRATIVE":
        content = render_transaction_narrative(event)
    elif source_type == "RECONCILIATION_EXCEPTION":
        content = render_exception_narrative(event)
    else:
        return

    if not settings.openai_api_key:
        # embed_text() degrades to all-zero vectors when no key is configured;
        # indexing them would make the zero vectors the "most similar" match
        # for every future query, so skip until a key is available.
        logger.warning(
            "index_skipped_no_api_key",
            source_type=source_type,
            source_ref=source_ref,
        )
        return

    # Resolve ownership from committed ledger data, never from untrusted event claims.
    from uuid import UUID

    async with pool.acquire() as conn:
        if source_type == "TXN_NARRATIVE":
            merchant = await conn.fetchval(
                """SELECT min(a.merchant_id) FROM ledger_entries e
                   JOIN accounts a ON a.account_id = e.account_id
                   WHERE e.transaction_id = $1
                   HAVING count(*) > 0 AND count(a.merchant_id) = count(*)
                      AND count(DISTINCT a.merchant_id) = 1""",
                UUID(source_ref),
            )
        else:
            merchant = await conn.fetchval(
                """SELECT f.uploaded_by FROM reconciliation_exceptions e
                   JOIN settlement_files f ON f.batch_id = e.batch_id
                   WHERE e.exception_id = $1""",
                UUID(source_ref),
            )
    if not merchant:
        logger.warning("index_skipped_unassigned_owner", source_ref=source_ref)
        return
    embedding = await embed_text(content)
    await index_document(
        pool, source_type, source_ref, content, embedding, merchant_id=merchant
    )
    logger.info("indexed_event", source_type=source_type, source_ref=source_ref)


async def consume_ledger_events():
    pool = await get_pool()

    consumer = AIOKafkaConsumer(
        "ledger.transaction.posted",
        "ledger.transaction.reversed",
        "reconciliation.exception.raised",
        bootstrap_servers=settings.kafka_brokers,
        group_id="rag-indexer",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )

    await consumer.start()
    logger.info("kafka_consumer_started")

    try:
        async for msg in consumer:
            try:
                event = json.loads(msg.value.decode())
                topic = msg.topic

                if (
                    topic == "ledger.transaction.posted"
                    or topic == "ledger.transaction.reversed"
                ):
                    await index_document_from_event(
                        pool, event, "TXN_NARRATIVE", event["transaction_id"]
                    )
                elif topic == "reconciliation.exception.raised":
                    await index_document_from_event(
                        pool, event, "RECONCILIATION_EXCEPTION", event["exception_id"]
                    )

                await consumer.commit()
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                logger.error("event_processing_failed", topic=msg.topic, error=str(e))
                raise
    finally:
        await consumer.stop()
        await pool.close()
        logger.info("kafka_consumer_stopped")


if __name__ == "__main__":
    asyncio.run(consume_ledger_events())
