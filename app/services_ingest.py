from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import timedelta
from io import BytesIO
from uuid import UUID, uuid4

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

from app.config import settings
from app.embeddings import embed_texts
from app.errors import raise_error
from app.models import (
    IdempotencyRecord,
    IngestError,
    IngestJob,
    IngestProgress,
    IngestRequest,
    IngestStatus,
    SourceChunkRecord,
)
from app.store import store, utc_now


def _payload_hash(payload: IngestRequest) -> str:
    canonical = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _split_into_chunks(text: str, max_chars: int = 900) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    words = normalized.split(" ")
    chunks: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word]).strip()
        if len(candidate) <= max_chars:
            current.append(word)
            continue
        if current:
            chunks.append(" ".join(current).strip())
        current = [word]
    if current:
        chunks.append(" ".join(current).strip())
    return chunks


def _extract_article_number(text: str) -> str | None:
    match = re.search(r"(?:art(?:icolul)?\.?\s*)(\d+(?:\^\d+)?)", text, flags=re.IGNORECASE)
    return match.group(1) if match else None


async def _fetch_url(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text
    except httpx.HTTPStatusError as exc:
        raise ValueError(f"fetch_failed: URL returned {exc.response.status_code}") from exc
    except Exception as exc:
        raise ValueError(f"fetch_failed: {exc}") from exc


def _extract_text_from_bytes(body: bytes, content_type: str | None) -> str:
    mime = (content_type or "text/plain").split(";")[0].strip()
    if mime == "application/pdf":
        reader = PdfReader(BytesIO(body))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return body.decode("utf-8", errors="ignore")


async def _post_callback(url: str, payload: dict) -> None:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    sig = hmac.new(settings.webhook_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    headers = {"X-Vendor-Signature": f"sha256={sig}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, content=body, headers=headers)
    except Exception:
        pass


async def create_or_reuse_job(
    arq_pool: object,
    tenant_id: str,
    request_id: UUID,
    idempotency_key: UUID,
    payload: IngestRequest,
    file_bytes: bytes | None = None,
    file_content_type: str | None = None,
) -> IngestJob:
    existing = store.get_idempotency(tenant_id, idempotency_key)
    incoming_hash = _payload_hash(payload)
    if existing:
        if existing.payload_hash != incoming_hash:
            raise_error(409, "duplicate_job", "Idempotency key re-used with a different payload.", request_id)
        job = store.get_job(existing.job_id)
        if job is None:
            raise_error(500, "internal_error", "Idempotency record references a missing job.", request_id)
        return job

    now = utc_now()
    job_id = f"j_{uuid4().hex[:12]}"
    job = IngestJob(
        job_id=job_id,
        namespace_id=payload.namespace_id,
        source_id=payload.source_id,
        status=IngestStatus.queued,
        progress=IngestProgress(stage="queued", percent=0, chunks_created=0),
        submitted_at=now,
        estimated_completion_at=now + timedelta(minutes=5),
    )
    store.put_job(job, tenant_id=tenant_id)
    store.put_job_extras(
        job_id,
        callback_url=str(payload.callback_url) if payload.callback_url else None,
        file_bytes=file_bytes,
    )
    store.put_idempotency(
        IdempotencyRecord(key=idempotency_key, tenant_id=tenant_id, payload_hash=incoming_hash, job_id=job_id)
    )

    # Enqueue background task; arq_pool is duck-typed to support tests with a mock pool
    await arq_pool.enqueue_job(  # type: ignore[union-attr]
        "ingest_background",
        job_id,
        tenant_id,
        payload.model_dump(mode="json"),
        file_content_type,
    )

    return job


async def run_ingest_task(
    job_id: str,
    tenant_id: str,
    payload_dict: dict,
    file_content_type: str | None = None,
) -> None:
    """Called by the ARQ worker to execute the ingest pipeline in the background."""
    payload = IngestRequest.model_validate(payload_dict)
    file_bytes = store.get_job_file_bytes(job_id)
    callback_url = store.get_job_callback_url(job_id)

    try:
        await _execute_ingest_pipeline(tenant_id, job_id, payload, file_bytes, file_content_type)
        # Release large binary now that processing is done
        store.clear_job_file_bytes(job_id)
    except Exception as exc:
        store.clear_job_file_bytes(job_id)
        failed = store.get_job(job_id)
        if failed:
            failed.status = IngestStatus.failed
            failed.completed_at = utc_now()
            failed.error = IngestError(code="ingest_failed", message=str(exc), retryable=False)
            failed.progress = IngestProgress(stage="fetching", percent=0, chunks_created=0)
            store.put_job(failed, tenant_id=tenant_id)

        if callback_url:
            await _post_callback(
                callback_url,
                {
                    "event": "ingest.failed",
                    "job_id": job_id,
                    "namespace_id": payload.namespace_id,
                    "source_id": payload.source_id,
                    "status": "failed",
                    "error": str(exc),
                    "at": utc_now().isoformat(),
                },
            )


async def _execute_ingest_pipeline(
    tenant_id: str,
    job_id: str,
    payload: IngestRequest,
    file_bytes: bytes | None,
    file_content_type: str | None,
) -> None:
    job = store.get_job(job_id)
    if not job:
        return

    # ── Fetch ────────────────────────────────────────────────────────────────
    job.status = IngestStatus.fetching
    job.progress = IngestProgress(stage="fetching", percent=15, chunks_created=0)
    store.put_job(job, tenant_id=tenant_id)

    mime_type = payload.mime_type_hint or "text/plain"
    if payload.source_type == "url":
        raw = await _fetch_url(str(payload.url))
        if raw.lstrip().startswith("<"):
            soup = BeautifulSoup(raw, "html.parser")
            raw = soup.get_text(separator=" ", strip=True)
            mime_type = "text/html"
        job.source_url = str(payload.url) if payload.url else None
    else:
        if file_bytes is None:
            raise ValueError("Missing file bytes for file-type ingest")
        raw = _extract_text_from_bytes(file_bytes, file_content_type)
        mime_type = file_content_type or "application/octet-stream"

    # ── Extract ──────────────────────────────────────────────────────────────
    job.status = IngestStatus.extracting
    job.progress = IngestProgress(stage="extracting", percent=40, chunks_created=0)
    store.put_job(job, tenant_id=tenant_id)

    chunks = _split_into_chunks(raw)

    # ── Chunk ────────────────────────────────────────────────────────────────
    job.status = IngestStatus.chunking
    job.progress = IngestProgress(stage="chunking", percent=60, chunks_created=len(chunks))
    store.put_job(job, tenant_id=tenant_id)

    # ── Embed ────────────────────────────────────────────────────────────────
    job.status = IngestStatus.embedding
    job.progress = IngestProgress(stage="embedding", percent=75, chunks_created=len(chunks))
    store.put_job(job, tenant_id=tenant_id)

    BATCH = 64
    embeddings: list[list[float]] = []
    for i in range(0, len(chunks), BATCH):
        batch = [c[:4000] for c in chunks[i : i + BATCH]]
        embeddings.extend(await embed_texts(batch))

    # ── Index ────────────────────────────────────────────────────────────────
    job.status = IngestStatus.indexing
    job.progress = IngestProgress(stage="indexing", percent=90, chunks_created=len(chunks))
    store.put_job(job, tenant_id=tenant_id)

    rows = [
        SourceChunkRecord(
            tenant_id=tenant_id,
            namespace_id=payload.namespace_id,
            source_id=payload.source_id,
            source_url=str(payload.url) if payload.url else None,
            source_title=(payload.metadata or {}).get("source_title") if payload.metadata else None,
            article_number=_extract_article_number(chunk),
            content=chunk[:4000],
            metadata=payload.metadata or {},
            embedding=embeddings[i] if i < len(embeddings) else None,
        )
        for i, chunk in enumerate(chunks)
    ]
    store.add_chunks(rows)

    # ── Done ─────────────────────────────────────────────────────────────────
    job.status = IngestStatus.done
    job.progress = IngestProgress(stage="done", percent=100, chunks_created=len(rows))
    job.mime_type = mime_type
    job.content = raw[:5000]
    job.completed_at = utc_now()
    store.put_job(job, tenant_id=tenant_id)

    callback_url = store.get_job_callback_url(job_id)
    if callback_url:
        await _post_callback(
            callback_url,
            {
                "event": "ingest.completed",
                "job_id": job_id,
                "namespace_id": payload.namespace_id,
                "source_id": payload.source_id,
                "status": "done",
                "chunks_created": len(rows),
                "at": job.completed_at.isoformat() if job.completed_at else utc_now().isoformat(),
            },
        )
