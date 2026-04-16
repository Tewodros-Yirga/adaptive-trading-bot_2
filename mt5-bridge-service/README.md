# MT5 Bridge Service (Render Account B)

This service runs MT5 bridge components in one container:

- Xvfb (virtual display)
- Wine + MT5 terminal
- FastAPI bridge API (`/order`, `/close`, `/account`, `/positions`)

## Required env vars

- `MT_LOGIN`
- `MT_PASSWORD`
- `MT_SERVER`
- `MT_BRIDGE_SECRET`

## Optional env vars

- `MT5_INSTALLER_URL` (download URL for MT5 installer if terminal not pre-baked)
- `MT_TERMINAL_EXE` (terminal path inside Wine prefix)
- `BRIDGE_PORT` (default `5555`)
- `MT_FALLBACK_MODE` (`true` by default, allows mock responses when MT5 binding unavailable)

## Run locally

```bash
docker build -t mt5-bridge-service .
docker run --rm -p 5555:5555 \
  -e MT_LOGIN=123456 \
  -e MT_PASSWORD=secret \
  -e MT_SERVER="Broker-Real 01" \
  -e MT_BRIDGE_SECRET=bridge_secret_token \
  mt5-bridge-service
```
