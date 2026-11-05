import re
from datetime import date
from decimal import Decimal
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from stock_deep_research import budget as budget_mod
from stock_deep_research import research, shared
from stock_deep_research.derived import derive
from stock_deep_research.evidence import Evidence, new_run
from stock_deep_research.models import Excerpt, Finding, RunEvent, Source
from stock_deep_research.plan import QUESTIONS, SECTIONS, make_plan

TODAY = date(2026, 11, 5)
SONNET = "claude-sonnet-5-5"


@pytest.fixture
def setup(research_url, shared_url):
    shared_eng = create_engine(shared_url)
    company = shared.find_company(shared_eng, "LT")
    with Session(create_engine(research_url)) as session:
        yield session, shared_eng, company
        session.rollback()


def usage(inp=20_000, out=2_000, searches=0, fetches=0):
    return NS(
        input_tokens=inp,
        output_tokens=out,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        server_tool_use=NS(web_search_requests=searches, web_fetch_requests=fetches),
    )


def source_id(prompt: str, title: str) -> int:
    return int(re.search(r"\[S(\d+)\] " + re.escape(title), prompt).group(1))


class FakeClient:
    """Answers assessments by section, and plays one follow-up: a shared_data call, then a
    web search and fetch."""

    def __init__(self):
        self.parsed = []
        self.created = []
        self.messages = NS(parse=self.parse)
        self.beta = NS(messages=NS(create=self.create))

    def parse(self, **kw):
        prompt = kw["messages"][0]["content"]
        self.parsed.append(prompt)
        section = re.search(r"Section: (.+)", prompt).group(1)
        A, F, C, G = (
            research.Assessment,
            research.FindingOut,
            research.Citation,
            research.GapOut,
        )
        out = A(findings=[], gaps=[])
        if section == "Financials":
            q2 = source_id(prompt, "Results Q2 FY27 (consolidated)")
            figures = source_id(prompt, "Figures computed from the filings")
            out.findings = [
                F(
                    question="financials.quarters",
                    claim="Q2 FY27 consolidated profit was Rs 3,926.15 crore.",
                    claim_type="fact",
                    support="supported",
                    citations=[C(source=q2, quote="pat_cr: 3926.15")],
                    period="2026-09-30",
                    limitations="",
                ),
                F(
                    question="financials.quarters",
                    claim="Revenue grew about 10% on the year.",
                    claim_type="fact",
                    support="supported",
                    citations=[C(source=figures, quote="revenue 10.0% on Q2 FY26")],
                    period="2026-09-30",
                    limitations="",
                ),
                F(
                    question="financials.years",
                    claim="Profit doubled.",
                    claim_type="fact",
                    support="supported",
                    citations=[C(source=q2, quote="pat_cr: 7852")],  # not in the source
                    period="2026-09-30",
                    limitations="",
                ),
            ]
        elif section == "Industry and competition" and "web.example" in prompt:
            page = source_id(prompt, "EPC league table")
            out.findings = [
                F(
                    question="industry.position",
                    claim="L&T is India's largest EPC company by order book.",
                    claim_type="fact",
                    support="supported",
                    citations=[C(source=page, quote="L&T is the largest EPC company")],
                    period="",
                    limitations="",
                )
            ]
        elif section == "Industry and competition":
            out.gaps = [
                G(
                    question="industry.position",
                    missing="market shares of EPC companies",
                    look="web",
                    query="India EPC market share",
                ),
                G(question="industry.market", missing="demand drivers", look="none", query=""),
            ]
        return NS(parsed_output=out, stop_reason="end_turn", usage=usage(), model=SONNET)

    def create(self, **kw):
        self.created.append(kw)
        turn = len(self.created)
        if turn % 2 == 1:  # read the collected orders first
            return NS(
                content=[
                    NS(
                        type="tool_use",
                        id=f"t{turn}",
                        name="shared_data",
                        input={"dataset": "orders"},
                    ),
                ],
                stop_reason="tool_use",
                usage=usage(5_000, 500),
                model=SONNET,
            )
        url = f"https://web.example/epc{turn}"
        return NS(
            content=[
                NS(
                    type="server_tool_use", id=f"s{turn}", name="web_search", input={"query": "EPC"}
                ),
                NS(
                    type="web_search_tool_result",
                    tool_use_id=f"s{turn}",
                    content=[NS(url=url, title="EPC league table", page_age=None)],
                ),
                NS(type="server_tool_use", id=f"f{turn}", name="web_fetch", input={"url": url}),
                NS(
                    type="web_fetch_tool_result",
                    tool_use_id=f"f{turn}",
                    content=NS(
                        type="web_fetch_result",
                        url=url,
                        retrieved_at="2026-11-05T10:00:00Z",
                        content=NS(
                            title="EPC league table",
                            source=NS(
                                type="text",
                                media_type="text/plain",
                                data="By order book, L&T is the largest EPC company.",
                            ),
                        ),
                    ),
                ),
                NS(type="text", text="Found a league table."),
            ],
            stop_reason="end_turn",
            usage=usage(8_000, 800, searches=1, fetches=1),
            model=SONNET,
        )


def test_plan_covers_every_section():
    assert {q.section for q in QUESTIONS} == {s.key for s in SECTIONS} - {"request"}
    plan = make_plan(["Is the Saudi exposure a risk?"])
    assert plan[-1].id == "request.1" and not plan[-1].mandatory
    assert sum(q.mandatory for q in plan) == len(QUESTIONS) - 1  # geography is optional


def test_budget_reserves_the_worst_case():
    run = NS(cost_usd=Decimal("3.40"))
    b = budget_mod.Budget(run, Decimal("3.50"))
    # 30,000 characters ~ 10,000 tokens ($0.02) + 8,000 output tokens ($0.08) = $0.10
    assert b.worst_case(SONNET, 30_000, 8_000) == Decimal("0.1")
    b.reserve(SONNET, 30_000, 8_000)
    with pytest.raises(budget_mod.BudgetStop):
        b.reserve(SONNET, 30_000, 8_000, searches=1)


@pytest.mark.db
def test_derived_figures(setup):
    session, shared_eng, company = setup
    run = new_run(session, company, "test", SONNET, 5.0, "1.0")
    ev = Evidence(session, run, shared_eng, company)
    figures = derive(ev)
    text = figures.text
    assert (
        "Q2 FY27 (consolidated): PAT margin 5.9%; revenue 10.0% on Q2 FY26; PAT 15.5% on Q2 FY26"
        in text
    )
    assert "EPS, trailing four quarters (consolidated, Q3 FY26 to Q2 FY27): Rs 115.86" in text
    assert "P/E, trailing: 33.8 = close Rs 3921.5 on 2026-10-31" in text
    assert "Revenue growth a year, FY24 to FY26 (2 years, consolidated): 7.4%" in text
    assert "Balance sheet 2026-03-31 (consolidated): net debt Rs 95000 crore" in text
    assert "cash from operations 120% of PAT" in text
    # every figure names the sources it came from
    assert all(re.search(r"\[S\d+", line) for line in figures.data["lines"])


def test_context_gives_each_dataset_a_share():
    long = [
        NS(
            id=i,
            text="x" * 5_000,
            dataset="filings",
            kind="shared",
            title=f"F{i}",
            ref="r",
            published_at=None,
        )
        for i in range(10)
    ]
    short = [
        NS(
            id=100,
            text="pat_cr: 1",
            dataset="results",
            kind="shared",
            title="R",
            ref="r",
            published_at=None,
        )
    ]
    body, shown = research.context(long + short, cap=12_000)
    assert 100 in [s.id for s in shown] and len(shown) < 11 and "[S100]" in body


@pytest.mark.db
def test_research_run(setup):
    session, shared_eng, company = setup
    client = FakeClient()
    steps = []
    run = research.research(
        session,
        shared_eng,
        company,
        client=client,
        model=SONNET,
        budget_usd=5.0,
        contract_version="1.0",
        today=TODAY,
        progress=steps.append,
    )
    assert run.status == "succeeded" and run.plan["coverage"]["financials.quarters"] == "answered"
    assert steps[0] == "Reading the shared data" and "Assessing: Financials" in steps

    found = session.scalars(select(Finding).where(Finding.run_id == run.id)).all()
    by_claim = {f.claim: f for f in found}
    profit = by_claim["Q2 FY27 consolidated profit was Rs 3,926.15 crore."]
    assert profit.support == "supported" and profit.effective_date == date(2026, 9, 30)
    ex = session.get(Excerpt, profit.excerpt_ids[0])
    src = session.get(Source, ex.source_id)
    assert src.text[ex.start_char :].startswith("pat_cr: 3926.15")
    growth = by_claim["Revenue grew about 10% on the year."]
    assert (
        session.get(Source, session.get(Excerpt, growth.excerpt_ids[0]).source_id).dataset
        == "derived"
    )
    doubled = by_claim["Profit doubled."]
    assert doubled.support == "insufficient" and "no quoted passage" in doubled.limitations
    assert run.plan["coverage"]["financials.years"] == "partly"

    # follow-ups: industry.position (a web gap) was gathered and then answered from the page;
    # industry.market was skipped (nothing to look for)
    assert run.plan["coverage"]["industry.position"] == "answered"
    events = [
        e.message
        for e in session.scalars(
            select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.seq)
        )
    ]
    assert events[0].startswith("16 questions (15 mandatory); research may spend $3.50 of $5.00")
    assert any(m.startswith("industry.market: skipped") for m in events)
    assert any(m.startswith("industry.position: 2 turns, 1 searches, 1 fetches") for m in events)
    assert events[-1].startswith("succeeded:")
    tool_results = client.created[1]["messages"][2]["content"][0]
    assert tool_results["type"] == "tool_result" and "Saudi Aramco" in tool_results["content"]
    assert client.created[0]["tools"][-1]["max_uses"] == research.GATHER_FETCHES

    # costs: every call's actual cost is on the run, and the decision record adds up to it
    assert run.cost_usd == sum(
        e.cost_usd for e in session.scalars(select(RunEvent).where(RunEvent.run_id == run.id))
    )
    assert Decimal(0) < run.cost_usd <= Decimal("3.50")


@pytest.mark.db
def test_research_stops_at_the_budget(setup):
    session, shared_eng, company = setup
    client = FakeClient()
    run = research.research(
        session,
        shared_eng,
        company,
        client=client,
        model=SONNET,
        budget_usd=0.6,
        contract_version="1.0",
        today=TODAY,
    )
    assert run.status == "partial"
    assert run.cost_usd <= Decimal("0.42")  # 70% of $0.60
    last = session.scalars(
        select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.seq.desc())
    ).first()
    assert last.message.startswith("partial:")
    assert len(client.parsed) < len(SECTIONS)
