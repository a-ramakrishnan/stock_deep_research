import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

ADMIN = "postgresql+psycopg://sna:sna@localhost:5433/postgres"


@pytest.fixture(scope="session")
def admin_url():
    eng = create_engine(ADMIN, isolation_level="AUTOCOMMIT")
    try:
        with eng.connect():
            pass
    except OperationalError:
        pytest.skip("Postgres is not reachable on localhost:5433")
    finally:
        eng.dispose()
    return ADMIN


@pytest.fixture(scope="session")
def research_url(admin_url):
    """A fresh `deep_research_test` database, migrated."""
    from stock_deep_research import db

    url = make_url(admin_url).set(database="deep_research_test")
    eng = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(text('DROP DATABASE IF EXISTS "deep_research_test" WITH (FORCE)'))
    eng.dispose()
    rendered = url.render_as_string(hide_password=False)
    assert db.create_database(rendered) is True
    db.upgrade_database(rendered)
    return rendered


@pytest.fixture(scope="session")
def shared_url(admin_url):
    """A stand-in for stock_news_analyzer's shared views: a `shared_test` database with a
    `shared` schema holding the contract and a companies view, readable by a reader login."""
    eng = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(text('DROP DATABASE IF EXISTS "shared_test" WITH (FORCE)'))
        conn.execute(text('CREATE DATABASE "shared_test"'))
    eng.dispose()
    url = make_url(admin_url).set(database="shared_test")
    eng = create_engine(url, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(text("CREATE SCHEMA shared"))
        conn.execute(
            text(
                "CREATE TABLE companies (isin text, nse_symbol text, name text, industry text, "
                "owned bool, on_watchlist bool)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO companies VALUES "
                "('INE018A01030', 'LT', 'Larsen & Toubro Limited', 'Construction', true, true), "
                "('INE036D01028', 'KARURVYSYA', 'Karur Vysya Bank Limited', "
                "'Financial Services', false, true), "
                "('INE999Z01999', 'OTHER', 'Another Company Limited', null, false, false)"
            )
        )
        conn.execute(text("CREATE VIEW shared.companies AS SELECT * FROM companies"))
        conn.execute(text("CREATE VIEW shared.contract AS SELECT '1.0'::text AS version"))
    eng.dispose()
    return url.render_as_string(hide_password=False)
