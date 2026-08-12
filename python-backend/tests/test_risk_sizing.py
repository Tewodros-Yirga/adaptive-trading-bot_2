"""Tests for risk sizing: pip classification, balance parsing, dynamic lots."""
from app.services.risk_manager import (
    pip_value,
    pip_cost_per_lot,
    _parse_balance,
    compute_dynamic_lot_size,
)


# ── pip_value / pip_cost_per_lot (CFD-suffix handling) ────────────────────────

def test_pip_value_recognizes_cfd_suffix():
    # XAUUSDc previously fell through to 0.0001 — the root bug
    assert pip_value("XAUUSDc") == 0.1
    assert pip_value("XAUUSD") == 0.1
    assert pip_value("GOLD") == 0.1


def test_pip_value_other_instruments():
    assert pip_value("XAGUSDc") == 0.001
    assert pip_value("US30c") == 1.0
    assert pip_value("EURUSD") == 0.0001  # unchanged


def test_pip_cost_per_lot_suffix():
    assert pip_cost_per_lot("XAUUSDc") == 10.0
    assert pip_cost_per_lot("XAGUSDc") == 50.0


# ── Balance parsing (USDC treated 1:1 as USD) ─────────────────────────────────

def test_parse_balance_numeric():
    assert _parse_balance(195.0) == 195.0
    assert _parse_balance("195") == 195.0


def test_parse_balance_with_currency_label():
    assert _parse_balance("195.00 USDC") == 195.0
    assert _parse_balance("$195.00") == 195.0
    assert _parse_balance("1,950.50") == 1950.5


def test_parse_balance_garbage():
    assert _parse_balance(None) is None
    assert _parse_balance("") is None
    assert _parse_balance("abc") is None


# ── DYNAMIC lot sizing (user formula: (balance*risk*0.01)/stop_distance) ─────

def test_dynamic_lot_floor_below_50_balance():
    assert compute_dynamic_lot_size(49, 20, 4398.0, 4346.0, "XAUUSDc") == 0.01


def test_dynamic_lot_scales_with_balance():
    # $100 @20% with a $52 stop → (100*0.2*0.1)/(6*52) = 0.0064 → 0.01 floor
    assert compute_dynamic_lot_size(100, 20, 4398.0, 4346.0, "XAUUSDc") == 0.01
    # $1000 @20% → (1000*0.2*0.1)/(6*52) = 0.064 → 0.06 — grows proportionally
    assert compute_dynamic_lot_size(1000, 20, 4398.0, 4346.0, "XAUUSDc") == 0.06
    # $5000 @20% → (5000*0.2*0.1)/(6*52) = 0.32
    assert compute_dynamic_lot_size(5000, 20, 4398.0, 4346.0, "XAUUSDc") == 0.32


def test_dynamic_lot_user_example_capped_stop():
    # User's exact case: $100, 20% risk, $15 stop (150-pip cap on gold)
    # → (100 * 0.2 * 0.1) / (6 * 15) = 0.022 → 0.02 lots
    assert compute_dynamic_lot_size(100, 20, 4398.0, 4383.0, "XAUUSDc") == 0.02


def test_dynamic_lot_respects_risk_pct():
    # Lower risk % -> smaller lot for same balance and stop
    assert compute_dynamic_lot_size(1000, 5, 4398.0, 4346.0, "XAUUSDc") < compute_dynamic_lot_size(1000, 20, 4398.0, 4346.0, "XAUUSDc")


def test_dynamic_lot_zero_stop_distance():
    assert compute_dynamic_lot_size(100, 20, 4398.0, 4398.0, "XAUUSDc") == 0.01
