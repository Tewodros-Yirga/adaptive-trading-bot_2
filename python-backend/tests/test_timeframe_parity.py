"""Tests for backtest/live timeframe parity.

A strategy must run live on the same timeframe its promoted params were
backtested on:
  - single-TF strategies signal on Strategy.live_timeframe
  - MTF strategies keep fetching their full timeframe set

These cover the pure selection helper `select_signal_df` (no DB/bridge needed).
The DataFrame is faked with a tiny stand-in exposing `.empty`, matching how the
orchestrator only checks emptiness on the candle frame.
"""
from app.services.orchestrator import select_signal_df, _DEFAULT_LIVE_TIMEFRAME


class _Frame:
    """Minimal DataFrame stand-in: truthy identity + `.empty` flag."""
    def __init__(self, name, empty=False):
        self.name = name
        self.empty = empty

    def __repr__(self):
        return f"_Frame({self.name}, empty={self.empty})"


def test_prefers_strategy_live_timeframe():
    # Key_Level backtested on 15m must signal on the 15m bars, even when a 1h
    # caller df and a default-tf frame are also present.
    bars = {"15m": _Frame("15m"), "1h": _Frame("1h")}
    caller_df = _Frame("caller-1h")
    df, used_fallback = select_signal_df("15m", bars, caller_df)
    assert df.name == "15m"
    assert used_fallback is False


def test_falls_back_to_caller_df_when_tf_bars_missing():
    # live_timeframe set but its bars unavailable → use caller df, flag fallback.
    bars = {"1h": _Frame("1h")}
    caller_df = _Frame("caller")
    df, used_fallback = select_signal_df("15m", bars, caller_df)
    assert df.name == "caller"
    assert used_fallback is True


def test_falls_back_to_caller_df_when_tf_bars_empty():
    bars = {"15m": _Frame("15m", empty=True)}
    caller_df = _Frame("caller")
    df, used_fallback = select_signal_df("15m", bars, caller_df)
    assert df.name == "caller"
    assert used_fallback is True


def test_falls_back_to_default_tf_when_no_caller_df():
    bars = {_DEFAULT_LIVE_TIMEFRAME: _Frame("default")}
    df, used_fallback = select_signal_df("15m", bars, None)
    assert df.name == "default"
    assert used_fallback is True


def test_no_live_timeframe_uses_caller_df_without_fallback_flag():
    # Strategy without a persisted live_timeframe (never promoted since the field
    # was added) uses the caller df and is NOT counted as a parity fallback.
    bars = {}
    caller_df = _Frame("caller")
    df, used_fallback = select_signal_df(None, bars, caller_df)
    assert df.name == "caller"
    assert used_fallback is False


def test_no_live_timeframe_no_caller_df_uses_default():
    bars = {_DEFAULT_LIVE_TIMEFRAME: _Frame("default")}
    df, used_fallback = select_signal_df(None, bars, None)
    assert df.name == "default"
    assert used_fallback is False


def test_returns_none_when_nothing_available():
    df, used_fallback = select_signal_df("15m", {}, None)
    assert df is None
    assert used_fallback is True


def test_empty_caller_df_skipped_for_default():
    # An empty caller df must not be chosen — fall through to the default tf.
    bars = {_DEFAULT_LIVE_TIMEFRAME: _Frame("default")}
    caller_df = _Frame("caller", empty=True)
    df, used_fallback = select_signal_df(None, bars, caller_df)
    assert df.name == "default"
