import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from ..auth_deps import require_write_access, require_admin
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from .. import crud
from ..db import get_db
from ..models import Strategy, Trade
from ..schemas import BacktestCandidateOut, SearchStatusOut, SearchSettingsIn
from ..services.orchestrator import get_ensemble_config, set_ensemble_config, set_strategy_live
from ..strategy.registry import STRATEGY_REGISTRY, list_strategies

router = APIRouter(prefix="/strategies", tags=["strategies"])


def _ensure_strategies_exist(db: Session):
    """Seed strategy rows if they don't exist."""
    for info in list_strategies():
        existing = db.scalar(select(Strategy).where(Strategy.name == info["name"]))
        if not existing:
            row = Strategy(
                name=info["name"],
                display_name=info["display_name"],
                description=info["description"],
                is_active=info["name"] == "DTC",
                is_live=info["name"] == "DTC",
                params_json=json.dumps(info["default_params"]),
            )
            db.add(row)
    db.commit()


@router.get("")
def list_all(db: Session = Depends(get_db)):
    _ensure_strategies_exist(db)
    rows = list(db.scalars(select(Strategy)).all())
    result = []
    for row in rows:
        stats = _get_strategy_stats(db, row.name)
        result.append({
            "name": row.name,
            "display_name": row.display_name,
            "description": row.description,
            "is_active": row.is_active,
            "is_live": row.is_live,
            "params": json.loads(row.params_json or "{}"),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            **stats,
        })
    return result


# ── Ensemble config — MUST be before /{name} to avoid path conflict ───────
@router.get("/ensemble/config")
def get_ens_config(db: Session = Depends(get_db)):
    return get_ensemble_config(db)


@router.post("/ensemble/config")
def update_ens_config(body: dict, db: Session = Depends(get_db), _w=Depends(require_write_access)):
    return set_ensemble_config(db, body)


# ── Single strategy by name ───────────────────────────────────────────────
@router.get("/{name}")
def get_one(name: str, db: Session = Depends(get_db)):
    _ensure_strategies_exist(db)
    row = db.scalar(select(Strategy).where(Strategy.name == name))
    if not row:
        raise HTTPException(404, f"Strategy {name} not found")
    stats = _get_strategy_stats(db, name)
    return {
        "name": row.name,
        "display_name": row.display_name,
        "description": row.description,
        "is_active": row.is_active,
        "is_live": row.is_live,
        "params": json.loads(row.params_json or "{}"),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        **stats,
    }


@router.post("/{name}/activate")
def activate(name: str, db: Session = Depends(get_db), _w=Depends(require_write_access)):
    _ensure_strategies_exist(db)
    row = db.scalar(select(Strategy).where(Strategy.name == name))
    if not row:
        raise HTTPException(404)
    row.is_active = True
    row.updated_at = datetime.utcnow()
    db.commit()
    return {"status": "activated", "name": name}


@router.post("/{name}/deactivate")
def deactivate(name: str, db: Session = Depends(get_db), _w=Depends(require_write_access)):
    _ensure_strategies_exist(db)
    row = db.scalar(select(Strategy).where(Strategy.name == name))
    if not row:
        raise HTTPException(404)
    if row.is_live:
        raise HTTPException(400, "Cannot deactivate the live strategy")
    row.is_active = False
    row.updated_at = datetime.utcnow()
    db.commit()
    return {"status": "deactivated", "name": name}


@router.post("/{name}/set-live")
def make_live(name: str, db: Session = Depends(get_db), _w=Depends(require_write_access)):
    _ensure_strategies_exist(db)
    if name not in STRATEGY_REGISTRY:
        raise HTTPException(404, f"Strategy {name} not in registry")
    row = set_strategy_live(db, name)
    row.is_active = True
    db.commit()
    return {"status": "live", "name": name}


@router.post("/{name}/params")
def update_params(name: str, body: dict, db: Session = Depends(get_db), _w=Depends(require_write_access)):
    _ensure_strategies_exist(db)
    row = db.scalar(select(Strategy).where(Strategy.name == name))
    if not row:
        raise HTTPException(404)
    current = json.loads(row.params_json or "{}")
    current.update(body)
    row.params_json = json.dumps(current)
    row.updated_at = datetime.utcnow()
    db.commit()
    crud.save_params(db, current, reason="Manual update via API", trigger="MANUAL")
    return {"status": "updated", "params": current}


@router.get("/{name}/params/history")
def params_history(name: str, limit: int = 30, db: Session = Depends(get_db)):
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
    db: Session = Depends(get_db),
    _a=Depends(require_admin),
):
    """
    Roll back a strategy's parameters to a specific historical version (admin only).
    Saves a new ParameterVersion with trigger=MANUAL_ROLLBACK and records which version
    was rolled back from.
    """
    _ensure_strategies_exist(db)
    row = db.scalar(select(Strategy).where(Strategy.name == name))
    if not row:
        raise HTTPException(404, f"Strategy {name} not found")

    # Locate the target version in parameter history
    from ..models import ParameterVersion
    target = db.scalar(
        select(ParameterVersion).where(ParameterVersion.version == version)
    )
    if not target:
        raise HTTPException(404, f"Parameter version {version} not found")

    # Save current version number before overwriting
    current_params = json.loads(row.params_json or "{}")
    current_history = crud.get_params_history(db, 1)
    current_version = current_history[0].version if current_history else None

    # Apply rolled-back params
    rolled_back_params = json.loads(target.params_json)
    row.params_json = target.params_json
    row.updated_at = datetime.utcnow()
    db.commit()

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
    db: Session = Depends(get_db),
):
    """
    Returns daily snapshots of win_rate, profit_factor, total_pnl, trade_count,
    and avg_duration for the given strategy, computed from live trades.
    """
    _ensure_strategies_exist(db)
    row = db.scalar(select(Strategy).where(Strategy.name == name))
    if not row:
        raise HTTPException(404, f"Strategy {name} not found")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    trades = list(
        db.scalars(
            select(Trade)
            .where(Trade.strategy_name == name)
            .where(Trade.result.in_(["WIN", "LOSS"]))
            .where(Trade.closed_at >= cutoff)
            .order_by(Trade.closed_at)
        ).all()
    )

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
    db: Session = Depends(get_db),
    _w=Depends(require_write_access),
):
    """
    Returns what signal the strategy WOULD generate right now — does NOT place any order.
    Body: {"symbol": "XAUUSD", "use_live_price": true} or {"market_data": {...}}
    """
    _ensure_strategies_exist(db)
    row = db.scalar(select(Strategy).where(Strategy.name == name))
    if not row:
        raise HTTPException(404, f"Strategy {name} not found")

    if name not in STRATEGY_REGISTRY:
        raise HTTPException(400, f"Strategy {name} has no runnable implementation in registry")

    strategy_cls = STRATEGY_REGISTRY[name]
    params = json.loads(row.params_json or "{}")
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
    db: Session = Depends(get_db),
):
    _ensure_strategies_exist(db)
    row = db.scalar(select(Strategy).where(Strategy.name == name))
    if not row:
        raise HTTPException(404, f"Strategy {name} not found")
    return crud.get_backtest_candidates(db, name, page=page, limit=limit, qualified_only=qualified_only)


@router.get("/{name}/backtest-candidates/best", response_model=BacktestCandidateOut | None)
def get_best_candidate(name: str, db: Session = Depends(get_db)):
    _ensure_strategies_exist(db)
    row = db.scalar(select(Strategy).where(Strategy.name == name))
    if not row:
        raise HTTPException(404, f"Strategy {name} not found")
    return crud.get_best_backtest_candidate(db, name)


@router.get("/{name}/search-status", response_model=SearchStatusOut)
async def search_status(name: str, db: Session = Depends(get_db)):
    _ensure_strategies_exist(db)
    row = db.scalar(select(Strategy).where(Strategy.name == name))
    if not row:
        raise HTTPException(404, f"Strategy {name} not found")

    from ..config import settings
    import httpx
    try:
        async with httpx.AsyncClient(timeout=settings.backtester_service_timeout) as client:
            r = await client.get(f"{settings.backtester_service_url}/status/{name}")
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass

    from ..services.continuous_backtest import get_search_status
    return get_search_status(name)


@router.post("/{name}/search-settings")
def update_search_settings(
    name: str,
    body: SearchSettingsIn,
    db: Session = Depends(get_db),
    _a=Depends(require_admin),
):
    _ensure_strategies_exist(db)
    row = db.scalar(select(Strategy).where(Strategy.name == name))
    if not row:
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


@router.post("/{name}/pause-search")
def pause_search(name: str, db: Session = Depends(get_db), _a=Depends(require_admin)):
    _ensure_strategies_exist(db)
    row = db.scalar(select(Strategy).where(Strategy.name == name))
    if not row:
        raise HTTPException(404, f"Strategy {name} not found")
    from ..services.continuous_backtest import pause_search as _pause
    _pause(name)
    return {"status": "paused", "strategy": name}


@router.post("/{name}/resume-search")
def resume_search(name: str, db: Session = Depends(get_db), _a=Depends(require_admin)):
    _ensure_strategies_exist(db)
    row = db.scalar(select(Strategy).where(Strategy.name == name))
    if not row:
        raise HTTPException(404, f"Strategy {name} not found")
    from ..services.continuous_backtest import resume_search as _resume
    _resume(name)
    return {"status": "resumed", "strategy": name}


# ── Internal helpers ──────────────────────────────────────────────────────

def _get_strategy_stats(db: Session, strategy_name: str) -> dict:
    trades = list(
        db.scalars(
            select(Trade)
            .where(Trade.strategy_name == strategy_name)
            .where(Trade.result.in_(["WIN", "LOSS"]))
            .order_by(desc(Trade.closed_at))
            .limit(100)
        ).all()
    )
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