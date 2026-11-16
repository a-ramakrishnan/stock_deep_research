from datetime import date
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from stock_deep_research import eval as eval_mod
from stock_deep_research import excerpts, report, shared
from stock_deep_research.cli import app
from stock_deep_research.derived import derive
from stock_deep_research.evidence import Evidence, new_run
from stock_deep_research.models import Finding
from stock_deep_research.plan import Question, to_json

TODAY = date(2026, 11, 5)
SONNET = "claude-sonnet-5-5"


class ScriptWriter:
    def __init__(self, draft):
        self.draft = draft
        self.messages = NS(parse=self.parse)

    def parse(self, **kw):
        usage = NS(
            input_tokens=10_000,
            output_tokens=2_000,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            server_tool_use=None,
        )
        if kw["output_format"] is report.Draft:
            return NS(parsed_output=self.draft, stop_reason="end_turn", usage=usage, model=SONNET)
        return NS(
            parsed_output=report.ReviewOut(issues=[]),
            stop_reason="end_turn",
            usage=usage,
            model=SONNET,
        )


def test_adversarial_veracity_traps():
    traps = eval_mod.run_traps()
    assert len(traps) == 5
    assert all(t.passed for t in traps)
    out = CliRunner().invoke(app, ["eval", "--traps"])
    assert out.exit_code == 0 and "[PASS]" in out.stdout


def test_figure_lineage_matching():
    from decimal import Decimal

    ex = NS(id=1, quote="revenue_cr: 67079.45", start_char=0)
    src = NS(
        id=11, ref="shared.results:LT:2026-09-30:3m:consolidated", dataset="results", kind="shared"
    )
    facts = NS(quotes={5: [(ex, src)]})
    traced = eval_mod._match_figure(Decimal("67079"), 0, "67,079", [5], facts)
    assert traced.verified and traced.matched_value == Decimal("67079.45")
    assert traced.finding_id == 5 and traced.source_id == 11 and traced.dataset == "results"
    untraced = eval_mod._match_figure(Decimal("70000"), 0, "70,000", [5], facts)
    assert not untraced.verified and untraced.finding_id is None


@pytest.mark.db
def test_score_run_and_veracity_lineage(research_url, shared_url, monkeypatch):
    from stock_deep_research.config import get_settings

    shared_eng = create_engine(shared_url)
    company = shared.find_company(shared_eng, "LT")
    with Session(create_engine(research_url)) as session:
        run = new_run(session, company, "eval test", SONNET, 5.0, "1.0")
        ev = Evidence(session, run, shared_eng, company)
        ev.collect()
        figures_src = derive(ev)
        q2 = ev.get("shared.results:LT:2026-09-30:3m:consolidated")
        flag = ev.get("shared.red_flags:2")

        def add_finding(question, claim, support, src, quote, eff=date(2026, 9, 30)):
            ex = excerpts.excerpt(session, src, quote)
            f = Finding(
                run_id=run.id,
                section=question.split(".")[0],
                question=question,
                claim=claim,
                claim_type="fact",
                support=support,
                excerpt_ids=[ex.id],
                effective_date=eff,
            )
            session.add(f)
            session.flush()
            return f.id

        f_profit = add_finding(
            "financials.quarters",
            "Q2 FY27 consolidated profit was Rs 3,926.15 crore.",
            "supported",
            q2,
            "pat_cr: 3926.15",
        )
        f_growth = add_finding(
            "financials.quarters",
            "Revenue grew 10.0% on the year.",
            "supported",
            figures_src,
            "revenue 10.0% on Q2 FY26",
        )
        f_flag = add_finding(
            "management.governance",
            "Auditor raised an emphasis of matter.",
            "supported",
            flag,
            "title: Auditor qualification",
        )

        plan = [
            Question("financials.quarters", "financials", "How have results moved?"),
            Question("management.governance", "management", "Governance concerns?"),
        ]
        run.plan = {
            "questions": to_json(plan),
            "coverage": {
                "financials.quarters": "answered",
                "management.governance": "answered",
            },
        }
        run.status = "succeeded"
        session.commit()

        d = report.Draft(
            conclusion=report.Para(
                kind="fact", text=f"Profit was Rs 3,926.15 crore in Q2 FY27 [F{f_profit}]."
            ),
            stances=[
                report.Stance(
                    label="for",
                    para=report.Para(kind="fact", text=f"Revenue rose 10.0% [F{f_growth}]."),
                ),
                report.Stance(
                    label="against",
                    para=report.Para(
                        kind="fact", text=f"An auditor qualification is on record [F{f_flag}]."
                    ),
                ),
                report.Stance(
                    label="open",
                    para=report.Para(kind="gap", text="Longer guidance was not found."),
                ),
            ],
            would_change=report.Para(kind="gap", text="No dated catalyst found."),
            sections=[
                report.SectionDraft(
                    key="financials",
                    paragraphs=[
                        report.Para(
                            kind="fact",
                            text=f"Profit reached Rs 3,926.15 crore [F{f_profit}], with revenue "
                            f"up 10.0% on the year [F{f_growth}].",
                        )
                    ],
                ),
                report.SectionDraft(
                    key="management",
                    paragraphs=[
                        report.Para(
                            kind="fact",
                            text=f"The auditor noted an emphasis of matter [F{f_flag}].",
                        )
                    ],
                ),
            ],
        )
        made = report.write_report(
            session, run, company.name, client=ScriptWriter(d), model=SONNET, today=TODAY
        )
        veracity = eval_mod.audit_report(session, made, today=TODAY)
        assert veracity.warnings == []
        assert veracity.figures_total == 4 and veracity.figures_verified == 4
        assert veracity.figures_from_shared == 2 and veracity.figures_from_derived == 2
        assert veracity.excerpts_intact == veracity.excerpts_checked == 3

        card = eval_mod.score_run(session, run.id)
        assert card.mandatory_coverage_pct == 100.0
        assert card.quote_fidelity_pct == 100.0
        assert card.figure_precision_pct == 100.0
        assert card.veracity_warnings == []

        monkeypatch.setenv("DATABASE_URL", research_url)
        monkeypatch.setenv("SHARED_DATABASE_URL", shared_url)
        get_settings.cache_clear()
        try:
            cli_out = CliRunner().invoke(app, ["eval", str(run.id)])
            assert cli_out.exit_code == 0 and "figure precision     4/4 (100%" in cli_out.stdout
        finally:
            get_settings.cache_clear()
        session.rollback()


@pytest.mark.db
def test_veracity_flags_standalone_stale_quarter_and_omitted_red_flags(research_url, shared_url):
    shared_eng = create_engine(shared_url)
    company = shared.find_company(shared_eng, "LT")
    with Session(create_engine(research_url)) as session:
        run = new_run(session, company, "warnings test", SONNET, 5.0, "1.0")
        ev = Evidence(session, run, shared_eng, company)
        ev.collect()
        q1 = ev.get("shared.results:LT:2026-06-30:3m:consolidated")
        standalone = ev.get("shared.results:LT:2025-09-30:3m:standalone")

        ex_q1 = excerpts.excerpt(session, q1, "revenue_cr: 63679")
        f_q1 = Finding(
            run_id=run.id,
            section="financials",
            question="financials.quarters",
            claim="Q1 revenue was Rs 63,679 crore.",
            claim_type="fact",
            support="supported",
            excerpt_ids=[ex_q1.id],
            effective_date=date(2026, 6, 30),
        )
        ex_sa = excerpts.excerpt(session, standalone, "pat_cr: 2000")
        f_sa = Finding(
            run_id=run.id,
            section="financials",
            question="financials.quarters",
            claim="Profit was Rs 2,000 crore.",
            claim_type="fact",
            support="supported",
            excerpt_ids=[ex_sa.id],
            effective_date=date(2025, 9, 30),
        )
        f_conflict = Finding(
            run_id=run.id,
            section="financials",
            question="financials.quarters",
            claim="Sources disagree on segment margins.",
            claim_type="fact",
            support="conflicting",
            excerpt_ids=[ex_q1.id],
            effective_date=date(2026, 6, 30),
        )
        session.add_all([f_q1, f_sa, f_conflict])
        session.flush()

        plan = [Question("financials.quarters", "financials", "How have results moved?")]
        run.plan = {"questions": to_json(plan), "coverage": {"financials.quarters": "answered"}}
        run.status = "succeeded"
        session.commit()

        # Draft cites standalone profit without saying 'standalone', cites only Q1 FY27 (missing Q2
        # FY27), and omits both the red_flags source and the conflicting finding.
        d = report.Draft(
            conclusion=report.Para(kind="fact", text=f"Revenue was Rs 63,679 crore [F{f_q1.id}]."),
            stances=[
                report.Stance(
                    label="for",
                    para=report.Para(kind="fact", text=f"Revenue Rs 63,679 crore [F{f_q1.id}]."),
                ),
                report.Stance(
                    label="against",
                    para=report.Para(kind="fact", text=f"Profit was Rs 2,000 crore [F{f_sa.id}]."),
                ),
                report.Stance(label="open", para=report.Para(kind="gap", text="Nothing else.")),
            ],
            would_change=report.Para(kind="gap", text="None."),
            sections=[
                report.SectionDraft(
                    key="financials",
                    paragraphs=[
                        report.Para(
                            kind="fact",
                            text=f"In Q1 FY27 revenue was Rs 63,679 crore [F{f_q1.id}].",
                        )
                    ],
                )
            ],
        )
        made = report.write_report(
            session, run, company.name, client=ScriptWriter(d), model=SONNET, today=TODAY
        )
        veracity = eval_mod.audit_report(session, made, today=TODAY)
        joined = "\n".join(veracity.warnings)
        assert "cites non-primary standalone result" in joined
        assert "newest primary quarterly result (Q2 FY27" in joined
        assert "red_flags source(s) collected in run but none cited" in joined
        assert "conflicting finding(s) in run not surfaced" in joined
        session.rollback()
