"""sources: the dataset a record came from, its fields as read, one row per ref in a run

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-05 19:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("dataset", sa.String(32)))
    op.add_column("sources", sa.Column("data", postgresql.JSONB()))
    op.create_unique_constraint("uq_sources_run_ref", "sources", ["run_id", "ref"])


def downgrade() -> None:
    op.drop_constraint("uq_sources_run_ref", "sources")
    op.drop_column("sources", "data")
    op.drop_column("sources", "dataset")
