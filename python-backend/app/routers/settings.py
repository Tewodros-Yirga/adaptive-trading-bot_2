from fastapi import APIRouter, Depends, Query
from pymongo.database import Database

from .. import crud
from ..auth_deps import require_admin, require_write_access
from ..db import get_db

router = APIRouter(prefix="/settings", tags=["settings"])


# ── Bulk endpoints — MUST be before /{key} to avoid path conflict ──────────
@router.get("/bulk")
def get_bulk(keys: list[str] = Query(default=[]), db: Database = Depends(get_db), _a=Depends(require_admin)):
    return crud.get_settings(db, keys)


@router.post("/bulk")
def set_bulk(body: dict, db: Database = Depends(get_db), _w=Depends(require_write_access)):
    for key, value in body.items():
        if value is not None:  # allow empty strings — callers may intentionally clear a key
            crud.set_setting(db, key, str(value))
    return {"saved": list(body.keys())}


# ── Single key endpoints ──────────────────────────────────────────────────
@router.post("/{key}")
def set_one(key: str, body: dict, db: Database = Depends(get_db), _w=Depends(require_write_access)):
    value = body.get("value", "")
    crud.set_setting(db, key, str(value))
    return {"key": key, "value": value}


@router.get("/{key}")
def get_one(key: str, db: Database = Depends(get_db), _a=Depends(require_admin)):
    value = crud.get_setting(db, key)
    return {"key": key, "value": value}