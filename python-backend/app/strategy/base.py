from abc import ABC, abstractmethod
from typing import Any


class BaseStrategy(ABC):
    name: str = "base"
    display_name: str = "Base Strategy"
    description: str = ""

    def __init__(self, params: dict[str, Any] | None = None):
        self.params = {**self.default_params(), **(params or {})}

    @classmethod
    def default_params(cls) -> dict:
        return {}

    @abstractmethod
    def signal(self, market_data: dict) -> str | None:
        """Return 'BUY', 'SELL', or None."""
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
