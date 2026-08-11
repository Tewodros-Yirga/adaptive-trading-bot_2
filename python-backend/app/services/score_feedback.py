"""
Live score feedback.

When a trade closes, update each *voting* strategy's ``live_score`` based on the
trade's realised **R-multiple** (reward earned per unit of risk taken), signed by
whether the strategy voted WITH or AGAINST the trade direction:

    contribution = R                if the strategy voted WITH the direction
                 = -R               if it voted AGAINST

where R = realised_PnL_in_price / initial_risk_in_price
       = (exit - entry)/(entry - stop)   for a BUY   (sign-adjusted for SELL).

Using R instead of a fixed ±step fixes the core flaw of win-rate-based feedback:
a strategy that wins 45% of the time at 2:1 reward:risk is *profitable* and now
earns positive feedback, whereas a fixed +0.01/-0.02 rule would slowly suppress
it. Magnitude matters too — a +3R win moves the score far more than a -1R loss.

``live_score`` is an EWMA of these contributions (centred on 0.0) stored on the
Strategy document. It is deliberately SEPARATE from the backtest
``composite_score`` — the two control loops (live feedback vs. backtest
promotion) never overwrite each other. The EnsembleVoter blends live_score in as
a weight multiplier at vote time (see orchestrator._load_ensemble_voter).

No queue, no re-backtesting — applied directly and synchronously at close.
"""
import logging

from pymongo.database import Database

from .. import crud
from ..models import Trade

logger = logging.getLogger(__name__)


def _enabled(db: Database) -> bool:
    return str(crud.get_setting(db, "score_feedback_enabled") or "true").lower() in (
        "1", "true", "yes", "on",
    )


def _realised_r_multiple(trade: Trade) -> float:
    """
    R-multiple = realised reward / initial risk, in price terms.

    Initial risk is the entry→stop distance captured at order time. Falls back to
    ±1.0 (a plain win/loss unit) when the stop or entry is missing or degenerate,
    so the feedback still has the correct sign. Clamped to a sane band so a single
    runaway trade can't dominate the EWMA.
    """
    entry = trade.entry_price
    stop = trade.stop_loss
    exit_price = trade.exit_price
    sign = 1.0 if trade.direction == "BUY" else -1.0

    risk = abs(entry - stop) if (entry is not None and stop is not None) else 0.0
    if risk <= 0 or exit_price is None or entry is None:
        # No usable stop distance → fall back to a unit R from the result.
        return 1.0 if trade.result == "WIN" else -1.0

    r = sign * (exit_price - entry) / risk
    return max(-5.0, min(10.0, r))


def apply_score_feedback(db: Database, trade: Trade) -> dict:
    """
    Update each voting strategy's ``live_score`` from this closed trade's
    R-multiple. Correct-direction voters move by +alpha*R, wrong-direction voters
    by -alpha*R (so a win pushes against-voters down, a loss pushes them up).

    Returns ``{strategy_name: {"r": R, "contribution": .., "live_score": ..}}``
    (empty if disabled, no decision found, or no votes).
    """
    if not _enabled(db):
        return {}

    if trade is None or trade.result not in ("WIN", "LOSS") or not trade.direction:
        return {}

    # Read voting data from the trade itself (stored at open, survives TTL deletion).
    # For legacy trades without strategy_votes_json, fall back to EnsembleDecision lookup.
    strategy_votes = trade.strategy_votes_json
    if not strategy_votes:
        try:
            trade_id = int(trade.id)
        except (TypeError, ValueError):
            logger.debug("score_feedback: trade has no integer id; skipping")
            return {}
        
        decision = crud.get_ensemble_decision_by_trade_id(db, trade_id)
        if not decision or not decision.strategy_votes_json:
            logger.debug("score_feedback: no strategy votes for trade #%s (decision missing or deleted by TTL)", trade_id)
            return {}
        strategy_votes = decision.strategy_votes_json

    # EWMA smoothing factor and a clamp on the accumulated score.
    alpha = float(crud.get_setting(db, "score_feedback_alpha") or 0.2)
    bound = float(crud.get_setting(db, "score_feedback_score_bound") or 3.0)

    r_multiple = _realised_r_multiple(trade)
    trade_dir = trade.direction

    adjustments: dict[str, dict] = {}
    for vote in strategy_votes:
        name = vote.get("strategy_name")
        vote_dir = vote.get("direction")
        if not name or not vote_dir:
            continue  # strategy didn't signal this bar → neutral, no update

        # +R if the strategy voted with the trade direction, -R if against.
        contribution = r_multiple if vote_dir == trade_dir else -r_multiple

        prev = crud.get_strategy_live_score(db, name)
        new_score = (1.0 - alpha) * prev + alpha * contribution
        new_score = max(-bound, min(bound, new_score))
        crud.update_strategy_live_score(db, name, new_score)

        adjustments[name] = {
            "r": round(r_multiple, 3),
            "contribution": round(contribution, 3),
            "live_score": round(new_score, 4),
        }

    if adjustments:
        logger.info(
            "[ScoreFeedback] trade #%s %s %s R=%.2f → %s",
            trade_id, trade.result, trade_dir, r_multiple, adjustments,
        )
    return adjustments


def run_trade_close_hooks(db: Database, trade: Trade) -> None:
    """
    Single entry point for all trade-close learning. Called from every place a
    trade is closed (webhook, position stream, reconciler).

      1. Strategy score feedback (this module) — live_score R-multiple update.
      2. News intelligence learning — updates per-item impact_learning_weight and
         source credibility from the realised outcome.

    Each step is isolated so one failing never blocks the other or the close.
    """
    try:
        apply_score_feedback(db, trade)
    except Exception as exc:
        logger.warning("score_feedback failed for trade #%s: %s", getattr(trade, "id", "?"), exc)

    try:
        from .news_intelligence import learn_from_trade
        learn_from_trade(db, {
            "symbol": trade.symbol,
            "direction": trade.direction,
            "opened_at": trade.opened_at,
            "closed_at": trade.closed_at,
            "pnl": trade.pnl,
            "result": trade.result,
            "contributing_news_ids": getattr(trade, "contributing_news_ids", None) or [],
        })
    except Exception as exc:
        logger.warning("news learn-from-close failed for trade #%s: %s", getattr(trade, "id", "?"), exc)
