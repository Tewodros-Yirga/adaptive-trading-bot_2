"""add backtest_batches and strategy_pair_analyses tables

Revision ID: 006_add_pair_analysis_tables
Revises: 004_add_ensemble_decision_table
Create Date: 2026-01-05 00:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "006_add_pair_analysis_tables"
down_revision = "004_add_ensemble_decision_table"
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
    # ── backtest_batches ──────────────────────────────────────────────────────
    if not _table_exists("backtest_batches"):
        op.create_table(
            "backtest_batches",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            # UUID string; unique per batch run
            sa.Column("batch_id", sa.String(), unique=True, nullable=False, index=True),
            # JSON list of strategy names included in the batch
            sa.Column("strategy_names", sa.JSON(), nullable=False),
            # Shared settings forwarded to all runs (e.g. date range, initial_balance)
            sa.Column("shared_settings_json", sa.JSON(), nullable=True),
            # PENDING / RUNNING / COMPLETE / PARTIAL_FAILURE
            sa.Column("status", sa.String(), nullable=False, server_default="PENDING"),
            # Cross-analysis JSON: ranked scores, correlation matrix, complementary pairs
            sa.Column("cross_analysis_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
        )

    # ── strategy_pair_analyses ────────────────────────────────────────────────
    if not _table_exists("strategy_pair_analyses"):
        op.create_table(
            "strategy_pair_analyses",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            # Foreign key to backtest_batches.batch_id (string ref, not int FK)
            sa.Column("batch_id", sa.String(), nullable=False, index=True),
            # JSON list: ["RSI_Reversal", "MACD_Momentum"]
            sa.Column("strategy_names_json", sa.JSON(), nullable=False),
            # "pair" / "triple" / "all"
            sa.Column("combination_type", sa.String(), nullable=False),
            # Combined performance metrics (ensemble simulation)
            sa.Column("combined_win_rate", sa.Float(), nullable=True),
            sa.Column("combined_roi_pct", sa.Float(), nullable=True),
            sa.Column("combined_profit_factor", sa.Float(), nullable=True),
            sa.Column("combined_composite_score", sa.Float(), nullable=True),
            # JSON dict: {strategy_name: composite_score}
            sa.Column("individual_scores_json", sa.JSON(), nullable=True),
            # Fraction of bars where all strategies agreed on direction
            sa.Column("agreement_rate", sa.Float(), nullable=True),
            # Win rate on trades taken when strategies disagreed
            sa.Column("disagreement_win_rate", sa.Float(), nullable=True),
            # Trade-window overlap fraction (proxy for signal correlation)
            sa.Column("correlation", sa.Float(), nullable=True),
            # combined_composite_score / max(individual_composite_scores)
            sa.Column("synergy_score", sa.Float(), nullable=True),
            # True when synergy_score > 1.05
            sa.Column("recommended", sa.Boolean(), nullable=False, server_default="false"),
            # Claude-generated narrative: {"narrative": "...", "works_well_when": "...", "watch_out_for": "..."}
            sa.Column("analysis_json", sa.JSON(), nullable=True),
            sa.Column("computed_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )

        # Index for batch lookup ordered by synergy_score (dashboard ranking)
        op.create_index(
            "ix_strategy_pair_analyses_batch_synergy",
            "strategy_pair_analyses",
            ["batch_id", "synergy_score"],
        )

    # ── Migrate DOMINANT → WEIGHTED_VOTE in ensemble_config AppSetting ────────
    # If an existing ensemble_config row uses mode=DOMINANT, convert it so the
    # dominant strategy gets weight 1.0 and all others 0.0.
    conn = op.get_bind()
    if _table_exists("app_settings"):
        row = conn.execute(
            sa.text("SELECT value FROM app_settings WHERE key = 'ensemble_config' LIMIT 1")
        ).fetchone()
        if row:
            import json
            try:
                cfg = json.loads(row[0])
                if cfg.get("mode") == "DOMINANT":
                    dominant = cfg.get("dominant_strategy", "")
                    existing_weights = cfg.get("weights", {})
                    new_weights = dict(existing_weights)
                    if dominant:
                        new_weights[dominant] = 1.0
                    migrated = {
                        "mode": "WEIGHTED_VOTE",
                        "weights": new_weights,
                        "min_vote_threshold": 0.0,
                        "_migrated_from_dominant": dominant,
                    }
                    conn.execute(
                        sa.text(
                            "UPDATE app_settings SET value = :v WHERE key = 'ensemble_config'"
                        ),
                        {"v": json.dumps(migrated)},
                    )
            except Exception:
                pass  # Don't fail migration on bad JSON


def downgrade() -> None:
    if _table_exists("strategy_pair_analyses"):
        op.drop_index("ix_strategy_pair_analyses_batch_synergy", table_name="strategy_pair_analyses")
        op.drop_table("strategy_pair_analyses")

    if _table_exists("backtest_batches"):
        op.drop_table("backtest_batches")