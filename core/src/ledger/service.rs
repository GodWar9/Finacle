use crate::ledger::domain::{
    Direction, GetBalanceResponse, LedgerEntry, LedgerError, PostedTransaction, TransactionRequest,
};
use crate::ledger::idempotency::{insert_idempotency_record, lookup_idempotency};
use crate::ledger::outbox::{insert_outbox_event, insert_reversal_outbox_event};
use chrono::Utc;
use sqlx::{PgPool, Postgres, Transaction};
use tracing::info;
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
        idempotency_key: idempotency_key.clone(),
        transaction_type: "REVERSAL".to_string(),
        reference_id: original.reference_id,
        entries: reversed_entries,
        narrative: Some(format!(
            "Reversal of {}: {}",
            original.transaction_type, reason
        )),
    };

    let request_hash = req.request_hash();

    // A retry of an already-performed reversal must replay the stored result,
    // so the idempotency lookup happens before the "already reversed" gate.
    if let Some(cached) = lookup_idempotency(pool, &req.idempotency_key).await? {
        if cached.request_hash != request_hash {
            return Err(LedgerError::IdempotencyConflict(idempotency_key));
        }
        info!(
            "Idempotency replay for reversal key: {}",
            req.idempotency_key
        );
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

    if original.status == "REVERSED" {
        return Err(LedgerError::Internal(
            "Transaction already reversed".to_string(),
        ));
    }

    // The reversal journal row, the idempotency record, the outbox events, and
    // the status flip on the original all happen in ONE transaction, so a
    // crash mid-reversal leaves no partially-reversed state and a retry is safe.
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
    // Contract: the ORIGINAL row carries reversal_of = the reversing transaction.
    sqlx::query!(
        "UPDATE transactions SET status = 'REVERSED', reversal_of = $1 WHERE transaction_id = $2",
        txn_id,
        original_txn_id
    )
    .execute(&mut *tx)
    .await?;
    insert_reversal_outbox_event(&mut tx, txn_id, original_txn_id, &reason).await?;

    tx.commit().await?;

    info!("Transaction {} reversed by {}", original_txn_id, txn_id);

    Ok(PostedTransaction {
        transaction_id: txn_id,
        status: "POSTED".to_string(),
        posted_at_unix_ms: Utc::now().timestamp_millis(),
    })
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
            .filter_map(|r| r.ok().and_then(|inner| inner.ok()))
            .collect();

        // Under SERIALIZABLE isolation, contending writers may be aborted
        // (serialization_failure); clients are expected to retry. The ledger
        // invariant is that the materialized balance reflects exactly the
        // set of transactions that committed.
        assert!(
            !successful.is_empty(),
            "at least one transaction should commit"
        );

        let balance = get_balance(&pool, account1).await.unwrap();
        assert_eq!(balance.balance_minor as usize, successful.len() * 1000);
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

    async fn seed_two_accounts(pool: &PgPool, prefix: &str) -> (Uuid, Uuid) {
        let account1 = sqlx::query_as::<_, (Uuid,)>(
            "INSERT INTO accounts (account_number, account_type, owner_ref, currency) VALUES ($1, 'ASSET', 'merchant1', 'INR') RETURNING account_id",
        )
        .bind(format!("{prefix}_A"))
        .fetch_one(pool)
        .await
        .unwrap()
        .0;

        let account2 = sqlx::query_as::<_, (Uuid,)>(
            "INSERT INTO accounts (account_number, account_type, owner_ref, currency) VALUES ($1, 'LIABILITY', 'merchant2', 'INR') RETURNING account_id",
        )
        .bind(format!("{prefix}_B"))
        .fetch_one(pool)
        .await
        .unwrap()
        .0;

        (account1, account2)
    }

    fn transfer_req(key: &str, from: Uuid, to: Uuid, amount_minor: i64) -> TransactionRequest {
        TransactionRequest {
            idempotency_key: key.to_string(),
            transaction_type: "ADJUSTMENT".to_string(),
            reference_id: Some(format!("ref-{key}")),
            entries: vec![
                LedgerEntry {
                    account_id: from,
                    direction: Direction::Debit,
                    amount_minor,
                    currency: "INR".to_string(),
                },
                LedgerEntry {
                    account_id: to,
                    direction: Direction::Credit,
                    amount_minor,
                    currency: "INR".to_string(),
                },
            ],
            narrative: None,
        }
    }

    #[sqlx::test(migrations = "../db/migrations")]
    async fn test_reversal_round_trip(pool: PgPool) {
        let (account1, account2) = seed_two_accounts(&pool, "RRT").await;

        let posted = post_transaction(&pool, transfer_req("rrt-post", account1, account2, 10000))
            .await
            .unwrap();

        let reversal = reverse_transaction(
            &pool,
            posted.transaction_id,
            "rrt-reverse".to_string(),
            "customer requested refund".to_string(),
        )
        .await
        .unwrap();

        assert_ne!(reversal.transaction_id, posted.transaction_id);

        // The original row points at the reversing transaction and is REVERSED…
        let (original_status, original_reversal_of): (String, Option<Uuid>) = sqlx::query_as(
            "SELECT status, reversal_of FROM transactions WHERE transaction_id = $1",
        )
        .bind(posted.transaction_id)
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(original_status, "REVERSED");
        assert_eq!(original_reversal_of, Some(reversal.transaction_id));

        // …while the reversal row is a normal POSTED transaction.
        let (reversal_status,): (String,) =
            sqlx::query_as("SELECT status FROM transactions WHERE transaction_id = $1")
                .bind(reversal.transaction_id)
                .fetch_one(&pool)
                .await
                .unwrap();
        assert_eq!(reversal_status, "POSTED");

        // The compensating entries restore both balances to zero.
        assert_eq!(get_balance(&pool, account1).await.unwrap().balance_minor, 0);
        assert_eq!(get_balance(&pool, account2).await.unwrap().balance_minor, 0);

        // A second reversal (new key) is refused.
        let again = reverse_transaction(
            &pool,
            posted.transaction_id,
            "rrt-reverse-2".to_string(),
            "attempted double reversal".to_string(),
        )
        .await;
        assert!(matches!(again, Err(LedgerError::Internal(_))));
    }

    #[sqlx::test(migrations = "../db/migrations")]
    async fn test_reversal_replay(pool: PgPool) {
        let (account1, account2) = seed_two_accounts(&pool, "REP").await;
        let posted = post_transaction(&pool, transfer_req("rep-post", account1, account2, 2000))
            .await
            .unwrap();

        let first = reverse_transaction(
            &pool,
            posted.transaction_id,
            "rep-reverse".to_string(),
            "reason".to_string(),
        )
        .await
        .unwrap();

        // A retry with the same reversal key + reason is a replay of the same
        // reversal, not an "already reversed" error.
        let second = reverse_transaction(
            &pool,
            posted.transaction_id,
            "rep-reverse".to_string(),
            "reason".to_string(),
        )
        .await
        .unwrap();

        assert_eq!(first.transaction_id, second.transaction_id);
    }

    #[sqlx::test(migrations = "../db/migrations")]
    async fn test_ledger_entries_are_append_only(pool: PgPool) {
        let (account1, account2) = seed_two_accounts(&pool, "WORM").await;
        let posted = post_transaction(&pool, transfer_req("worm-post", account1, account2, 3000))
            .await
            .unwrap();

        let update = sqlx::query(
            "UPDATE ledger_entries SET amount_minor = amount_minor WHERE transaction_id = $1",
        )
        .bind(posted.transaction_id)
        .execute(&pool)
        .await;
        assert!(
            update.is_err(),
            "UPDATE on ledger_entries must be rejected by the WORM trigger"
        );

        let delete = sqlx::query("DELETE FROM ledger_entries WHERE transaction_id = $1")
            .bind(posted.transaction_id)
            .execute(&pool)
            .await;
        assert!(
            delete.is_err(),
            "DELETE on ledger_entries must be rejected by the WORM trigger"
        );
    }

    #[sqlx::test(migrations = "../db/migrations")]
    async fn test_concurrent_same_key(pool: PgPool) {
        let (account1, account2) = seed_two_accounts(&pool, "CSK").await;

        let mut handles = vec![];
        for _ in 0..10 {
            let pool_clone = pool.clone();
            let req = transfer_req("same-key-race", account1, account2, 1000);
            handles.push(tokio::spawn(async move {
                post_transaction(&pool_clone, req).await
            }));
        }

        let results: Vec<_> = futures::future::join_all(handles).await;

        let committed: Vec<Uuid> = results
            .iter()
            .filter_map(|r| {
                r.as_ref()
                    .ok()
                    .and_then(|inner| inner.as_ref().ok().map(|p| p.transaction_id))
            })
            .collect();

        // Exactly one journal row may exist for the key, regardless of how many
        // callers raced.
        let count: i64 = sqlx::query_scalar::<_, i64>(
            "SELECT COUNT(*)::bigint FROM transactions WHERE idempotency_key = 'same-key-race'",
        )
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(
            count, 1,
            "exactly one transaction must be committed for the shared key"
        );

        // Every caller that got a success must agree on the same transaction.
        if let Some(first) = committed.first() {
            assert!(committed.iter().all(|id| id == first));
        }

        // The materialized balance exactly reflects the single committed entry.
        assert_eq!(
            get_balance(&pool, account1).await.unwrap().balance_minor,
            1000
        );
    }

    #[sqlx::test(migrations = "../db/migrations")]
    async fn test_crash_replay_no_double_post(pool: PgPool) {
        let (account1, account2) = seed_two_accounts(&pool, "CRS").await;

        // Simulate a crash mid-transaction: begin, write the journal rows, then
        // roll back without ever committing.
        let mut crashed = pool.begin().await.unwrap();
        let phantom_id = Uuid::new_v4();
        sqlx::query(
            "INSERT INTO transactions (transaction_id, idempotency_key, request_hash, transaction_type) VALUES ($1, $2, $3, $4)",
        )
        .bind(phantom_id)
        .bind("crash-1")
        .bind("phantom")
        .bind("PAYMENT")
        .execute(&mut *crashed)
        .await
        .unwrap();
        sqlx::query(
            "INSERT INTO ledger_entries (transaction_id, account_id, direction, amount_minor, currency) VALUES ($1, $2, 'DEBIT', 10000, 'INR'), ($1, $3, 'CREDIT', 10000, 'INR')",
        )
        .bind(phantom_id)
        .bind(account1)
        .bind(account2)
        .execute(&mut *crashed)
        .await
        .unwrap();
        crashed.rollback().await.unwrap();

        // Nothing from the aborted attempt may be visible.
        let before: i64 = sqlx::query_scalar::<_, i64>(
            "SELECT COUNT(*)::bigint FROM transactions WHERE idempotency_key = 'crash-1'",
        )
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(before, 0);

        // The client retries the exact same request; it must succeed exactly once.
        post_transaction(&pool, transfer_req("crash-1", account1, account2, 10000))
            .await
            .unwrap();

        let after: i64 = sqlx::query_scalar::<_, i64>(
            "SELECT COUNT(*)::bigint FROM transactions WHERE idempotency_key = 'crash-1'",
        )
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(
            after, 1,
            "replay after a crash must leave exactly one transaction"
        );

        assert_eq!(
            get_balance(&pool, account1).await.unwrap().balance_minor,
            10000
        );
    }
}
