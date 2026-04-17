# MT5 Bridge Service — Deployment Notes

## What We're Building

A cloud-hosted FastAPI bridge that connects the adaptive trading bot backend to a MetaTrader 5 terminal. The bridge translates REST API calls (`/order`, `/close`, `/account`, `/positions`) into MT5 operations via the `mt5linux` Python library, which uses Wine to run the Windows MT5 terminal on a Linux container.

```
Trading Bot Backend (Node.js)
    │  HTTP REST
    ▼
MT5 Bridge (FastAPI / Python)       ← hosted on HF Spaces (free, 16GB RAM)
    │  RPyC (mt5linux)
    ▼
Wine Python (RPyC server, port 18812)
    │  Windows IPC / named pipes
    ▼
MetaTrader 5 terminal64.exe (under Wine + Xvfb)
    │  TCP
    ▼
MT5 Broker Server (e.g. MetaQuotes-Demo)
```

---

## Hosting History

| Platform | RAM | Status | Issue |
|----------|-----|--------|-------|
| Render Free | 512MB | ❌ | OOM — terminal64.exe uses ~400MB |
| Render Starter | $7/mo, 512MB | ❌ | Still OOM |
| **HF Spaces CPU Basic** | **16GB** | ✅ Running | IPC timeout (current focus) |

---

## Architecture: Pre-Baked Base Image

To avoid 30-minute cold starts, we build a custom base image (`ghcr.io/loriloha/mt5-bridge-base:latest`) containing:

- Wine64 + Xvfb
- Windows Python 3.9 (installed via Wine)
- `MetaTrader5` + `mt5linux` Python packages (Wine-side)
- MT5 terminal64.exe pre-installed in `/opt/wineprefix/drive_c/Program Files/MetaTrader 5/`

The bridge service Dockerfile (`mt5-bridge-service/`) builds FROM this base and adds the FastAPI app layer. Cold starts are now ~2 minutes instead of 30+.

---

## Startup Sequence

```
Container start
    │
    ├─ Xvfb :99          (virtual display for Wine GUI apps)
    │
    ├─ uvicorn            (FastAPI on port 7860, starts immediately)
    │
    ├─ bootstrap-mt5.sh   (background — skips all steps on pre-baked image)
    │       └─ writes bootstrap.ready sentinel
    │
    ├─ mt5linux-launcher  (background — waits for bootstrap.ready)
    │       └─ wine python.exe -m mt5linux --host 127.0.0.1 --port 18812
    │               → RPyC server listening on 18812
    │
    └─ mt5-terminal       (background — if MT5_LAUNCH_TERMINAL=true)
            └─ wine terminal64.exe
```

The FastAPI adapter runs a background asyncio loop (T+15s, then every 60s) that calls `MetaTrader5.initialize(path, login, password, server)` via the RPyC bridge to connect to the terminal.

---

## Current Problem: IPC Timeout (-10005)

### Symptom
Every `initialize()` call returns `False` with error `(-10005, 'IPC timeout')`.

### What -10005 Means
The `MetaTrader5` Python module (running inside Wine) tried to establish Windows IPC (named pipe) communication with `terminal64.exe` but the terminal did not respond within its internal ~60s timeout.

### What We Know
- ✅ Xvfb starts fine
- ✅ Wine is working (Python starts, RPyC server starts)
- ✅ RPyC port 18812 is open and accepts connections
- ✅ `initialize()` runs to completion (returns `False`, not a timeout/exception)
- ❓ `terminal64.exe` status is **unknown** — may be running (stuck at login dialog) or crashing silently

### Root Causes Investigated (in order)

| Attempt | Hypothesis | Result |
|---------|-----------|--------|
| 1 | Wine prefix not owned by runtime user | Fixed → still IPC timeout |
| 2 | Manual terminal launch conflicts with API | Tested MT5_LAUNCH_TERMINAL=false → same timeout |
| 3 | Wrong Wine username at runtime (root vs wineuser) | Tested USER 1000 → mkdir permission denied |
| 4 | Linux path passed to Wine initialize(path=...) | Fixed → converted to `C:\...` Windows path → still timeout |
| 5 | Terminal running vs. crashing? | **Diagnostic pending** (added `/debug/processes` endpoint) |

### Next Diagnostic Step
The `/debug/processes` endpoint (just deployed) will shows `ps aux` output filtered for `wine`/`terminal`/`xvfb`. This will tell us:

- **If `terminal64.exe` is in the list** → terminal is alive but stuck behind login dialog → need one-time GUI login
- **If `terminal64.exe` is missing** → terminal is crashing silently → likely a DirectX/rendering issue under Xvfb

---

## Likely Resolution Paths

### Path A: Terminal is alive, needs one-time GUI login
MT5 requires a human to log in once before the Python IPC becomes available. Solution options:
1. **Add VNC/noVNC** to the container so the user can browser-login once. After login, credentials are saved in the Wine prefix for future restarts.
2. **Use `xdotool`** to automate typing credentials into the login dialog (fragile but no extra ports needed).

### Path B: Terminal is crashing (DirectX/graphics issue)
WineD3D software rendering might not be initializing correctly. Solutions:
1. Set `LIBGL_ALWAYS_SOFTWARE=1` + `GALLIUM_DRIVER=llvmpipe`
2. Add `mesa-utils` and software Vulkan drivers to the base image
3. Use `WINEDLLOVERRIDES="d3d*=n,b"` to force Wine's built-in D3D

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | 5555 | Port for FastAPI bridge (set to `7860` on HF Spaces) |
| `MT_LOGIN` | — | MT5 account login number (**secret**) |
| `MT_PASSWORD` | — | MT5 account password (**secret**) |
| `MT_SERVER` | — | MT5 broker server name (**secret**) |
| `MT_BRIDGE_SECRET` | — | Shared secret for API auth header (**secret**) |
| `MT5_LAUNCH_TERMINAL` | `false` | Set `true` to manually launch terminal64.exe |
| `WINEPREFIX` | `/opt/wineprefix` | Wine prefix path |
| `DISPLAY` | `:99` | Xvfb display |

---

## Key Files

| File | Purpose |
|------|---------|
| `mt5-bridge-base/Dockerfile` | Builds the pre-baked base image (Wine + Python + MT5 terminal) |
| `.github/workflows/build-mt5-base.yml` | GitHub Actions workflow to build and push the base image |
| `mt5-bridge-service/Dockerfile` | Bridge service image (FROM base + FastAPI app) |
| `mt5-bridge-service/start.sh` | Container entrypoint: Xvfb → uvicorn → bootstrap → mt5linux |
| `mt5-bridge-service/bootstrap-mt5.sh` | Installs Python/MT5 at runtime if not pre-baked |
| `mt5-bridge-service/app/mt5_adapter.py` | MT5 connection adapter with retry/backoff logic |
| `mt5-bridge-service/app/main.py` | FastAPI app with bridge endpoints |

---

## Deployment

### Push to HF Spaces (from local)
```powershell
# First time
git -C g:\adaptive-trading-bot remote add hf-space https://loriloha:HF_TOKEN@huggingface.co/spaces/loriloha/mt5-bridge-service

# Every deploy
git -C g:\adaptive-trading-bot subtree split --prefix=mt5-bridge-service -b hf-deploy-branch
git -C g:\adaptive-trading-bot push hf-space hf-deploy-branch:main --force
git -C g:\adaptive-trading-bot branch -D hf-deploy-branch
```

### Service URL
```
https://loriloha-mt5-bridge-service.hf.space
```

### Keep-Alive (prevent HF free tier sleeping)
Add a free UptimeRobot monitor pinging `https://loriloha-mt5-bridge-service.hf.space/health` every 5 minutes.
