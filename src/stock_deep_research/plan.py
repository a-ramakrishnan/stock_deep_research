"""The plan: the questions a report must answer, by section (docs/design/design.md, section 5).

Mandatory questions are the report's minimum; a request may add its own. Each section names the
shared datasets that can answer it; the web covers the rest. The summary (section 1) is written
from the findings in R3, and section 10 is the coverage itself, so neither is assessed here.
"""

from dataclasses import asdict, dataclass


@dataclass
class Question:
    id: str
    section: str
    text: str
    mandatory: bool = True


@dataclass
class Section:
    key: str
    title: str
    datasets: tuple[str, ...]


SECTIONS = [
    Section(
        "business", "Business", ("company", "derived", "filings", "concalls", "orders", "news")
    ),
    Section("industry", "Industry and competition", ("company", "peers", "concalls", "news")),
    Section(
        "management",
        "Management and governance",
        ("events", "promises", "red_flags", "concalls", "shareholding", "deals"),
    ),
    Section("financials", "Financials", ("derived", "results", "concalls")),
    Section("balance", "Balance sheet and cash", ("derived", "balance_sheets", "results")),
    Section(
        "valuation",
        "Valuation",
        (
            "derived",
            "price_summary",
            "prices_monthly",
            "index_valuation",
            "corporate_actions",
            "peers",
            "results",
        ),
    ),
    Section(
        "catalysts",
        "Catalysts and what to watch",
        ("events", "order_books", "orders", "promises", "concalls", "filings"),
    ),
    Section("risks", "Risks", ("red_flags", "events", "concalls", "news", "shareholding")),
    Section("request", "Your questions", ("company", "derived", "concalls", "news", "filings")),
]
SECTION = {s.key: s for s in SECTIONS}

QUESTIONS = [
    Question("business.what", "business", "What does the company sell, and to whom?"),
    Question(
        "business.segments",
        "business",
        "What are its segments (or main businesses) and their share of revenue?",
    ),
    Question(
        "business.geography",
        "business",
        "Where does its revenue come from (domestic, export, regions)?",
        mandatory=False,
    ),
    Question("industry.market", "industry", "What market does it serve, and what drives demand?"),
    Question(
        "industry.position",
        "industry",
        "Who are its main competitors, and how does it compare with them in size and share?",
    ),
    Question(
        "management.who",
        "management",
        "Who runs the company (chair, CEO/MD, CFO), and what changed recently?",
    ),
    Question(
        "management.promises",
        "management",
        "What has management promised, and which promises were kept or missed?",
    ),
    Question(
        "management.governance",
        "management",
        "What governance concerns are on record (red flags, auditor remarks, related parties, "
        "promoter pledges or selling)?",
    ),
    Question(
        "financials.quarters",
        "financials",
        "How have revenue, profit and margins moved over the last eight quarters, against the "
        "same quarters a year earlier?",
    ),
    Question(
        "financials.years",
        "financials",
        "How have revenue and profit grown over the last five years?",
    ),
    Question(
        "balance.debt",
        "balance",
        "How much debt and cash does it have, and how has that changed?",
    ),
    Question(
        "balance.cash",
        "balance",
        "Does cash from operations keep up with profit, and how much does it spend on capex?",
    ),
    Question(
        "valuation.multiples",
        "valuation",
        "What is its P/E (and other multiples), against its own history, its peers and the index?",
    ),
    Question(
        "valuation.ranges",
        "valuation",
        "What would bear, base and bull outcomes assume (growth, margins, multiple), each "
        "assumption sourced or marked as an assumption?",
    ),
    Question(
        "catalysts.dated",
        "catalysts",
        "What dated events are ahead (results, board meetings, orders, approvals, guidance "
        "deadlines)?",
    ),
    Question(
        "risks.main",
        "risks",
        "What are the main risks, each tied to evidence?",
    ),
]


def make_plan(extra: list[str] | None = None) -> list[Question]:
    """The mandatory questions, plus the request's own (not mandatory, in their own section)."""
    plan = list(QUESTIONS)
    for i, text in enumerate(extra or [], 1):
        plan.append(Question(f"request.{i}", "request", text.strip(), mandatory=False))
    return plan


def to_json(plan: list[Question]) -> list[dict]:
    return [asdict(q) for q in plan]


def from_json(rows: list[dict]) -> list[Question]:
    return [Question(**r) for r in rows]
