FROM ubuntu:22.04 AS recon-builder

RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    pkg-config \
    libpqxx-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /recon

COPY recon/CMakeLists.txt .
COPY recon/include ./include
COPY recon/src ./src

RUN cmake -B build -DCMAKE_BUILD_TYPE=Release -DENABLE_OPENMP=ON -DBUILD_TESTS=OFF \
    && cmake --build build -- -j$(nproc)

FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    libpqxx-6.4 \
    libgomp1 \
    libpq5 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=recon-builder /recon/build/recon_engine /app/recon_engine

COPY gateway/pyproject.toml ./gateway/
COPY gateway/app ./gateway/app
RUN pip install --no-cache-dir ./gateway

COPY core/proto ./proto
RUN python -m grpc_tools.protoc \
    --proto_path=proto \
    --python_out=. \
    --grpc_python_out=. \
    --pyi_out=. \
    proto/ledger.proto

ENV RECON_ENGINE_PATH=/app/recon_engine

CMD ["python", "-m", "gateway.app.settlement_processor"]
