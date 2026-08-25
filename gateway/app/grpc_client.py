import grpc
import structlog
from typing import Optional
from uuid import UUID

from app.config import get_settings

logger = structlog.get_logger()
settings = get_settings()

try:
    import ledger_pb2
    import ledger_pb2_grpc
except ImportError:
    ledger_pb2 = None
    ledger_pb2_grpc = None

class LedgerGrpcClient:
    def __init__(self, address: str):
        self.address = address
        self.channel: Optional[grpc.aio.Channel] = None
        self.stub: Optional[ledger_pb2_grpc.LedgerCoreStub] = None

    async def connect(self):
        self.channel = grpc.aio.insecure_channel(self.address)
        self.stub = ledger_pb2_grpc.LedgerCoreStub(self.channel)
        logger.info("grpc_client_connected", address=self.address)

    async def close(self):
        if self.channel:
            await self.channel.close()
            logger.info("grpc_client_closed")

    async def post_transaction(self, request: ledger_pb2.PostTransactionRequest) -> ledger_pb2.PostTransactionResponse:
        if not self.stub:
            raise RuntimeError("gRPC client not connected")
        return await self.stub.PostTransaction(request, timeout=5.0)

    async def get_balance(self, request: ledger_pb2.GetBalanceRequest) -> ledger_pb2.GetBalanceResponse:
        if not self.stub:
            raise RuntimeError("gRPC client not connected")
        return await self.stub.GetBalance(request, timeout=2.0)

    async def reverse_transaction(self, request: ledger_pb2.ReverseTransactionRequest) -> ledger_pb2.PostTransactionResponse:
        if not self.stub:
            raise RuntimeError("gRPC client not connected")
        return await self.stub.ReverseTransaction(request, timeout=5.0)

grpc_client: Optional[LedgerGrpcClient] = None

async def get_grpc_client() -> LedgerGrpcClient:
    global grpc_client
    if grpc_client is None:
        grpc_client = LedgerGrpcClient(settings.ledger_core_addr)
        await grpc_client.connect()
    return grpc_client