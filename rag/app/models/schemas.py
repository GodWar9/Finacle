from pydantic import BaseModel
from typing import List, Optional
from uuid import UUID

class AskRequest(BaseModel):
    question: str
    scope: Optional[str] = None

class SourceCitation(BaseModel):
    source_type: str
    source_ref: str
    similarity: float

class AskResponse(BaseModel):
    answer: str
    sources: List[SourceCitation]

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