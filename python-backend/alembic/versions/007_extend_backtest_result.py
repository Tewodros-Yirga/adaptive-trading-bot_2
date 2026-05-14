"""extend backtest_results with detailed report columns

Adds per-trade log, monthly breakdown, parameter evolution log, drawdown periods,
strategy signals log, strategy performance timeline, and batch_id linkage.

Revision ID: 007_extend_backtest_result
Revises: 006_add_pair_analysis_tables
Create Date: 2026-01-06 00:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "007_extend_backtest_result"
down_revision = "006_add_pair_analysis_tables"
branch_labels = None
depends_on = None


def _table_exists(table: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.tables WHERE table_name = :t"
    ), {"t": table})
    return result.fetchone() is not None


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = :t AND column_name = :c"
    ), {"t": table, "c": column})
    return result.fetchone() is not None


def upgrade() -> None:
    if not _table_exists("backtest_results"):
        return  # Nothing to extend; base table must exist first

    # Each add_column is guarded so re-running the migration is idempotent.

    if not _column_exists("backtest_results", "strategy_performance_timeline_json"):
        op.add_column(
            "backtest_results",
            sa.Column("strategy_performance_timeline_json", sa.JSON(), nullable=True),
        )

    if not _column_exists("backtest_results", "parameter_evolution_log_json"):
        op.add_column(
            "backtest_results",
            # JSON shape: {"adaptation_events": [{after_trade_index, timestamp,
            #   win_rate_at_time, profit_factor_at_time, old_params, new_params,
            #   param_deltas, composite_score_before, composite_score_after}]}
            sa.Column("parameter_evolution_log_json", sa.JSON(), nullable=True),
        )

    if not _column_exists("backtest_results", "trade_log_json"):
        op.add_column(
            "backtest_results",
            # JSON list of per-trade detail dicts (see PerTradeDetail schema).
            # Each entry: {trade_index, opened_at, closed_at, symbol, direction,
            #   entry_price, exit_price, stop_loss, take_profit_1, lot_size, pnl,
            #   result, exit_reason, duration_minutes, atr_at_entry,
            #   params_version_at_open, strategy_signals, news_bias_at_open,
            #   market_context: {session, day_of_week, was_high_volatility}}
            sa.Column("trade_log_json", sa.JSON(), nullable=True),
        )

    if not _column_exists("backtest_results", "monthly_breakdown_json"):
        op.add_column(
            "backtest_results",
            # JSON dict keyed by "YYYY-MM": {wins, losses, pnl}
            sa.Column("monthly_breakdown_json", sa.JSON(), nullable=True),
        )

    if not _column_exists("backtest_results", "drawdown_periods_json"):
        op.add_column(
            "backtest_results",
            # JSON list: [{start_date, end_date, peak_equity, trough_equity, drawdown_pct}]
            sa.Column("drawdown_periods_json", sa.JSON(), nullable=True),
        )

    if not _column_exists("backtest_results", "strategy_signals_log_json"):
        op.add_column(
            "backtest_results",
            # JSON list of per-bar signal events for replay/debugging
            sa.Column("strategy_signals_log_json", sa.JSON(), nullable=True),
        )

    if not _column_exists("backtest_results", "batch_id"):
        op.add_column(
            "backtest_results",
            # Links this result to a BacktestBatch record
            sa.Column("batch_id", sa.String(), nullable=True),
        )
        # Index for efficient batch → results lookups
        op.create_index(
            "ix_backtest_results_batch_id",
            "backtest_results",
            ["batch_id"],
        )


def downgrade() -> None:
    if not _table_exists("backtest_results"):
        return

    # Drop index first if it exists, then columns
    try:
        op.drop_index("ix_backtest_results_batch_id", table_name="backtest_results")
    except Exception:
        pass

    for col in (
        "batch_id",
        "strategy_signals_log_json",
        "drawdown_periods_json",
        "monthly_breakdown_json",
        "trade_log_json",
        "parameter_evolution_log_json",
        "strategy_performance_timeline_json",
    ):
        if _column_exists("backtest_results", col):
            op.drop_column("backtest_results", col)