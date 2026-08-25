pub mod ledger {
    pub mod domain;
    pub mod service;
    pub mod idempotency;
    pub mod outbox;
    pub mod poller;
}

pub mod grpc_server;