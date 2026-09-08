import asyncio
import json
import structlog
from aiokafka import AIOKafkaConsumer
from typing import Optional

from app.config import get_settings
from app.embedding import embed_text
from app.db import index_document, get_pool

logger = structlog.get_logger()
settings = get_settings()

def render_transaction_narrative(event: dict) -> str:
    entries = event.get("entries", [])
    entry_strs = []
    for e in entries:
        entry_strs.append(f"{e['direction']} {e['amount_minor']} {e['currency']} to account {e['account_id']}")
    
    return f"""Transaction {event['transaction_id']} ({event['transaction_type']})
Reference: {event.get('reference_id', 'N/A')}
Entries:
{chr(10).join(entry_strs)}
Narrative: {event.get('narrative', 'N/A')}
Posted at: {event.get('posted_at', 'N/A')}"""

def render_exception_narrative(event: dict) -> str:
    return f"""Reconciliation Exception {event['exception_id']}
Type: {event['exception_type']}
Transaction: {event.get('transaction_id', 'N/A')}
Bank Reference: {event.get('bank_reference', 'N/A')}
Ledger Amount: {event.get('ledger_amount_minor', 0)} minor units
Bank Amount: {event.get('bank_amount_minor', 0)} minor units
Detected at: {event.get('detected_at', 'N/A')}"""

async def index_document_from_event(pool, event: dict, source_type: str, source_ref: str):
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

    embedding = await embed_text(content)
    await index_document(pool, source_type, source_ref, content, embedding)
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
        enable_auto_commit=True,
    )
    
    await consumer.start()
    logger.info("kafka_consumer_started")
    
    try:
        async for msg in consumer:
            try:
                event = json.loads(msg.value.decode())
                topic = msg.topic
                
                if topic == "ledger.transaction.posted":
                    await index_document_from_event(pool, event, "TXN_NARRATIVE", event["transaction_id"])
                elif topic == "ledger.transaction.reversed":
                    await index_document_from_event(pool, event, "TXN_NARRATIVE", event["transaction_id"])
                elif topic == "reconciliation.exception.raised":
                    await index_document_from_event(pool, event, "RECONCILIATION_EXCEPTION", event["exception_id"])
                    
            except Exception as e:
                logger.error("event_processing_failed", topic=msg.topic, error=str(e))
    finally:
        await consumer.stop()
        await pool.close()
        logger.info("kafka_consumer_stopped")

if __name__ == "__main__":
    asyncio.run(consume_ledger_events())