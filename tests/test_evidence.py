from decimal import Decimal
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from stock_deep_research import costs, excerpts, shared, tools, web
from stock_deep_research.evidence import DATASETS, Evidence, new_run, render, sources
from stock_deep_research.models import Excerpt, RunEvent, Source


@pytest.fixture
def ev(research_url, shared_url):
    shared_eng = create_engine(shared_url)
    company = shared.find_company(shared_eng, "LT")
    with Session(create_engine(research_url)) as session:
        run = new_run(session, company, "test", "gemini-2.5-pro", 5.0, "1.0")
        yield Evidence(session, run, shared_eng, company)
        session.rollback()


def test_render_is_one_line_per_field_with_text_last():
    body = render({"period": "Q2 FY27", "pat_cr": Decimal("3926.150"), "x": None, "text": "Body"})
    assert body == "period: Q2 FY27\npat_cr: 3926.150\n\nBody"
    assert render({"eps": Decimal("28.00")}) == "eps: 28"


@pytest.mark.db
def test_collect_keeps_every_dataset_as_sources(ev):
    counts = ev.collect()
    assert set(counts) == set(DATASETS)
    assert counts["results"] == 8 and counts["company"] == 1 and counts["peers"] == 1
    assert counts["filings"] == 2  # the board meeting has no text
    assert counts["news"] == 1  # the irrelevant article is left out
    assert counts["events"] == 1  # only the ones that passed the check
    assert counts["red_flags"] == 1  # dismissed flags left out
    assert counts["price_summary"] == 1 and counts["index_valuation"] == 1

    q2 = ev.get("shared.results:LT:2026-09-30:3m:consolidated")
    assert q2.title == "Results Q2 FY27 (consolidated)" and q2.kind == "shared"
    assert "pat_cr: 3926.15" in q2.text and q2.data["eps"] == "28.56"
    assert len(q2.content_hash) == 64

    summary = ev.get("shared.prices_daily:LT:summary:2026-10-31")
    assert "last_close: 3921.5" in summary.text and "high_52w: 3950" in summary.text

    filing = ev.get("shared.documents:11")
    assert filing.publisher == "BSE" and filing.published_at.year == 2026
    assert "Order inflow was strong" in filing.text

    call = ev.get("shared.concalls:1")
    assert 'guidance: Revenue growth of 15% kept (quote: "we retain 15%")' in call.text

    # collecting again keeps nothing twice
    before = len(sources(ev.session, ev.run.id))
    ev.collect()
    assert len(sources(ev.session, ev.run.id)) == before


@pytest.mark.db
def test_excerpts_are_located_or_marked_missing(ev):
    filing = ev.read_document(11)
    exact = excerpts.excerpt(ev.session, filing, "Rs 67,079 crore")
    assert filing.text[exact.start_char :].startswith("Rs 67,079 crore")
    reflowed = excerpts.excerpt(ev.session, filing, "67,079 crore, UP 10% on the year")
    assert reflowed.start_char == filing.text.index("67,079")
    missing = excerpts.excerpt(ev.session, filing, "Rs 70,000 crore")
    assert missing.start_char is None
    assert excerpts.verify(ev.session, exact) and excerpts.verify(ev.session, reflowed)
    assert not excerpts.verify(ev.session, missing)
    assert ev.session.scalar(select(Excerpt).where(Excerpt.id == missing.id)).quote


@pytest.mark.db
def test_research_tools(ev):
    answer, _, _ = tools.handle(ev, "shared_data", {"dataset": "orders"})
    assert answer.startswith("[S") and "Saudi Aramco" in answer and "ref: shared.orders:1" in answer
    assert "No such" in tools.handle(ev, "read_document", {"document_id": 999})[0]
    hits, _, _ = tools.handle(ev, "search_documents", {"query": "middle east"})
    assert '"document_id": 11' in hits and "Order inflow" in hits
    assert "Unknown dataset" in tools.handle(ev, "shared_data", {"dataset": "nope"})[0]
    names = [t["name"] for t in tools.TOOLS]
    assert names == ["shared_data", "search_documents", "read_document", "web_search", "web_fetch"]
    assert set(tools.TOOLS[0]["parameters"]["properties"]["dataset"]["enum"]) == set(DATASETS)


def _gemini_web_response(text, chunks, queries=("L&T peers",), inp=10_000, out=1_000):
    usage = NS(
        prompt_token_count=inp,
        candidates_token_count=out,
        cached_content_token_count=0,
        thoughts_token_count=0,
    )
    gm = NS(
        web_search_queries=list(queries),
        grounding_chunks=[NS(web=NS(uri=u, title=t)) for u, t in chunks],
    )
    return NS(
        text=text,
        candidates=[NS(finish_reason="STOP", grounding_metadata=gm)],
        usage_metadata=usage,
        model_version="gemini-2.5-pro",
    )


class FakeGeminiWebClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.models = NS(generate_content=self.generate_content)

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeHttp:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url, **kwargs):
        return self.pages[url]


@pytest.mark.db
def test_web_pages_are_kept_as_sources(ev):
    resp = _gemini_web_response(
        "L&T leads Indian EPC.",
        chunks=[
            ("https://a.example/x", "A"),
            ("https://b.example/y", "B"),
            ("https://c.example/z", "C"),
            ("https://d.example/r.pdf", "D"),
        ],
    )
    http = FakeHttp(
        {
            "https://a.example/x": NS(
                url="https://a.example/x",
                status_code=200,
                headers={"content-type": "text/plain"},
                text="L&T leads EPC.",
                content=b"L&T leads EPC.",
            ),
            "https://b.example/y": NS(
                url="https://b.example/y",
                status_code=403,
                headers={"content-type": "text/html"},
                text="",
                content=b"",
            ),
            "https://d.example/r.pdf": NS(
                url="https://d.example/r.pdf",
                status_code=200,
                headers={"content-type": "application/pdf"},
                text="",
                content=b"not-a-valid-pdf",
            ),
        }
    )
    client = FakeGeminiWebClient([resp])
    answer = web.ask_web(
        ev,
        "Who are L&T's competitors?",
        client=client,
        model="gemini-2.5-pro",
        http=http,
        max_fetches=2,  # fetch a and b; leave c and d as listed
    )

    assert answer.answer == "L&T leads Indian EPC." and answer.stop_reason == "stop"
    assert len(client.calls) == 1
    status = {s.ref: s.retrieval_status for s in answer.sources}
    assert status == {
        "https://a.example/x": "ok",
        "https://b.example/y": "blocked",
        "https://c.example/z": "listed",
        "https://d.example/r.pdf": "listed",
    }
    pdf_src = web.fetch_url(ev, "https://d.example/r.pdf", http=http)
    assert pdf_src.retrieval_status == "unreadable" and pdf_src.text is None
    page = ev.get("https://a.example/x")
    assert page.text == "L&T leads EPC." and page.kind == "web" and page.content_hash
    assert page.data["media_type"] == "text/plain"
    # cost: 10k in ($0.0125) + 1k out ($0.01) + 1 Google Search ($0.035) = $0.0575
    assert answer.cost_usd == Decimal("0.0575")
    assert ev.run.cost_usd == Decimal("0.0575") and ev.run.tool_calls == 3
    event = ev.session.scalar(select(RunEvent).where(RunEvent.run_id == ev.run.id))
    assert "1 searches, 2 fetches, 1 pages kept" in event.message


def test_costs():
    usage = NS(
        prompt_token_count=1_000_000,
        candidates_token_count=0,
        cached_content_token_count=0,
        thoughts_token_count=0,
    )
    assert costs.cost("gemini-2.5-pro", usage) == Decimal("1.2500")
    with pytest.raises(ValueError):
        costs.cost("unknown-model", usage)


def test_extract_pdf_text_handles_invalid_and_valid_pdfs():
    assert web._extract_pdf_bytes(b"JVBER") is None  # invalid/truncated PDF
    # Minimal valid 1-page PDF with "L&T Order" text stream
    pdf = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 200 200]/Parent 2 0 R"
        b"/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj\n"
        b"4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
        b"5 0 obj<</Length 44>>stream\nBT /F1 12 Tf 10 100 Td (L&T Order) Tj ET\nendstream endobj\n"
        b"xref\n0 6\n0000000000 65535 f \n0000000009 00000 n \n0000000056 00000 n \n"
        b"0000000111 00000 n \n0000000237 00000 n \n0000000305 00000 n \n"
        b"trailer<</Size 6/Root 1 0 R>>\nstartxref\n398\n%%EOF"
    )
    extracted = web._extract_pdf_bytes(pdf)
    assert extracted is not None and "L&T Order" in extracted


@pytest.mark.db
def test_evidence_command(research_url, shared_url, monkeypatch):
    from stock_deep_research.cli import app
    from stock_deep_research.config import get_settings

    monkeypatch.setenv("DATABASE_URL", research_url)
    monkeypatch.setenv("SHARED_DATABASE_URL", shared_url)
    get_settings.cache_clear()
    runner = CliRunner()
    try:
        result = runner.invoke(app, ["evidence", "LT"])
        assert result.exit_code == 0, result.stdout
        assert "results           8" in result.stdout
        run_id = int(result.stdout.split()[1].rstrip(":"))
        listed = runner.invoke(app, ["sources", str(run_id), "--dataset", "results"])
        assert listed.stdout.count("Results Q") == 6
        with Session(create_engine(research_url)) as session:
            some = session.scalar(
                select(Source).where(Source.run_id == run_id, Source.dataset == "orders")
            )
        shown = runner.invoke(app, ["source", str(some.id)])
        assert "ref        shared.orders:1" in shown.stdout and "Saudi Aramco" in shown.stdout
        assert runner.invoke(app, ["evidence", "nothing-like-this"]).exit_code == 1
    finally:
        get_settings.cache_clear()
