# R4: Agent evaluation and report data veracity

**Goal:** score any research run and audit the veracity of every claim and figure in its published report, at $0 extra model cost.

- **5-layer veracity audit** (`eval.audit_report`):
  1. **Layer 1 · Cryptographic source & quote offset (`excerpts.verify`):** checks that every cited excerpt has a valid `start_char` in `Source.text` and that `Source.content_hash` matches `sha256(Source.text)`.
  2. **Layer 2 · Figure-to-quote decimal matching (`review.unsupported_figures`):** traces every number written in each report paragraph to the decimal value inside the verbatim quote of a finding cited by that paragraph (`FigureTrace`), allowing standard rounding.
  3. **Layer 3 · Deterministic math provenance (`derived.py`):** categorises every figure in the report by its backing source (`shared` record, `derived` formula or `web` page).
  4. **Layer 4 · Basis, period & freshness consistency:** flags paragraphs that cite non-primary `standalone` results without stating `'standalone'`, checks whether `financials` cites the newest primary quarterly result collected in the run, and blocks future dates outside `catalysts`.
  5. **Layer 5 · Omission, contradiction & gap honesty:** flags collected `red_flags` sources or `conflicting` findings that were omitted from the published report.
- **Run scorecard** (`eval.score_run`): mandatory coverage, quote fidelity (`located / total`), supported facts, follow-up questions and sources read, paragraph survival vs. dropped, figure precision, stage-by-stage cost breakdown and cost per supported fact.
- **Adversarial veracity traps** (`eval.run_traps`): five deterministic traps seeding a fabricated quote, a hallucinated figure, a tampered source `content_hash`, a future date outside `catalysts`, and investment advice / price targets.
- **Running it:**
  - `sdr eval --traps` runs the 5-layer adversarial veracity traps
  - `sdr eval RUN` prints the run's scorecard and 5-layer report veracity audit
  - an **Evaluation & Veracity** page in the Streamlit app (`ui/app.py`) with a cross-run benchmark table, the 5-layer veracity guide and trap status, a figure-by-figure lineage table and a paragraph-by-paragraph claim audit

### Checked

Tests (`tests/test_eval.py`) cover:
- All five adversarial veracity traps and `sdr eval --traps`.
- Full figure-by-figure lineage tracing (`3,926.15` traced to `shared.results`, `10.0%` traced to `derived:LT:figures`) and excerpt `sha256` + offset integrity on a validated report.
- Detection of unlabelled `standalone` basis citations, stale primary quarters when a newer quarter was collected, omitted `red_flags` sources, and omitted `conflicting` findings.
