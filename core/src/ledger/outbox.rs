use crate::ledger::domain::TransactionRequest;
use sqlx::{Postgres, Transaction};
use uuid::Uuid;

pub async fn insert_outbox_event(
    tx: &mut Transaction<'_, Postgres>,
    txn_id: Uuid,
    req: &TransactionRequest,
) -> Result<(), sqlx::Error> {
    let payload = serde_json::json!({
        "transaction_id": txn_id.to_string(),
        "transaction_type": req.transaction_type,
        "reference_id": req.reference_id,
        "entries": req.entries.iter().map(|e| serde_json::json!({
            "account_id": e.account_id.to_string(),
            "direction": match e.direction {
                crate::ledger::domain::Direction::Debit => "DEBIT",
                crate::ledger::domain::Direction::Credit => "CREDIT",
            },
            "amount_minor": e.amount_minor,
            "currency": e.currency,
        })).collect::<Vec<_>>(),
        "narrative": req.narrative,
        "posted_at": chrono::Utc::now().to_rfc3339(),
    });

    sqlx::query!(
        r#"
        INSERT INTO outbox_events (topic, payload_json)
        VALUES ($1, $2)
        "#,
        "ledger.transaction.posted",
        payload
    )
    .execute(&mut **tx)
    .await?;

    Ok(())
}

pub async fn insert_reversal_outbox_event(
    tx: &mut Transaction<'_, Postgres>,
    txn_id: Uuid,
    original_txn_id: Uuid,
    reason: &str,
) -> Result<(), sqlx::Error> {
    let payload = serde_json::json!({
        "transaction_id": txn_id.to_string(),
        "transaction_type": "REVERSAL",
        "reversal_of": original_txn_id.to_string(),
        "reason": reason,
        "posted_at": chrono::Utc::now().to_rfc3339(),
    });

    sqlx::query!(
        r#"
        INSERT INTO outbox_events (topic, payload_json)
        VALUES ($1, $2)
        "#,
        "ledger.transaction.reversed",
        payload
    )
    .execute(&mut **tx)
    .await?;

    Ok(())
}
