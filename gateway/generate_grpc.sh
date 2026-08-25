#!/usr/bin/env bash
set -e

PROTO_DIR="../core/proto"
OUT_DIR="./app"

python -m grpc_tools.protoc \
    --proto_path=$PROTO_DIR \
    --python_out=$OUT_DIR \
    --grpc_python_out=$OUT_DIR \
    --pyi_out=$OUT_DIR \
    $PROTO_DIR/ledger.proto

echo "Generated Python gRPC stubs in $OUT_DIR"