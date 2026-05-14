import asyncio
import logging
import os
import re
import socket
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query

from .config import settings, validate_required_settings
from .mt5_adapter import adapter
from .schemas import CandlesRequest, CloseRequest, OrderRequest

app = FastAPI(title="Adaptive MT5 Bridge")
logger = logging.getLogger(__name__)


async def _background_connect_loop() -> None:
    """Proactively call ensure_connection in a thread pool every 60 s.

    Starts after a 15-second grace period to let Xvfb + Wine + the RPyC server
    come up before the first attempt. Once connected, continues polling to
    detect and recover from disconnections.

    IMPORTANT: ensure_connection() is synchronous and blocks for the full
    mt5.initialize() timeout (up to 180s). Running it directly in an async
    coroutine would block the event loop, freezing ALL endpoints. Use
    asyncio.to_thread so it runs in a thread-pool worker instead.
    """
    await asyncio.sleep(15)
    while True:
        try:
            await asyncio.to_thread(adapter.ensure_connection)
        except Exception:
            pass
        await asyncio.sleep(60)


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
    # Kick off the background reconnect loop so the adapter connects
    # proactively without waiting for the first HTTP request.
    asyncio.create_task(_background_connect_loop())
    asyncio.create_task(_peer_keepalive_loop())


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
    """Python source for `wine python.exe -c` — mirrors start.sh IPC probe."""
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
    """Check which Wine/MT5 processes are running inside the container."""
    import subprocess
    try:
        ps = subprocess.run(
            ["ps", "aux"],
            capture_output=True, text=True, timeout=5
        )
        all_lines = ps.stdout.splitlines()
        wine_lines = [l for l in all_lines if any(
            kw in l.lower() for kw in ["wine", "terminal", "xvfb", "python", "mt5"]
        )]
    except Exception as exc:
        wine_lines = [f"ps failed: {exc}"]
    return {"wine_processes": wine_lines}


@app.get("/debug/mt5-ipc-test", dependencies=[Depends(require_secret)])
def debug_mt5_ipc_test(
    with_credentials: bool = Query(
        True,
        description=(
            "If true (default), call initialize(login, password, server, timeout=…) like start.sh; "
            "if false, bare initialize(timeout=…) only."
        ),
    ),
    portable: bool | None = Query(
        None,
        description="If set, forces portable=True on initialize when with_credentials is true; "
        "if omitted, uses MT5_CONTEXT_MODE==portable from the environment.",
    ),
    timeout_ms: int = Query(
        60_000,
        ge=5_000,
        le=120_000,
        description="mt5.initialize timeout in milliseconds (matches start.sh probe default).",
    ),
):
    """
    Run MetaTrader5.initialize() directly inside Wine Python (bypasses RPyC).
    By default uses the same credentialized initialize() call as the IPC probe in start.sh.
    Subprocess wall-clock timeout is max(95, timeout_ms // 1000 + 35) seconds.
    """
    import subprocess

    python_path = Path("/opt/wine_python_exe.path")
    if not python_path.exists():
        return {"error": "wine_python_exe.path sentinel not found"}
    wine_python = python_path.read_text().strip()

    portable_flag = (
        portable
        if portable is not None
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
    """List named pipes in Wine to see if terminal64.exe has registered its IPC pipe."""
    import subprocess
    python_path = Path("/opt/wine_python_exe.path")
    wine_python = python_path.read_text().strip() if python_path.exists() else None
    env = {**os.environ, "DISPLAY": ":99", "WINEPREFIX": "/opt/wineprefix",
           "WINEDEBUG": "-all"}
    results = {}

    # 1. List \\.\pipe\ via Wine cmd
    try:
        r = subprocess.run(
            ["wine", "cmd", "/c", "dir \\\\.\\pipe\\"],
            env=env, capture_output=True, text=True, timeout=15
        )
        results["cmd_dir_pipe"] = (r.stdout + r.stderr).strip()[-3000:]
    except Exception as exc:
        results["cmd_dir_pipe"] = f"error: {exc}"

    # 2. Enumerate pipes from Python inside Wine
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
    """Take a screenshot of the Xvfb display and return as base64 PNG."""
    import subprocess
    import base64
    env = {**os.environ, "DISPLAY": ":99"}
    path = "/tmp/mt5-screenshot.png"
    # Try scrot first, then ffmpeg
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


@app.get("/account", dependencies=[Depends(require_secret)])
def account():
    return adapter.account()


@app.post("/reset", dependencies=[Depends(require_secret)])
def reset_connection():
    """Force the adapter to reconnect to MT5 on the next request."""
    adapter.reset_connection()
    return {"reset": True, "message": "Adapter connection reset. Next request will reconnect."}


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
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
