"""The deterministic review: checks a draft must pass before any model looks at it.

For each paragraph of a draft:

1. **Citations:** a fact or interpretation paragraph cites findings (`[F12]`) that exist in the
   run; a fact paragraph cites at least one supported fact whose quote was found in its source
   (invariant 1); a gap paragraph states what is missing and cites nothing it needs to.
2. **Numbers:** every number in the text is in a passage quoted by the findings it cites (the
   computed figures included), allowing for rounding: "10.0%" matches 9.966, "Rs 67,079 crore"
   matches 67079.45. Labels (FY27, Q2, S12), years, day-of-month and counts up to twelve are not
   figures.
3. **Dates:** a finding cited outside "what to watch" is not about a date after today.
4. **No advice:** no buy, sell or hold call, no price target, no recommendation.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

MARKER = re.compile(r"\[F(\d+)\]")
# A figure: digits with Indian or Western grouping, optional decimals; not part of a label
NUMBER = re.compile(r"(?<![A-Za-z0-9.])(\d{1,3}(?:,\d{2,3})+|\d+)(?:\.(\d+))?(?![A-Za-z0-9])")
MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
FIGURE_AFTER = re.compile(r"\s*(%|x\b|×|crore|cr\b|lakh|bn|billion|mn|million|per cent)", re.I)
ADVICE = re.compile(
    r"\b(we recommend|recommend(?:ed|s)? (?:buying|selling|holding)|target price|price target|"
    r"(?:a |rated? )?(?:buy|sell|hold|accumulate|outperform|underperform) (?:rating|call)|"
    r"(?:buy|sell|accumulate) (?:the|this) (?:stock|shares?))\b",
    re.I,
)


@dataclass
class Cited:
    """What a finding brings to a paragraph."""

    id: int
    claim_type: str  # fact, interpretation
    support: str
    effective_date: date | None
    quotes: list[str] = field(default_factory=list)  # passages found in their sources
    missing_quotes: int = 0  # passages not found


@dataclass
class Issue:
    where: str  # "summary.conclusion", "financials.2"
    problem: str


def figures(text: str) -> list[tuple[Decimal, int, str]]:
    """The figures in `text`: (value, decimals written, as written). Labels, years, days of the
    month and small counts are left out."""
    text = MARKER.sub(" ", text)
    out = []
    for m in NUMBER.finditer(text):
        whole, frac = m.group(1), m.group(2)
        try:
            value = Decimal(whole.replace(",", "") + (f".{frac}" if frac else ""))
        except InvalidOperation:
            continue
        after = text[m.end() : m.end() + 12]
        before = text[max(0, m.start() - 12) : m.start()]
        unit = FIGURE_AFTER.match(after)
        if frac is None and not unit and "Rs" not in before and "₹" not in before:
            if value <= 12:
                continue  # a count: three quarters, 2 segments
            if 1990 <= value <= 2100:
                continue  # a year
            if value <= 31 and (
                re.match(rf"\s*{MONTHS}", after, re.I) or re.search(rf"{MONTHS}\s*$", before, re.I)
            ):
                continue  # a day of the month
        if text[max(0, m.start() - 1) : m.start()] == "-" and value <= 12:
            continue  # a range like 2-3
        out.append((value, len(frac or ""), m.group(0)))
    return out


def _values(texts: list[str]) -> list[Decimal]:
    out = []
    for t in texts:
        for m in NUMBER.finditer(t):
            whole, frac = m.group(1), m.group(2)
            try:
                out.append(Decimal(whole.replace(",", "") + (f".{frac}" if frac else "")))
            except InvalidOperation:
                pass
    return out


def matches(value: Decimal, decimals: int, allowed: list[Decimal]) -> bool:
    step = Decimal(1).scaleb(-decimals)
    for a in allowed:
        a = abs(a)  # "down 8.9%" is the passage's -8.9
        if a.quantize(step) == value or abs(a - value) <= step / 2:
            return True
    return False


def unsupported_figures(text: str, quotes: list[str]) -> list[str]:
    allowed = _values(quotes)
    return [written for value, k, written in figures(text) if not matches(value, k, allowed)]


def check_paragraph(
    where: str,
    kind: str,
    text: str,
    cited: dict[int, Cited],
    section: str,
    today: date,
) -> list[Issue]:
    issues: list[Issue] = []
    ids = [int(i) for i in MARKER.findall(text)]
    unknown = [i for i in ids if i not in cited]
    if unknown:
        issues.append(Issue(where, "cites findings that don't exist: " + _ids(unknown)))
    mine = [cited[i] for i in ids if i in cited]
    if kind in ("fact", "interpretation") and not mine:
        issues.append(Issue(where, f"a {kind} paragraph that cites no finding"))
    if (
        kind == "fact"
        and mine
        and not any(
            c.claim_type == "fact" and c.support in ("supported", "conflicting") and c.quotes
            for c in mine
        )
    ):
        issues.append(Issue(where, "a fact paragraph with no supported fact whose quote was found"))
    quotes = [q for c in mine for q in c.quotes]
    loose = unsupported_figures(text, quotes)
    if loose:
        issues.append(Issue(where, "figures not in the passages it cites: " + ", ".join(loose[:6])))
    if section != "catalysts":
        future = [c for c in mine if c.effective_date and c.effective_date > today]
        if future:
            issues.append(
                Issue(where, "cites findings dated after today: " + _ids(c.id for c in future))
            )
    if ADVICE.search(text):
        issues.append(Issue(where, "reads as investment advice: " + ADVICE.search(text).group(0)))
    return issues


def _ids(ids) -> str:
    return ", ".join(f"F{i}" for i in ids)
