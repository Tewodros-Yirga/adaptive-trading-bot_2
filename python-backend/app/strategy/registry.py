from .base import BaseStrategy
from .bollinger_breakout import BollingerBreakoutStrategy
from .dtc import DTCStrategy
from .macd_momentum import MACDMomentumStrategy
from .multi_ema_scalper import MultiEMAScalperStrategy
from .rsi_reversal import RSIReversalStrategy
from .vwap_reversion import VWAPReversionStrategy

STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    "DTC": DTCStrategy,
    "RSI_Reversal": RSIReversalStrategy,
    "MACD_Momentum": MACDMomentumStrategy,
    "Bollinger_Breakout": BollingerBreakoutStrategy,
    "Multi_EMA_Scalper": MultiEMAScalperStrategy,
    "VWAP_Reversion": VWAPReversionStrategy,
}


def get_strategy(name: str, params: dict | None = None) -> BaseStrategy:
    cls = STRATEGY_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"Unknown strategy: {name}")
    return cls(params)


def list_strategies() -> list[dict]:
    result = []
    for name, cls in STRATEGY_REGISTRY.items():
        instance = cls()
        result.append({
            "name": name,
            "display_name": cls.display_name,
            "description": cls.description,
            "default_params": instance.default_params(),
        })
    return result