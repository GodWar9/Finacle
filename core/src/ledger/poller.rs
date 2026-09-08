#[cfg(feature = "kafka")]
use rdkafka::producer::{FutureProducer, FutureRecord};
#[cfg(feature = "kafka")]
use rdkafka::ClientConfig;
#[cfg(feature = "kafka")]
use sqlx::{PgPool, Postgres, Transaction};
#[cfg(feature = "kafka")]
use tracing::{error, info, warn};
#[cfg(feature = "kafka")]
use uuid::Uuid;

#[cfg(feature = "kafka")]
pub struct OutboxPoller {
    pool: PgPool,
    producer: FutureProducer,
    batch_size: usize,
    poll_interval_ms: u64,
}

#[cfg(feature = "kafka")]
impl OutboxPoller {
    pub fn new(
        pool: PgPool,
        kafka_brokers: &str,
        batch_size: usize,
        poll_interval_ms: u64,
    ) -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        let producer: FutureProducer = ClientConfig::new()
            .set("bootstrap.servers", kafka_brokers)
            .set("message.timeout.ms", "5000")
            .create()?;

        Ok(Self {
            pool,
            producer,
            batch_size,
            poll_interval_ms,
        })
    }

    pub async fn run(&self) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        info!("Starting outbox poller");
        loop {
            if let Err(e) = self.poll_once().await {
                error!("Outbox poller error: {}", e);
            }
            tokio::time::sleep(tokio::time::Duration::from_millis(self.poll_interval_ms)).await;
        }
    }

    async fn poll_once(&self) -> Result<usize, Box<dyn std::error::Error + Send + Sync>> {
        let mut tx: Transaction<'_, Postgres> = self.pool.begin().await?;

        // Runtime-checked queries (not sqlx::query!) so the offline .sqlx cache
        // stays feature-agnostic: these are only compiled with the `kafka`
        // feature, and the committed cache is prepared without it.
        let sql = "
            SELECT event_id, topic, payload_json
            FROM outbox_events
            WHERE published = false
            ORDER BY created_at
            LIMIT $1
            FOR UPDATE SKIP LOCKED
        ";
        let rows = sqlx::query(sql)
            .bind(self.batch_size as i64)
            .fetch_all(&mut *tx)
            .await?;

        let count = rows.len();

        for row in rows {
            use sqlx::Row;
            let event_id: Uuid = row.get("event_id");
            let topic: String = row.get("topic");
            let payload_json: serde_json::Value = row.get("payload_json");

            let event_id_str = event_id.to_string();
            let payload = payload_json.to_string();
            let record = FutureRecord::to(&topic)
                .payload(&payload)
                .key(&event_id_str);

            match self
                .producer
                .send(record, tokio::time::Duration::from_secs(5))
                .await
            {
                Ok(_) => {
                    sqlx::query("UPDATE outbox_events SET published = true WHERE event_id = $1")
                        .bind(event_id)
                        .execute(&mut *tx)
                        .await?;
                }
                Err((e, _)) => {
                    warn!("Failed to publish event {}: {}", event_id, e);
                }
            }
        }

        tx.commit().await?;

        if count > 0 {
            info!("Published {} outbox events to Kafka", count);
        }

        Ok(count)
    }
}

#[cfg(not(feature = "kafka"))]
pub struct OutboxPoller;

#[cfg(not(feature = "kafka"))]
impl OutboxPoller {
    pub fn new(
        _pool: sqlx::PgPool,
        _kafka_brokers: &str,
        _batch_size: usize,
        _poll_interval_ms: u64,
    ) -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        Err("Kafka feature not enabled".into())
    }
}

#[cfg(test)]
mod tests {
    use super::OutboxPoller;

    #[cfg(not(feature = "kafka"))]
    #[tokio::test]
    async fn test_poller_stub_rejects_without_kafka() {
        let pool = sqlx::PgPool::connect_lazy("postgresql://localhost:5432/ledger")
            .expect("lazy pool creation should not require a connection");
        match OutboxPoller::new(pool, "localhost:9092", 10, 1000) {
            Ok(_) => panic!("non-kafka stub should fail"),
            Err(e) => assert!(
                e.to_string().contains("Kafka feature not enabled"),
                "unexpected error: {e}"
            ),
        }
    }
}
