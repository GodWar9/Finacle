pub mod ledger {
    pub mod auditor;
    pub mod circuit_breaker;
    pub mod domain;
    pub mod idempotency;
    pub mod outbox;
    pub mod poller;
    pub mod service;
    pub mod tracing;
}

pub mod pb {
    tonic::include_proto!("ledger.v1");
}

pub mod grpc_server;
