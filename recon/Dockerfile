FROM ubuntu:22.04 as builder

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

FROM ubuntu:22.04

RUN apt-get update && apt-get install -y \
    libpqxx-6.4 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/build/recon_engine /app/recon_engine

ENTRYPOINT ["/app/recon_engine"]