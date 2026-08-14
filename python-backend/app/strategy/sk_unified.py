"""
app/strategy/sk_unified.py — SK Unified Strategy

Merges the two prior SK implementations:

  - sk_sequence.py contributed: turning-area prominence check, HTF
    "timeframe puzzle" bias gate, a real BLASH percentile-of-range filter,
    a structure-shift breakout confirmation trigger, a B-point de-dup
    guard, ATR-based stops, and Fibonacci-extension take-profits measured
    from B (the actual "sequence target"). This is the more rigorous of
    the two and is used as the base here.

  - sk_strategy.py contributed two ideas worth keeping that sk_sequence.py
    lacked:
      1. A recency guard: sk_sequence.py had no limit on how old the A->B
         leg could be before it stopped being tradeable. We reintroduce
         sk_strategy's `confirmation_lookback` concept (renamed
         `max_sequence_age_bars` to avoid colliding with sk_sequence's own
         `confirmation_lookback`, which means something different there —
         the trigger window, not the leg's age).
      2. A candlestick reversal-quality signal (wick-to-body ratio, close
         beyond the prior close). Rather than using it as an alternate
         *gate* (which would let weaker, undconfirmed reversals trade —
         a regression vs. sk_sequence's stricter breakout requirement),
         we fold it in as an additional confidence *booster* on top of
         the breakout confirmation that remains the hard gate.

Everything else (pivot detection, turning-area check, retracement band,
HTF bias, BLASH, invalidation buffer, extension-based TP/SL) is carried
over from sk_sequence.py essentially unchanged.
"""
from __future__ import annotations

from typing import Any

from app.strategy.base import BaseStrategy


def _find_pivots(ohlcv: list[dict], strength: int) -> list[tuple[int, float, str]]:
    """Fractal pivot detector; collapses adjacent same-type pivots to the
    more extreme one so the result strictly alternates H/L."""
    n = len(ohlcv)
    pivots: list[tuple[int, float, str]] = []
    if n < strength * 2 + 1:
        return pivots

    highs = [float(b["high"]) for b in ohlcv]
    lows = [float(b["low"]) for b in ohlcv]

    for i in range(strength, n - strength):
        window_hi = highs[i - strength: i + strength + 1]
        window_lo = lows[i - strength: i + strength + 1]
        if highs[i] == max(window_hi):
            pivots.append((i, highs[i], "H"))
        elif lows[i] == min(window_lo):
            pivots.append((i, lows[i], "L"))

    collapsed: list[tuple[int, float, str]] = []
    for pt in pivots:
        if collapsed and collapsed[-1][2] == pt[2]:
            keep_existing = (
                (pt[2] == "H" and collapsed[-1][1] >= pt[1]) or
                (pt[2] == "L" and collapsed[-1][1] <= pt[1])
            )
            if not keep_existing:
                collapsed[-1] = pt
        else:
            collapsed.append(pt)
    return collapsed


def _ema_list(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


class SKUnifiedStrategy(BaseStrategy):
    name = "SK_Unified"
    display_name = "SK Unified (Kassing ABC + Fib Extension, merged)"
    description = (
        "Merged SK system: ABC corrective sequences at turning areas, "
        "Fibonacci-retracement B point, recency-limited leg age, HTF "
        "timeframe-puzzle bias, BLASH context filter, structure-shift "
        "breakout confirmation boosted by candlestick reversal quality, "
        "Fibonacci-extension take-profit targets."
    )
    is_adaptive = False
    requires_mtf = True  # orchestrator fetches 4h/1d bars for the HTF timeframe-puzzle bias

    DEFAULT_PARAMS: dict[str, Any] = {
        # Pivot / sequence detection
        "pivot_strength": 3,
        "turning_area_lookback": 40,
        "min_retracement_pct": 0.382,
        "max_retracement_pct": 0.886,
        "invalidation_buffer_pct": 0.10,
        # Recency guard (from sk_strategy.py's confirmation_lookback)
        "max_sequence_age_bars": 60,
        # HTF timeframe-puzzle bias
        "htf_ema_fast": 20,
        "htf_ema_slow": 50,
        # BLASH context filter
        "blash_enabled": 1,
        "blash_lookback": 250,
        "blash_cheap_pct": 35.0,
        "blash_expensive_pct": 65.0,
        # Structure-shift confirmation trigger
        "confirmation_lookback": 3,
        # Candlestick reversal-quality boost (from sk_strategy.py)
        "reversal_boost_weight": 0.10,  # max confidence added for a clean reversal candle
        "reversal_min_wick_ratio": 0.25,
        # Risk / targets
        "stop_buffer_atr": 0.25,
        "atr_period": 14,
        "tp1_extension": 0.618,
        "tp2_extension": 1.0,
        "tp3_extension": 1.272,
        "tp4_extension": 1.618,
        "min_confidence_threshold": 0.45,
    }

    PARAM_BOUNDS: dict[str, tuple[float, float]] = {
        "pivot_strength": (2, 6),
        "turning_area_lookback": (20, 100),
        "min_retracement_pct": (0.236, 0.5),
        "max_retracement_pct": (0.618, 0.95),
        "invalidation_buffer_pct": (0.02, 0.30),
        "max_sequence_age_bars": (20, 150),
        "htf_ema_fast": (10, 40),
        "htf_ema_slow": (30, 100),
        "blash_lookback": (100, 500),
        "blash_cheap_pct": (15.0, 45.0),
        "blash_expensive_pct": (55.0, 85.0),
        "confirmation_lookback": (1, 6),
        "reversal_boost_weight": (0.0, 0.20),
        "reversal_min_wick_ratio": (0.10, 0.50),
        "stop_buffer_atr": (0.05, 1.0),
        "atr_period": (7, 21),
        "tp1_extension": (0.382, 1.0),
        "tp2_extension": (0.618, 1.618),
        "tp3_extension": (1.0, 2.0),
        "tp4_extension": (1.272, 2.618),
        "min_confidence_threshold": (0.3, 0.75),
    }

    RESTART_SAMPLE_BOUNDS: dict[str, tuple[float, float]] = {
        "min_retracement_pct": (0.30, 0.42),
        "max_retracement_pct": (0.70, 0.82),
        "turning_area_lookback": (25, 60),
        "max_sequence_age_bars": (40, 90),
        "min_confidence_threshold": (0.3, 0.5),
    }

    def __init__(self, params: dict[str, Any] | None = None):
        super().__init__(params)
        self._last_sequence: dict | None = None
        self._last_signal_b_date: str | None = None

    @classmethod
    def default_params(cls) -> dict:
        return cls.DEFAULT_PARAMS.copy()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def signal(self, market_data: dict) -> tuple[str | None, float]:
        self._last_sequence = None

        ohlcv = market_data.get("ohlcv_window")
        if not ohlcv:
            return None, 0.0

        p = self.params
        strength = max(1, int(p.get("pivot_strength", 3)))
        lookback = int(p.get("turning_area_lookback", 40))
        min_bars = strength * 2 + 1 + lookback
        if len(ohlcv) < min_bars:
            return None, 0.0

        pivots = _find_pivots(ohlcv, strength)
        if len(pivots) < 3:
            return None, 0.0

        p0_idx, p0_price, p0_type = pivots[-3]
        a_idx, a_price, a_type = pivots[-2]
        b_idx, b_price, b_type = pivots[-1]

        if p0_type == a_type or a_type == b_type:
            return None, 0.0

        closes = [float(x["close"]) for x in ohlcv]
        price = closes[-1]
        n = len(ohlcv)

        # ── Recency guard (sk_strategy.py) — the A->B leg must still be
        # "fresh"; a sequence built off a stale impulsive leg is no longer
        # the market's live structure.
        max_age = int(p.get("max_sequence_age_bars", 60))
        if (n - 1) - a_idx > max_age:
            return None, 0.0

        # ── Turning-area check (sk_sequence.py) ───────────────────────────
        window_start = max(0, p0_idx - lookback)
        prior_window = ohlcv[window_start:p0_idx + 1]
        if not prior_window:
            return None, 0.0
        if p0_type == "L":
            is_turning_point = p0_price <= min(float(b["low"]) for b in prior_window)
        else:
            is_turning_point = p0_price >= max(float(b["high"]) for b in prior_window)
        if not is_turning_point:
            return None, 0.0

        leg1 = abs(a_price - p0_price)
        leg2 = abs(b_price - a_price)
        if leg1 <= 0:
            return None, 0.0

        retracement_pct = leg2 / leg1
        min_retr = float(p.get("min_retracement_pct", 0.382))
        max_retr = float(p.get("max_retracement_pct", 0.886))
        if not (min_retr <= retracement_pct <= max_retr):
            return None, 0.0

        if p0_type == "L" and a_type == "H" and b_type == "L":
            direction = "BUY"
            invalidation = a_price
        elif p0_type == "H" and a_type == "L" and b_type == "H":
            direction = "SELL"
            invalidation = a_price
        else:
            return None, 0.0

        buffer_pct = float(p.get("invalidation_buffer_pct", 0.10))
        invalidation_buffer = leg1 * buffer_pct
        if direction == "BUY" and price > invalidation + invalidation_buffer:
            return None, 0.0
        if direction == "SELL" and price < invalidation - invalidation_buffer:
            return None, 0.0

        # ── Structure-shift confirmation trigger off B (hard gate) ────────
        confirm_n = max(1, int(p.get("confirmation_lookback", 3)))
        post_b = ohlcv[b_idx + 1:]
        if len(post_b) < confirm_n + 1:
            return None, 0.0
        recent = post_b[-(confirm_n + 1):]
        confirm_window = recent[:-1]
        last_bar = recent[-1]

        if direction == "BUY":
            confirmed = float(last_bar["close"]) > max(float(x["high"]) for x in confirm_window)
        else:
            confirmed = float(last_bar["close"]) < min(float(x["low"]) for x in confirm_window)
        if not confirmed:
            return None, 0.0

        b_date = str(ohlcv[b_idx].get("date", b_idx))
        if self._last_signal_b_date == b_date:
            return None, 0.0

        # ── Timeframe puzzle: HTF bias must agree ─────────────────────────
        htf_bias = self._htf_bias(market_data, ohlcv)
        if htf_bias == "BULLISH" and direction == "SELL":
            return None, 0.0
        if htf_bias == "BEARISH" and direction == "BUY":
            return None, 0.0

        # ── BLASH context filter ───────────────────────────────────────
        if float(p.get("blash_enabled", 1)) >= 0.5:
            if not self._blash_allows(closes, direction):
                return None, 0.0

        # ── Confidence ──────────────────────────────────────────────────
        sweet_center = 0.667
        band_half = max((max_retr - min_retr) / 2.0, 1e-9)
        retr_quality = max(0.0, 1.0 - abs(retracement_pct - sweet_center) / band_half)
        turning_quality = min(1.0, (p0_idx - window_start) / max(lookback, 1))
        htf_quality = 1.0 if htf_bias != "NEUTRAL" else 0.5

        confidence = 0.15 + 0.45 * retr_quality + 0.20 * turning_quality + 0.20 * htf_quality

        # Candlestick reversal-quality boost (sk_strategy.py idea, applied
        # as a bonus on top of the breakout gate rather than as its own
        # gate — the breakout confirmation already did the hard work).
        boost_weight = float(p.get("reversal_boost_weight", 0.10))
        if boost_weight > 0:
            reversal_quality = self._reversal_quality(last_bar, confirm_window, direction, p)
            confidence += boost_weight * reversal_quality

        confidence = max(0.0, min(confidence, 1.0))
        if confidence < float(p.get("min_confidence_threshold", 0.45)):
            return None, 0.0

        self._last_signal_b_date = b_date
        self._last_sequence = {
            "direction": direction,
            "b_price": b_price,
            "leg2": leg2,
        }
        return direction, confidence

    def compute_levels(self, direction: str, price: float, params: dict) -> dict:
        if not direction:
            return {}
        seq = self._last_sequence
        if not seq or seq.get("direction") != direction:
            return {}

        params = self._sort_extensions(params)

        atr = float(params.get("atr", price * 0.005))
        stop_buffer = float(params.get("stop_buffer_atr", 0.25)) * atr
        b_price = seq["b_price"]
        leg2 = seq["leg2"]
        sign = 1 if direction == "BUY" else -1

        sl = b_price - sign * stop_buffer
        tp1 = b_price + sign * leg2 * float(params.get("tp1_extension", 0.618))
        tp2 = b_price + sign * leg2 * float(params.get("tp2_extension", 1.0))
        tp3 = b_price + sign * leg2 * float(params.get("tp3_extension", 1.272))
        tp4 = b_price + sign * leg2 * float(params.get("tp4_extension", 1.618))

        risk = abs(price - sl)
        reward = abs(tp2 - price)
        if risk <= 0 or reward <= 0:
            return {}

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

    @staticmethod
    def _sort_extensions(params: dict) -> dict:
        keys = ["tp1_extension", "tp2_extension", "tp3_extension", "tp4_extension"]
        present = [k for k in keys if k in params]
        if len(present) < 2:
            return params.copy()
        result = params.copy()
        running = result[present[0]]
        for k in present[1:]:
            running = max(result[k], running)
            result[k] = running
        return result

    @staticmethod
    def _reversal_quality(last_bar: dict, confirm_window: list[dict], direction: str, p: dict) -> float:
        """0..1 score for how clean the confirmation candle's reversal
        signature is (wick-to-body ratio + close beyond the prior close),
        mirroring sk_strategy.py's candlestick filter — used here as a
        confidence booster, not a gate."""
        cur_open = float(last_bar["open"])
        cur_close = float(last_bar["close"])
        cur_high = float(last_bar["high"])
        cur_low = float(last_bar["low"])
        body = abs(cur_close - cur_open)
        if body <= 0:
            body = 1e-9
        prev_close = float(confirm_window[-1]["close"]) if confirm_window else cur_open
        min_wick_ratio = float(p.get("reversal_min_wick_ratio", 0.25))

        if direction == "BUY":
            if cur_close <= cur_open or cur_close <= prev_close:
                return 0.0
            lower_wick = min(cur_open, cur_close) - cur_low
            wick_ratio = lower_wick / body
        else:
            if cur_close >= cur_open or cur_close >= prev_close:
                return 0.0
            upper_wick = cur_high - max(cur_open, cur_close)
            wick_ratio = upper_wick / body

        if wick_ratio < min_wick_ratio:
            return 0.0
        # Scale 0..1 as wick ratio runs from the minimum threshold up to 1.0
        return max(0.0, min(1.0, (wick_ratio - min_wick_ratio) / max(1.0 - min_wick_ratio, 1e-9)))

    def _htf_bias(self, market_data: dict, ohlcv_window: list[dict]) -> str:
        fast_p = int(self.params.get("htf_ema_fast", 20))
        slow_p = int(self.params.get("htf_ema_slow", 50))

        bars_4h = market_data.get("4h_bars")
        bars_1d = market_data.get("1d_bars")
        try:
            import pandas as pd
            for bars in (bars_4h, bars_1d):
                if bars is not None and isinstance(bars, pd.DataFrame) and len(bars) >= slow_p + 1:
                    close = bars["close"]
                    ema_fast = close.ewm(span=fast_p, adjust=False).mean().iloc[-1]
                    ema_slow = close.ewm(span=slow_p, adjust=False).mean().iloc[-1]
                    last_price = float(close.iloc[-1])
                    if last_price > ema_fast > ema_slow:
                        return "BULLISH"
                    if last_price < ema_fast < ema_slow:
                        return "BEARISH"
                    return "NEUTRAL"
        except ImportError:
            pass

        closes = [float(b["close"]) for b in ohlcv_window]
        if len(closes) < slow_p + 1:
            return "NEUTRAL"
        ema_fast = _ema_list(closes, fast_p)[-1]
        ema_slow = _ema_list(closes, slow_p)[-1]
        last_price = closes[-1]
        if last_price > ema_fast > ema_slow:
            return "BULLISH"
        if last_price < ema_fast < ema_slow:
            return "BEARISH"
        return "NEUTRAL"

    def _blash_allows(self, closes: list[float], direction: str) -> bool:
        lookback = int(self.params.get("blash_lookback", 250))
        window = closes[-min(lookback, len(closes)):]
        if len(window) < 10:
            return True
        lo, hi = min(window), max(window)
        span = (hi - lo) or 1e-9
        percentile = (window[-1] - lo) / span * 100.0
        if direction == "BUY":
            return percentile <= float(self.params.get("blash_cheap_pct", 35.0))
        return percentile >= float(self.params.get("blash_expensive_pct", 65.0))
