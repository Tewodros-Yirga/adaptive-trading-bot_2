from pymongo.database import Database

from .. import crud
from ..config import settings

LEARNING_KEYS = [
    "adaptation_interval",
    "adaptation_min_closed_trades",
    "adaptation_cooldown_trades",
    "adaptation_lr",
    "adaptation_max_change_pct",
    "adaptation_confidence_threshold",
]


def get_learning_settings(db: Database) -> dict:
    stored = crud.get_settings(db, LEARNING_KEYS)
    defaults = {
        "adaptation_interval": settings.adaptation_interval,
        "adaptation_min_closed_trades": settings.adaptation_min_closed_trades,
        "adaptation_cooldown_trades": settings.adaptation_cooldown_trades,
        "adaptation_lr": settings.adaptation_lr,
        "adaptation_max_change_pct": settings.adaptation_max_change_pct,
        "adaptation_confidence_threshold": settings.adaptation_confidence_threshold,
    }
    typed: dict[str, int | float] = {}
    for key, default in defaults.items():
        raw = stored.get(key)
        if raw is None:
            typed[key] = default
        else:
            typed[key] = int(raw) if isinstance(default, int) else float(raw)
    return typed


def update_learning_settings(db: Database, payload: dict) -> dict:
    current = get_learning_settings(db)
    merged = current | payload
    validated = {
        "adaptation_interval": min(max(int(merged["adaptation_interval"]), 1), 500),
        "adaptation_min_closed_trades": min(max(int(merged["adaptation_min_closed_trades"]), 5), 1000),
        "adaptation_cooldown_trades": min(max(int(merged["adaptation_cooldown_trades"]), 0), 1000),
        "adaptation_lr": min(max(float(merged["adaptation_lr"]), 0.00001), 0.1),
        "adaptation_max_change_pct": min(max(float(merged["adaptation_max_change_pct"]), 0.01), 5.0),
        "adaptation_confidence_threshold": min(max(float(merged["adaptation_confidence_threshold"]), 0.0), 1.0),
    }
    for key, value in validated.items():
        crud.set_setting(db, key, str(value))
    return validated