from dataclasses import dataclass
from typing import Any


@dataclass
class DTCParams:
    ema_1: int = 30
    ema_2: int = 35
    ema_3: int = 40
    ema_4: int = 45
    ema_5: int = 50
    ema_6: int = 60
    stop_loss_pct: float = 0.25
    tp1_multiplier: float = 1.0
    tp2_multiplier: float = 2.0
    tp3_multiplier: float = 3.0
    tp4_multiplier: float = 4.0
    lot_size: float = 0.01

    min_stop_loss_pct: float = 0.1
    max_stop_loss_pct: float = 1.5
    min_tp_multiplier: float = 0.5
    max_tp_multiplier: float = 6.0
    min_ema_1: int = 20
    max_ema_1: int = 40
    min_ema_6: int = 45
    max_ema_6: int = 100


DEFAULT_PARAMS = DTCParams().__dict__.copy()


def resolve_params(raw: dict[str, Any] | None) -> DTCParams:
    data = DEFAULT_PARAMS.copy()
    if raw:
        data.update(raw)
    return DTCParams(**data)


def bullish_trend(ema_values: dict[str, float]) -> bool:
    return (
        ema_values["ema_1"] > ema_values["ema_2"]
        and ema_values["ema_2"] > ema_values["ema_3"]
        and ema_values["ema_3"] > ema_values["ema_4"]
        and ema_values["ema_4"] > ema_values["ema_5"]
        and ema_values["ema_5"] > ema_values["ema_6"]
    )


def bearish_trend(ema_values: dict[str, float]) -> bool:
    return (
        ema_values["ema_1"] < ema_values["ema_2"]
        and ema_values["ema_2"] < ema_values["ema_3"]
        and ema_values["ema_3"] < ema_values["ema_4"]
        and ema_values["ema_4"] < ema_values["ema_5"]
        and ema_values["ema_5"] < ema_values["ema_6"]
    )


def trend_shift_signal(previous_bull: bool, previous_bear: bool, current_bull: bool, current_bear: bool) -> str | None:
    if not previous_bull and current_bull:
        return "BUY"
    if not previous_bear and current_bear:
        return "SELL"
    return None


def compute_levels(direction: str, price: float, params: DTCParams) -> dict[str, float]:
    sl_dist = price * (params.stop_loss_pct / 100.0)
    sign = 1 if direction == "BUY" else -1
    return {
        "sl": round(price - (sign * sl_dist), 5),
        "tp1": round(price + (sign * sl_dist * params.tp1_multiplier), 5),
        "tp2": round(price + (sign * sl_dist * params.tp2_multiplier), 5),
        "tp3": round(price + (sign * sl_dist * params.tp3_multiplier), 5),
        "tp4": round(price + (sign * sl_dist * params.tp4_multiplier), 5),
    }


def compute_mtf_state(mtf_pairs: dict[str, tuple[float, float]]) -> dict[str, bool]:
    return {k: fast > slow for k, (fast, slow) in mtf_pairs.items()}
