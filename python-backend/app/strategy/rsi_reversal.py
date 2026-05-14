from .base import BaseStrategy

DEFAULT_PARAMS = {
    "rsi_period": 14,
    "oversold_threshold": 30.0,
    "overbought_threshold": 70.0,
    "stop_loss_pct": 0.3,
    "tp1_multiplier": 1.5,
    "tp2_multiplier": 2.5,
    "tp3_multiplier": 3.5,
    "tp4_multiplier": 5.0,
    "lot_size": 0.01,
    "min_stop_loss_pct": 0.1,
    "max_stop_loss_pct": 2.0,
    "min_tp_multiplier": 0.5,
    "max_tp_multiplier": 8.0,
}


class RSIReversalStrategy(BaseStrategy):
    name = "RSI_Reversal"
    display_name = "RSI Reversal"
    description = "Counter-trend entries based on RSI overbought/oversold conditions."

    PARAM_BOUNDS: dict[str, tuple[float, float]] = {
        "rsi_period": (5, 30),
        "oversold_threshold": (10.0, 45.0),
        "overbought_threshold": (55.0, 90.0),
        "stop_loss_pct": (0.1, 2.0),
        "tp1_multiplier": (0.5, 8.0),
        "tp2_multiplier": (0.5, 8.0),
        "tp3_multiplier": (0.5, 8.0),
        "tp4_multiplier": (0.5, 8.0),
    }

    @classmethod
    def default_params(cls) -> dict:
        return DEFAULT_PARAMS.copy()

    def signal(self, market_data: dict) -> str | None:
        rsi = market_data.get("rsi")
        if rsi is None:
            return None
        oversold = float(self.params.get("oversold_threshold", 30.0))
        overbought = float(self.params.get("overbought_threshold", 70.0))
        prev_rsi = market_data.get("prev_rsi")
        if prev_rsi is not None:
            if prev_rsi <= oversold and rsi > oversold:
                return "BUY"
            if prev_rsi >= overbought and rsi < overbought:
                return "SELL"
        else:
            if rsi < oversold:
                return "BUY"
            if rsi > overbought:
                return "SELL"
        return None

    def compute_levels(self, direction: str, price: float, params: dict) -> dict:
        sl_pct = float(params.get("stop_loss_pct", DEFAULT_PARAMS["stop_loss_pct"]))
        sl_dist = price * (sl_pct / 100.0)
        sign = 1 if direction == "BUY" else -1
        return {
            "sl": round(price - sign * sl_dist, 5),
            "tp1": round(price + sign * sl_dist * float(params.get("tp1_multiplier", 1.5)), 5),
            "tp2": round(price + sign * sl_dist * float(params.get("tp2_multiplier", 2.5)), 5),
            "tp3": round(price + sign * sl_dist * float(params.get("tp3_multiplier", 3.5)), 5),
            "tp4": round(price + sign * sl_dist * float(params.get("tp4_multiplier", 5.0)), 5),
        }