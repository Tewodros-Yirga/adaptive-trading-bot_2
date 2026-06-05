"""
WeightManager — computes and persists ensemble strategy weights.

Weights are derived from each strategy's recent backtest performance.
Called by the EnsembleBacktester (Prompt 04) after every optimization cycle.
Also provides the suspension logic used by EnsembleVoter.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from .voter import ALCHEMIST_MIN_WEIGHT

logger = logging.getLogger(__name__)

_ALL_STRATEGIES = [
    "DTC",
    "RSI_Reversal",
    "MACD_Momentum",
    "Bollinger_Breakout",
    "Multi_EMA_Scalper",
    "VWAP_Reversion",
    "Alchemist",
    "ADX_Regime",
    "OBV_Momentum",
    "StochRSI_Cross",
    "HTF_Structure",
]

COLL_ENSEMBLE_WEIGHTS = "ensemble_weights"


class WeightManager:
    """
    Manages ensemble weights stored in MongoDB collection ``ensemble_weights``.

    Weight computation formula (for each strategy):
      raw_weight = w_wr × win_rate_norm
                 + w_pf × profit_factor_norm
                 + w_bt × backtest_composite_score

    Where:
      win_rate_norm            = strategy_win_rate / 100
      profit_factor_norm       = min(profit_factor / 3.0, 1.0)
      backtest_composite_score = from best qualified backtest candidate

    Factor weights (w_wr, w_pf, w_bt) are configurable via MongoDB AppSettings:
      ensemble_weight_factor_win_rate          default 0.4
      ensemble_weight_factor_profit_factor     default 0.3
      ensemble_weight_factor_backtest_score    default 0.3

    Alchemist floor: enforced by EnsembleVoter, not here.
    """

    SUSPENSION_WIN_RATE_THRESHOLD: float = 0.40   # below this → suspended
    SUSPENSION_MIN_TRADES: int = 10                # need at least this many trades to suspend
    SUSPENSION_LOOKBACK: int = 30                  # trades to look back

    # ------------------------------------------------------------------
    # Weight computation
    # ------------------------------------------------------------------

    def compute_weights(self, db) -> dict[str, float]:
        """
        Compute current weights for all 11 strategies from DB data.
        Returns raw (unnormalized) weights.
        """
        # Read factor weights from settings
        w_wr = self._float_setting(db, "ensemble_weight_factor_win_rate",       0.4)
        w_pf = self._float_setting(db, "ensemble_weight_factor_profit_factor",  0.3)
        w_bt = self._float_setting(db, "ensemble_weight_factor_backtest_score", 0.3)

        raw_weights: dict[str, float] = {}

        for strategy_name in _ALL_STRATEGIES:
            win_rate_norm = 0.0
            pf_norm = 0.0
            bt_score = 0.0

            # --- Live trade performance from trades collection ---
            try:
                recent_trades = list(
                    db["trades"]
                    .find(
                        {"strategy_name": strategy_name, "result": {"$in": ["WIN", "LOSS"]}},
                        {"result": 1},
                    )
                    .sort("opened_at", -1)
                    .limit(self.SUSPENSION_LOOKBACK)
                )
                if recent_trades:
                    wins = sum(1 for t in recent_trades if t.get("result") == "WIN")
                    win_rate_norm = wins / len(recent_trades)
            except Exception as exc:
                logger.debug("WeightManager: trade fetch failed for %s: %s", strategy_name, exc)

            # --- Profit factor from best backtest candidate ---
            try:
                best = db["backtest_candidates"].find_one(
                    {"strategy_name": strategy_name, "qualified": True},
                    sort=[("composite_score", -1)],
                )
                if best:
                    pf = float(best.get("profit_factor") or 0.0)
                    pf_norm = min(pf / 3.0, 1.0)
                    bt_score = float(best.get("composite_score") or 0.0)
            except Exception as exc:
                logger.debug("WeightManager: candidate fetch failed for %s: %s", strategy_name, exc)

            raw_weights[strategy_name] = w_wr * win_rate_norm + w_pf * pf_norm + w_bt * bt_score

        return raw_weights

    # ------------------------------------------------------------------
    # Suspension
    # ------------------------------------------------------------------

    def get_suspended_strategies(self, db) -> set[str]:
        """
        Return set of strategy names whose rolling win rate is below
        SUSPENSION_WIN_RATE_THRESHOLD (minimum SUSPENSION_MIN_TRADES required).
        """
        suspended: set[str] = set()

        for strategy_name in _ALL_STRATEGIES:
            try:
                recent_trades = list(
                    db["trades"]
                    .find(
                        {"strategy_name": strategy_name, "result": {"$in": ["WIN", "LOSS"]}},
                        {"result": 1},
                    )
                    .sort("opened_at", -1)
                    .limit(self.SUSPENSION_LOOKBACK)
                )
                if len(recent_trades) < self.SUSPENSION_MIN_TRADES:
                    continue  # not enough data to suspend
                wins = sum(1 for t in recent_trades if t.get("result") == "WIN")
                win_rate = wins / len(recent_trades)
                if win_rate < self.SUSPENSION_WIN_RATE_THRESHOLD:
                    suspended.add(strategy_name)
                    logger.info(
                        "WeightManager: suspending %s (win_rate=%.1f%% over last %d trades)",
                        strategy_name, win_rate * 100, len(recent_trades),
                    )
            except Exception as exc:
                logger.debug("WeightManager: suspension check failed for %s: %s", strategy_name, exc)

        return suspended

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_weights(
        self,
        db,
        weights: dict[str, float],
        metadata: dict | None = None,
    ) -> None:
        """Upsert weights to MongoDB ``ensemble_weights`` collection."""
        suspended = self.get_suspended_strategies(db)

        # Deactivate all existing active documents
        try:
            db[COLL_ENSEMBLE_WEIGHTS].update_many(
                {"is_active": True},
                {"$set": {"is_active": False}},
            )
        except Exception as exc:
            logger.warning("WeightManager: deactivate existing weights failed: %s", exc)

        doc = {
            "weights": weights,
            "suspended": list(suspended),
            "metadata": metadata or {},
            "saved_at": datetime.utcnow(),
            "is_active": True,
        }
        try:
            db[COLL_ENSEMBLE_WEIGHTS].insert_one(doc)
        except Exception as exc:
            logger.warning("WeightManager: save_weights insert failed: %s", exc)
            return

        # Also write suspended list to AppSettings so continuous_backtest can read it
        try:
            import json
            db["app_settings"].update_one(
                {"key": "ensemble_suspended_strategies"},
                {"$set": {"value": json.dumps(list(suspended)), "updated_at": datetime.utcnow()},
                 "$setOnInsert": {"key": "ensemble_suspended_strategies"}},
                upsert=True,
            )
        except Exception as exc:
            logger.warning("WeightManager: writing suspended strategies setting failed: %s", exc)

    def load_weights(self, db) -> dict[str, float] | None:
        """Load latest saved weights. Returns None if none saved yet."""
        try:
            doc = db[COLL_ENSEMBLE_WEIGHTS].find_one(
                {"is_active": True},
                sort=[("saved_at", -1)],
            )
            if doc and "weights" in doc:
                return doc["weights"]
        except Exception as exc:
            logger.warning("WeightManager: load_weights failed: %s", exc)
        return None

    # ------------------------------------------------------------------
    # Defaults
    # ------------------------------------------------------------------

    def get_default_weights(self) -> dict[str, float]:
        """
        Equal weights for all 11 strategies as starting point.
        Alchemist gets ALCHEMIST_MIN_WEIGHT + equal share of remainder.
        """
        n = len(_ALL_STRATEGIES)
        # Give Alchemist its floor; spread the rest equally among all strategies
        # including Alchemist so it gets a little extra.
        equal_share = 1.0 / n
        alchemist_share = max(ALCHEMIST_MIN_WEIGHT, equal_share)
        remaining = 1.0 - alchemist_share
        others_share = remaining / (n - 1) if n > 1 else 0.0

        return {
            name: (alchemist_share if name == "Alchemist" else others_share)
            for name in _ALL_STRATEGIES
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _float_setting(db, key: str, default: float) -> float:
        try:
            row = db["app_settings"].find_one({"key": key})
            if row and row.get("value") is not None:
                return float(row["value"])
        except Exception:
            pass
        return default