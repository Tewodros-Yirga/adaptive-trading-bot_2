"""
Safe startup migrations — self-contained.
Does NOT import from .config or .db; reads DATABASE_URL from env directly.
All DDL is idempotent — safe to run on every boot.
"""
import os
from sqlalchemy import create_engine, text


def _get_engine():
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    return create_engine(url, future=True, pool_pre_ping=True)


_STMTS = [
    # existing tables: add strategy_name column where missing
    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='parameter_versions' AND column_name='strategy_name')
        THEN ALTER TABLE parameter_versions ADD COLUMN strategy_name VARCHAR;
             UPDATE parameter_versions SET strategy_name='DTC' WHERE strategy_name IS NULL;
        END IF; END $$;""",

    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='trades' AND column_name='strategy_name')
        THEN ALTER TABLE trades ADD COLUMN strategy_name VARCHAR;
             UPDATE trades SET strategy_name='DTC' WHERE strategy_name IS NULL;
        END IF; END $$;""",

    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='adaptation_logs' AND column_name='strategy_name')
        THEN ALTER TABLE adaptation_logs ADD COLUMN strategy_name VARCHAR;
             UPDATE adaptation_logs SET strategy_name='DTC' WHERE strategy_name IS NULL;
        END IF; END $$;""",

    # new tables
    """CREATE TABLE IF NOT EXISTS strategies (
        id           SERIAL PRIMARY KEY,
        name         VARCHAR UNIQUE NOT NULL,
        display_name VARCHAR NOT NULL,
        description  TEXT,
        is_active    BOOLEAN DEFAULT FALSE,
        is_live      BOOLEAN DEFAULT FALSE,
        params_json  TEXT NOT NULL DEFAULT '{}',
        created_at   TIMESTAMP DEFAULT NOW(),
        updated_at   TIMESTAMP DEFAULT NOW()
    );""",

    """CREATE TABLE IF NOT EXISTS shadow_signals (
        id                     SERIAL PRIMARY KEY,
        strategy_name          VARCHAR NOT NULL,
        symbol                 VARCHAR NOT NULL,
        direction              VARCHAR NOT NULL,
        entry_price            FLOAT NOT NULL,
        sl                     FLOAT,
        tp1                    FLOAT,
        confidence             FLOAT,
        signal_time            TIMESTAMP DEFAULT NOW(),
        would_have_resulted_in VARCHAR,
        actual_exit_price      FLOAT,
        pnl_hypothetical       FLOAT
    );""",

    """CREATE TABLE IF NOT EXISTS backtest_results (
        id                 SERIAL PRIMARY KEY,
        strategy_name      VARCHAR NOT NULL,
        symbol             VARCHAR NOT NULL,
        from_date          VARCHAR NOT NULL,
        to_date            VARCHAR NOT NULL,
        params_json        TEXT NOT NULL DEFAULT '{}',
        initial_balance    FLOAT DEFAULT 10000,
        leverage           INTEGER DEFAULT 100,
        risk_per_trade_pct FLOAT DEFAULT 1.0,
        metrics_json       TEXT NOT NULL DEFAULT '{}',
        equity_curve_json  TEXT NOT NULL DEFAULT '[]',
        status             VARCHAR DEFAULT 'PENDING',
        created_at         TIMESTAMP DEFAULT NOW(),
        completed_at       TIMESTAMP
    );""",

    """CREATE TABLE IF NOT EXISTS ohlcv_cache (
        id         SERIAL PRIMARY KEY,
        symbol     VARCHAR NOT NULL,
        interval   VARCHAR NOT NULL,
        from_date  VARCHAR NOT NULL,
        to_date    VARCHAR NOT NULL,
        data_json  TEXT NOT NULL,
        fetched_at TIMESTAMP DEFAULT NOW()
    );""",

    """CREATE TABLE IF NOT EXISTS news_items (
        id                      SERIAL PRIMARY KEY,
        source                  VARCHAR NOT NULL,
        headline                TEXT NOT NULL,
        summary                 TEXT,
        url                     TEXT,
        published_at            TIMESTAMP,
        symbols_mentioned       TEXT DEFAULT '[]',
        raw_sentiment_score     FLOAT,
        ai_sentiment_score      FLOAT,
        ai_sentiment_label      VARCHAR,
        ai_confidence           FLOAT,
        market_impact_predicted FLOAT,
        market_impact_actual    FLOAT,
        impact_learning_weight  FLOAT DEFAULT 1.0,
        fetched_at              TIMESTAMP DEFAULT NOW()
    );""",
]


def run_startup_migrations() -> None:
    """Run all idempotent DDL. Call BEFORE importing any ORM models."""
    engine = _get_engine()
    with engine.begin() as conn:
        for stmt in _STMTS:
            conn.execute(text(stmt))
    engine.dispose()