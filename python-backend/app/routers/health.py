"""
app/routers/health.py — MongoDB connection health endpoint.
"""
from fastapi import APIRouter, Depends
from pymongo.database import Database

from ..db import get_db

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/db")
def db_health(db: Database = Depends(get_db)):
    """
    Public, unauthenticated endpoint that verifies MongoDB connectivity
    via a lightweight ping command.
    """
    try:
        db.command("ping")
        return {"status": "ok", "type": "mongodb"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}