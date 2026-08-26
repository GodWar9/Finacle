use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::RwLock;
use tracing::{error, warn};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CircuitState {
    Closed,
    Open,
    HalfOpen,
}

pub struct CircuitBreaker {
    state: Arc<RwLock<CircuitState>>,
    failure_count: Arc<AtomicU32>,
    success_count: Arc<AtomicU32>,
    failure_threshold: u32,
    success_threshold: u32,
    timeout: Duration,
    last_failure_time: Arc<RwLock<Option<Instant>>>,
}

impl CircuitBreaker {
    pub fn new(failure_threshold: u32, success_threshold: u32, timeout: Duration) -> Self {
        Self {
            state: Arc::new(RwLock::new(CircuitState::Closed)),
            failure_count: Arc::new(AtomicU32::new(0)),
            success_count: Arc::new(AtomicU32::new(0)),
            failure_threshold,
            success_threshold,
            timeout,
            last_failure_time: Arc::new(RwLock::new(None)),
        }
    }

    pub async fn call<F, T, E>(&self, f: F) -> Result<T, CircuitBreakerError<E>>
    where
        F: FnOnce() -> Result<T, E>,
    {
        if self.is_open().await {
            return Err(CircuitBreakerError::Open);
        }

        match f() {
            Ok(result) => {
                self.on_success().await;
                Ok(result)
            }
            Err(e) => {
                self.on_failure().await;
                Err(CircuitBreakerError::Inner(e))
            }
        }
    }

    async fn is_open(&self) -> bool {
        let state = *self.state.read().await;
        match state {
            CircuitState::Closed => false,
            CircuitState::Open => {
                let last_failure = *self.last_failure_time.read().await;
                if let Some(last) = last_failure {
                    if last.elapsed() >= self.timeout {
                        *self.state.write().await = CircuitState::HalfOpen;
                        return false;
                    }
                }
                true
            }
            CircuitState::HalfOpen => false,
        }
    }

    async fn on_success(&self) {
        let state = *self.state.read().await;
        match state {
            CircuitState::HalfOpen => {
                let successes = self.success_count.fetch_add(1, Ordering::SeqCst) + 1;
                if successes >= self.success_threshold {
                    *self.state.write().await = CircuitState::Closed;
                    self.failure_count.store(0, Ordering::SeqCst);
                    self.success_count.store(0, Ordering::SeqCst);
                }
            }
            CircuitState::Closed => {
                self.failure_count.store(0, Ordering::SeqCst);
            }
            _ => {}
        }
    }

    async fn on_failure(&self) {
        let state = *self.state.read().await;
        match state {
            CircuitState::Closed => {
                let failures = self.failure_count.fetch_add(1, Ordering::SeqCst) + 1;
                if failures >= self.failure_threshold {
                    *self.state.write().await = CircuitState::Open;
                    *self.last_failure_time.write().await = Some(Instant::now());
                    warn!("Circuit breaker opened after {} failures", failures);
                }
            }
            CircuitState::HalfOpen => {
                *self.state.write().await = CircuitState::Open;
                *self.last_failure_time.write().await = Some(Instant::now());
                self.success_count.store(0, Ordering::SeqCst);
                error!("Circuit breaker reopened after failure in half-open state");
            }
            _ => {}
        }
    }

    pub async fn get_state(&self) -> CircuitState {
        *self.state.read().await
    }
}

#[derive(Debug, thiserror::Error)]
pub enum CircuitBreakerError<E> {
    #[error("Circuit breaker is open")]
    Open,
    #[error("Inner error: {0}")]
    Inner(E),
}
