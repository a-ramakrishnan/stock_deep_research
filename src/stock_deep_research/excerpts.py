"""Excerpts: the passages a finding rests on, located in their source's text.

A quote is looked for exactly, then with whitespace and case relaxed (model-quoted text often
reflows lines). Its position is the character where it starts in `Source.text`. A quote that
can't be found is kept with no position: such a citation fails the review (design, invariant 1)
rather than being trusted.
"""

import re

from sqlalchemy.orm import Session

from stock_deep_research.models import Excerpt, Source

_SPACE = re.compile(r"\s+")
_ELLIPSIS = re.compile(r"\s*(?:\.\.\.|…)\s*")
MIN_SPAN_CHARS = 12


def _normalised(s: str) -> tuple[str, list[int]]:
    """`s` with runs of whitespace as one space and lower case, and for each character of the
    result the index in `s` it came from."""
    out: list[str] = []
    where: list[int] = []
    for m in re.finditer(r"\s+|\S", s):
        out.append(" " if m.group().isspace() else m.group().lower())
        where.append(m.start())
    return "".join(out), where


def locate(body: str | None, quote: str) -> int | None:
    """Where `quote` starts in `body`, or None. Supports `...` / `…` multi-span quotes when every
    span is at least `MIN_SPAN_CHARS` characters and appears in order."""
    quote = quote.strip()
    if not body or not quote:
        return None
    exact = body.find(quote)
    if exact >= 0:
        return exact
    norm, where = _normalised(body)
    q = _SPACE.sub(" ", quote).lower()
    at = norm.find(q)
    if at >= 0:
        return where[at]
    if "..." in quote or "…" in quote:
        spans = [_SPACE.sub(" ", s).lower().strip() for s in _ELLIPSIS.split(quote)]
        spans = [s for s in spans if s]
        if len(spans) >= 2 and all(len(s) >= MIN_SPAN_CHARS for s in spans):
            cursor = 0
            first_at: int | None = None
            for span in spans:
                pos = norm.find(span, cursor)
                if pos < 0:
                    return None
                if first_at is None:
                    first_at = pos
                cursor = pos + len(span)
            return where[first_at]
    return None


def excerpt(session: Session, source: Source, quote: str) -> Excerpt:
    """Keep a passage of `source`, located if it is there."""
    found = Excerpt(source_id=source.id, quote=quote.strip(), start_char=locate(source.text, quote))
    session.add(found)
    session.flush()
    return found


def verify(session: Session, ex: Excerpt) -> bool:
    """True if the excerpt is still found where it says, in its source's text."""
    source = session.get(Source, ex.source_id)
    if source is None or source.text is None or ex.start_char is None:
        return False
    return locate(source.text[ex.start_char :], ex.quote) == 0
