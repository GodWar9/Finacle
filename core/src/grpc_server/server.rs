use tonic::{Request, Response, Status};
use uuid::Uuid;
use std::future::Future;
use crate::ledger::domain::{TransactionRequest, LedgerEntry, Direction, PostedTransaction, LedgerError, GetBalanceResponse as DomainGetBalanceResponse};
use crate::ledger::service::{post_transaction, get_balance, reverse_transaction};
use sqlx::PgPool;

// Manual gRPC types to avoid protobuf compilation
pub mod ledger {
    #[derive(Clone, PartialEq, ::prost::Message)]
    pub struct LedgerEntry {
        #[prost(string, tag = "1")]
        pub account_id: String,
        #[prost(string, tag = "2")]
        pub direction: String,
        #[prost(int64, tag = "3")]
        pub amount_minor: i64,
        #[prost(string, tag = "4")]
        pub currency: String,
    }

    #[derive(Clone, PartialEq, ::prost::Message)]
    pub struct PostTransactionRequest {
        #[prost(string, tag = "1")]
        pub idempotency_key: String,
        #[prost(string, tag = "2")]
        pub transaction_type: String,
        #[prost(string, tag = "3")]
        pub reference_id: String,
        #[prost(message, repeated, tag = "4")]
        pub entries: Vec<LedgerEntry>,
        #[prost(string, tag = "5")]
        pub narrative: String,
    }

    #[derive(Clone, PartialEq, ::prost::Message)]
    pub struct PostTransactionResponse {
        #[prost(string, tag = "1")]
        pub transaction_id: String,
        #[prost(string, tag = "2")]
        pub status: String,
        #[prost(int64, tag = "3")]
        pub posted_at_unix_ms: i64,
    }

    #[derive(Clone, PartialEq, ::prost::Message)]
    pub struct GetBalanceRequest {
        #[prost(string, tag = "1")]
        pub account_id: String,
    }

    #[derive(Clone, PartialEq, ::prost::Message)]
    pub struct GetBalanceResponse {
        #[prost(string, tag = "1")]
        pub account_id: String,
        #[prost(int64, tag = "2")]
        pub balance_minor: i64,
        #[prost(string, tag = "3")]
        pub currency: String,
        #[prost(int64, tag = "4")]
        pub as_of_unix_ms: i64,
    }

    #[derive(Clone, PartialEq, ::prost::Message)]
    pub struct ReverseTransactionRequest {
        #[prost(string, tag = "1")]
        pub transaction_id: String,
        #[prost(string, tag = "2")]
        pub idempotency_key: String,
        #[prost(string, tag = "3")]
        pub reason: String,
    }
}

mod ledger_service {
    use super::ledger::*;
    use tonic::{Request, Response, Status};
    use std::future::Future;

    pub trait LedgerCore: Send + Sync {
        fn post_transaction(&self, request: Request<PostTransactionRequest>) -> impl Future<Output = Result<Response<PostTransactionResponse>, Status>> + Send;
        fn get_balance(&self, request: Request<GetBalanceRequest>) -> impl Future<Output = Result<Response<GetBalanceResponse>, Status>> + Send;
        fn reverse_transaction(&self, request: Request<ReverseTransactionRequest>) -> impl Future<Output = Result<Response<PostTransactionResponse>, Status>> + Send;
    }

    pub struct LedgerCoreServer<T> {
        inner: T,
    }

    impl<T: LedgerCore> LedgerCoreServer<T> {
        pub fn new(inner: T) -> Self {
            Self { inner }
        }
    }
}

use ledger::*;
use ledger_service::LedgerCore;
use ledger_service::LedgerCoreServer;

#[derive(Debug, Clone)]
pub struct LedgerGrpcServer {
    pool: PgPool,
}

impl LedgerGrpcServer {
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }
    
    pub fn into_server(self) -> LedgerCoreServer<Self> {
        LedgerCoreServer::new(self)
    }
}

impl LedgerCore for LedgerGrpcServer {
    fn post_transaction(&self, request: Request<PostTransactionRequest>) -> impl Future<Output = Result<Response<PostTransactionResponse>, Status>> + Send {
        let pool = self.pool.clone();
        async move {
            let req = request.into_inner();
            
            let entries: Result<Vec<LedgerEntry>, Status> = req.entries.into_iter().map(|e| {
                let direction = match e.direction.as_str() {
                    "DEBIT" => Direction::Debit,
                    "CREDIT" => Direction::Credit,
                    _ => return Err(Status::invalid_argument("invalid direction")),
                };
                Ok(LedgerEntry {
                    account_id: Uuid::parse_str(&e.account_id).map_err(|_| Status::invalid_argument("invalid account_id"))?,
                    direction,
                    amount_minor: e.amount_minor,
                    currency: e.currency,
                })
            }).collect();
            
            let txn_req = TransactionRequest {
                idempotency_key: req.idempotency_key,
                transaction_type: req.transaction_type,
                reference_id: if req.reference_id.is_empty() { None } else { Some(req.reference_id) },
                entries: entries?,
                narrative: if req.narrative.is_empty() { None } else { Some(req.narrative) },
            };
            
            match post_transaction(&pool, txn_req).await {
                Ok(result) => Ok(Response::new(PostTransactionResponse {
                    transaction_id: result.transaction_id.to_string(),
                    status: result.status,
                    posted_at_unix_ms: result.posted_at_unix_ms,
                })),
                Err(LedgerError::Unbalanced { debit, credit }) => Err(Status::invalid_argument(format!("unbalanced: debit {} != credit {}", debit, credit))),
                Err(LedgerError::InvalidAccount(id)) => Err(Status::not_found(format!("account not found: {}", id))),
                Err(LedgerError::IdempotencyConflict(key)) => Err(Status::already_exists(format!("idempotency conflict: {}", key))),
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
    }
    
    fn get_balance(&self, request: Request<GetBalanceRequest>) -> impl Future<Output = Result<Response<GetBalanceResponse>, Status>> + Send {
        let pool = self.pool.clone();
        async move {
            let req = request.into_inner();
            let account_id = Uuid::parse_str(&req.account_id).map_err(|_| Status::invalid_argument("invalid account_id"))?;
            
            match get_balance(&pool, account_id).await {
                Ok(result) => Ok(Response::new(GetBalanceResponse {
                    account_id: result.account_id.to_string(),
                    balance_minor: result.balance_minor,
                    currency: result.currency,
                    as_of_unix_ms: result.as_of_unix_ms,
                })),
                Err(LedgerError::InvalidAccount(id)) => Err(Status::not_found(format!("account not found: {}", id))),
                Err(e) => Err(Status::internal(format!("internal error: {}", e))),
            }
        }
    }
    
    fn reverse_transaction(&self, request: Request<ReverseTransactionRequest>) -> impl Future<Output = Result<Response<PostTransactionResponse>, Status>> + Send {
        let pool = self.pool.clone();
        async move {
            let req = request.into_inner();
            let transaction_id = Uuid::parse_str(&req.transaction_id).map_err(|_| Status::invalid_argument("invalid transaction_id"))?;
            
            match reverse_transaction(&pool, transaction_id, req.idempotency_key, req.reason).await {
                Ok(result) => Ok(Response::new(PostTransactionResponse {
                    transaction_id: result.transaction_id.to_string(),
                    status: result.status,
                    posted_at_unix_ms: result.posted_at_unix_ms,
                })),
                Err(LedgerError::TransactionNotFound(id)) => Err(Status::not_found(format!("transaction not found: {}", id))),
                Err(LedgerError::IdempotencyConflict(key)) => Err(Status::already_exists(format!("idempotency conflict: {}", key))),
                Err(e) => Err(Status::internal(format!("internal error: {}", e))),
            }
        }
    }
}