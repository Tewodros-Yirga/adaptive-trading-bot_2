from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import crud
from ..db import get_db

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/bulk")
def get_bulk(keys: list[str], db: Session = Depends(get_db)):
    return crud.get_settings(db, keys)


@router.post("/bulk")
def set_bulk(body: dict, db: Session = Depends(get_db)):
    for key, value in body.items():
        if value is not None and str(value).strip():
            crud.set_setting(db, key, str(value))
    return {"saved": list(body.keys())}


@router.post("/{key}")
def set_one(key: str, body: dict, db: Session = Depends(get_db)):
    value = body.get("value", "")
    crud.set_setting(db, key, str(value))
    return {"key": key, "value": value}


@router.get("/{key}")
def get_one(key: str, db: Session = Depends(get_db)):
    value = crud.get_setting(db, key)
    return {"key": key, "value": value}
