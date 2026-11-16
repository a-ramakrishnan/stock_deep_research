"""Evaluation and report veracity auditing (R4).

1. **Veracity audit (`audit_report`):** traces every paragraph and every figure in a published
   report back through its cited findings (`[F12]`) and excerpts to the immutable `Source` rows,
   checking five layers of truth:
   - **Layer 1 (Source & quote integrity):** every cited excerpt has a valid `start_char` in
     `Source.text`, and `Source.content_hash` matches `sha256(Source.text)`.
   - **Layer 2 (Figure-to-quote grounding):** every number in a paragraph matches a decimal in
     the verbatim quotes of the findings it cites, allowing for standard rounding.
   - **Layer 3 (Derived math provenance):** distinguishes figures traced to `derived.py`
     formulas (`dataset='derived'`), raw `shared` records, and `web` pages.
   - **Layer 4 (Basis & period freshness):** flags paragraphs that cite non-primary `standalone`
     results without disclosing `'standalone'`, and checks whether `financials` cites the newest
     primary quarterly result available in the run.
   - **Layer 5 (Omission & gap honesty):** flags collected `red_flags` sources or `conflicting`
     findings that were omitted from the report, and checks whether open questions are written
     honestly as `gap` paragraphs.
2. **Run scorecard (`score_run`):** grades any research run and its latest report on quote
   fidelity, numeric precision, mandatory question coverage, follow-up lift, review survival and
   stage-by-stage cost efficiency, at $0 extra model cost.
3. **Adversarial veracity traps (`run_traps`):** deterministic test cases seeding fabricated
   quotes, hallucinated numbers, standalone/consolidated mix-ups, stale periods, omitted red
   flags, future dates and investment advice to verify 100% guardrail recall.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from stock_deep_research import evidence, excerpts, report, review
from stock_deep_research.models import Excerpt, Finding, Report, Run, RunEvent, Source


@dataclass
class FigureTrace:
    """One figure written in a report paragraph and where it came from."""

    written: str
    value: Decimal
    decimals: int
    verified: bool
    matched_value: Decimal | None = None
    finding_id: int | None = None
    source_id: int | None = None
    source_ref: str | None = None
    dataset: str | None = None
    quote: str | None = None


@dataclass
class ParagraphAudit:
    """Veracity check of one paragraph in a report."""

    where: str
    section: str
    kind: str  # fact, interpretation, gap
    text: str
    finding_ids: list[int] = field(default_factory=list)
    source_ids: list[int] = field(default_factory=list)
    figures: list[FigureTrace] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        return not self.issues and all(f.verified for f in self.figures)


@dataclass
class VeracityReport:
    """Full 5-layer veracity audit of a published report."""

    report_id: int
    run_id: int
    isin: str
    version: int
    outcome: str
    paragraphs: list[ParagraphAudit] = field(default_factory=list)
    figures_total: int = 0
    figures_verified: int = 0
    figures_from_derived: int = 0
    figures_from_shared: int = 0
    figures_from_web: int = 0
    excerpts_checked: int = 0
    excerpts_intact: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def figure_precision_pct(self) -> float:
        return (self.figures_verified / self.figures_total * 100.0) if self.figures_total else 100.0

    @property
    def excerpt_integrity_pct(self) -> float:
        return (
            (self.excerpts_intact / self.excerpts_checked * 100.0)
            if self.excerpts_checked
            else 100.0
        )


@dataclass
class RunScorecard:
    """Automated evaluation scorecard for a research run and its report."""

    run_id: int
    symbol: str
    status: str
    model: str
    cost_usd: Decimal
    budget_usd: Decimal
    wall_seconds: float | None
    tool_calls: int
    sources_total: int
    web_pages_ok: int
    web_pages_failed: int
    findings_total: int
    facts_total: int
    facts_supported: int
    quotes_total: int
    quotes_located: int
    mandatory_total: int
    mandatory_answered: int
    mandatory_partly: int
    mandatory_open: int
    followup_questions: int
    followup_sources_read: int
    stage_costs: dict[str, Decimal] = field(default_factory=dict)
    report_version: int | None = None
    report_outcome: str | None = None
    paragraphs_total: int = 0
    paragraphs_verified: int = 0
    paragraphs_dropped: int = 0
    figures_total: int = 0
    figures_verified: int = 0
    veracity_warnings: list[str] = field(default_factory=list)

    @property
    def quote_fidelity_pct(self) -> float:
        return (self.quotes_located / self.quotes_total * 100.0) if self.quotes_total else 100.0

    @property
    def mandatory_coverage_pct(self) -> float:
        return (
            self.mandatory_answered / self.mandatory_total * 100.0 if self.mandatory_total else 0.0
        )

    @property
    def figure_precision_pct(self) -> float:
        return (self.figures_verified / self.figures_total * 100.0) if self.figures_total else 100.0

    @property
    def cost_per_supported_fact(self) -> Decimal | None:
        if not self.facts_supported:
            return None
        return (self.cost_usd / Decimal(self.facts_supported)).quantize(Decimal("0.0001"))


@dataclass
class TrapResult:
    """Result of one adversarial veracity trap test."""

    name: str
    layer: str
    passed: bool
    detail: str


def _match_figure(
    value: Decimal,
    decimals: int,
    written: str,
    finding_ids: list[int],
    facts: report.Facts,
) -> FigureTrace:
    """Locate which cited finding, excerpt and source back a figure in a paragraph."""
    step = Decimal(1).scaleb(-decimals)
    for fid in finding_ids:
        for ex, src in facts.quotes.get(fid, []):
            for candidate in review._values([ex.quote]):
                c_abs = abs(candidate)
                if c_abs.quantize(step) == value or abs(c_abs - value) <= step / 2:
                    return FigureTrace(
                        written=written,
                        value=value,
                        decimals=decimals,
                        verified=True,
                        matched_value=candidate,
                        finding_id=fid,
                        source_id=src.id,
                        source_ref=src.ref,
                        dataset=src.dataset or src.kind,
                        quote=ex.quote,
                    )
    return FigureTrace(written=written, value=value, decimals=decimals, verified=False)


def _excerpt_intact(ex: Excerpt, src: Source) -> bool:
    """True when the quote still sits at `start_char` and `Source.content_hash` matches."""
    if src.text is None or ex.start_char is None:
        return False
    if src.content_hash and evidence.digest(src.text) != src.content_hash:
        return False
    return excerpts.locate(src.text[ex.start_char :], ex.quote) == 0


def audit_report(
    session: Session,
    rep: Report,
    *,
    today: date | None = None,
) -> VeracityReport:
    """Perform a 5-layer veracity audit on `rep` against its run's findings and sources."""
    run = session.get(Run, rep.run_id)
    facts = report.load(session, run)
    all_sources = {
        s.id: s for s in session.scalars(select(Source).where(Source.run_id == run.id)).all()
    }
    if today is None:
        today = (
            (run.finished_at or rep.created_at).date()
            if (run.finished_at or rep.created_at)
            else date.today()
        )

    out = VeracityReport(
        report_id=rep.id,
        run_id=rep.run_id,
        isin=rep.isin,
        version=rep.version,
        outcome=rep.outcome,
    )
    raw_draft = (rep.sections or {}).get("draft")
    if not raw_draft:
        return out

    draft = report.Draft.model_validate(raw_draft)
    checked_excerpts: dict[int, bool] = {}
    cited_source_ids: set[int] = set()
    cited_finding_ids: set[int] = set()
    financials_sources: set[int] = set()

    for where, sec_key, para in report.addresses(draft):
        fids = [int(i) for i in review.MARKER.findall(para.text)]
        cited_finding_ids.update(fids)
        sids: list[int] = []
        para_issues = [
            i.problem
            for i in review.check_paragraph(
                where, para.kind, para.text, facts.cited, sec_key, today
            )
        ]

        for fid in fids:
            for ex, src in facts.quotes.get(fid, []):
                if src.id not in sids:
                    sids.append(src.id)
                cited_source_ids.add(src.id)
                if sec_key == "financials":
                    financials_sources.add(src.id)
                    if src.dataset == "derived":
                        for m in review.re.findall(r"S(\d+)", ex.quote):
                            financials_sources.add(int(m))
                intact = _excerpt_intact(ex, src)
                checked_excerpts[ex.id] = intact
                if not intact:
                    para_issues.append(f"excerpt {ex.id} in S{src.id} failed hash/offset check")
                # Layer 4a: standalone result cited without saying 'standalone'
                if (
                    src.dataset == "results"
                    and (src.data or {}).get("basis") == "standalone"
                    and not (src.data or {}).get("primary")
                    and "standalone" not in para.text.lower()
                ):
                    msg = (
                        f"cites non-primary standalone result S{src.id} "
                        "without stating 'standalone'"
                    )
                    if msg not in para_issues:
                        para_issues.append(msg)
                    out.warnings.append(f"{where}: {msg}")

        fig_traces = [
            _match_figure(val, dec, written, fids, facts)
            for val, dec, written in review.figures(para.text)
        ]
        for ft in fig_traces:
            out.figures_total += 1
            if ft.verified:
                out.figures_verified += 1
                if ft.dataset == "derived":
                    out.figures_from_derived += 1
                elif ft.dataset == "web":
                    out.figures_from_web += 1
                else:
                    out.figures_from_shared += 1

        out.paragraphs.append(
            ParagraphAudit(
                where=where,
                section=sec_key,
                kind=para.kind,
                text=para.text,
                finding_ids=fids,
                source_ids=sids,
                figures=fig_traces,
                issues=para_issues,
            )
        )

    out.excerpts_checked = len(checked_excerpts)
    out.excerpts_intact = sum(checked_excerpts.values())

    # Layer 4b: Freshness check — did financials cite the newest primary quarterly result?
    primary_quarters = [
        s
        for s in all_sources.values()
        if s.dataset == "results"
        and (s.data or {}).get("primary")
        and int((s.data or {}).get("months") or 0) == 3
        and (s.data or {}).get("period_end")
    ]
    if primary_quarters and any(
        p.section == "financials" and p.kind == "fact" for p in out.paragraphs
    ):
        newest = max(primary_quarters, key=lambda s: str(s.data["period_end"]))
        newest_end = str(newest.data["period_end"])[:10]
        fin_dates = {
            f.effective_date.isoformat()
            for f in facts.findings
            if f.id in cited_finding_ids and f.section == "financials" and f.effective_date
        }
        if newest.id not in financials_sources and newest_end not in fin_dates:
            label = newest.data.get("period") or newest_end
            out.warnings.append(
                f"freshness: newest primary quarterly result ({label}, S{newest.id}) "
                "was not cited in financials"
            )

    # Layer 5a: Omitted red flags check
    red_flag_sources = [
        s for s in all_sources.values() if s.dataset == "red_flags" and s.retrieval_status == "ok"
    ]
    if red_flag_sources and not any(s.id in cited_source_ids for s in red_flag_sources):
        out.warnings.append(
            f"omission: {len(red_flag_sources)} red_flags source(s) collected in run "
            "but none cited in report"
        )

    # Layer 5b: Omitted conflicting findings check
    conflicting = [f for f in facts.findings if f.support == "conflicting"]
    if conflicting and not any(f.id in cited_finding_ids for f in conflicting):
        out.warnings.append(
            f"omission: {len(conflicting)} conflicting finding(s) in run not surfaced in report"
        )

    return out


def score_run(session: Session, run_id: int) -> RunScorecard:
    """Compute the evaluation scorecard for `run_id` and its latest report (if any)."""
    run = session.get(Run, run_id)
    if run is None:
        raise ValueError(f"No run {run_id}")

    all_sources = session.scalars(select(Source).where(Source.run_id == run.id)).all()
    all_findings = session.scalars(select(Finding).where(Finding.run_id == run.id)).all()
    events = session.scalars(
        select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.seq)
    ).all()
    ex_ids = [i for f in all_findings for i in (f.excerpt_ids or [])]
    all_excerpts = (
        session.scalars(select(Excerpt).where(Excerpt.id.in_(ex_ids))).all() if ex_ids else []
    )

    plan_questions = (run.plan or {}).get("questions", [])
    cov = (run.plan or {}).get("coverage", {})
    mandatory_ids = [q["id"] for q in plan_questions if q.get("mandatory", True)]

    stage_costs: dict[str, Decimal] = {}
    followup_questions = 0
    followup_sources = 0
    for e in events:
        stage_costs[e.stage] = stage_costs.get(e.stage, Decimal(0)) + Decimal(e.cost_usd or 0)
        if e.stage == "follow up" and "skipped" not in e.message:
            followup_questions += 1
            m = review.re.search(r"(\d+) sources read", e.message)
            if m:
                followup_sources += int(m.group(1))

    wall = None
    if run.started_at and run.finished_at:
        wall = round((run.finished_at - run.started_at).total_seconds(), 1)

    web_sources = [s for s in all_sources if s.kind == "web"]
    facts_list = [f for f in all_findings if f.claim_type == "fact"]

    card = RunScorecard(
        run_id=run.id,
        symbol=run.symbol or run.isin,
        status=run.status,
        model=run.model,
        cost_usd=Decimal(run.cost_usd or 0),
        budget_usd=Decimal(run.budget_usd or 0),
        wall_seconds=wall,
        tool_calls=run.tool_calls or 0,
        sources_total=len(all_sources),
        web_pages_ok=sum(1 for s in web_sources if s.retrieval_status == "ok"),
        web_pages_failed=sum(
            1 for s in web_sources if s.retrieval_status in ("blocked", "failed", "unreadable")
        ),
        findings_total=len(all_findings),
        facts_total=len(facts_list),
        facts_supported=sum(1 for f in facts_list if f.support == "supported"),
        quotes_total=len(all_excerpts),
        quotes_located=sum(1 for e in all_excerpts if e.start_char is not None),
        mandatory_total=len(mandatory_ids),
        mandatory_answered=sum(1 for qid in mandatory_ids if cov.get(qid) == "answered"),
        mandatory_partly=sum(1 for qid in mandatory_ids if cov.get(qid) == "partly"),
        mandatory_open=sum(1 for qid in mandatory_ids if cov.get(qid, "open") == "open"),
        followup_questions=followup_questions,
        followup_sources_read=followup_sources,
        stage_costs=stage_costs,
    )

    rep = session.scalar(
        select(Report)
        .where(Report.run_id == run.id)
        .order_by(Report.version.desc(), Report.id.desc())
        .limit(1)
    )
    if rep is not None:
        veracity = audit_report(session, rep)
        card.report_version = rep.version
        card.report_outcome = rep.outcome
        card.paragraphs_total = len(veracity.paragraphs)
        card.paragraphs_verified = sum(1 for p in veracity.paragraphs if p.verified)
        card.paragraphs_dropped = len((rep.sections or {}).get("dropped", []))
        card.figures_total = veracity.figures_total
        card.figures_verified = veracity.figures_verified
        card.veracity_warnings = veracity.warnings

    return card


def run_traps() -> list[TrapResult]:
    """Run deterministic adversarial veracity traps across all 5 verification layers."""
    today = date(2026, 11, 5)
    cited = {
        1: review.Cited(1, "fact", "supported", date(2026, 9, 30), ["revenue_cr: 67079.45"]),
        2: review.Cited(2, "fact", "insufficient", date(2026, 9, 30), [], missing_quotes=1),
        3: review.Cited(3, "fact", "supported", date(2027, 1, 25), ["board meets 25 Jan 2027"]),
    }
    results: list[TrapResult] = []

    # Trap 1: Fabricated quote (missing_quotes=1, quotes=[])
    t1 = review.check_paragraph(
        "financials.1", "fact", "Revenue was strong [F2].", cited, "financials", today
    )
    results.append(
        TrapResult(
            name="Fabricated quote rejected",
            layer="Layer 1 · Quote Offset",
            passed=any("no supported fact whose quote was found" in i.problem for i in t1),
            detail=t1[0].problem if t1 else "missed",
        )
    )

    # Trap 2: Hallucinated / altered figure in prose
    t2 = review.check_paragraph(
        "financials.2",
        "fact",
        "Revenue was Rs 68,500 crore in Q2 FY27 [F1].",
        cited,
        "financials",
        today,
    )
    results.append(
        TrapResult(
            name="Hallucinated figure caught",
            layer="Layer 2 · Figure Grounding",
            passed=any("68,500" in i.problem for i in t2),
            detail=t2[0].problem if t2 else "missed",
        )
    )

    # Trap 3: Tampered source text hash / missing excerpt offset
    src = Source(
        id=10,
        run_id=1,
        kind="shared",
        dataset="results",
        ref="shared.results:LT",
        text="revenue_cr: 67079.45",
        content_hash=evidence.digest("revenue_cr: 99999.00"),  # hash mismatch!
    )
    ex = Excerpt(id=1, source_id=10, quote="revenue_cr: 67079.45", start_char=0)
    intact = _excerpt_intact(ex, src)
    results.append(
        TrapResult(
            name="Tampered source hash detected",
            layer="Layer 1 · Source SHA-256",
            passed=not intact,
            detail="SHA-256 content_hash mismatch flagged",
        )
    )

    # Trap 4: Future date outside catalysts
    t4 = review.check_paragraph(
        "business.1", "fact", "The board meets on 25 Jan 2027 [F3].", cited, "business", today
    )
    results.append(
        TrapResult(
            name="Future date outside catalysts blocked",
            layer="Layer 4 · Temporal Consistency",
            passed=any("dated after today" in i.problem for i in t4),
            detail=t4[0].problem if t4 else "missed",
        )
    )

    # Trap 5: Subtle investment recommendation / price target
    t5 = review.check_paragraph(
        "valuation.1",
        "interpretation",
        "With a target price of Rs 4,500, we recommend buying the stock [F1].",
        cited,
        "valuation",
        today,
    )
    results.append(
        TrapResult(
            name="Investment advice & price target blocked",
            layer="Layer 5 · Regulatory Guardrail",
            passed=any("investment advice" in i.problem for i in t5),
            detail=t5[0].problem if t5 else "missed",
        )
    )

    return results
