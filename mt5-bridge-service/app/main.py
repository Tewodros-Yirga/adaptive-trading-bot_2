from fastapi import Depends, FastAPI, Header, HTTPException

from .config import settings, validate_required_settings
from .mt5_adapter import adapter
from .schemas import CloseRequest, OrderRequest

app = FastAPI(title="Adaptive MT5 Bridge")


@app.on_event("startup")
def startup_validation() -> None:
    validate_required_settings()


def require_secret(x_bridge_secret: str = Header(default="")) -> None:
    if x_bridge_secret != settings.mt_bridge_secret:
        raise HTTPException(status_code=403, detail="Invalid bridge secret")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    try:
        data = adapter.account()
        return {"ready": True, "account_mode": data.get("mode", "UNKNOWN")}
    except Exception as exc:
        return {"ready": False, "error": str(exc)}


@app.get("/account", dependencies=[Depends(require_secret)])
def account():
    return adapter.account()


@app.get("/positions", dependencies=[Depends(require_secret)])
def positions():
    return {"positions": adapter.positions()}


@app.post("/order", dependencies=[Depends(require_secret)])
def order(payload: OrderRequest):
    side = payload.type.upper()
    if side not in {"BUY", "SELL"}:
        raise HTTPException(status_code=400, detail="type must be BUY or SELL")
    return adapter.place_order(payload.model_dump())


@app.post("/close", dependencies=[Depends(require_secret)])
def close(payload: CloseRequest):
    return adapter.close_position(payload.ticket, payload.volume)
