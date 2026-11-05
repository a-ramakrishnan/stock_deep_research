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


# A stand-in for stock_news_analyzer's shared views (its migration 0030, contract 1.0): the same
# view names and columns, over plain tables, with a little data for L&T.
SHARED_TABLES = {
    "companies": "isin text, nse_symbol text, bse_code text, name text, industry text, "
    "market_cap_cr numeric, listed_on text, on_watchlist bool, owned bool, study_only bool, "
    "sector_index text",
    "peers": "isin text, peer_isin text",
    "documents": "document_id int, isin text, source text, doc_type text, title text, "
    "subject text, published_at timestamptz, first_seen_at timestamptz, url text, "
    "publisher text, attachment_url text, text_status text, text text",
    "news_triage": "document_id int, isin text, relevant bool, topic text, sentiment text, "
    "importance int, summary text, duplicate_of int",
    "prices_daily": "isin text, symbol text, series text, trade_date date, prev_close numeric, "
    "open numeric, high numeric, low numeric, close numeric, volume bigint, delivery_pct numeric",
    "index_daily": "index_name text, trade_date date, close numeric, pe numeric, pb numeric, "
    "div_yield numeric",
    "corporate_actions": "symbol text, isin text, ex_date date, subject text, kind text, "
    "price_factor numeric, dividend numeric",
    "results": "isin text, period text, period_end date, months int, basis text, "
    '"primary" bool, revenue_cr numeric, other_income_cr numeric, finance_costs_cr numeric, '
    "depreciation_cr numeric, pbt_cr numeric, pat_cr numeric, eps numeric, source_model text, "
    "document_id int",
    "balance_sheets": "isin text, period_end date, months int, basis text, equity_cr numeric, "
    "borrowings_cr numeric, cash_cr numeric, total_assets_cr numeric, cfo_cr numeric, "
    "capex_cr numeric, source text",
    "shareholding": "isin text, quarter_end date, filed_at timestamptz, promoter_pct numeric, "
    "public_pct numeric, fii_pct numeric, dii_pct numeric, mf_pct numeric, "
    "insurance_pct numeric, retail_pct numeric, pledged_pct numeric, shareholders int",
    "events": "event_id int, isin text, document_id int, published_at timestamptz, "
    "event_type text, headline text, event_date date, sentiment text, importance int, "
    "value_cr numeric, counterparty text, person text, role text, action text, quote text, "
    "quote_found bool, qa_status text, review text, shown bool",
    "orders": "order_id int, isin text, document_id int, announced_at timestamptz, kind text, "
    "client text, client_type text, segment text, geography text, value_cr numeric, "
    "execution_months int, description text",
    "order_books": "isin text, quarter text, period_end date, order_book_cr numeric, "
    "inflow_cr numeric, revenue_cr numeric",
    "concalls": "concall_id int, isin text, document_id int, quarter text, period_end date, "
    "call_date date, tone text, summary text",
    "concall_points": "concall_id int, isin text, category text, text text, sentiment text, "
    "quote text, quote_found bool",
    "promises": "promise_id int, isin text, statement text, metric text, target text, due text, "
    "due_date date, status text, status_note text",
    "red_flags": "flag_id int, isin text, kind text, severity text, title text, detail text, "
    "flagged_on date, document_id int, dismissed_at timestamptz",
    "deals": "trade_date date, isin text, symbol text, kind text, client text, side text, "
    "quantity bigint, price numeric",
}
LT = "INE018A01030"
SHARED_DATA = f"""
INSERT INTO src.companies VALUES
 ('{LT}', 'LT', '500510', 'Larsen & Toubro Limited', 'Construction', 520000, 'NSE', true, true,
  false, 'NIFTY INFRA'),
 ('INE036D01028', 'KARURVYSYA', '590003', 'Karur Vysya Bank Limited', 'Financial Services',
  18000, 'NSE', true, false, false, 'NIFTY BANK'),
 ('INE999Z01999', 'OTHER', null, 'Another Company Limited', null, null, 'BSE', false, false,
  false, null),
 ('INE00PEER001', 'KECI', null, 'KEC International Limited', 'Construction', 22000, 'NSE',
  false, false, false, null);
INSERT INTO src.peers VALUES ('{LT}', 'INE00PEER001');
INSERT INTO src.results VALUES
 ('{LT}', 'Q2 FY27', '2026-09-30', 3, 'consolidated', true, 67079.45, 1200, 900, 1100,
  5000.5, 3926.15, 28.56, 'xbrl', 11),
 ('{LT}', 'Q1 FY27', '2026-06-30', 3, 'consolidated', true, 63679, 1100, 880, 1080,
  4800, 3617.2, 26.3, 'xbrl', 10);
INSERT INTO src.balance_sheets VALUES
 ('{LT}', '2026-03-31', 12, 'consolidated', 98000, 120000, 25000, 400000, 18000, 4000,
  'xbrl-1.xml');
INSERT INTO src.prices_daily VALUES
 ('{LT}', 'LT', 'EQ', '2026-09-30', 3500, 3500, 3600, 3450, 3580, 1000000, 55),
 ('{LT}', 'LT', 'EQ', '2026-10-30', 3580, 3600, 3700, 3550, 3650, 900000, 52),
 ('{LT}', 'LT', 'EQ', '2026-10-31', 3650, 3650, 3950, 3600, 3921.5, 1200000, 58);
INSERT INTO src.index_daily VALUES
 ('NIFTY 50', current_date - 3, 25000, 22.1, 3.6, 1.2),
 ('NIFTY INFRA', current_date - 3, 9000, 25.4, 3.9, 1.1);
INSERT INTO src.shareholding VALUES
 ('{LT}', '2026-09-30', '2026-10-15 18:00+05:30', 0, 100, 19.4, 40.2, 20.1, 15.3, 22.0, 0,
  1600000);
INSERT INTO src.documents VALUES
 (10, '{LT}', 'nse', 'results', 'Financial results Q1 FY27', 'Results', '2026-07-25 16:00+05:30',
  '2026-07-25 16:05+05:30', null, null, 'https://nse/q1.pdf', 'ok',
  'Revenue from operations for the quarter was Rs 63,679 crore.'),
 (11, '{LT}', 'bse', 'results', 'Financial results Q2 FY27', 'Results', '2026-10-28 16:00+05:30',
  '2026-10-28 16:05+05:30', null, null, 'https://bse/q2.pdf', 'ok',
  'Revenue from operations for the quarter was Rs 67,079 crore,
up 10% on the year. Order inflow was strong in the Middle East.'),
 (12, '{LT}', 'nse', 'board_meeting', 'Board meeting intimation', null,
  '2026-10-01 10:00+05:30', '2026-10-01 10:05+05:30', null, null, null, 'empty', null),
 (20, '{LT}', 'news:et', 'news', 'L&T wins mega order in Saudi Arabia', null,
  '2026-10-20 09:00+05:30', '2026-10-20 09:10+05:30', 'https://et/lt-order', 'Economic Times',
  null, 'ok', 'Larsen & Toubro has won a mega order for a gas project.'),
 (21, '{LT}', 'news:et', 'news', 'Unrelated market wrap', null, '2026-10-21 09:00+05:30',
  '2026-10-21 09:10+05:30', 'https://et/wrap', 'Economic Times', null, 'ok', 'Markets fell.');
INSERT INTO src.news_triage VALUES
 (20, '{LT}', true, 'orders', 'positive', 5, 'L&T won a mega order (Rs 10,000-15,000 crore).',
  null),
 (21, '{LT}', false, 'market', 'neutral', 1, 'Market wrap.', null);
INSERT INTO src.events VALUES
 (1, '{LT}', 11, '2026-10-28 16:00+05:30', 'results', 'Q2 revenue up 10%', '2026-09-30',
  'positive', 4, null, null, null, null, null, 'Revenue from operations', true, 'passed', null,
  true),
 (2, '{LT}', 11, '2026-10-28 16:00+05:30', 'results', 'Rejected repeat', '2026-09-30',
  'positive', 4, null, null, null, null, null, 'x', false, 'failed', null, false);
INSERT INTO src.orders VALUES
 (1, '{LT}', 20, '2026-10-20 09:00+05:30', 'mega', 'Saudi Aramco', 'private', 'Hydrocarbon',
  'Middle East', 12500, 36, 'Gas project');
INSERT INTO src.order_books VALUES ('{LT}', 'Q2 FY27', '2026-09-30', 650000, 110000, 67079);
INSERT INTO src.concalls VALUES
 (1, '{LT}', 11, 'Q2 FY27', '2026-09-30', '2026-10-29', 'confident', 'Guidance kept.');
INSERT INTO src.concall_points VALUES
 (1, '{LT}', 'guidance', 'Revenue growth of 15% kept', 'positive', 'we retain 15%', true);
INSERT INTO src.promises VALUES
 (1, '{LT}', 'Revenue growth of 15% in FY27', 'revenue growth', '15%', 'FY27', '2027-03-31',
  'open', null);
INSERT INTO src.red_flags VALUES
 (1, '{LT}', 'pledge', 'low', 'Dismissed flag', 'x', '2026-01-01', null, '2026-02-01'),
 (2, '{LT}', 'auditor', 'medium', 'Auditor qualification', 'Emphasis of matter', '2026-05-20',
  null, null);
INSERT INTO src.deals VALUES
 ('2026-10-10', '{LT}', 'LT', 'block', 'A Fund', 'buy', 100000, 3700);
"""


@pytest.fixture(scope="session")
def shared_url(admin_url):
    """A `shared_test` database standing in for stock_news_analyzer's shared views."""
    eng = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(text('DROP DATABASE IF EXISTS "shared_test" WITH (FORCE)'))
        conn.execute(text('CREATE DATABASE "shared_test"'))
    eng.dispose()
    url = make_url(admin_url).set(database="shared_test")
    eng = create_engine(url, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(text("CREATE SCHEMA shared"))
        conn.execute(text("CREATE SCHEMA src"))
        for name, columns in SHARED_TABLES.items():
            conn.execute(text(f"CREATE TABLE src.{name} ({columns})"))
            conn.execute(text(f"CREATE VIEW shared.{name} AS SELECT * FROM src.{name}"))
        conn.execute(text("CREATE VIEW shared.contract AS SELECT '1.0'::text AS version"))
        conn.exec_driver_sql(SHARED_DATA.replace("%", "%%"))
    eng.dispose()
    return url.render_as_string(hide_password=False)
