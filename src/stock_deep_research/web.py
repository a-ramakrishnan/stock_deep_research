"""The open web, through Claude's web search and web fetch tools, kept as sources.

`ask_web` puts one question to the model with both tools, for what the shared data can't
answer (industry, competitors, regulation, management backgrounds). Every page it fetches is
kept as a `Source` with its URL, title, retrieval time, content hash and text; every search
result it saw but didn't fetch is kept as `listed` (no text, so it can't be cited); a page that
couldn't be fetched is kept as `blocked` or `failed`, a recorded gap. The model's answer is a
lead for the research loop (R2), not evidence: only the pages are.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

import anthropic

from stock_deep_research import costs
from stock_deep_research.evidence import Evidence
from stock_deep_research.models import Source

MAX_SEARCHES = 5
MAX_FETCHES = 6
PAGE_TOKENS = 30_000  # cap on one fetched page, to bound a call's cost
CONTINUATIONS = 3  # a long server-side tool turn may pause; resume it at most this often

TOOLS = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": MAX_SEARCHES},
    {
        "type": "web_fetch_20260209",
        "name": "web_fetch",
        "max_uses": MAX_FETCHES,
        "max_content_tokens": PAGE_TOKENS,
    },
]
BLOCKED = {"url_not_allowed", "url_not_accessible", "too_many_requests"}

SYSTEM = """You research an Indian listed company for an evidence-first report.
Answer the question from pages you fetch: search, then fetch the pages that answer it (the \
company's own site and filings, regulators, reputable news and industry bodies first). Prefer \
recent pages; say when a page is old. Quote the passages you rely on exactly as they appear. If \
the pages don't answer the question, say so: never fill a gap with what seems plausible. \
No buy, sell or hold opinions."""


@dataclass
class WebAnswer:
    answer: str
    sources: list[Source] = field(default_factory=list)
    cost_usd: Decimal = Decimal(0)
    searches: int = 0
    fetches: int = 0
    stop_reason: str | None = None


def _when(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def harvest(evidence: Evidence, content: list) -> list[Source]:
    """Keep every search result and fetched page in a response's content as sources."""
    asked: dict[str, dict] = {}  # server tool call id -> its input (the URL fetched)
    kept: list[Source] = []
    for block in content:
        kind = getattr(block, "type", None)
        if kind == "server_tool_use":
            asked[block.id] = dict(block.input or {})
        elif kind == "web_search_tool_result":
            query = asked.get(block.tool_use_id, {}).get("query")
            if not isinstance(block.content, list):  # an error object, not results
                continue
            for r in block.content:
                kept.append(
                    evidence.keep(
                        kind="web",
                        dataset="web",
                        ref=r.url,
                        title=r.title,
                        status="listed",
                        data={"page_age": r.page_age, "query": query},
                    )
                )
        elif kind == "web_fetch_tool_result":
            result = block.content
            url = getattr(result, "url", None) or asked.get(block.tool_use_id, {}).get("url")
            if not url:
                continue
            if getattr(result, "type", None) == "web_fetch_result":
                doc = result.content
                is_text = getattr(doc.source, "type", None) == "text"
                kept.append(
                    evidence.keep(
                        kind="web",
                        dataset="web",
                        ref=url,
                        title=doc.title,
                        body=doc.source.data if is_text else None,
                        status="ok" if is_text else "unreadable",  # a PDF: no text kept
                        retrieved_at=_when(result.retrieved_at),
                        data={"media_type": getattr(doc.source, "media_type", None)},
                    )
                )
            else:
                code = getattr(result, "error_code", "unknown")
                kept.append(
                    evidence.keep(
                        kind="web",
                        dataset="web",
                        ref=url,
                        status="blocked" if code in BLOCKED else "failed",
                        data={"error": code},
                    )
                )
    return kept


def ask_web(
    evidence: Evidence,
    question: str,
    client: anthropic.Anthropic | None = None,
    model: str | None = None,
) -> WebAnswer:
    """Put `question` about the run's company to the web; keep what was read."""
    from stock_deep_research.config import get_settings

    settings = get_settings()
    model = model or settings.research_model
    client = client or anthropic.Anthropic(api_key=settings.anthropic_api_key or None)
    c = evidence.company
    messages: list[dict] = [
        {
            "role": "user",
            "content": f"Company: {c.name} (NSE: {c.nse_symbol or '-'}, ISIN {c.isin}, "
            f"industry: {c.industry or 'unknown'}).\n\nQuestion: {question}",
        }
    ]
    found = WebAnswer(answer="")
    texts: list[str] = []
    for _ in range(CONTINUATIONS + 1):
        response = client.beta.messages.create(
            model=model,
            max_tokens=16_000,
            system=SYSTEM,
            tools=TOOLS,
            messages=messages,
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        found.cost_usd += costs.cost(model, response.usage)
        server = getattr(response.usage, "server_tool_use", None)
        if server:
            found.searches += server.web_search_requests or 0
            found.fetches += server.web_fetch_requests or 0
        found.sources += harvest(evidence, response.content)
        texts += [b.text for b in response.content if getattr(b, "type", None) == "text"]
        found.stop_reason = response.stop_reason
        if response.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": response.content})
    found.sources = list({s.id: s for s in found.sources}.values())
    found.answer = "\n".join(t for t in texts if t).strip()
    if found.stop_reason == "refusal":
        found.answer = "(declined)"
    evidence.run.tool_calls = (evidence.run.tool_calls or 0) + found.searches + found.fetches
    pages = sum(1 for s in found.sources if s.retrieval_status == "ok")
    evidence.event(
        "collect",
        f"web: {question} ({found.searches} searches, {found.fetches} fetches, {pages} pages kept)",
        found.cost_usd,
    )
    return found
