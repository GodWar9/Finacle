use crate::ledger::domain::{
    Direction, GetBalanceResponse, LedgerEntry, LedgerError, PostedTransaction, TransactionRequest,
};
use crate::ledger::idempotency::{insert_idempotency_record, lookup_idempotency};
use crate::ledger::outbox::insert_outbox_event;
use chrono::Utc;
use futures::future::join_all;
use sha2::{Digest, Sha256};
use sqlx::{PgPool, Postgres, Transaction};
use tracing::{error, info, warn};
use uuid::Uuid;

pub async fn post_transaction(
    pool: &PgPool,
    req: TransactionRequest,
) -> Result<PostedTransaction, LedgerError> {
    req.validate_balanced()?;

    info!(
        "Posting transaction with idempotency_key: {}",
        req.idempotency_key
    );

    let request_hash = req.request_hash();

    if let Some(cached) = lookup_idempotency(pool, &req.idempotency_key).await? {
        if cached.request_hash != request_hash {
            return Err(LedgerError::IdempotencyConflict(
                req.idempotency_key.clone(),
            ));
        }
        info!("Idempotency replay for key: {}", req.idempotency_key);
        return Ok(PostedTransaction {
            transaction_id: cached.response_body["transaction_id"]
                .as_str()
                .unwrap_or("")
                .parse()
                .unwrap_or_default(),
            status: cached.response_body["status"]
                .as_str()
                .unwrap_or("POSTED")
                .to_string(),
            posted_at_unix_ms: cached.response_body["posted_at_unix_ms"]
                .as_i64()
                .unwrap_or(0),
        });
    }

    let mut tx: Transaction<'_, Postgres> = pool.begin().await?;

    sqlx::query!("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        .execute(&mut *tx)
        .await?;

    let txn_id = insert_transaction_header(&mut tx, &req).await?;

    for entry in &req.entries {
        insert_ledger_entry(&mut tx, txn_id, entry).await?;
    }

    insert_idempotency_record(&mut tx, &req, txn_id, &request_hash).await?;
    insert_outbox_event(&mut tx, txn_id, &req).await?;

    tx.commit().await?;

    info!("Transaction {} posted successfully", txn_id);

    Ok(PostedTransaction {
        transaction_id: txn_id,
        status: "POSTED".to_string(),
        posted_at_unix_ms: Utc::now().timestamp_millis(),
    })
}

async fn insert_transaction_header(
    tx: &mut Transaction<'_, Postgres>,
    req: &TransactionRequest,
) -> Result<Uuid, sqlx::Error> {
    let txn_id = Uuid::new_v4();
    sqlx::query!(
        r#"
        INSERT INTO transactions (transaction_id, idempotency_key, request_hash, transaction_type, reference_id, narrative)
        VALUES ($1, $2, $3, $4, $5, $6)
        "#,
        txn_id,
        req.idempotency_key,
        req.request_hash(),
        req.transaction_type,
        req.reference_id,
        req.narrative
    )
    .execute(&mut **tx)
    .await?;
    Ok(txn_id)
}

async fn insert_ledger_entry(
    tx: &mut Transaction<'_, Postgres>,
    txn_id: Uuid,
    entry: &LedgerEntry,
) -> Result<(), sqlx::Error> {
    let direction_str = match entry.direction {
        Direction::Debit => "DEBIT",
        Direction::Credit => "CREDIT",
    };

    sqlx::query!(
        r#"
        INSERT INTO ledger_entries (transaction_id, account_id, direction, amount_minor, currency)
        VALUES ($1, $2, $3, $4, $5)
        "#,
        txn_id,
        entry.account_id,
        direction_str,
        entry.amount_minor,
        entry.currency
    )
    .execute(&mut **tx)
    .await?;
    Ok(())
}

pub async fn get_balance(
    pool: &PgPool,
    account_id: Uuid,
) -> Result<GetBalanceResponse, LedgerError> {
    let row = sqlx::query!(
        r#"
        SELECT account_id, balance_minor, currency, as_of_transaction_id, updated_at
        FROM account_balances
        WHERE account_id = $1
        "#,
        account_id
    )
    .fetch_optional(pool)
    .await?;

    match row {
        Some(r) => Ok(GetBalanceResponse {
            account_id: r.account_id,
            balance_minor: r.balance_minor,
            currency: r.currency,
            as_of_unix_ms: r.updated_at.timestamp_millis(),
        }),
        None => {
            let account = sqlx::query!(
                "SELECT currency FROM accounts WHERE account_id = $1 AND status = 'ACTIVE'",
                account_id
            )
            .fetch_optional(pool)
            .await?;

            match account {
                Some(acc) => Ok(GetBalanceResponse {
                    account_id,
                    balance_minor: 0,
                    currency: acc.currency,
                    as_of_unix_ms: Utc::now().timestamp_millis(),
                }),
                None => Err(LedgerError::InvalidAccount(account_id)),
            }
        }
    }
}

pub async fn reverse_transaction(
    pool: &PgPool,
    original_txn_id: Uuid,
    idempotency_key: String,
    reason: String,
) -> Result<PostedTransaction, LedgerError> {
    let original = sqlx::query!(
        "SELECT transaction_id, transaction_type, reference_id, narrative, status FROM transactions WHERE transaction_id = $1",
        original_txn_id
    )
    .fetch_optional(pool)
    .await?
    .ok_or(LedgerError::TransactionNotFound(original_txn_id))?;

    if original.status == "REVERSED" {
        return Err(LedgerError::Internal(
            "Transaction already reversed".to_string(),
        ));
    }

    let entries = sqlx::query!(
        "SELECT account_id, direction, amount_minor, currency FROM ledger_entries WHERE transaction_id = $1",
        original_txn_id
    )
    .fetch_all(pool)
    .await?;

    let reversed_entries: Vec<LedgerEntry> = entries
        .into_iter()
        .map(|e| {
            let direction = match e.direction.as_str() {
                "DEBIT" => Direction::Credit,
                "CREDIT" => Direction::Debit,
                _ => Direction::Debit,
            };
            LedgerEntry {
                account_id: e.account_id,
                direction,
                amount_minor: e.amount_minor,
                currency: e.currency,
            }
        })
        .collect();

    let req = TransactionRequest {
        idempotency_key,
        transaction_type: "REVERSAL".to_string(),
        reference_id: original.reference_id,
        entries: reversed_entries,
        narrative: Some(format!(
            "Reversal of {}: {}",
            original.transaction_type, reason
        )),
    };

    let result = post_transaction(pool, req).await?;

    sqlx::query!(
        "UPDATE transactions SET status = 'REVERSED', reversal_of = $1 WHERE transaction_id = $2",
        original_txn_id,
        original_txn_id
    )
    .execute(pool)
    .await?;

    Ok(result)
}

pub async fn run_balance_auditor(pool: &PgPool) -> Result<Vec<(Uuid, i64, i64)>, LedgerError> {
    let mismatches = sqlx::query!(
        r#"
        SELECT a.account_id, 
               COALESCE(SUM(CASE WHEN le.direction = 'DEBIT' THEN le.amount_minor ELSE -le.amount_minor END), 0)::bigint as "derived_balance!",
               COALESCE(ab.balance_minor, 0)::bigint as "materialized_balance!"
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

    let results: Vec<(Uuid, i64, i64)> = mismatches
        .into_iter()
        .map(|r| (r.account_id, r.derived_balance, r.materialized_balance))
        .collect();

    for (account_id, derived, materialized) in &results {
        error!(
            "BALANCE MISMATCH for account {}: derived={}, materialized={}",
            account_id, derived, materialized
        );
    }

    Ok(results)
}

#[cfg(test)]
mod tests {
    use super::*;
    use sqlx::PgPool;

    #[sqlx::test(migrations = "../db/migrations")]
    async fn test_post_transaction_happy_path(pool: PgPool) {
        let account1 = sqlx::query!(
            "INSERT INTO accounts (account_number, account_type, owner_ref, currency) VALUES ('ACC001', 'ASSET', 'merchant1', 'INR') RETURNING account_id"
        )
        .fetch_one(&pool)
        .await
        .unwrap()
        .account_id;

        let account2 = sqlx::query!(
            "INSERT INTO accounts (account_number, account_type, owner_ref, currency) VALUES ('ACC002', 'LIABILITY', 'merchant2', 'INR') RETURNING account_id"
        )
        .fetch_one(&pool)
        .await
        .unwrap()
        .account_id;

        let req = TransactionRequest {
            idempotency_key: "idem-1".to_string(),
            transaction_type: "PAYMENT".to_string(),
            reference_id: Some("order-1".to_string()),
            entries: vec![
                LedgerEntry {
                    account_id: account1,
                    direction: Direction::Debit,
                    amount_minor: 10000,
                    currency: "INR".to_string(),
                },
                LedgerEntry {
                    account_id: account2,
                    direction: Direction::Credit,
                    amount_minor: 10000,
                    currency: "INR".to_string(),
                },
            ],
            narrative: Some("Test payment".to_string()),
        };

        let result = post_transaction(&pool, req).await.unwrap();
        assert!(!result.transaction_id.is_nil());
        assert_eq!(result.status, "POSTED");
    }

    #[sqlx::test(migrations = "../db/migrations")]
    async fn test_idempotency_replay(pool: PgPool) {
        let account1 = sqlx::query!(
            "INSERT INTO accounts (account_number, account_type, owner_ref, currency) VALUES ('ACC003', 'ASSET', 'merchant1', 'INR') RETURNING account_id"
        )
        .fetch_one(&pool)
        .await
        .unwrap()
        .account_id;

        let account2 = sqlx::query!(
            "INSERT INTO accounts (account_number, account_type, owner_ref, currency) VALUES ('ACC004', 'LIABILITY', 'merchant2', 'INR') RETURNING account_id"
        )
        .fetch_one(&pool)
        .await
        .unwrap()
        .account_id;

        let req = TransactionRequest {
            idempotency_key: "idem-2".to_string(),
            transaction_type: "PAYMENT".to_string(),
            reference_id: Some("order-2".to_string()),
            entries: vec![
                LedgerEntry {
                    account_id: account1,
                    direction: Direction::Debit,
                    amount_minor: 5000,
                    currency: "INR".to_string(),
                },
                LedgerEntry {
                    account_id: account2,
                    direction: Direction::Credit,
                    amount_minor: 5000,
                    currency: "INR".to_string(),
                },
            ],
            narrative: None,
        };

        let result1 = post_transaction(&pool, req.clone()).await.unwrap();
        let result2 = post_transaction(&pool, req).await.unwrap();

        assert_eq!(result1.transaction_id, result2.transaction_id);

        let count: i64 = sqlx::query_scalar!(
            r#"SELECT COUNT(*)::bigint as "count!" FROM transactions WHERE idempotency_key = 'idem-2'"#
        )
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(count, 1);
    }

    #[sqlx::test(migrations = "../db/migrations")]
    async fn test_concurrent_same_account(pool: PgPool) {
        let account1 = sqlx::query!(
            "INSERT INTO accounts (account_number, account_type, owner_ref, currency) VALUES ('ACC007', 'ASSET', 'merchant1', 'INR') RETURNING account_id"
        )
        .fetch_one(&pool)
        .await
        .unwrap()
        .account_id;

        let account2 = sqlx::query!(
            "INSERT INTO accounts (account_number, account_type, owner_ref, currency) VALUES ('ACC008', 'LIABILITY', 'merchant2', 'INR') RETURNING account_id"
        )
        .fetch_one(&pool)
        .await
        .unwrap()
        .account_id;

        let make_req = |key: &str| TransactionRequest {
            idempotency_key: key.to_string(),
            transaction_type: "PAYMENT".to_string(),
            reference_id: Some(format!("order-{}", key)),
            entries: vec![
                LedgerEntry {
                    account_id: account1,
                    direction: Direction::Debit,
                    amount_minor: 1000,
                    currency: "INR".to_string(),
                },
                LedgerEntry {
                    account_id: account2,
                    direction: Direction::Credit,
                    amount_minor: 1000,
                    currency: "INR".to_string(),
                },
            ],
            narrative: None,
        };

        let mut handles = vec![];
        for i in 0..10 {
            let pool_clone = pool.clone();
            let req = make_req(&format!("concurrent-{}", i));
            handles.push(tokio::spawn(async move {
                post_transaction(&pool_clone, req).await
            }));
        }

        let results: Vec<_> = futures::future::join_all(handles).await;
        let successful: Vec<_> = results
            .into_iter()
            .filter_map(|r| r.ok().flatten())
            .collect();

        assert_eq!(successful.len(), 10);

        let balance = get_balance(&pool, account1).await.unwrap();
        assert_eq!(balance.balance_minor, 10000);
    }

    #[sqlx::test(migrations = "../db/migrations")]
    async fn test_idempotency_conflict(pool: PgPool) {
        let account1 = sqlx::query!(
            "INSERT INTO accounts (account_number, account_type, owner_ref, currency) VALUES ('ACC005', 'ASSET', 'merchant1', 'INR') RETURNING account_id"
        )
        .fetch_one(&pool)
        .await
        .unwrap()
        .account_id;

        let account2 = sqlx::query!(
            "INSERT INTO accounts (account_number, account_type, owner_ref, currency) VALUES ('ACC006', 'LIABILITY', 'merchant2', 'INR') RETURNING account_id"
        )
        .fetch_one(&pool)
        .await
        .unwrap()
        .account_id;

        let req1 = TransactionRequest {
            idempotency_key: "idem-3".to_string(),
            transaction_type: "PAYMENT".to_string(),
            reference_id: Some("order-3".to_string()),
            entries: vec![
                LedgerEntry {
                    account_id: account1,
                    direction: Direction::Debit,
                    amount_minor: 1000,
                    currency: "INR".to_string(),
                },
                LedgerEntry {
                    account_id: account2,
                    direction: Direction::Credit,
                    amount_minor: 1000,
                    currency: "INR".to_string(),
                },
            ],
            narrative: None,
        };

        post_transaction(&pool, req1).await.unwrap();

        let req2 = TransactionRequest {
            idempotency_key: "idem-3".to_string(),
            transaction_type: "PAYMENT".to_string(),
            reference_id: Some("order-3-different".to_string()),
            entries: vec![
                LedgerEntry {
                    account_id: account1,
                    direction: Direction::Debit,
                    amount_minor: 2000,
                    currency: "INR".to_string(),
                },
                LedgerEntry {
                    account_id: account2,
                    direction: Direction::Credit,
                    amount_minor: 2000,
                    currency: "INR".to_string(),
                },
            ],
            narrative: None,
        };

        let result = post_transaction(&pool, req2).await;
        assert!(matches!(result, Err(LedgerError::IdempotencyConflict(_))));
    }
}
