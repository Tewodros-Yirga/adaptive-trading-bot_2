from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import crud
from ..config import settings
from ..db import get_db
from ..services.bridge_client import bridge_client
from ..services.runtime_settings import get_learning_settings, update_learning_settings
from ..strategy.dtc import DEFAULT_PARAMS

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/")
def dashboard_home(request: Request, db: Session = Depends(get_db)):
    params = crud.get_current_params(db) or DEFAULT_PARAMS.copy()
    learning = get_learning_settings(db)
    stats = crud.get_stats(db)
    trades = crud.get_recent_trades(db, 20)
    account_error = None
    positions_error = None
    try:
        account = bridge_client.get_account()
    except Exception as exc:
        account = {}
        account_error = str(exc)
    try:
        positions = bridge_client.get_positions()
    except Exception as exc:
        positions = []
        positions_error = str(exc)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "status": {"mode": "SIMULATION" if settings.simulation_mode else "LIVE", "symbol": settings.symbol},
            "stats": stats,
            "params": params,
            "learning": learning,
            "trades": trades,
            "account": account,
            "positions": positions,
            "account_error": account_error,
            "positions_error": positions_error,
        },
    )


@router.post("/dashboard/params")
def dashboard_update_params(
    ema_1: int = Form(...),
    ema_2: int = Form(...),
    ema_3: int = Form(...),
    ema_4: int = Form(...),
    ema_5: int = Form(...),
    ema_6: int = Form(...),
    stop_loss_pct: float = Form(...),
    tp1_multiplier: float = Form(...),
    tp2_multiplier: float = Form(...),
    tp3_multiplier: float = Form(...),
    tp4_multiplier: float = Form(...),
    lot_size: float = Form(...),
    db: Session = Depends(get_db),
):
    current = crud.get_current_params(db) or DEFAULT_PARAMS.copy()
    merged = current | {
        "ema_1": ema_1,
        "ema_2": ema_2,
        "ema_3": ema_3,
        "ema_4": ema_4,
        "ema_5": ema_5,
        "ema_6": ema_6,
        "stop_loss_pct": min(max(stop_loss_pct, current["min_stop_loss_pct"]), current["max_stop_loss_pct"]),
        "tp1_multiplier": min(max(tp1_multiplier, current["min_tp_multiplier"]), current["max_tp_multiplier"]),
        "tp2_multiplier": min(max(tp2_multiplier, current["min_tp_multiplier"]), current["max_tp_multiplier"]),
        "tp3_multiplier": min(max(tp3_multiplier, current["min_tp_multiplier"]), current["max_tp_multiplier"]),
        "tp4_multiplier": min(max(tp4_multiplier, current["min_tp_multiplier"]), current["max_tp_multiplier"]),
        "lot_size": max(lot_size, 0.0001),
    }
    crud.save_params(db, merged, reason="Manual override via dashboard", trigger="MANUAL")
    return RedirectResponse(url="/", status_code=303)


@router.post("/dashboard/learning")
def dashboard_update_learning(
    adaptation_interval: int = Form(...),
    adaptation_min_closed_trades: int = Form(...),
    adaptation_cooldown_trades: int = Form(...),
    adaptation_lr: float = Form(...),
    adaptation_max_change_pct: float = Form(...),
    adaptation_confidence_threshold: float = Form(...),
    db: Session = Depends(get_db),
):
    update_learning_settings(
        db,
        {
            "adaptation_interval": adaptation_interval,
            "adaptation_min_closed_trades": adaptation_min_closed_trades,
            "adaptation_cooldown_trades": adaptation_cooldown_trades,
            "adaptation_lr": adaptation_lr,
            "adaptation_max_change_pct": adaptation_max_change_pct,
            "adaptation_confidence_threshold": adaptation_confidence_threshold,
        },
    )
    return RedirectResponse(url="/", status_code=303)
