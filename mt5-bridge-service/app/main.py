import asyncio
import logging
import os
import re
import socket
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query

from .config import settings, validate_required_settings
from .mt5_adapter import AutoTradingDisabledError, TooManyRequestsError, adapter
from .schemas import CancelRequest, CandlesRequest, CloseRequest, LimitOrderRequest, ModifyRequest, OrderRequest

app = FastAPI(title="Adaptive MT5 Bridge")
logger = logging.getLogger(__name__)


async def _background_connect_loop() -> None:
    await asyncio.sleep(15)
    while True:
        try:
            await asyncio.to_thread(adapter.ensure_connection)
        except Exception:
            pass
        await asyncio.sleep(60)


async def _autotrading_watchdog_loop() -> None:
    """Proactively keep MT5 'Algo Trading' enabled.

    The terminal can flip its AutoTrading toggle OFF mid-session (a late
    account-change event, a stray UI toggle). ``terminal_info().trade_allowed``
    is the authoritative, real-time signal — unlike the on-disk MT5 journal,
    which is buffered and lags by seconds/minutes. When trade_allowed reads
    False while IPC is up, we drop the re-enable sentinel that the start.sh
    dismiss loop watches; it then sends Ctrl+E to turn AutoTrading back on.

    This heals the toggle even with zero order traffic, so order_send rarely
    has to recover from 10027 in the first place.
    """
    logdir = Path(os.environ.get("LOGDIR", "/home/wineuser/.mt5-bridge-logs"))
    ipc_ready_file = logdir / "mt5_ipc.ready"
    interval = float(os.environ.get("MT5_AUTOTRADING_WATCHDOG_SECONDS", "20"))
    # Consecutive zombie reads before we force a reconnect. The terminal's
    # self-restart leaves a brief window where account_info() is legitimately
    # None; requiring 2 in a row (~40s) avoids resetting on that transient.
    zombie_reset_threshold = int(os.environ.get("MT5_ZOMBIE_RESET_THRESHOLD", "2"))
    zombie_streak = 0
    # Let startup, IPC bring-up and the dismiss loop's own cold-start settle.
    await asyncio.sleep(45)
    while True:
        try:
            if ipc_ready_file.exists() and adapter.connected:
                diag = await asyncio.to_thread(adapter.diagnostics)
                logged_in = bool(diag.get("account_logged_in"))
                terminal_up = bool(diag.get("terminal_connected"))

                if logged_in and terminal_up:
                    # Healthy session. Ctrl+E only fixes the AutoTrading TOGGLE,
                    # so signal a re-enable only here (toggle off but link good).
                    zombie_streak = 0
                    if not diag.get("trade_allowed"):
                        logger.warning(
                            "AutoTrading watchdog: toggle OFF (terminal connected, "
                            "account %s logged in) — signalling Ctrl+E re-enable",
                            diag.get("account_login"),
                        )
                        await asyncio.to_thread(adapter._signal_reenable_autotrading)
                else:
                    # ZOMBIE: ipc_connected=True but account_info()/terminal_info()
                    # return None — the IPC session was orphaned by the terminal's
                    # self-restart. ensure_connection() won't rebuild on its own
                    # (connected is still True), so force a clean reconnect.
                    zombie_streak += 1
                    logger.error(
                        "MT5 watchdog: ZOMBIE session (ipc_connected=True but "
                        "terminal_connected=%s account_logged_in=%s) streak=%d/%d. "
                        "diag=%s",
                        diag.get("terminal_connected"), diag.get("account_logged_in"),
                        zombie_streak, zombie_reset_threshold, diag,
                    )
                    if zombie_streak >= zombie_reset_threshold:
                        recovered = await asyncio.to_thread(adapter.force_reconnect)
                        logger.warning(
                            "MT5 watchdog: force_reconnect %s",
                            "succeeded" if recovered else "did not reconnect (retrying)",
                        )
                        zombie_streak = 0
        except Exception as exc:
            logger.debug("AutoTrading watchdog iteration failed: %s", exc)
        await asyncio.sleep(interval)


async def _peer_keepalive_loop() -> None:
    base = settings.peer_healthcheck_url.strip().rstrip("/")
    if not base:
        logger.info("Peer keepalive disabled (PEER_HEALTHCHECK_URL not set)")
        return

    health_url = f"{base}/health"
    headers: dict[str, str] = {}
    if settings.peer_healthcheck_bearer_token:
        headers["Authorization"] = f"Bearer {settings.peer_healthcheck_bearer_token}"

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


@app.on_event("startup")
async def startup_validation() -> None:
    validate_required_settings()
    asyncio.create_task(_background_connect_loop())
    asyncio.create_task(_peer_keepalive_loop())
    asyncio.create_task(_autotrading_watchdog_loop())



def require_secret(x_bridge_secret: str = Header(default="")) -> None:
    if not x_bridge_secret:
        raise HTTPException(status_code=403, detail="Missing X-Bridge-Secret header")
    if x_bridge_secret != settings.mt_bridge_secret:
        raise HTTPException(status_code=403, detail="Invalid bridge secret (check X-Bridge-Secret)")

from .bridge_position_stream import router as stream_router
app.include_router(stream_router, dependencies=[Depends(require_secret)])
# ---------------------------------------------------------------------------
# Health / root
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.api_route("/", methods=["GET", "HEAD"])
def root():
    return {"service": "adaptive-mt5-bridge", "status": "ok"}


@app.get("/ready")
def ready():
    logdir = Path(os.environ.get("LOGDIR", "/home/wineuser/.mt5-bridge-logs"))
    ipc_ready_file = logdir / "mt5_ipc.ready"
    ipc_failed_file = logdir / "mt5_ipc.failed"
    ipc_status_file = logdir / "mt5_ipc.status"
    context_status_file = logdir / "mt5_context.status"
    if not ipc_ready_file.exists():
        return {
            "ready": False,
            "error": "mt5 ipc not ready",
            "error_class": adapter.last_error_class or "ipc_not_ready",
            "ipc_ready": False,
            "ipc_failed": ipc_failed_file.exists(),
            "ipc_status": _tail_file(ipc_status_file, max_bytes=4_000),
            "context_status": _tail_file(context_status_file, max_bytes=4_000),
        }
    try:
        data = adapter.account()
        return {
            "ready": True,
            "account_mode": "LIVE",
            "error_class": adapter.last_error_class,
            "ipc_ready": True,
            "ipc_failed": False,
            "ipc_status": _tail_file(ipc_status_file, max_bytes=4_000),
            "context_status": _tail_file(context_status_file, max_bytes=4_000),
            "backend": data.get("backend"),
        }
    except Exception as exc:
        return {
            "ready": False,
            "error": str(exc),
            "error_class": adapter.last_error_class,
            "ipc_ready": ipc_ready_file.exists(),
            "ipc_failed": ipc_failed_file.exists(),
            "ipc_status": _tail_file(ipc_status_file, max_bytes=4_000),
            "context_status": _tail_file(context_status_file, max_bytes=4_000),
        }


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


def _parse_ipc_probe_stdout(stdout: str) -> dict[str, object]:
    parsed: dict[str, object] = {"ok": None, "err_code": None, "err_message": None}
    if not stdout:
        return parsed
    ok_match = re.search(r"ok=(True|False)", stdout)
    if ok_match:
        parsed["ok"] = ok_match.group(1) == "True"
    err_match = re.search(r"err=\(([-0-9]+),\s*'([^']*)'\)", stdout)
    if err_match:
        parsed["err_code"] = int(err_match.group(1))
        parsed["err_message"] = err_match.group(2)
    return parsed


def _wine_mt5_ipc_probe_script(
    *,
    with_credentials: bool,
    portable: bool,
    timeout_ms: int,
) -> str:
    if not with_credentials:
        return (
            "import MetaTrader5 as mt5; "
            f"ok = mt5.initialize(timeout={timeout_ms}); "
            "err = mt5.last_error(); mt5.shutdown(); print(f'ok={ok} err={err}')"
        )
    login = int(settings.mt_login)
    pw = repr(settings.mt_password)
    srv = repr(settings.mt_server)
    portable_arg = ", portable=True" if portable else ""
    return (
        "import MetaTrader5 as mt5; "
        f"ok = mt5.initialize(login={login}, password={pw}, server={srv}, "
        f"timeout={timeout_ms}{portable_arg}); "
        "err = mt5.last_error(); mt5.shutdown(); print(f'ok={ok} err={err}')"
    )


# ---------------------------------------------------------------------------
# Debug endpoints
# ---------------------------------------------------------------------------

@app.get("/debug/diagnostics", dependencies=[Depends(require_secret)])
def debug_diagnostics():
    """One-shot state snapshot to tell apart the look-alike failure modes:
    broker link down vs account not logged in vs AutoTrading toggle off."""
    return adapter.diagnostics()


@app.get("/debug/mt5", dependencies=[Depends(require_secret)])
def debug_mt5():
    logdir = Path(os.environ.get("LOGDIR", "/home/wineuser/.mt5-bridge-logs"))
    ready_file = logdir / "bootstrap.ready"
    failed_file = logdir / "bootstrap.failed"
    status_file = logdir / "bootstrap.status"
    terminal_ready_file = logdir / "mt5_terminal.ready"
    terminal_path_file = logdir / "mt5_terminal_exe.path"
    ipc_ready_file = logdir / "mt5_ipc.ready"
    ipc_failed_file = logdir / "mt5_ipc.failed"
    ipc_status_file = logdir / "mt5_ipc.status"
    context_status_file = logdir / "mt5_context.status"
    ipc_probe_log = logdir / "mt5-ipc-probe.log"

    terminal_exe_from_sentinel: str | None = None
    if terminal_path_file.exists():
        try:
            terminal_exe_from_sentinel = terminal_path_file.read_text().strip() or None
        except Exception:
            pass

    return {
        "wineprefix": os.environ.get("WINEPREFIX"),
        "mt_terminal_exe": settings.mt_terminal_exe,
        "mt_terminal_exe_discovered": terminal_exe_from_sentinel,
        "mt5linux_host": settings.mt5linux_host,
        "mt5linux_port": settings.mt5linux_port,
        "mt5linux_port_open": _tcp_open(settings.mt5linux_host, settings.mt5linux_port),
        "logdir": str(logdir),
        "adapter": {
            "connected": adapter.connected,
            "backend": adapter._backend,
            "last_error": adapter.last_error,
            "last_error_class": adapter.last_error_class,
            "connect_attempts": adapter._connect_attempts,
        },
        "bootstrap": {
            "ready": ready_file.exists(),
            "failed": failed_file.exists(),
            "status": _tail_file(status_file, max_bytes=4_000),
            "terminal_ready": terminal_ready_file.exists(),
            "ipc_ready": ipc_ready_file.exists(),
            "ipc_failed": ipc_failed_file.exists(),
            "ipc_status": _tail_file(ipc_status_file, max_bytes=4_000),
            "context_status": _tail_file(context_status_file, max_bytes=4_000),
            "mt5_ipc_probe_log_exists": ipc_probe_log.is_file(),
        },
        "runtime_env": {
            "mt5_launch_terminal": os.environ.get("MT5_LAUNCH_TERMINAL", ""),
            "mt5_context_mode": os.environ.get("MT5_CONTEXT_MODE", ""),
            "mt5_skip_pipe_verification": os.environ.get("MT5_SKIP_PIPE_VERIFICATION", ""),
            "mt5_require_x11_window": os.environ.get("MT5_REQUIRE_X11_WINDOW", ""),
            "mt_login_configured": bool(settings.mt_login),
            "mt_server_configured": bool(settings.mt_server.strip()),
        },
        "logs": {
            "bootstrap-mt5": _tail_file(logdir / "bootstrap-mt5.log"),
            "mt5linux": _tail_file(logdir / "mt5linux.log"),
            "python-encodings-check": _tail_file(logdir / "python-encodings-check.log"),
            "mt5linux-import-check": _tail_file(logdir / "mt5linux-import-check.log"),
            "python-download": _tail_file(logdir / "python-download.log"),
            "python-installer": _tail_file(logdir / "python-installer.log"),
            "mt5-download": _tail_file(logdir / "mt5-download.log"),
            "mt5-install": _tail_file(logdir / "mt5-install.log"),
            "wine-pip-upgrade": _tail_file(logdir / "wine-pip-upgrade.log"),
            "wine-metatrader5-pip-install": _tail_file(logdir / "wine-metatrader5-pip-install.log"),
            "wine-mt5linux-pip-install": _tail_file(logdir / "wine-mt5linux-pip-install.log"),
            "mt5-terminal": _tail_file(logdir / "mt5-terminal.log"),
            "mt5-launch-wrapper": _tail_file(logdir / "mt5-launch-wrapper.log"),
            "mt5-dismiss": _tail_file(logdir / "mt5-dismiss.log"),
            "mt5-ipc-probe": _tail_file(ipc_probe_log),
        },
    }


@app.get("/debug/processes", dependencies=[Depends(require_secret)])
def debug_processes():
    import subprocess
    try:
        ps = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=5)
        all_lines = ps.stdout.splitlines()
        wine_lines = [l for l in all_lines if any(
            kw in l.lower() for kw in ["wine", "terminal", "xvfb", "python", "mt5"]
        )]
    except Exception as exc:
        wine_lines = [f"ps failed: {exc}"]
    return {"wine_processes": wine_lines}


@app.get("/debug/mt5-ipc-test", dependencies=[Depends(require_secret)])
def debug_mt5_ipc_test(
    with_credentials: bool = Query(True),
    portable: bool | None = Query(None),
    timeout_ms: int = Query(60_000, ge=5_000, le=120_000),
):
    import subprocess

    python_path = Path("/opt/wine_python_exe.path")
    if not python_path.exists():
        return {"error": "wine_python_exe.path sentinel not found"}
    wine_python = python_path.read_text().strip()

    portable_flag = (
        portable if portable is not None
        else (os.environ.get("MT5_CONTEXT_MODE", "").lower() == "portable")
    )
    if with_credentials and not settings.mt_login:
        return {"error": "MT_LOGIN unset or zero; set with_credentials=false or configure MT_LOGIN"}

    script = _wine_mt5_ipc_probe_script(
        with_credentials=with_credentials,
        portable=portable_flag,
        timeout_ms=timeout_ms,
    )
    env = {
        **os.environ,
        "DISPLAY": os.environ.get("DISPLAY", ":99"),
        "WINEPREFIX": os.environ.get("WINEPREFIX", "/opt/wineprefix"),
        "WINEDEBUG": "-all",
    }
    wall_timeout = max(95, timeout_ms // 1000 + 35)
    try:
        r = subprocess.run(
            ["wine", wine_python, "-c", script],
            env=env, capture_output=True, text=True, timeout=wall_timeout
        )
        parsed = _parse_ipc_probe_stdout(r.stdout.strip())
        return {
            "with_credentials": with_credentials,
            "portable": portable_flag,
            "timeout_ms": timeout_ms,
            "returncode": r.returncode,
            "stdout": r.stdout.strip(),
            "stderr": r.stderr.strip()[-2000:],
            "ok": parsed["ok"],
            "err_code": parsed["err_code"],
            "err_message": parsed["err_message"],
        }
    except subprocess.TimeoutExpired:
        return {"error": f"subprocess timed out after {wall_timeout}s"}
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/debug/pipes", dependencies=[Depends(require_secret)])
def debug_pipes():
    import subprocess
    python_path = Path("/opt/wine_python_exe.path")
    wine_python = python_path.read_text().strip() if python_path.exists() else None
    env = {**os.environ, "DISPLAY": ":99", "WINEPREFIX": "/opt/wineprefix", "WINEDEBUG": "-all"}
    results = {}
    try:
        r = subprocess.run(
            ["wine", "cmd", "/c", "dir \\\\.\\pipe\\"],
            env=env, capture_output=True, text=True, timeout=15
        )
        results["cmd_dir_pipe"] = (r.stdout + r.stderr).strip()[-3000:]
    except Exception as exc:
        results["cmd_dir_pipe"] = f"error: {exc}"
    if wine_python:
        script = (
            "import ctypes, ctypes.wintypes\n"
            "EnumWindows = ctypes.windll.user32.EnumWindows\n"
            "WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)\n"
            "GetWindowTextW = ctypes.windll.user32.GetWindowTextW\n"
            "GetWindowTextLengthW = ctypes.windll.user32.GetWindowTextLengthW\n"
            "IsWindowVisible = ctypes.windll.user32.IsWindowVisible\n"
            "titles = []\n"
            "def cb(hwnd, _):\n"
            "    if IsWindowVisible(hwnd):\n"
            "        n = GetWindowTextLengthW(hwnd)\n"
            "        if n > 0:\n"
            "            b = ctypes.create_unicode_buffer(n+1)\n"
            "            GetWindowTextW(hwnd, b, n+1)\n"
            "            titles.append(b.value)\n"
            "    return True\n"
            "EnumWindows(WNDENUMPROC(cb), 0)\n"
            "print('TOTAL_WINDOWS=' + str(len(titles)))\n"
            "for t in titles: print('WIN:', t)\n"
        )
        try:
            r2 = subprocess.run(
                ["wine", wine_python, "-c", script],
                env=env, capture_output=True, text=True, timeout=20
            )
            results["wine_python_windows"] = (r2.stdout + r2.stderr).strip()[-3000:]
        except Exception as exc:
            results["wine_python_windows"] = f"error: {exc}"
    return results


@app.get("/debug/screenshot", dependencies=[Depends(require_secret)])
def debug_screenshot():
    import subprocess
    import base64
    env = {**os.environ, "DISPLAY": ":99"}
    path = "/tmp/mt5-screenshot.png"
    for cmd in [
        ["scrot", "-z", path],
        ["ffmpeg", "-y", "-f", "x11grab", "-video_size", "1280x720",
         "-i", ":99.0", "-vframes", "1", path],
    ]:
        try:
            r = subprocess.run(cmd, env=env, capture_output=True, timeout=10)
            if r.returncode == 0:
                data = open(path, "rb").read()
                return {
                    "format": "png",
                    "tool": cmd[0],
                    "image_b64": base64.b64encode(data).decode(),
                }
        except FileNotFoundError:
            continue
        except Exception as exc:
            return {"error": str(exc)}
    return {"error": "Neither scrot nor ffmpeg available"}


# ---------------------------------------------------------------------------
# Account / connection management
# ---------------------------------------------------------------------------

@app.get("/account", dependencies=[Depends(require_secret)])
def account():
    try:
        return adapter.account()
    except RuntimeError as exc:
        err_msg = str(exc)
        if "not connected" in err_msg or "ipc not ready" in err_msg:
            raise HTTPException(status_code=503, detail=err_msg)
        raise HTTPException(status_code=502, detail=err_msg)


@app.post("/reset", dependencies=[Depends(require_secret)])
def reset_connection():
    adapter.reset_connection()
    return {"reset": True, "message": "Adapter connection reset. Next request will reconnect."}


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

@app.get("/positions", dependencies=[Depends(require_secret)])
def positions():
    return {"positions": _get_positions_full()}


@app.get("/orders", dependencies=[Depends(require_secret)])
def orders():
    """List live pending orders (BUY_LIMIT / SELL_LIMIT / BUY_STOP / SELL_STOP)."""
    try:
        return {"orders": adapter.orders()}
    except RuntimeError as exc:
        err_msg = str(exc)
        if "not connected" in err_msg or "ipc not ready" in err_msg:
            raise HTTPException(status_code=503, detail=err_msg)
        raise HTTPException(status_code=502, detail=err_msg)



def _get_positions_full():
    """
    Returns full position data including sl, tp, and current profit.
    The backend's position_stream service diffs on these fields to detect
    partial closes and SL/TP modifications.

    RPyC proxies do not support hasattr() reliably — attribute access on a
    missing field raises AttributeError across the wire, which also spams
    the RPyC server log (Thread-6 exceptions). We therefore use a helper
    that catches AttributeError locally without touching the remote object
    a second time. Index-based access (p[N]) is used for fields that are
    always present in the MT5 TradePosition namedtuple, which avoids any
    remote __getattr__ round-trips for those fields.

    TradePosition field order (MetaTrader5 ≥ 5.0.37, all builds):
        0  time          8  price_open    16  tp
        1  type          9  sl            17  swap
        2  magic        10  price_current 18  profit
        3  identifier   11  volume        19  symbol
        4  reason       12  price_stoplimit 20 comment
        5  volume       13  (reserved)    21  external_id
        6  price_open   14  (reserved)
        7  sl           15  (reserved)
    Actual layout varies by build; attribute access is safer for named
    fields, but we guard every access individually so a single missing
    attribute never breaks the whole response.
    """
    from .mt5_adapter import adapter

    def _safe(obj, attr, default=0.0):
        """
        Safely read an attribute from an RPyC-proxied namedtuple.
        Catches AttributeError so that optional/build-specific fields
        (e.g. 'commission') never propagate an exception to the caller
        or generate noise in the RPyC server log.
        """
        try:
            return getattr(obj, attr)
        except AttributeError:
            return default

    try:
        adapter.ensure_connection()
        if adapter._mt is None or not adapter.connected:
            return []
        rows = adapter._mt.positions_get()
        if rows is None:
            return []
        out = []
        for p in rows:
            try:
                out.append({
                    "ticket":       _safe(p, "ticket",        0),
                    "symbol":       _safe(p, "symbol",        ""),
                    "type":         "BUY" if _safe(p, "type", 0) == 0 else "SELL",
                    "volume":       _safe(p, "volume",        0.0),
                    "openPrice":    _safe(p, "price_open",    0.0),
                    "currentPrice": _safe(p, "price_current", 0.0),
                    "sl":           _safe(p, "sl",            0.0),
                    "tp":           _safe(p, "tp",            0.0),
                    "profit":       _safe(p, "profit",        0.0),
                    "swap":         _safe(p, "swap",          0.0),
                    # 'commission' was added in a later MT5 build; guard it explicitly
                    "commission":   0.0,  # not exposed by TradePosition in this MT5 build — fetch from history_deals_get instead
                    "openTime":     _safe(p, "time",          0),
                    "magic":        _safe(p, "magic",         0),
                    "comment":      _safe(p, "comment",       ""),
                })
            except Exception as row_exc:
                logger.warning("skipping malformed position row: %s", row_exc)
                continue
        return out
    except Exception as exc:
        logger.warning("positions_get failed: %s", exc)
        return []
 
# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

@app.post("/order", dependencies=[Depends(require_secret)])
def order(payload: OrderRequest):
    """Place a market order (BUY or SELL at current price)."""
    side = payload.type.upper()
    if side not in {"BUY", "SELL"}:
        raise HTTPException(status_code=400, detail="type must be BUY or SELL")
    try:
        return adapter.place_order(payload.model_dump())
    except AutoTradingDisabledError as exc:
        # AutoTrading is OFF in the terminal — a persistent config state, not a
        # transient throttle. 503 (not 429) so callers/operators see the real
        # cause instead of a misleading "too many requests".
        raise HTTPException(status_code=503, detail=str(exc))
    except TooManyRequestsError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        err_msg = str(exc)
        if "not connected" in err_msg or "ipc not ready" in err_msg:
            raise HTTPException(status_code=503, detail=err_msg)
        raise HTTPException(status_code=502, detail=err_msg)


@app.post("/order/limit", dependencies=[Depends(require_secret)])
def limit_order(payload: LimitOrderRequest):
    """
    Place a pending limit or stop order.

    **type** must be one of: `BUY_LIMIT`, `SELL_LIMIT`, `BUY_STOP`, `SELL_STOP`.

    - **BUY_LIMIT** — buy when Ask drops to `price` (price < current Ask)
    - **SELL_LIMIT** — sell when Bid rises to `price` (price > current Bid)
    - **BUY_STOP** — buy when Ask rises to `price` (price > current Ask)
    - **SELL_STOP** — sell when Bid drops to `price` (price < current Bid)

    Optionally set `expiration` (ISO datetime) to auto-cancel the order.
    """
    order_type = payload.type.upper()
    valid_types = {"BUY_LIMIT", "SELL_LIMIT", "BUY_STOP", "SELL_STOP"}
    if order_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"type must be one of {sorted(valid_types)}")
    try:
        return adapter.place_limit_order(payload.model_dump())
    except AutoTradingDisabledError as exc:
        # AutoTrading is OFF in the terminal — a persistent config state, not a
        # transient throttle. 503 (not 429) so callers/operators see the real
        # cause instead of a misleading "too many requests".
        raise HTTPException(status_code=503, detail=str(exc))
    except TooManyRequestsError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        err_msg = str(exc)
        if "not connected" in err_msg or "ipc not ready" in err_msg:
            raise HTTPException(status_code=503, detail=err_msg)
        raise HTTPException(status_code=502, detail=err_msg)


# ---------------------------------------------------------------------------
# Close (full or partial)
# ---------------------------------------------------------------------------

@app.post("/close", dependencies=[Depends(require_secret)])
def close(payload: CloseRequest):
    """
    Close a position fully or partially.

    Omit `volume` (or set to null) to close the full position.
    Supply `volume` less than the position size for a partial close.
    """
    try:
        return adapter.close_position(payload.ticket, payload.volume)
    except AutoTradingDisabledError as exc:
        # AutoTrading is OFF in the terminal — a persistent config state, not a
        # transient throttle. 503 (not 429) so callers/operators see the real
        # cause instead of a misleading "too many requests".
        raise HTTPException(status_code=503, detail=str(exc))
    except TooManyRequestsError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        err_msg = str(exc)
        if "not connected" in err_msg or "ipc not ready" in err_msg:
            raise HTTPException(status_code=503, detail=err_msg)
        raise HTTPException(status_code=502, detail=err_msg)


# ---------------------------------------------------------------------------
# Modify SL/TP
# ---------------------------------------------------------------------------

@app.post("/modify", dependencies=[Depends(require_secret)])
def modify(payload: ModifyRequest):
    """
    Modify the stop loss and/or take profit of an open position.

    - Pass `stopLoss` and/or `takeProfit` to update them.
    - Omit a field (or pass `null`) to leave it unchanged.
    - Pass `0.0` to remove an existing SL or TP.

    Only open positions are supported; pending orders are not.
    """
    if payload.stopLoss is None and payload.takeProfit is None:
        raise HTTPException(status_code=400, detail="Provide at least one of stopLoss or takeProfit")
    try:
        return adapter.modify_position(payload.ticket, payload.stopLoss, payload.takeProfit)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        err_msg = str(exc)
        if "not connected" in err_msg or "ipc not ready" in err_msg:
            raise HTTPException(status_code=503, detail=err_msg)
        raise HTTPException(status_code=502, detail=err_msg)


# ---------------------------------------------------------------------------
# Historical data / deals
# ---------------------------------------------------------------------------

@app.get("/candles", dependencies=[Depends(require_secret)])
def candles(payload: CandlesRequest = Depends()):
    """
    Fetch historical OHLCV candles from the MT5 terminal.

    Query params:
        symbol: e.g. XAUUSD
        timeframe: 1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w (default: 1h)
        from_date: ISO date e.g. 2024-01-01
        to_date: ISO date e.g. 2024-12-31
    """
    try:
        data = adapter.copy_rates_range(
            symbol=payload.symbol,
            timeframe=payload.timeframe,
            from_date=payload.from_date,
            to_date=payload.to_date,
        )
        return {"candles": data, "count": len(data), "symbol": payload.symbol, "timeframe": payload.timeframe}
    except RuntimeError as exc:
        err_msg = str(exc)
        if "not connected" in err_msg or "ipc not ready" in err_msg:
            raise HTTPException(status_code=503, detail=f"MT5 not connected: {err_msg}")
        raise HTTPException(status_code=502, detail=err_msg)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/deals/{ticket}", dependencies=[Depends(require_secret)])
def deals(ticket: int, lookback_days: int = Query(default=14, ge=1, le=90)):
    """
    Fetch historical deal records for a closed MT5 position ticket.
    """
    try:
        deals_list = adapter.history_deals_get(ticket=ticket, lookback_days=lookback_days)
        return {"deals": deals_list, "count": len(deals_list), "ticket": ticket}
    except RuntimeError as exc:
        err_msg = str(exc)
        if "not connected" in err_msg or "ipc not ready" in err_msg:
            raise HTTPException(status_code=503, detail=f"MT5 not connected: {err_msg}")
        raise HTTPException(status_code=502, detail=err_msg)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# ---------------------------------------------------------------------------
# Item 16 — cancel pending order + closed-deal history range
# ---------------------------------------------------------------------------

@app.post("/order/cancel", dependencies=[Depends(require_secret)])
def cancel_order(payload: CancelRequest):
    """Cancel a pending (limit/stop) order by ticket."""
    try:
        return adapter.cancel_order(payload.ticket)
    except AutoTradingDisabledError as exc:
        # AutoTrading is OFF in the terminal — a persistent config state, not a
        # transient throttle. 503 (not 429) so callers/operators see the real
        # cause instead of a misleading "too many requests".
        raise HTTPException(status_code=503, detail=str(exc))
    except TooManyRequestsError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        err_msg = str(exc)
        if "not connected" in err_msg or "ipc not ready" in err_msg:
            raise HTTPException(status_code=503, detail=err_msg)
        raise HTTPException(status_code=502, detail=err_msg)


@app.get("/history", dependencies=[Depends(require_secret)])
def history(
    from_date: str = Query(description="ISO date e.g. 2024-01-01"),
    to_date: str = Query(description="ISO date e.g. 2024-12-31"),
    symbol: str | None = Query(default=None),
):
    """All closed deals within a date range (optionally filtered by symbol)."""
    try:
        deals_list = adapter.history_deals_range(from_date, to_date, symbol)
        return {"deals": deals_list, "count": len(deals_list)}
    except RuntimeError as exc:
        err_msg = str(exc)
        if "not connected" in err_msg or "ipc not ready" in err_msg:
            raise HTTPException(status_code=503, detail=f"MT5 not connected: {err_msg}")
        raise HTTPException(status_code=502, detail=err_msg)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/tick/{symbol}", dependencies=[Depends(require_secret)])
def tick(symbol: str):
    """Current bid/ask/spread for a symbol (item 11 — spread guard source)."""
    try:
        return adapter.get_tick(symbol)
    except RuntimeError as exc:
        err_msg = str(exc)
        if "not connected" in err_msg or "ipc not ready" in err_msg:
            raise HTTPException(status_code=503, detail=f"MT5 not connected: {err_msg}")
        raise HTTPException(status_code=502, detail=err_msg)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))