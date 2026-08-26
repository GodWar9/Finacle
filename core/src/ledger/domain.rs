use serde::{Deserialize, Serialize};
use thiserror::Error;
use uuid::Uuid;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum Direction {
    Debit,
    Credit,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LedgerEntry {
    pub account_id: Uuid,
    pub direction: Direction,
    pub amount_minor: i64,
    pub currency: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TransactionRequest {
    pub idempotency_key: String,
    pub transaction_type: String,
    pub reference_id: Option<String>,
    pub entries: Vec<LedgerEntry>,
    pub narrative: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PostedTransaction {
    pub transaction_id: Uuid,
    pub status: String,
    pub posted_at_unix_ms: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GetBalanceRequest {
    pub account_id: Uuid,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GetBalanceResponse {
    pub account_id: Uuid,
    pub balance_minor: i64,
    pub currency: String,
    pub as_of_unix_ms: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReverseTransactionRequest {
    pub transaction_id: Uuid,
    pub idempotency_key: String,
    pub reason: String,
}

#[derive(Error, Debug)]
pub enum LedgerError {
    #[error("unbalanced transaction: debit {debit} != credit {credit}")]
    Unbalanced { debit: i64, credit: i64 },

    #[error("account {0} not found or not ACTIVE")]
    InvalidAccount(Uuid),

    #[error("idempotency key {0} already used with a different payload")]
    IdempotencyConflict(String),

    #[error("transaction {0} not found")]
    TransactionNotFound(Uuid),

    #[error("database error: {0}")]
    Database(#[from] sqlx::Error),

    #[error("kafka error: {0}")]
    Kafka(String),

    #[error("serialization error: {0}")]
    Serialization(String),

    #[error("internal error: {0}")]
    Internal(String),
}

impl TransactionRequest {
    pub fn request_hash(&self) -> String {
        use sha2::{Digest, Sha256};
        let canonical = serde_json::to_string(self).unwrap_or_default();
        let mut hasher = Sha256::new();
        hasher.update(canonical.as_bytes());
        hex::encode(hasher.finalize())
    }

    pub fn validate_balanced(&self) -> Result<(), LedgerError> {
        let debit_total: i64 = self
            .entries
            .iter()
            .filter(|e| e.direction == Direction::Debit)
            .map(|e| e.amount_minor)
            .sum();
        let credit_total: i64 = self
            .entries
            .iter()
            .filter(|e| e.direction == Direction::Credit)
            .map(|e| e.amount_minor)
            .sum();

        if debit_total != credit_total {
            return Err(LedgerError::Unbalanced {
                debit: debit_total,
                credit: credit_total,
            });
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_balanced_transaction() {
        let req = TransactionRequest {
            idempotency_key: "test-key".to_string(),
            transaction_type: "PAYMENT".to_string(),
            reference_id: Some("ref-1".to_string()),
            entries: vec![
                LedgerEntry {
                    account_id: Uuid::new_v4(),
                    direction: Direction::Debit,
                    amount_minor: 10000,
                    currency: "INR".to_string(),
                },
                LedgerEntry {
                    account_id: Uuid::new_v4(),
                    direction: Direction::Credit,
                    amount_minor: 10000,
                    currency: "INR".to_string(),
                },
            ],
            narrative: None,
        };
        assert!(req.validate_balanced().is_ok());
    }

    #[test]
    fn test_unbalanced_transaction() {
        let req = TransactionRequest {
            idempotency_key: "test-key".to_string(),
            transaction_type: "PAYMENT".to_string(),
            reference_id: Some("ref-1".to_string()),
            entries: vec![
                LedgerEntry {
                    account_id: Uuid::new_v4(),
                    direction: Direction::Debit,
                    amount_minor: 10000,
                    currency: "INR".to_string(),
                },
                LedgerEntry {
                    account_id: Uuid::new_v4(),
                    direction: Direction::Credit,
                    amount_minor: 5000,
                    currency: "INR".to_string(),
                },
            ],
            narrative: None,
        };
        assert!(req.validate_balanced().is_err());
    }

    #[test]
    fn test_request_hash_deterministic() {
        let req = TransactionRequest {
            idempotency_key: "test-key".to_string(),
            transaction_type: "PAYMENT".to_string(),
            reference_id: Some("ref-1".to_string()),
            entries: vec![LedgerEntry {
                account_id: Uuid::parse_str("550e8400-e29b-41d4-a716-446655440000").unwrap(),
                direction: Direction::Debit,
                amount_minor: 10000,
                currency: "INR".to_string(),
            }],
            narrative: None,
        };
        let hash1 = req.request_hash();
        let hash2 = req.request_hash();
        assert_eq!(hash1, hash2);
    }
}
