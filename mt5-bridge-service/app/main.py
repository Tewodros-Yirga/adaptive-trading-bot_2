import os
import socket
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException

from .config import settings, validate_required_settings
from .mt5_adapter import adapter
from .schemas import CloseRequest, OrderRequest

app = FastAPI(title="Adaptive MT5 Bridge")


@app.on_event("startup")
def startup_validation() -> None:
    validate_required_settings()


def require_secret(x_bridge_secret: str = Header(default="")) -> None:
    if not x_bridge_secret:
        raise HTTPException(status_code=403, detail="Missing X-Bridge-Secret header")
    if x_bridge_secret != settings.mt_bridge_secret:
        raise HTTPException(status_code=403, detail="Invalid bridge secret (check X-Bridge-Secret)")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.api_route("/", methods=["GET", "HEAD"])
def root():
    return {"service": "adaptive-mt5-bridge", "status": "ok"}


@app.get("/ready")
def ready():
    try:
        data = adapter.account()
        account_mode = data.get("mode", "UNKNOWN")
        # If MT5 is not actually connected, adapter returns FALLBACK.
        # Treat that as "not ready" so operators can distinguish endpoint availability vs MT5 connectivity.
        return {"ready": account_mode == "LIVE", "account_mode": account_mode, "warning": data.get("warning")}
    except Exception as exc:
        return {"ready": False, "error": str(exc)}


def _tail_file(path: Path, max_bytes: int = 40_000) -> str | None:
    try:
        if not path.exists():
            return None
        data = path.read_bytes()
        if len(data) > max_bytes:
            data = data[-max_bytes:]
        return data.decode("utf-8", errors="replace")
    except Exception as exc:
        return f"(failed reading {path}: {exc})"


def _tcp_open(host: str, port: int, timeout_s: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except Exception:
        return False


@app.get("/debug/mt5", dependencies=[Depends(require_secret)])
def debug_mt5():
    """
    Operational debug endpoint for Render deployments.
    Shows whether mt5linux (RPyC) port is reachable and tails relevant logs.
    """

    logdir = Path(os.environ.get("LOGDIR", "/home/wineuser/.mt5-bridge-logs"))
    ready_file = logdir / "bootstrap.ready"
    failed_file = logdir / "bootstrap.failed"
    status_file = logdir / "bootstrap.status"

    return {
        "wineprefix": os.environ.get("WINEPREFIX"),
        "mt_terminal_exe": settings.mt_terminal_exe,
        "mt5linux_host": settings.mt5linux_host,
        "mt5linux_port": settings.mt5linux_port,
        "mt5linux_port_open": _tcp_open(settings.mt5linux_host, settings.mt5linux_port),
        "logdir": str(logdir),
        "bootstrap": {
            "ready": ready_file.exists(),
            "failed": failed_file.exists(),
            "status": _tail_file(status_file, max_bytes=4_000),
        },
        "logs": {
            "bootstrap-mt5": _tail_file(logdir / "bootstrap-mt5.log"),
            "mt5linux": _tail_file(logdir / "mt5linux.log"),
            "python-encodings-check": _tail_file(logdir / "python-encodings-check.log"),
            "mt5linux-import-check": _tail_file(logdir / "mt5linux-import-check.log"),
            "python-download": _tail_file(logdir / "python-download.log"),
            "python-installer": _tail_file(logdir / "python-installer.log"),
            "wine-pip-upgrade": _tail_file(logdir / "wine-pip-upgrade.log"),
            "wine-metatrader5-pip-install": _tail_file(logdir / "wine-metatrader5-pip-install.log"),
            "wine-mt5linux-pip-install": _tail_file(logdir / "wine-mt5linux-pip-install.log"),
            "mt5-terminal": _tail_file(logdir / "mt5-terminal.log"),
            "mt5-launch-wrapper": _tail_file(logdir / "mt5-launch-wrapper.log"),
        },
    }


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
