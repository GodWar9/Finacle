use chrono::Utc;
use sqlx::PgPool;
use tracing::{error, info, warn};

pub async fn run_balance_auditor(
    pool: &PgPool,
) -> Result<Vec<(uuid::Uuid, i64, i64)>, crate::ledger::domain::LedgerError> {
    let mismatches = sqlx::query!(
        r#"
        SELECT a.account_id, 
               COALESCE(SUM(CASE WHEN le.direction = 'DEBIT' THEN le.amount_minor ELSE -le.amount_minor END), 0)::bigint as derived_balance,
               COALESCE(ab.balance_minor, 0)::bigint as materialized_balance
        FROM accounts a
        LEFT JOIN ledger_entries le ON le.account_id = a.account_id
        LEFT JOIN account_balances ab ON ab.account_id = a.account_id
        WHERE a.status = 'ACTIVE'
        GROUP BY a.account_id, ab.balance_minor
        HAVING COALESCE(SUM(CASE WHEN le.direction = 'DEBIT' THEN le.amount_minor ELSE -le.amount_minor END), 0) != COALESCE(ab.balance_minor, 0)
        "#
    )
    .fetch_all(pool)
    .await?;

    let results: Vec<(uuid::Uuid, i64, i64)> = mismatches
        .into_iter()
        .map(|r| (r.account_id, r.derived_balance, r.materialized_balance))
        .collect();

    for (account_id, derived, materialized) in &results {
        error!(
            "BALANCE MISMATCH for account {}: derived={}, materialized={}",
            account_id, derived, materialized
        );
    }

    if results.is_empty() {
        info!(
            "Balance auditor: All {} accounts balanced",
            sqlx::query_scalar!(
                r#"SELECT COUNT(*)::bigint as "count!" FROM accounts WHERE status = 'ACTIVE'"#
            )
            .fetch_one(pool)
            .await
            .unwrap_or(0)
        );
    } else {
        warn!("Balance auditor: Found {} mismatches", results.len());
    }

    Ok(results)
}

pub async fn run_balance_auditor_scheduled(pool: PgPool, interval_hours: u64) {
    let mut interval = tokio::time::interval(tokio::time::Duration::from_hours(interval_hours));

    loop {
        interval.tick().await;

        if let Err(e) = run_balance_auditor(&pool).await {
            error!("Balance auditor failed: {}", e);
        }
    }
}
