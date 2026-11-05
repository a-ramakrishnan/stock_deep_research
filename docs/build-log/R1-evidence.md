# R1: Evidence

**Goal:** everything a run reads is kept as it was read (shared records and web pages), and a passage a claim relies on can be located in its source. Done when a company's evidence can be collected and inspected.

- **Shared records as sources** (`evidence.py`): 18 datasets over the `shared` views, newest first to a default depth: company, peers, results, balance sheets, price summary, month-end prices, index valuations (Nifty 50 and the company's benchmark), corporate actions, shareholding, filings with text, relevant news (stock_news_analyzer's triage), checked events (`shown` only), orders, order books, earnings calls with their points, promises, red flags not dismissed, deals. A record's text is one `field: value` line per field; a series is a line per row. Refs are stable (`shared.results:LT:2026-09-30:3m:consolidated`); a ref is kept once per run (migration 0002 adds `dataset`, `data` and that constraint).
- **Research tools** (`tools.py`) for R2's loop: `shared_data`, `search_documents`, `read_document`; answers label sources `[S<id>]`.
- **The web** (`web.py`): one question with Claude's web search and web fetch tools; fetched pages kept with URL, title, retrieval time, hash and text; search results not fetched kept as `listed`; failures as `blocked` or `failed`; PDFs as `unreadable`. A paused server turn is resumed (at most 3 times). Cost from usage at list prices plus $0.01 a search (`costs.py`), added to the run with a line in its decision record. Refusals fall back server-side.
- **Excerpts** (`excerpts.py`): a quote is located exactly, then with whitespace and case relaxed; not found, it is kept without a position (it will fail R3's review). `verify` re-checks one.
- **Inspecting:** `sdr evidence SYMBOL [--web QUESTION]...`, `sdr sources RUN [--dataset]`, `sdr source ID`; an Evidence page (collect, pick a run, filter by dataset, read a source as it was read).

### Checked

The test stand-in for the shared views now has every view of contract 1.0 with a little L&T data. Tests collect every dataset (filings without text, irrelevant news, events that failed their check and dismissed flags are left out; collecting twice keeps nothing twice), locate excerpts exactly and reflowed and mark a missing one, run the three research tools, keep web pages from a fake two-part (paused, then resumed) response with a page read, a search result not fetched, a blocked page and a PDF, and check the cost, and run the commands. 13 tests pass. Against stock_news_analyzer's synthetic database migrated to 0030, `sdr evidence LT` kept 56 records across 13 datasets; the Evidence page renders them.

Not checked here: a real web call (no API key in this environment). The first `--web` question on the Mac is the check.

## On the Mac

```
cd stock_deep_research
git pull
uv sync
uv run sdr db init
uv run sdr evidence LT
uv run sdr sources 1
uv run sdr evidence LT --web "Who are L&T's main competitors in Indian EPC, and how big are they?"
make ui
```
