from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import numpy as np
import sqlalchemy as sa
from sqlalchemy import JSON, DateTime, LargeBinary, String, Text, delete, func, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.config import settings
from app.models import IdempotencyRecord, IngestJob, NamespaceStats, SourceChunkRecord

EMBED_DIM = settings.embedding_dim


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class JobRow(Base):
    __tablename__ = "rag_jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(255), index=True)
    namespace_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    progress: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    estimated_completion_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    callback_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)


class IdempotencyRow(Base):
    __tablename__ = "rag_idempotency"

    tenant_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload_hash: Mapped[str] = mapped_column(String(255))
    job_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ChunkRow(Base):
    __tablename__ = "rag_chunks"

    chunk_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(255), index=True)
    namespace_id: Mapped[str] = mapped_column(String(255), index=True)
    source_id: Mapped[str] = mapped_column(String(255), index=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    article_number: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    content: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    embedding_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NamespaceDeleteRow(Base):
    __tablename__ = "rag_namespace_delete_jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


def _rrf(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal Rank Fusion over multiple ranked lists of chunk IDs."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)
    return scores


class SQLStore:
    def __init__(self, database_url: str) -> None:
        self._is_postgres = database_url.startswith("postgresql")
        self.engine = sa.create_engine(database_url, future=True, pool_pre_ping=True)
        self.SessionLocal = sessionmaker(bind=self.engine, class_=Session, expire_on_commit=False)
        Base.metadata.create_all(self.engine)

    # ── Job CRUD ────────────────────────────────────────────────────────────

    def _to_job(self, row: JobRow) -> IngestJob:
        return IngestJob.model_validate(
            {
                "job_id": row.job_id,
                "namespace_id": row.namespace_id,
                "source_id": row.source_id,
                "status": row.status,
                "progress": row.progress,
                "submitted_at": row.submitted_at,
                "completed_at": row.completed_at,
                "estimated_completion_at": row.estimated_completion_at,
                "error": row.error,
                "content": row.content,
                "mime_type": row.mime_type,
                "source_url": row.source_url,
                "source_title": row.source_title,
            }
        )

    def put_job(self, job: IngestJob, tenant_id: str | None = None) -> None:
        if tenant_id is None:
            tenant_id = self.get_job_tenant(job.job_id) or ""
        with self.SessionLocal() as session:
            row = session.get(JobRow, job.job_id)
            if row is None:
                row = JobRow(job_id=job.job_id, tenant_id=tenant_id)
            row.namespace_id = job.namespace_id
            row.source_id = job.source_id
            row.status = job.status.value if hasattr(job.status, "value") else str(job.status)
            row.progress = job.progress.model_dump(mode="json") if job.progress else None
            row.submitted_at = job.submitted_at
            row.completed_at = job.completed_at
            row.estimated_completion_at = job.estimated_completion_at
            row.error = job.error.model_dump(mode="json") if job.error else None
            row.content = job.content
            row.mime_type = job.mime_type
            row.source_url = job.source_url
            row.source_title = job.source_title
            session.merge(row)
            session.commit()

    def put_job_extras(
        self,
        job_id: str,
        callback_url: str | None,
        file_bytes: bytes | None,
    ) -> None:
        with self.SessionLocal() as session:
            row = session.get(JobRow, job_id)
            if row:
                row.callback_url = callback_url
                row.file_bytes = file_bytes
                session.commit()

    def get_job_tenant(self, job_id: str) -> str | None:
        with self.SessionLocal() as session:
            row = session.get(JobRow, job_id)
            return row.tenant_id if row else None

    def get_job(self, job_id: str) -> IngestJob | None:
        with self.SessionLocal() as session:
            row = session.get(JobRow, job_id)
            return self._to_job(row) if row else None

    def get_job_callback_url(self, job_id: str) -> str | None:
        with self.SessionLocal() as session:
            row = session.get(JobRow, job_id)
            return row.callback_url if row else None

    def get_job_file_bytes(self, job_id: str) -> bytes | None:
        with self.SessionLocal() as session:
            row = session.get(JobRow, job_id)
            return row.file_bytes if row else None

    def clear_job_file_bytes(self, job_id: str) -> None:
        with self.SessionLocal() as session:
            row = session.get(JobRow, job_id)
            if row:
                row.file_bytes = None
                session.commit()

    def is_job_owned_by_tenant(self, job_id: str, tenant_id: str) -> bool:
        return self.get_job_tenant(job_id) == tenant_id

    def get_namespace_callback_urls(self, tenant_id: str, namespace_id: str) -> list[str]:
        with self.SessionLocal() as session:
            rows = session.execute(
                select(JobRow.callback_url).where(
                    JobRow.tenant_id == tenant_id,
                    JobRow.namespace_id == namespace_id,
                    JobRow.callback_url.is_not(None),
                )
            ).scalars().all()
            return [r for r in rows if r]

    # ── Idempotency ─────────────────────────────────────────────────────────

    def put_idempotency(self, record: IdempotencyRecord) -> None:
        with self.SessionLocal() as session:
            row = IdempotencyRow(
                tenant_id=record.tenant_id,
                key=str(record.key),
                payload_hash=record.payload_hash,
                job_id=record.job_id,
                created_at=record.created_at,
            )
            session.merge(row)
            session.commit()

    def get_idempotency(self, tenant_id: str, key: UUID) -> IdempotencyRecord | None:
        with self.SessionLocal() as session:
            row = session.get(IdempotencyRow, {"tenant_id": tenant_id, "key": str(key)})
            if row is None:
                return None
            return IdempotencyRecord(
                key=UUID(row.key),
                tenant_id=row.tenant_id,
                payload_hash=row.payload_hash,
                job_id=row.job_id,
                created_at=row.created_at,
            )

    # ── Chunk storage ────────────────────────────────────────────────────────

    def add_chunks(self, rows: list[SourceChunkRecord]) -> None:
        with self.SessionLocal() as session:
            for row in rows:
                session.merge(
                    ChunkRow(
                        chunk_id=str(row.chunk_id),
                        tenant_id=row.tenant_id,
                        namespace_id=row.namespace_id,
                        source_id=row.source_id,
                        source_url=row.source_url,
                        source_title=row.source_title,
                        article_number=row.article_number,
                        content=row.content,
                        metadata_json=row.metadata,
                        embedding_json=json.dumps(row.embedding) if row.embedding else None,
                        created_at=row.created_at,
                    )
                )
            session.commit()

    def delete_source(self, tenant_id: str, namespace_id: str, source_id: str) -> int:
        with self.SessionLocal() as session:
            existing = session.execute(
                select(func.count(ChunkRow.chunk_id)).where(
                    ChunkRow.tenant_id == tenant_id,
                    ChunkRow.namespace_id == namespace_id,
                    ChunkRow.source_id == source_id,
                )
            ).scalar_one()
            session.execute(
                delete(ChunkRow).where(
                    ChunkRow.tenant_id == tenant_id,
                    ChunkRow.namespace_id == namespace_id,
                    ChunkRow.source_id == source_id,
                )
            )
            session.commit()
            return int(existing)

    def delete_namespace(self, tenant_id: str, namespace_id: str) -> int:
        with self.SessionLocal() as session:
            existing = session.execute(
                select(func.count(ChunkRow.chunk_id)).where(
                    ChunkRow.tenant_id == tenant_id,
                    ChunkRow.namespace_id == namespace_id,
                )
            ).scalar_one()
            session.execute(
                delete(ChunkRow).where(
                    ChunkRow.tenant_id == tenant_id,
                    ChunkRow.namespace_id == namespace_id,
                )
            )
            session.commit()
            return int(existing)

    # ── Hybrid retrieval ─────────────────────────────────────────────────────

    def _row_to_chunk_record(self, row: ChunkRow) -> SourceChunkRecord:
        embedding = json.loads(row.embedding_json) if row.embedding_json else None
        return SourceChunkRecord(
            tenant_id=row.tenant_id,
            namespace_id=row.namespace_id,
            source_id=row.source_id,
            source_url=row.source_url,
            source_title=row.source_title,
            article_number=row.article_number,
            content=row.content,
            metadata=row.metadata_json,
            embedding=embedding,
            chunk_id=UUID(row.chunk_id),
            created_at=row.created_at,
        )

    def search_chunks(
        self,
        tenant_id: str,
        namespaces: list[str],
        query_embedding: list[float],
        question: str,
        hint_article_number: str | None,
        top_k: int,
    ) -> list[tuple[SourceChunkRecord, float]]:
        with self.SessionLocal() as session:
            if self._is_postgres:
                return self._search_postgres(
                    session, tenant_id, namespaces, query_embedding,
                    question, hint_article_number, top_k,
                )
            return self._search_sqlite(
                session, tenant_id, namespaces, query_embedding,
                question, hint_article_number, top_k,
            )

    def _search_postgres(
        self,
        session: Session,
        tenant_id: str,
        namespaces: list[str],
        query_embedding: list[float],
        question: str,
        hint_article_number: str | None,
        top_k: int,
    ) -> list[tuple[SourceChunkRecord, float]]:
        fetch_n = min(top_k * 4, 200)
        qvec = json.dumps(query_embedding)

        # ANN vector search via pgvector cosine distance
        vec_ranking: list[str] = []
        try:
            vec_rows = session.execute(
                text(
                    "SELECT chunk_id, "
                    "1 - (embedding_json::vector(:dim) <=> :qvec::vector(:dim)) AS vscore "
                    "FROM rag_chunks "
                    "WHERE tenant_id = :tenant_id AND namespace_id = ANY(:namespaces) "
                    "  AND embedding_json IS NOT NULL "
                    "ORDER BY vscore DESC LIMIT :fetch_n"
                ),
                {"dim": EMBED_DIM, "qvec": qvec, "tenant_id": tenant_id,
                 "namespaces": namespaces, "fetch_n": fetch_n},
            ).fetchall()
            vec_ranking = [r[0] for r in vec_rows]
        except Exception:
            session.rollback()

        # Full-text search with PostgreSQL tsvector ('simple' = no language stemming)
        fts_ranking: list[str] = []
        try:
            fts_rows = session.execute(
                text(
                    "SELECT chunk_id "
                    "FROM rag_chunks "
                    "WHERE tenant_id = :tenant_id AND namespace_id = ANY(:namespaces) "
                    "  AND to_tsvector('simple', content) @@ plainto_tsquery('simple', :question) "
                    "ORDER BY ts_rank_cd(to_tsvector('simple', content), "
                    "         plainto_tsquery('simple', :question)) DESC "
                    "LIMIT :fetch_n"
                ),
                {"tenant_id": tenant_id, "namespaces": namespaces,
                 "question": question, "fetch_n": fetch_n},
            ).fetchall()
            fts_ranking = [r[0] for r in fts_rows]
        except Exception:
            session.rollback()

        rrf_scores = _rrf([vec_ranking, fts_ranking])

        # Boost exact article-number matches
        if hint_article_number:
            article_ids = {
                r[0]
                for r in session.execute(
                    select(ChunkRow.chunk_id).where(
                        ChunkRow.tenant_id == tenant_id,
                        ChunkRow.namespace_id.in_(namespaces),
                        ChunkRow.article_number == hint_article_number,
                    )
                ).fetchall()
            }
            for cid in article_ids:
                rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 0.35

        sorted_ids = sorted(rrf_scores, key=lambda k: rrf_scores[k], reverse=True)[:top_k]
        if not sorted_ids:
            return []

        row_map = {
            r.chunk_id: r
            for r in session.execute(
                select(ChunkRow).where(ChunkRow.chunk_id.in_(sorted_ids))
            ).scalars().all()
        }

        return [
            (self._row_to_chunk_record(row_map[cid]), min(1.0, rrf_scores[cid]))
            for cid in sorted_ids
            if cid in row_map
        ]

    def _search_sqlite(
        self,
        session: Session,
        tenant_id: str,
        namespaces: list[str],
        query_embedding: list[float],
        question: str,
        hint_article_number: str | None,
        top_k: int,
    ) -> list[tuple[SourceChunkRecord, float]]:
        rows = session.execute(
            select(ChunkRow).where(
                ChunkRow.tenant_id == tenant_id,
                ChunkRow.namespace_id.in_(namespaces),
            )
        ).scalars().all()

        if not rows:
            return []

        q_vec = np.array(query_embedding, dtype=np.float32)
        q_norm = float(np.linalg.norm(q_vec))
        q_tokens = set(question.lower().split())

        scored: list[tuple[ChunkRow, float]] = []
        for row in rows:
            # Vector cosine similarity
            if row.embedding_json and q_norm > 0:
                c_vec = np.array(json.loads(row.embedding_json), dtype=np.float32)
                c_norm = float(np.linalg.norm(c_vec))
                vscore = float(np.dot(q_vec, c_vec) / (q_norm * c_norm)) if c_norm > 0 else 0.0
            else:
                vscore = 0.0

            # Keyword overlap as BM25 proxy
            c_tokens = set(row.content.lower().split())
            ftscore = len(q_tokens & c_tokens) / max(len(q_tokens), 1) * 0.5

            # RRF-style merge (single-query simplified)
            score = (vscore + ftscore) / 2.0

            if hint_article_number and row.article_number == hint_article_number:
                score = min(1.0, score + 0.35)

            scored.append((row, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [
            (self._row_to_chunk_record(row), score)
            for row, score in scored[:top_k]
            if score > 0
        ]

    # ── Namespace stats ──────────────────────────────────────────────────────

    def get_namespace_stats(self, tenant_id: str, namespace_id: str) -> NamespaceStats | None:
        with self.SessionLocal() as session:
            chunk_count, source_count, last_ingested_at = session.execute(
                select(
                    func.count(ChunkRow.chunk_id),
                    func.count(func.distinct(ChunkRow.source_id)),
                    func.max(ChunkRow.created_at),
                ).where(
                    ChunkRow.tenant_id == tenant_id,
                    ChunkRow.namespace_id == namespace_id,
                )
            ).one()
            if not chunk_count:
                return None
            total_tokens = sum(
                len((c or "").split())
                for c in session.execute(
                    select(ChunkRow.content).where(
                        ChunkRow.tenant_id == tenant_id,
                        ChunkRow.namespace_id == namespace_id,
                    )
                ).scalars()
            )
            return NamespaceStats(
                namespace_id=namespace_id,
                chunk_count=int(chunk_count),
                source_count=int(source_count),
                total_tokens_indexed=total_tokens,
                last_ingested_at=last_ingested_at,
                embedding_model=settings.embedding_model,
                embedding_dim=settings.embedding_dim,
            )

    # ── Misc ─────────────────────────────────────────────────────────────────

    def mark_namespace_delete_job(self, job_id: str) -> None:
        with self.SessionLocal() as session:
            session.merge(NamespaceDeleteRow(job_id=job_id, created_at=utc_now()))
            session.commit()

    def get_uptime_seconds(self, started_at: datetime) -> int:
        return int((utc_now() - started_at).total_seconds())

    def prune_finished_jobs(self) -> None:
        threshold = utc_now() - timedelta(days=7)
        with self.SessionLocal() as session:
            session.execute(
                delete(JobRow).where(
                    JobRow.completed_at.is_not(None),
                    JobRow.completed_at < threshold,
                )
            )
            session.commit()


store = SQLStore(settings.database_url)
