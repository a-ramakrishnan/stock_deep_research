# R3: The report

**Goal:** a report passes the review or shows its gaps.

- **Write** (`report.py`): one structured call over the run's findings, grouped by section and question with their coverage, support, period and quoted passages. It writes a one-line conclusion, three stances (the case for, the case against, what is unclear), what would change the view, and each section. Every paragraph is a fact, an interpretation or a gap, and cites findings as `[F12]`.
- **Checks** (`review.py`), for every paragraph:
  - citations exist, and a fact paragraph rests on a supported fact whose quote was found
  - every figure in the text is in a cited passage, allowing for rounding ("Rs 67,079 crore" matches 67079.45; "10.0%" matches 9.966), while labels, years, days of the month and small counts are not figures
  - nothing cited is dated after today outside "what to watch"
  - no buy, sell or hold call, price target or recommendation
- **Model review and repair:** one review of support and coverage, with each problem marked blocking or not. If the checks or blocking problems found anything, there is one rewrite with the problems listed, and the checks run again. Paragraphs that still fail are removed and named.
- **Outcome:** validated; validated with limitations (mandatory questions not fully answered, or paragraphs removed); or rejected (no draft, conclusion removed, nothing verified, or fewer than half the mandatory questions answered). A rejected report is still published with its coverage and the reason.
- **Rendering:** Markdown with the outcome, version, run, cost and shared contract version, and the disclaimer. Sources are numbered, each with publisher, published and retrieved dates, reference and the passages relied on. A coverage table and a Review section close it.
- **Running it:**
  - `sdr research LT` now writes the report at the end (`--no-report` to skip)
  - `sdr report RUN` writes a new version from a run's findings
  - `sdr export LT [--version N] [--out FILE]`
  - a Report page (company and version, the outcome badge, the report, a Markdown download; print the page for PDF) and a write-again button on the Findings page

### Checked

Tests cover the following:
- Which numbers count as figures, and the rounding rules.
- Each paragraph check.
- A clean draft validated: the writer's prompt carries findings and quotes; sources are numbered with their passages; no `[F..]` markers remain.
- A draft with an unsupported figure and a blocking review problem: repaired once, the advice paragraph that survived the repair removed, validated with limitations.
- A refused draft published as rejected, with coverage.
- Writing stopped when research had left too little budget.
- `sdr export`.

27 tests pass. On the synthetic database, a report written by a stand-in model from a stand-in research run renders on the Report page.

Not checked here: a real report (no API key in this environment). `sdr research LT` on the Mac is the check; its last line gives the outcome and the total cost.

## On the Mac

```
cd stock_deep_research
git pull
uv sync
uv run sdr research LT
uv run sdr export LT --out LT.md
make ui
```
