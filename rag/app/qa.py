import structlog
from openai import AsyncOpenAI

from app.config import get_settings
from app.db import get_pool, search_documents
from app.models.schemas import AskRequest, AskResponse, SourceCitation

logger = structlog.get_logger()
settings = get_settings()

SCOPE_FILTERS = {
    "ops": ["TXN_NARRATIVE", "RECONCILIATION_EXCEPTION"],
    "compliance": ["POLICY_DOC", "REGULATORY_CIRCULAR"],
}


async def answer_question(request: AskRequest) -> AskResponse:
    if not settings.openai_api_key:
        return AskResponse(
            answer="OpenAI API key not configured. Set OPENAI_API_KEY to enable RAG queries.",
            sources=[],
        )

    pool = await get_pool()

    try:
        query_embedding = await embed_text(request.question)

        source_filter = SCOPE_FILTERS.get(request.scope) if request.scope else None

        docs = await search_documents(
            pool, query_embedding, source_filter, settings.rag_top_k
        )

        if not docs:
            return AskResponse(
                answer="I couldn't find any relevant information in the knowledge base to answer your question.",
                sources=[],
            )

        context_block = "\n\n".join(
            f"[Source {i + 1} | {doc['source_type']} | {doc['source_ref']}]\n{doc['content']}"
            for i, doc in enumerate(docs)
        )

        scope_instruction = ""
        if request.scope == "ops":
            scope_instruction = "You are an operations assistant for a ledger system. Answer questions about transactions, reconciliations, and exceptions."
        elif request.scope == "compliance":
            scope_instruction = "You are a compliance assistant. Answer questions about regulatory policies, reversal rules, and compliance requirements."

        prompt = f"""{scope_instruction}

Answer ONLY using the sources below. Cite sources as [Source N] inline.
If the sources don't contain the answer, say so explicitly — never guess.

{context_block}

Question: {request.question}
"""

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=500,
        )
        answer = response.choices[0].message.content

        sources = [
            SourceCitation(
                source_type=doc["source_type"],
                source_ref=doc["source_ref"],
                similarity=doc["similarity"],
            )
            for doc in docs
        ]

        return AskResponse(answer=answer, sources=sources)

    finally:
        await pool.close()


async def embed_text(text: str) -> list[float]:
    from app.embedding import embed_text as embed_fn

    return await embed_fn(text)
