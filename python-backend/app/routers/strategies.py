import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from ..auth_deps import require_write_access, require_admin
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import crud
from ..db import get_db
from ..models import Strategy
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


# ── Continuous Backtest / Candidate Endpoints ─────────────────────────────

@router.get("/{name}/backtest-candidates", response_model=list[BacktestCandidateOut])
def list_backtest_candidates(
    name: str,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    qualified_only: bool = Query(False),
    db: Session = Depends(get_db),
):
    """List all backtest candidates for a strategy (paginated)."""
    _ensure_strategies_exist(db)
    row = db.scalar(select(Strategy).where(Strategy.name == name))
    if not row:
        raise HTTPException(404, f"Strategy {name} not found")
    return crud.get_backtest_candidates(db, name, page=page, limit=limit, qualified_only=qualified_only)


@router.get("/{name}/backtest-candidates/best", response_model=BacktestCandidateOut | None)
def get_best_candidate(name: str, db: Session = Depends(get_db)):
    """Return the highest-scoring qualified candidate for the strategy."""
    _ensure_strategies_exist(db)
    row = db.scalar(select(Strategy).where(Strategy.name == name))
    if not row:
        raise HTTPException(404, f"Strategy {name} not found")
    return crud.get_best_backtest_candidate(db, name)


@router.get("/{name}/search-status", response_model=SearchStatusOut)
def search_status(name: str, db: Session = Depends(get_db)):
    """Return the current search phase, iteration count, best score, and run/pause state."""
    _ensure_strategies_exist(db)
    row = db.scalar(select(Strategy).where(Strategy.name == name))
    if not row:
        raise HTTPException(404, f"Strategy {name} not found")
    from ..services.continuous_backtest import get_search_status
    return get_search_status(name)


@router.post("/{name}/search-settings")
def update_search_settings(
    name: str,
    body: SearchSettingsIn,
    db: Session = Depends(get_db),
    _a=Depends(require_admin),
):
    """Update continuous backtest search settings for a strategy (admin only)."""
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
    """Pause the continuous backtest loop for a strategy (admin only)."""
    _ensure_strategies_exist(db)
    row = db.scalar(select(Strategy).where(Strategy.name == name))
    if not row:
        raise HTTPException(404, f"Strategy {name} not found")
    from ..services.continuous_backtest import pause_search as _pause
    _pause(name)
    return {"status": "paused", "strategy": name}


@router.post("/{name}/resume-search")
def resume_search(name: str, db: Session = Depends(get_db), _a=Depends(require_admin)):
    """Resume the continuous backtest loop for a strategy (admin only)."""
    _ensure_strategies_exist(db)
    row = db.scalar(select(Strategy).where(Strategy.name == name))
    if not row:
        raise HTTPException(404, f"Strategy {name} not found")
    from ..services.continuous_backtest import resume_search as _resume
    _resume(name)
    return {"status": "resumed", "strategy": name}


# ── Internal helpers ──────────────────────────────────────────────────────

def _get_strategy_stats(db: Session, strategy_name: str) -> dict:
    from sqlalchemy import desc
    from ..models import Trade
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