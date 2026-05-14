"""add backtest_candidates table

Revision ID: 003_add_continuous_backtest_tables
Revises: 001_strategy_schema
Create Date: 2026-01-02 00:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "003_add_continuous_backtest_tables"
down_revision = "001_strategy_schema"
branch_labels = None
depends_on = None


def _table_exists(table: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.tables WHERE table_name = :t"
    ), {"t": table})
    return result.fetchone() is not None


def upgrade() -> None:
    if not _table_exists("backtest_candidates"):
        op.create_table(
            "backtest_candidates",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("strategy_name", sa.String(), nullable=False, index=True),
            # JSON stored as TEXT for SQLite compatibility; PostgreSQL will use JSONB via dialect
            sa.Column("params_json", sa.JSON(), nullable=False),
            sa.Column("search_context_json", sa.JSON(), nullable=False),
            sa.Column("win_rate", sa.Float(), nullable=True),
            sa.Column("roi_pct", sa.Float(), nullable=True),
            sa.Column("profit_factor", sa.Float(), nullable=True),
            sa.Column("composite_score", sa.Float(), nullable=True),
            sa.Column("qualified", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("promoted", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("score_delta", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column(
                "backtest_result_id",
                sa.Integer(),
                sa.ForeignKey("backtest_results.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("evaluated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )

        # Index on strategy_name + composite_score for fast best-candidate lookups
        op.create_index(
            "ix_backtest_candidates_strategy_score",
            "backtest_candidates",
            ["strategy_name", "composite_score"],
        )

        # Index on strategy_name + qualified for filtered listing
        op.create_index(
            "ix_backtest_candidates_strategy_qualified",
            "backtest_candidates",
            ["strategy_name", "qualified"],
        )


def downgrade() -> None:
    if _table_exists("backtest_candidates"):
        op.drop_index("ix_backtest_candidates_strategy_qualified", table_name="backtest_candidates")
        op.drop_index("ix_backtest_candidates_strategy_score", table_name="backtest_candidates")
        op.drop_table("backtest_candidates")