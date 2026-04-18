# MT5 Bridge Service — IPC Debugging Journal

## The Core Problem

The Python [`MetaTrader5`](https://pypi.org/project/MetaTrader5/) package communicates with the terminal via **Windows Named Pipes IPC**.
When running inside **Wine on Linux** (Hugging Face Spaces), this IPC channel consistently fails with one of two errors:

| Error Code | Meaning |
|---|---|
| `-10003` | Terminal process not found / IPC channel not yet created |
| `-10005` | Terminal found but IPC pipe timed out / unresponsive |

The service starts the terminal (`terminal64.exe` visible in `ps`), yet `mt5.initialize()` never returns `True`.

---

## Environment

| Component | Detail |
|---|---|
| **Host** | Hugging Face Spaces (Docker, Ubuntu 22.04, stateless/ephemeral) |
| **Wine** | `wine-devel` 11.x (64-bit) |
| **Terminal** | `terminal64.exe` v5.0.x (64-bit PE) |
| **Python (Wine-side)** | Python 3.9 AMD64 at `Program Files/Python39/python.exe` |
| **WINEPREFIX** | `/opt/wineprefix` (pre-baked base image `ghcr.io/loriloha/mt5-bridge-base`) |
| **Display** | Xvfb `:99` (1280×720) + Openbox WM |

---

## Identified Root Causes & Fixes

### 1. Update dialog blocks IPC pipe creation

**Symptom:** `dir \\.\pipe\` inside Wine shows **zero pipes** while terminal is "running".

**Verified cause:** The terminal downloads a LiveUpdate on every fresh container start and shows a modal **"Updates have been downloaded — Restart to install"** dialog. The IPC named pipe is **not created** while this dialog is blocking.

**Fixes applied:**
- Domain-blocking in `/etc/hosts` (guessed wrong domains initially; actual LiveUpdate CDN domains not yet confirmed)
- `xdotool` daemon: activate MetaTrader-titled windows first, then a **grid of clicks** (Later-ish coords before Restart) plus `Escape` / `Tab`+`Return` / `Alt+l` fallbacks — LiveUpdate often stacks **inside** the "Select a company to open an account with" wizard, so one fixed `(469, 335)` click can miss the inner dialog entirely
- Long-term: monthly base image rebuild via GitHub Actions ensures the terminal version is current

---

### 2. `/noupdate` is not a valid flag

**Verified:** Extensive search of MetaQuotes documentation and forums confirms `/noupdate` is **not** a valid `terminal64.exe` command-line argument. It has no effect.

**Fix:** Removed from launch args. Domain-blocking + base image freshness are the correct approaches.

---

### 3. `xdotool` clicks not reaching Wine dialogs (no window manager)

**Verified cause:** Xvfb was started without a window manager. Without a WM, `xdotool windowactivate` is a no-op — Wine's message queue never receives focus events, so button clicks and keyboard input are silently ignored.

**Fix:** Installed and started `openbox` (lightweight WM) in the Dockerfile and `start.sh`. This is the standard practice for X11 automation in headless environments.

---

### 4. `/portable` vs `default` mode

**Verified (from MetaQuotes docs):**
- `/portable` → terminal stores all user data in its own installation directory. In a Docker image, this directory has no saved session → shows account setup wizard → blocks IPC.
- Default (no flag) → terminal reads from `%APPDATA%\MetaQuotes\Terminal\` → finds the pre-baked demo session → auto-logs in → IPC should be available.

**Fix:** Changed `MT5_CONTEXT_MODE` default from `portable` to `default` in `start.sh`.

---

### 5. `find | head -1` hanging — python path never resolved

**Cause:** `find /opt/wineprefix/drive_c -maxdepth 5 -name python.exe | head -1` can hang due to `SIGPIPE + set -o pipefail` interaction. `find` receives SIGPIPE when `head` closes the pipe, exits 141, and `pipefail` causes the subshell to exit.

**Fix:** Replaced with direct known-path lookup (no `find`):
```bash
for _py in "${WINEPREFIX}/drive_c/Program Files/Python39/python.exe" ...; do
  [[ -f "$_py" ]] && FOUND_PYTHON="$_py" && break
done
```
A bounded `timeout 5 find ...` is kept as a last-resort fallback.

---

### 6. `set -euo pipefail` silently kills probe loop

**Verified (bash 4.4+ behavior):**
In bash 4.4+, a command substitution in an assignment:
```bash
VAR=$(some_command)
```
**does** trigger `set -e` exit if `some_command` fails — even though it's an assignment. The entire launcher subshell exits silently with no log entry.

**Fix:**
```bash
PROBE_EXIT=0
PROBE_OUT=$(timeout 75 "$WINE_CMD" "$FOUND_PYTHON" -c "$PROBE_SCRIPT" 2>&1) || PROBE_EXIT=$?
```
The `|| PROBE_EXIT=$?` suppresses `set -e` and captures the real exit code.

---

### 7. Probe loop still not running (under investigation)

**Current symptoms:**
- Wrapper log ends at `[mt5-probe] Using prebaked path: ...`
- `mt5-ipc-probe.log` remains empty after 10+ minutes
- No `timeout`/`wine python.exe -c ...` process visible in `ps`
- `ipc_status` stays `pending`

**`set -x` xtrace added** to the probe section — every executed command is now logged to the wrapper log. The next deployment will reveal the exact line that fails.

---

## mt5.initialize() Timeout

**Verified:** `-10005` is the standard IPC timeout error. The `timeout` parameter in `mt5.initialize()` controls how long Python waits for the IPC server.

- Previously: `timeout=15000` (15s) — too short for a cold terminal with an update dialog.  
- **Current:** `timeout=60000` (60s) — safe buffer for cold-start scenarios.  
- Outer shell `timeout 75` wraps the entire Wine process to guarantee it terminates.

---

## Current Startup Sequence

```
Xvfb :99 starts (1280×720)
→ openbox WM starts (enables xdotool focus delivery)
→ bootstrap-mt5.sh: installs terminal if needed (pre-baked → ~0s)
→ terminal64.exe starts (default/AppData mode)
→ LiveUpdate downloads update (~60s on cold start)
→ Update dialog appears
→ xdotool daemon clicks "Restart" (x=469, y=335) every 10s
→ Terminal installs update, restarts itself
→ Terminal auto-connects to MetaQuotes demo session (pre-baked AppData)
→ IPC named pipe created
→ Probe: timeout 75 wine python.exe → mt5.initialize(timeout=60000) → ok=True
→ IPC ready sentinel written → adapter connects
```

---

## Open Questions

1. **Does the probe loop start at all?** The `set -x` trace in the next deployment will answer this definitively.

2. **Does `xdotool + openbox` successfully click "Restart"?** The coordinates `(469, 335)` are derived from screenshot analysis at 1280×720. First confirmed test pending.

3. **Is the MetaQuotes demo session still valid?** The session is pre-baked in the base image. If expired, the terminal shows a login dialog, IPC is unavailable. A base image rebuild would fix this.

4. **Do the correct LiveUpdate domains need to be blocked?** Need to capture actual outbound connections (e.g., via `tcpdump` or `ss`) to identify the real CDN/hostnames used by LiveUpdate.

---

## Long-Term Fix

Rebuild the base image with the **latest** terminal version baked in:
- No pending updates → no dialog → IPC available in <30s from cold start
- Monthly GitHub Actions schedule (`0 3 1 * *`) already in place on `build-mt5-base.yml`

---

## Key Files

| File | Purpose |
|---|---|
| `start.sh` | Entrypoint: Xvfb, openbox, terminal launch, xdotool daemon, IPC probe loop |
| `app/main.py` | FastAPI bridge: `/debug/mt5`, `/debug/screenshot`, `/debug/pipes`, `/debug/processes` |
| `app/mt5_adapter.py` | Wraps `MetaTrader5` IPC; retries connect on every request |
| `Dockerfile` | Installs `openbox`, `xdotool`, `scrot` on top of base image |
| `IPC_DEBUGGING.md` | This file |
| `.github/workflows/build-mt5-base.yml` | Monthly base image rebuild |
