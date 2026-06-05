"""
app/strategy/htf_structure.py — Higher Timeframe Structure Strategy

Determines HTF (4H/Daily) swing structure and only signals in the direction of the
HTF trend. Extracted from Alchemist's _htf_bias() logic as a standalone strategy.
"""
from typing import Any

import pandas as pd

from app.strategy.base import BaseStrategy


def _ema_series_from_list(values: list[float], period: int) -> list[float]:
    """EMA over a plain list of floats."""
    if not values:
        return []
    k = 2.0 / (period + 1)
    result: list[float] = []
    for v in values:
        if not result:
            result.append(v)
        else:
            result.append(v * k + result[-1] * (1 - k))
    return result


def _ema_series_from_series(series: "pd.Series", period: int) -> "pd.Series":
    return series.ewm(span=period, adjust=False).mean()


class HTFStructureStrategy(BaseStrategy):
    name = "HTF_Structure"
    display_name = "Higher Timeframe Structure"
    description = (
        "Determines HTF swing structure using EMA cascade and HH/HL or LH/LL detection. "
        "Signals only in the direction of the higher-timeframe trend."
    )
    is_adaptive = False

    DEFAULT_PARAMS: dict[str, Any] = {
        "htf_ema_fast": 20,
        "htf_ema_slow": 50,
        "swing_lookback": 10,
        "structure_confirmation": 2,
        "stop_loss_pct": 0.4,
        "tp1_multiplier": 1.5,
        "tp2_multiplier": 2.5,
        "tp3_multiplier": 3.5,
        "tp4_multiplier": 5.0,
    }

    PARAM_BOUNDS: dict[str, tuple[float, float]] = {
        "htf_ema_fast": (5, 50),
        "htf_ema_slow": (20, 200),
        "swing_lookback": (5, 30),
        "structure_confirmation": (1, 5),
        "stop_loss_pct": (0.1, 2.0),
        "tp1_multiplier": (0.5, 8.0),
        "tp2_multiplier": (0.5, 8.0),
        "tp3_multiplier": (0.5, 8.0),
        "tp4_multiplier": (0.5, 8.0),
    }

    @classmethod
    def default_params(cls) -> dict:
        return cls.DEFAULT_PARAMS.copy()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _bias_from_dataframe(
        self,
        bars: pd.DataFrame,
        fast_period: int,
        slow_period: int,
        confirm_bars: int,
        swing_lookback: int,
    ) -> tuple[str, bool]:
        """
        Returns (ema_bias, swing_confirms) where ema_bias is 'BULLISH', 'BEARISH', or 'NEUTRAL'.
        swing_confirms is True when swing structure agrees with ema_bias.
        """
        if bars.empty or len(bars) < slow_period + 1:
            return "NEUTRAL", False

        close = bars["close"]
        ema_fast_s = _ema_series_from_series(close, fast_period)
        ema_slow_s = _ema_series_from_series(close, slow_period)

        ema_fast_vals = ema_fast_s.values
        ema_slow_vals = ema_slow_s.values
        close_vals = close.values

        if len(close_vals) < confirm_bars:
            return "NEUTRAL", False

        # EMA structure over last confirm_bars bars
        tail_close = close_vals[-confirm_bars:]
        tail_fast = ema_fast_vals[-confirm_bars:]
        tail_slow = ema_slow_vals[-confirm_bars:]

        ema_fast_last = float(ema_fast_vals[-1])
        ema_slow_last = float(ema_slow_vals[-1])

        bullish_ema = (
            all(float(c) > float(f) and float(c) > float(s) for c, f, s in zip(tail_close, tail_fast, tail_slow))
            and ema_fast_last > ema_slow_last
        )
        bearish_ema = (
            all(float(c) < float(f) and float(c) < float(s) for c, f, s in zip(tail_close, tail_fast, tail_slow))
            and ema_fast_last < ema_slow_last
        )

        ema_bias = "NEUTRAL"
        if bullish_ema:
            ema_bias = "BULLISH"
        elif bearish_ema:
            ema_bias = "BEARISH"

        # Swing structure
        swing_confirms = False
        if len(bars) >= swing_lookback * 2 and ema_bias != "NEUTRAL":
            highs = bars["high"].values.astype(float)
            lows = bars["low"].values.astype(float)
            mid = len(highs) - swing_lookback
            prior_swing_high = float(highs[mid - swing_lookback : mid].max())
            last_swing_high = float(highs[mid:].max())
            prior_swing_low = float(lows[mid - swing_lookback : mid].min())
            last_swing_low = float(lows[mid:].min())

            if ema_bias == "BULLISH":
                swing_confirms = last_swing_high > prior_swing_high and last_swing_low > prior_swing_low
            else:
                swing_confirms = last_swing_low < prior_swing_low and last_swing_high < prior_swing_high

        return ema_bias, swing_confirms

    def _bias_from_list(
        self,
        ohlcv: list[dict],
        fast_period: int,
        slow_period: int,
        confirm_bars: int,
        swing_lookback: int,
    ) -> tuple[str, bool]:
        if len(ohlcv) < slow_period + 1:
            return "NEUTRAL", False

        closes = [float(b["close"]) for b in ohlcv]
        highs = [float(b["high"]) for b in ohlcv]
        lows = [float(b["low"]) for b in ohlcv]

        ema_fast_s = _ema_series_from_list(closes, fast_period)
        ema_slow_s = _ema_series_from_list(closes, slow_period)

        if len(closes) < confirm_bars:
            return "NEUTRAL", False

        tail_close = closes[-confirm_bars:]
        tail_fast = ema_fast_s[-confirm_bars:]
        tail_slow = ema_slow_s[-confirm_bars:]

        ema_fast_last = ema_fast_s[-1]
        ema_slow_last = ema_slow_s[-1]

        bullish_ema = (
            all(c > f and c > s for c, f, s in zip(tail_close, tail_fast, tail_slow))
            and ema_fast_last > ema_slow_last
        )
        bearish_ema = (
            all(c < f and c < s for c, f, s in zip(tail_close, tail_fast, tail_slow))
            and ema_fast_last < ema_slow_last
        )

        ema_bias = "NEUTRAL"
        if bullish_ema:
            ema_bias = "BULLISH"
        elif bearish_ema:
            ema_bias = "BEARISH"

        swing_confirms = False
        if len(ohlcv) >= swing_lookback * 2 and ema_bias != "NEUTRAL":
            mid = len(ohlcv) - swing_lookback
            prior_swing_high = max(highs[mid - swing_lookback : mid])
            last_swing_high = max(highs[mid:])
            prior_swing_low = min(lows[mid - swing_lookback : mid])
            last_swing_low = min(lows[mid:])

            if ema_bias == "BULLISH":
                swing_confirms = last_swing_high > prior_swing_high and last_swing_low > prior_swing_low
            else:
                swing_confirms = last_swing_low < prior_swing_low and last_swing_high < prior_swing_high

        return ema_bias, swing_confirms

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def signal(self, market_data: dict) -> tuple[str | None, float]:
        fast_period = int(self.params.get("htf_ema_fast", 20))
        slow_period = int(self.params.get("htf_ema_slow", 50))
        confirm_bars = int(self.params.get("structure_confirmation", 2))
        swing_lookback = int(self.params.get("swing_lookback", 10))

        using_intraday_fallback = False

        # Try DataFrame sources first
        bars_4h = market_data.get("4h_bars")
        bars_1d = market_data.get("1d_bars")

        bars_df: pd.DataFrame | None = None
        if bars_4h is not None and isinstance(bars_4h, pd.DataFrame) and not bars_4h.empty:
            bars_df = bars_4h
        elif bars_1d is not None and isinstance(bars_1d, pd.DataFrame) and not bars_1d.empty:
            bars_df = bars_1d

        if bars_df is not None:
            ema_bias, swing_confirms = self._bias_from_dataframe(
                bars_df, fast_period, slow_period, confirm_bars, swing_lookback
            )
        else:
            # Fall back to ohlcv_window
            ohlcv = market_data.get("ohlcv_window")
            if not ohlcv:
                return None, 0.0
            using_intraday_fallback = True
            ema_bias, swing_confirms = self._bias_from_list(
                ohlcv, fast_period, slow_period, confirm_bars, swing_lookback
            )

        if ema_bias == "NEUTRAL":
            return None, 0.0

        base_confidence = 1.0 if swing_confirms else 0.7
        if using_intraday_fallback:
            base_confidence = min(base_confidence, 0.5)

        if ema_bias == "BULLISH":
            return "BUY", base_confidence
        else:
            return "SELL", base_confidence

    def compute_levels(self, direction: str, price: float, params: dict) -> dict:
        if not direction:
            return {}
        params = self._sort_tp_multipliers(params)
        sl_pct = float(params.get("stop_loss_pct", 0.4))
        sl_dist = price * (sl_pct / 100.0)
        sign = 1 if direction == "BUY" else -1
        return {
            "sl":  round(price - sign * sl_dist, 5),
            "tp1": round(price + sign * sl_dist * float(params.get("tp1_multiplier", 1.5)), 5),
            "tp2": round(price + sign * sl_dist * float(params.get("tp2_multiplier", 2.5)), 5),
            "tp3": round(price + sign * sl_dist * float(params.get("tp3_multiplier", 3.5)), 5),
            "tp4": round(price + sign * sl_dist * float(params.get("tp4_multiplier", 5.0)), 5),
        }