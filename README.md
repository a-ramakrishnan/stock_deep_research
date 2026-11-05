# stock_deep_research

A deep research report for any Indian listed stock: evidence first, every claim cited, conclusion up front. It reads the data [stock_news_analyzer](https://github.com/a-ramakrishnan/stock_news_analyzer) collects (exchange filings, XBRL results, prices, shareholding, events, orders, earnings calls, news) through that project's read-only `shared` views, adds what the open web says, and writes a report whose every factual claim links to the passage it rests on.

> **Not investment advice.** The reports are research inputs built from public filings and news, for information only. They make no recommendation to buy, sell or hold any security. Check anything that matters to you against the filings themselves.

## Status

Early. R0 and R1 are done: the project, its databases, and the evidence layer (shared records and web pages kept as sources, excerpts located in them, the research tools). The research loop and the report come in R2–R4; see [docs/design/design.md](docs/design/design.md).

| Step | What | Status |
| --- | --- | --- |
| R0 | Project, shared data contract check, own database and tables, design | done |
| R1 | Evidence: shared records and web pages kept as sources with excerpts; the research tools | done |
| R2 | The research loop: plan, collect, assess into findings, targeted follow-up, within a budget | next |
| R3 | Review (numbers, periods, dates, citations), the report in sections, the report page, export | |
| R4 | Evaluation: a fixed set of stocks, human-checked claims, coverage, cost and time per report | |

## How it fits with stock_news_analyzer

```
Postgres (localhost:5433)
├─ database sna            stock_news_analyzer's: collected data
│   └─ schema shared       read-only views, the contract (its docs/shared-data.md)
└─ database deep_research  this project's: runs, sources, excerpts, findings, reports
```

This project imports nothing from stock_news_analyzer. It reads the `shared` views as `research_reader`, a login that can read only those views, and checks the contract's version before it reads anything.

## Running it

You need stock_news_analyzer running (its database up to date: `make start` there), [uv](https://docs.astral.sh/uv/) and Python 3.13.

```
git clone https://github.com/a-ramakrishnan/stock_deep_research.git
cd stock_deep_research
cp .env.example .env        # add your Anthropic API key
uv sync
uv run sdr db init          # creates the deep_research database and its tables
uv run sdr health           # both databases and the key
uv run sdr company LT       # look a company up in the shared data
uv run sdr evidence LT      # collect its shared data into a run (free); prints the run id
uv run sdr sources 1        # what run 1 kept; `sdr source ID` shows one as it was read
uv run sdr evidence LT --web "Who are L&T's main competitors in EPC?"   # adds web pages (paid)
uv run streamlit run src/stock_deep_research/ui/app.py --server.port 8502
```

The Evidence page (in the sidebar) collects and shows the same. A `--web` question costs its tokens plus $0.01 a search, typically a few cents to about $0.30; a report will cost at most `REPORT_BUDGET_USD` (default $5).

## Development

```
uv run pytest               # tests that need Postgres skip when it isn't on localhost:5433
uv run ruff check . && uv run ruff format --check .
```

## Licence

MIT. See [LICENSE](LICENSE).
