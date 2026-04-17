# MT5 Bridge Service — Deployment Notes

## What We're Building

A cloud-hosted FastAPI bridge that connects the adaptive trading bot backend to a MetaTrader 5 terminal. The bridge translates REST API calls (`/order`, `/close`, `/account`, `/positions`) into MT5 operations via the `mt5linux` Python library, which uses RPyC to communicate with a Wine-hosted Windows Python process that talks to `terminal64.exe`.

```
Trading Bot Backend (Node.js)
    │  HTTP REST
    ▼
MT5 Bridge (FastAPI / Python)       ← hosted on HF Spaces (free, 16GB RAM)
    │  RPyC (mt5linux, port 18812)
    ▼
Wine Python (RPyC server, port 18812)
    │  Windows IPC / named pipes
    ▼
MetaTrader 5 terminal64.exe (under Wine + Xvfb :99)
    │  TCP
    ▼
MT5 Broker Server (e.g. MetaQuotes-Demo)
```

---

## Hosting History

| Platform | RAM | Status | Issue |
|----------|-----|--------|-------|
| Render Free | 512MB | ❌ | OOM — terminal64.exe uses ~400MB |
| Render Starter | $7/mo | ❌ | File ownership errors with pre-baked Wine prefix |
| **HF Spaces CPU Basic** | **16GB** | ✅ Running | IPC connection being stabilised (current) |

---

## Architecture: Pre-Baked Base Image

To avoid 30-minute cold starts, we build a custom base image (`ghcr.io/loriloha/mt5-bridge-base:latest`) via GitHub Actions, containing:

- Wine 11.6 devel + Xvfb
- Windows Python 3.9 (installed via Wine)
- `MetaTrader5` + `mt5linux` Python packages (Wine-side)
- MT5 terminal64.exe pre-installed in `/opt/wineprefix/drive_c/Program Files/MetaTrader 5/`
- Saved MetaQuotes demo session (terminal auto-connects on startup)

The bridge service Dockerfile (`mt5-bridge-service/`) builds FROM this base and only adds the FastAPI app layer. Cold starts are ~2 minutes instead of 30+.

---

## Startup Sequence (runtime)

```
Container start
    │
    ├─ Xvfb :99              (virtual display for Wine GUI apps)
    ├─ uvicorn               (FastAPI on port 7860, starts immediately)
    ├─ bootstrap-mt5.sh      (background — skips all installs on pre-baked image)
    │       └─ writes bootstrap.ready sentinel
    ├─ mt5linux-launcher     (waits for bootstrap.ready)
    │       └─ wine python.exe -m mt5linux --host 127.0.0.1 --port 18812
    │               → RPyC server listening on 18812
    └─ mt5-terminal          (if MT5_LAUNCH_TERMINAL=true)
            └─ wine terminal64.exe
                    → loads saved session → connects to MetaQuotes demo
                    → updates 453 MQL5 files (~8 min cold start)
                    → recompiles MQL5 → shows charts
                    → IPC probe loop runs in start.sh
                    → writes mt5_ipc.ready when initialize() attach succeeds
```

The FastAPI adapter runs an asyncio background loop (T+15s, every 60s thereafter) that calls `MetaTrader5.initialize(login, password, server)` via RPyC to authenticate with your broker account. Adapter retries now classify `-10005` as `ipc_timeout` and prioritize no-path attach retries before any path fallback.

---

## Environment Variables (HF Spaces)

| Variable | Value | Notes |
|----------|-------|-------|
| `PORT` | `7860` | HF Spaces required port |
| `MT5_LAUNCH_TERMINAL` | `true` | Launch terminal64.exe on startup |
| `MT_LOGIN` | `<number>` | Broker account number — **Secret** |
| `MT_PASSWORD` | `<pass>` | Broker password — **Secret** |
| `MT_SERVER` | `<server>` | Broker server name — **Secret** |
| `MT_BRIDGE_SECRET` | `<token>` | Shared API auth secret — **Secret** |
| `WINEPREFIX` | `/opt/wineprefix` | Pre-baked Wine prefix |
| `DISPLAY` | `:99` | Xvfb display |
| `MT5_CONTEXT_MODE` | `portable` | Terminal context mode (`portable`, `data_dir`, `default`) |
| `MT5_CONTEXT_DIR` | `/opt/wineprefix/drive_c/mt5-data` | Data directory when `MT5_CONTEXT_MODE=data_dir` |

---

## IPC Timeout Debugging Chronicle

All attempts resulted in `(-10005, 'IPC timeout')` from `MetaTrader5.initialize()`.

### What -10005 Means
The `MetaTrader5` Python module (running inside Wine Python via RPyC) tried to communicate with `terminal64.exe` via Windows named pipes but the terminal's IPC pipe was not found within the module's internal ~60s window.

### Confirmed Facts (via diagnostics)
| Fact | How confirmed |
|------|--------------|
| ✅ Xvfb starts | startup logs |
| ✅ Wine starts | wineserver in `ps aux` |
| ✅ RPyC server port 18812 opens | `/debug/mt5` → `port_open=True` |
| ✅ `initialize()` runs to completion | error is returned result, not RPyC exception |
| ✅ **terminal64.exe IS running** | `/debug/processes` → PID visible, 241MB RAM, `Sl` state |
| ✅ **Terminal shows full charts** | `/debug/screenshot` → live market data, loaded session |
| ✅ Terminal logged in (MetaQuotes demo) | screenshot Journal tab confirms |
| ❓ IPC pipe reachable from Python? | **See current attempt below** |

### Root Causes Investigated

| # | Hypothesis | Result |
|---|-----------|--------|
| 1 | Wine prefix not owned by runtime user | Fixed with `chown root:root` |
| 2 | Scottyhardy gosu switches to UID 1000 at runtime | Fixed with `ENTRYPOINT []` |
| 3 | Manual terminal launch causes login-dialog IPC block | Tested `MT5_LAUNCH_TERMINAL=false` → same error |
| 4 | Running as UID 1000 — no `/home/wineuser` | Reverted to `USER root` |
| 5 | Linux path passed to `initialize(path=...)` | Fixed → convert to `C:\...` Windows path |
| 6 | Terminal not started yet (updating in background) | Eliminated — terminal visible in screenshot |
| 7 | **`path=` causes pipe-name mismatch** | **Current fix: try `initialize()` without `path` first** |

### Current Fix (attempt 7)
When `MT5_LAUNCH_TERMINAL=true`, `start.sh` launches the terminal directly via:
```bash
wine /opt/wineprefix/drive_c/Program Files/MetaTrader 5/terminal64.exe
```
Passing `path="C:\..."` to `MetaTrader5.initialize()` tells the module to look for (or start) a terminal specifically registered under that Windows path. If the terminal's IPC pipe was registered under a slightly different name (e.g. from the Linux-path invocation), the lookup fails.

**Fix**: call `initialize(login, password, server)` first with no `path` — this finds *any* running terminal. Only fall back to `path=` if no terminal is found.

---

## Deployment

### Build and push base image
Triggered automatically by GitHub Actions on push to `main` when files under `mt5-bridge-base/` change.

### Push bridge service to HF Spaces
```powershell
# One-time remote setup
git -C g:\adaptive-trading-bot remote add hf-space `
  https://loriloha:HF_TOKEN@huggingface.co/spaces/loriloha/mt5-bridge-service

# Every deploy
git -C g:\adaptive-trading-bot subtree split --prefix=mt5-bridge-service -b hf-deploy-branch
git -C g:\adaptive-trading-bot push hf-space hf-deploy-branch:main --force
git -C g:\adaptive-trading-bot branch -D hf-deploy-branch
```

### Service URL
```
https://loriloha-mt5-bridge-service.hf.space
```

---

## Diagnostic Endpoints

All require `X-Bridge-Secret` header.

| Endpoint | What it shows |
|----------|--------------|
| `GET /health` | Basic liveness check (no auth required) |
| `GET /debug/mt5` | Port status, adapt state, last error, log tails |
| `GET /debug/processes` | `ps aux` filtered for wine/terminal/python |
| `GET /debug/mt5-ipc-test` | Direct Wine Python `MetaTrader5.initialize()` probe (parsed `ok/err_code/err_message`) |
| `GET /debug/screenshot` | Base64 PNG of the Xvfb display (see terminal UI) |
| `POST /reset` | Force adapter reconnect |
| `GET /account` | MT5 account info (requires connection) |

### IPC Readiness Gate

`start.sh` now writes these files in `LOGDIR`:
- `mt5_ipc.ready` — direct Wine Python `initialize()` attach succeeded.
- `mt5_ipc.failed` — probe attempts exhausted or terminal exited before attach.
- `mt5_ipc.status` — latest probe status line.
- `mt5-ipc-probe.log` — attempt history.
- `mt5_context.status` — selected terminal context mode and launch arguments.

`/ready` and `/debug/mt5` expose these states (`ipc_ready`, `ipc_failed`, `ipc_status`, `context_status`).

### Hard Readiness Policy

- `/ready` now hard-fails (`ready=false`) whenever `mt5_ipc.ready` is absent.
- `/account` no longer returns FALLBACK payloads when IPC is unavailable; it surfaces connection failure directly.
- This prevents false-positive readiness in environments where terminal UI is visible but MT5 IPC is still detached.

### Interpreting New Signals

- `ipc_ready=true` + `/account` still fails: likely broker login/session/credentials issue, not Wine pipe attach.
- `ipc_ready=false` and `/debug/mt5-ipc-test` gives `err_code=-10005`: MT5 IPC attach is still failing at Wine/terminal layer.
- `ipc_failed=true` quickly after startup: terminal likely exited or IPC never became attachable in allotted warmup window.
- `ipc_ready=false` + repeated `-10005` under fixed `context_status` (e.g. `mode=portable`) strongly suggests Wine/MT5 runtime compatibility limits rather than startup sequencing.

### Screenshot command
```powershell
$hfToken = "<YOUR_HF_READ_TOKEN>"
$h = @{"Authorization"="Bearer $hfToken"; "X-Bridge-Secret"="<YOUR_BRIDGE_SECRET>"}
$r = Invoke-RestMethod -Uri "https://loriloha-mt5-bridge-service.hf.space/debug/screenshot" -Headers $h
[IO.File]::WriteAllBytes("$env:USERPROFILE\Desktop\mt5-screen.png",
    [Convert]::FromBase64String($r.image_b64))
```

---

## Key Files

| File | Purpose |
|------|---------|
| `mt5-bridge-base/Dockerfile` | Pre-baked base image (Wine + Python + MT5 terminal) |
| `.github/workflows/build-mt5-base.yml` | GHA workflow — builds and pushes base image to GHCR |
| `mt5-bridge-service/Dockerfile` | Bridge image (FROM base + FastAPI app) |
| `mt5-bridge-service/start.sh` | Entrypoint: Xvfb → uvicorn → bootstrap → mt5linux → terminal |
| `mt5-bridge-service/bootstrap-mt5.sh` | Runtime installer (skipped on pre-baked image) |
| `mt5-bridge-service/app/mt5_adapter.py` | MT5 adapter with retry/backoff + Wine path conversion |
| `mt5-bridge-service/app/main.py` | FastAPI routes + diagnostic endpoints |
| `mt5-bridge-service/DEPLOYMENT.md` | This file |

---

## Uptime / Keep-Alive

HF Spaces free tier sleeps after ~15 minutes of inactivity. Add a free **UptimeRobot** monitor:
- URL: `https://loriloha-mt5-bridge-service.hf.space/health`
- Interval: every 5 minutes
- Type: HTTP(s)

This prevents the Space from sleeping and keeps the MT5 terminal session alive.
