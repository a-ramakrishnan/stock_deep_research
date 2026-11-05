"""The research loop (R2): plan, collect, assess, follow up, within a budget.

1. **Plan:** the report's questions by section (plan.py), plus the request's own.
2. **Collect:** every dataset of the shared data (free, exact, dated) and the figures code
   computes from them (derived.py).
3. **Assess:** per section, one model call reads that section's sources and returns findings
   (atomic claims, fact or interpretation, with quoted passages) and the gaps it sees. Code then
   locates every quote in its source; a fact none of whose quotes is found is downgraded to
   `insufficient`. Interpretations and facts are kept apart, and nothing is filled in.
4. **Follow up:** for each question not yet answered (mandatory first), one tool-using call
   reads what it needs (the research tools over the shared data, web search and fetch), and the
   question is assessed again over what was read.
5. **Budget:** research may use `RESEARCH_SHARE` of the report's budget, leaving the rest for
   writing and review (R3). Every call is reserved first (budget.py); when the next call could
   cross the ceiling the run stops as `partial`, with everything found so far kept.

What the run did is in its decision record (`run_events`), not the model's reasoning.
"""

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

import anthropic
from pydantic import BaseModel
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from stock_deep_research import costs, excerpts, tools, web
from stock_deep_research.budget import Budget, BudgetStop
from stock_deep_research.derived import derive
from stock_deep_research.evidence import Evidence, new_run
from stock_deep_research.models import Finding, Run, Source
from stock_deep_research.plan import SECTION, SECTIONS, Question, Section, make_plan, to_json
from stock_deep_research.shared import Company

RESEARCH_SHARE = Decimal("0.7")
ASSESS_TOKENS = 16_000  # thinking and the findings
SOURCE_CHARS = 5_000  # of one source in an assessment's context
DOCUMENT_CHARS = 12_000  # of a filing, article or web page
SECTION_CHARS = 140_000  # of all sources in one assessment (about 40k tokens)
MAX_FOLLOW_UPS = 6
GATHER_TURNS = 8
GATHER_TOKENS = 8_000
GATHER_SEARCHES = 3
GATHER_FETCHES = 4


class Citation(BaseModel):
    source: int  # the number after S in [S12]
    quote: str  # copied exactly from that source's text


class FindingOut(BaseModel):
    question: str  # the question's id
    claim: str
    claim_type: Literal["fact", "interpretation"]
    support: Literal["supported", "conflicting", "insufficient", "inferred"]
    citations: list[Citation]
    period: str  # ISO date of the period end or event the claim is about, or ""
    limitations: str  # "" if none


class GapOut(BaseModel):
    question: str
    missing: str
    look: Literal["web", "filings", "none"]  # where it might be found
    query: str  # what to search for, or ""


class Assessment(BaseModel):
    findings: list[FindingOut]
    gaps: list[GapOut]


ASSESS_SYSTEM = """You assess evidence about an Indian listed company for a research report.
You get questions and numbered sources ([S12]: a record of exchange filings or market data, \
figures computed by code, a news article or a web page). Turn what the sources say into findings.

- One finding is one atomic claim answering one question (by its id).
- A fact states what a source says; an interpretation is your reading of facts. Label each.
- Cite every finding: the source number and a passage copied exactly from that source's text \
(a whole `field: value` line for a record; a sentence for prose). No citation, no fact.
- Support: supported (the passages say it), conflicting (sources disagree; cite both), \
inferred (reasoned from facts, not stated), insufficient (what there is falls short).
- Numbers: use them as the sources give them, with their period and unit (Rs crore, %). Don't \
compute: growth, margins, P/E and the like are in the computed figures source when they exist.
- Period: the date the claim is about (a period end or event date), not when it was published.
- Say what is missing as gaps, with where it might be found and what to search for. Never fill \
a gap with what seems plausible.
- No buy, sell or hold views, no price targets."""

GATHER_SYSTEM = """You collect evidence about an Indian listed company for one open question \
of a research report. Use the tools: shared_data, search_documents and read_document for the \
filings, results and news already collected; web_search and web_fetch for the rest (the \
company's site and reports, regulators, industry bodies, reputable news). Read the pages that \
answer the question; a search result is only a pointer. Stop when you have read enough, and \
reply in one or two lines saying what you found and what you could not find."""


def _clip(s: Source) -> str:
    limit = DOCUMENT_CHARS if s.kind == "web" or s.dataset in ("filings", "news") else SOURCE_CHARS
    head = f"[S{s.id}] {s.title or s.ref} ({s.dataset or s.kind}"
    if s.published_at:
        head += f", published {s.published_at.date().isoformat()}"
    head += ")"
    if s.kind == "web":
        head += f"\n{s.ref}"
    body = s.text or ""
    if len(body) > limit:
        body = body[:limit] + f"\n[... {len(s.text) - limit} more characters not shown]"
    return f"{head}\n{body}"


def context(found: list[Source], cap: int = SECTION_CHARS) -> tuple[str, list[Source]]:
    """The sources as the model sees them, and those shown. Each dataset gets a fair share of
    `cap` characters (newest records first), so long filings don't crowd out the rest."""
    groups: dict[str, list[Source]] = {}
    for s in found:
        if s.text:
            groups.setdefault(s.dataset or s.kind, []).append(s)
    if not groups:
        return "", []
    share = cap // len(groups)
    parts, shown = [], []
    for members in groups.values():
        size = 0
        for s in members:
            block = _clip(s)
            if size + len(block) > share and size > 0:
                break
            parts.append(block)
            shown.append(s)
            size += len(block)
    return "\n\n".join(parts), shown


def section_sources(evidence: Evidence, section: Section) -> list[Source]:
    sym = evidence.company.nse_symbol or evidence.company.isin
    found: dict[int, Source] = {}
    for name in section.datasets:
        if name == "derived":
            d = evidence.get(f"derived:{sym}:figures")
            batch = [d] if d else []
        else:
            batch = evidence.read(name)
        for s in batch:
            found.setdefault(s.id, s)
    return list(found.values())


def _questions(questions: list[Question]) -> str:
    return "\n".join(f"- [{q.id}] {q.text}" for q in questions)


def _company(c: Company) -> str:
    return f"{c.name} (NSE: {c.nse_symbol or '-'}, ISIN {c.isin}, industry: {c.industry or '?'})"


def assess(
    evidence: Evidence,
    client: anthropic.Anthropic,
    model: str,
    budget: Budget,
    section: Section,
    questions: list[Question],
    found: list[Source],
    today: date,
) -> tuple[list[Finding], list[GapOut]]:
    """Findings for `questions` from `found`; quotes located by code."""
    body, shown = context(found)
    prompt = (
        f"Company: {_company(evidence.company)}\nToday: {today.isoformat()}\n"
        f"Section: {section.title}\n\nQuestions:\n{_questions(questions)}\n\nSources:\n\n{body}"
    )
    budget.reserve(model, len(ASSESS_SYSTEM) + len(prompt), ASSESS_TOKENS)
    response = client.messages.parse(
        model=model,
        max_tokens=ASSESS_TOKENS,
        system=ASSESS_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_format=Assessment,
        output_config={"effort": "medium"},
    )
    cost = costs.response_cost(model, response)
    parsed = response.parsed_output if response.stop_reason != "refusal" else None
    if parsed is None:
        evidence.event(
            "assess",
            f"{section.title}: no assessment ({response.stop_reason}) over {len(shown)} sources",
            cost,
        )
        return [], []
    by_id = {s.id: s for s in shown}
    asked = {q.id for q in questions}
    kept: list[Finding] = []
    for f in parsed.findings:
        if f.question not in asked:
            continue
        ids, located, notes = [], 0, [f.limitations] if f.limitations else []
        for c in f.citations:
            source = by_id.get(c.source)
            if source is None:
                notes.append(f"cites S{c.source}, which it was not shown")
                continue
            ex = excerpts.excerpt(evidence.session, source, c.quote)
            ids.append(ex.id)
            located += ex.start_char is not None
        support = f.support
        if f.claim_type == "fact" and located == 0 and support in ("supported", "conflicting"):
            support = "insufficient"
            notes.append("no quoted passage was found in its sources")
        finding = Finding(
            run_id=evidence.run.id,
            section=section.key,
            question=f.question,
            claim=f.claim,
            claim_type=f.claim_type,
            support=support,
            excerpt_ids=ids,
            effective_date=_period(f.period),
            limitations="; ".join(notes) or None,
        )
        evidence.session.add(finding)
        kept.append(finding)
    evidence.session.flush()
    gaps = [g for g in parsed.gaps if g.question in asked]
    evidence.event(
        "assess",
        f"{section.title}: {len(kept)} findings, {len(gaps)} gaps from {len(shown)} sources",
        cost,
    )
    return kept, gaps


def _period(v: str) -> date | None:
    try:
        return date.fromisoformat(v[:10]) if v else None
    except ValueError:
        return None


def _tool_input_chars(messages: list) -> int:
    return len(json.dumps(messages, default=lambda o: repr(o)))


def gather(
    evidence: Evidence,
    client: anthropic.Anthropic,
    model: str,
    budget: Budget,
    question: Question,
    missing: str,
) -> list[Source]:
    """One tool-using pass at an open question. Returns the readable sources it read."""
    start = evidence.mark()
    web_tools = [
        {**web.TOOLS[0], "max_uses": GATHER_SEARCHES},
        {**web.TOOLS[1], "max_uses": GATHER_FETCHES},
    ]
    messages: list[dict] = [
        {
            "role": "user",
            "content": f"Company: {_company(evidence.company)}\n\nQuestion: {question.text}\n"
            + (f"What is missing so far: {missing}\n" if missing else ""),
        }
    ]
    searches = fetches = turns = 0
    cost = Decimal(0)
    try:
        for _ in range(GATHER_TURNS):
            turns += 1
            budget.reserve(
                model,
                len(GATHER_SYSTEM) + _tool_input_chars(messages),
                GATHER_TOKENS,
                searches=GATHER_SEARCHES,
            )
            response = client.beta.messages.create(
                model=model,
                max_tokens=GATHER_TOKENS,
                system=GATHER_SYSTEM,
                tools=[*tools.TOOLS, *web_tools],
                messages=messages,
                cache_control={"type": "ephemeral"},
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            step = costs.response_cost(model, response)
            cost += step
            evidence.run.cost_usd = Decimal(evidence.run.cost_usd or 0) + step
            server = getattr(response.usage, "server_tool_use", None)
            if server:
                searches += server.web_search_requests or 0
                fetches += server.web_fetch_requests or 0
            web.harvest(evidence, response.content)
            messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason == "tool_use":
                results = [
                    {
                        "type": "tool_result",
                        "tool_use_id": b.id,
                        "content": tools.handle(evidence, b.name, dict(b.input or {})),
                    }
                    for b in response.content
                    if getattr(b, "type", None) == "tool_use"
                ]
                evidence.run.tool_calls = (evidence.run.tool_calls or 0) + len(results)
                messages.append({"role": "user", "content": results})
                continue
            if response.stop_reason == "pause_turn":
                continue
            break
    finally:
        # the cost so far was added to the run step by step; record the step once
        evidence.run.cost_usd = Decimal(evidence.run.cost_usd or 0) - cost
        evidence.run.tool_calls = (evidence.run.tool_calls or 0) + searches + fetches
        read = {sid for sid in evidence.touched[start:]}
        evidence.event(
            "follow up",
            f"{question.id}: {turns} turns, {searches} searches, {fetches} fetches, "
            f"{len(read)} sources read",
            cost,
        )
    found = [evidence.session.get(Source, sid) for sid in dict.fromkeys(evidence.touched[start:])]
    return [s for s in found if s is not None and s.text]


def coverage(session: Session, run: Run, plan: list[Question]) -> dict[str, str]:
    """Each question: answered (a supported finding), partly (findings, none supported), open."""
    findings = session.scalars(select(Finding).where(Finding.run_id == run.id)).all()
    out = {}
    for q in plan:
        mine = [f for f in findings if f.question == q.id]
        if any(f.support == "supported" for f in mine):
            out[q.id] = "answered"
        elif mine:
            out[q.id] = "partly"
        else:
            out[q.id] = "open"
    return out


def research(
    session: Session,
    shared: Engine,
    company: Company,
    *,
    request: str = "report",
    questions: list[str] | None = None,
    client: anthropic.Anthropic | None = None,
    model: str | None = None,
    budget_usd: float | None = None,
    contract_version: str | None = None,
    today: date | None = None,
    progress: Callable[[str], None] = lambda _: None,
) -> Run:
    """Research a company: plan, collect, assess, follow up. Returns the finished run."""
    from stock_deep_research.config import get_settings

    settings = get_settings()
    model = model or settings.research_model
    budget_usd = budget_usd if budget_usd is not None else settings.report_budget_usd
    client = client or anthropic.Anthropic(api_key=settings.anthropic_api_key or None)
    today = today or date.today()

    run = new_run(session, company, request, model, budget_usd, contract_version)
    plan = make_plan(questions)
    run.plan = {"questions": to_json(plan)}
    ev = Evidence(session, run, shared, company)
    budget = Budget(run, Decimal(str(budget_usd)) * RESEARCH_SHARE)
    ev.event(
        "plan",
        f"{len(plan)} questions ({sum(q.mandatory for q in plan)} mandatory); research may "
        f"spend ${budget.ceiling:.2f} of ${budget_usd:.2f}",
    )
    session.commit()
    gaps: dict[str, GapOut] = {}
    try:
        progress("Reading the shared data")
        counts = ev.collect()
        figures = derive(ev)
        ev.event(
            "collect",
            f"shared data: {sum(counts.values())} sources; "
            f"{len(figures.data['lines']) if figures else 0} computed figures",
        )
        session.commit()

        for section in SECTIONS:
            asked = [q for q in plan if q.section == section.key]
            if not asked:
                continue
            progress(f"Assessing: {section.title}")
            _, found_gaps = assess(
                ev, client, model, budget, section, asked, section_sources(ev, section), today
            )
            for g in found_gaps:
                gaps.setdefault(g.question, g)
            session.commit()

        cover = coverage(session, run, plan)
        todo = sorted(
            (q for q in plan if cover[q.id] != "answered"),
            key=lambda q: (not q.mandatory, cover[q.id] == "partly"),
        )
        for q in todo[:MAX_FOLLOW_UPS]:
            gap = gaps.get(q.id)
            if gap and gap.look == "none":
                ev.event("follow up", f"{q.id}: skipped, nothing more to look for ({gap.missing})")
                continue
            progress(f"Following up: {q.text}")
            read = gather(ev, client, model, budget, q, gap.missing if gap else "")
            session.commit()
            if not read:
                continue
            figures_src = [figures] if figures else []
            assess(ev, client, model, budget, SECTION[q.section], [q], read + figures_src, today)
            session.commit()
        run.status = "succeeded"
    except BudgetStop as stop:
        ev.event("budget", f"stopped: {stop}")
        run.status = "partial"
    except Exception as exc:
        session.rollback()
        run = session.get(Run, run.id)
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}"
        run.finished_at = datetime.now(UTC)
        session.commit()
        raise
    cover = coverage(session, run, plan)
    run.plan = {"questions": to_json(plan), "coverage": cover}
    run.finished_at = datetime.now(UTC)
    done = sum(v == "answered" for v in cover.values())
    ev.event("done", f"{run.status}: {done} of {len(cover)} questions answered, ${run.cost_usd}")
    session.commit()
    return run


def findings(session: Session, run_id: int) -> list[Finding]:
    """A run's findings in the report's section order."""
    order = {s.key: i for i, s in enumerate(SECTIONS)}
    found = session.scalars(select(Finding).where(Finding.run_id == run_id).order_by(Finding.id))
    return sorted(found, key=lambda f: order.get(f.section, len(order)))
