"""vector column, file_bytes, callback_url, FTS index

Revision ID: 0003_vector_and_extras
Revises: 0002_pgvector_extension
Create Date: 2026-04-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_vector_and_extras"
down_revision = "0002_pgvector_extension"
branch_labels = None
depends_on = None


def _column_exists(inspector: sa.Inspector, table: str, column: str) -> bool:
    return any(c["name"] == column for c in inspector.get_columns(table))


def _index_exists(inspector: sa.Inspector, table: str, index: str) -> bool:
    return any(i["name"] == index for i in inspector.get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    dialect = bind.dialect.name

    if not _column_exists(inspector, "rag_chunks", "embedding_json"):
        op.add_column("rag_chunks", sa.Column("embedding_json", sa.Text(), nullable=True))

    if dialect == "postgresql":
        if not _index_exists(inspector, "rag_chunks", "rag_chunks_embedding_hnsw"):
            op.execute(
                "CREATE INDEX rag_chunks_embedding_hnsw "
                "ON rag_chunks USING hnsw ((embedding_json::vector(768)) vector_cosine_ops)"
            )
        if not _index_exists(inspector, "rag_chunks", "rag_chunks_content_fts"):
            op.execute(
                "CREATE INDEX rag_chunks_content_fts "
                "ON rag_chunks USING gin (to_tsvector('simple', content))"
            )

    if not _column_exists(inspector, "rag_jobs", "callback_url"):
        op.add_column("rag_jobs", sa.Column("callback_url", sa.Text(), nullable=True))

    if not _column_exists(inspector, "rag_jobs", "file_bytes"):
        op.add_column("rag_jobs", sa.Column("file_bytes", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    op.drop_column("rag_jobs", "file_bytes")
    op.drop_column("rag_jobs", "callback_url")

    if dialect == "postgresql":
        op.execute("DROP INDEX IF EXISTS rag_chunks_content_fts")
        op.execute("DROP INDEX IF EXISTS rag_chunks_embedding_hnsw")

    op.drop_column("rag_chunks", "embedding_json")
