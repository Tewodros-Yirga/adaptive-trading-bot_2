"""
WeightManager — computes and persists ensemble strategy weights.

Weights are derived from each strategy's recent backtest performance.
Called by the EnsembleBacktester (Prompt 04) after every optimization cycle.
Also provides the suspension logic used by EnsembleVoter.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
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
    "Key_Level",
    "Pullback_Sniper",
    "SK_Unified",
    "Ten_AM",
]

COLL_ENSEMBLE_WEIGHTS = "ensemble_weights"

# ── Suspension override / state persistence (AppSettings keys) ──────────────
# Suspension is otherwise *derived* (recomputed each call) and only snapshotted,
# so a manual change to the snapshot would be recomputed away. These keys make
# operator overrides and the cooldown clock durable, guaranteeing a suspended
# strategy can always escape (manually, via backtest, or via timed probation).
FORCE_ACTIVE_KEY = "ensemble_force_active"           # never auto-suspend these
FORCE_SUSPENDED_KEY = "ensemble_force_suspended"     # always suspend these
SUSPENSION_STATE_KEY = "ensemble_suspension_state"   # {name: {since, probation_until}}
SUSPENDED_SNAPSHOT_KEY = "ensemble_suspended_strategies"


def _get_setting_raw(db, key: str) -> str | None:
    doc = db["app_settings"].find_one({"key": key})
    return doc.get("value") if doc else None


def _set_setting_raw(db, key: str, value: str) -> None:
    """Upsert an AppSetting (same pattern save_weights already uses for the
    suspended snapshot; Mongo auto-assigns _id for genuinely new docs)."""
    db["app_settings"].update_one(
        {"key": key},
        {"$set": {"value": value, "updated_at": datetime.utcnow()},
         "$setOnInsert": {"key": key}},
        upsert=True,
    )


def _load_json_list(db, key: str) -> list[str]:
    raw = _get_setting_raw(db, key) or "[]"
    try:
        val = json.loads(raw)
        return [str(x) for x in val] if isinstance(val, list) else []
    except Exception:
        return []


def get_force_active(db) -> list[str]:
    """Strategies the operator has pinned ACTIVE (never auto-suspended)."""
    return _load_json_list(db, FORCE_ACTIVE_KEY)


def get_force_suspended(db) -> list[str]:
    """Strategies the operator has pinned SUSPENDED (manual kill switch)."""
    return _load_json_list(db, FORCE_SUSPENDED_KEY)


def set_force_active(db, names: list[str]) -> None:
    _set_setting_raw(db, FORCE_ACTIVE_KEY, json.dumps(sorted(set(names))))


def set_force_suspended(db, names: list[str]) -> None:
    _set_setting_raw(db, FORCE_SUSPENDED_KEY, json.dumps(sorted(set(names))))


def remove_from_suspended_snapshot(db, name: str) -> None:
    """Immediately drop a name from the persisted suspended snapshot so the
    backtester (and any snapshot reader) sees it un-suspended right away, without
    waiting for the next weight-save cycle to recompute the snapshot."""
    cur = _load_json_list(db, SUSPENDED_SNAPSHOT_KEY)
    if name in cur:
        _set_setting_raw(db, SUSPENDED_SNAPSHOT_KEY, json.dumps([n for n in cur if n != name]))


def _load_state(db) -> dict:
    raw = _get_setting_raw(db, SUSPENSION_STATE_KEY) or "{}"
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except Exception:
        return {}


def _save_state(db, state: dict) -> None:
    try:
        _set_setting_raw(db, SUSPENSION_STATE_KEY, json.dumps(state))
    except Exception as exc:
        logger.warning("WeightManager: persisting suspension state failed: %s", exc)


def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


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

    # Backtest-recovery: a strong *qualified* candidate lifts a suspension.
    # ``qualified`` already encodes the backtester's min-trades / win-rate /
    # drawdown gates, so we only add a profit-factor bar on top of it.
    SUSPENSION_RECOVERY_PROFIT_FACTOR: float = 1.2
    # Cooldown probation defaults (overridable via app_settings).
    DEFAULT_COOLDOWN_HOURS: float = 72.0
    DEFAULT_PROBATION_HOURS: float = 24.0

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
        Return the set of strategy names currently excluded from ensemble voting.

        The base signal is a poor rolling live win-rate (< SUSPENSION_WIN_RATE_THRESHOLD
        over the last SUSPENSION_LOOKBACK closed trades, needs SUSPENSION_MIN_TRADES).
        On top of that, three escape routes guarantee a strategy is never trapped
        forever (a suspended strategy places no live trades, so its win-rate window
        would otherwise never refresh):

          1. Manual override — a name in ``ensemble_force_active`` is never suspended;
             a name in ``ensemble_force_suspended`` is always suspended.
          2. Backtest recovery — a strong *qualified* backtest candidate
             (profit_factor >= SUSPENSION_RECOVERY_PROFIT_FACTOR) lifts it, since the
             backtester keeps optimizing suspended strategies.
          3. Cooldown probation — after being suspended for
             ``ensemble_suspension_cooldown_hours`` a strategy is released for a
             ``ensemble_suspension_probation_hours`` window to rebuild its live
             win-rate; if it is still bad when the window closes the cooldown restarts.

        This is the single source of truth used both by the live EnsembleVoter and by
        the suspended snapshot the backtester reads. It advances and persists the
        cooldown state machine only when a strategy *materially* transitions (rare —
        hours apart), so it is safe to call on the hot voting path.
        """
        force_active = set(get_force_active(db))
        force_suspended = set(get_force_suspended(db))
        cooldown = timedelta(hours=self._float_setting(
            db, "ensemble_suspension_cooldown_hours", self.DEFAULT_COOLDOWN_HOURS))
        probation = timedelta(hours=self._float_setting(
            db, "ensemble_suspension_probation_hours", self.DEFAULT_PROBATION_HOURS))

        state = _load_state(db)
        new_state = dict(state)
        now = datetime.utcnow()
        suspended: set[str] = set()
        changed = False

        for name in _ALL_STRATEGIES:
            # 1. Manual overrides win outright.
            if name in force_active:
                if new_state.pop(name, None) is not None:
                    changed = True
                continue
            if name in force_suspended:
                suspended.add(name)
                continue

            # Base signal: is the live win-rate currently bad?
            if not self._is_live_bad(db, name):
                if new_state.pop(name, None) is not None:
                    changed = True
                continue

            # 2. Backtest recovery — a strong qualified candidate re-earns its place.
            if self._has_recovery_candidate(db, name):
                if new_state.pop(name, None) is not None:
                    changed = True
                    logger.info("WeightManager: %s recovered via qualified backtest — lifting suspension", name)
                continue

            # 3. Cooldown / probation state machine (bad + no recovery).
            st = new_state.get(name) or {}
            since = _parse_iso(st.get("since"))
            prob_until = _parse_iso(st.get("probation_until"))

            if prob_until is not None:
                if now < prob_until:
                    continue  # inside probation window → released to rebuild win-rate
                # Probation elapsed and still bad → restart the cooldown clock.
                new_state[name] = {"since": now.isoformat(), "probation_until": None}
                changed = True
                suspended.add(name)
                logger.info("WeightManager: %s failed probation (still bad) — re-suspending", name)
                continue

            if since is None:
                # First time seen bad → start the cooldown clock, suspend now.
                new_state[name] = {"since": now.isoformat(), "probation_until": None}
                changed = True
                suspended.add(name)
                logger.info("WeightManager: suspending %s (poor live win-rate)", name)
                continue

            if (now - since) >= cooldown:
                # Cooled down long enough → open a probation window and release it.
                prob_end = (now + probation).isoformat()
                new_state[name] = {"since": since.isoformat(), "probation_until": prob_end}
                changed = True
                logger.info(
                    "WeightManager: %s released on probation until %s (rebuild live win-rate)",
                    name, prob_end,
                )
                continue

            # Still cooling down → remain suspended.
            suspended.add(name)

        if changed:
            _save_state(db, new_state)

        return suspended

    def _is_live_bad(self, db, strategy_name: str) -> bool:
        """True when the rolling live win-rate is below threshold with enough closed
        trades to judge. This is the original suspension signal, factored out."""
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
                return False  # not enough data to judge → not (yet) bad
            wins = sum(1 for t in recent_trades if t.get("result") == "WIN")
            return (wins / len(recent_trades)) < self.SUSPENSION_WIN_RATE_THRESHOLD
        except Exception as exc:
            logger.debug("WeightManager: live win-rate check failed for %s: %s", strategy_name, exc)
            return False

    def _has_recovery_candidate(self, db, strategy_name: str) -> bool:
        """True when the strategy's best *qualified* backtest candidate is strong
        enough to lift a suspension. ``qualified`` already encodes the backtester's
        minimum-trades / win-rate / drawdown gates, so we only add a profit-factor
        bar (well above the qualification floor) on top."""
        try:
            best = db["backtest_candidates"].find_one(
                {"strategy_name": strategy_name, "qualified": True},
                sort=[("composite_score", -1)],
            )
            if not best:
                return False
            pf = float(best.get("profit_factor") or 0.0)
            return pf >= self.SUSPENSION_RECOVERY_PROFIT_FACTOR
        except Exception as exc:
            logger.debug("WeightManager: recovery check failed for %s: %s", strategy_name, exc)
            return False

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