"""
AlgoTrade Pro — FastAPI Application Entry Point
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.security import APIKeyHeader
import os
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .db import SessionLocal, engine
from .models import Base
from .startup_migrations import run_startup_migrations

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── 1. Run DDL migrations FIRST, before any ORM queries ───────────────
    try:
        logger.info("Running startup migrations...")
        run_startup_migrations()
        logger.info("Startup migrations complete.")
    except Exception as e:
        logger.error(f"Startup migration failed: {e}")
        raise

    # ── 2. Ensure default params exist ────────────────────────────────────
    db = SessionLocal()
    try:
        from .crud import get_current_params, save_params
        from .strategy.dtc import DEFAULT_PARAMS
        if not get_current_params(db):
            save_params(db, DEFAULT_PARAMS.copy(), reason="Initial defaults", trigger="SYSTEM")
            logger.info("Seeded default DTC parameters.")
    except Exception as e:
        logger.warning(f"Could not seed default params: {e}")
    finally:
        db.close()

    # ── 3. Seed strategy registry into DB ─────────────────────────────────
    db = SessionLocal()
    try:
        from .routers.strategies import _ensure_strategies_exist
        _ensure_strategies_exist(db)
        logger.info("Strategy registry seeded.")
    except Exception as e:
        logger.warning(f"Could not seed strategies: {e}")
    finally:
        db.close()

    # ── 4. Start background tasks ─────────────────────────────────────────
    bg_tasks = []
    bg_tasks.append(asyncio.create_task(_news_fetch_loop()))
    bg_tasks.append(asyncio.create_task(_news_learning_loop()))
    bg_tasks.append(asyncio.create_task(_global_context_loop()))

    logger.info("Application startup complete.")
    yield

    # ── Shutdown ──────────────────────────────────────────────────────────
    for task in bg_tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    logger.info("Application shutdown complete.")


async def _news_fetch_loop():
    """Fetch news every 30 minutes."""
    await asyncio.sleep(60)  # initial delay
    while True:
        try:
            db = SessionLocal()
            from .services.news_intelligence import fetch_and_store_news
            from .config import settings
            fetch_and_store_news(db, getattr(settings, "symbol", "XAUUSD"))
        except Exception as e:
            logger.warning(f"News fetch loop error: {e}")
        finally:
            db.close()
        await asyncio.sleep(1800)  # 30 min


async def _news_learning_loop():
    """Run retrospective learning every 2 hours."""
    await asyncio.sleep(120)
    while True:
        try:
            db = SessionLocal()
            from .services.news_intelligence import run_retrospective_learning
            updated = run_retrospective_learning(db)
            if updated:
                logger.info(f"Retrospective learning updated {updated} news items.")
        except Exception as e:
            logger.warning(f"News learning loop error: {e}")
        finally:
            db.close()
        await asyncio.sleep(7200)  # 2 hours


async def _global_context_loop():
    """Update global market context every 30 minutes."""
    await asyncio.sleep(90)
    while True:
        try:
            db = SessionLocal()
            from .services.news_intelligence import update_global_context
            update_global_context(db)
        except Exception as e:
            logger.warning(f"Global context loop error: {e}")
        finally:
            db.close()
        await asyncio.sleep(1800)

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

async def verify_api_key(api_key: str = Security(API_KEY_HEADER)):
    expected = os.environ.get("APP_API_KEY")
    if not expected or api_key != expected:
        raise HTTPException(status_code=403, detail="Forbidden")
    return api_key

# ── App factory ────────────────────────────────────────────────────────────────
def create_app() -> FastAPI:
    app = FastAPI(
        title="AlgoTrade Pro",
        version="2.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Register all routers ───────────────────────────────────────────────
    from .routers.webhook import router as webhook_router
    from .routers.strategies import router as strategies_router
    from .routers.risk import router as risk_router
    from .routers.news import router as news_router
    from .routers.backtest import router as backtest_router
    from .routers.websocket import router as ws_router
    from .routers.settings import router as settings_router

    app.include_router(webhook_router, dependencies=[Depends(verify_api_key)])
    app.include_router(strategies_router, dependencies=[Depends(verify_api_key)])
    app.include_router(risk_router, dependencies=[Depends(verify_api_key)])
    app.include_router(news_router, dependencies=[Depends(verify_api_key)])
    app.include_router(backtest_router, dependencies=[Depends(verify_api_key)])
    app.include_router(ws_router, dependencies=[Depends(verify_api_key)])
    app.include_router(settings_router, dependencies=[Depends(verify_api_key)])

    # ── Conditionally include pre-existing routers if they exist ──────────
    _try_include(app, ".routers.trades", "router")
    _try_include(app, ".routers.params", "router")
    _try_include(app, ".routers.adapt", "router")
    _try_include(app, ".routers.simulate", "router")
    _try_include(app, ".routers.bridge", "router")

    # ── Serve frontend SPA ────────────────────────────────────────────────
    import os
    frontend_dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
    if os.path.exists(frontend_dist):
        app.mount("/app", StaticFiles(directory=frontend_dist, html=True), name="frontend")

    @app.get("/health")
    def health():
        return {"status": "ok", "version": "2.0.0"}

    return app


def _try_include(app: FastAPI, module: str, attr: str):
    try:
        import importlib
        mod = importlib.import_module(module, package="app")
        router = getattr(mod, attr)
        app.include_router(router)
    except Exception:
        pass


app = create_app()
