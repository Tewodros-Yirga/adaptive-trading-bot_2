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

## Peer keepalive (service-to-service)

This service can ping another service every 14 minutes to keep both warm.

- `PEER_HEALTHCHECK_URL` (example: `https://your-python-backend.hf.space`)
- `PEER_HEALTHCHECK_INTERVAL_SECONDS` (default `840`)
- `PEER_HEALTHCHECK_TIMEOUT_SECONDS` (default `20`)
- `PEER_HEALTHCHECK_BEARER_TOKEN` (optional, for private HF Space access)
