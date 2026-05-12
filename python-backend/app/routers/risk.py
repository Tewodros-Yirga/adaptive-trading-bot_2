from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..services.risk_manager import get_risk_settings, get_risk_status, update_risk_settings

router = APIRouter(prefix="/risk", tags=["risk"])


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    return get_risk_settings(db)


@router.post("/settings")
def update_settings(body: dict, db: Session = Depends(get_db)):
    return update_risk_settings(db, body)


@router.get("/status")
def get_status(db: Session = Depends(get_db)):
    return get_risk_status(db)


@router.post("/halt")
def halt_trading(db: Session = Depends(get_db)):
    update_risk_settings(db, {"trading_halt": True})
    return {"status": "halted", "trading_halt": True}


@router.post("/resume")
def resume_trading(db: Session = Depends(get_db)):
    update_risk_settings(db, {"trading_halt": False})
    return {"status": "resumed", "trading_halt": False}