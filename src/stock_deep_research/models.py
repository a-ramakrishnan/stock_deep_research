"""This project's own tables (its database, not stock_news_analyzer's).

A research run turns a request ("research LT") into a report. Everything it reads is a
`Source` (a shared record or a fetched web page, kept as retrieved); the passages it relies on
are `Excerpt`s; each claim is a `Finding` that cites excerpts and says how well they support it;
the `Report` is built from findings. See docs/design/design.md.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Run(Base):
    """One research request and its execution."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    isin: Mapped[str] = mapped_column(String(12), index=True)
    symbol: Mapped[str | None] = mapped_column(String(32))
    request: Mapped[str] = mapped_column(Text)  # what was asked, in the user's words
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # queued, running, succeeded, partial, failed, cancelled
    status: Mapped[str] = mapped_column(String(16), default="queued")
    model: Mapped[str] = mapped_column(String(64))
    budget_usd: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(8, 4), default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    plan: Mapped[dict] = mapped_column(JSONB, default=dict)  # questions, mandatory or not
    contract_version: Mapped[str | None] = mapped_column(String(16))  # shared views read
    error: Mapped[str | None] = mapped_column(Text)


class RunEvent(Base):
    """A step of a run, for the user: the question, the tool, what it found, why it stopped.
    A decision record, not the model's hidden reasoning."""

    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    stage: Mapped[str] = mapped_column(String(16))  # plan, collect, assess, write, review
    message: Mapped[str] = mapped_column(Text)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(8, 4), default=0)


class Source(Base):
    """Something read: a record of the shared data or a web page, as it was when retrieved."""

    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("run_id", "ref", name="uq_sources_run_ref"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(8))  # shared, web
    dataset: Mapped[str | None] = mapped_column(String(32))  # results, documents, ... or web
    ref: Mapped[str] = mapped_column(Text)  # 'shared.results:LT:Q2 FY27' or the URL
    title: Mapped[str | None] = mapped_column(Text)
    publisher: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # ok; listed (a search result, not fetched); blocked, failed, unreadable (no text kept)
    retrieval_status: Mapped[str] = mapped_column(String(16), default="ok")
    content_hash: Mapped[str | None] = mapped_column(String(64))
    text: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict | None] = mapped_column(JSONB)  # a shared record's fields, as read


class Excerpt(Base):
    """A passage of a source that a finding relies on, located in its text."""

    __tablename__ = "excerpts"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    quote: Mapped[str] = mapped_column(Text)
    start_char: Mapped[int | None] = mapped_column(Integer)  # in Source.text; None if not found


class Finding(Base):
    """An atomic claim and its support."""

    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    section: Mapped[str] = mapped_column(String(32))
    question: Mapped[str | None] = mapped_column(Text)
    claim: Mapped[str] = mapped_column(Text)
    claim_type: Mapped[str] = mapped_column(String(16))  # fact, interpretation
    support: Mapped[str] = mapped_column(
        String(16)
    )  # supported, conflicting, insufficient, inferred
    excerpt_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), default=list)
    effective_date: Mapped[date | None] = mapped_column(Date)  # the period or event date
    limitations: Mapped[str | None] = mapped_column(Text)


class Report(Base):
    """A version of a stock's report, built from a run's findings."""

    __tablename__ = "reports"
    __table_args__ = (UniqueConstraint("isin", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    isin: Mapped[str] = mapped_column(String(12), index=True)
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    outcome: Mapped[str] = mapped_column(
        String(32)
    )  # validated, validated_with_limitations, rejected
    sections: Mapped[dict] = mapped_column(JSONB, default=dict)
    coverage: Mapped[dict] = mapped_column(JSONB, default=dict)  # question -> answered or the gap
    markdown: Mapped[str | None] = mapped_column(Text)
