FROM ubuntu:22.04 as recon-builder

RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    libpqxx-dev \
    libgtest-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY CMakeLists.txt .
COPY include ./include
COPY src ./src
COPY tests ./tests

RUN cmake -B build -DCMAKE_BUILD_TYPE=Release -DENABLE_OPENMP=ON \
    && cmake --build build -- -j$(nproc)

FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    libpqxx-6.4 \
    libgomp1 \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=recon-builder /app/build/recon_engine /app/recon_engine

COPY gateway/pyproject.toml ./gateway/
RUN pip install --no-cache-dir -e ./gateway

COPY gateway/app ./app
COPY core/proto ./proto

RUN python -m grpc_tools.protoc \
    --proto_path=proto \
    --python_out=app \
    --grpc_python_out=app \
    --pyi_out=app \
    proto/ledger.proto

ENV RECON_ENGINE_PATH=/app/recon_engine

CMD ["python", "-m", "app.settlement_processor"]