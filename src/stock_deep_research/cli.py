"""`sdr`: the command line."""

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
