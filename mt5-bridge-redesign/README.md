# MT5 Bridge Redesign (Standalone)

This is an isolated MT5-on-Wine redesign stack that follows proven patterns:

- Wine terminal runtime (`terminal64.exe`)
- X stack (`Xvfb` + `openbox`)
- operational visibility (`x11vnc` + `noVNC`)
- Wine-side automation bridge (`mt5linux` RPyC server)
- standalone smoke validation with liveness/TCP/RPC gates

It is intentionally independent from existing folders.

## Folder Layout

- `Dockerfile` - container image definition
- `start.sh` - process supervisor entrypoint
- `bootstrap.sh` - preflight and readiness checks
- `supervisord.conf` - process orchestration
- `smoke-test.sh` - standalone health gates
- `scripts/launch-terminal.sh` - terminal startup state
- `scripts/dialog-dismiss.sh` - modal dialog dismiss loop
- `scripts/start-mt5linux.sh` - Wine-side RPyC service launch

## Exposed Ports

- `18812` - mt5linux RPyC bridge
- `5900` - raw VNC
- `6080` - noVNC web UI

## Build

```bash
docker build -t mt5-bridge-redesign ./mt5-bridge-redesign
```

## Run

```bash
docker run --rm -it \
  -p 18812:18812 \
  -p 5900:5900 \
  -p 6080:6080 \
  -e BRIDGE_PORT=18812 \
  -e DISPLAY=:99 \
  mt5-bridge-redesign
```

Open noVNC at `http://localhost:6080/vnc.html`.

## Smoke Test

Run inside the container:

```bash
/opt/mt5-redesign/smoke-test.sh
```

### Gate Semantics

- `GATE 1` terminal process alive
- `GATE 2` external TCP connectivity established
- `GATE 3` bridge RPC check (diagnostic)

## Key Environment Variables

- `DISPLAY` (default `:99`)
- `WINEPREFIX` (default `/opt/wineprefix`)
- `LOGDIR` (default `/var/log/mt5`)
- `BRIDGE_PORT` (default `18812`)
- `VNC_PORT` (default `5900`)
- `NOVNC_PORT` (default `6080`)

## Logs

- `/var/log/mt5/bootstrap.log`
- `/var/log/mt5/terminal.log`
- `/var/log/mt5/dismiss.log`
- `/var/log/mt5/mt5linux.log`
- `/var/log/mt5/wineserver-timeline.log`

## Troubleshooting

- If `GATE 2` fails with zero external TCP:
  - inspect noVNC for blocked dialogs
  - inspect `dismiss.log` heartbeat/actions
  - inspect `terminal.log` and `wineserver-timeline.log`
- If bridge port is down:
  - inspect `mt5linux.log`
  - confirm `bootstrap.ready` exists and `wine_python_exe.path` is valid
