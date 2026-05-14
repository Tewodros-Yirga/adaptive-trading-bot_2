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

    """CREATE TABLE IF NOT EXISTS users (
        id            SERIAL PRIMARY KEY,
        username      VARCHAR UNIQUE NOT NULL,
        password_hash VARCHAR NOT NULL,
        role          VARCHAR NOT NULL DEFAULT 'viewer',
        full_access   BOOLEAN DEFAULT FALSE,
        is_active     BOOLEAN DEFAULT TRUE,
        created_at    TIMESTAMP DEFAULT NOW()
    );""",

    # ── Tables from Alembic migration 003 ─────────────────────────────────
    """CREATE TABLE IF NOT EXISTS backtest_candidates (
        id                  SERIAL PRIMARY KEY,
        strategy_name       VARCHAR NOT NULL,
        params_json         JSON NOT NULL,
        search_context_json JSON NOT NULL,
        win_rate            FLOAT,
        roi_pct             FLOAT,
        profit_factor       FLOAT,
        composite_score     FLOAT,
        qualified           BOOLEAN NOT NULL DEFAULT FALSE,
        promoted            BOOLEAN NOT NULL DEFAULT FALSE,
        score_delta         FLOAT NOT NULL DEFAULT 0.0,
        backtest_result_id  INTEGER REFERENCES backtest_results(id) ON DELETE SET NULL,
        evaluated_at        TIMESTAMP NOT NULL DEFAULT NOW()
    );""",
    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_backtest_candidates_strategy_name')
        THEN CREATE INDEX ix_backtest_candidates_strategy_name ON backtest_candidates (strategy_name);
        END IF; END $$;""",
    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_backtest_candidates_strategy_score')
        THEN CREATE INDEX ix_backtest_candidates_strategy_score ON backtest_candidates (strategy_name, composite_score);
        END IF; END $$;""",
    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_backtest_candidates_strategy_qualified')
        THEN CREATE INDEX ix_backtest_candidates_strategy_qualified ON backtest_candidates (strategy_name, qualified);
        END IF; END $$;""",

    # ── Tables from Alembic migration 004 ─────────────────────────────────
    """CREATE TABLE IF NOT EXISTS ensemble_decisions (
        id                   SERIAL PRIMARY KEY,
        symbol               VARCHAR NOT NULL,
        timestamp            TIMESTAMP NOT NULL,
        resolved_direction   VARCHAR,
        resolved_confidence  FLOAT,
        trade_id             INTEGER REFERENCES trades(id),
        strategy_votes_json  JSON,
        final_entry          FLOAT,
        final_sl             FLOAT,
        final_tp1            FLOAT,
        final_tp2            FLOAT,
        final_tp3            FLOAT,
        final_tp4            FLOAT,
        news_bias            FLOAT,
        news_blocked         BOOLEAN DEFAULT FALSE,
        risk_blocked         BOOLEAN DEFAULT FALSE,
        block_reason         VARCHAR
    );""",

    # ── Tables from Alembic migration 005 ─────────────────────────────────
    """CREATE TABLE IF NOT EXISTS strategy_picker_decisions (
        id                          SERIAL PRIMARY KEY,
        symbol                      VARCHAR NOT NULL,
        timestamp                   TIMESTAMP NOT NULL,
        strategy_scores_json        JSON,
        selected_strategies_json    JSON,
        ensemble_weights_used_json  JSON,
        trade_id                    INTEGER REFERENCES trades(id),
        news_influence_json         JSON,
        picker_confidence           FLOAT,
        reasoning                   VARCHAR
    );""",
    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_strategy_picker_decisions_symbol')
        THEN CREATE INDEX ix_strategy_picker_decisions_symbol ON strategy_picker_decisions (symbol);
        END IF; END $$;""",
    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_strategy_picker_decisions_timestamp')
        THEN CREATE INDEX ix_strategy_picker_decisions_timestamp ON strategy_picker_decisions (timestamp);
        END IF; END $$;""",

    """CREATE TABLE IF NOT EXISTS picker_weight_history (
        id                   SERIAL PRIMARY KEY,
        trade_id             INTEGER NOT NULL REFERENCES trades(id),
        weights_before_json  JSON,
        weights_after_json   JSON,
        weight_deltas_json   JSON,
        trade_result         VARCHAR NOT NULL,
        updated_at           TIMESTAMP DEFAULT NOW()
    );""",
    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_picker_weight_history_trade_id')
        THEN CREATE INDEX ix_picker_weight_history_trade_id ON picker_weight_history (trade_id);
        END IF; END $$;""",

    # ── Tables from Alembic migration 006 ─────────────────────────────────
    """CREATE TABLE IF NOT EXISTS backtest_batches (
        id                   SERIAL PRIMARY KEY,
        batch_id             VARCHAR UNIQUE NOT NULL,
        strategy_names       JSON NOT NULL,
        shared_settings_json JSON,
        status               VARCHAR DEFAULT 'PENDING',
        cross_analysis_json  JSON,
        created_at           TIMESTAMP DEFAULT NOW(),
        completed_at         TIMESTAMP
    );""",
    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_backtest_batches_batch_id')
        THEN CREATE INDEX ix_backtest_batches_batch_id ON backtest_batches (batch_id);
        END IF; END $$;""",

    """CREATE TABLE IF NOT EXISTS strategy_pair_analyses (
        id                       SERIAL PRIMARY KEY,
        batch_id                 VARCHAR NOT NULL,
        strategy_names_json      JSON NOT NULL,
        combination_type         VARCHAR NOT NULL,
        combined_win_rate        FLOAT,
        combined_roi_pct         FLOAT,
        combined_profit_factor   FLOAT,
        combined_composite_score FLOAT,
        individual_scores_json   JSON,
        agreement_rate           FLOAT,
        disagreement_win_rate    FLOAT,
        correlation              FLOAT,
        synergy_score            FLOAT,
        recommended              BOOLEAN DEFAULT FALSE,
        analysis_json            JSON,
        computed_at              TIMESTAMP DEFAULT NOW()
    );""",
    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_strategy_pair_analyses_batch_id')
        THEN CREATE INDEX ix_strategy_pair_analyses_batch_id ON strategy_pair_analyses (batch_id);
        END IF; END $$;""",

    # ── Columns from Alembic migration 007 (extend backtest_results) ──────
    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='backtest_results' AND column_name='strategy_performance_timeline_json')
        THEN
            ALTER TABLE backtest_results ADD COLUMN strategy_performance_timeline_json JSON;
            ALTER TABLE backtest_results ADD COLUMN parameter_evolution_log_json JSON;
            ALTER TABLE backtest_results ADD COLUMN trade_log_json JSON;
            ALTER TABLE backtest_results ADD COLUMN monthly_breakdown_json JSON;
            ALTER TABLE backtest_results ADD COLUMN drawdown_periods_json JSON;
            ALTER TABLE backtest_results ADD COLUMN strategy_signals_log_json JSON;
            ALTER TABLE backtest_results ADD COLUMN batch_id VARCHAR;
        END IF; END $$;""",
    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_backtest_results_batch_id')
        THEN CREATE INDEX ix_backtest_results_batch_id ON backtest_results (batch_id);
        END IF; END $$;""",

    # ── Column from Alembic migration 008 (ohlcv_cache.source) ────────────
    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='ohlcv_cache' AND column_name='source')
        THEN ALTER TABLE ohlcv_cache ADD COLUMN source VARCHAR;
        END IF; END $$;""",

    # ── app_settings table (used by seed_default_settings) ────────────────
    """CREATE TABLE IF NOT EXISTS app_settings (
        id         SERIAL PRIMARY KEY,
        key        VARCHAR UNIQUE NOT NULL,
        value      TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT NOW()
    );""",
]


def run_startup_migrations() -> None:
    """Run all idempotent DDL. Call BEFORE importing any ORM models."""
    engine = _get_engine()
    with engine.begin() as conn:
        for stmt in _STMTS:
            conn.execute(text(stmt))
    engine.dispose()