# R0: The project

**Goal:** a separate project for deep research reports on Indian listed stocks, reading the data stock_news_analyzer collects without depending on its code.

- **Two connections:** stock_news_analyzer's `shared` views as `research_reader` (read-only; that project's migration 0030 and docs/shared-data.md), and this project's own `deep_research` database on the same Postgres server (`sdr db init` creates and migrates it).
- **The contract check:** `shared.contract` must be a 1.x version, or nothing is read (`shared.check_contract`); a missing schema or login is a clear error naming the migration to run.
- **Own tables** (migration 0001): runs and their decision log, sources, excerpts, findings, report versions (docs/design/design.md, section 4).
- **`sdr`:** `version`, `health` (both databases, the contract version, the key), `db init`, `company` (look a company up by symbol, ISIN or a name that matches only one).
- **A placeholder page** listing the companies stock_news_analyzer follows, with the disclaimer.
- **Design:** docs/design/design.md, the author's Relay design narrowed to one job, without Temporal, LangGraph or React (section 3 says why).

### Checked

Tests create a fresh research database and migrate it, stand in for the shared views (contract 1.0 and a companies view), look companies up by symbol, ISIN and name (one match only), refuse a 2.x contract, report a missing shared schema clearly, and run `sdr health` against both. 6 tests pass.

## On the Mac

After stock_news_analyzer's step 32 is merged and started (`git pull && make start` there):

```
git clone https://github.com/a-ramakrishnan/stock_deep_research.git
cd stock_deep_research
cp .env.example .env
uv sync
uv run sdr db init
uv run sdr health
uv run sdr company LT
```
