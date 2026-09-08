FROM rust:1-bookworm AS builder

RUN apt-get update && apt-get install -y \
    protobuf-compiler \
    cmake \
    build-essential \
    pkg-config \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build/core

COPY core/Cargo.toml core/Cargo.lock core/build.rs ./
COPY core/proto ./proto
COPY core/src ./src
COPY db /build/db/

COPY core/.sqlx ./.sqlx
ENV SQLX_OFFLINE=true

# The `kafka` feature enables the transactional outbox poller, which publishes
# outbox_events to Kafka so downstream consumers (RAG indexer) stay consistent.
# It pulls in rdkafka, which builds librdkafka from source (cmake + OpenSSL).
RUN cargo build --release --features kafka

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y \
    ca-certificates \
    libssl3 \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /build/core/target/release/ledger-core /app/ledger-core

EXPOSE 50051

CMD ["/app/ledger-core"]
