"""
app/services/continuous_backtest.py — Continuous Backtesting Background Service

Runs a background asyncio loop per strategy that:
  1. Generates parameter candidates via ParamSearchEngine
  2. Runs backtests in a ProcessPoolExecutor (non-blocking)
  3. Promotes candidates that beat the current best score
  4. Advances through 3 search phases (single-TF → multi-TF → range expansion)
  5. Broadcasts WebSocket events on promotion

Control:
  start_continuous_backtest(strategy_name, executor) — call from startup
  pause_search(strategy_name)
  resume_search(strategy_name)
  stop_search(strategy_name)
  get_search_status(strategy_name) → dict
"""
import asyncio
import json
import logging
from datetime import date, timedelta

from ..db import get_database
from ..models import BacktestCandidate
from ..strategy.registry import STRATEGY_REGISTRY
from .param_search import ParamSearchEngine, ParamCandidate

logger = logging.getLogger(__name__)

# Per-strategy control flags
_cancel_flags: dict[str, asyncio.Event] = {}
_paused_flags: dict[str, asyncio.Event] = {}

# Per-strategy status snapshot (for GET /search-status)
_status: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Public control API
# ---------------------------------------------------------------------------

def pause_search(strategy_name: str) -> None:
    if strategy_name in _paused_flags:
        _paused_flags[strategy_name].set()


def resume_search(strategy_name: str) -> None:
    if strategy_name in _paused_flags:
        _paused_flags[strategy_name].clear()


def stop_search(strategy_name: str) -> None:
    if strategy_name in _cancel_flags:
        _cancel_flags[strategy_name].set()


def get_search_status(strategy_name: str) -> dict:
    """Return current phase, iterations, best score, is_paused, is_running."""
    default = {
        "strategy_name": strategy_name,
        "phase": 0,
        "iteration": 0,
        "best_score": 0.0,
        "best_params": {},
        "is_running": False,
        "is_paused": False,
    }
    status = _status.get(strategy_name, default)
    status["is_paused"] = (
        strategy_name in _paused_flags and _paused_flags[strategy_name].is_set()
    )
    status["is_running"] = (
        strategy_name in _cancel_flags and not _cancel_flags[strategy_name].is_set()
    )
    return status


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def start_continuous_backtest(strategy_name: str, executor, startup_delay: int = 0) -> None:
    """
    Main loop for one strategy. Call from startup via:
      asyncio.create_task(start_continuous_backtest(name, executor))

    startup_delay: seconds to wait before the first iteration (stagger at boot).
    """
    if startup_delay > 0:
        logger.info("Continuous backtest for %s: waiting %ds before first run", strategy_name, startup_delay)
        await asyncio.sleep(startup_delay)

    cancel_flag = asyncio.Event()
    pause_flag = asyncio.Event()
    _cancel_flags[strategy_name] = cancel_flag
    _paused_flags[strategy_name] = pause_flag

    _status[strategy_name] = {
        "strategy_name": strategy_name,
        "phase": 1,
        "iteration": 0,
        "best_score": 0.0,
        "best_params": {},
        "is_running": True,
        "is_paused": False,
    }

    db = get_database()
    try:
        from .. import crud as _crud

        strategy_record = _crud.get_strategy_by_name(db, strategy_name)
        if not strategy_record:
            logger.warning("Continuous backtest: strategy %s not found in DB, aborting", strategy_name)
            return

        strategy_class = STRATEGY_REGISTRY.get(strategy_name)
        if not strategy_class:
            logger.warning("Continuous backtest: strategy %s not in registry, aborting", strategy_name)
            return

        default_params = (
            json.loads(strategy_record.params_json)
            if strategy_record.params_json
            else strategy_class.default_params()
        )
        engine = ParamSearchEngine(strategy_name, strategy_class, default_params)
        phase = 1

        while not cancel_flag.is_set():
            # Honour pause
            while pause_flag.is_set() and not cancel_flag.is_set():
                await asyncio.sleep(5)

            if cancel_flag.is_set():
                break

            # Read per-strategy settings from AppSetting
            def _setting(key: str, default) -> str:
                return _crud.get_setting(db, f"{strategy_name}_{key}") or str(default)

            try:
                interval = float(_setting("backtest_interval_seconds", 300))
                step_size = float(_setting("param_step_size", 0.05))
                qualify_win_rate = float(_setting("qualify_threshold_win_rate", 55.0))
                wr_weight = float(_setting("score_weight_win_rate", 0.6))
                roi_weight = float(_setting("score_weight_roi", 0.4))
                timeframes = json.loads(_setting("backtest_timeframes", '["1h","4h","1d"]'))
                symbols = json.loads(_setting("backtest_symbols", '["XAUUSD","EURUSD"]'))
                range_months = int(_setting("range_expansion_months", 6))
                max_months = int(_setting("max_history_months", 36))
            except Exception as exc:
                logger.warning("Could not read settings for %s: %s", strategy_name, exc)
                await asyncio.sleep(60)
                continue

            # Compute date range based on phase
            to_date = date.today().isoformat()
            months_back = min(range_months * phase, max_months)
            from_date = (date.today() - timedelta(days=30 * months_back)).isoformat()

            candidate = engine.next_candidate(
                step_size, timeframes, symbols, (from_date, to_date), phase
            )

            try:
                loop = asyncio.get_event_loop()
                result: dict = await loop.run_in_executor(
                    executor,
                    _run_backtest_worker,
                    {
                        "strategy_name": strategy_name,
                        "symbol": candidate.search_context.get("symbol", symbols[0]),
                        "timeframe": candidate.search_context.get("timeframe", timeframes[0]),
                        "from_date": from_date,
                        "to_date": to_date,
                        "params": candidate.params,
                        "initial_balance": 10000,
                        "leverage": 100,
                        "risk_per_trade_pct": 1.0,
                    },
                )

                win_rate = float(result.get("win_rate", 0))
                roi_pct = float(result.get("total_return_pct", 0))
                profit_factor = float(result.get("profit_factor", 0))
                composite_score = wr_weight * win_rate + roi_weight * roi_pct
                qualified = win_rate >= qualify_win_rate
                score_delta = composite_score - engine.current_best_score
                promoted = qualified and composite_score > engine.current_best_score

                # Persist candidate record
                candidate_row = BacktestCandidate(
                    strategy_name=strategy_name,
                    params_json=candidate.params,
                    search_context_json=candidate.search_context,
                    win_rate=win_rate,
                    roi_pct=roi_pct,
                    profit_factor=profit_factor,
                    composite_score=composite_score,
                    qualified=qualified,
                    promoted=promoted,
                    score_delta=score_delta,
                    backtest_result_id=result.get("backtest_result_id"),
                )
                candidate_row = _crud.create_backtest_candidate(db, candidate_row)

                if promoted:
                    engine.promote(candidate, composite_score)
                    # Write new ParameterVersion
                    _crud.save_params(
                        db,
                        params=candidate.params,
                        reason=f"auto_promote composite_score_delta={score_delta:.4f}",
                        trigger="continuous_backtest",
                    )
                    # Update Strategy params_json
                    _crud.update_strategy_params(db, strategy_name, candidate.params)
                    # Advance phase (max 3)
                    if phase < 3:
                        phase += 1
                    # WebSocket broadcast
                    try:
                        await _broadcast({
                            "event": "params_promoted",
                            "strategy_name": strategy_name,
                            "composite_score": composite_score,
                            "score_delta": score_delta,
                            "phase": phase,
                        })
                    except Exception:
                        pass
                else:
                    engine.increment_iteration()

                # Update status snapshot
                _status[strategy_name].update({
                    "phase": phase,
                    "iteration": engine.iteration,
                    "best_score": engine.current_best_score,
                    "best_params": engine.current_best_params,
                })

            except Exception as exc:
                logger.error(
                    "Continuous backtest error for %s (iteration %d): %s",
                    strategy_name, engine.iteration, exc, exc_info=True
                )
                engine.increment_iteration()

            await asyncio.sleep(interval)

    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.error("Continuous backtest loop crashed for %s: %s", strategy_name, exc, exc_info=True)
    finally:
        _status[strategy_name]["is_running"] = False


# ---------------------------------------------------------------------------
# Worker function (runs in ProcessPoolExecutor — must be top-level picklable)
# ---------------------------------------------------------------------------

def _run_backtest_worker(kwargs: dict) -> dict:
    """
    Top-level function executed in a subprocess via ProcessPoolExecutor.
    Imports are local to avoid pickling issues.
    """
    from ..services.backtester import run_backtest_sync_standalone
    return run_backtest_sync_standalone(**kwargs)


# ---------------------------------------------------------------------------
# WebSocket broadcast helper
# ---------------------------------------------------------------------------

async def _broadcast(event: dict) -> None:
    """Broadcast an event to all connected WebSocket clients."""
    try:
        from ..routers.websocket import broadcast_ws_event
        await broadcast_ws_event(event)
    except ImportError:
        pass