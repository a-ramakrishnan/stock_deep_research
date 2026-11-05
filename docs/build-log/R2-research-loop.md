# R2: The research loop

**Goal:** a run produces findings with quoted passages, within $5.

- **Plan** (`plan.py`): the report's questions by section (business, industry, management, financials, balance sheet, valuation, catalysts, risks): 16, of which 15 are mandatory. `--question` adds your own, as optional.
- **Computed figures** (`derived.py`): margins, growth against the same quarter a year earlier, trailing EPS and P/E, multi-year growth, net debt, debt to equity, free cash flow, cash from operations against profit. Code computes them from the records, each line names the sources it used, and the model cites them instead of doing arithmetic.
- **Assess** (`research.py`): one structured call per section over that section's datasets, with a fair share of the context for each. It returns atomic findings (fact or interpretation; supported, conflicting, inferred or insufficient; quoted passages; the period they concern; limitations) and the gaps it sees. Code locates every quote. A fact with no quote found, or one citing a source it wasn't shown, is marked insufficient, and its limitations say why.
- **Follow up:** up to six unanswered questions (mandatory first). Each gets one tool-using pass with the shared-data tools and web search and fetch (at most 8 turns, 3 searches, 4 fetches), and is then assessed again over what was read.
- **Budget** (`budget.py`):
  - research may spend 70% of `REPORT_BUDGET_USD` ($3.50 of $5); the rest is kept for writing and review (R3)
  - each call reserves its worst case first, and a call that could cross the ceiling stops the run as `partial` with everything kept
  - actual costs come from usage, at the served model's prices
- **Coverage:** each question is answered, partly answered or open, stored on the run. The decision record logs every stage with its cost.
- **Running it:** `sdr research SYMBOL [--question ...] [--budget N]` and `sdr findings RUN`, plus a Findings page (start a research run, read the findings by question with their passages, see coverage and cost).

### Checked

Tests cover the following:
- The plan covers every section.
- The budget's worst case, and its refusal of a call that could cross the ceiling.
- The computed figures on stand-in data: margins, year-on-year growth, trailing EPS of Rs 115.86 and P/E of 33.8, revenue growth a year, net debt, cash conversion, and each line citing its sources.
- Fair context shares.
- A whole run with a fake model:
  - quotes located
  - a made-up quote marked insufficient
  - a web gap followed up through a shared-data call, a search and a fetch, then answered from the page
  - a "nothing to look for" gap skipped
  - the decision record adding up to the run's cost
- A $0.60 budget stopping the run as partial within $0.42.

19 tests pass. On stock_news_analyzer's synthetic database migrated to 0030, the figures and section contexts compute, and a fake-model run renders on the Findings page.

Not checked here: a real run (no API key in this environment). Expected cost is about $1–2.50 for a large company. The first `sdr research LT` on the Mac is the check, and `sdr findings` shows what it cost.

## On the Mac

```
cd stock_deep_research
git pull
uv sync
uv run sdr db init
uv run sdr research LT
make ui
```
