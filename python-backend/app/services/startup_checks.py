"""
app/services/startup_checks.py

Validates system state on boot. Returns a list of check results.
Does NOT raise exceptions — the app must start even if checks fail.
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


async def run_startup_checks(db) -> list[dict]:
    """
    Returns list of check results:
      [{"name": str, "status": "OK"/"WARN"/"ERROR", "message": str, "ts": str}]

    Logs each result at the appropriate level.
    """
    checks: list[dict] = []

    # ── 1. DB connectivity ─────────────────────────────────────────────────
    try:
        from sqlalchemy import text
        db.execute(text("SELECT 1"))
        checks.append({
            "name": "db_connectivity",
            "status": "OK",
            "message": "Database reachable",
        })
    except Exception as e:
        checks.append({
            "name": "db_connectivity",
            "status": "ERROR",
            "message": str(e),
        })

    # ── 2. Alembic migration state ─────────────────────────────────────────
    # NOTE: Use a separate raw engine connection (NOT the ORM session) so that
    # MigrationContext doesn't invalidate the shared session for later checks.
    try:
        import os
        from sqlalchemy import create_engine as _ce
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory
        from alembic.config import Config

        alembic_cfg = Config("alembic.ini")
        script = ScriptDirectory.from_config(alembic_cfg)

        _check_engine = _ce(
            os.environ["DATABASE_URL"], future=True, pool_pre_ping=True
        )
        with _check_engine.connect() as raw_conn:
            context = MigrationContext.configure(raw_conn)
            current = set(context.get_current_heads())
        _check_engine.dispose()

        heads = set(script.get_heads())

        if current == heads:
            checks.append({
                "name": "migrations",
                "status": "OK",
                "message": f"All migrations applied: {sorted(current)}",
            })
        else:
            missing = heads - current
            checks.append({
                "name": "migrations",
                "status": "WARN",
                "message": f"Unapplied migrations: {sorted(missing)} — run `alembic upgrade head`",
            })
    except Exception as e:
        checks.append({
            "name": "migrations",
            "status": "WARN",
            "message": f"Could not check migration state: {e}",
        })

    # ── 3. Strategy registry consistency ──────────────────────────────────
    try:
        from app.strategy.registry import STRATEGY_REGISTRY
        from app import crud

        db_strategies = crud.get_all_strategies(db)
        unregistered = [s.name for s in db_strategies if s.name not in STRATEGY_REGISTRY]

        if unregistered:
            for name in unregistered:
                checks.append({
                    "name": f"strategy_registry_{name}",
                    "status": "WARN",
                    "message": (
                        f"Strategy '{name}' exists in DB but is not in STRATEGY_REGISTRY — "
                        "signals will not be generated for it"
                    ),
                })

        checks.append({
            "name": "strategy_registry",
            "status": "OK",
            "message": (
                f"Registry has {len(STRATEGY_REGISTRY)} strategies: "
                f"{', '.join(STRATEGY_REGISTRY.keys())}"
            ),
        })
    except Exception as e:
        checks.append({
            "name": "strategy_registry",
            "status": "WARN",
            "message": f"Could not check strategy registry: {e}",
        })

    # ── 4. Critical AppSetting keys ────────────────────────────────────────
    key_checks = [
        (
            "groq_api_key",
            "Groq API — AI-powered strategy reasoning and pair analysis narratives will not work",
        ),
        (
            "newsapi_key",
            "NewsAPI — news fetching will fall back to RSS/Finnhub only",
        ),
        (
            "alphavantage_key",
            "Alpha Vantage — OHLCV fallback source 2 will be skipped",
        ),
        (
            "finnhub_key",
            "Finnhub — news and OHLCV source will be skipped",
        ),
        (
            "twelve_data_key",
            "Twelve Data — last-resort OHLCV fallback will be skipped (MT5 bridge is preferred)",
        ),
    ]

    try:
        from app import crud as _crud

        for key, warn_msg in key_checks:
            val = _crud.get_setting_sync(db, key)
            is_set = bool(val and val.strip())
            checks.append({
                "name": f"setting_{key}",
                "status": "OK" if is_set else "WARN",
                "message": (
                    f"'{key}' is set"
                    if is_set
                    else f"'{key}' is empty — {warn_msg}"
                ),
            })
    except Exception as e:
        checks.append({
            "name": "settings_check",
            "status": "WARN",
            "message": f"Could not check AppSettings: {e}",
        })

    # ── 5. MT5 bridge connectivity ─────────────────────────────────────────
    try:
        from app.services.bridge_client import bridge_client
        from app.config import settings

        if settings.simulation_mode:
            checks.append({
                "name": "bridge_connectivity",
                "status": "OK",
                "message": "Simulation mode active — bridge not required",
            })
        else:
            account = bridge_client.get_account()
            balance = account.get("balance", "?")
            checks.append({
                "name": "bridge_connectivity",
                "status": "OK",
                "message": f"MT5 bridge reachable — balance: {balance}",
            })
    except Exception as e:
        checks.append({
            "name": "bridge_connectivity",
            "status": "WARN",
            "message": f"MT5 bridge unreachable: {e} — live trading will not work",
        })

    # ── 6. WeasyPrint availability (PDF reports) ───────────────────────────
    try:
        import importlib
        importlib.import_module("weasyprint")
        checks.append({
            "name": "weasyprint",
            "status": "OK",
            "message": "WeasyPrint is installed — PDF report generation available",
        })
    except ImportError:
        checks.append({
            "name": "weasyprint",
            "status": "WARN",
            "message": (
                "WeasyPrint not installed — PDF report endpoint will return 501. "
                "Install with: pip install weasyprint"
            ),
        })

    # ── 7. Active strategies have valid params ─────────────────────────────
    try:
        from app import crud as _crud
        import json

        active = _crud.get_active_strategies(db)
        for s in active:
            try:
                json.loads(s.params_json or "{}")
                checks.append({
                    "name": f"strategy_params_{s.name}",
                    "status": "OK",
                    "message": f"Strategy '{s.name}' params are valid JSON",
                })
            except Exception:
                checks.append({
                    "name": f"strategy_params_{s.name}",
                    "status": "ERROR",
                    "message": f"Strategy '{s.name}' has invalid params_json — signals will fail",
                })
    except Exception as e:
        checks.append({
            "name": "strategy_params",
            "status": "WARN",
            "message": f"Could not validate strategy params: {e}",
        })

    # ── Stamp all checks with a timestamp and log ─────────────────────────
    ts = datetime.utcnow().isoformat() + "Z"
    for check in checks:
        check["ts"] = ts
        level = (
            logging.ERROR
            if check["status"] == "ERROR"
            else (logging.WARNING if check["status"] == "WARN" else logging.INFO)
        )
        logger.log(level, "[startup] %s: %s — %s", check["name"], check["status"], check["message"])

    ok = sum(1 for c in checks if c["status"] == "OK")
    warn = sum(1 for c in checks if c["status"] == "WARN")
    errors = sum(1 for c in checks if c["status"] == "ERROR")
    logger.info(
        "[startup] checks complete: %d OK, %d WARN, %d ERROR",
        ok, warn, errors,
    )

    return checks