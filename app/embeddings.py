from __future__ import annotations

from google import genai
from google.genai import types

from app.config import settings

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        # text-embedding-004 is on the stable v1 endpoint, not v1beta
        _client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options={"api_version": "v1beta"},
        )
    return _client


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts for document indexing (RETRIEVAL_DOCUMENT task)."""
    if not texts:
        return []
    client = _get_client()
    response = await client.aio.models.embed_content(
        model=settings.embedding_model,
        contents=texts,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_DOCUMENT",
            output_dimensionality=settings.embedding_dim,
        ),
    )
    return [list(e.values) for e in response.embeddings]


async def embed_query(text: str) -> list[float]:
    """Embed a single query string (RETRIEVAL_QUERY task)."""
    client = _get_client()
    response = await client.aio.models.embed_content(
        model=settings.embedding_model,
        contents=[text],
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=settings.embedding_dim,
        ),
    )
    return list(response.embeddings[0].values)
