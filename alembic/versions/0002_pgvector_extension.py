"""pgvector extension bootstrap

Revision ID: 0002_pgvector_extension
Revises: 0001_initial_schema
Create Date: 2026-04-24
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0002_pgvector_extension"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP EXTENSION IF EXISTS vector")
