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


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # embedding_json: stores float list as JSON text; on Postgres we cast to vector(768) at query time
    op.add_column("rag_chunks", sa.Column("embedding_json", sa.Text(), nullable=True))

    # On Postgres: HNSW expression index using the vector cast for fast ANN search
    if dialect == "postgresql":
        op.execute(
            "CREATE INDEX rag_chunks_embedding_hnsw "
            "ON rag_chunks USING hnsw ((embedding_json::vector(768)) vector_cosine_ops)"
        )
        # GIN index for full-text search using 'simple' dictionary (language-agnostic)
        op.execute(
            "CREATE INDEX rag_chunks_content_fts "
            "ON rag_chunks USING gin (to_tsvector('simple', content))"
        )

    # callback_url: stored per-job so the worker can fire webhooks (ingest.failed, namespace.deleted)
    op.add_column("rag_jobs", sa.Column("callback_url", sa.Text(), nullable=True))

    # file_bytes: temporary storage for uploaded file payloads before the worker picks them up
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
