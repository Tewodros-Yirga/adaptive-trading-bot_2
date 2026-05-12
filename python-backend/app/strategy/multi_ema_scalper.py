from .base import BaseStrategy

DEFAULT_PARAMS = {
    "ema_1": 5,
    "ema_2": 8,
    "ema_3": 13,
    "ema_4": 21,
    "ema_5": 34,
    "ema_6": 55,
    "stop_loss_pct": 0.12,
    "tp1_multiplier": 0.8,
    "tp2_multiplier": 1.5,
    "tp3_multiplier": 2.2,
    "tp4_multiplier": 3.0,
    "lot_size": 0.01,
    "min_stop_loss_pct": 0.05,
    "max_stop_loss_pct": 0.5,
    "min_tp_multiplier": 0.3,
    "max_tp_multiplier": 4.0,
    "min_ema_1": 3,
    "max_ema_1": 15,
    "min_ema_6": 30,
    "max_ema_6": 80,
}


class MultiEMAScalperStrategy(BaseStrategy):
    name = "Multi_EMA_Scalper"
    display_name = "Multi EMA Scalper"
    description = "Faster DTC variant with tighter multipliers and shorter EMA periods for scalping."

    @classmethod
    def default_params(cls) -> dict:
        return DEFAULT_PARAMS.copy()

    def signal(self, market_data: dict) -> str | None:
        ema_values = market_data.get("ema_values", {})
        if not ema_values:
            return None
        keys = ["ema_1", "ema_2", "ema_3", "ema_4", "ema_5", "ema_6"]
        if not all(k in ema_values for k in keys):
            return None
        bullish = all(ema_values[keys[i]] > ema_values[keys[i + 1]] for i in range(5))
        bearish = all(ema_values[keys[i]] < ema_values[keys[i + 1]] for i in range(5))
        prev_bull = market_data.get("previous_bull", False)
        prev_bear = market_data.get("previous_bear", False)
        if not prev_bull and bullish:
            return "BUY"
        if not prev_bear and bearish:
            return "SELL"
        return None

    def compute_levels(self, direction: str, price: float, params: dict) -> dict:
        sl_pct = float(params.get("stop_loss_pct", DEFAULT_PARAMS["stop_loss_pct"]))
        sl_dist = price * (sl_pct / 100.0)
        sign = 1 if direction == "BUY" else -1
        return {
            "sl": round(price - sign * sl_dist, 5),
            "tp1": round(price + sign * sl_dist * float(params.get("tp1_multiplier", 0.8)), 5),
            "tp2": round(price + sign * sl_dist * float(params.get("tp2_multiplier", 1.5)), 5),
            "tp3": round(price + sign * sl_dist * float(params.get("tp3_multiplier", 2.2)), 5),
            "tp4": round(price + sign * sl_dist * float(params.get("tp4_multiplier", 3.0)), 5),
        }