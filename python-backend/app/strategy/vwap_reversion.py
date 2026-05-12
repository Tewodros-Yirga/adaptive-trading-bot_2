from .base import BaseStrategy

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

    @classmethod
    def default_params(cls) -> dict:
        return DEFAULT_PARAMS.copy()

    def signal(self, market_data: dict) -> str | None:
        price = market_data.get("price")
        vwap = market_data.get("vwap")
        if price is None or vwap is None:
            return None
        dev_pct = float(self.params.get("vwap_deviation_pct", 0.3))
        deviation = (price - vwap) / vwap * 100
        if deviation < -dev_pct:
            return "BUY"
        if deviation > dev_pct:
            return "SELL"
        return None

    def compute_levels(self, direction: str, price: float, params: dict) -> dict:
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