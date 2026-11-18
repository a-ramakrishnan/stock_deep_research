"""The research tools the model may call (R2's loop), over the shared data and the open web.

Each tool keeps what it reads as sources and answers with them labelled `[S<id>]`, so the
model can cite a source by id and quote from its text. Web search uses Gemini's Google Search
grounding (`web_search`) and direct HTTP/PDF fetch (`web_fetch`), kept by `web.py`.
"""

import json

from google import genai
from google.genai import types

from stock_deep_research import web
from stock_deep_research.evidence import DATASETS, Evidence
from stock_deep_research.models import Source

SHOWN_CHARS = 6_000  # of one source's text in a tool answer; read_document/web_fetch show more
DOCUMENT_CHARS = 40_000

TOOLS = [
    {
        "name": "shared_data",
        "description": "Read one dataset of the collected data about the company (exchange "
        "filings, XBRL results, prices, shareholding, checked events, ...). Datasets:\n"
        + "\n".join(f"- {d.name}: {d.about}" for d in DATASETS.values()),
        "parameters": {
            "type": "object",
            "properties": {
                "dataset": {"type": "string", "enum": list(DATASETS)},
                "limit": {
                    "type": "integer",
                    "description": "How many records, newest first (default per dataset).",
                },
            },
            "required": ["dataset"],
        },
    },
    {
        "name": "search_documents",
        "description": "Find the company's filings and articles that mention a phrase, with a "
        "snippet each. Read one in full with read_document.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "read_document",
        "description": "Read one filing or article of the company in full, by document_id.",
        "parameters": {
            "type": "object",
            "properties": {"document_id": {"type": "integer"}},
            "required": ["document_id"],
        },
    },
    {
        "name": "web_search",
        "description": "Search the open web using Google Search for industry, peers, credit "
        "ratings, management or regulatory details not in the filings. Returns candidate URLs; "
        "fetch the best ones with web_fetch to read and quote them.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "web_fetch",
        "description": "Fetch a web page or PDF URL and return its plain text as a citable "
        "source [S<id>].",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
]

DECLARATIONS = [
    types.FunctionDeclaration(
        name=t["name"],
        description=t["description"],
        parameters_json_schema=t["parameters"],
    )
    for t in TOOLS
]


def show(source: Source, chars: int = SHOWN_CHARS) -> str:
    head = f"[S{source.id}] {source.title or source.ref}"
    if source.published_at:
        head += f" (published {source.published_at.date().isoformat()})"
    head += f"\nref: {source.ref}"
    if not source.text:
        return head + f"\n(no text: {source.retrieval_status})"
    body = source.text
    if len(body) > chars:
        body = body[:chars] + f"\n... ({len(source.text) - chars} more characters)"
    return f"{head}\n{body}"


def handle(
    evidence: Evidence,
    name: str,
    args: dict,
    *,
    client: genai.Client | None = None,
    model: str | None = None,
) -> tuple[str, int, int]:
    """Run a tool call; returns `(answer_text, extra_searches, extra_fetches)`."""
    if name == "shared_data":
        dataset = args.get("dataset")
        if dataset not in DATASETS:
            return f"Unknown dataset {dataset!r}. Datasets: {', '.join(DATASETS)}.", 0, 0
        found = evidence.read(dataset, args.get("limit"))
        if not found:
            return f"No {dataset} records for this company in the collected data.", 0, 0
        return "\n\n".join(show(s) for s in found), 0, 0
    if name == "search_documents":
        hits = evidence.search_documents(str(args.get("query", "")))
        if not hits:
            return "No filing or article mentions that.", 0, 0
        return json.dumps(hits, default=str, indent=1), 0, 0
    if name == "read_document":
        source = evidence.read_document(int(args.get("document_id", 0)))
        if source is None:
            return "No such document for this company.", 0, 0
        return show(source, DOCUMENT_CHARS), 0, 0
    if name == "web_search":
        if client is None or model is None:
            return "web_search requires an active Gemini client.", 0, 0
        query = str(args.get("query", "")).strip()
        if not query:
            return "Empty search query.", 0, 0
        resp = client.models.generate_content(
            model=model,
            contents=query,
            config=types.GenerateContentConfig(
                max_output_tokens=2_000,
                tools=[types.Tool(google_search=types.GoogleSearch())],
            ),
        )
        listed = web.harvest_grounding(evidence, resp)
        lines = [f"Summary: {(getattr(resp, 'text', None) or '').strip()}"]
        for s in listed:
            lines.append(f"- [S{s.id}] {s.title or s.ref} ({s.ref})")
        return "\n".join(lines), max(1, len(getattr(resp, "candidates", None) or [])), 0
    if name == "web_fetch":
        url = str(args.get("url", "")).strip()
        if not url:
            return "Empty URL.", 0, 0
        source = web.fetch_url(evidence, url)
        return show(source, DOCUMENT_CHARS), 0, 1
    return f"Unknown tool {name!r}.", 0, 0
