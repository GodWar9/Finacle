FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY app ./app
COPY ../core/proto ./proto

RUN python -m grpc_tools.protoc \
    --proto_path=proto \
    --python_out=app \
    --grpc_python_out=app \
    --pyi_out=app \
    proto/ledger.proto

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]