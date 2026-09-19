import tiktoken

from app.config import get_settings

settings = get_settings()


def get_encoding():
    try:
        return tiktoken.encoding_for_model(settings.embedding_model)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def chunk_text(
    text: str, max_tokens: int | None = None, overlap: int | None = None
) -> list[str]:
    if max_tokens is None:
        max_tokens = settings.chunk_size
    if overlap is None:
        overlap = settings.chunk_overlap

    encoding = get_encoding()
    tokens = encoding.encode(text)

    if len(tokens) <= max_tokens:
        return [text]

    chunks = []
    start = 0

    while start < len(tokens):
        end = min(start + max_tokens, len(tokens))
        chunk_tokens = tokens[start:end]
        chunk_text = encoding.decode(chunk_tokens)
        chunks.append(chunk_text)
        start += max_tokens - overlap

    return chunks


async def embed_text(text: str) -> list[float]:
    from openai import AsyncOpenAI

    settings = get_settings()
    if not settings.openai_api_key:
        return [0.0] * settings.embedding_dimension

    client = AsyncOpenAI(api_key=settings.openai_api_key)

    response = await client.embeddings.create(
        model=settings.embedding_model, input=text
    )

    return response.data[0].embedding
