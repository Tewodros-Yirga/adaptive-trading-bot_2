import logging

from .base import BaseStrategy

logger = logging.getLogger(__name__)

DEFAULT_PARAMS = {
    "vwap_deviation_pct": 0.3,
    "reversion_strength": 1.0,
    "stop_loss_pct": 0.2,
    "tp1_multiplier": 1.0,
    "tp2_multiplier": 2.0,
    "tp3_multiplier": 3.0,
    "tp4_multiplier": 4.0,
    "lot_size": 0.01,
    "min_stop_loss_pct": 0.05,
    "max_stop_loss_pct": 1.0,
    "min_tp_multiplier": 0.5,
    "max_tp_multiplier": 6.0,
}


class VWAPReversionStrategy(BaseStrategy):
    name = "VWAP_Reversion"
    display_name = "VWAP Reversion"
    description = "Mean-reversion entries based on price deviation from VWAP."

    PARAM_BOUNDS: dict[str, tuple[float, float]] = {
        "vwap_deviation_pct": (0.05, 2.0),
        "reversion_strength": (0.1, 3.0),
        "stop_loss_pct": (0.05, 1.0),
        "tp1_multiplier": (0.5, 6.0),
        "tp2_multiplier": (0.5, 6.0),
        "tp3_multiplier": (0.5, 6.0),
        "tp4_multiplier": (0.5, 6.0),
    }

    @classmethod
    def default_params(cls) -> dict:
        return DEFAULT_PARAMS.copy()

    def signal(self, market_data: dict) -> tuple[str | None, float]:
        """
        BUG-02: Returns (direction, confidence) tuple.
        BUG-04: Detects the crossing event (first bar the condition becomes true)
                rather than firing on every bar the condition holds.
                Uses prev_price/prev_vwap from market_data for crossover detection.
                Falls back to simple threshold check if prev_price is unavailable.
        """
        price = market_data.get("price")
        vwap = market_data.get("vwap")
        if price is None or vwap is None:
            return None, 0.0

        dev_pct = float(self.params.get("vwap_deviation_pct", 0.3))
        deviation = (price - vwap) / vwap * 100

        prev_price = market_data.get("prev_price")
        prev_vwap = market_data.get("prev_vwap")

        if prev_price is not None and prev_vwap is not None:
            # BUG-04 FIX: fire only on the crossing event, not on every bar.
            prev_deviation = (prev_price - prev_vwap) / max(prev_vwap, 1e-9) * 100
            # BUY: price just crossed BELOW the -dev_pct threshold (wasn't there before)
            if prev_deviation >= -dev_pct and deviation < -dev_pct:
                return "BUY", 1.0
            # SELL: price just crossed ABOVE the +dev_pct threshold (wasn't there before)
            if prev_deviation <= dev_pct and deviation > dev_pct:
                return "SELL", 1.0
        else:
            # Fallback: no previous data available — use simple threshold (original behaviour)
            logger.debug(
                "VWAPReversionStrategy: prev_price/prev_vwap not in market_data; "
                "falling back to non-crossover signal logic."
            )
            if deviation < -dev_pct:
                return "BUY", 1.0
            if deviation > dev_pct:
                return "SELL", 1.0

        return None, 0.0

    def compute_levels(self, direction: str, price: float, params: dict) -> dict:
        # BUG-08: ensure TP multipliers are in ascending order before computing levels
        params = self._sort_tp_multipliers(params)
        sl_pct = float(params.get("stop_loss_pct", DEFAULT_PARAMS["stop_loss_pct"]))
        sl_dist = price * (sl_pct / 100.0)
        sign = 1 if direction == "BUY" else -1
        return {
            "sl": round(price - sign * sl_dist, 5),
            "tp1": round(price + sign * sl_dist * float(params.get("tp1_multiplier", 1.0)), 5),
            "tp2": round(price + sign * sl_dist * float(params.get("tp2_multiplier", 2.0)), 5),
            "tp3": round(price + sign * sl_dist * float(params.get("tp3_multiplier", 3.0)), 5),
            "tp4": round(price + sign * sl_dist * float(params.get("tp4_multiplier", 4.0)), 5),
        }