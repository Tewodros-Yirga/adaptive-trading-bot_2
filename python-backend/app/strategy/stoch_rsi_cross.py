"""
app/strategy/stoch_rsi_cross.py — Stochastic RSI Cross Strategy

Stochastic RSI detects overbought/oversold conditions. Signals on %K/%D crossover
inside extreme zones.
"""
from typing import Any

from app.strategy.base import BaseStrategy


def _rsi(closes: list[float], period: int) -> list[float]:
    """Returns RSI series (same length as closes, first `period` values are 0.0)."""
    if len(closes) < period + 1:
        return [0.0] * len(closes)

    rsi_vals = [0.0] * period
    gains = []
    losses = []
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    def _rsi_from_avg(ag: float, al: float) -> float:
        if al == 0:
            return 100.0
        rs = ag / al
        return 100.0 - (100.0 / (1.0 + rs))

    rsi_vals.append(_rsi_from_avg(avg_gain, avg_loss))

    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gain = max(diff, 0.0)
        loss = max(-diff, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        rsi_vals.append(_rsi_from_avg(avg_gain, avg_loss))

    return rsi_vals


def _sma(values: list[float], period: int) -> list[float]:
    """Rolling SMA. Returns same-length list; first period-1 entries mirror first valid SMA."""
    if not values or period <= 0:
        return values[:]
    result: list[float] = []
    for i in range(len(values)):
        if i < period - 1:
            result.append(sum(values[: i + 1]) / (i + 1))
        else:
            result.append(sum(values[i - period + 1 : i + 1]) / period)
    return result


class StochRSICrossStrategy(BaseStrategy):
    name = "StochRSI_Cross"
    display_name = "Stochastic RSI Cross"
    description = (
        "Stochastic RSI signals on %K/%D crossover inside overbought/oversold zones. "
        "More sensitive than raw RSI for mean-reversion entries."
    )
    is_adaptive = False

    DEFAULT_PARAMS: dict[str, Any] = {
        "rsi_period": 14,
        "stoch_period": 14,
        "smooth_k": 3,
        "smooth_d": 3,
        "oversold_threshold": 20.0,
        "overbought_threshold": 80.0,
        "stop_loss_pct": 0.3,
        "tp1_multiplier": 1.5,
        "tp2_multiplier": 2.5,
        "tp3_multiplier": 3.5,
        "tp4_multiplier": 5.0,
    }

    PARAM_BOUNDS: dict[str, tuple[float, float]] = {
        "rsi_period": (5, 30),
        "stoch_period": (5, 30),
        "smooth_k": (1, 10),
        "smooth_d": (1, 10),
        "oversold_threshold": (5.0, 35.0),
        "overbought_threshold": (65.0, 95.0),
        "stop_loss_pct": (0.1, 1.5),
        "tp1_multiplier": (0.5, 8.0),
        "tp2_multiplier": (0.5, 8.0),
        "tp3_multiplier": (0.5, 8.0),
        "tp4_multiplier": (0.5, 8.0),
    }

    @classmethod
    def default_params(cls) -> dict:
        return cls.DEFAULT_PARAMS.copy()

    def signal(self, market_data: dict) -> tuple[str | None, float]:
        ohlcv = market_data.get("ohlcv_window")
        if not ohlcv:
            return None, 0.0

        rsi_period = int(self.params.get("rsi_period", 14))
        stoch_period = int(self.params.get("stoch_period", 14))
        smooth_k = int(self.params.get("smooth_k", 3))
        smooth_d = int(self.params.get("smooth_d", 3))
        oversold = float(self.params.get("oversold_threshold", 20.0))
        overbought = float(self.params.get("overbought_threshold", 80.0))

        min_bars = rsi_period + stoch_period + smooth_k + smooth_d + 5
        if len(ohlcv) < min_bars:
            return None, 0.0

        closes = [float(b["close"]) for b in ohlcv]

        rsi_series = _rsi(closes, rsi_period)

        # Stochastic of RSI
        stoch_k_raw: list[float] = []
        for i in range(len(rsi_series)):
            if i < stoch_period - 1:
                stoch_k_raw.append(50.0)  # neutral placeholder
                continue
            window = rsi_series[i - stoch_period + 1 : i + 1]
            lo = min(window)
            hi = max(window)
            denom = hi - lo
            if denom == 0:
                stoch_k_raw.append(50.0)
            else:
                stoch_k_raw.append((rsi_series[i] - lo) / denom * 100.0)

        pct_k_series = _sma(stoch_k_raw, smooth_k)
        pct_d_series = _sma(pct_k_series, smooth_d)

        if len(pct_k_series) < 2 or len(pct_d_series) < 2:
            return None, 0.0

        k_now = pct_k_series[-1]
        k_prev = pct_k_series[-2]
        d_now = pct_d_series[-1]
        d_prev = pct_d_series[-2]

        # BUY: %K crossed above %D, both previously in oversold zone
        if k_prev <= d_prev and k_now > d_now and k_prev < oversold and d_prev < oversold:
            avg_kd = (k_now + d_now) / 2.0
            confidence = max(0.0, min(1.0, 1.0 - avg_kd / oversold))
            return "BUY", confidence

        # SELL: %K crossed below %D, both previously in overbought zone
        if k_prev >= d_prev and k_now < d_now and k_prev > overbought and d_prev > overbought:
            avg_kd = (k_now + d_now) / 2.0
            confidence = max(0.0, min(1.0, (avg_kd - overbought) / (100.0 - overbought)))
            return "SELL", confidence

        return None, 0.0

    def compute_levels(self, direction: str, price: float, params: dict) -> dict:
        if not direction:
            return {}
        params = self._sort_tp_multipliers(params)
        sl_pct = float(params.get("stop_loss_pct", 0.3))
        sl_dist = price * (sl_pct / 100.0)
        sign = 1 if direction == "BUY" else -1
        return {
            "sl":  round(price - sign * sl_dist, 5),
            "tp1": round(price + sign * sl_dist * float(params.get("tp1_multiplier", 1.5)), 5),
            "tp2": round(price + sign * sl_dist * float(params.get("tp2_multiplier", 2.5)), 5),
            "tp3": round(price + sign * sl_dist * float(params.get("tp3_multiplier", 3.5)), 5),
            "tp4": round(price + sign * sl_dist * float(params.get("tp4_multiplier", 5.0)), 5),
        }