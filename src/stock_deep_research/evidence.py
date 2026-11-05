"""Evidence: everything a run reads, kept as a `Source`, as it was when read.

The shared data is read through stock_news_analyzer's `shared` views (docs/shared-data.md
there). Each record becomes a source with a stable ref (`shared.results:LT:2026-09-30:3m:
consolidated`), its fields as read (`data`) and a plain-text rendering (`text`) that excerpts
quote from: one `field: value` line per field, so a number in a report can be traced to the line
it came from. Long-running series (prices, index valuations, deals) are one source per series,
a line per row.

Web pages are kept the same way by `web.py`. Excerpts are located in a source's text by
`excerpts.py`.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from stock_deep_research.models import Run, RunEvent, Source
from stock_deep_research.shared import Company


def _value(v) -> str:
    if isinstance(v, Decimal):
        return format(v.normalize(), "f") if v == v.to_integral() else str(v)
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, bool):
        return "yes" if v else "no"
    return str(v)


def render(row: dict, skip: tuple[str, ...] = ()) -> str:
    """A record as `field: value` lines, empty fields left out, long text (a `text` field) last."""
    lines = [
        f"{k}: {_value(v)}"
        for k, v in row.items()
        if v is not None and v != "" and k not in skip and k != "text"
    ]
    if row.get("text"):
        lines += ["", str(row["text"])]
    return "\n".join(lines)


def render_rows(rows: list[dict]) -> str:
    """A series, one line per row: `trade_date=2026-10-31; close=3921.5`."""
    return "\n".join(
        "; ".join(f"{k}={_value(v)}" for k, v in r.items() if v is not None) for r in rows
    )


def jsonable(row: dict) -> dict:
    return {
        k: (_value(v) if isinstance(v, Decimal | date | datetime) else v) for k, v in row.items()
    }


def digest(body: str | None) -> str | None:
    return hashlib.sha256(body.encode()).hexdigest() if body is not None else None


@dataclass(frozen=True)
class Dataset:
    """One kind of shared record and how it becomes sources."""

    name: str
    about: str  # what it holds, for the research tools' description
    sql: str  # parameters :isin and :limit; newest first
    ref: Callable[[dict, str], str]  # (row, symbol) -> ref, for per-record datasets
    title: Callable[[dict], str]
    limit: int = 20
    series: bool = False  # one source for all rows
    published: str | None = None  # the column that dates the record's release
    publisher: Callable[[dict], str | None] = lambda r: None


def _sym(c: Company) -> str:
    return c.nse_symbol or c.isin


DATASETS: dict[str, Dataset] = {
    d.name: d
    for d in [
        Dataset(
            "company",
            "The company: name, industry, market cap, where it is listed, its benchmark index.",
            "SELECT * FROM shared.companies WHERE isin = :isin LIMIT :limit",
            lambda r, s: f"shared.companies:{s}",
            lambda r: f"{r['name']}: profile",
            limit=1,
        ),
        Dataset(
            "peers",
            "The listed peers stock_news_analyzer compares it with.",
            """SELECT c.nse_symbol, c.isin, c.name, c.industry, c.market_cap_cr
               FROM shared.peers p JOIN shared.companies c ON c.isin = p.peer_isin
               WHERE p.isin = :isin ORDER BY c.market_cap_cr DESC NULLS LAST LIMIT :limit""",
            lambda r, s: f"shared.peers:{s}",
            lambda r: "Peers",
            series=True,
        ),
        Dataset(
            "results",
            "Reported results per period (quarter, half, year; consolidated and standalone): "
            "revenue, other income, finance costs, depreciation, profit before tax, profit, EPS. "
            "Rs crore; EPS in Rs. source_model 'xbrl' means as filed.",
            """SELECT * FROM shared.results WHERE isin = :isin
               ORDER BY period_end DESC, "primary" DESC, months LIMIT :limit""",
            lambda r, s: f"shared.results:{s}:{r['period_end']}:{r['months']}m:{r['basis']}",
            lambda r: f"Results {r['period']} ({r['basis']})",
            limit=40,
        ),
        Dataset(
            "balance_sheets",
            "Balance sheet and cash flow at half-year and year ends: equity, borrowings, cash, "
            "total assets, cash from operations, capex (Rs crore; cash flow over `months`).",
            "SELECT * FROM shared.balance_sheets WHERE isin = :isin "
            "ORDER BY period_end DESC LIMIT :limit",
            lambda r, s: f"shared.balance_sheets:{s}:{r['period_end']}:{r['basis']}",
            lambda r: f"Balance sheet {r['period_end']} ({r['basis']})",
            limit=12,
        ),
        Dataset(
            "price_summary",
            "The latest close and the 52-week high and low.",
            """WITH p AS (SELECT trade_date, close, high, low FROM shared.prices_daily
                          WHERE isin = :isin),
                    last AS (SELECT max(trade_date) AS d FROM p)
               SELECT (SELECT d FROM last) AS as_of,
                      (SELECT close FROM p WHERE trade_date = (SELECT d FROM last) LIMIT 1)
                          AS last_close,
                      max(high) AS high_52w, min(low) AS low_52w
               FROM p WHERE trade_date > (SELECT d FROM last) - 365
               HAVING count(*) > 0 LIMIT :limit""",
            lambda r, s: f"shared.prices_daily:{s}:summary:{r['as_of']}",
            lambda r: f"Price on {r['as_of']}",
            limit=1,
        ),
        Dataset(
            "prices_monthly",
            "Month-end closing prices (not adjusted for splits and bonuses; see "
            "corporate_actions) and volume, newest first.",
            """SELECT DISTINCT ON (date_trunc('month', trade_date)) trade_date, close, volume
               FROM shared.prices_daily WHERE isin = :isin
               ORDER BY date_trunc('month', trade_date) DESC, trade_date DESC LIMIT :limit""",
            lambda r, s: f"shared.prices_daily:{s}:monthly",
            lambda r: "Month-end prices",
            limit=60,
            series=True,
        ),
        Dataset(
            "index_valuation",
            "Month-end close, P/E and P/B of the Nifty 50 and of the company's benchmark index.",
            """SELECT DISTINCT ON (index_name, date_trunc('month', trade_date))
                      index_name, trade_date, close, pe, pb
               FROM shared.index_daily
               WHERE index_name IN ('NIFTY 50',
                     (SELECT sector_index FROM shared.companies WHERE isin = :isin))
                 AND trade_date > current_date - 365 * 5
               ORDER BY index_name, date_trunc('month', trade_date) DESC, trade_date DESC
               LIMIT :limit""",
            lambda r, s: f"shared.index_daily:{s}:benchmarks",
            lambda r: "Index valuations",
            limit=130,
            series=True,
        ),
        Dataset(
            "corporate_actions",
            "Dividends, splits, bonuses and other corporate actions, with their price factors.",
            "SELECT ex_date, kind, subject, price_factor, dividend FROM shared.corporate_actions "
            "WHERE isin = :isin ORDER BY ex_date DESC LIMIT :limit",
            lambda r, s: f"shared.corporate_actions:{s}",
            lambda r: "Corporate actions",
            limit=40,
            series=True,
        ),
        Dataset(
            "shareholding",
            "Shareholding by quarter: promoters, FIIs, DIIs, mutual funds, retail, pledges.",
            "SELECT * FROM shared.shareholding WHERE isin = :isin "
            "ORDER BY quarter_end DESC LIMIT :limit",
            lambda r, s: f"shared.shareholding:{s}:{r['quarter_end']}",
            lambda r: f"Shareholding {r['quarter_end']}",
            limit=8,
            published="filed_at",
        ),
        Dataset(
            "filings",
            "Exchange filings with their text (results, order wins, board meetings, "
            "presentations, ...), newest first.",
            """SELECT document_id, source, doc_type, title, subject, published_at, attachment_url,
                      text_status, text
               FROM shared.documents
               WHERE isin = :isin AND source IN ('bse', 'nse') AND text_status IN ('ok', 'partial')
               ORDER BY published_at DESC LIMIT :limit""",
            lambda r, s: f"shared.documents:{r['document_id']}",
            lambda r: r["title"],
            limit=30,
            published="published_at",
            publisher=lambda r: r["source"].upper(),
        ),
        Dataset(
            "news",
            "News articles about the company that stock_news_analyzer judged relevant, the most "
            "important first, with its one-line summary.",
            """SELECT d.document_id, d.title, d.publisher, d.url, d.published_at, t.topic,
                      t.sentiment, t.importance, t.summary, d.text
               FROM shared.documents d
               JOIN shared.news_triage t ON t.document_id = d.document_id AND t.isin = d.isin
               WHERE d.isin = :isin AND d.source LIKE 'news:%' AND t.relevant
                 AND t.duplicate_of IS NULL
               ORDER BY t.importance DESC NULLS LAST, d.published_at DESC LIMIT :limit""",
            lambda r, s: f"shared.documents:{r['document_id']}",
            lambda r: r["title"],
            limit=30,
            published="published_at",
            publisher=lambda r: r["publisher"],
        ),
        Dataset(
            "events",
            "Events read from filings and checked: results, orders, management changes, "
            "ratings, disputes, ... with value, counterparty and the quote they rest on.",
            """SELECT event_id, document_id, published_at, event_type, headline, event_date,
                      sentiment, importance, value_cr, counterparty, person, role, action, quote
               FROM shared.events WHERE isin = :isin AND shown
               ORDER BY published_at DESC LIMIT :limit""",
            lambda r, s: f"shared.events:{r['event_id']}",
            lambda r: r["headline"],
            limit=60,
            published="published_at",
        ),
        Dataset(
            "orders",
            "Orders won: client, segment, geography, value, execution period.",
            "SELECT * FROM shared.orders WHERE isin = :isin "
            "ORDER BY announced_at DESC LIMIT :limit",
            lambda r, s: f"shared.orders:{r['order_id']}",
            lambda r: f"Order: {r['client'] or r['description'] or r['order_id']}",
            limit=40,
            published="announced_at",
        ),
        Dataset(
            "order_books",
            "Order book by quarter: order book, inflow, revenue.",
            "SELECT * FROM shared.order_books WHERE isin = :isin "
            "ORDER BY period_end DESC LIMIT :limit",
            lambda r, s: f"shared.order_books:{s}:{r['period_end']}",
            lambda r: f"Order book {r['quarter']}",
            limit=12,
        ),
        Dataset(
            "concalls",
            "Earnings calls analysed: tone, summary, and the points made (guidance, demand, "
            "margins, risks) with quotes.",
            """SELECT c.concall_id, c.document_id, c.quarter, c.period_end, c.call_date, c.tone,
                      c.summary,
                      (SELECT string_agg(p.category || ': ' || p.text
                                 || coalesce(' (quote: "' || p.quote || '")', ''), E'\n')
                       FROM shared.concall_points p WHERE p.concall_id = c.concall_id) AS text
               FROM shared.concalls c WHERE c.isin = :isin
               ORDER BY c.period_end DESC LIMIT :limit""",
            lambda r, s: f"shared.concalls:{r['concall_id']}",
            lambda r: f"Earnings call {r['quarter']}",
            limit=8,
            published="call_date",
        ),
        Dataset(
            "promises",
            "What management promised (metric, target, due) and whether it was kept.",
            "SELECT * FROM shared.promises WHERE isin = :isin "
            "ORDER BY due_date DESC NULLS LAST LIMIT :limit",
            lambda r, s: f"shared.promises:{r['promise_id']}",
            lambda r: f"Promise: {r['statement'][:80]}",
            limit=50,
        ),
        Dataset(
            "red_flags",
            "Red flags raised (auditor, pledges, related parties, delays, ...), not dismissed.",
            "SELECT * FROM shared.red_flags WHERE isin = :isin AND dismissed_at IS NULL "
            "ORDER BY flagged_on DESC LIMIT :limit",
            lambda r, s: f"shared.red_flags:{r['flag_id']}",
            lambda r: f"Red flag: {r['title']}",
            limit=30,
            published="flagged_on",
        ),
        Dataset(
            "deals",
            "Bulk and block deals: who bought or sold, how much, at what price.",
            "SELECT trade_date, kind, client, side, quantity, price FROM shared.deals "
            "WHERE isin = :isin ORDER BY trade_date DESC LIMIT :limit",
            lambda r, s: f"shared.deals:{s}",
            lambda r: "Bulk and block deals",
            limit=50,
            series=True,
        ),
    ]
}


def _as_datetime(v) -> datetime | None:
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    return None


class Evidence:
    """What a run has read. `keep` stores a source once per ref and returns it."""

    def __init__(self, session: Session, run: Run, shared: Engine, company: Company):
        self.session = session
        self.run = run
        self.shared = shared
        self.company = company
        self.touched: list[int] = []  # ids of sources kept or re-read, in order (see `mark`)

    def event(self, stage: str, message: str, cost: Decimal = Decimal(0)) -> RunEvent:
        """Add a step to the run's decision record, and its cost to the run's."""
        seq = (
            self.session.scalar(
                select(func.max(RunEvent.seq)).where(RunEvent.run_id == self.run.id)
            )
            or 0
        ) + 1
        ev = RunEvent(run_id=self.run.id, seq=seq, stage=stage, message=message, cost_usd=cost)
        self.session.add(ev)
        self.run.cost_usd = (self.run.cost_usd or Decimal(0)) + cost
        self.session.flush()
        return ev

    def get(self, ref: str) -> Source | None:
        return self.session.scalar(
            select(Source).where(Source.run_id == self.run.id, Source.ref == ref)
        )

    def mark(self) -> int:
        """A point in `touched`: `touched[mark:]` is what was read since."""
        return len(self.touched)

    def keep(
        self,
        *,
        kind: str,
        dataset: str,
        ref: str,
        title: str | None = None,
        body: str | None = None,
        publisher: str | None = None,
        published_at: datetime | None = None,
        status: str = "ok",
        data: dict | None = None,
        retrieved_at: datetime | None = None,
    ) -> Source:
        source = self.get(ref)
        if source is not None:
            if source.text is not None or (body is None and status == "listed"):
                self.touched.append(source.id)
                return source  # read once; the first reading stands
            # a search result now fetched (or found unfetchable): record the fetch
            data = {**(source.data or {}), **(data or {})}
        else:
            source = Source(run_id=self.run.id, kind=kind, dataset=dataset, ref=ref)
            self.session.add(source)
        source.title = title or source.title
        source.publisher = publisher or source.publisher
        source.published_at = published_at or source.published_at
        source.retrieval_status = status
        source.text = body
        source.content_hash = digest(body)
        source.data = data
        if retrieved_at is not None:
            source.retrieved_at = retrieved_at
        self.session.flush()
        self.touched.append(source.id)
        return source

    def read(self, name: str, limit: int | None = None) -> list[Source]:
        """One dataset of the shared data, kept as sources (newest first)."""
        d = DATASETS[name]
        with self.shared.connect() as conn:
            rows = [
                dict(r._mapping)
                for r in conn.execute(
                    text(d.sql), {"isin": self.company.isin, "limit": limit or d.limit}
                )
            ]
        if not rows:
            return []
        sym = _sym(self.company)
        if d.series:
            return [
                self.keep(
                    kind="shared",
                    dataset=name,
                    ref=d.ref(rows[0], sym),
                    title=d.title(rows[0]),
                    body=render_rows(rows),
                    data={"rows": [jsonable(r) for r in rows]},
                )
            ]
        return [
            self.keep(
                kind="shared",
                dataset=name,
                ref=d.ref(r, sym),
                title=d.title(r),
                body=render(r),
                publisher=d.publisher(r),
                published_at=_as_datetime(r.get(d.published)) if d.published else None,
                data=jsonable({k: v for k, v in r.items() if k != "text"}),
            )
            for r in rows
        ]

    def collect(self) -> dict[str, int]:
        """Every dataset, at its default depth. Returns sources kept per dataset."""
        return {name: len(self.read(name)) for name in DATASETS}

    def read_document(self, document_id: int) -> Source | None:
        """One document of the shared data (a filing or article) with its full text."""
        with self.shared.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT document_id, source, doc_type, title, subject, published_at, url, "
                    "publisher, attachment_url, text_status, text FROM shared.documents "
                    "WHERE document_id = :id AND isin = :isin LIMIT 1"
                ),
                {"id": document_id, "isin": self.company.isin},
            ).first()
        if row is None:
            return None
        r = dict(row._mapping)
        news = r["source"].startswith("news:")
        return self.keep(
            kind="shared",
            dataset="news" if news else "filings",
            ref=f"shared.documents:{r['document_id']}",
            title=r["title"],
            body=render(r),
            publisher=r["publisher"] if news else r["source"].upper(),
            published_at=r["published_at"],
            status="ok" if r["text"] else "unreadable",
            data=jsonable({k: v for k, v in r.items() if k != "text"}),
        )

    def search_documents(self, query: str, limit: int = 10) -> list[dict]:
        """Filings and articles about the company whose title or text mention `query`, with a
        snippet around the first mention. Not kept: read one with `read_document`."""
        with self.shared.connect() as conn:
            rows = conn.execute(
                text(
                    """SELECT document_id, doc_type, title, published_at,
                              substr(coalesce(text, ''),
                                     greatest(strpos(lower(coalesce(text, '')), lower(:q)) - 150,
                                              1), 400) AS snippet
                       FROM shared.documents
                       WHERE isin = :isin AND (title ILIKE :like OR text ILIKE :like)
                       ORDER BY published_at DESC LIMIT :limit"""
                ),
                {"isin": self.company.isin, "q": query, "like": f"%{query}%", "limit": limit},
            )
            return [dict(r._mapping) for r in rows]


def new_run(
    session: Session,
    company: Company,
    request: str,
    model: str,
    budget_usd: float,
    contract_version: str | None,
) -> Run:
    run = Run(
        isin=company.isin,
        symbol=company.nse_symbol,
        request=request,
        status="running",
        model=model,
        budget_usd=Decimal(str(budget_usd)),
        cost_usd=Decimal(0),
        tool_calls=0,
        plan={},
        contract_version=contract_version,
        started_at=datetime.now(UTC),
    )
    session.add(run)
    session.flush()
    return run


def sources(session: Session, run_id: int) -> list[Source]:
    return list(
        session.scalars(
            select(Source)
            .where(Source.run_id == run_id)
            .order_by(
                Source.kind, Source.dataset, Source.published_at.desc().nulls_last(), Source.id
            )
        )
    )
