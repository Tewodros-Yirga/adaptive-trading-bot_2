from .base import BaseStrategy

DEFAULT_PARAMS = {
    "bb_period": 20,
    "bb_std": 2.0,
    "atr_period": 14,
    "atr_sl_multiplier": 1.5,
    "tp1_multiplier": 1.0,
    "tp2_multiplier": 2.0,
    "tp3_multiplier": 3.0,
    "tp4_multiplier": 4.0,
    "lot_size": 0.01,
    "min_atr_sl_multiplier": 0.5,
    "max_atr_sl_multiplier": 4.0,
    "min_tp_multiplier": 0.5,
    "max_tp_multiplier": 8.0,
}


class BollingerBreakoutStrategy(BaseStrategy):
    name = "Bollinger_Breakout"
    display_name = "Bollinger Breakout"
    description = "Price breakout above/below Bollinger Bands with ATR-based stop loss."

    PARAM_BOUNDS: dict[str, tuple[float, float]] = {
        "bb_period": (10, 50),
        "bb_std": (1.0, 4.0),
        "atr_period": (5, 30),
        "atr_sl_multiplier": (0.5, 4.0),
        "tp1_multiplier": (0.5, 8.0),
        "tp2_multiplier": (0.5, 8.0),
        "tp3_multiplier": (0.5, 8.0),
        "tp4_multiplier": (0.5, 8.0),
    }

    @classmethod
    def default_params(cls) -> dict:
        return DEFAULT_PARAMS.copy()

    def signal(self, market_data: dict) -> tuple[str | None, float]:
        """BUG-02: Returns (direction, confidence) tuple."""
        price = market_data.get("price")
        upper_band = market_data.get("bb_upper")
        lower_band = market_data.get("bb_lower")
        prev_price = market_data.get("prev_price")
        prev_upper = market_data.get("prev_bb_upper")
        prev_lower = market_data.get("prev_bb_lower")
        if None in (price, upper_band, lower_band):
            return None, 0.0
        if prev_price is not None and prev_upper is not None and prev_lower is not None:
            if prev_price <= prev_upper and price > upper_band:
                return "BUY", 1.0
            if prev_price >= prev_lower and price < lower_band:
                return "SELL", 1.0
        return None, 0.0

    def compute_levels(self, direction: str, price: float, params: dict) -> dict:
        # BUG-08: ensure TP multipliers are in ascending order before computing levels
        params = self._sort_tp_multipliers(params)
        atr = params.get("atr", price * 0.003)
        atr_mult = float(params.get("atr_sl_multiplier", DEFAULT_PARAMS["atr_sl_multiplier"]))
        sl_dist = float(atr) * atr_mult
        sign = 1 if direction == "BUY" else -1
        return {
            "sl": round(price - sign * sl_dist, 5),
            "tp1": round(price + sign * sl_dist * float(params.get("tp1_multiplier", 1.0)), 5),
            "tp2": round(price + sign * sl_dist * float(params.get("tp2_multiplier", 2.0)), 5),
            "tp3": round(price + sign * sl_dist * float(params.get("tp3_multiplier", 3.0)), 5),
            "tp4": round(price + sign * sl_dist * float(params.get("tp4_multiplier", 4.0)), 5),
        }