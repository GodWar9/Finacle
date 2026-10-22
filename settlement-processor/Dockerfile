FROM python:3.12-slim-bookworm AS recon-builder
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake git libpq-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /recon
COPY recon/CMakeLists.txt .
COPY recon/include ./include
COPY recon/src ./src
RUN cmake -B build -DCMAKE_BUILD_TYPE=Release -DENABLE_OPENMP=ON -DBUILD_TESTS=OFF -DBUILD_SHARED_LIBS=OFF \
    && cmake --build build --parallel 2

FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libpq5 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=recon-builder /recon/build/recon_engine /app/recon_engine
COPY gateway/pyproject.toml gateway/README.md ./gateway/
COPY gateway/app ./gateway/app
RUN pip install --no-cache-dir ./gateway && useradd --create-home settlement
ENV RECON_ENGINE_PATH=/app/recon_engine
USER settlement
CMD ["python", "-m", "app.settlement_processor"]
