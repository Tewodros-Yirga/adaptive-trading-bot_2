"""Unit tests for the pending limit-order decision + miss-entry math.

These cover the pure helpers in app.services.pending_orders that need no DB or
bridge connection: the limit-vs-market decision and the entry→TP progress used
by the missed-entry cancel.
"""
from types import SimpleNamespace

from app.services.pending_orders import decide_pending_entry, _miss_entry_progress


def _settings(**overrides):
    base = {
        "enabled": True,
        "min_distance_atr": 0.25,
        "entry_offset_atr": 0.5,
        "miss_entry_fraction": 0.40,
    }
    base.update(overrides)
    return base


# ── decide_pending_entry ────────────────────────────────────────────────────

def test_disabled_returns_none():
    assert decide_pending_entry("BUY", 2000.0, 1990.0, 5.0, _settings(enabled=False)) is None


def test_buy_uses_favourable_strategy_entry():
    # proposed entry below market and beyond min distance → use it
    limit = decide_pending_entry("BUY", 2000.0, 1990.0, 5.0, _settings())
    assert limit == 1990.0


def test_sell_uses_favourable_strategy_entry():
    limit = decide_pending_entry("SELL", 2000.0, 2010.0, 5.0, _settings())
    assert limit == 2010.0


def test_buy_synthesises_pullback_when_no_entry():
    # no proposed entry → price - offset*atr = 2000 - 0.5*5 = 1997.5
    limit = decide_pending_entry("BUY", 2000.0, None, 5.0, _settings())
    assert limit == 1997.5


def test_sell_synthesises_pullback_when_no_entry():
    limit = decide_pending_entry("SELL", 2000.0, None, 5.0, _settings())
    assert limit == 2002.5


def test_buy_entry_too_close_returns_none():
    # entry only 1.0 below market, min_dist = 0.25*5 = 1.25 → too close → market
    assert decide_pending_entry("BUY", 2000.0, 1999.0, 5.0, _settings()) is None


def test_buy_entry_through_price_falls_back_to_pullback():
    # proposed entry above market (unfavourable for a BUY) → synthesise pullback
    limit = decide_pending_entry("BUY", 2000.0, 2005.0, 5.0, _settings())
    assert limit == 1997.5


def test_zero_atr_returns_none():
    assert decide_pending_entry("BUY", 2000.0, 1990.0, 0.0, _settings()) is None


# ── _miss_entry_progress ────────────────────────────────────────────────────

def test_buy_progress_halfway():
    po = SimpleNamespace(direction="BUY", limit_price=1990.0, tp1=2010.0)
    # price 2000 → (2000-1990)/(2010-1990) = 10/20 = 0.5
    assert _miss_entry_progress(po, 2000.0) == 0.5


def test_sell_progress_halfway():
    po = SimpleNamespace(direction="SELL", limit_price=2010.0, tp1=1990.0)
    # price 2000 → (2010-2000)/(2010-1990) = 10/20 = 0.5
    assert _miss_entry_progress(po, 2000.0) == 0.5


def test_progress_none_when_no_tp():
    po = SimpleNamespace(direction="BUY", limit_price=1990.0, tp1=None)
    assert _miss_entry_progress(po, 2000.0) is None


def test_progress_none_on_degenerate_span():
    po = SimpleNamespace(direction="BUY", limit_price=2010.0, tp1=2000.0)  # tp below limit
    assert _miss_entry_progress(po, 2005.0) is None


def test_buy_progress_at_threshold():
    po = SimpleNamespace(direction="BUY", limit_price=1990.0, tp1=2010.0)
    # 40% progress → price = 1990 + 0.4*20 = 1998
    assert abs(_miss_entry_progress(po, 1998.0) - 0.40) < 1e-9
