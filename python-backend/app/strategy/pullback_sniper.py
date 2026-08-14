"""
app/strategy/pullback_sniper.py — Pullback Sniper Method

Python/backtester port of the "Pullback Sniper Method [trade_w_samet]"
Pine Script v6 indicator. This file merges the two prior drafts of this
strategy:

  * the STATEFUL engine (setup persists across bars like Pine's `var`,
    ATR-based SL/staged reward-R TPs, ported quality score, cooldown,
    fire-once-per-setup) — this is the primary architecture, because it
    is the more faithful and more bug-resistant of the two, and
  * the STATELESS draft's one genuine advantage: it could reconstruct an
    in-flight setup purely by re-scanning `ohlcv_window` history, with no
    dependency on having been fed every prior bar in order.

That second property is grafted in as a one-time COLD-START RECOVERY
step: on the very first call, if the strategy is handed a window that
already contains history (rather than starting fresh at bar zero), it
re-derives any breakout/pullback setup that would already be "in flight"
from that history before falling through to the normal per-bar state
machine. After that first call, behavior is identical to the pure
stateful version — self._setup persists and is advanced incrementally,
which is both cheaper and closer to the original indicator's semantics
than re-scanning the whole window on every bar.

Logic (trading rules only — the Pine script's boxes/lines/dashboard/stats
table are visual-only and are not part of the backtest):

  1. TREND:      EMA(fast) vs EMA(slow), plus a slope check on EMA(fast).
  2. BREAKOUT:   trend-aligned close beyond the N-bar high/low, with a
                 minimum candle body relative to ATR. Opens a "setup"
                 (direction + invalidation level from a shorter lookback).
  3. PULLBACK:   the setup waits (up to max_bars_to_find_pullback bars,
                 starting min_bars_after_breakout bars in) for price to
                 tag the pullback EMA without breaking the invalidation
                 level.
  4. CONFIRMATION: once the pullback is tagged, a candle-pattern trigger
                 (Fast / Balanced / Strict) confirms the bounce back in
                 the trend direction, subject to a max-distance-from-EMA
                 cap (in ATR) so entries don't chase.
  5. FILTERS:    optional McGinley Dynamic "stay-away-from-chop" distance
                 filter, optional RSI directional-bias filter, and a
                 signal cooldown.
  6. LEVELS:     SL = entry -/+ ATR * multiplier. TP1/TP2/TP3 are staged
                 fractions of a final reward-R target (tp3_reward_r).
                 TP4 is a stretch target beyond TP3, added because this
                 backtester's shared trade engine supports 4 partial-TP
                 legs; it does not change entries, SL, TP1, TP2 or TP3.

Not ported (purely visual / not applicable to headless backtesting):
  color themes, boxes/lines/labels/tables, alertconditions, the
  statistics dashboard. The original's "protected TP on SL after a
  partial fill" money management is also not replicated — the shared
  partial-TP / breakeven / trailing engine in this backend already does
  that generically for every strategy via the sl/tp1-4 contract, so
  duplicating it here would fight the harness rather than use it.
  Likewise the tp3-direction-lock (blocking immediate same-direction
  re-entry right after a TP3 win) is not replicated: this harness never
  calls signal() while a trade is open, so the condition it exists to
  prevent cannot occur here.
"""
from __future__ import annotations

from typing import Any

from app.strategy.base import BaseStrategy


def _ema_list(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _atr_list(ohlcv: list[dict], period: int) -> list[float]:
    """Wilder-smoothed ATR (matches Pine's ta.atr), same-length output."""
    n = len(ohlcv)
    if n == 0:
        return []
    trs: list[float] = []
    prev_close = None
    for bar in ohlcv:
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])
        tr = (high - low) if prev_close is None else max(
            high - low, abs(high - prev_close), abs(low - prev_close)
        )
        trs.append(tr)
        prev_close = close

    out = [trs[0]]
    for i in range(1, n):
        if i < period:
            out.append(sum(trs[:i + 1]) / (i + 1))
        else:
            out.append((out[-1] * (period - 1) + trs[i]) / period)
    return out


def _rsi_list(closes: list[float], period: int) -> list[float]:
    n = len(closes)
    if n < period + 1:
        return [50.0] * n
    out = [50.0] * period
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, period + 1)]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, period + 1)]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    def _rsi_from_avg(ag: float, al: float) -> float:
        if al == 0:
            return 100.0
        rs = ag / al
        return 100.0 - 100.0 / (1.0 + rs)

    out.append(_rsi_from_avg(avg_gain, avg_loss))
    for i in range(period + 1, n):
        diff = closes[i] - closes[i - 1]
        gain = max(diff, 0.0)
        loss = max(-diff, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out.append(_rsi_from_avg(avg_gain, avg_loss))
    return out


def _mcginley_list(closes: list[float], length: int) -> list[float]:
    """McGinley Dynamic, seeded with an EMA (matches the Pine source).
    Recomputed fresh over the supplied window each call — the same
    windowed-recompute approach every indicator helper in this codebase
    already uses (see e.g. adx_regime_filter.py's Wilder smoothing)."""
    if not closes:
        return []
    seed = _ema_list(closes, length)[0]
    out = [seed]
    for price in closes[1:]:
        prev = out[-1]
        ratio = (price / prev) if prev != 0 else 1.0
        divider = length * (ratio ** 4)
        divider = divider if divider != 0 else length
        out.append(prev + (price - prev) / divider)
    return out


class PullbackSniperStrategy(BaseStrategy):
    name = "Pullback_Sniper"
    display_name = "Pullback Sniper Method"
    description = (
        "EMA-trend breakout -> pullback-to-EMA -> confirmation-candle entry "
        "system, with optional McGinley Dynamic chop filter and RSI bias "
        "filter, and staged TP1/TP2/TP3(/TP4) reward-R targets."
    )
    is_adaptive = False

    DEFAULT_PARAMS: dict[str, Any] = {
        "fast_ema_length": 50,
        "slow_ema_length": 200,
        "pullback_ema_length": 21,
        "slope_lookback": 5,
        "breakout_lookback": 20,
        "invalidation_lookback": 12,
        "min_bars_after_breakout": 2,
        "max_bars_to_find_pullback": 60,
        "atr_length": 14,
        "min_breakout_body_atr": 0.20,
        "min_confirm_body_atr": 0.25,
        "max_entry_distance_atr": 1.00,
        "cooldown_bars": 20,
        "mcginley_length": 100,
        "mcginley_distance_atr": 0.25,
        "rsi_length": 14,
        "rsi_long_min": 50.0,
        "rsi_short_max": 50.0,
        "trade_atr_length": 14,
        "trade_atr_multiplier": 2.0,
        "tp3_reward_r": 2.0,
        "tp1_percent_of_tp3": 25.0,
        "tp2_percent_of_tp3": 50.0,
        "tp4_extension_factor": 1.5,
        # Non-numeric switches — kept as plain params, deliberately absent
        # from PARAM_BOUNDS since coordinate ascent only tunes numeric keys
        # (see param_search.py's _ensure_param_keys filter).
        "confirmation_mode": "Balanced",   # "Fast" | "Balanced" | "Strict"
        "use_mcginley_filter": True,
        "use_rsi_filter": False,
        "use_cooldown": True,
    }

    PARAM_BOUNDS: dict[str, tuple[float, float]] = {
        "fast_ema_length": (10, 100),
        "slow_ema_length": (100, 300),
        "pullback_ema_length": (10, 50),
        "slope_lookback": (2, 15),
        "breakout_lookback": (10, 60),
        "invalidation_lookback": (5, 40),
        "min_bars_after_breakout": (1, 10),
        "max_bars_to_find_pullback": (20, 150),
        "atr_length": (5, 30),
        "min_breakout_body_atr": (0.05, 1.0),
        "min_confirm_body_atr": (0.05, 1.0),
        "max_entry_distance_atr": (0.2, 3.0),
        "cooldown_bars": (5, 80),
        "mcginley_length": (30, 200),
        "mcginley_distance_atr": (0.05, 1.0),
        "rsi_length": (5, 30),
        "rsi_long_min": (30.0, 70.0),
        "rsi_short_max": (30.0, 70.0),
        "trade_atr_length": (5, 30),
        "trade_atr_multiplier": (0.5, 4.0),
        "tp3_reward_r": (1.0, 5.0),
        "tp1_percent_of_tp3": (10.0, 50.0),
        "tp2_percent_of_tp3": (30.0, 80.0),
        "tp4_extension_factor": (1.0, 3.0),
    }

    # Restart-sampling feasibility guard (coordinate ascent still uses the
    # full PARAM_BOUNDS). Sampling these near the strict end of their range
    # routinely yields zero breakouts / zero confirmations per backtest
    # window — same rationale as Alchemist's / Key_Level's
    # RESTART_SAMPLE_BOUNDS.
    RESTART_SAMPLE_BOUNDS: dict[str, tuple[float, float]] = {
        "min_breakout_body_atr": (0.05, 0.35),
        "min_confirm_body_atr": (0.05, 0.40),
        "max_entry_distance_atr": (0.5, 1.5),
        "mcginley_distance_atr": (0.05, 0.40),
    }

    def __init__(self, params: dict[str, Any] | None = None):
        super().__init__(params)
        # Persistent multi-bar setup state (mirrors the Pine `var` state).
        # Safe as instance state because signal() is called once per bar,
        # strictly sequentially, for the life of a single backtest/live
        # instance — the same assumption documented in alchemist.py's and
        # key_level.py's BUG-01 notes, extended here across MULTIPLE bars
        # rather than reset every call, because the breakout -> pullback ->
        # confirmation setup is inherently a multi-bar process.
        self._bar_counter: int = -1
        self._setup: dict | None = None
        self._last_signal_bar: int | None = None
        self._last_confirmed_setup_id: int | None = None
        self._last_trade_atr: float | None = None
        self._recovery_attempted: bool = False

    @classmethod
    def default_params(cls) -> dict:
        return cls.DEFAULT_PARAMS.copy()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def signal(self, market_data: dict) -> tuple[str | None, float]:
        ohlcv = market_data.get("ohlcv_window")
        if not ohlcv:
            return None, 0.0

        p = self.params
        slow_len = int(p.get("slow_ema_length", 200))
        slope_lb = int(p.get("slope_lookback", 5))
        min_bars = slow_len + slope_lb + 2
        if len(ohlcv) < min_bars:
            return None, 0.0

        closes = [float(b["close"]) for b in ohlcv]
        opens = [float(b["open"]) for b in ohlcv]
        highs = [float(b["high"]) for b in ohlcv]
        lows = [float(b["low"]) for b in ohlcv]

        fast_ema = _ema_list(closes, int(p.get("fast_ema_length", 50)))
        slow_ema = _ema_list(closes, slow_len)
        pullback_ema = _ema_list(closes, int(p.get("pullback_ema_length", 21)))
        atr = _atr_list(ohlcv, int(p.get("atr_length", 14)))

        if len(fast_ema) <= slope_lb or atr[-1] <= 0:
            return None, 0.0

        # ── Cold-start recovery ─────────────────────────────────────────
        # If this is the first bar this instance has ever seen AND the
        # window already contains a lot of history (i.e. we were not
        # instantiated at the true start of the series), re-scan that
        # history once to recover any breakout/pullback setup that would
        # already be "in flight" rather than silently waiting for a brand
        # new breakout. After this one-time pass, state advances normally.
        if not self._recovery_attempted:
            self._recovery_attempted = True
            if len(ohlcv) > min_bars + 5:
                self._recover_setup_from_history(
                    p, closes, opens, highs, lows, fast_ema, slow_ema, pullback_ema, atr,
                )

        self._bar_counter += 1
        bar_idx = self._bar_counter

        close = closes[-1]
        open_ = opens[-1]
        high = highs[-1]
        low = lows[-1]
        body_size = abs(close - open_)
        upper_wick = high - max(open_, close)
        lower_wick = min(open_, close) - low

        bull_trend = (
            fast_ema[-1] > slow_ema[-1]
            and close > slow_ema[-1]
            and fast_ema[-1] > fast_ema[-1 - slope_lb]
        )
        bear_trend = (
            fast_ema[-1] < slow_ema[-1]
            and close < slow_ema[-1]
            and fast_ema[-1] < fast_ema[-1 - slope_lb]
        )

        breakout_lb = int(p.get("breakout_lookback", 20))
        if len(highs) <= breakout_lb:
            return None, 0.0
        highest_before = max(highs[-1 - breakout_lb:-1])
        lowest_before = min(lows[-1 - breakout_lb:-1])
        breakout_body_ok = body_size >= atr[-1] * float(p.get("min_breakout_body_atr", 0.20))

        bull_breakout = bull_trend and close > highest_before and close > open_ and breakout_body_ok
        bear_breakout = bear_trend and close < lowest_before and close < open_ and breakout_body_ok

        # ── McGinley / RSI filters ────────────────────────────────────────
        mcginley = _mcginley_list(closes, int(p.get("mcginley_length", 100)))
        mcginley_distance = abs(close - mcginley[-1])
        mcginley_allowed = (
            not bool(p.get("use_mcginley_filter", True))
            or mcginley_distance >= atr[-1] * float(p.get("mcginley_distance_atr", 0.25))
        )
        rsi = _rsi_list(closes, int(p.get("rsi_length", 14)))
        rsi_value = rsi[-1]
        use_rsi = bool(p.get("use_rsi_filter", False))
        rsi_long_ok = (not use_rsi) or rsi_value >= float(p.get("rsi_long_min", 50.0))
        rsi_short_ok = (not use_rsi) or rsi_value <= float(p.get("rsi_short_max", 50.0))

        # ── Setup state machine ───────────────────────────────────────────
        inval_lb = int(p.get("invalidation_lookback", 12))
        can_create_setup = self._setup is None or self._setup.get("completed", False)
        if len(highs) > inval_lb:
            if bull_breakout and can_create_setup:
                self._setup = {
                    "direction": 1, "start_bar": bar_idx,
                    "invalidation": min(lows[-1 - inval_lb:-1]),
                    "pullback_hit": False, "completed": False, "id": bar_idx,
                }
            elif bear_breakout and can_create_setup:
                self._setup = {
                    "direction": -1, "start_bar": bar_idx,
                    "invalidation": max(highs[-1 - inval_lb:-1]),
                    "pullback_hit": False, "completed": False, "id": bar_idx,
                }

        setup = self._setup
        if setup and not setup["completed"]:
            bars_since = bar_idx - setup["start_bar"]
            expired = bars_since > int(p.get("max_bars_to_find_pullback", 60))
            invalidated = (
                (setup["direction"] == 1 and close < setup["invalidation"]) or
                (setup["direction"] == -1 and close > setup["invalidation"])
            )
            if expired or invalidated:
                setup["completed"] = True
            else:
                min_after = int(p.get("min_bars_after_breakout", 2))
                if not setup["pullback_hit"] and bars_since >= min_after:
                    if setup["direction"] == 1 and low <= pullback_ema[-1] and close > setup["invalidation"]:
                        setup["pullback_hit"] = True
                    elif setup["direction"] == -1 and high >= pullback_ema[-1] and close < setup["invalidation"]:
                        setup["pullback_hit"] = True

        setup = self._setup
        if not setup or setup["completed"] or not setup["pullback_hit"]:
            return None, 0.0

        # ── Confirmation ───────────────────────────────────────────────────
        entry_distance_ok = abs(close - pullback_ema[-1]) <= atr[-1] * float(p.get("max_entry_distance_atr", 1.0))
        confirm_body_ok = body_size >= atr[-1] * float(p.get("min_confirm_body_atr", 0.25))
        mode = str(p.get("confirmation_mode", "Balanced"))
        prev_high = highs[-2]
        prev_low = lows[-2]

        if setup["direction"] == 1:
            if mode == "Fast":
                confirm = close > pullback_ema[-1] and close > open_
            elif mode == "Strict":
                confirm = close > pullback_ema[-1] and close > prev_high and close > open_ and confirm_body_ok
            else:  # Balanced
                confirm = (
                    close > pullback_ema[-1] and close > open_ and
                    (close > prev_high or lower_wick >= body_size * 0.5)
                )
        else:
            if mode == "Fast":
                confirm = close < pullback_ema[-1] and close < open_
            elif mode == "Strict":
                confirm = close < pullback_ema[-1] and close < prev_low and close < open_ and confirm_body_ok
            else:  # Balanced
                confirm = (
                    close < pullback_ema[-1] and close < open_ and
                    (close < prev_low or upper_wick >= body_size * 0.5)
                )

        if not confirm or not entry_distance_ok or not mcginley_allowed:
            return None, 0.0

        trend_ok = bull_trend if setup["direction"] == 1 else bear_trend
        if not trend_ok:
            return None, 0.0

        direction_ok = rsi_long_ok if setup["direction"] == 1 else rsi_short_ok
        if not direction_ok:
            return None, 0.0

        if bool(p.get("use_cooldown", True)):
            cooldown_bars = int(p.get("cooldown_bars", 20))
            if self._last_signal_bar is not None and bar_idx - self._last_signal_bar < cooldown_bars:
                return None, 0.0

        # Fire once per setup, not once per bar the confirmation stays true.
        if self._last_confirmed_setup_id == setup["id"]:
            return None, 0.0

        setup["completed"] = True
        self._last_signal_bar = bar_idx
        self._last_confirmed_setup_id = setup["id"]

        direction = "BUY" if setup["direction"] == 1 else "SELL"
        self._last_trade_atr = atr[-1]

        confidence = self._quality_score(
            is_long=(direction == "BUY"), bull_trend=bull_trend, bear_trend=bear_trend,
            body_size=body_size, atr_value=atr[-1], close=close, pullback_ema=pullback_ema[-1],
            lower_wick=lower_wick, upper_wick=upper_wick,
            use_mcginley=bool(p.get("use_mcginley_filter", True)), mcginley_distance=mcginley_distance,
            mcginley_threshold=atr[-1] * float(p.get("mcginley_distance_atr", 0.25)),
            use_rsi=use_rsi, rsi_value=rsi_value,
            rsi_long_min=float(p.get("rsi_long_min", 50.0)), rsi_short_max=float(p.get("rsi_short_max", 50.0)),
        )
        return direction, confidence

    def compute_levels(self, direction: str, price: float, params: dict) -> dict:
        if not direction:
            return {}
        atr = float(params.get("atr", self._last_trade_atr or price * 0.005))
        sl_dist = atr * float(params.get("trade_atr_multiplier", 2.0))
        if sl_dist <= 0:
            return {}
        sign = 1 if direction == "BUY" else -1

        reward_r = float(params.get("tp3_reward_r", 2.0))
        tp1_pct = float(params.get("tp1_percent_of_tp3", 25.0)) / 100.0
        tp2_pct = max(float(params.get("tp2_percent_of_tp3", 50.0)) / 100.0, tp1_pct)
        tp4_factor = max(float(params.get("tp4_extension_factor", 1.5)), 1.0)

        sl = price - sign * sl_dist
        tp1 = price + sign * sl_dist * reward_r * tp1_pct
        tp2 = price + sign * sl_dist * reward_r * tp2_pct
        tp3 = price + sign * sl_dist * reward_r
        tp4 = price + sign * sl_dist * reward_r * tp4_factor

        return {
            "sl": round(sl, 5),
            "tp1": round(tp1, 5),
            "tp2": round(tp2, 5),
            "tp3": round(tp3, 5),
            "tp4": round(tp4, 5),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _recover_setup_from_history(
        self, p: dict, closes: list[float], opens: list[float],
        highs: list[float], lows: list[float],
        fast_ema: list[float], slow_ema: list[float], pullback_ema: list[float],
        atr: list[float],
    ) -> None:
        """One-time cold-start reconstruction of an in-flight setup.

        Ported from the stateless draft's backward scan: walks the window
        (excluding the final/current bar, which the normal state machine
        will process itself right after this returns) looking for the
        most recent trend-aligned breakout that hasn't since been
        invalidated or timed out, and checks whether its pullback has
        already been tagged. This only matters if the instance is handed
        a window that already has history in it — if signal() has been
        driving this instance bar-by-bar since the start of the series,
        self._setup is already correct and this scan is skipped.
        """
        n = len(closes)
        slope_lb = int(p.get("slope_lookback", 5))
        breakout_lb = int(p.get("breakout_lookback", 20))
        inval_lb = int(p.get("invalidation_lookback", 12))
        min_after = int(p.get("min_bars_after_breakout", 2))
        max_find = int(p.get("max_bars_to_find_pullback", 60))
        min_breakout_body_atr = float(p.get("min_breakout_body_atr", 0.20))

        # Last index this scan is allowed to treat as "already happened";
        # the true current bar (n - 1) is left for the normal path.
        last_hist_idx = n - 2
        if last_hist_idx <= breakout_lb:
            return

        def bull_trend_at(i: int) -> bool:
            if i - slope_lb < 0:
                return False
            return (
                fast_ema[i] > slow_ema[i]
                and closes[i] > slow_ema[i]
                and fast_ema[i] > fast_ema[i - slope_lb]
            )

        def bear_trend_at(i: int) -> bool:
            if i - slope_lb < 0:
                return False
            return (
                fast_ema[i] < slow_ema[i]
                and closes[i] < slow_ema[i]
                and fast_ema[i] < fast_ema[i - slope_lb]
            )

        def body_at(i: int) -> float:
            return abs(closes[i] - opens[i])

        scan_start = max(breakout_lb + 1, last_hist_idx - (min_after + max_find) - 5)
        breakout_idx = None
        breakout_dir = 0
        for i in range(last_hist_idx, scan_start - 1, -1):
            if i - breakout_lb < 0 or atr[i] <= 0:
                continue
            highest_before = max(highs[i - breakout_lb:i])
            lowest_before = min(lows[i - breakout_lb:i])
            body_ok = body_at(i) >= atr[i] * min_breakout_body_atr
            if bull_trend_at(i) and closes[i] > highest_before and closes[i] > opens[i] and body_ok:
                breakout_idx, breakout_dir = i, 1
                break
            if bear_trend_at(i) and closes[i] < lowest_before and closes[i] < opens[i] and body_ok:
                breakout_idx, breakout_dir = i, -1
                break

        if breakout_idx is None:
            return

        bars_since = last_hist_idx - breakout_idx
        if bars_since > (min_after + max_find):
            return  # setup would already be expired — nothing to recover

        inv_start = max(0, breakout_idx - inval_lb)
        if breakout_dir == 1:
            invalidation = min(lows[inv_start:breakout_idx]) if breakout_idx > inv_start else lows[breakout_idx]
        else:
            invalidation = max(highs[inv_start:breakout_idx]) if breakout_idx > inv_start else highs[breakout_idx]

        invalidated = False
        for i in range(breakout_idx + 1, last_hist_idx + 1):
            if breakout_dir == 1 and closes[i] < invalidation:
                invalidated = True
                break
            if breakout_dir == -1 and closes[i] > invalidation:
                invalidated = True
                break
        if invalidated:
            return

        pullback_hit = False
        for i in range(breakout_idx + min_after, last_hist_idx + 1):
            if pullback_ema[i] is None:
                continue
            if breakout_dir == 1 and lows[i] <= pullback_ema[i] and closes[i] > invalidation:
                pullback_hit = True
                break
            if breakout_dir == -1 and highs[i] >= pullback_ema[i] and closes[i] < invalidation:
                pullback_hit = True
                break

        # Map history array index -> the bar_idx numbering the state
        # machine will use going forward (current bar becomes bar_idx 0).
        start_bar = breakout_idx - last_hist_idx - 1
        self._setup = {
            "direction": breakout_dir, "start_bar": start_bar,
            "invalidation": invalidation,
            "pullback_hit": pullback_hit, "completed": False,
            "id": start_bar,
        }

    @staticmethod
    def _quality_score(
        is_long: bool, bull_trend: bool, bear_trend: bool,
        body_size: float, atr_value: float, close: float, pullback_ema: float,
        lower_wick: float, upper_wick: float,
        use_mcginley: bool, mcginley_distance: float, mcginley_threshold: float,
        use_rsi: bool, rsi_value: float, rsi_long_min: float, rsi_short_max: float,
    ) -> float:
        """0-1 confidence score, ported from the original's 0-100
        f_qualityScore() so the relative weighting of trend / body-size /
        EMA-distance / wick / filter-quality stays the same."""
        score = 0.0
        if (is_long and bull_trend) or (not is_long and bear_trend):
            score += 0.25

        body_atr = (body_size / atr_value) if atr_value > 0 else 0.0
        if body_atr >= 0.75:
            score += 0.20
        elif body_atr >= 0.50:
            score += 0.16
        elif body_atr >= 0.35:
            score += 0.12
        elif body_atr >= 0.20:
            score += 0.08
        else:
            score += 0.04

        dist_atr = (abs(close - pullback_ema) / atr_value) if atr_value > 0 else 10.0
        if dist_atr <= 0.25:
            score += 0.20
        elif dist_atr <= 0.50:
            score += 0.16
        elif dist_atr <= 0.75:
            score += 0.12
        elif dist_atr <= 1.00:
            score += 0.08
        else:
            score += 0.04

        wick = lower_wick if is_long else upper_wick
        if wick >= body_size * 0.50:
            score += 0.10
        elif wick >= body_size * 0.25:
            score += 0.06
        else:
            score += 0.03

        if use_mcginley:
            if mcginley_distance >= mcginley_threshold * 1.5:
                score += 0.10
            elif mcginley_distance >= mcginley_threshold:
                score += 0.07
            else:
                score += 0.03
        else:
            score += 0.10

        if use_rsi:
            if is_long and rsi_value >= rsi_long_min + 5:
                score += 0.10
            elif is_long and rsi_value >= rsi_long_min:
                score += 0.07
            elif not is_long and rsi_value <= rsi_short_max - 5:
                score += 0.10
            elif not is_long and rsi_value <= rsi_short_max:
                score += 0.07
            else:
                score += 0.03
        else:
            score += 0.10

        return max(0.0, min(1.0, score + 0.05))
