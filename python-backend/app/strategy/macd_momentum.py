from .base import BaseStrategy

DEFAULT_PARAMS = {
    "fast_period": 12,
    "slow_period": 26,
    "signal_period": 9,
    "histogram_threshold": 0.0,
    "stop_loss_pct": 0.35,
    "tp1_multiplier": 1.2,
    "tp2_multiplier": 2.2,
    "tp3_multiplier": 3.2,
    "tp4_multiplier": 4.5,
    "lot_size": 0.01,
    "min_stop_loss_pct": 0.1,
    "max_stop_loss_pct": 2.0,
    "min_tp_multiplier": 0.5,
    "max_tp_multiplier": 8.0,
}


class MACDMomentumStrategy(BaseStrategy):
    name = "MACD_Momentum"
    display_name = "MACD Momentum"
    description = "MACD line crossover signal with histogram confirmation for momentum trades."

    PARAM_BOUNDS: dict[str, tuple[float, float]] = {
        "fast_period": (5, 20),
        "slow_period": (15, 50),
        "signal_period": (3, 20),
        "histogram_threshold": (0.0, 0.01),
        "stop_loss_pct": (0.1, 2.0),
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
        macd_line = market_data.get("macd_line")
        signal_line = market_data.get("macd_signal")
        histogram = market_data.get("macd_histogram", 0)
        prev_macd = market_data.get("prev_macd_line")
        prev_signal = market_data.get("prev_macd_signal")
        if macd_line is None or signal_line is None:
            return None, 0.0
        hist_thresh = float(self.params.get("histogram_threshold", 0.0))
        if prev_macd is not None and prev_signal is not None:
            crossed_up = prev_macd <= prev_signal and macd_line > signal_line
            crossed_down = prev_macd >= prev_signal and macd_line < signal_line
            if crossed_up and histogram > hist_thresh:
                return "BUY", 1.0
            if crossed_down and histogram < -hist_thresh:
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
            "tp1": round(price + sign * sl_dist * float(params.get("tp1_multiplier", 1.2)), 5),
            "tp2": round(price + sign * sl_dist * float(params.get("tp2_multiplier", 2.2)), 5),
            "tp3": round(price + sign * sl_dist * float(params.get("tp3_multiplier", 3.2)), 5),
            "tp4": round(price + sign * sl_dist * float(params.get("tp4_multiplier", 4.5)), 5),
        }