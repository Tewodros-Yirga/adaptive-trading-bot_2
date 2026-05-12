import asyncio
import logging

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import settings
from .crud import get_current_params, save_params
from .db import Base, SessionLocal, engine
from .routers import adapt, bridge, dashboard, params, simulate, trades, webhook
from .strategy.dtc import DEFAULT_PARAMS

app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
logger = logging.getLogger(__name__)

app.include_router(webhook.router)
app.include_router(trades.router)
app.include_router(params.router)
app.include_router(adapt.router)
app.include_router(simulate.router)
app.include_router(bridge.router)
app.include_router(dashboard.router)


@app.on_event("startup")
async def startup() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if not get_current_params(db):
            save_params(db, DEFAULT_PARAMS.copy(), reason="Initial seed on startup", trigger="SYSTEM")
    finally:
        db.close()
    asyncio.create_task(_peer_keepalive_loop())


async def _peer_keepalive_loop() -> None:
    base = settings.peer_healthcheck_url.strip().rstrip("/")
    if not base:
        logger.info("Peer keepalive disabled (PEER_HEALTHCHECK_URL not set)")
        return

    health_url = f"{base}/health"
    headers: dict[str, str] = {}
    if settings.peer_healthcheck_bearer_token:
        headers["Authorization"] = f"Bearer {settings.peer_healthcheck_bearer_token}"

    # Small initial delay to avoid hammering peers during cold startup.
    await asyncio.sleep(20)
    timeout = settings.peer_healthcheck_timeout_seconds
    interval = settings.peer_healthcheck_interval_seconds

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        while True:
            try:
                response = await client.get(health_url, headers=headers)
                if response.status_code == 200:
                    logger.info("Peer keepalive OK: %s", health_url)
                else:
                    logger.warning("Peer keepalive non-200 (%s): %s", response.status_code, health_url)
            except Exception as exc:
                logger.warning("Peer keepalive failed (%s): %s", health_url, exc)
            await asyncio.sleep(interval)


@app.get("/api/status")
def status():
    return {
        "status": "running",
        "mode": "SIMULATION" if settings.simulation_mode else "LIVE",
        "symbol": settings.symbol,
        "bridge": settings.mt_bridge_url,
    }


@app.get("/health")
def health():
    return {"status": "ok"}
