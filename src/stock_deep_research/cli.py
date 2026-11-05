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
