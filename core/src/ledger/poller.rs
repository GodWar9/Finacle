#[cfg(feature = "kafka")]
use chrono::Utc;
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
    ) -> Result<Self, Box<dyn std::error::Error>> {
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

    pub async fn run(&self) -> Result<(), Box<dyn std::error::Error>> {
        info!("Starting outbox poller");
        loop {
            if let Err(e) = self.poll_once().await {
                error!("Outbox poller error: {}", e);
            }
            tokio::time::sleep(tokio::time::Duration::from_millis(self.poll_interval_ms)).await;
        }
    }

    async fn poll_once(&self) -> Result<usize, Box<dyn std::error::Error>> {
        let mut tx: Transaction<'_, Postgres> = self.pool.begin().await?;

        let rows = sqlx::query!(
            r#"
            SELECT event_id, topic, payload_json
            FROM outbox_events
            WHERE published = false
            ORDER BY created_at
            LIMIT $1
            FOR UPDATE SKIP LOCKED
            "#,
            self.batch_size as i64
        )
        .fetch_all(&mut *tx)
        .await?;

        let count = rows.len();

        for row in rows {
            let record = FutureRecord::to(&row.topic)
                .payload(&row.payload_json.to_string())
                .key(&row.event_id.to_string());

            match self
                .producer
                .send(record, tokio::time::Duration::from_secs(5))
                .await
            {
                Ok(_) => {
                    sqlx::query!(
                        "UPDATE outbox_events SET published = true WHERE event_id = $1",
                        row.event_id
                    )
                    .execute(&mut *tx)
                    .await?;
                }
                Err((e, _)) => {
                    warn!("Failed to publish event {}: {}", row.event_id, e);
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
    ) -> Result<Self, Box<dyn std::error::Error>> {
        Err("Kafka feature not enabled".into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_poller_creation() {
        // This would need a real Kafka instance to test
    }
}
