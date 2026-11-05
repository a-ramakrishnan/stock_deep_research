"""The report (R3): written from a run's findings, reviewed, published as a version.

1. **Write:** one structured call turns the findings into the report's sections (design,
   section 5): a one-line conclusion, three stances (the case for, the case against, what is
   unclear), what would change the view, then each section as paragraphs. Every paragraph is a
   fact, an interpretation or a gap, and cites the findings it rests on as `[F12]`. Only
   findings can be used; open questions are written as gaps.
2. **Review:** the deterministic checks (review.py) on every paragraph, then one model review of
   support and coverage. If either found a problem, one repair pass rewrites the draft with
   the problems listed, and the checks run again. A paragraph that still fails is dropped and
   named in the report's limitations.
3. **Publish:** validated (nothing dropped, every mandatory question answered), validated with
   limitations (gaps shown), or rejected (no conclusion survives, nothing to say, or fewer than
   half the mandatory questions answered). Rendered as Markdown with numbered sources (title,
   publisher, published and retrieved dates, the passages relied on), the coverage of every
   question, the run, its cost and the shared contract version (invariant 4).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

import anthropic
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from stock_deep_research import costs
from stock_deep_research.budget import Budget, BudgetStop
from stock_deep_research.models import Excerpt, Finding, Report, Run, RunEvent, Source
from stock_deep_research.plan import SECTION, Question, from_json
from stock_deep_research.review import MARKER, Cited, Issue, check_paragraph

DISCLAIMER = (
    "Research input built from public filings, market data and web pages, for information only. "
    "Not investment advice and not a recommendation to buy, sell or hold any security. Check "
    "anything that matters to you against the sources."
)
WRITE_TOKENS = 20_000
REVIEW_TOKENS = 8_000
STANCES = {"for": "The case for", "against": "The case against", "open": "What is unclear"}
OUTCOMES = {
    "validated": "Validated",
    "validated_with_limitations": "Validated, with limitations",
    "rejected": "Rejected",
}


class Para(BaseModel):
    kind: Literal["fact", "interpretation", "gap"]
    text: str  # cites findings as [F12]


class Stance(BaseModel):
    label: Literal["for", "against", "open"]
    para: Para


class SectionDraft(BaseModel):
    key: str
    paragraphs: list[Para]


class Draft(BaseModel):
    conclusion: Para
    stances: list[Stance]
    would_change: Para
    sections: list[SectionDraft]


class ReviewIssue(BaseModel):
    where: str  # the paragraph's address, as given
    problem: str
    blocking: bool  # a claim its findings don't support, or a mandatory question ignored


class ReviewOut(BaseModel):
    issues: list[ReviewIssue]


WRITE_SYSTEM = """You write a research report on an Indian listed company from findings that \
have already been checked against their sources. You may use only these findings.

- Every paragraph is one of: fact (states what findings say), interpretation (your reading of \
the findings, written as such: "this suggests", "the risk is"), gap (what could not be found).
- Cite every finding you use as [F12] right after the words it supports. A fact paragraph must \
cite at least one supported fact finding.
- Numbers: copy them as the findings' quoted passages give them, with their period and unit \
(Rs crore, %, Q2 FY27). Never compute, convert or round to a different unit; growth, margins \
and P/E are in the computed figures when they exist.
- For an open or partly answered question, write a gap paragraph saying what is missing. Never \
fill it with what seems plausible.
- The conclusion is one sentence. The three stances are the case for, the case against, and \
what is unclear. Then what would change the view, with dates where findings give them.
- Write plainly for an investor: short sentences, no hype, no jargon without a reason.
- No buy, sell or hold call, no price target, no recommendation of any kind."""

REVIEW_SYSTEM = """You review a draft research report against the findings it cites. For each \
paragraph (by its address), report a problem only if: a claim goes beyond what its cited \
findings and their quotes say; an interpretation is presented as fact; a figure is attached to \
the wrong period or unit; or a gap is papered over. Also report a mandatory question the draft \
ignores (address it to its section). Mark a problem blocking if a reader would be misled. \
Report nothing for style. No issues is a valid answer."""


@dataclass
class Facts:
    """A run's findings as the writer and the checks see them."""

    run: Run
    plan: list[Question]
    coverage: dict[str, str]
    findings: list[Finding]
    cited: dict[int, Cited]
    quotes: dict[int, list[tuple[Excerpt, Source]]] = field(default_factory=dict)


def load(session: Session, run: Run) -> Facts:
    plan = from_json((run.plan or {}).get("questions", []))
    found = list(
        session.scalars(select(Finding).where(Finding.run_id == run.id).order_by(Finding.id))
    )
    ex_ids = [i for f in found for i in (f.excerpt_ids or [])]
    excerpts = {
        e.id: e for e in session.scalars(select(Excerpt).where(Excerpt.id.in_(ex_ids or [0])))
    }
    sources = {
        s.id: s
        for s in session.scalars(
            select(Source).where(Source.id.in_({e.source_id for e in excerpts.values()} or {0}))
        )
    }
    cited, quotes = {}, {}
    for f in found:
        mine = [excerpts[i] for i in (f.excerpt_ids or []) if i in excerpts]
        located = [e for e in mine if e.start_char is not None]
        cited[f.id] = Cited(
            f.id,
            f.claim_type,
            f.support,
            f.effective_date,
            [e.quote for e in located],
            len(mine) - len(located),
        )
        quotes[f.id] = [(e, sources[e.source_id]) for e in located if e.source_id in sources]
    return Facts(run, plan, (run.plan or {}).get("coverage", {}), found, cited, quotes)


def _finding_lines(facts: Facts) -> str:
    out = []
    for key in dict.fromkeys(q.section for q in facts.plan):
        out.append(f"\n## {SECTION[key].title} [{key}]")
        for q in (q for q in facts.plan if q.section == key):
            state = facts.coverage.get(q.id, "open")
            flag = "mandatory" if q.mandatory else "optional"
            out.append(f"\nQuestion [{q.id}] ({flag}, {state}): {q.text}")
            for f in (f for f in facts.findings if f.question == q.id):
                c = facts.cited[f.id]
                when = f", period {f.effective_date.isoformat()}" if f.effective_date else ""
                out.append(f"[F{f.id}] {f.claim_type}, {c.support}{when}: {f.claim}")
                for e, s in facts.quotes.get(f.id, []):
                    out.append(f'    quote from "{s.title or s.ref}": "{e.quote}"')
                if f.limitations:
                    out.append(f"    limitations: {f.limitations}")
    return "\n".join(out)


def addresses(draft: Draft) -> list[tuple[str, str, Para]]:
    """Every paragraph with its address and section key."""
    out = [("summary.conclusion", "summary", draft.conclusion)]
    out += [(f"summary.{s.label}", "summary", s.para) for s in draft.stances]
    out.append(("summary.would_change", "summary", draft.would_change))
    for sec in draft.sections:
        out += [(f"{sec.key}.{i}", sec.key, p) for i, p in enumerate(sec.paragraphs, 1)]
    return out


def check(draft: Draft, facts: Facts, today: date) -> list[Issue]:
    issues: list[Issue] = []
    labels = {s.label for s in draft.stances}
    if labels != set(STANCES) or len(draft.stances) != 3:
        issues.append(Issue("summary", "needs exactly three stances: for, against, open"))
    keys = [s.key for s in draft.sections]
    for key in dict.fromkeys(q.section for q in facts.plan):
        if key not in keys:
            issues.append(Issue(key, "section missing"))
    for key in keys:
        if key not in SECTION:
            issues.append(Issue(key, "not a section of this report"))
    for where, section, para in addresses(draft):
        issues += check_paragraph(where, para.kind, para.text, facts.cited, section, today)
    return issues


def _company_line(run: Run) -> str:
    return f"{run.symbol or run.isin} (ISIN {run.isin})"


def write(
    client: anthropic.Anthropic,
    model: str,
    budget: Budget,
    facts: Facts,
    today: date,
    previous: Draft | None = None,
    problems: list[str] | None = None,
) -> tuple[Draft | None, Decimal, str]:
    prompt = (
        f"Company: {_company_line(facts.run)}\nToday: {today.isoformat()}\n"
        f"Sections, in order: {', '.join(k for k in dict.fromkeys(q.section for q in facts.plan))}"
        f"\n\nFindings by section and question:\n{_finding_lines(facts)}"
    )
    if previous is not None:
        prompt += (
            "\n\nYour previous draft:\n"
            + previous.model_dump_json(indent=1)
            + "\n\nProblems found in it (fix each; drop a claim you can't support):\n- "
            + "\n- ".join(problems or [])
        )
    budget.reserve(model, len(WRITE_SYSTEM) + len(prompt), WRITE_TOKENS)
    response = client.messages.parse(
        model=model,
        max_tokens=WRITE_TOKENS,
        system=WRITE_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_format=Draft,
        output_config={"effort": "medium"},
    )
    cost = costs.response_cost(model, response)
    draft = response.parsed_output if response.stop_reason != "refusal" else None
    return draft, cost, response.stop_reason or ""


def model_review(
    client: anthropic.Anthropic, model: str, budget: Budget, facts: Facts, draft: Draft
) -> tuple[list[ReviewIssue], Decimal]:
    blocks = []
    for where, _, para in addresses(draft):
        blocks.append(f"[{where}] ({para.kind}) {para.text}")
    open_questions = [
        f"[{q.id}] {q.text} ({facts.coverage.get(q.id, 'open')})" for q in facts.plan if q.mandatory
    ]
    prompt = (
        f"Company: {_company_line(facts.run)}\n\nMandatory questions:\n"
        + "\n".join(open_questions)
        + f"\n\nFindings:\n{_finding_lines(facts)}\n\nDraft paragraphs:\n"
        + "\n\n".join(blocks)
    )
    budget.reserve(model, len(REVIEW_SYSTEM) + len(prompt), REVIEW_TOKENS)
    response = client.messages.parse(
        model=model,
        max_tokens=REVIEW_TOKENS,
        system=REVIEW_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_format=ReviewOut,
        output_config={"effort": "medium"},
    )
    cost = costs.response_cost(model, response)
    out = response.parsed_output if response.stop_reason != "refusal" else None
    return (out.issues if out else []), cost


def drop_failing(draft: Draft, issues: list[Issue]) -> tuple[Draft, list[str]]:
    """Remove paragraphs the checks still fail; the conclusion and stances become gaps."""
    bad = {i.where for i in issues}
    why = {}
    for i in issues:
        why.setdefault(i.where, []).append(i.problem)
    dropped = [f"{w}: {'; '.join(p)}" for w, p in why.items()]
    gap = Para(kind="gap", text="(Removed in review: its claims could not be verified.)")
    conclusion = gap if "summary.conclusion" in bad else draft.conclusion
    stances = [
        Stance(label=s.label, para=gap if f"summary.{s.label}" in bad else s.para)
        for s in draft.stances
    ]
    would = gap if "summary.would_change" in bad else draft.would_change
    sections = [
        SectionDraft(
            key=sec.key,
            paragraphs=[p for i, p in enumerate(sec.paragraphs, 1) if f"{sec.key}.{i}" not in bad],
        )
        for sec in draft.sections
    ]
    return Draft(conclusion=conclusion, stances=stances, would_change=would, sections=sections), (
        dropped
    )


def outcome(draft: Draft | None, facts: Facts, dropped: list[str]) -> tuple[str, list[str]]:
    mandatory = [q for q in facts.plan if q.mandatory]
    answered = [q for q in mandatory if facts.coverage.get(q.id) == "answered"]
    reasons = []
    if draft is None:
        return "rejected", ["no draft could be written"]
    if draft.conclusion.kind == "gap":
        reasons.append("the conclusion could not be verified")
    if not any(p.kind != "gap" for s in draft.sections for p in s.paragraphs):
        reasons.append("no section has a verified paragraph")
    if mandatory and len(answered) * 2 < len(mandatory):
        reasons.append(f"only {len(answered)} of {len(mandatory)} mandatory questions answered")
    if reasons:
        return "rejected", reasons
    limits = []
    if len(answered) < len(mandatory):
        limits.append(f"{len(mandatory) - len(answered)} mandatory questions not fully answered")
    if dropped:
        limits.append(f"{len(dropped)} paragraphs removed in review")
    return ("validated_with_limitations" if limits else "validated"), limits


# ---------------------------------------------------------------- rendering


@dataclass
class Footnotes:
    facts: Facts
    order: dict[int, int] = field(default_factory=dict)  # source id -> its number
    sources: dict[int, Source] = field(default_factory=dict)
    passages: dict[int, list[str]] = field(default_factory=dict)

    def cite(self, text: str) -> str:
        """`[F12]` markers as the numbers of the sources they rest on: `[3][5]`."""

        def repl(m) -> str:
            nums = []
            for e, s in self.facts.quotes.get(int(m.group(1)), []):
                if s.id not in self.order:
                    self.order[s.id] = len(self.order) + 1
                    self.sources[s.id] = s
                passages = self.passages.setdefault(s.id, [])
                if e.quote not in passages:
                    passages.append(e.quote)
                nums.append(self.order[s.id])
            return "".join(f"[{n}]" for n in sorted(set(nums)))

        return MARKER.sub(repl, text).replace(" .", ".")


def _para(p: Para, notes: Footnotes) -> str:
    text = notes.cite(p.text).strip()
    if p.kind == "interpretation":
        return f"*Interpretation:* {text}"
    if p.kind == "gap":
        return f"*Not found:* {text}"
    return text


def _date(d) -> str:
    return d.strftime("%d %b %Y") if d else "-"


def render(
    run: Run,
    company_name: str,
    version: int,
    draft: Draft | None,
    facts: Facts,
    result: str,
    limits: list[str],
    dropped: list[str],
    review_notes: list[str],
) -> tuple[str, dict]:
    notes = Footnotes(facts)
    md = [
        f"# {company_name}: research report",
        "",
        f"**{OUTCOMES[result]}** · version {version} · run {run.id} · "
        f"{_date(run.finished_at or datetime.now(UTC))} · cost ${run.cost_usd:.2f} of "
        f"${run.budget_usd:.2f} · shared data contract {run.contract_version or '-'}",
        "",
        f"> {DISCLAIMER}",
    ]
    if limits:
        md += ["", "**Limitations:** " + "; ".join(limits) + "."]
    if draft is not None:
        md += ["", "## Summary", "", f"**{_para(draft.conclusion, notes)}**", ""]
        for s in draft.stances:
            md.append(f"- **{STANCES[s.label]}:** {_para(s.para, notes)}")
        md += ["", f"**What would change the view:** {_para(draft.would_change, notes)}"]
        by_key = {s.key: s for s in draft.sections}
        for key in dict.fromkeys(q.section for q in facts.plan):
            md += ["", f"## {SECTION[key].title}", ""]
            sec = by_key.get(key)
            paras = [_para(p, notes) for p in sec.paragraphs] if sec else []
            md += ["\n\n".join(paras) if paras else "*Nothing verified to report.*"]
    md += ["", "## Coverage", "", "| Question | Status |", "| --- | --- |"]
    for q in facts.plan:
        state = facts.coverage.get(q.id, "open")
        md.append(
            f"| {q.text}{'' if q.mandatory else ' (optional)'} | "
            f"{ {'answered': 'Answered', 'partly': 'Partly answered'}.get(state, 'Open') } |"
        )
    md += ["", "## Sources", ""]
    for sid, n in sorted(notes.order.items(), key=lambda kv: kv[1]):
        s = notes.sources[sid]
        bits = [s.title or s.ref]
        if s.publisher:
            bits.append(s.publisher)
        if s.published_at:
            bits.append(f"published {_date(s.published_at)}")
        bits.append(f"retrieved {_date(s.retrieved_at)}")
        where = s.ref if s.kind == "web" else f"`{s.ref}`"
        md.append(f"{n}. {', '.join(bits)}. {where}")
        for q in notes.passages.get(sid, [])[:3]:
            md.append(f"   > {q[:300]}")
    if dropped or review_notes:
        md += ["", "## Review", ""]
        md += [f"- Removed: {d}" for d in dropped]
        md += [f"- {r}" for r in review_notes]
    sections = {
        "draft": draft.model_dump() if draft else None,
        "sources": {str(n): sid for sid, n in notes.order.items()},
        "limitations": limits,
        "dropped": dropped,
        "review": review_notes,
    }
    return "\n".join(md) + "\n", sections


# ---------------------------------------------------------------- the whole step


def _event(session: Session, run: Run, stage: str, message: str, cost=Decimal(0)) -> None:
    seq = (session.scalar(select(func.max(RunEvent.seq)).where(RunEvent.run_id == run.id)) or 0) + 1
    session.add(RunEvent(run_id=run.id, seq=seq, stage=stage, message=message, cost_usd=cost))
    run.cost_usd = Decimal(run.cost_usd or 0) + cost
    session.flush()


def write_report(
    session: Session,
    run: Run,
    company_name: str,
    *,
    client: anthropic.Anthropic | None = None,
    model: str | None = None,
    today: date | None = None,
    progress: Callable[[str], None] = lambda _: None,
) -> Report:
    """Write, review and publish a report from a finished research run."""
    from stock_deep_research.config import get_settings

    settings = get_settings()
    model = model or run.model
    client = client or anthropic.Anthropic(api_key=settings.anthropic_api_key or None)
    today = today or date.today()
    budget = Budget(run, Decimal(run.budget_usd))
    facts = load(session, run)
    draft: Draft | None = None
    issues: list[Issue] = []
    review_notes: list[str] = []
    try:
        progress("Writing the report")
        draft, cost, stop = write(client, model, budget, facts, today)
        _event(session, run, "write", f"draft written ({stop})", cost)
        if draft is not None:
            issues = check(draft, facts, today)
            progress("Reviewing it")
            flagged, cost = model_review(client, model, budget, facts, draft)
            blocking = [i for i in flagged if i.blocking]
            _event(
                session,
                run,
                "review",
                f"{len(issues)} problems found by the checks, {len(blocking)} blocking and "
                f"{len(flagged) - len(blocking)} minor by the model review",
                cost,
            )
            review_notes += [f"Model review: {i.where}: {i.problem}" for i in flagged]
            if issues or blocking:
                problems = [f"{i.where}: {i.problem}" for i in issues] + [
                    f"{i.where}: {i.problem}" for i in blocking
                ]
                progress("Repairing it")
                repaired, cost, stop = write(
                    client, model, budget, facts, today, previous=draft, problems=problems
                )
                if repaired is not None:
                    draft = repaired
                    review_notes.append("One repair pass made for the problems above.")
                issues = check(draft, facts, today)
                _event(session, run, "review", f"repaired: {len(issues)} problems remain", cost)
    except BudgetStop as stop:
        _event(session, run, "budget", f"stopped: {stop}")
        review_notes.append(f"Budget reached: {stop}.")
        if draft is not None:
            issues = check(draft, facts, today)
    dropped: list[str] = []
    if draft is not None:
        draft, dropped = drop_failing(draft, issues)
    result, limits = outcome(draft, facts, dropped)
    version = (
        session.scalar(select(func.max(Report.version)).where(Report.isin == run.isin)) or 0
    ) + 1
    markdown, sections = render(
        run, company_name, version, draft, facts, result, limits, dropped, review_notes
    )
    report = Report(
        run_id=run.id,
        isin=run.isin,
        version=version,
        outcome=result,
        sections=sections,
        coverage=facts.coverage,
        markdown=markdown,
    )
    session.add(report)
    _event(session, run, "publish", f"report version {version}: {OUTCOMES[result]}")
    session.commit()
    return report


def latest(session: Session, isin: str) -> Report | None:
    return session.scalar(
        select(Report).where(Report.isin == isin).order_by(Report.version.desc()).limit(1)
    )


def versions(session: Session, isin: str) -> list[Report]:
    return list(
        session.scalars(select(Report).where(Report.isin == isin).order_by(Report.version.desc()))
    )
