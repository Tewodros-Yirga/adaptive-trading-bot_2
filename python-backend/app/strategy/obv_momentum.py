"""
app/strategy/obv_momentum.py — OBV Momentum Strategy

On-Balance Volume trend confirmation. Signals when OBV trend aligns with price trend.
"""
from typing import Any

from app.strategy.base import BaseStrategy


def _ema(values: list[float], period: int) -> list[float]:
    """Simple EMA over a list of floats. Returns same-length list (first period-1 are seeded)."""
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


class OBVMomentumStrategy(BaseStrategy):
    name = "OBV_Momentum"
    display_name = "OBV Momentum"
    description = (
        "On-Balance Volume momentum confirmation. Signals on OBV EMA crossover "
        "when aligned with price trend. Used as a confirming vote in ensembles."
    )
    is_adaptive = False

    DEFAULT_PARAMS: dict[str, Any] = {
        "obv_ema_fast": 5,
        "obv_ema_slow": 20,
        "price_ema_period": 20,
        "min_obv_divergence_pct": 2.0,
        "stop_loss_pct": 0.3,
        "tp1_multiplier": 1.5,
        "tp2_multiplier": 2.5,
        "tp3_multiplier": 3.5,
        "tp4_multiplier": 5.0,
    }

    PARAM_BOUNDS: dict[str, tuple[float, float]] = {
        "obv_ema_fast": (3, 15),
        "obv_ema_slow": (10, 50),
        "price_ema_period": (10, 50),
        "min_obv_divergence_pct": (0.5, 10.0),
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

        fast_period = int(self.params.get("obv_ema_fast", 5))
        slow_period = int(self.params.get("obv_ema_slow", 20))
        price_period = int(self.params.get("price_ema_period", 20))
        min_div_pct = float(self.params.get("min_obv_divergence_pct", 2.0))

        min_bars = slow_period + 5
        if len(ohlcv) < min_bars:
            return None, 0.0

        closes: list[float] = [float(b["close"]) for b in ohlcv]
        volumes: list[float] = [float(b.get("volume", 0.0)) for b in ohlcv]

        # Compute OBV
        obv: list[float] = [0.0]
        for i in range(1, len(closes)):
            if closes[i] > closes[i - 1]:
                obv.append(obv[-1] + volumes[i])
            elif closes[i] < closes[i - 1]:
                obv.append(obv[-1] - volumes[i])
            else:
                obv.append(obv[-1])

        obv_fast_series = _ema(obv, fast_period)
        obv_slow_series = _ema(obv, slow_period)
        price_ema_series = _ema(closes, price_period)

        if len(obv_fast_series) < 2 or len(obv_slow_series) < 2:
            return None, 0.0

        obv_fast_now = obv_fast_series[-1]
        obv_fast_prev = obv_fast_series[-2]
        obv_slow_now = obv_slow_series[-1]
        obv_slow_prev = obv_slow_series[-2]
        price_now = closes[-1]
        price_ema_now = price_ema_series[-1]

        if obv_slow_now == 0:
            return None, 0.0

        obv_div_pct = abs(obv_fast_now - obv_slow_now) / abs(obv_slow_now) * 100.0

        if obv_div_pct < min_div_pct:
            return None, 0.0

        confidence = min(obv_div_pct / 10.0, 1.0)

        # BUY: fast crossed above slow and price above price EMA
        if obv_fast_prev <= obv_slow_prev and obv_fast_now > obv_slow_now and price_now > price_ema_now:
            return "BUY", confidence

        # SELL: fast crossed below slow and price below price EMA
        if obv_fast_prev >= obv_slow_prev and obv_fast_now < obv_slow_now and price_now < price_ema_now:
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