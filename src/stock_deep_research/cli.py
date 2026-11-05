"""`sdr`: the command line."""

from datetime import UTC, datetime

import typer

from stock_deep_research import __version__
from stock_deep_research.config import get_settings

app = typer.Typer(help="Deep research reports for Indian listed stocks.", no_args_is_help=True)
db_app = typer.Typer(help="This project's own database.", no_args_is_help=True)
app.add_typer(db_app, name="db")


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


@app.command()
def health() -> None:
    """Check both databases: the shared views (and their contract version) and this
    project's own, and whether an API key is set."""
    from sqlalchemy import text

    from stock_deep_research import db, shared

    ok = True
    try:
        version = shared.check_contract(db.shared_engine())
        typer.echo(f"shared data   ok, contract {version}")
    except shared.ContractError as exc:
        ok = False
        typer.echo(f"shared data   FAILED: {exc}")
    try:
        with db.engine().connect() as conn:
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        typer.echo(f"own database  ok, migration {rev}")
    except Exception as exc:  # report and carry on: this is a health check
        ok = False
        typer.echo(f"own database  FAILED: {type(exc).__name__}; run `sdr db init`")
    key = "set" if get_settings().anthropic_api_key else "not set (reports can't be written)"
    typer.echo(f"API key       {key}")
    if not ok:
        raise typer.Exit(1)


@db_app.command("init")
def db_init() -> None:
    """Create this project's database if needed and bring it up to date."""
    from stock_deep_research import db

    created = db.create_database()
    db.upgrade_database()
    typer.echo(("Created and migrated" if created else "Migrated") + " the research database.")


@app.command()
def company(query: str) -> None:
    """Look a company up in the shared data: NSE symbol, ISIN or part of its name."""
    from stock_deep_research import db, shared

    found = shared.find_company(db.shared_engine(), query)
    if found is None:
        typer.echo(f"No single company matches '{query}'.")
        raise typer.Exit(1)
    flags = ", ".join(
        f for f, on in (("owned", found.owned), ("followed", found.on_watchlist)) if on
    )
    typer.echo(
        f"{found.nse_symbol or '-'}  {found.isin}  {found.name}  {found.industry or ''}"
        + (f"  ({flags})" if flags else "")
    )


@app.command()
def evidence(
    query: str,
    web: list[str] = typer.Option(
        [], "--web", help="A question for the open web (costs money); repeat for more."
    ),
) -> None:
    """Collect a company's evidence into a new run: every dataset of the shared data, and the
    open web for any --web questions. Lists what was kept."""
    from sqlalchemy.orm import Session

    from stock_deep_research import db, shared
    from stock_deep_research.evidence import Evidence, new_run

    settings = get_settings()
    shared_eng = db.shared_engine()
    version = shared.check_contract(shared_eng)
    found = shared.find_company(shared_eng, query)
    if found is None:
        typer.echo(f"No single company matches '{query}'.")
        raise typer.Exit(1)
    if web and not settings.anthropic_api_key:
        typer.echo("--web needs ANTHROPIC_API_KEY in .env.")
        raise typer.Exit(1)
    with Session(db.engine()) as session:
        run = new_run(
            session,
            found,
            "evidence" + "".join(f"; web: {q}" for q in web),
            settings.research_model,
            settings.report_budget_usd,
            version,
        )
        ev = Evidence(session, run, shared_eng, found)
        counts = ev.collect()
        ev.event("collect", f"shared data: {sum(counts.values())} sources")
        session.commit()
        typer.echo(f"Run {run.id}: {found.nse_symbol or found.isin} {found.name}")
        for name, n in counts.items():
            typer.echo(f"  {name:<18}{n}")
        if web:
            from stock_deep_research.web import ask_web

            for q in web:
                answer = ask_web(ev, q)
                session.commit()
                pages = sum(1 for s in answer.sources if s.retrieval_status == "ok")
                typer.echo(
                    f"  web: {q}\n    {answer.searches} searches, {answer.fetches} fetches, "
                    f"{pages} pages kept, ${answer.cost_usd}"
                )
        run.status = "succeeded"
        run.finished_at = datetime.now(UTC)
        session.commit()
        typer.echo(f"Cost ${run.cost_usd}. See them: sdr sources {run.id}")


@app.command()
def sources(
    run_id: int,
    dataset: str = typer.Option(None, help="Only this dataset (results, filings, web, ...)."),
) -> None:
    """List a run's sources: id, dataset, status, date, title."""
    from sqlalchemy.orm import Session

    from stock_deep_research import db
    from stock_deep_research.evidence import sources as run_sources

    with Session(db.engine()) as session:
        for s in run_sources(session, run_id):
            if dataset and s.dataset != dataset:
                continue
            when = s.published_at.date().isoformat() if s.published_at else "-"
            typer.echo(
                f"{s.id:>6}  {s.dataset or s.kind:<18}{s.retrieval_status:<11}{when:<11}"
                f"{(s.title or s.ref)[:70]}"
            )


@app.command()
def source(source_id: int) -> None:
    """Show one source as it was read: ref, dates, hash, text."""
    from sqlalchemy.orm import Session

    from stock_deep_research import db
    from stock_deep_research.models import Source

    with Session(db.engine()) as session:
        s = session.get(Source, source_id)
        if s is None:
            typer.echo(f"No source {source_id}.")
            raise typer.Exit(1)
        typer.echo(f"{s.title or ''}\nref        {s.ref}\nstatus     {s.retrieval_status}")
        if s.publisher:
            typer.echo(f"publisher  {s.publisher}")
        if s.published_at:
            typer.echo(f"published  {s.published_at.isoformat()}")
        typer.echo(f"retrieved  {s.retrieved_at.isoformat()}\nsha256     {s.content_hash}\n")
        typer.echo(s.text or "(no text kept)")


@app.command()
def research(
    query: str,
    question: list[str] = typer.Option(
        [], "--question", "-q", help="A question of your own to add; repeat for more."
    ),
    budget: float = typer.Option(None, help="Dollars for the whole report (default .env's)."),
    report: bool = typer.Option(True, help="Write, review and publish the report afterwards."),
) -> None:
    """Research a company and write its report: collect the shared data, assess it into
    findings with quoted passages, follow up open questions with the research tools and the
    web (at most 70% of the budget), then write, review and publish the report."""
    from sqlalchemy.orm import Session

    from stock_deep_research import db, shared
    from stock_deep_research.research import research as run_research

    settings = get_settings()
    if not settings.anthropic_api_key:
        typer.echo("Research needs ANTHROPIC_API_KEY in .env.")
        raise typer.Exit(1)
    shared_eng = db.shared_engine()
    version = shared.check_contract(shared_eng)
    found = shared.find_company(shared_eng, query)
    if found is None:
        typer.echo(f"No single company matches '{query}'.")
        raise typer.Exit(1)
    with Session(db.engine()) as session:
        run = run_research(
            session,
            shared_eng,
            found,
            request="report" + "".join(f"; {q}" for q in question),
            questions=question,
            budget_usd=budget,
            contract_version=version,
            progress=lambda step: typer.echo(f"  {step}"),
        )
        _print_findings(session, run.id)
        if report and run.status in ("succeeded", "partial"):
            _write(session, run, found.name)


def _write(session, run, name: str) -> None:
    from stock_deep_research.report import OUTCOMES, write_report

    made = write_report(session, run, name, progress=lambda step: typer.echo(f"  {step}"))
    typer.echo(
        f"\nReport version {made.version}: {OUTCOMES[made.outcome]}. Total cost ${run.cost_usd} "
        f"of ${run.budget_usd}. Read it: sdr export {run.symbol or run.isin}"
    )


@app.command("report")
def report_cmd(run_id: int) -> None:
    """Write, review and publish a report from a research run already done (a new version)."""
    from sqlalchemy.orm import Session

    from stock_deep_research import db, shared
    from stock_deep_research.models import Run

    if not get_settings().anthropic_api_key:
        typer.echo("Writing a report needs ANTHROPIC_API_KEY in .env.")
        raise typer.Exit(1)
    with Session(db.engine()) as session:
        run = session.get(Run, run_id)
        if run is None or not (run.plan or {}).get("questions"):
            typer.echo(f"No research run {run_id}.")
            raise typer.Exit(1)
        found = shared.find_company(db.shared_engine(), run.isin)
        _write(session, run, found.name if found else run.symbol or run.isin)


@app.command()
def export(
    query: str,
    version: int = typer.Option(None, help="A version (default the latest)."),
    out: str = typer.Option(None, help="Write the Markdown to this file instead of printing."),
) -> None:
    """A company's report as Markdown: by symbol or ISIN, the latest version or one named."""
    from pathlib import Path

    from sqlalchemy.orm import Session

    from stock_deep_research import db, shared
    from stock_deep_research.report import versions

    found = shared.find_company(db.shared_engine(), query)
    isin = found.isin if found else query.upper()
    with Session(db.engine()) as session:
        reports = versions(session, isin)
        chosen = next((r for r in reports if version in (None, r.version)), None)
        if chosen is None:
            typer.echo(f"No report for {query}" + (f" version {version}." if version else "."))
            raise typer.Exit(1)
        if out:
            Path(out).write_text(chosen.markdown)
            typer.echo(f"Wrote version {chosen.version} to {out}.")
        else:
            typer.echo(chosen.markdown)


@app.command("findings")
def findings_cmd(run_id: int) -> None:
    """A run's findings by section, with their support and quoted passages, and its coverage."""
    from sqlalchemy.orm import Session

    from stock_deep_research import db

    with Session(db.engine()) as session:
        _print_findings(session, run_id)


def _print_findings(session, run_id: int) -> None:
    from stock_deep_research.models import Excerpt, Run
    from stock_deep_research.research import findings

    run = session.get(Run, run_id)
    if run is None:
        typer.echo(f"No run {run_id}.")
        raise typer.Exit(1)
    section = None
    for f in findings(session, run_id):
        if f.section != section:
            section = f.section
            typer.echo(f"\n{section.upper()}")
        typer.echo(f"- [{f.support}, {f.claim_type}] {f.claim}")
        for ex in session.query(Excerpt).filter(Excerpt.id.in_(f.excerpt_ids or [])):
            mark = "" if ex.start_char is not None else "  (NOT FOUND in source)"
            typer.echo(f'    S{ex.source_id}: "{ex.quote[:160]}"{mark}')
        if f.limitations:
            typer.echo(f"    limits: {f.limitations}")
    cover = (run.plan or {}).get("coverage", {})
    if cover:
        typer.echo("\nCOVERAGE")
        for qid, state in cover.items():
            typer.echo(f"  {state:<9}{qid}")
    typer.echo(f"\nRun {run.id}: {run.status}, ${run.cost_usd} of ${run.budget_usd}")
