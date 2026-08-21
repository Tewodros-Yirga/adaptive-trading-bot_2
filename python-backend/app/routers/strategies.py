import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from ..auth_deps import require_write_access, require_admin
from pymongo.database import Database

from .. import crud
from ..db import (
    get_db, COLL_STRATEGIES, COLL_TRADES, COLL_BACKTEST_CANDIDATES, next_id
)
from ..models import Strategy, Trade
from ..schemas import BacktestCandidateOut, SearchStatusOut, SearchSettingsIn
from ..services.orchestrator import set_strategy_live
from ..strategy.registry import STRATEGY_REGISTRY, list_strategies
from ..strategy.ensemble.weight_manager import (
    WeightManager, _ALL_STRATEGIES,
    get_force_active, get_force_suspended,
    set_force_active, set_force_suspended,
    remove_from_suspended_snapshot,
)
from pymongo import DESCENDING as _DESC

router = APIRouter(prefix="/strategies", tags=["strategies"])

# Collections written exclusively by the backtester microservice
COLL_BACKTESTER_RUNS = "backtester_runs"
COLL_ENSEMBLE_BACKTEST_RUNS = "ensemble_backtest_runs"
COLL_ENSEMBLE_WEIGHTS = "ensemble_weights"
COLL_SEARCH_ENGINE_STATE = "search_engine_state"

# The ensemble optimization loop persists its engine state under this name.
ENSEMBLE_STATE_NAME = "__ensemble__"
# Names the frontend / API may use to refer to the ensemble pseudo-strategy.
_ENSEMBLE_ALIASES = {"ensemble", "__ensemble__"}


def _is_ensemble(strategy_name: str) -> bool:
    return strategy_name in _ENSEMBLE_ALIASES


def _ensemble_search_status(db: Database) -> dict:
    """
    Build a search-status payload for the ensemble optimization loop, matching
    the per-strategy shape consumed by normalizeStatus() in Backtesting.tsx.

    Reads from the ensemble-only collections:
      • ensemble_backtest_runs  — per-iteration audit log (latest = current)
      • search_engine_state      — full engine state (best score / params)
      • ensemble_weights         — active promoted weights + metadata
    """
    latest_run = db[COLL_ENSEMBLE_BACKTEST_RUNS].find_one(sort=[("timestamp", _DESC)])
    state_doc = db[COLL_SEARCH_ENGINE_STATE].find_one({"strategy_name": ENSEMBLE_STATE_NAME})
    state = (state_doc or {}).get("state", {}) if state_doc else {}
    active_weights = db[COLL_ENSEMBLE_WEIGHTS].find_one(
        {"is_active": True}, sort=[("saved_at", _DESC)]
    )

    total_iters = db[COLL_ENSEMBLE_BACKTEST_RUNS].count_documents({})
    best_score = float(state.get("current_best_score") or 0.0)
    best_params = state.get("current_best_params") or (
        active_weights.get("weights", {}) if active_weights else {}
    )

    is_running = False
    last_run_at = None
    if latest_run:
        last_run_at = latest_run.get("timestamp")
        if last_run_at:
            now_utc = datetime.now(timezone.utc)
            ts = last_run_at if last_run_at.tzinfo else last_run_at.replace(tzinfo=timezone.utc)
            interval_sec = float(crud.get_setting(db, "ensemble_backtest_interval_seconds") or "600")
            is_running = (now_utc - ts).total_seconds() < interval_sec * 2.5

    return {
        "strategy_name": "ensemble",
        "phase":        0,
        "iteration":    latest_run.get("iteration", total_iters) if latest_run else int(state.get("iteration") or total_iters),
        "best_score":   best_score,
        "best_params":  best_params,
        "is_running":   is_running,
        "is_paused":    False,
        "last_win_rate":        latest_run.get("win_rate")       if latest_run else None,
        "last_profit_factor":   latest_run.get("profit_factor")  if latest_run else None,
        "last_total_trades":    latest_run.get("total_trades")   if latest_run else None,
        "last_composite_score": latest_run.get("ensemble_composite_score") if latest_run else best_score,
        "last_qualified":       latest_run.get("qualified")      if latest_run else None,
        "last_promoted":        latest_run.get("promoted")       if latest_run else None,
        "last_run_at":          last_run_at,
        "total_iterations":     total_iters,
    }


def _ensure_strategies_exist(db: Database):
    """Seed strategy rows if they don't exist."""
    for info in list_strategies():
        existing = db[COLL_STRATEGIES].find_one({"name": info["name"]})
        if not existing:
            doc = {
                "_id": next_id(db, COLL_STRATEGIES),
                "name": info["name"],
                "display_name": info["display_name"],
                "description": info["description"],
                "is_active": info["name"] == "DTC",
                "is_live": info["name"] == "DTC",
                "params_json": json.dumps(info["default_params"]),
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
            db[COLL_STRATEGIES].insert_one(doc)


@router.get("")
def list_all(db: Database = Depends(get_db)):
    _ensure_strategies_exist(db)
    docs = list(db[COLL_STRATEGIES].find())
    result = []
    for doc in docs:
        row = Strategy.from_doc(doc)
        stats = _get_strategy_stats(db, row.name)
        result.append({
            "name": row.name,
            "display_name": row.display_name,
            "description": row.description,
            "is_active": row.is_active,
            "is_live": row.is_live,
            "params": json.loads(row.params_json or "{}"),
            "live_score": row.live_score,  # R-EWMA score for frontend display
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            **stats,
        })
    return result


# ── Ensemble suspension: storage inspection + manual overrides ─────────────
# IMPORTANT: GET /suspension is declared BEFORE GET /{name} so the path-param
# route does not shadow it.
#
# Where suspension lives (all in the app_settings collection):
#   • ensemble_suspension_state       — cooldown/probation clock {name:{since,…}}
#   • ensemble_force_active           — manual "never auto-suspend" list
#   • ensemble_force_suspended        — manual kill-switch list
#   • ensemble_suspended_strategies   — snapshot the backtester reads
# The live suspended set is *derived* by WeightManager.get_suspended_strategies.

@router.get("/suspension")
def get_suspension_state(db: Database = Depends(get_db)):
    """
    Report ensemble-suspension state for every strategy: the current derived
    suspended set, the manual override lists, and per-strategy diagnostics (live
    win-rate, best qualified backtest profit factor, recovery eligibility) so the
    UI can show *why* each strategy is suspended and how it can escape.
    """
    wm = WeightManager()
    suspended = wm.get_suspended_strategies(db)
    force_active = set(get_force_active(db))
    force_suspended = set(get_force_suspended(db))

    strategies = []
    for name in _ALL_STRATEGIES:
        recent = list(
            db[COLL_TRADES]
            .find({"strategy_name": name, "result": {"$in": ["WIN", "LOSS"]}}, {"result": 1})
            .sort("opened_at", -1)
            .limit(WeightManager.SUSPENSION_LOOKBACK)
        )
        n = len(recent)
        wins = sum(1 for t in recent if t.get("result") == "WIN")
        win_rate = round(wins / n * 100, 2) if n else None

        best = db[COLL_BACKTEST_CANDIDATES].find_one(
            {"strategy_name": name, "promoted": True},
            sort=[("evaluated_at", _DESC)],
        ) or db[COLL_BACKTEST_CANDIDATES].find_one(
            {"strategy_name": name, "qualified": True},
            sort=[("evaluated_at", _DESC)],
        )
        best_pf = float(best.get("profit_factor") or 0.0) if best else None

        strategies.append({
            "name": name,
            "suspended": name in suspended,
            "force_active": name in force_active,
            "force_suspended": name in force_suspended,
            "live_win_rate": win_rate,
            "live_trades": n,
            "best_qualified_profit_factor": best_pf,
            "recovery_eligible": bool(
                best_pf is not None and best_pf >= WeightManager.SUSPENSION_RECOVERY_PROFIT_FACTOR
            ),
        })

    return {
        "suspended": sorted(suspended),
        "force_active": sorted(force_active),
        "force_suspended": sorted(force_suspended),
        "recovery_profit_factor_threshold": WeightManager.SUSPENSION_RECOVERY_PROFIT_FACTOR,
        "win_rate_threshold_pct": WeightManager.SUSPENSION_WIN_RATE_THRESHOLD * 100,
        "min_trades": WeightManager.SUSPENSION_MIN_TRADES,
        "strategies": strategies,
    }


@router.post("/{name}/unsuspend")
def unsuspend_strategy(name: str, db: Database = Depends(get_db), _w=Depends(require_write_access)):
    """Manually lift a suspension: pin the strategy ACTIVE (never auto-suspended),
    clear any force-suspend, and immediately drop it from the backtester's
    suspended snapshot so live voting and backtesting both resume at once."""
    if name not in _ALL_STRATEGIES:
        raise HTTPException(404, f"Strategy {name} not found")
    fa = set(get_force_active(db)); fa.add(name); set_force_active(db, list(fa))
    fs = set(get_force_suspended(db)); fs.discard(name); set_force_suspended(db, list(fs))
    remove_from_suspended_snapshot(db, name)
    return {"status": "unsuspended", "name": name, "force_active": sorted(fa)}


@router.post("/{name}/suspend")
def suspend_strategy(name: str, db: Database = Depends(get_db), _w=Depends(require_write_access)):
    """Manual kill switch: pin the strategy SUSPENDED (excluded from live voting)
    and clear any force-active override so the suspension actually takes effect."""
    if name not in _ALL_STRATEGIES:
        raise HTTPException(404, f"Strategy {name} not found")
    fs = set(get_force_suspended(db)); fs.add(name); set_force_suspended(db, list(fs))
    fa = set(get_force_active(db)); fa.discard(name); set_force_active(db, list(fa))
    return {"status": "suspended", "name": name, "force_suspended": sorted(fs)}


@router.post("/{name}/clear-suspension-override")
def clear_suspension_override(name: str, db: Database = Depends(get_db), _w=Depends(require_write_access)):
    """Remove both manual overrides — revert to fully automatic suspension
    (live win-rate + backtest recovery + cooldown probation)."""
    if name not in _ALL_STRATEGIES:
        raise HTTPException(404, f"Strategy {name} not found")
    fa = set(get_force_active(db)); fa.discard(name); set_force_active(db, list(fa))
    fs = set(get_force_suspended(db)); fs.discard(name); set_force_suspended(db, list(fs))
    return {"status": "cleared", "name": name}


# ── Single strategy by name ───────────────────────────────────────────────
@router.get("/{name}")
def get_one(name: str, db: Database = Depends(get_db)):
    _ensure_strategies_exist(db)
    doc = db[COLL_STRATEGIES].find_one({"name": name})
    if not doc:
        raise HTTPException(404, f"Strategy {name} not found")
    row = Strategy.from_doc(doc)
    stats = _get_strategy_stats(db, name)
    return {
        "name": row.name,
        "display_name": row.display_name,
        "description": row.description,
        "is_active": row.is_active,
        "is_live": row.is_live,
        "params": json.loads(row.params_json or "{}"),
        "live_score": row.live_score,  # R-EWMA score for frontend display
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        **stats,
    }


@router.post("/{name}/activate")
def activate(name: str, db: Database = Depends(get_db), _w=Depends(require_write_access)):
    _ensure_strategies_exist(db)
    doc = db[COLL_STRATEGIES].find_one({"name": name})
    if not doc:
        raise HTTPException(404)
    db[COLL_STRATEGIES].update_one(
        {"name": name},
        {"$set": {"is_active": True, "updated_at": datetime.utcnow()}},
    )
    return {"status": "activated", "name": name}


@router.post("/{name}/deactivate")
def deactivate(name: str, db: Database = Depends(get_db), _w=Depends(require_write_access)):
    _ensure_strategies_exist(db)
    doc = db[COLL_STRATEGIES].find_one({"name": name})
    if not doc:
        raise HTTPException(404)
    if doc.get("is_live"):
        raise HTTPException(400, "Cannot deactivate the live strategy")
    db[COLL_STRATEGIES].update_one(
        {"name": name},
        {"$set": {"is_active": False, "updated_at": datetime.utcnow()}},
    )
    return {"status": "deactivated", "name": name}


@router.post("/{name}/set-live")
def make_live(name: str, db: Database = Depends(get_db), _w=Depends(require_write_access)):
    _ensure_strategies_exist(db)
    if name not in STRATEGY_REGISTRY:
        raise HTTPException(404, f"Strategy {name} not in registry")
    set_strategy_live(db, name)
    db[COLL_STRATEGIES].update_one(
        {"name": name},
        {"$set": {"is_active": True, "updated_at": datetime.utcnow()}},
    )
    return {"status": "live", "name": name}


@router.post("/{name}/params")
def update_params(name: str, body: dict, db: Database = Depends(get_db), _w=Depends(require_write_access)):
    _ensure_strategies_exist(db)
    doc = db[COLL_STRATEGIES].find_one({"name": name})
    if not doc:
        raise HTTPException(404)
    current = json.loads(doc.get("params_json") or "{}")
    current.update(body)
    db[COLL_STRATEGIES].update_one(
        {"name": name},
        {"$set": {"params_json": json.dumps(current), "updated_at": datetime.utcnow()}},
    )
    crud.save_params(db, current, reason="Manual update via API", trigger="MANUAL")
    return {"status": "updated", "params": current}


@router.get("/{name}/params/history")
def params_history(name: str, limit: int = 30, db: Database = Depends(get_db)):
    history = crud.get_params_history(db, limit)
    return [
        {
            "version": p.version,
            "params": json.loads(p.params_json),
            "reason": p.reason,
            "trigger": p.trigger,
            "confidence_score": p.confidence_score,
            "delta_magnitude": p.delta_magnitude,
            "created_at": p.created_at,
        }
        for p in history
    ]


# ── Feature 7: Parameter rollback ────────────────────────────────────────

@router.post("/{name}/rollback-params")
def rollback_params(
    name: str,
    version: int = Query(..., ge=1),
    db: Database = Depends(get_db),
    _a=Depends(require_admin),
):
    """
    Roll back a strategy's parameters to a specific historical version (admin only).
    Saves a new ParameterVersion with trigger=MANUAL_ROLLBACK and records which version
    was rolled back from.
    """
    _ensure_strategies_exist(db)
    doc = db[COLL_STRATEGIES].find_one({"name": name})
    if not doc:
        raise HTTPException(404, f"Strategy {name} not found")

    # Locate the target version in parameter history
    from ..db import COLL_PARAMETER_VERSIONS
    from ..models import ParameterVersion
    target_doc = db[COLL_PARAMETER_VERSIONS].find_one({"version": version})
    if not target_doc:
        raise HTTPException(404, f"Parameter version {version} not found")
    target = ParameterVersion.from_doc(target_doc)

    # Save current version number before overwriting
    current_history = crud.get_params_history(db, 1)
    current_version = current_history[0].version if current_history else None

    # Apply rolled-back params
    rolled_back_params = json.loads(target.params_json)
    db[COLL_STRATEGIES].update_one(
        {"name": name},
        {"$set": {"params_json": target.params_json, "updated_at": datetime.utcnow()}},
    )

    # Record the rollback event as a new ParameterVersion
    crud.save_params(
        db,
        rolled_back_params,
        reason=f"Manual rollback to version {version}" + (
            f" (from version {current_version})" if current_version else ""
        ),
        trigger="MANUAL_ROLLBACK",
    )

    return {
        "status": "rolled_back",
        "strategy": name,
        "rolled_back_to_version": version,
        "rolled_back_from_version": current_version,
        "params": rolled_back_params,
    }


# ── Feature 2: Live performance timeline ─────────────────────────────────

@router.get("/{name}/performance-timeline")
def performance_timeline(
    name: str,
    days: int = Query(30, ge=1, le=365),
    db: Database = Depends(get_db),
):
    """
    Returns daily snapshots of win_rate, profit_factor, total_pnl, trade_count,
    and avg_duration for the given strategy, computed from live trades.
    """
    _ensure_strategies_exist(db)
    doc = db[COLL_STRATEGIES].find_one({"name": name})
    if not doc:
        raise HTTPException(404, f"Strategy {name} not found")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    trade_docs = list(
        db[COLL_TRADES].find(
            {
                "strategy_name": name,
                "result": {"$in": ["WIN", "LOSS"]},
                "closed_at": {"$gte": cutoff},
            }
        ).sort("closed_at", 1)
    )
    trades = [Trade.from_doc(d) for d in trade_docs]

    # Group by date
    daily: dict[str, list] = defaultdict(list)
    for t in trades:
        if t.closed_at:
            day_key = t.closed_at.strftime("%Y-%m-%d")
            daily[day_key].append(t)

    timeline = []
    for day_key in sorted(daily.keys()):
        day_trades = daily[day_key]
        wins = [t for t in day_trades if t.result == "WIN"]
        losses = [t for t in day_trades if t.result == "LOSS"]
        gross_loss = abs(sum(t.pnl or 0 for t in losses)) or 1e-9
        pf = sum(t.pnl or 0 for t in wins) / gross_loss
        timeline.append({
            "date": day_key,
            "trade_count": len(day_trades),
            "win_rate": round(len(wins) / len(day_trades) * 100, 2),
            "profit_factor": round(pf, 3),
            "total_pnl": round(sum(t.pnl or 0 for t in day_trades), 4),
            "avg_duration_mins": round(
                sum(t.duration_mins or 0 for t in day_trades) / len(day_trades), 1
            ),
        })

    return {"strategy_name": name, "period_days": days, "timeline": timeline}


# ── Feature 5: Strategy Signal Simulator ─────────────────────────────────

@router.post("/{name}/simulate-signal")
async def simulate_signal(
    name: str,
    body: dict,
    db: Database = Depends(get_db),
    _w=Depends(require_write_access),
):
    """
    Returns what signal the strategy WOULD generate right now — does NOT place any order.
    Body: {"symbol": "XAUUSD", "use_live_price": true} or {"market_data": {...}}
    """
    _ensure_strategies_exist(db)
    doc = db[COLL_STRATEGIES].find_one({"name": name})
    if not doc:
        raise HTTPException(404, f"Strategy {name} not found")

    if name not in STRATEGY_REGISTRY:
        raise HTTPException(400, f"Strategy {name} has no runnable implementation in registry")

    strategy_cls = STRATEGY_REGISTRY[name]
    params = json.loads(doc.get("params_json") or "{}")
    symbol = body.get("symbol", "XAUUSD")
    use_live_price = body.get("use_live_price", False)
    market_data = body.get("market_data")

    # Optionally fetch live price from bridge
    if use_live_price and market_data is None:
        try:
            import httpx
            from ..config import settings
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{settings.bridge_url}/price/{symbol}")
                if r.status_code == 200:
                    market_data = r.json()
        except Exception as e:
            raise HTTPException(503, f"Failed to fetch live price: {e}")

    if market_data is None:
        raise HTTPException(400, "Provide market_data or set use_live_price=true")

    # Run the strategy's evaluate/signal method (non-destructively)
    try:
        strategy_instance = strategy_cls(params=params)
        result = strategy_instance.evaluate(market_data)
    except Exception as e:
        raise HTTPException(500, f"Strategy evaluation error: {e}")

    # Sanitize market data snapshot (remove any PII or large arrays)
    snapshot = {k: v for k, v in market_data.items() if not isinstance(v, list)} if market_data else {}

    return {
        "strategy_name": name,
        "signal": result.get("signal"),
        "confidence": result.get("confidence", 0.0),
        "levels": result.get("levels", {}),
        "current_params": params,
        "market_data_snapshot": snapshot,
        "simulated_at": datetime.utcnow().isoformat() + "Z",
    }


# ── Continuous Backtest / Candidate Endpoints ─────────────────────────────

@router.get("/{name}/backtest-candidates", response_model=list[BacktestCandidateOut])
def list_backtest_candidates(
    name: str,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    qualified_only: bool = Query(False),
    db: Database = Depends(get_db),
):
    _ensure_strategies_exist(db)
    doc = db[COLL_STRATEGIES].find_one({"name": name})
    if not doc:
        raise HTTPException(404, f"Strategy {name} not found")
    return crud.get_backtest_candidates(db, name, page=page, limit=limit, qualified_only=qualified_only)


@router.get("/{name}/backtest-candidates/best", response_model=BacktestCandidateOut | None)
def get_best_candidate(name: str, db: Database = Depends(get_db)):
    _ensure_strategies_exist(db)
    doc = db[COLL_STRATEGIES].find_one({"name": name})
    if not doc:
        raise HTTPException(404, f"Strategy {name} not found")
    return crud.get_best_backtest_candidate(db, name)


@router.post("/{name}/search-settings")
def update_search_settings(
    name: str,
    body: SearchSettingsIn,
    db: Database = Depends(get_db),
    _a=Depends(require_admin),
):
    _ensure_strategies_exist(db)
    doc = db[COLL_STRATEGIES].find_one({"name": name})
    if not doc:
        raise HTTPException(404, f"Strategy {name} not found")

    updated: dict[str, str] = {}
    field_map = {
        "qualify_threshold_win_rate": body.qualify_threshold_win_rate,
        "score_weight_win_rate": body.score_weight_win_rate,
        "score_weight_roi": body.score_weight_roi,
        "backtest_interval_seconds": body.backtest_interval_seconds,
        "backtest_timeframes": json.dumps(body.backtest_timeframes) if body.backtest_timeframes is not None else None,
        "backtest_symbols": json.dumps(body.backtest_symbols) if body.backtest_symbols is not None else None,
        "param_step_size": body.param_step_size,
        "range_expansion_months": body.range_expansion_months,
        "max_history_months": body.max_history_months,
    }
    for suffix, value in field_map.items():
        if value is not None:
            key = f"{name}_{suffix}"
            crud.set_setting(db, key, str(value))
            updated[key] = str(value)

    return {"status": "updated", "settings": updated}


# ── Internal helpers ──────────────────────────────────────────────────────

def _get_strategy_stats(db: Database, strategy_name: str) -> dict:
    trade_docs = list(
        db[COLL_TRADES]
        .find({"strategy_name": strategy_name, "result": {"$in": ["WIN", "LOSS"]}})
        .sort("closed_at", -1)
        .limit(100)
    )
    trades = [Trade.from_doc(d) for d in trade_docs]
    if not trades:
        return {"win_rate": 0.0, "profit_factor": 0.0, "total_trades": 0, "last_adapted": None}
    wins = [t for t in trades if t.result == "WIN"]
    losses = [t for t in trades if t.result == "LOSS"]
    gross_loss = abs(sum(t.pnl or 0 for t in losses)) or 1e-9
    pf = sum(t.pnl or 0 for t in wins) / gross_loss
    return {
        "win_rate": round(len(wins) / len(trades) * 100, 2),
        "profit_factor": round(pf, 3),
        "total_trades": len(trades),
        "last_adapted": None,
    }


@router.get("/{strategy_name}/search-status")
def search_status(strategy_name: str, db: Database = Depends(get_db)):
    """
    Read the backtester microservice's latest status directly from MongoDB.

    The backtester writes to:
      • strategies collection  — promotion meta (last_composite_score, last_phase, …)
      • backtester_runs collection — per-iteration audit log (most recent = current state)

    No HTTP proxy — works even when the backtester is between iterations or restarting.
    """
    if _is_ensemble(strategy_name):
        return _ensemble_search_status(db)

    _ensure_strategies_exist(db)
    strategy_doc = db[COLL_STRATEGIES].find_one({"name": strategy_name})
    if not strategy_doc:
        raise HTTPException(404, f"Strategy {strategy_name} not found")

    # Most-recent backtester_runs entry tells us phase/iteration/score right now
    latest_run = db[COLL_BACKTESTER_RUNS].find_one(
        {"strategy_name": strategy_name},
        sort=[("timestamp", _DESC)],
    )

    # Promotion meta stamped onto the strategies doc by the backtester on every promotion
    last_score  = float(strategy_doc.get("last_composite_score") or 0.0)
    last_pf     = float(strategy_doc.get("last_profit_factor")   or 0.0)
    last_trades = int(  strategy_doc.get("last_total_trades")    or 0)
    last_phase  = int(  strategy_doc.get("last_phase")           or 1)

    # Count total iterations from audit log
    total_iters = db[COLL_BACKTESTER_RUNS].count_documents({"strategy_name": strategy_name})

    # Deployed (most recently promoted) candidate for best_params. Rank by
    # recency, not composite_score: a post-rescore promotion scores lower than
    # stale pre-rescore candidates, so DESC-by-score would show out-of-date
    # params next to a fresh score. Fall back to the latest qualified candidate.
    best_candidate = db[COLL_BACKTEST_CANDIDATES].find_one(
        {"strategy_name": strategy_name, "promoted": True},
        sort=[("evaluated_at", _DESC)],
    ) or db[COLL_BACKTEST_CANDIDATES].find_one(
        {"strategy_name": strategy_name, "qualified": True},
        sort=[("evaluated_at", _DESC)],
    )
    best_params = best_candidate.get("params_json", {}) if best_candidate else {}

    # Derive is_running: if the most recent run was within the last configured interval
    # we treat the backtester as running; otherwise "idle between runs"
    is_running = False
    last_run_at = None
    if latest_run:
        last_run_at = latest_run.get("timestamp")
        if last_run_at:
            from datetime import timezone as _tz
            now_utc = datetime.now(_tz.utc)
            ts = last_run_at if last_run_at.tzinfo else last_run_at.replace(tzinfo=_tz.utc)
            interval_setting = crud.get_setting(db, f"{strategy_name}_backtest_interval_seconds") or "300"
            interval_sec = float(interval_setting)
            is_running = (now_utc - ts).total_seconds() < interval_sec * 2.5

    return {
        "strategy_name": strategy_name,
        # Field names match what normalizeStatus() in Backtesting.tsx expects
        "phase":        latest_run.get("phase", last_phase)     if latest_run else last_phase,
        "iteration":    latest_run.get("iteration", total_iters) if latest_run else total_iters,
        "best_score":   last_score,
        "best_params":  best_params,
        "is_running":   is_running,
        "is_paused":    False,   # pause state lives only in the backtester process memory
        # Extra fields the frontend can use
        "last_win_rate":       latest_run.get("win_rate")       if latest_run else None,
        "last_profit_factor":  latest_run.get("profit_factor")  if latest_run else last_pf,
        "last_total_trades":   latest_run.get("total_trades")   if latest_run else last_trades,
        "last_composite_score": latest_run.get("composite_score") if latest_run else last_score,
        "last_qualified":      latest_run.get("qualified")       if latest_run else None,
        "last_promoted":       latest_run.get("promoted")        if latest_run else None,
        "last_run_at":         last_run_at,
        "total_iterations":    total_iters,
    }


@router.get("/{strategy_name}/search-runs")
def search_runs(
    strategy_name: str,
    limit: int = Query(50, ge=1, le=500),
    db: Database = Depends(get_db),
):
    """
    Return recent backtester_runs audit log entries for a strategy.
    The backtester writes one document per iteration.
    """
    if _is_ensemble(strategy_name):
        coll = COLL_ENSEMBLE_BACKTEST_RUNS
        query: dict = {}
    else:
        _ensure_strategies_exist(db)
        coll = COLL_BACKTESTER_RUNS
        query = {"strategy_name": strategy_name}
    docs = list(
        db[coll]
        .find(query)
        .sort("timestamp", _DESC)
        .limit(limit)
    )
    result = []
    for d in docs:
        d.pop("_id", None)
        # Convert datetime → isoformat for JSON
        for k, v in d.items():
            if hasattr(v, "isoformat"):
                d[k] = v.isoformat()
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# Search loop control — proxied to the backtester microservice.
# Works for per-strategy loops and the ensemble pseudo-strategy ("ensemble").
# ---------------------------------------------------------------------------

async def _proxy_control(strategy_name: str, action: str) -> dict:
    """
    Forward a pause/resume/trigger to the backtester service. Returns a uniform
    payload and degrades gracefully when the backtester URL isn't configured or
    the service is unreachable (so the UI shows a soft error, not a 500).
    """
    from ..services.backtester_client import BacktesterClient, BacktesterUnavailable

    client = BacktesterClient()
    is_ens = _is_ensemble(strategy_name)
    try:
        if is_ens:
            fn = {
                "pause":   client.pause_ensemble,
                "resume":  client.resume_ensemble,
                "trigger": client.trigger_ensemble,
            }[action]
            await fn()
        else:
            fn = {"pause": client.pause, "resume": client.resume, "trigger": client.trigger}[action]
            await fn(strategy_name)
        return {"ok": True, "strategy_name": strategy_name, "action": action}
    except BacktesterUnavailable as exc:
        return {"ok": False, "strategy_name": strategy_name, "action": action, "error": str(exc)}
    except Exception as exc:  # httpx errors, etc.
        raise HTTPException(502, f"Backtester control '{action}' failed: {exc}")


@router.post("/{strategy_name}/pause-search")
async def pause_search(strategy_name: str, _w=Depends(require_write_access)):
    return await _proxy_control(strategy_name, "pause")


@router.post("/{strategy_name}/resume-search")
async def resume_search(strategy_name: str, _w=Depends(require_write_access)):
    return await _proxy_control(strategy_name, "resume")


@router.post("/{strategy_name}/trigger-search")
async def trigger_search(strategy_name: str, _w=Depends(require_write_access)):
    return await _proxy_control(strategy_name, "trigger")


@router.get("/ensemble/config")
def ensemble_config(db: Database = Depends(get_db)):
    """
    Return the ensemble optimizer's configuration: search settings (interval,
    horizons, qualify thresholds, horizon weights) plus the active promoted
    weights and live voting threshold. Lets the UI inspect/monitor the ensemble
    without touching any per-strategy parameters.
    """
    setting_keys = [
        "ensemble_backtest_interval_seconds",
        "ensemble_param_step_size",
        "ensemble_qualify_win_rate",
        "ensemble_qualify_profit_factor",
        "ensemble_backtest_timeframe",
        "ensemble_horizon_short_months",
        "ensemble_horizon_medium_months",
        "ensemble_horizon_wide_months",
        "ensemble_weight_short",
        "ensemble_weight_medium",
        "ensemble_weight_wide",
        "ensemble_voting_threshold",
    ]
    settings = {k: crud.get_setting(db, k) for k in setting_keys}

    active = db[COLL_ENSEMBLE_WEIGHTS].find_one({"is_active": True}, sort=[("saved_at", _DESC)])
    weights = active.get("weights", {}) if active else {}
    metadata = active.get("metadata", {}) if active else {}
    saved_at = active.get("saved_at") if active else None

    return {
        "settings": settings,
        "active_weights": weights,
        "metadata": metadata,
        "saved_at": saved_at,
    }