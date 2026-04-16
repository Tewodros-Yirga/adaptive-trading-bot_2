# Render Deployment Guide (Python API + Separate MT5 Bridge Service)

This guide deploys:
- **Account A**: Python API + dashboard web service
- **Account B**: MT5 bridge web service (separate Render account)

## 1) Prerequisites

- Two Render accounts (or two teams/projects): one for API, one for bridge
- This repository pushed to GitHub/GitLab
- MT5 credentials and broker server details

## 2) Repository Layout

Deployables in this repo:

- `python-backend/` (FastAPI API + dashboard templates, Account A)
- `mt5-bridge-service/` (MT5 bridge container scaffold, Account B)

Render specs:

- `render.yaml` (Account A: Python API service)
- `render.bridge.yaml` (Account B: MT5 bridge service)

## 3) Deploy MT5 Bridge Service (Account B)

1. Sign in to **Render Account B**
2. Click **New** -> **Blueprint**
3. Connect this repository
4. Select **`render.bridge.yaml`**
5. Deploy `adaptive-trading-mt5-bridge`

Set environment variables on bridge service:

- `MT_LOGIN`
- `MT_PASSWORD`
- `MT_SERVER`
- `MT_BRIDGE_SECRET`
- `MT5_INSTALLER_URL` (recommended unless you bake terminal into image)
- `MT_TERMINAL_EXE` (optional override; default path already set)
- `MT_FALLBACK_MODE` (`true` for safe startup; set `false` once MT5 is fully wired)

Important:
- Current `mt5-bridge-service/` is a **scaffold**. Replace `start.sh` with your real startup flow (Wine + MT5 + bridge API on port `5555`).
- Bridge must expose:
  - `POST /order`
  - `POST /close`
  - `GET /account`
  - `GET /positions`
  - `GET /ready`

After bridge deploy, copy bridge public URL:

- `https://adaptive-trading-mt5-bridge.onrender.com`

## 4) Create Postgres Database on Render (Account A)

1. In Render dashboard, click **New** -> **PostgreSQL**
2. Choose:
   - Name: `adaptive-trading-db`
   - Plan: Free (or Starter for better reliability)
3. After creation, copy **Internal Database URL** (or External URL if needed)

## 5) Create the Python Web Service (Account A)

1. Click **New** -> **Blueprint**
2. Connect your repo
3. Render detects `render.yaml` and creates `adaptive-trading-python-api`
4. Confirm service root is `python-backend`
5. Deploy

## 6) Configure Environment Variables (Account A)

Set these in Render service **Environment**:

- `DATABASE_URL` = your Render Postgres URL
- `MT_BRIDGE_URL` = bridge URL from Account B (example: `https://adaptive-trading-mt5-bridge.onrender.com`)
- `MT_BRIDGE_SECRET` = same secret configured in Account B bridge service
- `WEBHOOK_SECRET` = secure webhook token for TradingView

Optional tuning variables (safe defaults already present in `render.yaml`):

- `SIMULATION_MODE=false`
- `ADAPTATION_LR=0.002`
- `ADAPTATION_MAX_CHANGE_PCT=0.3`
- `ADAPTATION_CONFIDENCE_THRESHOLD=0.05`
- `ADAPTATION_INTERVAL=20`
- `ADAPTATION_MIN_CLOSED_TRADES=20`
- `ADAPTATION_COOLDOWN_TRADES=20`

## 7) Startup and Database Initialization

No manual migration command is required in this version.

On app startup:

- SQLAlchemy creates required tables automatically
- default strategy parameters are seeded if none exist

## 8) Access the Frontend Dashboard

After successful deploy:

- Open your Render web service URL: `https://<service-name>.onrender.com/`
- Dashboard sections include:
  - bot status and performance
  - account balance and live positions
  - recent trades
  - strategy parameter controls
  - learning/stability controls

Useful API checks:

- `GET /health`
- `GET /api/status`
- `GET /bridge/account`
- `GET /bridge/positions`
- `GET /trades/stats`

## 9) Configure TradingView Webhook

Point TradingView webhook URL to:

- `https://<service-name>.onrender.com/webhook`

Example payload:

```json
{
  "secret": "YOUR_WEBHOOK_SECRET",
  "signal": "BUY",
  "symbol": "XAUUSDm",
  "price": 2350.25,
  "atr": 4.2,
  "ema_fast": 2348.9,
  "ema_slow": 2346.4
}
```

## 10) Cross-Service Validation Checklist

- Bridge service health endpoint (if implemented) is reachable publicly
- API service can call:
  - `GET /bridge/account`
  - `GET /bridge/positions`
- `MT_BRIDGE_SECRET` matches in both services
- API dashboard shows bridge account/position data

- `/health` returns `{"status":"ok"}`
- `/` dashboard loads without 500 errors
- dashboard can save parameter changes
- dashboard can save learning controls
- `/bridge/account` returns data from bridge (or expected error if bridge unavailable)
- simulated or real trades appear in recent trade table

## 11) Troubleshooting

## Database connection fails

- Verify `DATABASE_URL` is set
- Confirm database is running in Render
- Redeploy after env var changes

## Bridge errors on dashboard/API

- Check `MT_BRIDGE_URL` points to Account B bridge URL
- Check `MT_BRIDGE_SECRET` matches both services
- Check bridge service logs in Account B
- Confirm bridge endpoint implements `/account`, `/positions`, `/order`, `/close`
- Confirm bridge listens on port `5555` inside container and Render routes correctly

## Dashboard does not update controls

- Check service logs for validation errors
- Ensure submitted values are within allowed bounds
- Refresh dashboard and confirm values persisted

## App cold starts on free tier

- First request can be slow on free plan
- For lower latency, upgrade plan or keep-alive ping

## 12) Operational Notes

- Keep `SIMULATION_MODE=false` for live mode
- Start with conservative adaptation values
- Monitor drawdown and profit factor daily
- Rotate `WEBHOOK_SECRET` and bridge secrets periodically
- Keep API and bridge deploys decoupled: bridge can be restarted/upgraded without redeploying API
