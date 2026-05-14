"""
app/services/param_search.py — Parameter Search Engine

Generates parameter candidates using bounded random walk with directional memory.
One ParamSearchEngine instance per strategy, persisted across loop iterations.
"""
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParamCandidate:
    strategy_name: str
    params: dict
    search_context: dict   # timeframe, date_range, symbol, iteration_number, generation_method
    generation_method: str  # "initial" | "random_walk" | "timeframe_expansion" | "range_expansion"


class ParamSearchEngine:
    """
    Generates parameter candidates using bounded random walk with directional memory.
    One instance per strategy, persisted across loop iterations.

    Phases:
      1 — Single timeframe/symbol random walk (build confidence quickly)
      2 — Timeframe + symbol expansion (validate generalisation)
      3 — Range expansion (push date range further back)
    """

    def __init__(self, strategy_name: str, strategy_class: Any, default_params: dict):
        self.strategy_name = strategy_name
        self.strategy_class = strategy_class
        self.current_best_params: dict = default_params.copy()
        self.current_best_score: float = 0.0
        self.iteration: int = 0
        self.phase: int = 1
        # Directional memory: param_name → deque of last 5 step directions (+1 or -1)
        self._direction_memory: dict[str, list[int]] = {}
        # Track which timeframe/symbol we are currently cycling through in phase 2
        self._tf_symbol_index: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def next_candidate(
        self,
        step_size: float,
        timeframes: list[str],
        symbols: list[str],
        date_range: tuple[str, str],
        phase: int,
    ) -> ParamCandidate:
        """Generate the next candidate based on current phase."""
        self.phase = phase

        if self.iteration == 0:
            return self._initial_candidate(date_range, timeframes[0], symbols[0])

        if phase == 1:
            return self._random_walk_candidate(step_size, date_range, timeframes[0], symbols[0])

        if phase == 2:
            return self._timeframe_expansion_candidate(step_size, date_range, timeframes, symbols)

        # phase >= 3: range expansion (rotate through all timeframes/symbols)
        return self._range_expansion_candidate(step_size, date_range, timeframes, symbols)

    def update_directional_memory(self, param_name: str, direction: int, improved: bool) -> None:
        """Record whether stepping in a direction improved the score."""
        mem = self._direction_memory.setdefault(param_name, [])
        mem.append(direction if improved else -direction)
        if len(mem) > 5:
            mem.pop(0)

    def promote(self, candidate: ParamCandidate, score: float) -> None:
        """Promote a candidate to current best, updating directional memory."""
        # Update memory for each numeric param that changed
        for key, new_val in candidate.params.items():
            old_val = self.current_best_params.get(key)
            if isinstance(old_val, (int, float)) and isinstance(new_val, (int, float)):
                direction = 1 if new_val > old_val else (-1 if new_val < old_val else 0)
                if direction != 0:
                    improved = score > self.current_best_score
                    self.update_directional_memory(key, direction, improved)

        self.current_best_params = candidate.params.copy()
        self.current_best_score = score
        self.iteration += 1

    def increment_iteration(self) -> None:
        """Call when a candidate was evaluated but NOT promoted."""
        self.iteration += 1

    # ------------------------------------------------------------------
    # Private generation helpers
    # ------------------------------------------------------------------

    def _initial_candidate(self, date_range: tuple[str, str], timeframe: str, symbol: str) -> ParamCandidate:
        """Return the strategy's current best (or default) params unchanged."""
        return ParamCandidate(
            strategy_name=self.strategy_name,
            params=self.current_best_params.copy(),
            search_context={
                "timeframe": timeframe,
                "date_range": list(date_range),
                "symbol": symbol,
                "iteration_number": self.iteration,
                "generation_method": "initial",
            },
            generation_method="initial",
        )

    def _random_walk_candidate(
        self, step_size: float, date_range: tuple[str, str], timeframe: str, symbol: str
    ) -> ParamCandidate:
        """Perturb current best params with a bounded random walk + directional memory."""
        bounds: dict[str, tuple[float, float]] = getattr(self.strategy_class, "PARAM_BOUNDS", {})
        new_params: dict = {}

        for key, val in self.current_best_params.items():
            if key not in bounds or not isinstance(val, (int, float)):
                new_params[key] = val
                continue

            lo, hi = bounds[key]
            memory = self._direction_memory.get(key, [])
            positive_count = sum(1 for d in memory if d > 0)

            # Directional bias from memory
            if len(memory) >= 5 and positive_count >= 3:
                direction = 1 if random.random() < 0.7 else -1
            elif len(memory) >= 5 and positive_count <= 2:
                direction = -1 if random.random() < 0.7 else 1
            else:
                direction = random.choice([-1, 1])

            delta = val * step_size * direction
            new_val = max(lo, min(hi, val + delta))

            # Preserve integer type
            if isinstance(val, int):
                new_val = max(int(lo), min(int(hi), round(new_val)))
            else:
                new_val = round(new_val, 6)

            new_params[key] = new_val

        return ParamCandidate(
            strategy_name=self.strategy_name,
            params=new_params,
            search_context={
                "timeframe": timeframe,
                "date_range": list(date_range),
                "symbol": symbol,
                "iteration_number": self.iteration,
                "generation_method": "random_walk",
            },
            generation_method="random_walk",
        )

    def _timeframe_expansion_candidate(
        self,
        step_size: float,
        date_range: tuple[str, str],
        timeframes: list[str],
        symbols: list[str],
    ) -> ParamCandidate:
        """Rotate through timeframe × symbol combinations with small random walk."""
        combos = [(tf, sym) for tf in timeframes for sym in symbols]
        idx = self._tf_symbol_index % len(combos)
        timeframe, symbol = combos[idx]
        self._tf_symbol_index += 1

        # Use a reduced step size in expansion phase
        candidate = self._random_walk_candidate(step_size * 0.5, date_range, timeframe, symbol)
        candidate.generation_method = "timeframe_expansion"
        candidate.search_context["generation_method"] = "timeframe_expansion"
        return candidate

    def _range_expansion_candidate(
        self,
        step_size: float,
        date_range: tuple[str, str],
        timeframes: list[str],
        symbols: list[str],
    ) -> ParamCandidate:
        """Expand date range and rotate through all combos with smaller step."""
        combos = [(tf, sym) for tf in timeframes for sym in symbols]
        idx = self._tf_symbol_index % len(combos)
        timeframe, symbol = combos[idx]
        self._tf_symbol_index += 1

        candidate = self._random_walk_candidate(step_size * 0.3, date_range, timeframe, symbol)
        candidate.generation_method = "range_expansion"
        candidate.search_context["generation_method"] = "range_expansion"
        return candidate