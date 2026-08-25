pub mod ledger {
    pub mod domain;
    pub mod service;
    pub mod idempotency;
    pub mod outbox;
    pub mod poller;
    pub mod auditor;
    pub mod tracing;
    pub mod circuit_breaker;
}

pub mod grpc_server;