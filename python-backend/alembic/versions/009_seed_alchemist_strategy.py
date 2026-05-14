"""seed Alchemist strategy row

Revision ID: 009_seed_alchemist_strategy
Revises: 008_add_ohlcv_source_column
Create Date: 2026-01-04 00:00:00
"""
import json
from alembic import op
import sqlalchemy as sa

revision = "009_seed_alchemist_strategy"
down_revision = "008_add_ohlcv_source_column"
branch_labels = None
depends_on = None

# Default params serialised here so the migration is self-contained and does
# not depend on importing the Alchemist class at migration time.
_ALCHEMIST_DEFAULT_PARAMS = {
    # Session / timing
    "killzone_filter_enabled": True,
    "active_killzones": ["london_open", "ny_open", "overlap"],
    "judas_swing_filter": True,
    # MSNR zone detection
    "ocl_lookback_candles": 5,
    "ob_lookback_candles": 20,
    "qml_swing_lookback": 10,
    "zone_tolerance_pct": 0.0015,
    # CRT parameters
    "crt_signal_timeframe": "1h",
    "crt_entry_timeframe": "15m",
    "crt_sweep_min_pips": 3,
    "crt_close_back_required": True,
    # HTF bias
    "htf_bias_timeframe": "4h",
    "htf_ema_fast": 20,
    "htf_ema_slow": 50,
    # Entry precision
    "fib_entry_enabled": True,
    "fib_entry_level": 0.618,
    "fib_tp3_extension": 1.272,
    "fib_tp4_extension": 1.618,
    # Risk parameters
    "atr_sl_buffer": 0.5,
    "atr_period": 14,
    "min_rr_ratio": 1.5,
    "min_confidence_threshold": 0.55,
    # SMT divergence
    "smt_filter_enabled": False,
    "smt_correlated_symbol": "XAUUSD",
    # Structure
    "structure_shift_candles": 3,
}

_PARAMS_JSON = json.dumps(_ALCHEMIST_DEFAULT_PARAMS)


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO strategies
                (name, display_name, description, is_active, is_live, params_json, created_at, updated_at)
            VALUES
                (
                    'Alchemist',
                    'Alchemist (MSNR + CRT + SMC)',
                    'Multi-confluent session-based strategy using Malaysian S/R, Candle Range Theory, '
                    'ICT Smart Money Concepts, and Fibonacci precision entries.',
                    false,
                    false,
                    :params_json,
                    NOW(),
                    NOW()
                )
            ON CONFLICT (name) DO NOTHING
            """
        ).bindparams(params_json=_PARAMS_JSON)
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM strategies WHERE name = 'Alchemist'")
    )