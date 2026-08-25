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

pub mod pb {
    tonic::include_proto!("ledger.v1");
}

pub mod grpc_server;