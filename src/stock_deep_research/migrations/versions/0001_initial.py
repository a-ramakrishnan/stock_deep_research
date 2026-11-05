"""The research tables: runs and their events, sources, excerpts, findings, reports

Revision ID: 0001
Revises:
Create Date: 2026-11-05 17:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("isin", sa.String(12), nullable=False, index=True),
        sa.Column("symbol", sa.String(32)),
        sa.Column("request", sa.Text(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("budget_usd", sa.Numeric(8, 4), nullable=False),
        sa.Column("cost_usd", sa.Numeric(8, 4), nullable=False, server_default="0"),
        sa.Column("tool_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("plan", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("contract_version", sa.String(16)),
        sa.Column("error", sa.Text()),
    )
    op.create_table(
        "run_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(8, 4), nullable=False, server_default="0"),
        sa.UniqueConstraint("run_id", "seq"),
    )
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("ref", sa.Text(), nullable=False),
        sa.Column("title", sa.Text()),
        sa.Column("publisher", sa.Text()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("retrieval_status", sa.String(16), nullable=False, server_default="ok"),
        sa.Column("content_hash", sa.String(64)),
        sa.Column("text", sa.Text()),
    )
    op.create_table(
        "excerpts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("start_char", sa.Integer()),
    )
    op.create_table(
        "findings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("section", sa.String(32), nullable=False),
        sa.Column("question", sa.Text()),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.Column("claim_type", sa.String(16), nullable=False),
        sa.Column("support", sa.String(16), nullable=False),
        sa.Column(
            "excerpt_ids", postgresql.ARRAY(sa.BigInteger()), nullable=False, server_default="{}"
        ),
        sa.Column("effective_date", sa.Date()),
        sa.Column("limitations", sa.Text()),
    )
    op.create_table(
        "reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id", sa.Integer(), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("isin", sa.String(12), nullable=False, index=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("sections", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("coverage", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("markdown", sa.Text()),
        sa.UniqueConstraint("isin", "version"),
    )


def downgrade() -> None:
    for table in ("reports", "findings", "excerpts", "sources", "run_events", "runs"):
        op.drop_table(table)
