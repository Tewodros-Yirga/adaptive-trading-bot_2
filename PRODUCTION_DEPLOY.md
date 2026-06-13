# Production Deployment Guide

Step-by-step instructions for deploying all four services of the Adaptive Trading Bot. Read `SYSTEM_ARCHITECTURE.md` for full system context.

> **⚠️ This is live-money trading software.** Test with a demo account first. Never deploy to a live broker without verifying every health check below.

---

## Prerequisites

| Requirement | Purpose |
|-------------|---------|
| Git | Source control + subtree deploys |
| Python 3.11+ | Local testing |
| Node.js 18+ | Frontend build |
| HuggingFace account | Hosting (free Spaces) |
| MongoDB Atlas cluster | Shared database |
| MT5 broker account | Live or demo trading |
| UptimeRobot account (free) | Keep-alive monitoring |

---

## 1. Service Startup Order

Services must be started in this order due to dependencies:

```
1. MongoDB Atlas       ← always-on (cloud)
2. MT5 Bridge (B)      ← needs MongoDB (none), broker credentials
3. Python Backend (A)  ← needs MongoDB, Bridge URL
4. Backtester (C)      ← needs MongoDB, Bridge URL
5. Frontend (D)        ← needs Backend URL
```

The **Bridge** should be up first because the Backend's startup checks probe bridge connectivity, and the live trading loop begins ~30s after boot. The **Backtester** can start at any time — it's independent except for OHLCV data (via bridge) and MongoDB.

---

## 2. Service A — Python Backend

**Source:** `python-backend/`
**HF Space:** `loriloha/mt5-backend-service`

### Environment Variables

| Variable | Required | Secret | Default | Description |
|----------|----------|--------|---------|-------------|
| `MONGODB_URI` | ✅ | ✅ | — | MongoDB Atlas connection string |
| `MT_BRIDGE_URL` | ✅ | No | — | Bridge service URL (e.g. `https://loriloha-mt5-bridge-service.hf.space`) |
| `MT_BRIDGE_SECRET` | ✅ | ✅ | — | Shared secret for bridge auth (`X-Bridge-Secret`) |
| `MT_BRIDGE_HF_TOKEN` | If bridge is private | ✅ | — | HF read token for private Space auth |
| `JWT_SECRET_KEY` | ✅ | ✅ | — | JWT signing key — **must not be empty in production** |
| `ADMIN_USERNAME` | ✅ | No | — | Initial admin user |
| `ADMIN_PASSWORD` | ✅ | ✅ | — | Initial admin password — **must not be empty** |
| `PORT` | No | No | `8000` | Listening port (HF requires `7860`) |
| `BACKTESTER_SERVICE_URL` | Optional | No | — | Backtester URL for keepalive/status |
| `BACKTESTER_HF_TOKEN` | If backtester is private | ✅ | — | HF token for backtester Space |
| `SIMULATION_MODE` | No | No | `false` | `true` = paper trading (no real orders) |
| `APP_API_KEY` | Optional | ✅ | — | API key for webhook endpoint (`X-API-Key`) |
| `ADAPTATION_MAX_CHANGE_PCT` | No | No | `0.3` | Max parameter nudge per adaptation cycle |
| `ADAPTATION_LR` | No | No | `0.002` | Adaptation learning rate |
| `ADAPTATION_CONFIDENCE_THRESHOLD` | No | No | `0.05` | Min confidence to trigger adaptation |
| `PEER_HEALTHCHECK_URL` | Optional | No | — | URL to ping for mutual keepalive |
| `PEER_HEALTHCHECK_BEARER_TOKEN` | Optional | ✅ | — | Bearer token for peer ping |

### Deploy

> **PowerShell note:** The `git push` must be a **single line** — do not break it across lines. Set `$HF_TOKEN` first so you don't paste credentials into history.

```powershell
Set-Location "g:\adaptive-trading-bot"

# 0. Set your HF write token (do this once per session)
$HF_TOKEN = "hf_your_token_here"

# 1. Stage and commit
git add python-backend
git commit -m "feat: <description>"

# 2. Create subtree branch
git subtree split --prefix=python-backend -b hf-backend-deploy

# 3. Push to HF Space (MUST be one line)
git push --force "https://loriloha:${HF_TOKEN}@huggingface.co/spaces/loriloha/mt5-backend-service" hf-backend-deploy:main

# 4. Cleanup
git branch -D hf-backend-deploy
```

### Verify Health

```powershell
# 1. Basic liveness
Invoke-RestMethod "https://loriloha-mt5-backend-service.hf.space/health"
# Expected: {"status":"ok","version":"2.0.0"}

# 2. Login and get JWT
$login = Invoke-RestMethod -Method POST `
  -Uri "https://loriloha-mt5-backend-service.hf.space/auth/login" `
  -Body (@{username="<admin>";password="<pass>"} | ConvertTo-Json) `
  -ContentType "application/json"
$jwt = $login.access_token

# 3. System health (requires JWT)
$h = @{Authorization = "Bearer $jwt"}
Invoke-RestMethod -Uri "https://loriloha-mt5-backend-service.hf.space/system/health" `
  -Headers $h
# Check: db=ok, bridge=ok, strategies_registered > 0
```

---

## 3. Service B — MT5 Bridge

**Source:** `mt5-bridge-service/`
**HF Space:** `loriloha/mt5-bridge-service`
**Base Image:** `ghcr.io/loriloha/mt5-bridge-base:latest` (built by GitHub Actions)

### Environment Variables

| Variable | Required | Secret | Default | Description |
|----------|----------|--------|---------|-------------|
| `MT_LOGIN` | ✅ | ✅ | — | Broker account number |
| `MT_PASSWORD` | ✅ | ✅ | — | Broker account password |
| `MT_SERVER` | ✅ | ✅ | — | Broker server (e.g. `Exness-MT5Real`) |
| `MT_BRIDGE_SECRET` | ✅ | ✅ | — | Shared API auth secret (same as backend) |
| `PORT` | No | No | `7860` | Listening port |
| `MT5_LAUNCH_TERMINAL` | No | No | `true` | Launch terminal64.exe on startup |
| `WINEPREFIX` | No | No | `/opt/wineprefix` | Pre-baked Wine prefix path |
| `DISPLAY` | No | No | `:99` | Xvfb display number |
| `MT5LINUX_HOST` | No | No | `127.0.0.1` | RPyC server host |
| `MT5LINUX_PORT` | No | No | `18812` | RPyC server port |
| `MT_FALLBACK_MODE` | No | No | `true` | Use simulated responses when MT5 unavailable |
| `MT5_INSTALLER_URL` | Optional | No | — | URL to download MT5 installer (fallback) |
| `MT5_PORTABLE_ZIP_URL` | Optional | No | — | URL to portable MT5 ZIP (build-mismatch self-heal) |
| `PEER_HEALTHCHECK_URL` | Optional | No | — | Backend URL for mutual keepalive |
| `PEER_HEALTHCHECK_BEARER_TOKEN` | Optional | ✅ | — | HF token for peer keepalive |

### Base Image Rebuild

The base image is auto-built by GitHub Actions when `mt5-bridge-base/` changes. To manually trigger:

1. Go to **Actions → Build MT5 Base (Dispatch)** on GitHub
2. Click **Run workflow**
3. Wait ~25 min for the build to complete
4. The bridge's next deploy will pull the updated base

### Deploy

```powershell
Set-Location "g:\adaptive-trading-bot"

# 0. Set your HF write token (do this once per session)
$HF_TOKEN = "hf_your_token_here"

# 1. Stage and commit
git add mt5-bridge-service
git commit -m "fix: <description>"

# 2. Create subtree branch
git subtree split --prefix=mt5-bridge-service -b hf-bridge-deploy

# 3. Push to HF Space (MUST be one line)
git push --force "https://loriloha:${HF_TOKEN}@huggingface.co/spaces/loriloha/mt5-bridge-service" hf-bridge-deploy:main

# 4. Cleanup
git branch -D hf-bridge-deploy
```

### Verify Health

```powershell
$secret = "<MT_BRIDGE_SECRET>"
$hfToken = "<HF_READ_TOKEN>"  # only if Space is private
$baseUrl = "https://loriloha-mt5-bridge-service.hf.space"
$h = @{"X-Bridge-Secret" = $secret}
if ($hfToken) { $h["Authorization"] = "Bearer $hfToken" }

# 1. Basic liveness (no auth)
Invoke-RestMethod "$baseUrl/health"
# Expected: {"status":"ok"}

# 2. Ready check (IPC + MT5 connection)
Invoke-RestMethod "$baseUrl/ready" -Headers $h
# Expected: {"ready":true, ...}

# 3. Account info (proves MT5 connection works)
Invoke-RestMethod "$baseUrl/account" -Headers $h
# Expected: {"balance":..., "equity":..., "margin":...}

# 4. Screenshot (optional — see the terminal UI)
$r = Invoke-RestMethod "$baseUrl/debug/screenshot" -Headers $h
[IO.File]::WriteAllBytes("$env:USERPROFILE\Desktop\mt5-screen.png",
  [Convert]::FromBase64String($r.image_b64))
```

### Post-Deploy Checks

- [ ] `/ready` returns `ready: true` (MT5 IPC connected)
- [ ] `/account` returns real balance/equity (broker authenticated)
- [ ] `/positions` returns current open positions (or empty array)
- [ ] Debug screenshot shows live charts (not a login dialog)

---

## 4. Service C — Backtester

**Source:** `backtester_service/trading-backtester/`
**Separate repo** — deployed independently to its own HF Space.

### Environment Variables

| Variable | Required | Secret | Default | Description |
|----------|----------|--------|---------|-------------|
| `MONGODB_URI` | ✅ | ✅ | — | Same MongoDB Atlas as the backend |
| `MT_BRIDGE_URL` | ✅ | No | — | Bridge URL for OHLCV data |
| `MT_BRIDGE_SECRET` | ✅ | ✅ | — | Bridge auth secret |
| `MT_BRIDGE_HF_TOKEN` | If bridge is private | ✅ | — | HF token for bridge access |
| `BACKTESTER_PORT` | No | No | `7860` | Listening port |
| `MAX_WORKERS` | No | No | `2` | ProcessPoolExecutor workers |
| `SIMULATION_MODE` | No | No | `true` | Always true for backtester |
| `STRATEGIES_TO_RUN` | No | No | all 11 | JSON array of strategy names to optimize |

### Verify Health

```powershell
$baseUrl = "https://<backtester-space-url>"

# 1. Ping
Invoke-RestMethod "$baseUrl/ping"
# Expected: {"status":"ok","uptime_seconds":...}

# 2. Full health
Invoke-RestMethod "$baseUrl/health"
# Expected: {"status":"ok","mongodb":"connected","strategies_running":[...]}

# 3. Per-strategy status
Invoke-RestMethod "$baseUrl/status"
# Shows iteration count, best score, phase, is_running, is_paused per strategy
```

---

## 5. Service D — Frontend

**Source:** `frontend/` (separate Git repo)
**Deployed to:** Vercel (configured via `vercel.json`)

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `VITE_API_URL` | ✅ | Backend URL (e.g. `https://loriloha-mt5-backend-service.hf.space`) |

### Build & Deploy

```bash
cd frontend
npm install
npm run build          # outputs to dist/
# Deploy dist/ to Vercel, Netlify, or any static host
```

The `vercel.json` rewrites all paths to `index.html` for SPA routing. The Vite dev proxy (`/api` → backend, `/ws` → WebSocket) is only active in dev mode.

### Verify

1. Navigate to the deployed URL
2. Login with admin credentials
3. Check Dashboard loads with KPI cards
4. Check "Live Trades" page connects via WebSocket (green dot in header)

---

## 6. Shared Secrets Checklist

All services that talk to each other must share the same `MT_BRIDGE_SECRET`. Verify these match:

| Secret | Services that need it |
|--------|----------------------|
| `MT_BRIDGE_SECRET` | Backend (A), Bridge (B), Backtester (C) |
| `MONGODB_URI` | Backend (A), Backtester (C) |
| `MT_LOGIN` / `MT_PASSWORD` / `MT_SERVER` | Bridge (B) only |
| `JWT_SECRET_KEY` | Backend (A) only |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Backend (A) only |
| HF tokens (`MT_BRIDGE_HF_TOKEN`, etc.) | Whichever services talk to private Spaces |

---

## 7. Monitoring & Alerts (Before Going Live)

### UptimeRobot (free — set up before live trading)

| Monitor | URL | Interval | Type |
|---------|-----|----------|------|
| Backend health | `https://<backend>/health` | 5 min | HTTP(s) |
| Bridge health | `https://<bridge>/health` | 5 min | HTTP(s) |
| Backtester ping | `https://<backtester>/ping` | 5 min | HTTP(s) |

These also serve as **keepalive pings** that prevent HF Spaces from sleeping.

### Telegram Alerts (optional but recommended)

The backend supports Telegram notifications via `scripts/telegram-relay-worker.js` (Cloudflare Worker). Configure in `app_settings`:

| Setting | Value |
|---------|-------|
| `alert_telegram_bot_token` | Your Telegram bot token |
| `alert_telegram_chat_id` | Your chat/group ID |
| `alert_min_level` | `warning` (recommended) |

Events that trigger alerts:
- Service startup/shutdown
- Trade placed / closed
- Trading halt triggered (drawdown)
- Bridge circuit breaker opened
- Adaptation rollback
- Build mismatch detected

### What to Monitor Before Real Money

- [ ] **Bridge `/ready` = true** for at least 30 minutes straight
- [ ] **At least one full trade cycle** completes in simulation mode (place + close)
- [ ] **Position reconciler** successfully matches at least one trade (check logs)
- [ ] **No `-10005` IPC errors** in bridge logs for 1+ hours
- [ ] **Ensemble voting** produces sensible output (check `/ensemble/decisions`)
- [ ] **Risk limits** tested — try placing a trade that exceeds `max_open_trades`
- [ ] **Daily loss limit** tested — verify `trading_halt` triggers correctly
- [ ] **Frontend** shows live positions updating in real time via WebSocket

---

## 8. Going Live Checklist

```
□  All 4 services deployed and healthy
□  UptimeRobot monitors active
□  Telegram alerts configured and tested
□  SIMULATION_MODE = false on backend
□  MT5 broker account is LIVE (not demo) — or demo if still testing
□  MT_BRIDGE_SECRET is a strong random string (not the default)
□  JWT_SECRET_KEY is a strong random string (not empty)
□  ADMIN_PASSWORD is strong (not empty)
□  max_open_trades set to a conservative value (2–3)
□  symbol_exposure_limit set conservatively (0.5–1.0 lots)
□  max_daily_loss_pct set (3–5%)
□  max_drawdown_pct set (15–20%)
□  ensemble_voting_threshold at 0.60+ (not aggressive)
□  First trade placed and verified in MT5 terminal
□  First trade closed and PnL reconciled correctly in DB
```

---

## 9. Rollback Procedure

If something goes wrong after deployment:

### Quick halt (no redeploy needed)
```powershell
# Set trading_halt via the API
$h = @{Authorization = "Bearer $jwt"; "Content-Type" = "application/json"}
Invoke-RestMethod -Method PUT `
  -Uri "https://<backend>/settings" `
  -Headers $h `
  -Body '{"key":"trading_halt","value":"true"}'
```

### Service rollback
HF Spaces keeps recent builds. Go to the Space's **Settings → Factory reset** to revert to the previous build, or force-push a known-good commit:

```powershell
$HF_TOKEN = "hf_your_token_here"
git push --force "https://loriloha:${HF_TOKEN}@huggingface.co/spaces/loriloha/mt5-backend-service" <known-good-commit>:main
```

---

## 10. Git History Cleanup (One-Time)

The `.git` history is ~836 MB due to old committed MT5 binaries. After all code changes are committed and pushed:

```powershell
# Install git-filter-repo
pip install git-filter-repo

# Strip all blobs > 10 MB from history
git filter-repo --strip-blobs-bigger-than 10M

# filter-repo removes remotes — re-add them
git remote add origin <your-github-url>

# Force-push (destructive — all clones must re-clone)
git push --force --all
git push --force --tags
```

> **⚠️ WARNING:** This rewrites all commit SHAs. Every existing clone, fork, and CI reference to this repo will break. Coordinate with anyone else who has a clone.

---

*See `SYSTEM_ARCHITECTURE.md` for full technical details on each service, the strategy framework, data layer, and known issues.*
