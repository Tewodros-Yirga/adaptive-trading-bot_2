"""add strategy columns and new tables

Revision ID: 001_strategy_schema
Revises:
Create Date: 2026-01-01 00:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "001_strategy_schema"
down_revision = None
branch_labels = None
depends_on = None


def _col_exists(table: str, col: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name=:t AND column_name=:c"
    ), {"t": table, "c": col})
    return result.fetchone() is not None


def _table_exists(table: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.tables WHERE table_name=:t"
    ), {"t": table})
    return result.fetchone() is not None


def upgrade() -> None:
    # ── parameter_versions ────────────────────────────────────────────────
    if not _col_exists("parameter_versions", "strategy_name"):
        op.add_column("parameter_versions",
                      sa.Column("strategy_name", sa.String(), nullable=True))
        op.execute("UPDATE parameter_versions SET strategy_name = 'DTC' WHERE strategy_name IS NULL")

    # ── trades ────────────────────────────────────────────────────────────
    if not _col_exists("trades", "strategy_name"):
        op.add_column("trades",
                      sa.Column("strategy_name", sa.String(), nullable=True))
        op.execute("UPDATE trades SET strategy_name = 'DTC' WHERE strategy_name IS NULL")

    # ── adaptation_logs ───────────────────────────────────────────────────
    if not _col_exists("adaptation_logs", "strategy_name"):
        op.add_column("adaptation_logs",
                      sa.Column("strategy_name", sa.String(), nullable=True))
        op.execute("UPDATE adaptation_logs SET strategy_name = 'DTC' WHERE strategy_name IS NULL")

    # ── strategies table ──────────────────────────────────────────────────
    if not _table_exists("strategies"):
        op.create_table(
            "strategies",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(), unique=True, nullable=False),
            sa.Column("display_name", sa.String(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("is_active", sa.Boolean(), default=False),
            sa.Column("is_live", sa.Boolean(), default=False),
            sa.Column("params_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        )

    # ── shadow_signals ────────────────────────────────────────────────────
    if not _table_exists("shadow_signals"):
        op.create_table(
            "shadow_signals",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("strategy_name", sa.String(), nullable=False),
            sa.Column("symbol", sa.String(), nullable=False),
            sa.Column("direction", sa.String(), nullable=False),
            sa.Column("entry_price", sa.Float(), nullable=False),
            sa.Column("sl", sa.Float(), nullable=True),
            sa.Column("tp1", sa.Float(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("signal_time", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("would_have_resulted_in", sa.String(), nullable=True),
            sa.Column("actual_exit_price", sa.Float(), nullable=True),
            sa.Column("pnl_hypothetical", sa.Float(), nullable=True),
        )

    # ── backtest_results ──────────────────────────────────────────────────
    if not _table_exists("backtest_results"):
        op.create_table(
            "backtest_results",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("strategy_name", sa.String(), nullable=False),
            sa.Column("symbol", sa.String(), nullable=False),
            sa.Column("from_date", sa.String(), nullable=False),
            sa.Column("to_date", sa.String(), nullable=False),
            sa.Column("params_json", sa.Text(), nullable=False),
            sa.Column("initial_balance", sa.Float(), default=10000),
            sa.Column("leverage", sa.Integer(), default=100),
            sa.Column("risk_per_trade_pct", sa.Float(), default=1.0),
            sa.Column("metrics_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("equity_curve_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("status", sa.String(), default="PENDING"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
        )

    # ── ohlcv_cache ───────────────────────────────────────────────────────
    if not _table_exists("ohlcv_cache"):
        op.create_table(
            "ohlcv_cache",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("symbol", sa.String(), nullable=False),
            sa.Column("interval", sa.String(), nullable=False),
            sa.Column("from_date", sa.String(), nullable=False),
            sa.Column("to_date", sa.String(), nullable=False),
            sa.Column("data_json", sa.Text(), nullable=False),
            sa.Column("fetched_at", sa.DateTime(), server_default=sa.func.now()),
        )

    # ── news_items ────────────────────────────────────────────────────────
    if not _table_exists("news_items"):
        op.create_table(
            "news_items",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("source", sa.String(), nullable=False),
            sa.Column("headline", sa.Text(), nullable=False),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("url", sa.Text(), nullable=True),
            sa.Column("published_at", sa.DateTime(), nullable=True),
            sa.Column("symbols_mentioned", sa.Text(), default="[]"),
            sa.Column("raw_sentiment_score", sa.Float(), nullable=True),
            sa.Column("ai_sentiment_score", sa.Float(), nullable=True),
            sa.Column("ai_sentiment_label", sa.String(), nullable=True),
            sa.Column("ai_confidence", sa.Float(), nullable=True),
            sa.Column("market_impact_predicted", sa.Float(), nullable=True),
            sa.Column("market_impact_actual", sa.Float(), nullable=True),
            sa.Column("impact_learning_weight", sa.Float(), default=1.0),
            sa.Column("fetched_at", sa.DateTime(), server_default=sa.func.now()),
        )


def downgrade() -> None:
    # Remove added columns
    for table, col in [
        ("adaptation_logs", "strategy_name"),
        ("trades", "strategy_name"),
        ("parameter_versions", "strategy_name"),
    ]:
        if _col_exists(table, col):
            op.drop_column(table, col)

    for table in ["news_items", "ohlcv_cache", "backtest_results", "shadow_signals", "strategies"]:
        if _table_exists(table):
            op.drop_table(table)
