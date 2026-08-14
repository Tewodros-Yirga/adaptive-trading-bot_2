from .base import BaseStrategy
from .alchemist import Alchemist
from .adx_regime_filter import ADXRegimeFilterStrategy
from .bollinger_breakout import BollingerBreakoutStrategy
from .dtc import DTCStrategy
from .htf_structure import HTFStructureStrategy
from .key_level import KeyLevelStrategy
from .macd_momentum import MACDMomentumStrategy
from .multi_ema_scalper import MultiEMAScalperStrategy
from .obv_momentum import OBVMomentumStrategy
from .rsi_reversal import RSIReversalStrategy
from .stoch_rsi_cross import StochRSICrossStrategy
from .vwap_reversion import VWAPReversionStrategy
from .pullback_sniper import PullbackSniperStrategy
from .sk_unified import SKUnifiedStrategy
from .ten_am_strategy import TenAMStrategy

STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    "DTC": DTCStrategy,
    "RSI_Reversal": RSIReversalStrategy,
    "MACD_Momentum": MACDMomentumStrategy,
    "Bollinger_Breakout": BollingerBreakoutStrategy,
    "Multi_EMA_Scalper": MultiEMAScalperStrategy,
    "VWAP_Reversion": VWAPReversionStrategy,
    "Alchemist": Alchemist,
    "ADX_Regime": ADXRegimeFilterStrategy,
    "OBV_Momentum": OBVMomentumStrategy,
    "StochRSI_Cross": StochRSICrossStrategy,
    "HTF_Structure": HTFStructureStrategy,
    "Key_Level": KeyLevelStrategy,
    "Pullback_Sniper": PullbackSniperStrategy,
    "SK_Unified": SKUnifiedStrategy,
    "Ten_AM": TenAMStrategy,
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