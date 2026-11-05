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
        run = new_run(session, company, "test", "claude-sonnet-5-5", 5.0, "1.0")
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
    assert counts["results"] == 2 and counts["company"] == 1 and counts["peers"] == 1
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
    answer = tools.handle(ev, "shared_data", {"dataset": "orders"})
    assert answer.startswith("[S") and "Saudi Aramco" in answer and "ref: shared.orders:1" in answer
    assert "No such" in tools.handle(ev, "read_document", {"document_id": 999})
    hits = tools.handle(ev, "search_documents", {"query": "middle east"})
    assert '"document_id": 11' in hits and "Order inflow" in hits
    assert "Unknown dataset" in tools.handle(ev, "shared_data", {"dataset": "nope"})
    names = [t["name"] for t in tools.TOOLS]
    assert names == ["shared_data", "search_documents", "read_document"]
    assert set(tools.TOOLS[0]["input_schema"]["properties"]["dataset"]["enum"]) == set(DATASETS)


def _response(content, stop="end_turn", searches=1, fetches=2):
    usage = NS(
        input_tokens=10_000,
        output_tokens=1_000,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        server_tool_use=NS(web_search_requests=searches, web_fetch_requests=fetches),
    )
    return NS(content=content, stop_reason=stop, usage=usage)


def _page(url, body, media="text/plain"):
    kind = "text" if media == "text/plain" else "base64"
    return NS(
        type="web_fetch_result",
        url=url,
        retrieved_at="2026-11-05T10:00:00Z",
        content=NS(title=f"Page {url}", source=NS(type=kind, data=body, media_type=media)),
    )


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


@pytest.mark.db
def test_web_pages_are_kept_as_sources(ev):
    first = _response(
        [
            NS(type="server_tool_use", id="s1", name="web_search", input={"query": "L&T peers"}),
            NS(
                type="web_search_tool_result",
                tool_use_id="s1",
                content=[
                    NS(url="https://a.example/x", title="A", page_age="2 days ago"),
                    NS(url="https://b.example/y", title="B", page_age=None),
                    NS(url="https://c.example/z", title="C", page_age=None),
                ],
            ),
            NS(
                type="server_tool_use",
                id="f1",
                name="web_fetch",
                input={"url": "https://a.example/x"},
            ),
            NS(
                type="web_fetch_tool_result",
                tool_use_id="f1",
                content=_page("https://a.example/x", "L&T leads EPC."),
            ),
        ],
        stop="pause_turn",
    )
    second = _response(
        [
            NS(
                type="server_tool_use",
                id="f2",
                name="web_fetch",
                input={"url": "https://b.example/y"},
            ),
            NS(
                type="web_fetch_tool_result",
                tool_use_id="f2",
                content=NS(type="web_fetch_tool_result_error", error_code="url_not_accessible"),
            ),
            NS(
                type="server_tool_use",
                id="f3",
                name="web_fetch",
                input={"url": "https://d.example/r.pdf"},
            ),
            NS(
                type="web_fetch_tool_result",
                tool_use_id="f3",
                content=_page("https://d.example/r.pdf", "JVBER", "application/pdf"),
            ),
            NS(type="text", text="L&T leads Indian EPC."),
        ],
        searches=0,
        fetches=2,
    )
    client = FakeClient([first, second])
    answer = web.ask_web(ev, "Who are L&T's competitors?", client=client, model="claude-sonnet-5-5")

    assert answer.answer == "L&T leads Indian EPC." and answer.stop_reason == "end_turn"
    assert len(client.calls) == 2  # resumed after pause_turn
    assert client.calls[1]["messages"][-1]["role"] == "assistant"
    assert client.calls[0]["tools"][0]["type"] == "web_search_20260209"
    status = {s.ref: s.retrieval_status for s in answer.sources}
    assert status == {
        "https://a.example/x": "ok",
        "https://b.example/y": "blocked",
        "https://c.example/z": "listed",
        "https://d.example/r.pdf": "unreadable",
    }
    page = ev.get("https://a.example/x")
    assert page.text == "L&T leads EPC." and page.kind == "web" and page.content_hash
    assert page.retrieved_at.year == 2026 and page.data["media_type"] == "text/plain"
    assert ev.get("https://d.example/r.pdf").text is None
    # cost: two responses of 10k in / 1k out at Sonnet prices, plus one search
    assert answer.cost_usd == Decimal("0.0700")
    assert ev.run.cost_usd == Decimal("0.0700") and ev.run.tool_calls == 5
    event = ev.session.scalar(select(RunEvent).where(RunEvent.run_id == ev.run.id))
    assert "1 searches, 4 fetches, 1 pages kept" in event.message


def test_costs():
    usage = NS(input_tokens=1_000_000, output_tokens=0, server_tool_use=None)
    assert costs.cost("claude-sonnet-5-5", usage) == Decimal("2.0000")
    with pytest.raises(ValueError):
        costs.cost("unknown-model", usage)


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
        assert "results           2" in result.stdout
        run_id = int(result.stdout.split()[1].rstrip(":"))
        listed = runner.invoke(app, ["sources", str(run_id), "--dataset", "results"])
        assert listed.stdout.count("Results Q") == 2
        with Session(create_engine(research_url)) as session:
            some = session.scalar(
                select(Source).where(Source.run_id == run_id, Source.dataset == "orders")
            )
        shown = runner.invoke(app, ["source", str(some.id)])
        assert "ref        shared.orders:1" in shown.stdout and "Saudi Aramco" in shown.stdout
        assert runner.invoke(app, ["evidence", "nothing-like-this"]).exit_code == 1
    finally:
        get_settings.cache_clear()
