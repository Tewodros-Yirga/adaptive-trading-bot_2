"""
News veto service.

This is all that remains of the old "strategy picker": the news-driven veto
that can block a trade when high-confidence news points hard against every
strategy that is currently signalling. The EnsembleVoter is now the sole
authority on trade *direction*; this module only decides whether to veto.

Replaces the former ``strategy_picker.apply_news_adjustments`` — minus the
factor-scoring / strategy-selection machinery, which is gone. "Signalling"
strategies are read directly from the raw signals.
"""
import datetime
import logging
from typing import Any

from pymongo.database import Database

from .. import crud
from ..models import NewsVetoDecision

logger = logging.getLogger(__name__)


def news_veto_check(symbol: str, signal_dicts: list[dict], db: Database) -> dict:
    """
    Decide whether news should veto this bar's trade, and persist an audit record.

    Veto fires only when ALL of:
      • credibility-weighted news confidence > ``news_veto_threshold``, and
      • at least one strategy is actually signalling this bar, and
      • every signalling strategy is pointing AGAINST the news bias direction.

    If nothing is signalling there is nothing to veto — the normal NO_SIGNAL
    path handles that downstream.

    Returns a dict with keys: ``veto`` (bool), ``veto_reason`` (str|None),
    ``news_influence`` (dict), ``decision_id`` (int|None — the audit record id,
    later back-filled with the trade id by the orchestrator).
    """
    from .news_intelligence import get_news_bias

    bias_threshold = float(crud.get_setting(db, "news_veto_bias_threshold") or 0.5)
    veto_threshold = float(crud.get_setting(db, "news_veto_threshold") or 0.85)

    bias_data = get_news_bias(db, symbol)
    news_bias = float(bias_data.get("bias") or 0.0)
    news_confidence = float(bias_data.get("confidence") or 0.0)

    strategy_signals = {s["strategy_name"]: s.get("direction") for s in signal_dicts}
    signaling = {name: d for name, d in strategy_signals.items() if d}

    news_influence: dict[str, Any] = {
        "news_bias": news_bias,
        "news_confidence": news_confidence,
        "bias_threshold": bias_threshold,
        "veto_threshold": veto_threshold,
        "signaling_strategies": signaling,
    }

    veto = False
    veto_reason: str | None = None

    # Only consider a veto when the news signal is both strong (bias magnitude
    # past the bias threshold) and highly credible (confidence past the veto bar).
    if news_confidence > veto_threshold and abs(news_bias) > bias_threshold and signaling:
        bias_direction = "BUY" if news_bias > 0 else "SELL"
        if all(direction != bias_direction for direction in signaling.values()):
            veto = True
            veto_reason = "NEWS_VETO"
            news_influence["veto"] = True
            news_influence["bias_direction"] = bias_direction
            logger.info(
                "[NewsVeto] %s vetoed — news bias %s (conf=%.2f) opposes all "
                "%d signalling strategies %s",
                symbol, bias_direction, news_confidence, len(signaling),
                list(signaling.items()),
            )

    decision = NewsVetoDecision(
        symbol=symbol,
        timestamp=datetime.datetime.utcnow(),
        trade_id=None,
        veto=veto,
        veto_reason=veto_reason,
        news_influence_json=news_influence,
    )
    decision = crud.create_news_veto_decision(db, decision)

    # create_news_veto_decision stores an int _id but exposes .id as a string;
    # return the int so update_news_veto_decision_trade_id (which queries {_id})
    # can match and back-fill the trade id.
    try:
        decision_id: int | None = int(decision.id)
    except (TypeError, ValueError):
        decision_id = None

    return {
        "veto": veto,
        "veto_reason": veto_reason,
        "news_influence": news_influence,
        "decision_id": decision_id,
    }
