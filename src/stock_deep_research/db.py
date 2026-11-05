"""The two databases: stock_news_analyzer's `shared` views (read-only) and this project's own."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from stock_deep_research.config import get_settings

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def engine(url: str | None = None) -> Engine:
    """This project's database."""
    return create_engine(url or get_settings().database_url, pool_pre_ping=True)


def shared_engine(url: str | None = None) -> Engine:
    """stock_news_analyzer's data, read-only."""
    return create_engine(url or get_settings().shared_database_url, pool_pre_ping=True)


def create_database(url: str | None = None) -> bool:
    """Create this project's database on the server if it isn't there. True if created."""
    target = make_url(url or get_settings().database_url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :d"), {"d": target.database}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{target.database}"'))
            return not exists
    finally:
        admin.dispose()


def upgrade_database(url: str | None = None) -> None:
    """Apply this project's migrations up to the latest."""
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", (url or get_settings().database_url).replace("%", "%%"))
    command.upgrade(cfg, "head")
