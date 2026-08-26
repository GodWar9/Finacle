use crate::ledger::domain::{
    Direction, LedgerEntry, LedgerError, PostedTransaction, TransactionRequest,
};
use crate::ledger::service::{get_balance, post_transaction, reverse_transaction};
use crate::pb::{ledger_core_server::LedgerCore, *};
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

        let txn_req = TransactionRequest {
            idempotency_key: req.idempotency_key,
            transaction_type: req.transaction_type,
            reference_id: if req.reference_id.is_empty() {
                None
            } else {
                Some(req.reference_id)
            },
            entries: entries?,
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
        let account_id = Uuid::parse_str(&req.account_id)
            .map_err(|_| Status::invalid_argument("invalid account_id"))?;

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
        let transaction_id = Uuid::parse_str(&req.transaction_id)
            .map_err(|_| Status::invalid_argument("invalid transaction_id"))?;

        match reverse_transaction(&pool, transaction_id, req.idempotency_key, req.reason).await {
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
