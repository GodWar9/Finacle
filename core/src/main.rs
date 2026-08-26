#![allow(clippy::result_large_err)]

use sqlx::PgPool;
use std::net::SocketAddr;
use tokio::signal;
#[cfg(feature = "kafka")]
use tracing::error;
use tracing::{info, warn};
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

use ledger_core::grpc_server::LedgerGrpcServer;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::registry()
        .with(tracing_subscriber::EnvFilter::new(
            std::env::var("RUST_LOG").unwrap_or_else(|_| "info".to_string()),
        ))
        .with(tracing_subscriber::fmt::layer())
        .init();

    let database_url = std::env::var("DATABASE_URL").expect("DATABASE_URL must be set");
    let kafka_brokers =
        std::env::var("KAFKA_BROKERS").unwrap_or_else(|_| "localhost:9092".to_string());
    let grpc_addr: SocketAddr = std::env::var("GRPC_ADDR")
        .unwrap_or_else(|_| "0.0.0.0:50051".to_string())
        .parse()?;

    let pool = PgPool::connect(&database_url).await?;
    info!("Connected to database");

    sqlx::migrate!("../db/migrations").run(&pool).await?;
    info!("Migrations applied");

    let grpc_server = LedgerGrpcServer::new(pool.clone());

    #[cfg(feature = "kafka")]
    {
        let pool_for_poller = pool.clone();
        let kafka_brokers_clone = kafka_brokers.clone();
        tokio::spawn(async move {
            match ledger_core::ledger::poller::OutboxPoller::new(
                pool_for_poller,
                &kafka_brokers_clone,
                100,
                1000,
            ) {
                Ok(poller) => {
                    if let Err(e) = poller.run().await {
                        error!("Outbox poller failed: {}", e);
                    }
                }
                Err(e) => {
                    warn!("Failed to create outbox poller: {}", e);
                }
            }
        });
    }

    #[cfg(not(feature = "kafka"))]
    {
        warn!("Kafka feature not enabled - outbox poller disabled");
    }

    let pool_for_auditor = pool.clone();
    tokio::spawn(async move {
        ledger_core::ledger::auditor::run_balance_auditor_scheduled(pool_for_auditor, 24).await;
    });

    info!("Starting gRPC server on {}", grpc_addr);

    tonic::transport::Server::builder()
        .add_service(grpc_server.into_server())
        .serve_with_shutdown(grpc_addr, async {
            signal::ctrl_c().await.expect("failed to listen for ctrl-c");
            info!("Shutdown signal received");
        })
        .await?;

    Ok(())
}
