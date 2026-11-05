"""Settings, from the environment or `.env`."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # stock_news_analyzer's data, through its read-only `shared` views.
    shared_database_url: str = (
        "postgresql+psycopg://research_reader:research_reader@localhost:5433/sna"
    )
    # This project's own database: reports, findings, sources.
    database_url: str = "postgresql+psycopg://sna:sna@localhost:5433/deep_research"
    anthropic_api_key: str = ""
    research_model: str = "claude-sonnet-5-5"
    report_budget_usd: float = 5.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
