use crate::ledger::domain::{LedgerError, PostedTransaction};
use chrono::{Duration, Utc};
use serde_json::Value;
use sqlx::{PgPool, Postgres, Row, Transaction};
use uuid::Uuid;

#[derive(Debug, Clone)]
pub struct IdempotencyRecord {
    pub idempotency_key: String,
    pub request_hash: String,
    pub response_body: Value,
    pub status_code: i32,
    pub created_at: chrono::DateTime<Utc>,
    pub expires_at: chrono::DateTime<Utc>,
}

pub async fn lookup_idempotency(
    pool: &PgPool,
    key: &str,
) -> Result<Option<IdempotencyRecord>, LedgerError> {
    let row = sqlx::query!(
        r#"
        SELECT idempotency_key, request_hash, response_body, status_code, created_at, expires_at
        FROM idempotency_records
        WHERE idempotency_key = $1 AND expires_at > $2
        "#,
        key,
        Utc::now()
    )
    .fetch_optional(pool)
    .await?;

    Ok(row.map(|r| IdempotencyRecord {
        idempotency_key: r.idempotency_key,
        request_hash: r.request_hash,
        response_body: r.response_body,
        status_code: r.status_code,
        created_at: r.created_at,
        expires_at: r.expires_at,
    }))
}

pub async fn insert_idempotency_record(
    tx: &mut Transaction<'_, Postgres>,
    req: &crate::ledger::domain::TransactionRequest,
    txn_id: Uuid,
    request_hash: &str,
) -> Result<(), sqlx::Error> {
    let response = PostedTransaction {
        transaction_id: txn_id,
        status: "POSTED".to_string(),
        posted_at_unix_ms: Utc::now().timestamp_millis(),
    };

    let response_json = serde_json::to_value(&response).unwrap();
    let expires_at = Utc::now() + Duration::hours(24);

    sqlx::query!(
        r#"
        INSERT INTO idempotency_records (idempotency_key, request_hash, response_body, status_code, expires_at)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (idempotency_key) DO NOTHING
        "#,
        req.idempotency_key,
        request_hash,
        response_json,
        201,
        expires_at
    )
    .execute(&mut *tx)
    .await?;

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use sqlx::PgPool;

    #[sqlx::test(migrations = "../db/migrations")]
    async fn test_idempotency_lookup(pool: PgPool) {
        let record = lookup_idempotency(&pool, "non-existent").await.unwrap();
        assert!(record.is_none());
    }
}
