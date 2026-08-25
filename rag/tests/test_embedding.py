import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.embedding import chunk_text

def test_chunk_text_short():
    text = "This is a short text."
    chunks = chunk_text(text, max_tokens=100, overlap=10)
    assert len(chunks) == 1
    assert chunks[0] == text

def test_chunk_text_long():
    text = " ".join(["word"] * 500)
    chunks = chunk_text(text, max_tokens=100, overlap=20)
    assert len(chunks) > 1

def test_chunk_text_overlap():
    text = "word1 word2 word3 word4 word5 word6 word7 word8 word9 word10"
    chunks = chunk_text(text, max_tokens=5, overlap=2)
    assert len(chunks) >= 2

def test_chunk_text_empty():
    chunks = chunk_text("", max_tokens=100, overlap=10)
    assert len(chunks) == 1
    assert chunks[0] == ""