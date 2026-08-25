use tracing::{span, Level};
use tracing_opentelemetry::OpenTelemetrySpanExt;
use opentelemetry::{global, trace::TraceContextExt, Context};
use opentelemetry_sdk::trace::TracerProvider;
use opentelemetry_otlp::{WithExportConfig, Protocol};
use std::time::Duration;

pub fn init_tracing() -> Result<opentelemetry_sdk::trace::TracerProvider, Box<dyn std::error::Error + Send + Sync>> {
    let otlp_exporter = opentelemetry_otlp::new_exporter()
        .tonic()
        .with_endpoint("http://jaeger:4317")
        .with_protocol(Protocol::Grpc)
        .with_timeout(Duration::from_secs(10));
    
    let tracer_provider = TracerProvider::builder()
        .with_batch_exporter(otlp_exporter, opentelemetry_sdk::runtime::Tokio)
        .build();
    
    let tracer = tracer_provider.tracer("ledger-core");
    
    global::set_tracer_provider(tracer_provider.clone());
    
    let telemetry_layer = tracing_opentelemetry::layer().with_tracer(tracer);
    
    tracing_subscriber::registry()
        .with(tracing_subscriber::EnvFilter::new(
            std::env::var("RUST_LOG").unwrap_or_else(|_| "info".to_string()),
        ))
        .with(telemetry_layer)
        .with(tracing_subscriber::fmt::layer())
        .init();
    
    Ok(tracer_provider)
}

pub fn extract_trace_context(headers: &std::collections::HashMap<String, String>) -> Context {
    let mut context = Context::current();
    let mut header_map = opentelemetry::propagation::HeaderMap::new();
    
    for (k, v) in headers {
        header_map.insert(k.as_str(), v.as_str());
    }
    
    global::get_text_map_propagator(|propagator| {
        propagator.extract(&header_map)
    })
}

pub fn inject_trace_context(headers: &mut std::collections::HashMap<String, String>) {
    let mut header_map = opentelemetry::propagation::HeaderMap::new();
    
    global::get_text_map_propagator(|propagator| {
        propagator.inject_context(&Context::current(), &mut header_map);
    });
    
    for (k, v) in header_map.iter() {
        headers.insert(k.to_string(), v.to_string());
    }
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