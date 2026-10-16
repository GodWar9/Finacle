from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    scope: Literal["ops", "compliance"] | None = None


class SourceCitation(BaseModel):
    source_type: str
    source_ref: str
    similarity: float


class AskResponse(BaseModel):
    answer: str
    sources: list[SourceCitation]


class IngestPolicyRequest(BaseModel):
    source_type: Literal["POLICY_DOC", "REGULATORY_CIRCULAR"]
    source_ref: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=100000)


class IngestPolicyResponse(BaseModel):
    doc_id: UUID
    chunks_indexed: int


class HealthResponse(BaseModel):
    status: str
    service: str
