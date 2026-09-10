from uuid import UUID

from pydantic import BaseModel


class AskRequest(BaseModel):
    question: str
    scope: str | None = None

class SourceCitation(BaseModel):
    source_type: str
    source_ref: str
    similarity: float

class AskResponse(BaseModel):
    answer: str
    sources: list[SourceCitation]

class IngestPolicyRequest(BaseModel):
    source_type: str
    source_ref: str
    content: str

class IngestPolicyResponse(BaseModel):
    doc_id: UUID
    chunks_indexed: int

class HealthResponse(BaseModel):
    status: str
    service: str