"""Reading stock_news_analyzer's data through its `shared` views (its docs/shared-data.md).

This project depends on those views' columns only, never on that project's code or tables.
The contract's major version must match: a 2.x contract may have renamed or removed columns.
"""

from dataclasses import dataclass

from sqlalchemy import Engine, text

SUPPORTED_MAJOR = 1


class ContractError(RuntimeError):
    """The shared views are missing, or their contract version isn't one this code reads."""


def contract_version(shared: Engine) -> str:
    try:
        with shared.connect() as conn:
            return conn.execute(text("SELECT version FROM shared.contract")).scalar_one()
    except Exception as exc:  # no schema, no rights, no server: all mean "can't read it"
        raise ContractError(
            "Can't read shared.contract: is stock_news_analyzer up to date (migration 0030) "
            "and SHARED_DATABASE_URL right?"
        ) from exc


def check_contract(shared: Engine) -> str:
    version = contract_version(shared)
    major = int(version.split(".")[0])
    if major != SUPPORTED_MAJOR:
        raise ContractError(f"shared contract {version}: this code reads {SUPPORTED_MAJOR}.x")
    return version


@dataclass
class Company:
    isin: str
    nse_symbol: str | None
    name: str
    industry: str | None
    owned: bool
    on_watchlist: bool


def find_company(shared: Engine, query: str) -> Company | None:
    """A company by NSE symbol or ISIN (exact), else by name (the closest single match)."""
    q = query.strip()
    with shared.connect() as conn:
        row = conn.execute(
            text(
                "SELECT isin, nse_symbol, name, industry, owned, on_watchlist "
                "FROM shared.companies WHERE upper(nse_symbol) = upper(:q) OR isin = upper(:q) "
                "LIMIT 1"
            ),
            {"q": q},
        ).first()
        if row is None:
            rows = conn.execute(
                text(
                    "SELECT isin, nse_symbol, name, industry, owned, on_watchlist "
                    "FROM shared.companies WHERE name ILIKE :like ORDER BY length(name) LIMIT 2"
                ),
                {"like": f"%{q}%"},
            ).all()
            row = rows[0] if len(rows) == 1 else None
    return Company(*row) if row else None


def followed_companies(shared: Engine) -> list[Company]:
    """The companies stock_news_analyzer follows (its watchlist), owned first."""
    with shared.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT isin, nse_symbol, name, industry, owned, on_watchlist "
                "FROM shared.companies WHERE on_watchlist ORDER BY owned DESC, nse_symbol"
            )
        ).all()
    return [Company(*r) for r in rows]
