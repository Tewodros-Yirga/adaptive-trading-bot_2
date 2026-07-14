"""Unit tests for the pending limit-order decision + miss-entry math.

These cover the pure helpers in app.services.pending_orders that need no DB or
bridge connection: the limit-vs-market decision and the entry→TP progress used
by the missed-entry cancel.
"""
from types import SimpleNamespace

import app.services.pending_orders as po_mod
from app.services.pending_orders import (
    decide_pending_entry, _miss_entry_progress, place_or_replace_pending,
)


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


# ── place_or_replace_pending: one-per-direction dedup / replace ──────────────

def _pending(direction, confidence, ticket, limit_price=4038.0,
             tp1=3946.0, stop_loss=4097.0):
    return SimpleNamespace(
        direction=direction, confidence=confidence, mt5_ticket=ticket,
        limit_price=limit_price, tp1=tp1, stop_loss=stop_loss,
        order_type=f"{direction}_LIMIT", id=ticket, symbol="XAUUSD",
    )


def _patch(monkeypatch, existing):
    """Stub crud + record/cancel so the guard runs without DB or bridge.
    Returns dicts tracking what got placed / cancelled."""
    placed, cancelled = [], []
    monkeypatch.setattr(
        po_mod.crud, "get_active_pending_orders",
        lambda db, symbol=None: list(existing),
    )
    def _record(db, **kw):
        placed.append(kw)
        return {"status": "PENDING_PLACED", "ticket": 999}
    monkeypatch.setattr(po_mod, "record_and_place_pending", _record)
    def _cancel(db, po, reason):
        cancelled.append((po.mt5_ticket, reason))
        return True
    monkeypatch.setattr(po_mod, "cancel_pending", _cancel)
    return placed, cancelled


def _call(conf=0.5, direction="SELL", limit_price=4038.0,
          tp1=3946.0, stop_loss=4097.0):
    return place_or_replace_pending(
        None, symbol="XAUUSD", direction=direction, limit_price=limit_price,
        lot_size=0.01, stop_loss=stop_loss, tp1=tp1, tp2=None,
        strategy_name="MACD_Momentum", params_version=1,
        contributing_news_ids=None, ensemble_decision_id=None, confidence=conf,
    )


def test_first_order_places_normally(monkeypatch):
    placed, cancelled = _patch(monkeypatch, existing=[])
    result = _call(conf=0.5)
    assert result["status"] == "PENDING_PLACED"
    assert len(placed) == 1 and not cancelled


def test_lower_confidence_signal_is_skipped(monkeypatch):
    placed, cancelled = _patch(monkeypatch, existing=[_pending("SELL", 0.8, 111)])
    result = _call(conf=0.5)
    assert result["status"] == "PENDING_KEPT"
    assert result["kept_ticket"] == 111
    assert not placed and not cancelled  # no duplicate, nothing cancelled


def test_equal_confidence_equal_entry_is_skipped(monkeypatch):
    placed, cancelled = _patch(
        monkeypatch, existing=[_pending("SELL", 0.5, 111, limit_price=4038.0)]
    )
    result = _call(conf=0.5, limit_price=4038.0)
    assert result["status"] == "PENDING_KEPT"
    assert not placed and not cancelled


def test_sell_tie_higher_entry_replaces(monkeypatch):
    # Equal confidence; new SELL rests HIGHER (4040 > 4038) → better entry → wins.
    placed, cancelled = _patch(
        monkeypatch, existing=[_pending("SELL", 0.5, 111, limit_price=4038.0)]
    )
    result = _call(conf=0.5, direction="SELL", limit_price=4040.0)
    assert result["status"] == "PENDING_REPLACED"
    assert cancelled == [(111, "SUPERSEDED_HIGHER_CONFIDENCE")]
    assert len(placed) == 1


def test_sell_tie_lower_entry_is_kept(monkeypatch):
    # Equal confidence; new SELL rests LOWER (4036 < 4038) → worse entry → keep old.
    placed, cancelled = _patch(
        monkeypatch, existing=[_pending("SELL", 0.5, 111, limit_price=4038.0)]
    )
    result = _call(conf=0.5, direction="SELL", limit_price=4036.0)
    assert result["status"] == "PENDING_KEPT"
    assert not placed and not cancelled


def test_buy_tie_lower_entry_replaces(monkeypatch):
    # Equal confidence; new BUY rests LOWER (1995 < 1997) → cheaper → better → wins.
    placed, cancelled = _patch(
        monkeypatch, existing=[_pending("BUY", 0.5, 111, limit_price=1997.0)]
    )
    result = _call(conf=0.5, direction="BUY", limit_price=1995.0)
    assert result["status"] == "PENDING_REPLACED"
    assert cancelled == [(111, "SUPERSEDED_HIGHER_CONFIDENCE")]
    assert len(placed) == 1


def test_buy_tie_higher_entry_is_kept(monkeypatch):
    # Equal confidence; new BUY rests HIGHER (1999 > 1997) → pricier → worse → keep.
    placed, cancelled = _patch(
        monkeypatch, existing=[_pending("BUY", 0.5, 111, limit_price=1997.0)]
    )
    result = _call(conf=0.5, direction="BUY", limit_price=1999.0)
    assert result["status"] == "PENDING_KEPT"
    assert not placed and not cancelled


# ── tiebreak: TP (reward) after confidence + entry ──────────────────────────

def test_sell_tie_entry_tie_bigger_tp_replaces(monkeypatch):
    # Same conf + same entry; new SELL tp1 lower (3940 < 3946) → more reward → wins.
    placed, cancelled = _patch(monkeypatch, existing=[
        _pending("SELL", 0.5, 111, limit_price=4038.0, tp1=3946.0)
    ])
    result = _call(conf=0.5, direction="SELL", limit_price=4038.0, tp1=3940.0)
    assert result["status"] == "PENDING_REPLACED"
    assert len(placed) == 1


def test_sell_tie_entry_tie_smaller_tp_is_kept(monkeypatch):
    # Same conf + same entry; new SELL tp1 higher (3950 > 3946) → less reward → keep.
    placed, cancelled = _patch(monkeypatch, existing=[
        _pending("SELL", 0.5, 111, limit_price=4038.0, tp1=3946.0)
    ])
    result = _call(conf=0.5, direction="SELL", limit_price=4038.0, tp1=3950.0)
    assert result["status"] == "PENDING_KEPT"
    assert not placed and not cancelled


# ── tiebreak: SL (risk) after confidence + entry + TP ───────────────────────

def test_sell_tie_all_tie_tighter_sl_replaces(monkeypatch):
    # Same conf, entry, tp; new SELL sl closer (4090 < 4097) → tighter risk → wins.
    placed, cancelled = _patch(monkeypatch, existing=[
        _pending("SELL", 0.5, 111, limit_price=4038.0, tp1=3946.0, stop_loss=4097.0)
    ])
    result = _call(conf=0.5, direction="SELL", limit_price=4038.0,
                   tp1=3946.0, stop_loss=4090.0)
    assert result["status"] == "PENDING_REPLACED"
    assert len(placed) == 1


def test_sell_tie_all_tie_wider_sl_is_kept(monkeypatch):
    # Same conf, entry, tp; new SELL sl wider (4100 > 4097) → looser risk → keep.
    placed, cancelled = _patch(monkeypatch, existing=[
        _pending("SELL", 0.5, 111, limit_price=4038.0, tp1=3946.0, stop_loss=4097.0)
    ])
    result = _call(conf=0.5, direction="SELL", limit_price=4038.0,
                   tp1=3946.0, stop_loss=4100.0)
    assert result["status"] == "PENDING_KEPT"
    assert not placed and not cancelled


def test_fully_identical_order_is_kept(monkeypatch):
    # Same conf, entry, tp, sl → new key == old key → keep the resting one.
    placed, cancelled = _patch(monkeypatch, existing=[
        _pending("SELL", 0.5, 111, limit_price=4038.0, tp1=3946.0, stop_loss=4097.0)
    ])
    result = _call(conf=0.5, direction="SELL", limit_price=4038.0,
                   tp1=3946.0, stop_loss=4097.0)
    assert result["status"] == "PENDING_KEPT"
    assert not placed and not cancelled


def test_higher_confidence_replaces_existing(monkeypatch):
    placed, cancelled = _patch(monkeypatch, existing=[_pending("SELL", 0.5, 111)])
    result = _call(conf=0.9)
    assert result["status"] == "PENDING_REPLACED"
    assert result["replaced_count"] == 1
    assert cancelled == [(111, "SUPERSEDED_HIGHER_CONFIDENCE")]
    assert len(placed) == 1


def test_opposite_direction_is_ignored(monkeypatch):
    # A resting BUY must not block a fresh SELL pending.
    placed, cancelled = _patch(monkeypatch, existing=[_pending("BUY", 0.9, 111)])
    result = _call(conf=0.2)
    assert result["status"] == "PENDING_PLACED"
    assert len(placed) == 1 and not cancelled


def test_higher_confidence_replaces_all_stacked(monkeypatch):
    existing = [_pending("SELL", 0.3, 1), _pending("SELL", 0.4, 2), _pending("SELL", 0.2, 3)]
    placed, cancelled = _patch(monkeypatch, existing=existing)
    result = _call(conf=0.9)
    assert result["status"] == "PENDING_REPLACED"
    assert result["replaced_count"] == 3
    assert {t for t, _ in cancelled} == {1, 2, 3}
    assert len(placed) == 1
