from abc import ABC, abstractmethod
from typing import Any


class BaseStrategy(ABC):
    name: str = "base"
    display_name: str = "Base Strategy"
    description: str = ""

    # Set to True only for strategies with a real adapt() implementation.
    # Used by the backtester's _maybe_adapt() to skip the no-op call for
    # strategies that don't support live parameter adaptation.
    is_adaptive: bool = False

    # Subclasses override this with {param_name: (min_value, max_value)}
    PARAM_BOUNDS: dict[str, tuple[float, float]] = {}

    def __init__(self, params: dict[str, Any] | None = None):
        self.params = {**self.default_params(), **(params or {})}

    @classmethod
    def default_params(cls) -> dict:
        return {}

    @abstractmethod
    def signal(self, market_data: dict) -> tuple[str | None, float]:
        """
        Return (direction, confidence) where direction is 'BUY', 'SELL', or None
        and confidence is a float in [0.0, 1.0].
        Simple strategies return 1.0 when a signal fires, 0.0 otherwise.
        """
        ...

    @abstractmethod
    def compute_levels(self, direction: str, price: float, params: dict) -> dict:
        """Return {'sl': float, 'tp1': float, 'tp2': float, 'tp3': float, 'tp4': float}."""
        ...

    def adapt(self, trades: list, learning_settings: dict) -> dict:
        """Return updated params dict. Default: no-op."""
        return self.params.copy()

    def get_params(self) -> dict:
        return self.params.copy()

    def update_params(self, new_params: dict) -> None:
        self.params.update(new_params)

    @staticmethod
    def _sort_tp_multipliers(params: dict) -> dict:
        """
        Ensure tp1 <= tp2 <= tp3 <= tp4 multipliers.
        Returns a copy of params with the four TP multipliers sorted ascending.
        Only modifies keys that are present in the dict.
        """
        tp_keys = ["tp1_multiplier", "tp2_multiplier", "tp3_multiplier", "tp4_multiplier"]
        present = [k for k in tp_keys if k in params]
        if len(present) < 2:
            return params.copy()
        result = params.copy()
        values = sorted(result[k] for k in present)
        for k, v in zip(present, values):
            result[k] = v
        return result