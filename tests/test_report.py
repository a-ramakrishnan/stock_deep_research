import re
from datetime import date
from decimal import Decimal
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from stock_deep_research import excerpts, report, shared
from stock_deep_research.derived import derive
from stock_deep_research.evidence import Evidence, new_run
from stock_deep_research.models import Finding, Report, RunEvent
from stock_deep_research.plan import Question, to_json
from stock_deep_research.review import Cited, check_paragraph, figures, unsupported_figures

TODAY = date(2026, 11, 5)
SONNET = "claude-sonnet-5-5"


# ------------------------------------------------------------------ the checks


def test_figures_leave_out_labels_years_days_and_counts():
    text = "In Q2 FY27 [F3], on 28 Oct 2026, three of 4 segments grew 10.0% to Rs 67,079 crore"
    assert [w for _, _, w in figures(text)] == ["10.0", "67,079"]
    assert [w for _, _, w in figures("P/E of 33.8 and 2 lakh shares; 1,23,456")] == [
        "33.8",
        "2",
        "1,23,456",
    ]


def test_figures_must_be_in_the_cited_passages_allowing_for_rounding():
    quotes = ["revenue_cr: 67079.45", "Q2 FY27: revenue 9.97% on Q2 FY26; PAT -8.9%"]
    assert unsupported_figures("Revenue was Rs 67,079 crore, up 10.0% [F1]", quotes) == []
    assert unsupported_figures("Profit fell 8.9% [F1]", quotes) == []
    assert unsupported_figures("Revenue was Rs 67,080 crore [F1]", quotes) == ["67,080"]
    assert unsupported_figures("up 10% [F1]", quotes) == []
    assert unsupported_figures("up 9.9% [F1]", quotes) == ["9.9"]


def test_check_paragraph():
    cited = {
        1: Cited(1, "fact", "supported", date(2026, 9, 30), ["pat_cr: 3926.15"]),
        2: Cited(2, "interpretation", "inferred", None, []),
        3: Cited(3, "fact", "supported", date(2027, 1, 20), ["board meeting on 20 Jan 2027"]),
    }

    def problems(kind, text, section="financials"):
        return [i.problem for i in check_paragraph("x", kind, text, cited, section, TODAY)]

    assert problems("fact", "Profit was Rs 3,926 crore [F1].") == []
    assert "a fact paragraph that cites no finding" in problems("fact", "Profit rose.")
    assert any("don't exist: F9" in p for p in problems("fact", "Profit [F1][F9]."))
    assert any("no supported fact" in p for p in problems("fact", "It is cheap [F2]."))
    assert problems("interpretation", "This suggests pricing power [F2].") == []
    assert any("not in the passages" in p for p in problems("gap", "Market share is 25%."))
    assert any("dated after today" in p for p in problems("fact", "Board meets [F3]."))
    assert problems("fact", "Board meets [F3].", section="catalysts") == []
    assert any("advice" in p for p in problems("interpretation", "We recommend it [F2]."))
    assert any("advice" in p for p in problems("interpretation", "A target price of [F2]."))
    assert problems("fact", "It sells cement to builders [F1].") == []


# ------------------------------------------------------------------ the whole step


@pytest.fixture
def run_with_findings(research_url, shared_url):
    shared_eng = create_engine(shared_url)
    company = shared.find_company(shared_eng, "LT")
    with Session(create_engine(research_url)) as session:
        run = new_run(session, company, "report test", SONNET, 5.0, "1.0")
        ev = Evidence(session, run, shared_eng, company)
        ev.collect()
        figures_src = derive(ev)
        page = ev.keep(
            kind="web",
            dataset="web",
            ref="https://web.example/epc",
            title="EPC league table",
            publisher="Example News",
            body="By order book, L&T is the largest EPC company in India.",
        )
        q2 = ev.get("shared.results:LT:2026-09-30:3m:consolidated")

        def finding(question, claim, kind, support, quotes):
            ids = [excerpts.excerpt(session, src, q).id for src, q in quotes]
            f = Finding(
                run_id=run.id,
                section=question.split(".")[0],
                question=question,
                claim=claim,
                claim_type=kind,
                support=support,
                excerpt_ids=ids,
                effective_date=date(2026, 9, 30) if quotes else None,
            )
            session.add(f)
            session.flush()
            return f.id

        ids = {
            "profit": finding(
                "financials.quarters",
                "Q2 FY27 profit Rs 3,926.15 crore",
                "fact",
                "supported",
                [(q2, "pat_cr: 3926.15")],
            ),
            "growth": finding(
                "financials.quarters",
                "Revenue up 10.0% on the year",
                "fact",
                "supported",
                [(figures_src, "revenue 10.0% on Q2 FY26")],
            ),
            "leader": finding(
                "industry.position",
                "Largest EPC company",
                "fact",
                "supported",
                [(page, "L&T is the largest EPC company")],
            ),
            "reading": finding(
                "industry.position", "Scale is an advantage", "interpretation", "inferred", []
            ),
        }
        plan = [
            Question("financials.quarters", "financials", "How have results moved?"),
            Question("industry.position", "industry", "Who are its competitors?"),
            Question("industry.market", "industry", "What drives demand?", mandatory=False),
        ]
        run.plan = {
            "questions": to_json(plan),
            "coverage": {
                "financials.quarters": "answered",
                "industry.position": "answered",
                "industry.market": "open",
            },
        }
        run.status = "succeeded"
        session.commit()
        yield session, run, ids
        session.rollback()


def para(kind, text):
    return report.Para(kind=kind, text=text)


def draft(ids, extra_valuation=None, against=None):
    F = {k: f"[F{v}]" for k, v in ids.items()}
    industry = [
        para("fact", f"By order book it is the largest EPC company in India {F['leader']}."),
        para(
            "interpretation", f"Its scale is an advantage in winning large orders {F['reading']}."
        ),
        para("gap", "What drives demand could not be found in the sources."),
    ]
    return report.Draft(
        conclusion=para("fact", f"Profit was Rs 3,926.15 crore in Q2 FY27 {F['profit']}."),
        stances=[
            report.Stance(label="for", para=para("fact", f"Revenue rose 10.0% {F['growth']}.")),
            report.Stance(
                label="against",
                para=against or para("interpretation", f"Scale cuts both ways {F['reading']}."),
            ),
            report.Stance(label="open", para=para("gap", "Market share was not found.")),
        ],
        would_change=para("gap", "No dated event was found that would test this."),
        sections=[
            report.SectionDraft(
                key="financials",
                paragraphs=[
                    para(
                        "fact",
                        f"Q2 FY27 profit was Rs 3,926.15 crore {F['profit']}, with revenue up "
                        f"10.0% on the year {F['growth']}.",
                    ),
                    *(extra_valuation or []),
                ],
            ),
            report.SectionDraft(key="industry", paragraphs=industry),
        ],
    )


class Writer:
    """Plays the writer and the reviewer from a script."""

    def __init__(self, drafts, reviews=()):
        self.drafts = list(drafts)
        self.reviews = list(reviews)
        self.prompts = []
        self.messages = NS(parse=self.parse)

    def parse(self, **kw):
        self.prompts.append(kw["messages"][0]["content"])
        usage = NS(
            input_tokens=20_000,
            output_tokens=4_000,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            server_tool_use=None,
        )
        if kw["output_format"] is report.Draft:
            out = self.drafts.pop(0)
            return NS(
                parsed_output=out,
                stop_reason="refusal" if out is None else "end_turn",
                usage=usage,
                model=SONNET,
            )
        issues = self.reviews.pop(0) if self.reviews else []
        return NS(
            parsed_output=report.ReviewOut(issues=issues),
            stop_reason="end_turn",
            usage=usage,
            model=SONNET,
        )


@pytest.mark.db
def test_a_clean_draft_is_validated(run_with_findings):
    session, run, ids = run_with_findings
    before = run.cost_usd
    writer = Writer([draft(ids)])
    made = report.write_report(
        session, run, "Larsen & Toubro Limited", client=writer, model=SONNET, today=TODAY
    )
    assert made.outcome == "validated"
    assert len(writer.prompts) == 2  # written once, reviewed once, nothing to repair
    first = writer.prompts[0]
    assert f"[F{ids['profit']}] fact, supported, period 2026-09-30" in first
    assert 'quote from "Results Q2 FY27 (consolidated)": "pat_cr: 3926.15"' in first
    assert "Question [industry.market] (optional, open)" in first

    md = made.markdown
    assert md.startswith("# Larsen & Toubro Limited: research report")
    assert (
        re.search(r"\*\*Validated\*\* · version \d+ · run", md) and "shared data contract 1.0" in md
    )
    assert "Not investment advice" in md
    # findings become numbered sources; each source lists the passage relied on
    assert "**Profit was Rs 3,926.15 crore in Q2 FY27 [1].**" in md
    assert "1. Results Q2 FY27 (consolidated), retrieved" in md
    assert "   > pat_cr: 3926.15" in md
    assert "EPC league table, Example News, retrieved" in md and "https://web.example/epc" in md
    assert "*Interpretation:* Its scale is an advantage in winning large orders." in md
    assert "*Not found:* What drives demand could not be found" in md
    assert "| What drives demand? (optional) | Open |" in md
    assert "[F" not in md
    assert run.cost_usd > before  # the write and the review are on the run
    assert made.sections["sources"]["1"] and made.coverage["industry.position"] == "answered"


@pytest.mark.db
def test_problems_are_repaired_once_and_what_still_fails_is_dropped(run_with_findings):
    session, run, ids = run_with_findings
    bad_figure = para("interpretation", f"Profit grew about 50% [F{ids['profit']}].")
    advice = para("interpretation", f"We recommend buying the stock [F{ids['reading']}].")
    first = draft(ids, extra_valuation=[advice], against=bad_figure)
    repaired = draft(ids, extra_valuation=[advice])  # fixes the figure, keeps the advice
    review = [report.ReviewIssue(where="industry.2", problem="overstated", blocking=True)]
    writer = Writer([first, repaired], [review])
    made = report.write_report(session, run, "L&T", client=writer, model=SONNET, today=TODAY)

    assert len(writer.prompts) == 3
    repair = writer.prompts[2]
    assert "summary.against: figures not in the passages it cites: 50" in repair
    assert "industry.2: overstated" in repair and "Your previous draft" in repair
    assert made.outcome == "validated_with_limitations"
    assert made.sections["dropped"] == ["financials.2: reads as investment advice: We recommend"]
    assert "We recommend" not in made.markdown.split("## Review")[0]
    assert "- Removed: financials.2" in made.markdown
    assert "Model review: industry.2: overstated" in made.markdown
    events = [
        e.message
        for e in session.scalars(
            select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.seq)
        )
    ]
    assert any("2 problems found by the checks, 1 blocking" in m for m in events)
    assert events[-1] == f"report version {made.version}: Validated, with limitations"


@pytest.mark.db
def test_no_draft_is_a_rejected_report_that_still_shows_coverage(run_with_findings):
    session, run, _ = run_with_findings
    made = report.write_report(
        session, run, "L&T", client=Writer([None]), model=SONNET, today=TODAY
    )
    assert made.outcome == "rejected"
    assert "**Rejected**" in made.markdown and "## Coverage" in made.markdown
    assert "no draft could be written" in made.markdown
    again = report.write_report(
        session, run, "L&T", client=Writer([None]), model=SONNET, today=TODAY
    )
    assert again.version == made.version + 1 and report.latest(session, run.isin).id == again.id


@pytest.mark.db
def test_budget_left_for_writing_is_respected(run_with_findings):
    session, run, ids = run_with_findings
    run.cost_usd = Decimal("4.90")  # research spent nearly all of it
    made = report.write_report(
        session, run, "L&T", client=Writer([draft(ids)]), model=SONNET, today=TODAY
    )
    assert made.outcome == "rejected" and run.cost_usd == Decimal("4.90")
    assert "Budget reached" in made.markdown


@pytest.mark.db
def test_export_command(run_with_findings, research_url, shared_url, monkeypatch):
    from stock_deep_research.cli import app
    from stock_deep_research.config import get_settings

    session, run, ids = run_with_findings
    report.write_report(session, run, "L&T", client=Writer([draft(ids)]), model=SONNET, today=TODAY)
    monkeypatch.setenv("DATABASE_URL", research_url)
    monkeypatch.setenv("SHARED_DATABASE_URL", shared_url)
    get_settings.cache_clear()
    try:
        out = CliRunner().invoke(app, ["export", "LT"])
        assert out.exit_code == 0 and "research report" in out.stdout
        assert re.search(r"version \d+", out.stdout)
        assert CliRunner().invoke(app, ["export", "LT", "--version", "99"]).exit_code == 1
    finally:
        get_settings.cache_clear()
    assert session.scalar(select(Report).where(Report.run_id == run.id)) is not None
