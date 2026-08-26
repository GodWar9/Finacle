// tonic-generated service methods return Result<_, tonic::Status>, whose
// large size trips this lint; boxing Status is unidiomatic for tonic apps.
#![allow(clippy::result_large_err)]

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
