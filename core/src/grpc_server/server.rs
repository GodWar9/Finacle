use crate::ledger::domain::{Direction, LedgerEntry, LedgerError, TransactionRequest};
use crate::ledger::service::{get_balance, post_transaction, reverse_transaction};
use crate::pb::{ledger_core_server::LedgerCore, *};
use sha2::{Digest, Sha256};
use sqlx::PgPool;
use tonic::{Request, Response, Status};
use uuid::Uuid;

#[derive(Debug, Clone)]
pub struct LedgerGrpcServer {
    pool: PgPool,
}

impl LedgerGrpcServer {
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }
}

#[tonic::async_trait]
impl LedgerCore for LedgerGrpcServer {
    async fn post_transaction(
        &self,
        request: Request<PostTransactionRequest>,
    ) -> Result<Response<PostTransactionResponse>, Status> {
        let pool = self.pool.clone();
        let req = request.into_inner();
        if req.merchant_id.trim().is_empty() || req.merchant_id.len() > 200 {
            return Err(Status::unauthenticated("merchant identity required"));
        }

        let entries: Result<Vec<LedgerEntry>, Status> = req
            .entries
            .into_iter()
            .map(|e| {
                let direction = match e.direction.as_str() {
                    "DEBIT" => Direction::Debit,
                    "CREDIT" => Direction::Credit,
                    _ => return Err(Status::invalid_argument("invalid direction")),
                };
                Ok(LedgerEntry {
                    account_id: Uuid::parse_str(&e.account_id)
                        .map_err(|_| Status::invalid_argument("invalid account_id"))?,
                    direction,
                    amount_minor: e.amount_minor,
                    currency: e.currency,
                })
            })
            .collect();

        let entries = entries?;
        for entry in &entries {
            authorize_account(&pool, entry.account_id, &req.merchant_id).await?;
        }
        let txn_req = TransactionRequest {
            idempotency_key: scoped_key(&req.merchant_id, "post", &req.idempotency_key),
            transaction_type: req.transaction_type,
            reference_id: if req.reference_id.is_empty() {
                None
            } else {
                Some(req.reference_id)
            },
            entries,
            narrative: if req.narrative.is_empty() {
                None
            } else {
                Some(req.narrative)
            },
        };

        match post_transaction(&pool, txn_req).await {
            Ok(result) => Ok(Response::new(PostTransactionResponse {
                transaction_id: result.transaction_id.to_string(),
                status: result.status,
                posted_at_unix_ms: result.posted_at_unix_ms,
            })),
            Err(LedgerError::Unbalanced { debit, credit }) => Err(Status::invalid_argument(
                format!("unbalanced: debit {} != credit {}", debit, credit),
            )),
            Err(LedgerError::InvalidAccount(id)) => {
                Err(Status::not_found(format!("account not found: {}", id)))
            }
            Err(LedgerError::IdempotencyConflict(key)) => Err(Status::already_exists(format!(
                "idempotency conflict: {}",
                key
            ))),
            Err(LedgerError::Database(e)) => {
                if e.to_string().contains("serialization_failure") {
                    Err(Status::aborted("serialization failure, retry"))
                } else {
                    Err(Status::internal(format!("database error: {}", e)))
                }
            }
            Err(e) => Err(Status::internal(format!("internal error: {}", e))),
        }
    }

    async fn get_balance(
        &self,
        request: Request<GetBalanceRequest>,
    ) -> Result<Response<GetBalanceResponse>, Status> {
        let pool = self.pool.clone();
        let req = request.into_inner();
        if req.merchant_id.trim().is_empty() || req.merchant_id.len() > 200 {
            return Err(Status::unauthenticated("merchant identity required"));
        }
        let account_id = Uuid::parse_str(&req.account_id)
            .map_err(|_| Status::invalid_argument("invalid account_id"))?;

        authorize_account(&pool, account_id, &req.merchant_id).await?;
        match get_balance(&pool, account_id).await {
            Ok(result) => Ok(Response::new(GetBalanceResponse {
                account_id: result.account_id.to_string(),
                balance_minor: result.balance_minor,
                currency: result.currency,
                as_of_unix_ms: result.as_of_unix_ms,
            })),
            Err(LedgerError::InvalidAccount(id)) => {
                Err(Status::not_found(format!("account not found: {}", id)))
            }
            Err(e) => Err(Status::internal(format!("internal error: {}", e))),
        }
    }

    async fn reverse_transaction(
        &self,
        request: Request<ReverseTransactionRequest>,
    ) -> Result<Response<PostTransactionResponse>, Status> {
        let pool = self.pool.clone();
        let req = request.into_inner();
        if req.merchant_id.trim().is_empty() || req.merchant_id.len() > 200 {
            return Err(Status::unauthenticated("merchant identity required"));
        }
        let transaction_id = Uuid::parse_str(&req.transaction_id)
            .map_err(|_| Status::invalid_argument("invalid transaction_id"))?;

        let owned: bool = sqlx::query_scalar(
            "SELECT EXISTS (SELECT 1 FROM ledger_entries WHERE transaction_id = $1) AND NOT EXISTS (SELECT 1 FROM ledger_entries e JOIN accounts a ON a.account_id = e.account_id WHERE e.transaction_id = $1 AND a.merchant_id IS DISTINCT FROM $2)"
        ).bind(transaction_id).bind(&req.merchant_id).fetch_one(&pool).await
            .map_err(|_| Status::internal("ownership lookup failed"))?;
        if !owned {
            return Err(Status::not_found("transaction not found"));
        }
        let operation = format!("reverse:{transaction_id}");
        let key = scoped_key(&req.merchant_id, &operation, &req.idempotency_key);
        match reverse_transaction(&pool, transaction_id, key, req.reason).await {
            Ok(result) => Ok(Response::new(PostTransactionResponse {
                transaction_id: result.transaction_id.to_string(),
                status: result.status,
                posted_at_unix_ms: result.posted_at_unix_ms,
            })),
            Err(LedgerError::TransactionNotFound(id)) => {
                Err(Status::not_found(format!("transaction not found: {}", id)))
            }
            Err(LedgerError::IdempotencyConflict(key)) => Err(Status::already_exists(format!(
                "idempotency conflict: {}",
                key
            ))),
            Err(e) => Err(Status::internal(format!("internal error: {}", e))),
        }
    }
}

async fn authorize_account(pool: &PgPool, id: Uuid, merchant: &str) -> Result<(), Status> {
    let owned: bool = sqlx::query_scalar(
        "SELECT EXISTS (SELECT 1 FROM accounts WHERE account_id = $1 AND merchant_id = $2)",
    )
    .bind(id)
    .bind(merchant)
    .fetch_one(pool)
    .await
    .map_err(|_| Status::internal("ownership lookup failed"))?;
    if !owned {
        return Err(Status::not_found("account not found"));
    }
    Ok(())
}

fn scoped_key(merchant: &str, operation: &str, key: &str) -> String {
    let encoded = serde_json::to_vec(&(merchant, operation, key)).expect("strings serialize");
    format!("tenant-v1:{:x}", Sha256::digest(encoded))
}

#[cfg(test)]
mod tenant_tests {
    use super::*;
    use sqlx::Executor;

    // Only the ledger migrations are needed; this test also runs on PostgreSQL
    // installations without the optional RAG pgvector extension.
    #[sqlx::test(migrations = false)]
    async fn rpc_ownership_and_retry_scopes(pool: PgPool) {
        pool.execute(include_str!(
            "../../../db/migrations/001_initial_schema.sql"
        ))
        .await
        .unwrap();
        pool.execute(include_str!(
            "../../../db/migrations/007_worm_enforcement.sql"
        ))
        .await
        .unwrap();
        pool.execute(include_str!(
            "../../../db/migrations/008_tenant_ownership.sql"
        ))
        .await
        .unwrap();
        let mut ids = Vec::new();
        for (index, merchant) in ["merchant_a", "merchant_a", "merchant_b", "merchant_b"]
            .iter()
            .enumerate()
        {
            let id: Uuid = sqlx::query_scalar("INSERT INTO accounts(account_number,account_type,owner_ref,merchant_id) VALUES($1,'ASSET','customer',$2) RETURNING account_id")
                .bind(format!("account-{index}")).bind(merchant).fetch_one(&pool).await.unwrap();
            ids.push(id);
        }
        let server = LedgerGrpcServer::new(pool.clone());
        let make_request = |merchant: &str, debit: Uuid, credit: Uuid| PostTransactionRequest {
            merchant_id: merchant.into(),
            idempotency_key: "same-client-key".into(),
            transaction_type: "PAYMENT".into(),
            reference_id: String::new(),
            narrative: "tenant test".into(),
            entries: vec![
                crate::pb::LedgerEntry {
                    account_id: debit.to_string(),
                    direction: "DEBIT".into(),
                    amount_minor: 100,
                    currency: "INR".into(),
                },
                crate::pb::LedgerEntry {
                    account_id: credit.to_string(),
                    direction: "CREDIT".into(),
                    amount_minor: 100,
                    currency: "INR".into(),
                },
            ],
        };
        let first = server
            .post_transaction(Request::new(make_request("merchant_a", ids[0], ids[1])))
            .await
            .unwrap()
            .into_inner();
        let replay = server
            .post_transaction(Request::new(make_request("merchant_a", ids[0], ids[1])))
            .await
            .unwrap()
            .into_inner();
        assert_eq!(first.transaction_id, replay.transaction_id);
        let second = server
            .post_transaction(Request::new(make_request("merchant_b", ids[2], ids[3])))
            .await
            .unwrap()
            .into_inner();
        assert_ne!(first.transaction_id, second.transaction_id);
        let denied = server
            .post_transaction(Request::new(make_request("merchant_a", ids[0], ids[2])))
            .await
            .unwrap_err();
        assert_eq!(denied.code(), tonic::Code::NotFound);
        let denied = server
            .get_balance(Request::new(GetBalanceRequest {
                account_id: ids[2].to_string(),
                merchant_id: "merchant_a".into(),
            }))
            .await
            .unwrap_err();
        assert_eq!(denied.code(), tonic::Code::NotFound);
        let denied = server
            .get_balance(Request::new(GetBalanceRequest {
                account_id: ids[0].to_string(),
                merchant_id: String::new(),
            }))
            .await
            .unwrap_err();
        assert_eq!(denied.code(), tonic::Code::Unauthenticated);
        let denied = server
            .reverse_transaction(Request::new(ReverseTransactionRequest {
                transaction_id: second.transaction_id,
                merchant_id: "merchant_a".into(),
                idempotency_key: "reverse-key".into(),
                reason: "test".into(),
            }))
            .await
            .unwrap_err();
        assert_eq!(denied.code(), tonic::Code::NotFound);
        server
            .reverse_transaction(Request::new(ReverseTransactionRequest {
                transaction_id: first.transaction_id,
                merchant_id: "merchant_a".into(),
                idempotency_key: "reverse-key".into(),
                reason: "test".into(),
            }))
            .await
            .unwrap();
        let balance = server
            .get_balance(Request::new(GetBalanceRequest {
                account_id: ids[0].to_string(),
                merchant_id: "merchant_a".into(),
            }))
            .await
            .unwrap()
            .into_inner();
        assert_eq!(balance.balance_minor, 0);
    }
}
