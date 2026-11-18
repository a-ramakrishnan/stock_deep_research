"""The open web, through Gemini's Google Search grounding and direct HTTP/PDF fetching.

`ask_web` puts one question to Gemini with Google Search grounding, keeps every grounded search
result as a `listed` source, and fetches the top URLs (HTML or PDF) via `httpx` + `pypdf` into
full-text `ok` `Source` records (or `blocked` / `failed` / `unreadable` when inaccessible). The
model's answer is a lead for the research loop (R2), not evidence: only the fetched pages are.
"""

import html
import io
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from google import genai
from google.genai import types
from pypdf import PdfReader

from stock_deep_research import costs, gemini
from stock_deep_research.evidence import Evidence
from stock_deep_research.models import Source

MAX_SEARCHES = 5
MAX_FETCHES = 6
MAX_PAGE_CHARS = 120_000
FETCH_TIMEOUT = 15.0
BLOCKED_CODES = {401, 403, 429, 451}

_SCRIPT_STYLE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.I | re.S)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"[ \t]+")
_NEWLINES = re.compile(r"\n{3,}")

SYSTEM = """You research an Indian listed company for an evidence-first report.
Use Google Search to find authoritative pages (the company's own site and filings, regulators,
credit rating agencies, industry bodies and reputable financial news). Prefer recent pages and
state dates clearly. Never fill a gap with what seems plausible. No buy, sell or hold opinions."""


@dataclass
class WebAnswer:
    answer: str
    sources: list[Source] = field(default_factory=list)
    cost_usd: Decimal = Decimal(0)
    searches: int = 0
    fetches: int = 0
    stop_reason: str | None = None


def _extract_pdf_bytes(raw: bytes | None) -> str | None:
    """Extract plain text from raw PDF bytes; None if empty or invalid."""
    if not raw:
        return None
    try:
        reader = PdfReader(io.BytesIO(raw))
        pages = [page.extract_text() or "" for page in reader.pages]
        text = "\n\n".join(p.strip() for p in pages if p.strip()).strip()
        return text[:MAX_PAGE_CHARS] or None
    except Exception:
        return None


def _extract_html_text(raw_html: str) -> tuple[str | None, str]:
    """Return `(title, clean_text)` from an HTML document."""
    m = _TITLE.search(raw_html)
    title = html.unescape(_TAG.sub("", m.group(1))).strip() if m else None
    cleaned = _SCRIPT_STYLE.sub(" ", raw_html)
    cleaned = re.sub(r"</(?:p|div|li|tr|h[1-6]|br|section|article)>", "\n", cleaned, flags=re.I)
    cleaned = html.unescape(_TAG.sub(" ", cleaned))
    lines = [_SPACE.sub(" ", line).strip() for line in cleaned.splitlines()]
    text = _NEWLINES.sub("\n\n", "\n".join(line for line in lines if line)).strip()
    return title or None, text[:MAX_PAGE_CHARS]


def harvest_grounding(evidence: Evidence, response) -> list[Source]:
    """Keep every URL returned by Gemini Google Search grounding as a `listed` source."""
    kept: list[Source] = []
    candidates = getattr(response, "candidates", None) or []
    for cand in candidates:
        gm = getattr(cand, "grounding_metadata", None)
        if not gm:
            continue
        queries = list(getattr(gm, "web_search_queries", None) or [])
        query_str = "; ".join(str(q) for q in queries) if queries else None
        for chunk in getattr(gm, "grounding_chunks", None) or []:
            web_chunk = getattr(chunk, "web", None)
            if not web_chunk:
                continue
            url = getattr(web_chunk, "uri", None)
            if not url:
                continue
            title = getattr(web_chunk, "title", None)
            kept.append(
                evidence.keep(
                    kind="web",
                    dataset="web",
                    ref=url,
                    title=title,
                    status="listed",
                    data={"query": query_str} if query_str else None,
                )
            )
    return kept


def fetch_url(
    evidence: Evidence,
    url: str,
    *,
    http: httpx.Client | None = None,
) -> Source:
    """Fetch `url` (HTML, plain text or PDF) and keep it as a `Source`."""
    now = datetime.now(UTC)
    headers = {"User-Agent": "stock-deep-research/0.1 (+https://github.com/a-ramakrishnan)"}
    try:
        if http is not None:
            resp = http.get(url, headers=headers, follow_redirects=True, timeout=FETCH_TIMEOUT)
        else:
            with httpx.Client(follow_redirects=True, timeout=FETCH_TIMEOUT) as client:
                resp = client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return evidence.keep(
            kind="web",
            dataset="web",
            ref=url,
            status="failed",
            retrieved_at=now,
            data={"error": type(exc).__name__},
        )

    final_url = str(getattr(resp, "url", url) or url)
    status_code = getattr(resp, "status_code", 200)
    if status_code in BLOCKED_CODES:
        return evidence.keep(
            kind="web",
            dataset="web",
            ref=url,
            status="blocked",
            retrieved_at=now,
            data={"status_code": status_code, "final_url": final_url},
        )
    if status_code >= 400:
        return evidence.keep(
            kind="web",
            dataset="web",
            ref=url,
            status="failed",
            retrieved_at=now,
            data={"status_code": status_code, "final_url": final_url},
        )

    content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    is_pdf = content_type == "application/pdf" or final_url.lower().endswith(".pdf")
    if is_pdf:
        body = _extract_pdf_bytes(resp.content)
        return evidence.keep(
            kind="web",
            dataset="web",
            ref=url,
            body=body,
            status="ok" if body else "unreadable",
            retrieved_at=now,
            data={"media_type": content_type or "application/pdf", "final_url": final_url},
        )

    raw_text = resp.text or ""
    if "html" in content_type or "<html" in raw_text[:500].lower():
        title, body = _extract_html_text(raw_text)
    else:
        title, body = None, raw_text.strip()[:MAX_PAGE_CHARS]

    return evidence.keep(
        kind="web",
        dataset="web",
        ref=url,
        title=title,
        body=body or None,
        status="ok" if body else "unreadable",
        retrieved_at=now,
        data={"media_type": content_type or "text/plain", "final_url": final_url},
    )


def ask_web(
    evidence: Evidence,
    question: str,
    client: genai.Client | None = None,
    model: str | None = None,
    *,
    http: httpx.Client | None = None,
    max_fetches: int = MAX_FETCHES,
) -> WebAnswer:
    """Put `question` about the run's company to Google Search via Gemini and fetch top pages."""
    from stock_deep_research.config import get_settings

    settings = get_settings()
    model = model or settings.research_model
    client = client or gemini.make_client()
    c = evidence.company
    prompt = (
        f"Company: {c.name} (NSE: {c.nse_symbol or '-'}, ISIN {c.isin}, "
        f"industry: {c.industry or 'unknown'}).\n\nQuestion: {question}"
    )
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM,
            max_output_tokens=8_000,
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )
    cost = costs.response_cost(model, response)
    searches = costs.search_count(response)
    listed = harvest_grounding(evidence, response)

    fetched: list[Source] = []
    for s in listed[:max_fetches]:
        fetched.append(fetch_url(evidence, s.ref, http=http))

    all_sources = list({s.id: s for s in [*listed, *fetched]}.values())
    found = WebAnswer(
        answer=(getattr(response, "text", None) or "").strip(),
        sources=all_sources,
        cost_usd=cost,
        searches=searches,
        fetches=len(fetched),
        stop_reason=gemini.finish_reason(response),
    )
    evidence.run.tool_calls = (evidence.run.tool_calls or 0) + found.searches + found.fetches
    pages = sum(1 for s in found.sources if s.retrieval_status == "ok")
    evidence.event(
        "collect",
        f"web: {question} ({found.searches} searches, {found.fetches} fetches, {pages} pages kept)",
        found.cost_usd,
    )
    return found
