---
title: Adaptive Trading Backend
emoji: 🤖
colorFrom: green
colorTo: blue
sdk: docker
app_port: 8000
pinned: false
---

# Adaptive Trading Bot - Python Backend

FastAPI migration of the adaptive trading backend, based on DTC v1.35 strategy defaults.
Includes a built-in monitoring dashboard at `/`.

## Run locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000/` for the dashboard.

## Key endpoints

- `POST /webhook`
- `GET /trades`
- `GET /trades/stats`
- `POST /trades/{id}/close`
- `GET /params`
- `POST /params`
- `GET /params/history`
- `GET /settings`
- `POST /settings`
- `POST /adapt/run`
- `GET /adapt/log`
- `POST /simulate/batch`
- `DELETE /simulate/reset`
- `GET /bridge/account`
- `GET /bridge/positions`

## Stability controls

- Tiny-step parameter updates (`ADAPTATION_MAX_CHANGE_PCT`)
- Low learning rate (`ADAPTATION_LR`)
- Confidence gate (`ADAPTATION_CONFIDENCE_THRESHOLD`)
- Min sample gate (`ADAPTATION_MIN_CLOSED_TRADES`)

## Peer keepalive (service-to-service)

This service can ping another service every 14 minutes to keep both warm.

- `PEER_HEALTHCHECK_URL` (example: `https://loriloha-mt5-bridge-service.hf.space`)
- `PEER_HEALTHCHECK_INTERVAL_SECONDS` (default `840`)
- `PEER_HEALTHCHECK_TIMEOUT_SECONDS` (default `20`)
- `PEER_HEALTHCHECK_BEARER_TOKEN` (optional, for private HF Space access)
