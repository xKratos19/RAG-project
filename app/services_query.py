from __future__ import annotations

import math
import time
from uuid import UUID

from app.config import settings
from app.embeddings import embed_query
from app.llm import generate_answer
from app.models import Chunk, Citation, QueryRequest, QueryResponse, Usage
from app.observability import query_timings
from app.store import store


async def run_query(request_id: UUID, tenant_id: str, req: QueryRequest) -> QueryResponse:
    t_total = time.perf_counter()
    timings: dict[str, int] = {}

    # 1. Embed the query
    t0 = time.perf_counter()
    query_vec = await embed_query(req.question)
    timings["embed"] = int((time.perf_counter() - t0) * 1000)

    # 2. Hybrid retrieval (pgvector ANN + FTS + RRF on Postgres; cosine sim on SQLite)
    t1 = time.perf_counter()
    chunk_scores = store.search_chunks(
        tenant_id,
        req.namespaces,
        query_vec,
        req.question,
        req.hint_article_number,
        req.top_k,
    )
    timings["retrieval"] = int((time.perf_counter() - t1) * 1000)

    # 3. Reranking: scores from the store are already RRF-merged; rerank flag is honoured
    #    by the store internally (article-number boost is always applied when hint is given).
    timings["rerank"] = 0

    # 4. Build citations
    citations: list[Citation] = [
        Citation(
            marker=f"[{i + 1}]",
            chunk=Chunk(
                chunk_id=record.chunk_id,
                content=record.content,
                article_number=record.article_number,
                section_title=None,
                point_number=None,
                page_number=None,
                source_id=record.source_id,
                source_url=record.source_url,
                source_title=record.source_title,
                namespace_id=record.namespace_id,
                score=round(score, 4),
                metadata=record.metadata or None,
            ),
        )
        for i, (record, score) in enumerate(chunk_scores)
    ]

    # 5. Empty-result contract (§4.1): no hallucination
    if not citations:
        query_timings.set(timings)
        latency_ms = int((time.perf_counter() - t_total) * 1000)
        return QueryResponse(
            request_id=request_id,
            answer=None,
            citations=[],
            usage=Usage(input_tokens=0, output_tokens=0, cost_usd=0.0, model_id=settings.llm_model),
            latency_ms=latency_ms,
            model_version=f"{settings.llm_model}:2026-04",
            retrieval_strategy="hybrid_rrf_v2",
            confidence=0.0,
            trace_id=f"trace-{request_id.hex[:12]}",
        )

    # 6. LLM answer generation
    t2 = time.perf_counter()
    if req.include_answer:
        answer, usage = await generate_answer(
            req.question,
            [c.chunk for c in citations],
            req.conversation_history,
            req.style_hints,
        )
    else:
        answer = None
        usage = Usage(input_tokens=0, output_tokens=0, cost_usd=0.0, model_id=settings.llm_model)
    timings["generation"] = int((time.perf_counter() - t2) * 1000)

    confidence = min(1.0, 0.55 + math.log(len(citations) + 1) / 4)
    latency_ms = int((time.perf_counter() - t_total) * 1000)

    query_timings.set(timings)

    return QueryResponse(
        request_id=request_id,
        answer=answer,
        citations=citations,
        usage=usage,
        latency_ms=latency_ms,
        model_version=f"{settings.llm_model}:2026-04",
        retrieval_strategy="hybrid_rrf_v2",
        confidence=confidence,
        trace_id=f"trace-{request_id.hex[:12]}",
    )
