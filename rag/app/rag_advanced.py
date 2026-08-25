import structlog
from typing import List, Optional, Dict, Any
from uuid import UUID

from app.config import get_settings
from app.db import search_documents, get_pool
from app.embedding import embed_text, chunk_text
from app.models.schemas import AskRequest, AskResponse, SourceCitation

logger = structlog.get_logger()
settings = get_settings()

SCOPE_FILTERS = {
    "ops": ["TXN_NARRATIVE", "RECONCILIATION_EXCEPTION"],
    "compliance": ["POLICY_DOC", "REGULATORY_CIRCULAR"],
    "finance": ["TXN_NARRATIVE", "POLICY_DOC"],
    "audit": ["TXN_NARRATIVE", "RECONCILIATION_EXCEPTION", "POLICY_DOC", "REGULATORY_CIRCULAR"],
}

async def advanced_answer_question(request: AskRequest) -> AskResponse:
    """Advanced Q&A with multi-hop reasoning and source verification"""
    pool = await get_pool()
    
    try:
        query_embedding = await embed_text(request.question)
        
        source_filter = SCOPE_FILTERS.get(request.scope) if request.scope else None
        
        docs = await search_documents(pool, query_embedding, source_filter, settings.rag_top_k * 2)
        
        if not docs:
            return AskResponse(
                answer="I couldn't find any relevant information in the knowledge base to answer your question.",
                sources=[],
                confidence=0.0
            )
        
        # Multi-hop: use top docs to refine query
        refined_docs = await multi_hop_retrieval(pool, request.question, docs[:3])
        
        # Combine original and refined
        all_docs = docs[:5] + refined_docs
        all_docs = all_docs[:settings.rag_top_k]
        
        # Verify sources
        verified_docs = await verify_sources(all_docs)
        
        context_block = "\n\n".join(
            f"[Source {i+1} | {doc['source_type']} | {doc['source_ref']}]\n{doc['content']}"
            for i, doc in enumerate(verified_docs)
        )
        
        scope_instruction = get_scope_instruction(request.scope)
        
        prompt = f"""{scope_instruction}

Answer ONLY using the sources below. Cite sources as [Source N] inline.
If the sources don't contain the answer, say so explicitly — never guess.

{context_block}

Question: {request.question}
"""
        
        if not settings.openai_api_key:
            answer = "OpenAI API key not configured. Cannot generate answer."
            confidence = 0.0
        else:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=settings.openai_api_key)
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=500,
            )
            answer = response.choices[0].message.content
            confidence = calculate_confidence(answer, verified_docs)
        
        sources = [
            SourceCitation(
                source_type=doc["source_type"],
                source_ref=doc["source_ref"],
                similarity=doc["similarity"]
            )
            for doc in verified_docs
        ]
        
        return AskResponse(answer=answer, sources=sources, confidence=confidence)
    
    finally:
        await pool.close()

async def multi_hop_retrieval(pool, question: str, initial_docs: List[Dict]) -> List[Dict]:
    """Perform multi-hop retrieval using initial results to refine query"""
    if not initial_docs:
        return []
    
    # Extract key entities from initial docs
    entities = extract_entities(initial_docs)
    
    if not entities:
        return []
    
    # Create refined queries
    refined_queries = [
        f"{question} {entity}" for entity in entities[:3]
    ]
    
    all_results = []
    for rq in refined_queries:
        emb = await embed_text(rq)
        docs = await search_documents(pool, emb, None, 3)
        all_results.extend(docs)
    
    # Deduplicate
    seen = set()
    unique = []
    for doc in all_results:
        key = (doc["source_type"], doc["source_ref"])
        if key not in seen:
            seen.add(key)
            unique.append(doc)
    
    return unique[:5]

def extract_entities(docs: List[Dict]) -> List[str]:
    """Extract key entities from documents"""
    entities = []
    for doc in docs:
        content = doc["content"].lower()
        # Simple entity extraction - in production use NER
        if "transaction" in content:
            entities.append("transaction")
        if "reversal" in content:
            entities.append("reversal")
        if "reconciliation" in content:
            entities.append("reconciliation")
        if "fee" in content:
            entities.append("fee")
        if "settlement" in content:
            entities.append("settlement")
        if "exception" in content:
            entities.append("exception")
    return list(set(entities))

async def verify_sources(docs: List[Dict]) -> List[Dict]:
    """Verify that sources are valid and not hallucinated"""
    # In production, this would check against a trusted index
    # For now, just return docs with high similarity
    return [d for d in docs if d.get("similarity", 0) > 0.5]

def get_scope_instruction(scope: Optional[str]) -> str:
    if scope == "ops":
        return "You are an operations assistant for a ledger system. Answer questions about transactions, reconciliations, and exceptions."
    elif scope == "compliance":
        return "You are a compliance assistant. Answer questions about regulatory policies, reversal rules, and compliance requirements."
    elif scope == "finance":
        return "You are a finance assistant. Answer questions about transaction flows, fees, and settlement."
    elif scope == "audit":
        return "You are an audit assistant. Answer questions about transaction trails, compliance, and reconciliation."
    return "You are an assistant for a ledger system. Answer questions using the provided sources."

def calculate_confidence(answer: str, sources: List[Dict]) -> float:
    """Calculate confidence score based on source coverage"""
    if not sources:
        return 0.0
    
    # Simple heuristic: more sources + higher similarity = higher confidence
    avg_sim = sum(s.get("similarity", 0) for s in sources) / len(sources)
    source_factor = min(len(sources) / 5.0, 1.0)
    
    return round(avg_sim * source_factor, 2)