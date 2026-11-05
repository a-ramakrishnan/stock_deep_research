"""The research tools the model may call (R2's loop), over the shared data.

Each tool keeps what it reads as sources and answers with them labelled `[S<id>]`, so the
model can cite a source by id and quote from its text. Web search and web fetch are Claude's
server tools (`web.TOOLS`); their pages are kept by `web.harvest`.
"""

import json

from stock_deep_research.evidence import DATASETS, Evidence
from stock_deep_research.models import Source

SHOWN_CHARS = 6_000  # of one source's text in a tool answer; read_document shows more
DOCUMENT_CHARS = 40_000

TOOLS = [
    {
        "name": "shared_data",
        "description": "Read one dataset of the collected data about the company (exchange "
        "filings, XBRL results, prices, shareholding, checked events, ...). Datasets:\n"
        + "\n".join(f"- {d.name}: {d.about}" for d in DATASETS.values()),
        "input_schema": {
            "type": "object",
            "properties": {
                "dataset": {"type": "string", "enum": list(DATASETS)},
                "limit": {
                    "type": "integer",
                    "description": "How many records, newest first (default per dataset).",
                },
            },
            "required": ["dataset"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_documents",
        "description": "Find the company's filings and articles that mention a phrase, with a "
        "snippet each. Read one in full with read_document.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_document",
        "description": "Read one filing or article of the company in full, by document_id.",
        "input_schema": {
            "type": "object",
            "properties": {"document_id": {"type": "integer"}},
            "required": ["document_id"],
            "additionalProperties": False,
        },
    },
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


def handle(evidence: Evidence, name: str, args: dict) -> str:
    """Run a tool call; the answer the model sees."""
    if name == "shared_data":
        dataset = args.get("dataset")
        if dataset not in DATASETS:
            return f"Unknown dataset {dataset!r}. Datasets: {', '.join(DATASETS)}."
        found = evidence.read(dataset, args.get("limit"))
        if not found:
            return f"No {dataset} records for this company in the collected data."
        return "\n\n".join(show(s) for s in found)
    if name == "search_documents":
        hits = evidence.search_documents(str(args.get("query", "")))
        if not hits:
            return "No filing or article mentions that."
        return json.dumps(hits, default=str, indent=1)
    if name == "read_document":
        source = evidence.read_document(int(args.get("document_id", 0)))
        if source is None:
            return "No such document for this company."
        return show(source, DOCUMENT_CHARS)
    return f"Unknown tool {name!r}."
