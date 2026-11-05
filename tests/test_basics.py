import pytest
from sqlalchemy import create_engine, inspect, text
from typer.testing import CliRunner

from stock_deep_research import __version__, db, shared
from stock_deep_research.cli import app
from stock_deep_research.config import Settings

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0 and result.stdout.strip() == __version__


def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("REPORT_BUDGET_USD", raising=False)
    s = Settings(_env_file=None)
    assert s.report_budget_usd == 5.0
    assert "research_reader" in s.shared_database_url and s.database_url.endswith("/deep_research")


@pytest.mark.db
def test_own_database_is_created_and_migrated(research_url):
    tables = set(inspect(create_engine(research_url)).get_table_names())
    assert {"runs", "run_events", "sources", "excerpts", "findings", "reports"} <= tables
    assert db.create_database(research_url) is False  # already there


@pytest.mark.db
def test_shared_contract_and_companies(shared_url):
    eng = create_engine(shared_url)
    assert shared.check_contract(eng) == "1.0"
    assert shared.find_company(eng, "lt").name == "Larsen & Toubro Limited"
    assert shared.find_company(eng, "INE036D01028").nse_symbol == "KARURVYSYA"
    assert shared.find_company(eng, "Karur").isin == "INE036D01028"  # one name match
    assert shared.find_company(eng, "Limited") is None  # several: not a guess
    assert [c.nse_symbol for c in shared.followed_companies(eng)] == ["LT", "KARURVYSYA"]
    with eng.begin() as conn:
        conn.execute(
            text("CREATE OR REPLACE VIEW shared.contract AS SELECT '2.0'::text AS version")
        )
    with pytest.raises(shared.ContractError, match="reads 1.x"):
        shared.check_contract(eng)
    with eng.begin() as conn:
        conn.execute(
            text("CREATE OR REPLACE VIEW shared.contract AS SELECT '1.0'::text AS version")
        )


def test_missing_shared_data_is_a_clear_error():
    eng = create_engine("postgresql+psycopg://x:y@localhost:1/none")
    with pytest.raises(shared.ContractError, match="migration 0030"):
        shared.check_contract(eng)


@pytest.mark.db
def test_health_reports_both_databases(research_url, shared_url, monkeypatch):
    from stock_deep_research.config import get_settings

    monkeypatch.setenv("DATABASE_URL", research_url)
    monkeypatch.setenv("SHARED_DATABASE_URL", shared_url)
    get_settings.cache_clear()
    result = runner.invoke(app, ["health"])
    get_settings.cache_clear()
    assert result.exit_code == 0, result.stdout
    assert "shared data   ok, contract 1.0" in result.stdout
    assert "own database  ok, migration 0001" in result.stdout
