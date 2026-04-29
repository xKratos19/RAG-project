from __future__ import annotations

import os

# Force SQLite for tests — must be set before any app module is imported
# so pydantic-settings picks it up when building the Settings singleton.
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("API_KEY", "dev-api-key")

import asyncio
from typing import Any

import pytest

from app.config import settings
from app.models import Usage

_DUMMY_EMBEDDING = [0.1] * settings.embedding_dim
_DUMMY_ANSWER = "Articolul 15 prevede că aporturile în numerar sunt obligatorii [1]."
_DUMMY_USAGE = Usage(input_tokens=42, output_tokens=18, cost_usd=0.000005, model_id=settings.llm_model)


async def _mock_embed_query(text: str) -> list[float]:
    return _DUMMY_EMBEDDING


async def _mock_embed_texts(texts: list[str]) -> list[list[float]]:
    return [_DUMMY_EMBEDDING for _ in texts]


async def _mock_generate_answer(question: str, chunks: Any, history: Any, hints: Any) -> tuple[str, Usage]:
    return _DUMMY_ANSWER, _DUMMY_USAGE


class _MockArqPool:
    """Runs the ingest task inline so tests don't need a live Redis instance."""

    async def enqueue_job(self, func_name: str, *args: Any, **kwargs: Any) -> None:
        if func_name == "ingest_background":
            job_id, tenant_id, payload_dict, *rest = args
            file_content_type = rest[0] if rest else None
            from app.services_ingest import run_ingest_task
            await run_ingest_task(job_id, tenant_id, payload_dict, file_content_type)

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def mock_external_apis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch Gemini embedding and LLM calls for all tests."""
    monkeypatch.setattr("app.services_query.embed_query", _mock_embed_query)
    monkeypatch.setattr("app.services_query.generate_answer", _mock_generate_answer)
    monkeypatch.setattr("app.services_ingest.embed_texts", _mock_embed_texts)


@pytest.fixture(autouse=True)
def mock_arq_pool() -> None:
    """Inject a mock ARQ pool into the FastAPI app state."""
    from app.main import app
    app.state.arq_pool = _MockArqPool()
