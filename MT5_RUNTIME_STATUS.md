# MT5 Runtime Status

## Current Issue

The bridge starts and `mt5linux` opens on `127.0.0.1:18812`, but MT5 IPC is still failing in runtime:

- `mt5_ipc.ready` is not created.
- `mt5_ipc.failed=true` with `tcp_gate_timeout elapsed=600s`.
- `/debug/screenshot` shows a black screen (no visible MT5 UI).
- `/debug/pipes` shows no usable MT5 named pipes.

In short: the process launches, but terminal state is not becoming attachable for `mt5.initialize()`.

### Latest Incident Signature

Runtime logs now show a specific deadlock pattern:

- `mt5linux_port=1` (stable for minutes),
- `term_windows=0` continuously,
- gate never writes `mt5_ipc.ready`,
- final state becomes `mt5_ipc.failed=true` (`tcp_gate_timeout`).

This confirms the old gate was over-constrained for headless environments: TCP bridge readiness existed, but the extra visible-X11 requirement blocked progress.

### Follow-up Incident (Post Gate Fix)

After headless gate pass started working, a new lifecycle symptom appeared:

- `mt5_ipc.ready` is written successfully,
- terminal self-restarts to a new PID (expected),
- wrapper logs: `wait: pid <runtime_pid> is not a child of this shell`.

Root cause: terminal PID tracking reused one variable for both child PID (`$!`) and adopted runtime PID (`pgrep` result), so final `wait` could target a non-child process.

## What We Have Done

### 1) Root-cause investigation

- Mapped startup logs to `mt5-bridge-service/start.sh` and adapter flow.
- Confirmed repeated `accepted/welcome/goodbye` on RPyC are mostly probe/retry traffic.
- Identified readiness mismatch and restart/stabilization timing as key risk areas.

### 2) Runtime gate hardening

- Added stronger `mt5_ipc.ready` gate checks in `mt5-bridge-service/start.sh`:
  - grace period after terminal PID restart,
  - sustained mt5linux port-open window,
  - terminal window presence check before ready pass.

### 2b) Headless gate deadlock fix

- Updated `mt5-bridge-service/start.sh` gate policy:
  - added `MT5_REQUIRE_X11_WINDOW` toggle,
  - default is `0` (headless-safe): gate can pass without visible X11 windows if mt5linux TCP conditions are already satisfied,
  - strict mode remains available with `MT5_REQUIRE_X11_WINDOW=1`.
- Added explicit readiness mode telemetry:
  - `mode=headless_tcp_no_x11` when gate passes without visible windows,
  - `mode=x11_confirmed` when windows are visible.
- Enriched timeout diagnostics in `mt5-ipc-probe.log`:
  - `DISPLAY` and mode settings,
  - `xdotool` / `wmctrl` availability,
  - visible window snapshot,
  - terminal/Wine/X process snapshot.

### 2c) Terminal lifecycle wait fix

- Updated `mt5-bridge-service/start.sh` PID model:
  - `TERMINAL_CHILD_PID` tracks the directly launched child process,
  - `TERMINAL_RUNTIME_PID` tracks the active terminal process after self-restart/adoption.
- Replaced fragile final `wait` logic:
  - `mode=wait_child` when child PID is still valid,
  - `mode=poll_runtime_pid` when terminal moved to non-child PID,
  - `mode=no_active_terminal` when neither PID is alive.
- Added lifecycle telemetry and adoption logs so `/debug/mt5` clearly shows why the wrapper used wait-vs-poll mode.

### 3) Portable payload rebuild work

- Verified local `mt5 v5640/terminal64.exe` is still build `5.0.0.5640`.
- Rebuilt `mt5-portable.zip` from local payload.
- Found and fixed a ZIP-format problem:
  - initial ZIP used Windows-style entry separators,
  - Linux extraction treated paths incorrectly.
- Implemented custom POSIX-safe ZIP writer in:
  - `scripts/package-mt5-portable.ps1`
- Re-uploaded fixed asset to:
  - `mt5-portable-latest/mt5-portable.zip`

### 4) Build and deploy operations

- Triggered base image build workflow with updated portable asset.
- Confirmed extraction now finds `terminal64.exe` correctly in Docker build logs.
- Redeployed/restarted HF Space and validated current runtime state through debug endpoints.

## What We Are Planning Next

1. Let current HF restart/build settle fully.
2. Re-check:
   - `/debug/mt5`
   - `/debug/screenshot`
   - `/debug/pipes`
3. If runtime is still black-screen + IPC timeout:
   - keep current payload/build,
   - add screenshot-based bake/runtime readiness criteria (instead of relying only on xdotool/window count),
   - iterate startup stabilization logic with concrete visual readiness signal.
4. If runtime becomes healthy:
   - keep this payload + workflow path as baseline,
   - document final known-good sequence for future rebuilds.

## Runtime Readiness Contract (Current)

- Primary readiness path is TCP-based (`mt5linux` + stabilization checks).
- Visible MT5 X11 windows are optional by default in headless deployments.
- To force UI-backed readiness during interactive debugging, set:
  - `MT5_REQUIRE_X11_WINDOW=1`.

## Verification Checklist

1. `/debug/mt5`:
   - ensure `mt5_ipc.ready` is created,
   - confirm status line contains `ready: tcp_connected ... mode=...`.
2. `/debug/screenshot`:
   - black screenshot is no longer a blocker by itself when mode is `headless_tcp_no_x11`.
3. `/debug/pipes`:
   - capture for diagnostics, but do not require as readiness gate criterion.
4. First adapter `mt5.initialize()` call:
   - verify connection succeeds without waiting for visible UI windows.
5. Startup log lifecycle section:
   - confirm there is no `wait: pid ... is not a child of this shell`,
   - confirm one of `[mt5-lifecycle] mode=wait_child|poll_runtime_pid|no_active_terminal` is emitted.

## Files Touched in This Workstream

- `mt5-bridge-service/start.sh`
- `scripts/init-mt5-portable.ps1`
- `scripts/package-mt5-portable.ps1`
- `mt5-bridge-base/Dockerfile`
- `.github/workflows/build-mt5-base.yml`
- `.github/workflows/build-mt5-base-dispatch.yml`
- `mt5-bridge-service/DEPLOYMENT.md`

