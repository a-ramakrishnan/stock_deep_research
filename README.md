# stock_deep_research

A deep research report for any Indian listed stock: evidence first, every claim cited, conclusion up front. It reads the data [stock_news_analyzer](https://github.com/a-ramakrishnan/stock_news_analyzer) collects (exchange filings, XBRL results, prices, shareholding, events, orders, earnings calls, news) through that project's read-only `shared` views, adds what the open web says, and writes a report whose every factual claim links to the passage it rests on.

> **Not investment advice.** The reports are research inputs built from public filings and news, for information only. They make no recommendation to buy, sell or hold any security. Check anything that matters to you against the filings themselves.

## Status

Early. Step R0 (this): the project, its two database connections, its own tables, the design. The research agent comes in R1–R4; see [docs/design/design.md](docs/design/design.md).

| Step | What | Status |
| --- | --- | --- |
| R0 | Project, shared data contract check, own database and tables, design | done |
| R1 | Evidence: shared records and web pages kept as sources with excerpts; the research tools | next |
| R2 | The research loop: plan, collect, assess into findings, targeted follow-up, within a budget | |
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
uv run streamlit run src/stock_deep_research/ui/app.py --server.port 8502
```

A report will cost at most `REPORT_BUDGET_USD` (default $5).

## Development

```
uv run pytest               # tests that need Postgres skip when it isn't on localhost:5433
uv run ruff check . && uv run ruff format --check .
```

## Licence

MIT. See [LICENSE](LICENSE).
