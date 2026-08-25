FROM rust:1.78 as builder

WORKDIR /app

COPY Cargo.toml Cargo.lock ./
COPY src ./src
COPY db/migrations ./db/migrations

RUN cargo build --release

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y \
    ca-certificates \
    libssl3 \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/target/release/ledger-core /app/ledger-core
COPY --from=builder /app/db/migrations /app/db/migrations

EXPOSE 50051

CMD ["/app/ledger-core"]