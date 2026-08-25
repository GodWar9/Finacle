use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

pub fn init_tracing() {
    tracing_subscriber::registry()
        .with(tracing_subscriber::EnvFilter::new(
            std::env::var("RUST_LOG").unwrap_or_else(|_| "info".to_string()),
        ))
        .with(tracing_subscriber::fmt::layer())
        .init();
}

pub fn extract_trace_context(_headers: &std::collections::HashMap<String, String>) {
    // Placeholder for trace context extraction
}

pub fn inject_trace_context(_headers: &mut std::collections::HashMap<String, String>) {
    // Placeholder for trace context injection
}

#[macro_export]
macro_rules! traced_span {
    ($name:expr) => {
        tracing::info_span!($name)
    };
    ($name:expr, $($field:tt)*) => {
        tracing::info_span!($name, $($field)*)
    };
}