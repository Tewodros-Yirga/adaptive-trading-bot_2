from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import settings
from .crud import get_current_params, save_params
from .db import Base, SessionLocal, engine
from .routers import adapt, bridge, dashboard, params, simulate, trades, webhook
from .strategy.dtc import DEFAULT_PARAMS

app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(webhook.router)
app.include_router(trades.router)
app.include_router(params.router)
app.include_router(adapt.router)
app.include_router(simulate.router)
app.include_router(bridge.router)
app.include_router(dashboard.router)


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if not get_current_params(db):
            save_params(db, DEFAULT_PARAMS.copy(), reason="Initial seed on startup", trigger="SYSTEM")
    finally:
        db.close()


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
