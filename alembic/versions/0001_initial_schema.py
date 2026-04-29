"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-04-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = inspector.get_table_names()

    if "rag_jobs" not in existing:
        op.create_table(
            "rag_jobs",
            sa.Column("job_id", sa.String(length=64), nullable=False),
            sa.Column("tenant_id", sa.String(length=255), nullable=False),
            sa.Column("namespace_id", sa.String(length=255), nullable=True),
            sa.Column("source_id", sa.String(length=255), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("progress", sa.JSON(), nullable=True),
            sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("estimated_completion_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error", sa.JSON(), nullable=True),
            sa.Column("content", sa.Text(), nullable=True),
            sa.Column("mime_type", sa.String(length=128), nullable=True),
            sa.Column("source_url", sa.Text(), nullable=True),
            sa.Column("source_title", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("job_id"),
        )
        op.create_index("ix_rag_jobs_tenant_id", "rag_jobs", ["tenant_id"])

    if "rag_idempotency" not in existing:
        op.create_table(
            "rag_idempotency",
            sa.Column("tenant_id", sa.String(length=255), nullable=False),
            sa.Column("key", sa.String(length=64), nullable=False),
            sa.Column("payload_hash", sa.String(length=255), nullable=False),
            sa.Column("job_id", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("tenant_id", "key"),
        )
        op.create_index("ix_rag_idempotency_job_id", "rag_idempotency", ["job_id"])

    if "rag_chunks" not in existing:
        op.create_table(
            "rag_chunks",
            sa.Column("chunk_id", sa.String(length=64), nullable=False),
            sa.Column("tenant_id", sa.String(length=255), nullable=False),
            sa.Column("namespace_id", sa.String(length=255), nullable=False),
            sa.Column("source_id", sa.String(length=255), nullable=False),
            sa.Column("source_url", sa.Text(), nullable=True),
            sa.Column("source_title", sa.Text(), nullable=True),
            sa.Column("article_number", sa.String(length=64), nullable=True),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("chunk_id"),
        )
        op.create_index("ix_rag_chunks_tenant_id", "rag_chunks", ["tenant_id"])
        op.create_index("ix_rag_chunks_namespace_id", "rag_chunks", ["namespace_id"])
        op.create_index("ix_rag_chunks_source_id", "rag_chunks", ["source_id"])
        op.create_index("ix_rag_chunks_article_number", "rag_chunks", ["article_number"])

    if "rag_namespace_delete_jobs" not in existing:
        op.create_table(
            "rag_namespace_delete_jobs",
            sa.Column("job_id", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("job_id"),
        )
        op.create_index("ix_rag_namespace_delete_jobs_created_at", "rag_namespace_delete_jobs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_rag_namespace_delete_jobs_created_at", table_name="rag_namespace_delete_jobs")
    op.drop_table("rag_namespace_delete_jobs")
    op.drop_index("ix_rag_chunks_article_number", table_name="rag_chunks")
    op.drop_index("ix_rag_chunks_source_id", table_name="rag_chunks")
    op.drop_index("ix_rag_chunks_namespace_id", table_name="rag_chunks")
    op.drop_index("ix_rag_chunks_tenant_id", table_name="rag_chunks")
    op.drop_table("rag_chunks")
    op.drop_index("ix_rag_idempotency_job_id", table_name="rag_idempotency")
    op.drop_table("rag_idempotency")
    op.drop_index("ix_rag_jobs_tenant_id", table_name="rag_jobs")
    op.drop_table("rag_jobs")
