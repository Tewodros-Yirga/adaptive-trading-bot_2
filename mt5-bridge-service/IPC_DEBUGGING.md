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
- `xdotool` daemon: merge **separate** `search --name LiveUpdate` / `Welcome to` / wizard title queries — `--name "A|B"` does **not** mean OR; it looks for a literal pipe in the title and matches nothing. Use a click band around **y≈400–430** on **1280×720** for the centered "Welcome to LiveUpdate" **Restart/Later** row (older **y≈330** coords target nested-wizard layouts). Send `Escape` / `Return` / `Tab` with `key --window WID` so the correct X11 window receives them
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

### 7. Probe loop stalled by Wine pipe hang (root cause confirmed)

**Previous symptom:** Wrapper log ended at `[mt5-probe] Using prebaked path: ...`; `mt5-ipc-probe.log` stayed empty.

**Root cause confirmed:** `start.sh` line 375 used a pipe:
```bash
timeout 120 wine python.exe -m pip install ... 2>&1 | tail -3 >&2
```
`timeout 120` sends SIGTERM to the `wine` loader, but **wineserver survives** holding its end of the pipe open.
`tail -3 >&2` blocks indefinitely waiting for EOF on stdin.
`set +euo pipefail` suppresses the hang — it looks like the script stopped, but it is actually frozen in `tail`.
The entire probe section never runs.

**Fix applied:**
```bash
# Write pip output to a temp FILE; read it after Wine exits — no hanging pipe.
_PIP_TMP="/tmp/mt5-pip-upgrade-$$"
timeout 120 wine python.exe -m pip install ... > "${_PIP_TMP}" 2>&1 || true
tail -5 "${_PIP_TMP}" >&2 2>/dev/null || true
rm -f "${_PIP_TMP}" 2>/dev/null || true
```

- **`set -x` xtrace** added immediately after pip upgrade (before probe loop) so every executed command appears in the wrapper log.

---

### 8. `MT5_CONTEXT_MODE` default regressed back to `portable`

**Symptom:** Start.sh line 15 still had `portable` as the default, even though Issue #4 documented this was the cause of the wizard-blocking IPC failure.

**Root cause:** The fix was documented but never committed.

**Fix applied:** `start.sh` line 15 changed from `portable` → `default`.

---

### 9. `mt5_adapter.py` — `terminal_exe` undefined variable (latent `NameError`)

**Symptom:** `self._resolve_terminal_exe()` return value was discarded at line 136, then `path=terminal_exe` on line 162 would raise `NameError` if ever reached.

**Root cause:** Refactor left the call without capturing the return.

**Fix applied:** Changed to `terminal_exe = self._resolve_terminal_exe()`.

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

1. **Does `xdotool + openbox` successfully click the LiveUpdate dialogs?** Each probe attempt now logs visible X11 window titles to `mt5-ipc-probe.log` via `[pre-probe N/40] windows=...`. Check this after redeploy.

2. **Is the MetaQuotes demo session still valid?** The session is pre-baked in the base image. If expired, the terminal shows a login dialog. A base image rebuild would fix this.

3. **Does `common.ini` pre-write actually skip the wizard?** The new config write targets both portable and AppData Common paths. If the terminal still shows the wizard (visible in screenshot), the path might not match what MT5 reads.

4. **Call `/debug/screenshot` after redeploy** to see the actual terminal state on screen.

---

## Session 3 (2026-04-22) — Root Cause + Fixes Applied

### 10. `-10005` is NOT "pipe not found" — it's "auth packet not processed"

**Definitively confirmed:**
- `-10003` = IPC pipe itself not found (terminal not running or wrong path)
- `-10005` = IPC pipe found, OS-level connection accepted, **but auth packet sits in queue and is never processed within `timeout` ms**

The MetaTrader5 IPC message pump runs on the terminal's **main UI thread**. If the main thread is blocked (wizard, dialog), it cannot drain the IPC queue. `initialize()` waits for `timeout` ms, then returns `(-10005, 'IPC timeout')`.

### 11. First-run wizard blocks the main thread — confirmed by `mode=creds` every attempt

**Evidence:**
- `mode=creds` on every attempt: bare `initialize(timeout=60000)` returned almost instantly (not after 60s), meaning the pipe was found but got -10005 right away, not a full timeout wait. The creds attempt then ran with only 25s.
- No progress across 21+ attempts: the xdotool daemon was not successfully dismissing the wizard.

**Root cause:** AppData MetaQuotes session is not pre-baked in the base image. On every cold container start, the terminal shows **"Welcome to MetaTrader 5 — Select a company"** first-run wizard. The "Next" button is **disabled until a company is selected from the list**, so Tab+Return alone doesn't advance through it.

### 12. xdotool not dismissing the first-run wizard

**Verified failure mode:** `xdotool search --name "Select a company"` may miss the wizard if the dialog is embedded in the main terminal window (not a separate top-level X11 window). Additionally, plain `Tab+Return` cannot click "Next" when the broker list has no selection — the button is disabled.

**Fixes applied (start.sh)**:
- Extend WIZ_IDS search to also match `"MetaTrader"` and `"Setup"` (shorter substrings, more robust)
- Add click coordinates covering broker list item rows (y≈210–280) — **this is the critical missing step**
- Add double-click on first list item (y=245)
- For WIZ_IDS windows: `xdotool type "${MT_SERVER}"` to filter the broker list to the configured server
- Follow with double Return to select + advance
- Extend loop from 48→120 iterations with constant 5s sleep

### 13. Bare attach burns 60s per attempt — changed to 5s

**Fix:** `timeout=60000` → `timeout=5000` for bare attach. When the wizard is blocking the main thread, bare attach returns -10005 almost immediately (no retry benefit from 60s). Changing to 5s fail-fast means more time is available for creds.

### 14. Creds timeout too short at 25s — changed to 90s

**Fix:** `timeout=25000` → `timeout=90000`. When/if xdotool dismisses the wizard and the terminal processes the IPC command, it must: establish TCP connection to broker → authenticate → receive account data → signal IPC ready. This typically takes 30–60s on a fresh connection. 25s was not enough.

**Outer `timeout 100` → `timeout 120`** to cover bare(5s) + creds(90s) + startup overhead.

### 15. Orphan kill: pkill leaves wineserver-side python.exe alive

**Root cause:** `pkill -f "mt5\\.initialize"` matches the Linux-side Wine loader CLI string. When the outer `timeout 120` kills the loader, `wineserver` keeps the Windows-side `python.exe` alive. This process holds the MT5 IPC pipe reference. The next probe attempt sees the pipe as "busy" → instant -10005.

**Fix:**
```bash
WINEDEBUG="-all" "${WINE_CMD}" taskkill /F /IM python.exe > /dev/null 2>&1 || true
pkill -f "wine.*python.*-c" > /dev/null 2>&1 || true
sleep 3  # was sleep 2
```
`wine taskkill` kills only the Windows-side process without touching `wineserver`, `terminal64.exe`, or the mt5linux RPyC server (which uses `-m mt5linux`, not `-c`).

### 16. Domain blocking race condition — fixed

**Root cause:** The `/etc/hosts` writes happened inside the terminal-launcher subshell, executed just before `wine terminal64.exe`. By that point, wineserver was already running and may have cached DNS for update CDNs.

**Fix:** Domain blocking moved to the very top of `start.sh` (line 18, before Xvfb) and extended with extra CDN domains: `download.mql5.com`, `cdn.mql5.com`, `ec.mql5.com`, `files.mql5.com`, `mql5.com`.

### 17. MT5 config pre-write — new

**Added:** Before terminal launch, `start.sh` now writes `common.ini` with `Login=` and `Server=` to both:
- Portable path: `${WINEPREFIX}/drive_c/Program Files/MetaTrader 5/config/`
- AppData path: `${WINEPREFIX}/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal/Common/config/`

This tells the terminal which broker to connect to, ideally skipping the company-selection wizard and showing a Login form instead. `initialize()` with credentials then authenticates over IPC.

### 18. Window title snapshot added to probe log

**Added:** At the start of each probe attempt, `xdotool search --onlyvisible` + `getwindowname` dumps all visible window titles to `mt5-ipc-probe.log` as:
```
[pre-probe 1/40] windows=MetaTrader 5|Welcome to MetaTrader 5 Terminal|...
```
This makes the next debug session self-diagnosing — we can see exactly what was on screen when each probe fired.

---

## Diagnostic Commands (after redeploy)

```powershell
$base="https://loriloha-mt5-bridge-service.hf.space"
$hfToken="hf_..."
$h=@{"Authorization"="Bearer $hfToken";"X-Bridge-Secret"="1a364030..."}

# 1. Screenshot — what is the terminal showing?
$s = Invoke-RestMethod -Uri "$base/debug/screenshot" -Headers $h
$bytes = [Convert]::FromBase64String($s.image_b64)
[IO.File]::WriteAllBytes("$env:TEMP\mt5.png", $bytes)
Start-Process "$env:TEMP\mt5.png"

# 2. Check probe log (has window titles at each attempt now)
$d = Invoke-RestMethod -Uri "$base/debug/mt5" -Headers $h
$d.logs.'mt5-ipc-probe'
$d.logs.'mt5-launch-wrapper' | Select-Object -Last 80

# 3. IPC status
$d.bootstrap.ipc_status
```

---

## Long-Term Fix

Rebuild the base image with the **latest** terminal version baked in:
- No pending updates → no update dialog → IPC available in <30s from cold start
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
