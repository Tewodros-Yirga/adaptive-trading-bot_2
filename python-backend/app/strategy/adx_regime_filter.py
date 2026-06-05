"""
app/strategy/adx_regime_filter.py — ADX Regime Filter Strategy

Classifies market regime (trending vs ranging) using ADX/+DI/-DI and emits
directional signals only in trending conditions.
"""
from typing import Any

from app.strategy.base import BaseStrategy


def _wilder_smooth(values: list[float], period: int) -> list[float]:
    """Wilder's smoothing (same as Wilder's ATR/ADX smoothing)."""
    if len(values) < period:
        return []
    result: list[float] = []
    # Seed with simple sum of first `period` values
    seed = sum(values[:period])
    result.append(seed)
    for v in values[period:]:
        smoothed = result[-1] - (result[-1] / period) + v
        result.append(smoothed)
    return result


def _compute_adx(ohlcv: list[dict], adx_period: int, di_period: int) -> tuple[float, float, float]:
    """
    Returns (adx, plus_di, minus_di) for the last bar.
    Returns (0.0, 0.0, 0.0) if not enough data.
    """
    needed = adx_period + di_period + 1
    if len(ohlcv) < needed:
        return 0.0, 0.0, 0.0

    tr_list: list[float] = []
    plus_dm_list: list[float] = []
    minus_dm_list: list[float] = []

    for i in range(1, len(ohlcv)):
        high = float(ohlcv[i]["high"])
        low = float(ohlcv[i]["low"])
        prev_close = float(ohlcv[i - 1]["close"])
        prev_high = float(ohlcv[i - 1]["high"])
        prev_low = float(ohlcv[i - 1]["low"])

        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_list.append(tr)

        up_move = high - prev_high
        down_move = prev_low - low

        plus_dm = up_move if up_move > down_move and up_move > 0 else 0.0
        minus_dm = down_move if down_move > up_move and down_move > 0 else 0.0
        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)

    smooth_tr = _wilder_smooth(tr_list, di_period)
    smooth_plus = _wilder_smooth(plus_dm_list, di_period)
    smooth_minus = _wilder_smooth(minus_dm_list, di_period)

    if not smooth_tr:
        return 0.0, 0.0, 0.0

    dx_list: list[float] = []
    for s_tr, s_plus, s_minus in zip(smooth_tr, smooth_plus, smooth_minus):
        if s_tr == 0:
            dx_list.append(0.0)
            continue
        p_di = 100.0 * s_plus / s_tr
        m_di = 100.0 * s_minus / s_tr
        denom = p_di + m_di
        dx = 100.0 * abs(p_di - m_di) / denom if denom != 0 else 0.0
        dx_list.append(dx)

    smooth_dx = _wilder_smooth(dx_list, adx_period)
    if not smooth_dx:
        return 0.0, 0.0, 0.0

    adx = smooth_dx[-1]
    last_tr = smooth_tr[-1]
    last_plus = smooth_plus[-1]
    last_minus = smooth_minus[-1]
    plus_di = 100.0 * last_plus / last_tr if last_tr != 0 else 0.0
    minus_di = 100.0 * last_minus / last_tr if last_tr != 0 else 0.0

    return adx, plus_di, minus_di


class ADXRegimeFilterStrategy(BaseStrategy):
    name = "ADX_Regime"
    display_name = "ADX Regime Filter"
    description = (
        "Classifies market regime using ADX/+DI/-DI. Emits directional signals in trending "
        "conditions; stays silent in ranging markets. Also exposes current_regime property."
    )
    is_adaptive = False

    DEFAULT_PARAMS: dict[str, Any] = {
        "adx_period": 14,
        "trend_threshold": 25.0,
        "range_threshold": 20.0,
        "di_period": 14,
        "stop_loss_pct": 0.3,
        "tp1_multiplier": 1.5,
        "tp2_multiplier": 2.5,
        "tp3_multiplier": 3.5,
        "tp4_multiplier": 5.0,
    }

    PARAM_BOUNDS: dict[str, tuple[float, float]] = {
        "adx_period": (7, 30),
        "trend_threshold": (15.0, 40.0),
        "range_threshold": (10.0, 30.0),
        "di_period": (7, 30),
        "stop_loss_pct": (0.1, 1.5),
        "tp1_multiplier": (0.5, 8.0),
        "tp2_multiplier": (0.5, 8.0),
        "tp3_multiplier": (0.5, 8.0),
        "tp4_multiplier": (0.5, 8.0),
    }

    def __init__(self, params: dict[str, Any] | None = None):
        super().__init__(params)
        self._current_regime: str = "RANGING"

    @classmethod
    def default_params(cls) -> dict:
        return cls.DEFAULT_PARAMS.copy()

    @property
    def current_regime(self) -> str:
        """Returns 'TRENDING_BULL', 'TRENDING_BEAR', or 'RANGING'."""
        return self._current_regime

    def signal(self, market_data: dict) -> tuple[str | None, float]:
        ohlcv = market_data.get("ohlcv_window")
        if not ohlcv:
            self._current_regime = "RANGING"
            return None, 0.0

        adx_period = int(self.params.get("adx_period", 14))
        di_period = int(self.params.get("di_period", 14))
        trend_threshold = float(self.params.get("trend_threshold", 25.0))

        adx, plus_di, minus_di = _compute_adx(ohlcv, adx_period, di_period)

        if adx > trend_threshold:
            if plus_di > minus_di:
                self._current_regime = "TRENDING_BULL"
                return "BUY", min(adx / 100.0, 1.0)
            else:
                self._current_regime = "TRENDING_BEAR"
                return "SELL", min(adx / 100.0, 1.0)

        self._current_regime = "RANGING"
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