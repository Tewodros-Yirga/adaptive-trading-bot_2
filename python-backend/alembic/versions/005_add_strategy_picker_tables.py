"""add strategy picker tables

Creates strategy_picker_decisions and picker_weight_history tables.

Revision ID: 005_add_strategy_picker_tables
Revises: 004_add_ensemble_decision_table
Create Date: 2026-01-06 00:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "005_add_strategy_picker_tables"
down_revision = "004_add_ensemble_decision_table"
branch_labels = None
depends_on = None


def _table_exists(table: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.tables WHERE table_name = :t"
    ), {"t": table})
    return result.fetchone() is not None


def upgrade() -> None:
    # ── strategy_picker_decisions ─────────────────────────────────────────
    if not _table_exists("strategy_picker_decisions"):
        op.create_table(
            "strategy_picker_decisions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("symbol", sa.String(), nullable=False),
            sa.Column("timestamp", sa.DateTime(), nullable=False),
            sa.Column("strategy_scores_json", sa.JSON(), nullable=True),
            sa.Column("selected_strategies_json", sa.JSON(), nullable=True),
            sa.Column("ensemble_weights_used_json", sa.JSON(), nullable=True),
            sa.Column(
                "trade_id",
                sa.Integer(),
                sa.ForeignKey("trades.id"),
                nullable=True,
            ),
            sa.Column("news_influence_json", sa.JSON(), nullable=True),
            sa.Column("picker_confidence", sa.Float(), nullable=True),
            sa.Column("reasoning", sa.String(), nullable=True),
        )
        op.create_index(
            "ix_strategy_picker_decisions_symbol",
            "strategy_picker_decisions",
            ["symbol"],
        )
        op.create_index(
            "ix_strategy_picker_decisions_timestamp",
            "strategy_picker_decisions",
            ["timestamp"],
        )

    # ── picker_weight_history ─────────────────────────────────────────────
    if not _table_exists("picker_weight_history"):
        op.create_table(
            "picker_weight_history",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "trade_id",
                sa.Integer(),
                sa.ForeignKey("trades.id"),
                nullable=False,
            ),
            sa.Column("weights_before_json", sa.JSON(), nullable=True),
            sa.Column("weights_after_json", sa.JSON(), nullable=True),
            sa.Column("weight_deltas_json", sa.JSON(), nullable=True),
            sa.Column("trade_result", sa.String(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index(
            "ix_picker_weight_history_trade_id",
            "picker_weight_history",
            ["trade_id"],
        )


def downgrade() -> None:
    if _table_exists("picker_weight_history"):
        op.drop_index("ix_picker_weight_history_trade_id", table_name="picker_weight_history")
        op.drop_table("picker_weight_history")

    if _table_exists("strategy_picker_decisions"):
        op.drop_index("ix_strategy_picker_decisions_timestamp", table_name="strategy_picker_decisions")
        op.drop_index("ix_strategy_picker_decisions_symbol", table_name="strategy_picker_decisions")
        op.drop_table("strategy_picker_decisions")