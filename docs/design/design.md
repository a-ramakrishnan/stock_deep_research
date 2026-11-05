# Design: a deep research report for any stock

Version 0.2 · Nov 5, 2026 · @a-ramakrishnan

Adapted from the author's *Relay: Solution Design and Agent Methodology* (v0.1, 4 Nov 2026), a general research and monitoring platform inspired by Robyn's research results. This project keeps that document's method (evidence first, findings before prose, honest outcomes, evaluation with coverage and precision) and narrows its scope to one job: a complete research report on one Indian listed company. Robyn's internals are not known; nothing here claims to copy them.

## 1. What a report must let the reader answer

- What is the conclusion, and which evidence supports each claim behind it?
- What is known, what is inferred, and what could not be found?
- How does the company compare with its peers and with its own history?
- What would change the view, and when is the next chance to find out?
- What did the report cost, and which of its questions are still open?

It is a research input, not advice: no buy, sell or hold call, a disclaimer on every report. (In India, publishing recommendations needs SEBI Research Analyst registration.)

## 2. Scope

| In | Out (for now) |
| --- | --- |
| One company per report, by NSE symbol, ISIN or name | Portfolios, screens across stocks |
| stock_news_analyzer's data (filings and their text, XBRL results, balance sheets, prices, indices, shareholding, checked events, orders, earnings calls, promises, red flags, deals, news) | Paid data, broker research, paywalled news (a visible gap) |
| The open web for what the filings don't say: industry, competitors, regulation, management backgrounds, products | Logging in anywhere, writing anywhere |
| A report page, Markdown and PDF export | Slides, meeting notes (later) |
| Re-runs that say what changed since the last report | Continuous monitoring (stock_news_analyzer already does it) |

## 3. Architecture

Relay proposed Temporal, LangGraph, FastAPI, React and later Tauri. For a run of a few minutes on one laptop that is more machinery than the job needs, and two engines would both own the run's state. This project uses what the companion project already proved:

| Relay | Here | Why |
| --- | --- | --- |
| Temporal (lifecycle, retries, schedules) | A `runs` table with a status and a decision log; one run at a time | A report run is minutes long; a failed run is re-run, with completed sources kept |
| LangGraph (research state) | A plain Python loop with explicit stages, the Anthropic SDK's tool use | Fewer moving parts; the stages are the design below |
| FastAPI + React | Streamlit, as stock_news_analyzer | One language; enough for a report reader |
| Own search adapters | Claude's web search and web fetch tools | Less code; every fetched page still kept as a source |
| PostgreSQL | PostgreSQL: the `shared` views (read-only) and an own `deep_research` database | Shared data without shared code |

Execution is at least once: a run that dies part-way is marked failed and can be re-run; nothing is published from it.

## 4. Data

Own tables (migration 0001):

| Table | Holds |
| --- | --- |
| `runs` | The request, the company, status (queued, running, succeeded, partial, failed, cancelled), model, budget and cost, tool calls, the plan, the shared contract version read |
| `run_events` | The decision record: per stage, the question, the tool, what was found, why it stopped; cost per step. Not the model's hidden reasoning |
| `sources` | Everything read, as it was: a shared record (`shared.results:LT:2026-09-30:3m:consolidated`, its dataset and fields) or a web page (URL, title, publisher, published and retrieved times, content hash, text, retrieval status: ok, listed, blocked, failed, unreadable); one per ref in a run (migration 0002) |
| `excerpts` | The passages relied on, with their position in the source's text (none when the passage isn't found: such a citation fails the review) |
| `findings` | Atomic claims: section, the question they answer, the claim, fact or interpretation, support (supported, conflicting, insufficient, inferred), excerpts, the period or date they concern, limitations |
| `reports` | Versions of a company's report: outcome (validated, validated with limitations, rejected), sections, coverage per question, Markdown |

Invariants, from Relay:

1. A fact cannot enter a report without at least one excerpt that exists in its source.
2. Publication, retrieval and event dates stay apart; every number names its period.
3. Missing or blocked evidence is a recorded gap, never filled with plausible text.
4. Every report names its run, its sources' dates and the shared contract version.

### Evidence (R1)

**Shared records.** Each dataset of the shared views (company, peers, results, balance sheets, price summary, month-end prices, index valuations, corporate actions, shareholding, filings with their text, relevant news, checked events, orders, order books, earnings calls with their points, promises, open red flags, deals) is read newest first to a default depth and kept as sources. A record is one source with a stable ref (`shared.results:LT:2026-09-30:3m:consolidated`, `shared.documents:11`), its fields as read (`data`) and a text of one `field: value` line per field, so a number can be traced to its line; a series (prices, index valuations, deals, corporate actions, peers) is one source with a line per row. Reading the same ref twice in a run keeps the first reading.

**Research tools** (for R2's loop): `shared_data(dataset, limit)`, `search_documents(query)` (a snippet per hit, nothing kept) and `read_document(document_id)`; answers label each source `[S<id>]`.

**The web.** Claude's web search and web fetch tools (`web_search_20260209`, `web_fetch_20260209`; at most 5 searches and 6 fetches a question, a fetched page capped at 30,000 tokens). Every fetched page is kept with its URL, title, retrieval time, hash and text; a search result seen but not fetched is `listed` (no text, so it can't be cited); a page that couldn't be fetched is `blocked` or `failed`; a PDF fetched is `unreadable` (no text kept). The model's answer is a lead, not evidence. Cost per call is computed from its usage (list prices; $0.01 a search) and added to the run.

**Excerpts.** A quote is located in its source's text exactly, then with whitespace and case relaxed; not found, it is kept with no position and fails the review.

## 5. The report

Sections, each with its mandatory questions:

1. **Summary:** the conclusion in a line, three stances, what would change the view.
2. **Business:** what it sells, to whom, segments and their share.
3. **Industry and competition:** market, position, peers, what drives demand.
4. **Management and governance:** who runs it, changes, promises kept and missed, related parties and red flags.
5. **Financials:** five years and eight quarters of revenue, margins, profit, EPS; comparable periods only.
6. **Balance sheet and cash:** debt, cash, working capital, cash from operations against profit, capex.
7. **Valuation:** P/E and others against its own history, peers and the index; bear, base and bull ranges with every assumption stated and sourced (from filings, or marked as an assumption). No price target, no recommendation.
8. **Catalysts and what to watch:** dated events ahead.
9. **Risks:** each tied to evidence.
10. **Sources and coverage:** every source with dates; each question answered, partly answered or open.

## 6. The research loop (R2)

1. **Plan:** the questions above plus any the request adds, mandatory ones marked; an allowance of tool calls and dollars per stage.
2. **Collect:** the shared data first (cheap, exact, dated), then the web for the questions it can't answer. Every read becomes a `Source`.
3. **Assess:** turn what was read into findings, each with excerpts and a support label; record what is missing.
4. **Follow up:** only on a named open question, while the budget allows.
5. **Write:** the sections from the findings; interpretation labelled as such.
6. **Review:** deterministic checks first (every number in the text is in a cited excerpt or derived by code from them; periods and units match; dates in order; every citation's excerpt is found in its source), then one model review of support and coverage; one repair pass.
7. **Publish:** validated, validated with limitations (gaps shown), or rejected; partial if the budget ran out with useful findings.

**Budget:** `REPORT_BUDGET_USD` (default $5) is a hard ceiling: before each call the estimated cost is reserved, after it the actual cost recorded; the run stops and publishes what it has (partial) before it would cross the ceiling. Default model: Sonnet; configurable.

## 7. Evaluation (R4)

A fixed set of about ten stocks across sectors (a bank, an IT exporter, an engineering contractor, a consumer company, a small cap with thin coverage), with a held-out part:

| Measure | How |
| --- | --- |
| Numeric accuracy | Every number checked by code against the filings (shared data) |
| Factual support | A sample of claims checked by a person against their excerpts |
| Coverage | Mandatory questions answered, partly answered, open |
| Honesty | Gaps reported where evidence is missing, not filled |
| Cost and time | Per report, median and worst |

Compared with a single-call baseline over the same data. Results published with sample sizes and known failure classes.

## 8. Steps

| Step | Deliverable | Done when |
| --- | --- | --- |
| R0 | Project, connections, contract check, own tables, design | `sdr health` passes on the Mac |
| R1 | Sources and excerpts; tools over the shared data; web search and fetch kept as sources | A company's evidence can be collected and inspected |
| R2 | The loop: plan, collect, assess, follow up, budget | A run produces findings with excerpts within $5 |
| R3 | Review, the report, the report page, export | A report passes the review or shows its gaps |
| R4 | Evaluation set and results | Results published in docs |
