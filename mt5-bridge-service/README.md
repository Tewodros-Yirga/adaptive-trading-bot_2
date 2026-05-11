---
title: MT5 Bridge Service
emoji: 📊
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# MT5 Bridge Service

FastAPI bridge connecting MetaTrader 5 (via Wine) to the trading bot backend.
Runs on Hugging Face Spaces CPU Basic (16GB RAM, x86_64, free).

## Required Environment Variables (Space Settings → Variables and Secrets)

| Variable | Description | Secret? |
|----------|-------------|---------|
| `MT_LOGIN` | MT5 account login number | ✅ |
| `MT_PASSWORD` | MT5 account password | ✅ |
| `MT_SERVER` | MT5 broker server name | ✅ |
| `MT_BRIDGE_SECRET` | Shared secret for API auth | ✅ |
| `PORT` | Must be set to `7860` | No |
| `MT5_LAUNCH_TERMINAL` | Set `true` to auto-launch MT5 terminal | No |

## API Endpoints

- `GET /health` — health check
- `GET /debug/mt5` — diagnostics (requires `X-Bridge-Secret` header)
- `POST /order` — place order
- `POST /close` — close position
- `GET /account` — account info
- `GET /positions` — open positions

## Run locally

```bash
docker build -t mt5-bridge-service .
docker run --rm -p 7860:7860 \
  -e PORT=7860 \
  -e MT_LOGIN=123456 \
  -e MT_PASSWORD=secret \
  -e MT_SERVER="Broker-Real 01" \
  -e MT_BRIDGE_SECRET=bridge_secret_token \
  mt5-bridge-service
```
