import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import crud
from ..db import get_db
from ..models import Strategy
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
def activate(name: str, db: Session = Depends(get_db)):
    _ensure_strategies_exist(db)
    row = db.scalar(select(Strategy).where(Strategy.name == name))
    if not row:
        raise HTTPException(404)
    row.is_active = True
    row.updated_at = datetime.utcnow()
    db.commit()
    return {"status": "activated", "name": name}


@router.post("/{name}/deactivate")
def deactivate(name: str, db: Session = Depends(get_db)):
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
def make_live(name: str, db: Session = Depends(get_db)):
    _ensure_strategies_exist(db)
    if name not in STRATEGY_REGISTRY:
        raise HTTPException(404, f"Strategy {name} not in registry")
    row = set_strategy_live(db, name)
    # also ensure it's active
    row.is_active = True
    db.commit()
    return {"status": "live", "name": name}


@router.post("/{name}/params")
def update_params(name: str, body: dict, db: Session = Depends(get_db)):
    _ensure_strategies_exist(db)
    row = db.scalar(select(Strategy).where(Strategy.name == name))
    if not row:
        raise HTTPException(404)
    current = json.loads(row.params_json or "{}")
    current.update(body)
    row.params_json = json.dumps(current)
    row.updated_at = datetime.utcnow()
    db.commit()
    crud.save_params(db, current, reason="Manual update via API", trigger="MANUAL", )
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


@router.get("/ensemble/config")
def get_ens_config(db: Session = Depends(get_db)):
    return get_ensemble_config(db)


@router.post("/ensemble/config")
def update_ens_config(body: dict, db: Session = Depends(get_db)):
    return set_ensemble_config(db, body)


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
