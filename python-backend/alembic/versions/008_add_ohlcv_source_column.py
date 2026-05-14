"""add source column to ohlcv_cache

Revision ID: 008_add_ohlcv_source_column
Revises: 003_add_continuous_backtest_tables
Create Date: 2026-01-03 00:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "008_add_ohlcv_source_column"
down_revision = "003_add_continuous_backtest_tables"
branch_labels = None
depends_on = None


def _col_exists(table: str, col: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = :t AND column_name = :c"
    ), {"t": table, "c": col})
    return result.fetchone() is not None


def upgrade() -> None:
    if not _col_exists("ohlcv_cache", "source"):
        op.add_column(
            "ohlcv_cache",
            sa.Column("source", sa.String(), nullable=True),
        )
        # Backfill existing rows with a sentinel value
        op.execute("UPDATE ohlcv_cache SET source = 'unknown' WHERE source IS NULL")


def downgrade() -> None:
    if _col_exists("ohlcv_cache", "source"):
        op.drop_column("ohlcv_cache", "source")