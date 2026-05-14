"""add ensemble_decisions table

Revision ID: 004_add_ensemble_decision_table
Revises: 003_add_continuous_backtest_tables
Create Date: 2026-01-03 00:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "004_add_ensemble_decision_table"
down_revision = "003_add_continuous_backtest_tables"
branch_labels = None
depends_on = None


def _table_exists(table: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.tables WHERE table_name = :t"
    ), {"t": table})
    return result.fetchone() is not None


def upgrade() -> None:
    if not _table_exists("ensemble_decisions"):
        op.create_table(
            "ensemble_decisions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("symbol", sa.String(), nullable=False),
            sa.Column("timestamp", sa.DateTime(), nullable=False),
            sa.Column("resolved_direction", sa.String(), nullable=True),
            sa.Column("resolved_confidence", sa.Float(), nullable=True),
            sa.Column(
                "trade_id",
                sa.Integer(),
                sa.ForeignKey("trades.id", ondelete="SET NULL"),
                nullable=True,
            ),
            # JSON list of per-strategy vote dicts:
            # [{strategy_name, direction, confidence, weight, weighted_vote,
            #   proposed_entry, proposed_sl, proposed_tp1,
            #   was_agreeing, contributed_to_levels}]
            sa.Column("strategy_votes_json", sa.JSON(), nullable=True),
            sa.Column("final_entry", sa.Float(), nullable=True),
            sa.Column("final_sl", sa.Float(), nullable=True),
            sa.Column("final_tp1", sa.Float(), nullable=True),
            sa.Column("final_tp2", sa.Float(), nullable=True),
            sa.Column("final_tp3", sa.Float(), nullable=True),
            sa.Column("final_tp4", sa.Float(), nullable=True),
            sa.Column("news_bias", sa.Float(), nullable=True),
            sa.Column("news_blocked", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("risk_blocked", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("block_reason", sa.String(), nullable=True),
        )

        # Index for fast lookup by symbol + timestamp (dashboard queries)
        op.create_index(
            "ix_ensemble_decisions_symbol_timestamp",
            "ensemble_decisions",
            ["symbol", "timestamp"],
        )

        # Index for joining to trades
        op.create_index(
            "ix_ensemble_decisions_trade_id",
            "ensemble_decisions",
            ["trade_id"],
        )


def downgrade() -> None:
    if _table_exists("ensemble_decisions"):
        op.drop_index("ix_ensemble_decisions_trade_id", table_name="ensemble_decisions")
        op.drop_index("ix_ensemble_decisions_symbol_timestamp", table_name="ensemble_decisions")
        op.drop_table("ensemble_decisions")