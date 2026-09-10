import time

import grpc
import structlog

from app.config import get_settings
from app.metrics import GRPC_CALLS, GRPC_LATENCY

logger = structlog.get_logger()
settings = get_settings()

import ledger_pb2  # noqa: E402
import ledger_pb2_grpc  # noqa: E402


class LedgerGrpcClient:
    def __init__(self, address: str):
        self.address = address
        self.channel: grpc.aio.Channel | None = None
        self.stub: ledger_pb2_grpc.LedgerCoreStub | None = None

    async def connect(self):
        self.channel = grpc.aio.insecure_channel(self.address)
        self.stub = ledger_pb2_grpc.LedgerCoreStub(self.channel)
        logger.info("grpc_client_connected", address=self.address)

    async def close(self):
        if self.channel:
            await self.channel.close()
            logger.info("grpc_client_closed")

    async def _call_with_metrics(self, method_name: str, call_func):
        start_time = time.time()
        try:
            result = await call_func()
            GRPC_CALLS.labels(method=method_name, status="success").inc()
            return result
        except grpc.RpcError as e:
            GRPC_CALLS.labels(method=method_name, status=e.code().name).inc()
            raise
        finally:
            GRPC_LATENCY.labels(method=method_name).observe(time.time() - start_time)

    async def post_transaction(self, request: ledger_pb2.PostTransactionRequest) -> ledger_pb2.PostTransactionResponse:
        if not self.stub:
            raise RuntimeError("gRPC client not connected")
        return await self._call_with_metrics("PostTransaction",
            lambda: self.stub.PostTransaction(request, timeout=5.0))

    async def get_balance(self, request: ledger_pb2.GetBalanceRequest) -> ledger_pb2.GetBalanceResponse:
        if not self.stub:
            raise RuntimeError("gRPC client not connected")
        return await self._call_with_metrics("GetBalance",
            lambda: self.stub.GetBalance(request, timeout=2.0))

    async def reverse_transaction(
        self, request: ledger_pb2.ReverseTransactionRequest
    ) -> ledger_pb2.PostTransactionResponse:
        if not self.stub:
            raise RuntimeError("gRPC client not connected")
        return await self._call_with_metrics("ReverseTransaction",
            lambda: self.stub.ReverseTransaction(request, timeout=5.0))

grpc_client: LedgerGrpcClient | None = None

async def get_grpc_client() -> LedgerGrpcClient:
    global grpc_client
    if grpc_client is None:
        grpc_client = LedgerGrpcClient(settings.ledger_core_addr)
        await grpc_client.connect()
    return grpc_client
