"""
EnsembleVoter — aggregates signals from all active strategies into a single
high-conviction trade decision using weighted voting.

Design decisions (per project spec):
  - Alchemist has a configurable minimum weight floor (default 0.15) that its
    weight cannot drop below, regardless of optimizer results.
  - No veto power for any single strategy.
  - Voting mode: weighted threshold (configurable, default 0.60).
  - Correlated strategies (DTC, Multi_EMA_Scalper) are penalized by the
    weight optimizer — the voter itself is correlation-agnostic.
  - Suspended strategies (win rate < suspension_threshold) contribute 0 weight.
"""
from __future__ import annotations

# Alchemist weight floor — also imported by weight_manager.py
ALCHEMIST_MIN_WEIGHT: float = 0.15


class EnsembleVoter:
    ALCHEMIST_MIN_WEIGHT: float = ALCHEMIST_MIN_WEIGHT

    def __init__(
        self,
        weights: dict[str, float],
        threshold: float = 0.60,
        suspended_strategies: set[str] | None = None,
    ):
        self.threshold = threshold
        self.suspended_strategies: set[str] = suspended_strategies or set()
        self.normalized_weights = self._normalize(weights)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _normalize(self, weights: dict[str, float]) -> dict[str, float]:
        """
        Normalize weights according to the three rules:
        1. Zero out suspended strategies.
        2. Ensure Alchemist >= ALCHEMIST_MIN_WEIGHT after normalization.
        3. Normalize so weights sum to 1.0.
        """
        if not weights:
            return {}

        # Step 1 — zero out suspended strategies
        w = {k: (0.0 if k in self.suspended_strategies else max(0.0, v))
             for k, v in weights.items()}

        total = sum(w.values())
        if total == 0.0:
            # Fallback: equal weights for non-suspended strategies
            active = [k for k in w if k not in self.suspended_strategies]
            n = len(active)
            if n == 0:
                return {k: 0.0 for k in weights}
            equal = 1.0 / n
            return {k: (equal if k in active else 0.0) for k in weights}

        # Normalize to [0, 1] sum
        norm = {k: v / total for k, v in w.items()}

        # Step 2 — enforce Alchemist floor
        if "Alchemist" in norm and "Alchemist" not in self.suspended_strategies:
            alchemist_w = norm.get("Alchemist", 0.0)
            if alchemist_w < self.ALCHEMIST_MIN_WEIGHT:
                deficit = self.ALCHEMIST_MIN_WEIGHT - alchemist_w
                # Redistribute deficit away from other strategies proportionally
                others = {k: v for k, v in norm.items() if k != "Alchemist" and v > 0}
                others_total = sum(others.values())
                if others_total > 0:
                    for k in others:
                        norm[k] = max(0.0, norm[k] - deficit * (norm[k] / others_total))
                norm["Alchemist"] = self.ALCHEMIST_MIN_WEIGHT
                # Re-normalize to sum to 1.0
                final_total = sum(norm.values())
                if final_total > 0:
                    norm = {k: v / final_total for k, v in norm.items()}

        return norm

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def vote(self, signals: list[dict]) -> dict:
        """
        Aggregate signals into a single direction decision.

        Returns:
        {
            "direction": "BUY" | "SELL" | None,
            "confidence": float,
            "buy_score": float,
            "sell_score": float,
            "votes_breakdown": list[dict],
            "threshold_used": float,
            "fired": bool,
        }
        """
        buy_score = 0.0
        sell_score = 0.0
        votes_breakdown: list[dict] = []

        for sig in signals:
            name = sig.get("strategy_name", "")
            direction = sig.get("direction")
            raw_conf = float(sig.get("confidence") or 0.0)
            weight = self.normalized_weights.get(name, 0.0)
            is_suspended = name in self.suspended_strategies
            contribution = weight * raw_conf if direction else 0.0

            if direction == "BUY":
                buy_score += contribution
            elif direction == "SELL":
                sell_score += contribution

            votes_breakdown.append({
                "strategy_name": name,
                "direction": direction,
                "raw_confidence": raw_conf,
                "weight": round(weight, 6),
                "weighted_contribution": round(contribution, 6),
                "is_suspended": is_suspended,
                "contributed_to_winning_side": False,  # filled in below
            })

        # Determine winner
        direction: str | None = None
        confidence = 0.0

        if buy_score > sell_score and buy_score > self.threshold:
            direction = "BUY"
            confidence = buy_score
        elif sell_score > buy_score and sell_score > self.threshold:
            direction = "SELL"
            confidence = sell_score

        # Back-fill contributed_to_winning_side
        for entry in votes_breakdown:
            entry["contributed_to_winning_side"] = (
                direction is not None and entry["direction"] == direction
            )

        return {
            "direction": direction,
            "confidence": round(confidence, 6),
            "buy_score": round(buy_score, 6),
            "sell_score": round(sell_score, 6),
            "votes_breakdown": votes_breakdown,
            "threshold_used": self.threshold,
            "fired": direction is not None,
        }

    def get_level_strategy(self, direction: str, signals: list[dict]) -> str | None:
        """
        Return the name of the strategy that should compute SL/TP levels.

        Priority:
        1. Alchemist, if it voted in the winning direction.
        2. The highest-weighted non-suspended strategy that voted for `direction`.
        3. None if no strategy voted in that direction.
        """
        # Check Alchemist first
        for sig in signals:
            if (
                sig.get("strategy_name") == "Alchemist"
                and sig.get("direction") == direction
                and "Alchemist" not in self.suspended_strategies
            ):
                return "Alchemist"

        # Highest-weighted agreeing strategy
        best_name: str | None = None
        best_weight = -1.0
        for sig in signals:
            name = sig.get("strategy_name", "")
            if sig.get("direction") == direction and name not in self.suspended_strategies:
                w = self.normalized_weights.get(name, 0.0)
                if w > best_weight:
                    best_weight = w
                    best_name = name

        return best_name

    def update_weights(self, new_weights: dict[str, float]) -> None:
        """Replace weights and re-normalize, respecting Alchemist floor and suspended set."""
        self.normalized_weights = self._normalize(new_weights)