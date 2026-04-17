import asyncio
import os
import re
import socket
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException

from .config import settings, validate_required_settings
from .mt5_adapter import adapter
from .schemas import CloseRequest, OrderRequest

app = FastAPI(title="Adaptive MT5 Bridge")


async def _background_connect_loop() -> None:
    """Proactively call ensure_connection every 60 s.

    Starts after a 15-second grace period to let Xvfb + Wine + the RPyC server
    come up before the first attempt. Once connected, continues polling to
    detect and recover from disconnections.
    """
    await asyncio.sleep(15)
    while True:
        try:
            adapter.ensure_connection()
        except Exception:
            pass
        await asyncio.sleep(60)


@app.on_event("startup")
async def startup_validation() -> None:
    validate_required_settings()
    # Kick off the background reconnect loop so the adapter connects
    # proactively without waiting for the first HTTP request.
    asyncio.create_task(_background_connect_loop())


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
            "mt5-ipc-probe": _tail_file(logdir / "mt5-ipc-probe.log"),
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
def debug_mt5_ipc_test():
    """
    Run MetaTrader5.initialize() directly inside Wine Python (bypasses RPyC).
    Tests whether Wine IPC fundamentally works with the running terminal.
    Times out after 90 seconds.
    """
    import subprocess
    python_path = Path("/opt/wine_python_exe.path")
    if not python_path.exists():
        return {"error": "wine_python_exe.path sentinel not found"}
    wine_python = python_path.read_text().strip()
    script = (
        "import MetaTrader5 as mt5; "
        "ok = mt5.initialize(); "
        "err = mt5.last_error(); "
        "mt5.shutdown(); "
        "print(f'ok={ok} err={err}')"
    )
    env = {
        **os.environ,
        "DISPLAY": os.environ.get("DISPLAY", ":99"),
        "WINEPREFIX": os.environ.get("WINEPREFIX", "/opt/wineprefix"),
        "WINEDEBUG": "-all",
    }
    try:
        r = subprocess.run(
            ["wine", wine_python, "-c", script],
            env=env, capture_output=True, text=True, timeout=90
        )
        parsed = _parse_ipc_probe_stdout(r.stdout.strip())
        return {
            "returncode": r.returncode,
            "stdout": r.stdout.strip(),
            "stderr": r.stderr.strip()[-2000:],
            "ok": parsed["ok"],
            "err_code": parsed["err_code"],
            "err_message": parsed["err_message"],
        }
    except subprocess.TimeoutExpired:
        return {"error": "subprocess timed out after 90s"}
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
            ["wine", "cmd", "/c", r"dir \\.\pipe\"],
            env=env, capture_output=True, text=True, timeout=15
        )
        results["cmd_dir_pipe"] = (r.stdout + r.stderr).strip()[-3000:]
    except Exception as exc:
        results["cmd_dir_pipe"] = f"error: {exc}"

    # 2. Enumerate pipes from Python inside Wine
    if wine_python:
        script = r"""
import os, ctypes, ctypes.wintypes
FindFirstFile = ctypes.windll.kernel32.FindFirstFileW
FindNextFile = ctypes.windll.kernel32.FindNextFileW
FindClose = ctypes.windll.kernel32.FindClose
INVALID = ctypes.c_void_p(-1).value
class WIN32_FIND_DATA(ctypes.Structure):
    _fields_ = [('dwFileAttributes',ctypes.wintypes.DWORD),
                ('ftCreationTime', ctypes.c_ulonglong),
                ('ftLastAccessTime', ctypes.c_ulonglong),
                ('ftLastWriteTime', ctypes.c_ulonglong),
                ('nFileSizeHigh', ctypes.wintypes.DWORD),
                ('nFileSizeLow', ctypes.wintypes.DWORD),
                ('dwReserved0', ctypes.wintypes.DWORD),
                ('dwReserved1', ctypes.wintypes.DWORD),
                ('cFileName', ctypes.c_wchar * 260),
                ('cAlternateFileName', ctypes.c_wchar * 14)]
fd = WIN32_FIND_DATA()
h = FindFirstFile(r'\\.\pipe\*', ctypes.byref(fd))
pipes = []
if h != INVALID:
    while True:
        pipes.append(fd.cFileName)
        if not FindNextFile(h, ctypes.byref(fd)): break
    FindClose(h)
print('\n'.join(p for p in pipes if 'meta' in p.lower() or 'mt5' in p.lower() or 'metatrader' in p.lower()) or 'no_mt5_pipes_found')
print('TOTAL_PIPES=' + str(len(pipes)))
"""
        try:
            r2 = subprocess.run(
                ["wine", wine_python, "-c", script],
                env=env, capture_output=True, text=True, timeout=20
            )
            results["wine_python_pipes"] = (r2.stdout + r2.stderr).strip()[-2000:]
        except Exception as exc:
            results["wine_python_pipes"] = f"error: {exc}"

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
