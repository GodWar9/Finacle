FROM rust:1.78 AS builder

WORKDIR /build/core

COPY core/Cargo.toml core/Cargo.lock ./
COPY core/src ./src
COPY db ./db

RUN cargo build --release

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
