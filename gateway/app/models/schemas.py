from uuid import UUID

from pydantic import BaseModel, Field


class LedgerEntry(BaseModel):
    account_id: UUID
    direction: str = Field(pattern="^(DEBIT|CREDIT)$")
    amount_minor: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)


class PostTransactionRequest(BaseModel):
    transaction_type: str
    reference_id: str | None = None
    entries: list[LedgerEntry] = Field(min_length=2)
    narrative: str | None = None


class PostTransactionResponse(BaseModel):
    transaction_id: UUID
    status: str
    posted_at_unix_ms: int


class GetBalanceResponse(BaseModel):
    account_id: UUID
    balance_minor: int
    currency: str
    as_of_unix_ms: int


class ReverseTransactionRequest(BaseModel):
    idempotency_key: str
    reason: str


class ErrorResponse(BaseModel):
    detail: str


class HealthResponse(BaseModel):
    status: str
    service: str
