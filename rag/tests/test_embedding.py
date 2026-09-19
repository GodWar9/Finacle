import pytest
from app.embedding import chunk_text


@pytest.mark.asyncio
async def test_chunk_text_short():
    text = "This is a short text."
    chunks = chunk_text(text, max_tokens=100, overlap=10)
    assert len(chunks) == 1
    assert chunks[0] == text


@pytest.mark.asyncio
async def test_chunk_text_long():
    text = " ".join(["word"] * 500)
    chunks = chunk_text(text, max_tokens=100, overlap=20)
    assert len(chunks) > 1


@pytest.mark.asyncio
async def test_chunk_text_overlap():
    text = "word1 word2 word3 word4 word5 word6 word7 word8 word9 word10"
    chunks = chunk_text(text, max_tokens=5, overlap=2)
    assert len(chunks) >= 2


@pytest.mark.asyncio
async def test_chunk_text_empty():
    chunks = chunk_text("", max_tokens=100, overlap=10)
    assert len(chunks) == 1
    assert chunks[0] == ""
