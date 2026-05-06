# MT5 Headless Deployment — Status Report

**Date:** 2026-05-06  
**Goal:** Stable headless MT5 terminal running in Docker/Wine on Hugging Face Spaces, responding to `mt5.initialize()` via mt5linux RPyC bridge.

---

## What We've Fixed (This Session)

### 1. Pre-Packaged Portable ZIP ✅
**Problem:** The MQL5 forge ZIP contains only 3 exe stubs. The terminal couldn't self-initialize its `MQL5/` data directory under Wine (CDN download silently fails). This caused a 600s hang during Docker build.

**Fix:** Initialized the terminal locally on Windows in `/portable` mode (takes 5 seconds), zipped the result (137MB, 598 files), and uploaded to GitHub Releases as `mt5-portable-5640`.

- **Release:** `https://github.com/loriloha/adaptive-trading-bot/releases/download/mt5-portable-5640/mt5-portable.zip`
- **Script:** [init-mt5-portable.ps1](file:///g:/adaptive-trading-bot/scripts/init-mt5-portable.ps1)

### 2. Windows Backslash Path Fix ✅
**Problem:** PowerShell's `Compress-Archive` stores paths with Windows backslashes (`MQL5\Experts\file.ex5`). 7z on Linux extracts these as **flat files** with literal `\` in names. Wine translates `MQL5\Experts` → `MQL5/Experts` which doesn't match.

**Fix:** Added a Python post-extraction step in the Dockerfile that renames all 629 backslash-named entries into proper POSIX directory hierarchy.

### 3. Wait Loop Optimization ✅
**Problem:** The init wait loop used two conditions that both failed:
- `terminal.ini size 233 > 233` → **False** (pre-written, never grows)
- `[ -d MQL5/Experts ]` → **False** (flat backslash names, not real dirs)

**Fix:** Added recursive file count > 50 as primary success condition. Reduced timeout from 600s → 120s. Init now completes in **0 seconds**.

### 4. Docker Build Time ✅
| Before | After |
|--------|-------|
| Step #13: 625s (CDN download timeout) | Step #13: 122s |
| Step #14: 625s (init wait timeout) | Step #14: 24.9s |
| **Total init:** ~20 min | **Total init:** ~2.5 min |

---

## What's Still Broken ❌

### Terminal Self-Restart → Black Screen → IPC Timeout

The MT5 terminal **still self-restarts** at runtime despite:
- Binary locking (`chmod a-w *.exe *.dll`)
- Update domain blocking (`/etc/hosts` → `127.0.0.1`)
- `[LiveUpdate] Enabled=0` in `terminal.ini`
- Wine registry `LiveUpdate=0`

#### Runtime Log Evidence
```
[mt5-terminal] terminal pid=152
[mt5-probe] terminal64.exe (PID 152) has exited          ← CRASHED/EXITED
[mt5-probe] New terminal PID detected: 248 (self-restart) ← RESTARTED
[mt5-probe] TCP gate PASSED — terminal has 0 external TCP connection(s)
[mt5-probe] mt5_ipc.ready written
```

#### Screenshot After Restart
**Completely black screen** — the restarted terminal (PID 248) either:
- Crashed silently
- Is running but not rendering (no X11 window created)
- Is stuck in an invisible dialog

#### Adapter Result
The adapter's `mt5.initialize()` calls (via RPyC) are **hanging for 60-90s** then returning `-10003` or `-10005`. The RPyC connection log shows threads connecting and disconnecting without completing initialization.

---

## Root Cause Analysis

### Why Does the Terminal Self-Restart?

The terminal's self-restart is NOT a LiveUpdate (binary update). It's an **internal data directory initialization restart**. When MT5 detects that its `MQL5/` data directory needs compilation or first-run setup, it:

1. Starts up
2. Scans `MQL5/` directory structure
3. Detects that compiled caches (`.ex5` compilation cache, symbol database, etc.) need regeneration
4. Exits and relaunches itself to complete the setup

This is baked into the terminal binary and **cannot be disabled** via configuration. The binary locking and domain blocking only prevent version updates — they don't prevent the internal restart cycle.

### Why Is the Screen Black After Restart?

The restarted terminal (PID 248) is a **grandchild process** spawned by Wine's internal process manager, not a direct child of the shell. Possible causes:

1. **Wine process tree issue:** The new terminal process may not inherit the DISPLAY variable or X11 connection properly
2. **Rendering failure:** The terminal may have started but OpenGL/rendering initialization failed silently
3. **Invisible blocking dialog:** A UAC/elevation dialog or first-run dialog may be present but invisible to xdotool

### Why Does `wait` Fail?

```
/bridge/start.sh: line 827: wait: pid 248 is not a child of this shell
```

`wait` only works on direct child PIDs. PID 248 was spawned by Wine internally (grandchild), so `wait` fails. However, this is **not fatal** — the `exec uvicorn` on line 830 keeps the container alive.

---

## Architecture Overview

```
Container Entrypoint (start.sh, PID 1)
    │
    ├─ Xvfb :99                        (virtual display)
    ├─ Openbox                          (window manager)
    │
    ├─ Background subshell (lines 234-828):
    │   ├─ wine terminal64.exe /portable /config:C:\mt5-headless.ini
    │   │   └─ Self-restarts → NEW PID (grandchild)
    │   ├─ Dismiss loop (xdotool)       (kills dialogs)
    │   ├─ TCP connectivity gate         (waits for port 18812)
    │   └─ Writes mt5_ipc.ready          ← GATE PASSES
    │
    ├─ mt5linux launcher:
    │   └─ wine python.exe -m mt5linux   (RPyC server on :18812)
    │
    └─ exec uvicorn (FastAPI on :7860)
        └─ MT5Adapter.ensure_connection()
            └─ mt5linux_cls(host=127.0.0.1, port=18812)
                └─ client.initialize(timeout=30000)  ← HANGS/FAILS
```

---

## Key Files

| File | Purpose |
|------|---------|
| [Dockerfile](file:///g:/adaptive-trading-bot/mt5-bridge-base/Dockerfile) | Base image: Wine + Python + MT5 terminal (pre-packaged) |
| [start.sh](file:///g:/adaptive-trading-bot/mt5-bridge-service/start.sh) | Runtime entrypoint (831 lines) |
| [mt5_adapter.py](file:///g:/adaptive-trading-bot/mt5-bridge-service/app/mt5_adapter.py) | Python adapter: RPyC → MT5 IPC |
| [build-mt5-base.yml](file:///g:/adaptive-trading-bot/.github/workflows/build-mt5-base.yml) | GHA workflow for base image |

---

## Concrete Next Steps

### Option A: Eliminate the Self-Restart (Recommended)

The terminal self-restarts because it detects uncached/uncompiled MQL5 data. If we **run the terminal once during the Docker build** (which we already do in step #14) and **let it complete its full restart cycle before baking the image**, the runtime terminal should start cleanly without restarting.

**Current problem with step #14:** The init wait loop exits after 0s (file count > 50) and immediately kills the terminal — it never gets to complete its internal restart + recompilation cycle.

**Fix:**
1. In the Dockerfile step #14, after detecting files > 50, **don't break immediately**
2. Instead, wait for the terminal to self-restart AND stabilize (terminal.ini grows, or wait a fixed 60s)
3. Let the terminal complete its MQL5 compilation cache generation
4. THEN kill and bake the image

This means the runtime terminal starts with a fully-initialized data directory and should NOT self-restart.

### Option B: Handle the Self-Restart at Runtime

If Option A doesn't prevent the runtime restart:
1. After detecting self-restart (PID changed), wait longer (60-90s) before writing `mt5_ipc.ready`
2. Add `DISPLAY=:99` explicitly to the restarted terminal's environment
3. Take a screenshot after the grace period to verify terminal state
4. Only write `mt5_ipc.ready` if the terminal has a visible window

### Option C: Skip Portable Mode

The terminal in **portable mode** (`/portable`) uses the exe directory as its data dir. When it detects the exe directory's MQL5 cache is stale, it restarts. In **default (AppData) mode**, the terminal uses `%APPDATA%\MetaQuotes\Terminal\<hash>\` which was NOT populated by the portable init.

We could try launching WITHOUT `/portable` and see if the AppData path (populated by the Dockerfile's first-run) prevents the restart. This requires removing the portable auto-detection in `start.sh` (lines 316-321).

---

> [!IMPORTANT]
> **The most promising fix is Option A** — let the Dockerfile's step #14 run the terminal through its COMPLETE restart cycle (including MQL5 recompilation), then bake the fully-warm image. The current logic kills the terminal too early (0 seconds) because of the file-count shortcut.









we proceeded with option A:

Run docker/build-push-action@v5
GitHub Actions runtime token ACs
Docker info
Proxy configuration
Buildx version
Builder info
/usr/bin/docker buildx build --build-arg HAS_MT5=true --build-arg WINE_BASE=scottyhardy/docker-wine:latest --build-arg CACHE_BUST=25437632682 --cache-from type=gha --cache-to type=gha,mode=max --iidfile /home/runner/work/_temp/docker-actions-toolkit-BZPZFo/build-iidfile-c8de65f83e.txt --attest type=provenance,mode=min,inline-only=true,builder-id=https://github.com/loriloha/adaptive-trading-bot/actions/runs/25437632682 --tag ghcr.io/loriloha/mt5-bridge-base:candidate-3ac48bdf1d4cc22ac52cd7a8b6dcbedd2884a945 --metadata-file /home/runner/work/_temp/docker-actions-toolkit-BZPZFo/build-metadata-7a38db5d11.json --push ./mt5-bridge-base
#0 building with "builder-11d8adae-52ec-48e8-ab20-d067907ef57e" instance using docker-container driver
#1 [internal] load build definition from Dockerfile
#1 transferring dockerfile: 24.14kB done
#1 DONE 0.0s
#2 [internal] load metadata for docker.io/scottyhardy/docker-wine:latest
#2 ...
#3 [auth] scottyhardy/docker-wine:pull token for registry-1.docker.io
#3 DONE 0.0s
#2 [internal] load metadata for docker.io/scottyhardy/docker-wine:latest
#2 DONE 1.0s
#4 [internal] load .dockerignore
#4 transferring context: 2B done
#4 DONE 0.0s
#5 [internal] load build context
#5 DONE 0.0s
#6 [1/9] FROM docker.io/scottyhardy/docker-wine:latest@sha256:c25b5a2eaa201c7373ef34227c2ba99202cf3a70d96dc50188787577821756f0
#6 resolve docker.io/scottyhardy/docker-wine:latest@sha256:c25b5a2eaa201c7373ef34227c2ba99202cf3a70d96dc50188787577821756f0 done
#6 DONE 0.0s
#7 importing cache manifest from gha:1490887799388048129
#7 DONE 0.5s
#5 [internal] load build context
#5 transferring context: 173.35MB 2.0s done
#5 DONE 2.0s
#8 [2/9] RUN apt-get update -qq &&     apt-get install -y --no-install-recommends         openbox xdotool p7zip-full file winetricks     && rm -rf /var/lib/apt/lists/*
#8 CACHED
#9 [3/9] COPY python-installer.exe /tmp/python-installer.exe
#9 CACHED
#9 [3/9] COPY python-installer.exe /tmp/python-installer.exe
#9 sha256:9f8c8de89d803c3a2515c2bb0f9f61753d8ff94a4f6c2a6fc14d29e9bc2419de 0B / 27.10MB 0.2s
#9 sha256:6cc1444fbd96d73a875ccded102b2d2b355b293978c45b8ffc31402f8a3c46da 0B / 28.97MB 0.2s
#9 sha256:a31ba8cf481d8687ee785f409cd59d7fc697ae2f7a0162f36f1353181f749989 0B / 1.90kB 0.2s
#9 sha256:39228f0b898b8a1bb26f519b4b20194e7b4d52018b6f34587f3c887d6c214b04 0B / 328B 0.2s
#9 sha256:a31ba8cf481d8687ee785f409cd59d7fc697ae2f7a0162f36f1353181f749989 1.90kB / 1.90kB 0.3s
#9 sha256:39228f0b898b8a1bb26f519b4b20194e7b4d52018b6f34587f3c887d6c214b04 328B / 328B 0.5s
#9 sha256:a31ba8cf481d8687ee785f409cd59d7fc697ae2f7a0162f36f1353181f749989 1.90kB / 1.90kB 0.8s done
#9 sha256:39228f0b898b8a1bb26f519b4b20194e7b4d52018b6f34587f3c887d6c214b04 328B / 328B 0.8s done
#9 sha256:9f8c8de89d803c3a2515c2bb0f9f61753d8ff94a4f6c2a6fc14d29e9bc2419de 2.10MB / 27.10MB 1.1s
#9 sha256:c15600cb908d1b2ceab24882d2d0139824fac8f631aca6026edb442731501cd4 0B / 866.62kB 0.2s
#9 sha256:0023809b6c756b091b33e4484479dd9e5b9fa019d513c0834e48832a09464c9b 0B / 139.91MB 0.2s
#9 sha256:6cc1444fbd96d73a875ccded102b2d2b355b293978c45b8ffc31402f8a3c46da 2.10MB / 28.97MB 1.2s
#9 sha256:c15600cb908d1b2ceab24882d2d0139824fac8f631aca6026edb442731501cd4 866.62kB / 866.62kB 0.6s done
#9 sha256:9f8c8de89d803c3a2515c2bb0f9f61753d8ff94a4f6c2a6fc14d29e9bc2419de 4.19MB / 27.10MB 1.4s
#9 sha256:ac640f56e5f7403fa85afce6edef464b47e3acf0dac573bbc00f2487795e3c8a 0B / 848B 0.2s
#9 sha256:9f8c8de89d803c3a2515c2bb0f9f61753d8ff94a4f6c2a6fc14d29e9bc2419de 7.34MB / 27.10MB 1.5s
#9 sha256:6cc1444fbd96d73a875ccded102b2d2b355b293978c45b8ffc31402f8a3c46da 5.24MB / 28.97MB 1.5s
#9 sha256:ac640f56e5f7403fa85afce6edef464b47e3acf0dac573bbc00f2487795e3c8a 848B / 848B 0.3s done
#9 sha256:9f8c8de89d803c3a2515c2bb0f9f61753d8ff94a4f6c2a6fc14d29e9bc2419de 9.44MB / 27.10MB 1.7s
#9 sha256:6cc1444fbd96d73a875ccded102b2d2b355b293978c45b8ffc31402f8a3c46da 7.34MB / 28.97MB 1.7s
#9 sha256:6b7db6221da4f164cc258a828f073968e0aaf73f54d6e4f036a1be5c774f7f9a 0B / 179.13kB 0.2s
#9 sha256:6cc1444fbd96d73a875ccded102b2d2b355b293978c45b8ffc31402f8a3c46da 9.44MB / 28.97MB 1.8s
#9 sha256:9f8c8de89d803c3a2515c2bb0f9f61753d8ff94a4f6c2a6fc14d29e9bc2419de 12.58MB / 27.10MB 2.0s
#9 sha256:6cc1444fbd96d73a875ccded102b2d2b355b293978c45b8ffc31402f8a3c46da 11.53MB / 28.97MB 2.0s
#9 sha256:9f8c8de89d803c3a2515c2bb0f9f61753d8ff94a4f6c2a6fc14d29e9bc2419de 16.78MB / 27.10MB 2.3s
#9 sha256:6cc1444fbd96d73a875ccded102b2d2b355b293978c45b8ffc31402f8a3c46da 13.63MB / 28.97MB 2.1s
#9 sha256:6b7db6221da4f164cc258a828f073968e0aaf73f54d6e4f036a1be5c774f7f9a 179.13kB / 179.13kB 0.5s done
#9 sha256:9f8c8de89d803c3a2515c2bb0f9f61753d8ff94a4f6c2a6fc14d29e9bc2419de 19.92MB / 27.10MB 2.4s
#9 sha256:6cc1444fbd96d73a875ccded102b2d2b355b293978c45b8ffc31402f8a3c46da 15.73MB / 28.97MB 2.3s
#9 sha256:6c12570692ac616e19833498596abe53f24ea0eb8d5b39bdf778755244479c2c 0B / 673.50MB 0.2s
#9 sha256:6cc1444fbd96d73a875ccded102b2d2b355b293978c45b8ffc31402f8a3c46da 17.83MB / 28.97MB 2.4s
#9 sha256:9f8c8de89d803c3a2515c2bb0f9f61753d8ff94a4f6c2a6fc14d29e9bc2419de 23.07MB / 27.10MB 2.7s
#9 sha256:6cc1444fbd96d73a875ccded102b2d2b355b293978c45b8ffc31402f8a3c46da 19.92MB / 28.97MB 2.6s
#9 sha256:0023809b6c756b091b33e4484479dd9e5b9fa019d513c0834e48832a09464c9b 9.44MB / 139.91MB 1.8s
#9 sha256:9f8c8de89d803c3a2515c2bb0f9f61753d8ff94a4f6c2a6fc14d29e9bc2419de 26.21MB / 27.10MB 2.9s
#9 sha256:6cc1444fbd96d73a875ccded102b2d2b355b293978c45b8ffc31402f8a3c46da 22.02MB / 28.97MB 2.7s
#9 sha256:9f8c8de89d803c3a2515c2bb0f9f61753d8ff94a4f6c2a6fc14d29e9bc2419de 27.10MB / 27.10MB 2.9s done
#9 sha256:6cc1444fbd96d73a875ccded102b2d2b355b293978c45b8ffc31402f8a3c46da 26.21MB / 28.97MB 3.0s
#9 sha256:6cc1444fbd96d73a875ccded102b2d2b355b293978c45b8ffc31402f8a3c46da 28.31MB / 28.97MB 3.2s
#9 sha256:3067ff427683ee67dfdeb3daef29ab989f59665b9f3da4cf325514939ecd7957 0B / 33.54MB 0.2s
#9 sha256:6cc1444fbd96d73a875ccded102b2d2b355b293978c45b8ffc31402f8a3c46da 28.97MB / 28.97MB 3.2s done
#9 sha256:0023809b6c756b091b33e4484479dd9e5b9fa019d513c0834e48832a09464c9b 17.83MB / 139.91MB 2.4s
#9 sha256:6337251d80477e5cb2af578d42d9508b7d8b6ded25452aac3304d25c5c76ee16 0B / 507B 0.2s
#9 sha256:6337251d80477e5cb2af578d42d9508b7d8b6ded25452aac3304d25c5c76ee16 507B / 507B 0.3s done
#9 sha256:782e64f7cac889bab6d1d381d8b251f27491fdfc62e781a7af4d4f8d03b5a112 0B / 313B 0.2s
#9 sha256:0023809b6c756b091b33e4484479dd9e5b9fa019d513c0834e48832a09464c9b 25.17MB / 139.91MB 2.9s
#9 sha256:782e64f7cac889bab6d1d381d8b251f27491fdfc62e781a7af4d4f8d03b5a112 313B / 313B 0.3s done
#9 sha256:3067ff427683ee67dfdeb3daef29ab989f59665b9f3da4cf325514939ecd7957 2.10MB / 33.54MB 0.9s
#9 sha256:4567dc2e37f9b32363071b497e3c36b0ad8e79db875ea71878c4558bf5f96673 0B / 77.31kB 0.2s
#9 sha256:4567dc2e37f9b32363071b497e3c36b0ad8e79db875ea71878c4558bf5f96673 77.31kB / 77.31kB 0.2s done
#9 sha256:0023809b6c756b091b33e4484479dd9e5b9fa019d513c0834e48832a09464c9b 34.60MB / 139.91MB 3.5s
#9 sha256:3067ff427683ee67dfdeb3daef29ab989f59665b9f3da4cf325514939ecd7957 4.19MB / 33.54MB 1.2s
#9 sha256:c2186b7603013f0d0508756f5ca75369b2e2b97a71fefdc22ed05af53deea40e 2.10MB / 376.83MB 0.2s
#9 sha256:3067ff427683ee67dfdeb3daef29ab989f59665b9f3da4cf325514939ecd7957 7.34MB / 33.54MB 1.4s
#9 sha256:3067ff427683ee67dfdeb3daef29ab989f59665b9f3da4cf325514939ecd7957 10.49MB / 33.54MB 1.5s
#9 sha256:3067ff427683ee67dfdeb3daef29ab989f59665b9f3da4cf325514939ecd7957 14.68MB / 33.54MB 1.7s
#9 sha256:0023809b6c756b091b33e4484479dd9e5b9fa019d513c0834e48832a09464c9b 42.99MB / 139.91MB 4.1s
#9 sha256:6c12570692ac616e19833498596abe53f24ea0eb8d5b39bdf778755244479c2c 36.70MB / 673.50MB 2.6s
#9 sha256:3067ff427683ee67dfdeb3daef29ab989f59665b9f3da4cf325514939ecd7957 17.83MB / 33.54MB 1.8s
#9 sha256:3067ff427683ee67dfdeb3daef29ab989f59665b9f3da4cf325514939ecd7957 22.02MB / 33.54MB 2.0s
#9 sha256:c2186b7603013f0d0508756f5ca75369b2e2b97a71fefdc22ed05af53deea40e 20.97MB / 376.83MB 0.9s
#9 sha256:3067ff427683ee67dfdeb3daef29ab989f59665b9f3da4cf325514939ecd7957 27.26MB / 33.54MB 2.1s
#9 sha256:0023809b6c756b091b33e4484479dd9e5b9fa019d513c0834e48832a09464c9b 50.33MB / 139.91MB 4.5s
#9 sha256:3067ff427683ee67dfdeb3daef29ab989f59665b9f3da4cf325514939ecd7957 31.46MB / 33.54MB 2.3s
#9 sha256:3067ff427683ee67dfdeb3daef29ab989f59665b9f3da4cf325514939ecd7957 33.54MB / 33.54MB 2.4s done
#9 sha256:c2186b7603013f0d0508756f5ca75369b2e2b97a71fefdc22ed05af53deea40e 41.94MB / 376.83MB 1.4s
#9 sha256:b40150c1c2717d324cdb17278c8efdfa4dfcd2ffe083e976f0bcedf31115f081 0B / 29.73MB 0.2s
#9 sha256:0023809b6c756b091b33e4484479dd9e5b9fa019d513c0834e48832a09464c9b 57.67MB / 139.91MB 5.0s
#9 sha256:6c12570692ac616e19833498596abe53f24ea0eb8d5b39bdf778755244479c2c 78.64MB / 673.50MB 3.6s
#9 sha256:c2186b7603013f0d0508756f5ca75369b2e2b97a71fefdc22ed05af53deea40e 62.91MB / 376.83MB 1.8s
#9 sha256:0023809b6c756b091b33e4484479dd9e5b9fa019d513c0834e48832a09464c9b 66.06MB / 139.91MB 5.6s
#9 sha256:c2186b7603013f0d0508756f5ca75369b2e2b97a71fefdc22ed05af53deea40e 88.08MB / 376.83MB 2.3s
#9 sha256:b40150c1c2717d324cdb17278c8efdfa4dfcd2ffe083e976f0bcedf31115f081 2.10MB / 29.73MB 1.2s
#9 sha256:0023809b6c756b091b33e4484479dd9e5b9fa019d513c0834e48832a09464c9b 73.40MB / 139.91MB 6.0s
#9 sha256:c2186b7603013f0d0508756f5ca75369b2e2b97a71fefdc22ed05af53deea40e 109.05MB / 376.83MB 2.7s
#9 sha256:b40150c1c2717d324cdb17278c8efdfa4dfcd2ffe083e976f0bcedf31115f081 4.19MB / 29.73MB 1.4s
#9 sha256:b40150c1c2717d324cdb17278c8efdfa4dfcd2ffe083e976f0bcedf31115f081 6.29MB / 29.73MB 1.5s
#9 sha256:6c12570692ac616e19833498596abe53f24ea0eb8d5b39bdf778755244479c2c 115.34MB / 673.50MB 4.8s
#9 sha256:b40150c1c2717d324cdb17278c8efdfa4dfcd2ffe083e976f0bcedf31115f081 9.44MB / 29.73MB 1.7s
#9 sha256:0023809b6c756b091b33e4484479dd9e5b9fa019d513c0834e48832a09464c9b 80.74MB / 139.91MB 6.5s
#9 sha256:c2186b7603013f0d0508756f5ca75369b2e2b97a71fefdc22ed05af53deea40e 134.22MB / 376.83MB 3.2s
#9 sha256:b40150c1c2717d324cdb17278c8efdfa4dfcd2ffe083e976f0bcedf31115f081 12.58MB / 29.73MB 1.8s
#9 sha256:b40150c1c2717d324cdb17278c8efdfa4dfcd2ffe083e976f0bcedf31115f081 16.78MB / 29.73MB 2.0s
#9 sha256:b40150c1c2717d324cdb17278c8efdfa4dfcd2ffe083e976f0bcedf31115f081 20.97MB / 29.73MB 2.1s
#9 sha256:b40150c1c2717d324cdb17278c8efdfa4dfcd2ffe083e976f0bcedf31115f081 25.17MB / 29.73MB 2.3s
#9 sha256:c2186b7603013f0d0508756f5ca75369b2e2b97a71fefdc22ed05af53deea40e 159.38MB / 376.83MB 3.8s
#9 sha256:b40150c1c2717d324cdb17278c8efdfa4dfcd2ffe083e976f0bcedf31115f081 29.73MB / 29.73MB 2.5s done
#9 sha256:0023809b6c756b091b33e4484479dd9e5b9fa019d513c0834e48832a09464c9b 92.27MB / 139.91MB 7.2s
#9 extracting sha256:b40150c1c2717d324cdb17278c8efdfa4dfcd2ffe083e976f0bcedf31115f081
#9 sha256:6c12570692ac616e19833498596abe53f24ea0eb8d5b39bdf778755244479c2c 149.95MB / 673.50MB 6.0s
#9 sha256:c2186b7603013f0d0508756f5ca75369b2e2b97a71fefdc22ed05af53deea40e 180.36MB / 376.83MB 4.2s
#9 sha256:0023809b6c756b091b33e4484479dd9e5b9fa019d513c0834e48832a09464c9b 100.66MB / 139.91MB 7.8s
#9 sha256:c2186b7603013f0d0508756f5ca75369b2e2b97a71fefdc22ed05af53deea40e 200.28MB / 376.83MB 4.8s
#9 extracting sha256:b40150c1c2717d324cdb17278c8efdfa4dfcd2ffe083e976f0bcedf31115f081 1.0s done
#9 sha256:0023809b6c756b091b33e4484479dd9e5b9fa019d513c0834e48832a09464c9b 108.00MB / 139.91MB 8.3s
#9 sha256:0023809b6c756b091b33e4484479dd9e5b9fa019d513c0834e48832a09464c9b 115.34MB / 139.91MB 8.7s
#9 sha256:6c12570692ac616e19833498596abe53f24ea0eb8d5b39bdf778755244479c2c 187.70MB / 673.50MB 7.4s
#9 sha256:c2186b7603013f0d0508756f5ca75369b2e2b97a71fefdc22ed05af53deea40e 222.30MB / 376.83MB 5.7s
#9 sha256:0023809b6c756b091b33e4484479dd9e5b9fa019d513c0834e48832a09464c9b 122.68MB / 139.91MB 9.2s
#9 sha256:0023809b6c756b091b33e4484479dd9e5b9fa019d513c0834e48832a09464c9b 132.12MB / 139.91MB 9.8s
#9 sha256:c2186b7603013f0d0508756f5ca75369b2e2b97a71fefdc22ed05af53deea40e 243.27MB / 376.83MB 6.8s
#9 sha256:6c12570692ac616e19833498596abe53f24ea0eb8d5b39bdf778755244479c2c 222.30MB / 673.50MB 8.7s
#9 sha256:0023809b6c756b091b33e4484479dd9e5b9fa019d513c0834e48832a09464c9b 139.91MB / 139.91MB 10.4s
#9 sha256:0023809b6c756b091b33e4484479dd9e5b9fa019d513c0834e48832a09464c9b 139.91MB / 139.91MB 10.6s done
#9 sha256:c2186b7603013f0d0508756f5ca75369b2e2b97a71fefdc22ed05af53deea40e 264.24MB / 376.83MB 7.8s
#9 sha256:6c12570692ac616e19833498596abe53f24ea0eb8d5b39bdf778755244479c2c 260.05MB / 673.50MB 10.5s
#9 sha256:c2186b7603013f0d0508756f5ca75369b2e2b97a71fefdc22ed05af53deea40e 285.21MB / 376.83MB 8.9s
#9 sha256:c2186b7603013f0d0508756f5ca75369b2e2b97a71fefdc22ed05af53deea40e 305.14MB / 376.83MB 9.9s
#9 sha256:6c12570692ac616e19833498596abe53f24ea0eb8d5b39bdf778755244479c2c 296.75MB / 673.50MB 12.3s
#9 sha256:c2186b7603013f0d0508756f5ca75369b2e2b97a71fefdc22ed05af53deea40e 327.16MB / 376.83MB 11.0s
#9 sha256:c2186b7603013f0d0508756f5ca75369b2e2b97a71fefdc22ed05af53deea40e 348.13MB / 376.83MB 11.7s
#9 sha256:6c12570692ac616e19833498596abe53f24ea0eb8d5b39bdf778755244479c2c 333.45MB / 673.50MB 14.1s
#9 sha256:c2186b7603013f0d0508756f5ca75369b2e2b97a71fefdc22ed05af53deea40e 369.10MB / 376.83MB 12.5s
#9 sha256:c2186b7603013f0d0508756f5ca75369b2e2b97a71fefdc22ed05af53deea40e 376.83MB / 376.83MB 13.9s done
#9 extracting sha256:c2186b7603013f0d0508756f5ca75369b2e2b97a71fefdc22ed05af53deea40e
#9 sha256:6c12570692ac616e19833498596abe53f24ea0eb8d5b39bdf778755244479c2c 369.10MB / 673.50MB 15.9s
#9 sha256:6c12570692ac616e19833498596abe53f24ea0eb8d5b39bdf778755244479c2c 405.80MB / 673.50MB 17.7s
#9 sha256:6c12570692ac616e19833498596abe53f24ea0eb8d5b39bdf778755244479c2c 443.55MB / 673.50MB 19.5s
#9 sha256:6c12570692ac616e19833498596abe53f24ea0eb8d5b39bdf778755244479c2c 480.25MB / 673.50MB 21.3s
#9 sha256:6c12570692ac616e19833498596abe53f24ea0eb8d5b39bdf778755244479c2c 515.90MB / 673.50MB 23.1s
#9 sha256:6c12570692ac616e19833498596abe53f24ea0eb8d5b39bdf778755244479c2c 552.60MB / 673.50MB 24.9s
#9 sha256:6c12570692ac616e19833498596abe53f24ea0eb8d5b39bdf778755244479c2c 589.30MB / 673.50MB 26.7s
#9 extracting sha256:c2186b7603013f0d0508756f5ca75369b2e2b97a71fefdc22ed05af53deea40e 11.4s done
#9 extracting sha256:4567dc2e37f9b32363071b497e3c36b0ad8e79db875ea71878c4558bf5f96673 done
#9 extracting sha256:782e64f7cac889bab6d1d381d8b251f27491fdfc62e781a7af4d4f8d03b5a112 done
#9 extracting sha256:6337251d80477e5cb2af578d42d9508b7d8b6ded25452aac3304d25c5c76ee16 done
#9 extracting sha256:3067ff427683ee67dfdeb3daef29ab989f59665b9f3da4cf325514939ecd7957
#9 extracting sha256:3067ff427683ee67dfdeb3daef29ab989f59665b9f3da4cf325514939ecd7957 0.8s done
#9 sha256:6c12570692ac616e19833498596abe53f24ea0eb8d5b39bdf778755244479c2c 624.95MB / 673.50MB 28.5s
#9 sha256:6c12570692ac616e19833498596abe53f24ea0eb8d5b39bdf778755244479c2c 661.65MB / 673.50MB 30.3s
#9 sha256:6c12570692ac616e19833498596abe53f24ea0eb8d5b39bdf778755244479c2c 673.50MB / 673.50MB 32.5s done
#9 extracting sha256:6c12570692ac616e19833498596abe53f24ea0eb8d5b39bdf778755244479c2c
#9 [3/9] COPY python-installer.exe /tmp/python-installer.exe
#9 extracting sha256:a31ba8cf481d8687ee785f409cd59d7fc697ae2f7a0162f36f1353181f749989
#9 extracting sha256:a31ba8cf481d8687ee785f409cd59d7fc697ae2f7a0162f36f1353181f749989 0.2s done
#9 DONE 49.3s
#9 [3/9] COPY python-installer.exe /tmp/python-installer.exe
#9 extracting sha256:9f8c8de89d803c3a2515c2bb0f9f61753d8ff94a4f6c2a6fc14d29e9bc2419de
#9 extracting sha256:9f8c8de89d803c3a2515c2bb0f9f61753d8ff94a4f6c2a6fc14d29e9bc2419de 0.9s done
#9 DONE 50.3s
#9 [3/9] COPY python-installer.exe /tmp/python-installer.exe
#9 extracting sha256:6cc1444fbd96d73a875ccded102b2d2b355b293978c45b8ffc31402f8a3c46da 0.1s done
#9 DONE 50.4s
#10 [4/9] RUN echo "Cache-bust value: 25437632682"
#10 0.052 Cache-bust value: 25437632682
#10 DONE 1.8s
#11 [5/9] COPY mt5setup.exe         /tmp/mt5setup.exe
#11 DONE 1.4s
#12 [6/9] COPY smoke-test-ipc.sh    /bridge/smoke-test-ipc.sh
#12 DONE 0.0s
#13 [7/9] RUN set -eux;     mkdir -p "/tmp/xdg-runtime"; chmod 700 "/tmp/xdg-runtime";     mkdir -p "/opt/wineprefix";     rm -f /tmp/.X99-lock;     Xvfb :99 -screen 0 1280x720x24 &     XVFB_PID=$!;     sleep 3;         wineboot --init 2>/dev/null || true;     timeout 60 wine cmd /c ver || true;     sleep 2;         timeout 600 wine /tmp/python-installer.exe       /quiet InstallAllUsers=1 PrependPath=1 Shortcuts=0 Include_test=0       || true;     sleep 5;         WINE_PY=$(find "/opt/wineprefix/drive_c" -maxdepth 5 -name "python.exe"       2>/dev/null | head -1) || true;     echo "Python found at: ${WINE_PY:-NOT FOUND}";     if [ -z "${WINE_PY}" ]; then       echo "ERROR: python.exe not found after install";       kill "${XVFB_PID}" 2>/dev/null || true;       exit 1;     fi;     echo "${WINE_PY}" > /opt/wine_python_exe.path;         timeout 300 wine "${WINE_PY}" -m pip install --upgrade --no-cache-dir pip || true;     timeout 600 wine "${WINE_PY}" -m pip install --no-cache-dir MetaTrader5==5.0.5640 || true; 
#13 0.050 + mkdir -p /tmp/xdg-runtime
#13 0.055 + chmod 700 /tmp/xdg-runtime
#13 0.056 + mkdir -p /opt/wineprefix
#13 0.059 + rm -f /tmp/.X99-lock
#13 0.060 + + XVFB_PID=10
#13 0.061 + sleep 3
#13 0.061 Xvfb :99 -screen 0 1280x720x24
#13 0.114 The XKEYBOARD keymap compiler (xkbcomp) reports:
#13 0.114 > Warning:          Could not resolve keysym XF86CameraAccessEnable
#13 0.114 > Warning:          Could not resolve keysym XF86CameraAccessDisable
#13 0.114 > Warning:          Could not resolve keysym XF86CameraAccessToggle
#13 0.115 > Warning:          Could not resolve keysym XF86NextElement
#13 0.115 > Warning:          Could not resolve keysym XF86PreviousElement
#13 0.115 > Warning:          Could not resolve keysym XF86AutopilotEngageToggle
#13 0.115 > Warning:          Could not resolve keysym XF86MarkWaypoint
#13 0.115 > Warning:          Could not resolve keysym XF86Sos
#13 0.115 > Warning:          Could not resolve keysym XF86NavChart
#13 0.115 > Warning:          Could not resolve keysym XF86FishingChart
#13 0.115 > Warning:          Could not resolve keysym XF86SingleRangeRadar
#13 0.115 > Warning:          Could not resolve keysym XF86DualRangeRadar
#13 0.115 > Warning:          Could not resolve keysym XF86RadarOverlay
#13 0.115 > Warning:          Could not resolve keysym XF86TraditionalSonar
#13 0.115 > Warning:          Could not resolve keysym XF86ClearvuSonar
#13 0.115 > Warning:          Could not resolve keysym XF86SidevuSonar
#13 0.115 > Warning:          Could not resolve keysym XF86NavInfo
#13 0.118 Errors from xkbcomp are not fatal to the X server
#13 3.063 + wineboot --init
#13 14.08 + timeout 60 wine cmd /c ver
#13 14.23 
#13 14.23 Microsoft Windows 10.0.19045
#13 14.24 + sleep 2
#13 16.24 + timeout 600 wine /tmp/python-installer.exe /quiet InstallAllUsers=1 PrependPath=1 Shortcuts=0 Include_test=0
#13 53.26 + sleep 5
#13 58.26 + find /opt/wineprefix/drive_c -maxdepth 5 -name python.exe
#13 58.26 + head -1
#13 58.27 + WINE_PY=/opt/wineprefix/drive_c/Program Files/Python39/python.exe
#13 58.27 + echo Python found at: /opt/wineprefix/drive_c/Program Files/Python39/python.exe
#13 58.27 Python found at: /opt/wineprefix/drive_c/Program Files/Python39/python.exe
#13 58.27 + [ -z /opt/wineprefix/drive_c/Program Files/Python39/python.exe ]
#13 58.27 + echo /opt/wineprefix/drive_c/Program Files/Python39/python.exe
#13 58.27 + timeout 300 wine /opt/wineprefix/drive_c/Program Files/Python39/python.exe -m pip install --upgrade --no-cache-dir pip
#13 61.27 Requirement already satisfied: pip in c:\program files\python39\lib\site-packages (22.0.4)
#13 61.39 Collecting pip
#13 61.43   Downloading pip-26.0.1-py3-none-any.whl (1.8 MB)
#13 61.50      ---------------------------------------- 1.8/1.8 MB 28.7 MB/s eta 0:00:00
#13 61.55 Installing collected packages: pip
#13 61.55   Attempting uninstall: pip
#13 61.56     Found existing installation: pip 22.0.4
#13 64.40     Uninstalling pip-22.0.4:
#13 64.59       Successfully uninstalled pip-22.0.4
#13 69.03 Successfully installed pip-26.0.1
#13 69.20 + timeout 600 wine /opt/wineprefix/drive_c/Program Files/Python39/python.exe -m pip install --no-cache-dir MetaTrader5==5.0.5640
#13 71.84 Collecting MetaTrader5==5.0.5640
#13 71.95   Downloading metatrader5-5.0.5640-cp39-cp39-win_amd64.whl.metadata (2.5 kB)
#13 72.21 Collecting numpy>=1.7 (from MetaTrader5==5.0.5640)
#13 72.23   Downloading numpy-2.0.2-cp39-cp39-win_amd64.whl.metadata (59 kB)
#13 72.33 Downloading metatrader5-5.0.5640-cp39-cp39-win_amd64.whl (58 kB)
#13 72.34 Downloading numpy-2.0.2-cp39-cp39-win_amd64.whl (15.9 MB)
#13 72.60    ---------------------------------------- 15.9/15.9 MB 66.9 MB/s  0:00:00
#13 72.72 Installing collected packages: numpy, MetaTrader5
#13 78.66 
#13 78.68 Successfully installed MetaTrader5-5.0.5640 numpy-2.0.2
#13 78.81 + timeout 600 wine /opt/wineprefix/drive_c/Program Files/Python39/python.exe -m pip install --upgrade --no-cache-dir mt5linux>=0.1.9
#13 81.47 Collecting mt5linux>=0.1.9
#13 81.51   Downloading mt5linux-1.0.3-py3-none-any.whl.metadata (2.5 kB)
#13 81.52 Requirement already satisfied: numpy in c:\program files\python39\lib\site-packages (from mt5linux>=0.1.9) (2.0.2)
#13 81.54 Collecting plumbum==1.7.0 (from mt5linux>=0.1.9)
#13 81.55   Downloading plumbum-1.7.0-py2.py3-none-any.whl.metadata (8.6 kB)
#13 81.59 Collecting pyparsing<4,>=3.1.0 (from mt5linux>=0.1.9)
#13 81.61   Downloading pyparsing-3.3.2-py3-none-any.whl.metadata (5.8 kB)
#13 81.63 Collecting rpyc==5.2.3 (from mt5linux>=0.1.9)
#13 81.64   Downloading rpyc-5.2.3-py3-none-any.whl.metadata (3.3 kB)
#13 81.66 Collecting pypiwin32 (from plumbum==1.7.0->mt5linux>=0.1.9)
#13 81.68   Downloading pypiwin32-223-py3-none-any.whl.metadata (236 bytes)
#13 81.72 Collecting pywin32>=223 (from pypiwin32->plumbum==1.7.0->mt5linux>=0.1.9)
#13 81.73   Downloading pywin32-311-cp39-cp39-win_amd64.whl.metadata (10 kB)
#13 81.82 Downloading mt5linux-1.0.3-py3-none-any.whl (32 kB)
#13 81.84 Downloading plumbum-1.7.0-py2.py3-none-any.whl (116 kB)
#13 81.86 Downloading rpyc-5.2.3-py3-none-any.whl (71 kB)
#13 81.87 Downloading pyparsing-3.3.2-py3-none-any.whl (122 kB)
#13 81.89 Downloading pypiwin32-223-py3-none-any.whl (1.7 kB)
#13 81.90 Downloading pywin32-311-cp39-cp39-win_amd64.whl (9.6 MB)
#13 82.03    ---------------------------------------- 9.6/9.6 MB 77.7 MB/s  0:00:00
#13 82.18 Installing collected packages: pywin32, pypiwin32, pyparsing, plumbum, rpyc, mt5linux
#13 87.30 
#13 87.36 Successfully installed mt5linux-1.0.3 plumbum-1.7.0 pyparsing-3.3.2 pypiwin32-223 pywin32-311 rpyc-5.2.3
#13 87.48 + timeout 300 wine /opt/wineprefix/drive_c/Program Files/Python39/python.exe -m pip install --no-cache-dir python-dateutil
#13 90.16 Collecting python-dateutil
#13 90.21   Downloading python_dateutil-2.9.0.post0-py2.py3-none-any.whl.metadata (8.4 kB)
#13 90.23 Collecting six>=1.5 (from python-dateutil)
#13 90.24   Downloading six-1.17.0-py2.py3-none-any.whl.metadata (1.7 kB)
#13 90.26 Downloading python_dateutil-2.9.0.post0-py2.py3-none-any.whl (229 kB)
#13 90.29 Downloading six-1.17.0-py2.py3-none-any.whl (11 kB)
#13 90.39 Installing collected packages: six, python-dateutil
#13 90.62 
#13 90.67 Successfully installed python-dateutil-2.9.0.post0 six-1.17.0
#13 90.79 + wine /opt/wineprefix/drive_c/Program Files/Python39/python.exe -c import MetaTrader5 as m; print('MetaTrader5', m.__version__)
#13 91.61 MetaTrader5 5.0.5640
#13 91.63 + wine /opt/wineprefix/drive_c/Program Files/Python39/python.exe -c import mt5linux; print('mt5linux OK')
#13 92.30 mt5linux OK
#13 92.31 Installing vcrun2019 (real MSVC runtime DLLs) via winetricks...
#13 92.31 + echo Installing vcrun2019 (real MSVC runtime DLLs) via winetricks...
#13 92.31 + DISPLAY=:99 WINEDEBUG=-all winetricks -q --optout vcrun2019
#13 93.12 Using winetricks 20240105 - sha256sum: 17da748ce874adb2ee9fed79d2550c0c58e57d5969cc779a8779301350625c55 with wine-11.0 and WINEARCH=win64
#13 94.47 Using native,builtin override for following DLLs: api-ms-win-crt-private-l1-1-0 api-ms-win-crt-conio-l1-1-0 api-ms-win-crt-heap-l1-1-0 api-ms-win-crt-locale-l1-1-0 api-ms-win-crt-math-l1-1-0 api-ms-win-crt-runtime-l1-1-0 api-ms-win-crt-stdio-l1-1-0 api-ms-win-crt-time-l1-1-0 atl140 concrt140 msvcp140 msvcp140_1 msvcp140_2 msvcp140_atomic_wait msvcp140_codecvt_ids vcamp140 vccorlib140 vcomp140 vcruntime140
#13 94.61 Downloading https://aka.ms/vs/16/release/vc_redist.x86.exe to /root/.cache/winetricks/vcrun2019
#13 111.8 Using native,builtin override for following DLLs: vcruntime140_1
#13 111.9 Downloading https://aka.ms/vs/16/release/vc_redist.x64.exe to /root/.cache/winetricks/vcrun2019
#13 114.9 Using native,builtin override for following DLLs: ucrtbase
#13 115.0 Downloading https://web.archive.org/web/20210415064013/https://download.visualstudio.microsoft.com/download/pr/85d47aa9-69ae-4162-8300-e6b7e4bf3cf3/14563755AC24A874241935EF2C22C5FCE973ACB001F99E524145113B2DC638C1/VC_redist.x86.exe to /root/.cache/winetricks/ucrtbase2019
#13 116.3 Downloading https://web.archive.org/web/20210414165612/https://download.visualstudio.microsoft.com/download/pr/85d47aa9-69ae-4162-8300-e6b7e4bf3cf3/52B196BBE9016488C735E7B41805B651261FFA5D7AA86EB6A1D0095BE83687B2/VC_redist.x64.exe to /root/.cache/winetricks/ucrtbase2019
#13 128.0 + echo winetricks vcrun2019 done (exit 0)
#13 128.0 + [ true = true ]
#13 128.0 + TERM_EXE=winetricks vcrun2019 done (exit 0)
#13 128.0 
#13 128.0 + file /tmp/mt5setup.exe
#13 128.0 + MT5_FILE_TYPE=/tmp/mt5setup.exe: Zip archive data, at least v2.0 to extract, compression method=store
#13 128.0 + echo File type of mt5setup.exe: /tmp/mt5setup.exe: Zip archive data, at least v2.0 to extract, compression method=store
#13 128.0 File type of mt5setup.exe: /tmp/mt5setup.exe: Zip archive data, at least v2.0 to extract, compression method=store
#13 128.0 + echo /tmp/mt5setup.exe: Zip archive data, at least v2.0 to extract, compression method=store
#13 128.0 + grep -qiE (Zip archive|zip)
#13 128.0 === Detected ZIP archive — extracting portable MT5 installation ===
#13 128.0 + echo === Detected ZIP archive — extracting portable MT5 installation ===
#13 128.0 + _MT5_PARENT=/opt/wineprefix/drive_c/Program Files
#13 128.0 + _MT5_TARGET=/opt/wineprefix/drive_c/Program Files/MetaTrader 5
#13 128.0 + _TMP_EXTRACT=/tmp/mt5-extract-1
#13 128.0 + mkdir -p /opt/wineprefix/drive_c/Program Files /tmp/mt5-extract-1
#13 128.0 + 7z x /tmp/mt5setup.exe -o/tmp/mt5-extract-1 -y
#13 130.0 === 7z extraction log (last 10 lines) ===
#13 130.0 + echo === 7z extraction log (last 10 lines) ===
#13 130.0 + tail -10 /tmp/7z-extract.log
#13 130.0 Type = zip
#13 130.0 Physical Size = 144058134
#13 130.0 
#13 130.0 Everything is Ok
#13 130.0 
#13 130.0 Archives with Warnings: 1
#13 130.0 Folders: 34
#13 130.0 Files: 598
#13 130.0 Size:       331150619
#13 130.0 Compressed: 144058134
#13 130.0 + echo === Extracted contents ===
#13 130.0 + ls -la /tmp/mt5-extract-1/
#13 130.0 === Extracted contents ===
#13 130.0 total 324844
#13 130.0 drwxr-xr-x 36 root root     49152 May  6 13:20 .
#13 130.0 drwxrwxrwt  1 root root      4096 May  6 13:20 ..
#13 130.0 -rw-r--r--  1 root root       428 May  6  2026 bases\alerts.dat
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 bases\Custom\
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 bases\Custom\history\
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 bases\Custom\ticks\
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 bases\Default\
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 bases\Default\history\
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 bases\Default\mail\
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 bases\Default\news\
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 bases\Default\subscriptions\
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 bases\Default\symbols\
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 bases\Default\ticks\
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 bases\Default\trades\
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 bases\Default\trades\0\
#13 130.0 -rw-r--r--  1 root root         0 May  6  2026 bases\indicators.dat
#13 130.0 -rw-r--r--  1 root root         0 May  6  2026 bases\objects.dat
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 bases\signals\
#13 130.0 -rw-r--r--  1 root root         0 May  6  2026 bases\strategy.dat
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 config\certificates\
#13 130.0 -rw-r--r--  1 root root       590 May  6  2026 config\common.ini
#13 130.0 -rw-r--r--  1 root root    388248 May  6  2026 config\dnsperf.dat
#13 130.0 -rw-r--r--  1 root root         2 May  6  2026 config\hotkeys.ini
#13 130.0 -rw-r--r--  1 root root     28424 May  6  2026 config\servers.dat
#13 130.0 -rw-r--r--  1 root root     15796 May  6  2026 config\terminal.ini
#13 130.0 -rw-r--r--  1 root root      1356 May  6  2026 logs\20260506.log
#13 130.0 -rw-r--r--  1 root root     70542 May  6  2026 logs\metaeditor.log
#13 130.0 -rw-r--r--  1 root root 107965560 May  6  2026 MetaEditor64.exe
#13 130.0 -rw-r--r--  1 root root  52208632 May  6  2026 metatester64.exe
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 MQL5\Experts\
#13 130.0 -rw-r--r--  1 root root    144146 May  6  2026 MQL5\Experts\Advisors\ExpertMACD.ex5
#13 130.0 -rw-r--r--  1 root root      6074 May  6  2026 MQL5\Experts\Advisors\ExpertMACD.mq5
#13 130.0 -rw-r--r--  1 root root    153730 May  6  2026 MQL5\Experts\Advisors\ExpertMAMA.ex5
#13 130.0 -rw-r--r--  1 root root      6427 May  6  2026 MQL5\Experts\Advisors\ExpertMAMA.mq5
#13 130.0 -rw-r--r--  1 root root    152562 May  6  2026 MQL5\Experts\Advisors\ExpertMAPSAR.ex5
#13 130.0 -rw-r--r--  1 root root      6342 May  6  2026 MQL5\Experts\Advisors\ExpertMAPSAR.mq5
#13 130.0 -rw-r--r--  1 root root    156042 May  6  2026 MQL5\Experts\Advisors\ExpertMAPSARSizeOptimized.ex5
#13 130.0 -rw-r--r--  1 root root      6710 May  6  2026 MQL5\Experts\Advisors\ExpertMAPSARSizeOptimized.mq5
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 MQL5\Experts\Examples\
#13 130.0 -rw-r--r--  1 root root     19282 May  6  2026 MQL5\Experts\Examples\ChartInChart\ChartInChart.ex5
#13 130.0 -rw-r--r--  1 root root     30556 May  6  2026 MQL5\Experts\Examples\ChartInChart\ChartInChart.mq5
#13 130.0 -rw-r--r--  1 root root     17723 May  6  2026 MQL5\Experts\Examples\Controls\ControlsDialog.mqh
#13 130.0 -rw-r--r--  1 root root    177160 May  6  2026 MQL5\Experts\Examples\Controls\Controls.ex5
#13 130.0 -rw-r--r--  1 root root      2161 May  6  2026 MQL5\Experts\Examples\Controls\Controls.mq5
#13 130.0 -rw-r--r--  1 root root     68200 May  6  2026 MQL5\Experts\Examples\Correlation Matrix 3D\Correlation Matrix 3D.ex5
#13 130.0 -rw-r--r--  1 root root     77294 May  6  2026 MQL5\Experts\Examples\Correlation Matrix 3D\Correlation Matrix 3D.mq5
#13 130.0 -rw-r--r--  1 root root      8302 May  6  2026 MQL5\Experts\Examples\Correlation Matrix 3D\Correlation Matrix 3D.mqproj
#13 130.0 -rw-r--r--  1 root root     46048 May  6  2026 MQL5\Experts\Examples\MACD\MACD Sample.ex5
#13 130.0 -rw-r--r--  1 root root     18514 May  6  2026 MQL5\Experts\Examples\MACD\MACD Sample.mq5
#13 130.0 -rw-r--r--  1 root root     16008 May  6  2026 MQL5\Experts\Examples\Math 3D\Functions.mqh
#13 130.0 -rw-r--r--  1 root root     40564 May  6  2026 MQL5\Experts\Examples\Math 3D\Math 3D.ex5
#13 130.0 -rw-r--r--  1 root root     16958 May  6  2026 MQL5\Experts\Examples\Math 3D\Math 3D.ico
#13 130.0 -rw-r--r--  1 root root      6986 May  6  2026 MQL5\Experts\Examples\Math 3D\Math 3D.mq5
#13 130.0 -rw-r--r--  1 root root      6042 May  6  2026 MQL5\Experts\Examples\Math 3D\Math 3D.mqproj
#13 130.0 -rw-r--r--  1 root root     13957 May  6  2026 MQL5\Experts\Examples\Math 3D Morpher\Functions.mqh
#13 130.0 -rw-r--r--  1 root root     69438 May  6  2026 MQL5\Experts\Examples\Math 3D Morpher\Math 3D Morpher.ex5
#13 130.0 -rw-r--r--  1 root root     35776 May  6  2026 MQL5\Experts\Examples\Math 3D Morpher\Math 3D Morpher.mq5
#13 130.0 -rw-r--r--  1 root root     16042 May  6  2026 MQL5\Experts\Examples\Math 3D Morpher\Math 3D Morpher.mqproj
#13 130.0 -rw-r--r--  1 root root    196662 May  6  2026 MQL5\Experts\Examples\Math 3D Morpher\Textures\checker.bmp
#13 130.0 -rw-r--r--  1 root root       684 May  6  2026 MQL5\Experts\Examples\Math 3D\Sets\Chomolungma.set
#13 130.0 -rw-r--r--  1 root root       696 May  6  2026 MQL5\Experts\Examples\Math 3D\Sets\ClimberDream.set
#13 130.0 -rw-r--r--  1 root root       684 May  6  2026 MQL5\Experts\Examples\Math 3D\Sets\DoubleScrew.set
#13 130.0 -rw-r--r--  1 root root       684 May  6  2026 MQL5\Experts\Examples\Math 3D\Sets\Granite.set
#13 130.0 -rw-r--r--  1 root root       692 May  6  2026 MQL5\Experts\Examples\Math 3D\Sets\Hedgehog.set
#13 130.0 -rw-r--r--  1 root root       688 May  6  2026 MQL5\Experts\Examples\Math 3D\Sets\Hill.set
#13 130.0 -rw-r--r--  1 root root       688 May  6  2026 MQL5\Experts\Examples\Math 3D\Sets\Josephine.set
#13 130.0 -rw-r--r--  1 root root       684 May  6  2026 MQL5\Experts\Examples\Math 3D\Sets\MultyExtremalScrew.set
#13 130.0 -rw-r--r--  1 root root       684 May  6  2026 MQL5\Experts\Examples\Math 3D\Sets\Screw.set
#13 130.0 -rw-r--r--  1 root root       686 May  6  2026 MQL5\Experts\Examples\Math 3D\Sets\Sink.set
#13 130.0 -rw-r--r--  1 root root       686 May  6  2026 MQL5\Experts\Examples\Math 3D\Sets\Skin.set
#13 130.0 -rw-r--r--  1 root root       686 May  6  2026 MQL5\Experts\Examples\Math 3D\Sets\Trapfall.set
#13 130.0 -rw-r--r--  1 root root     35886 May  6  2026 MQL5\Experts\Examples\Moving Average\Moving Average.ex5
#13 130.0 -rw-r--r--  1 root root      8003 May  6  2026 MQL5\Experts\Examples\Moving Average\Moving Average.mq5
#13 130.0 -rw-r--r--  1 root root     47572 May  6  2026 MQL5\Experts\Free Robots\BlackCrows WhiteSoldiers CCI.ex5
#13 130.0 -rw-r--r--  1 root root     23577 May  6  2026 MQL5\Experts\Free Robots\BlackCrows WhiteSoldiers CCI.mq5
#13 130.0 -rw-r--r--  1 root root     46084 May  6  2026 MQL5\Experts\Free Robots\BlackCrows WhiteSoldiers MFI.ex5
#13 130.0 -rw-r--r--  1 root root     23582 May  6  2026 MQL5\Experts\Free Robots\BlackCrows WhiteSoldiers MFI.mq5
#13 130.0 -rw-r--r--  1 root root     47114 May  6  2026 MQL5\Experts\Free Robots\BlackCrows WhiteSoldiers RSI.ex5
#13 130.0 -rw-r--r--  1 root root     23595 May  6  2026 MQL5\Experts\Free Robots\BlackCrows WhiteSoldiers RSI.mq5
#13 130.0 -rw-r--r--  1 root root     47764 May  6  2026 MQL5\Experts\Free Robots\BlackCrows WhiteSoldiers Stoch.ex5
#13 130.0 -rw-r--r--  1 root root     24062 May  6  2026 MQL5\Experts\Free Robots\BlackCrows WhiteSoldiers Stoch.mq5
#13 130.0 -rw-r--r--  1 root root     47790 May  6  2026 MQL5\Experts\Free Robots\BullishBearish Engulfing CCI.ex5
#13 130.0 -rw-r--r--  1 root root     25070 May  6  2026 MQL5\Experts\Free Robots\BullishBearish Engulfing CCI.mq5
#13 130.0 -rw-r--r--  1 root root     47512 May  6  2026 MQL5\Experts\Free Robots\BullishBearish Engulfing MFI.ex5
#13 130.0 -rw-r--r--  1 root root     25066 May  6  2026 MQL5\Experts\Free Robots\BullishBearish Engulfing MFI.mq5
#13 130.0 -rw-r--r--  1 root root     47912 May  6  2026 MQL5\Experts\Free Robots\BullishBearish Engulfing RSI.ex5
#13 130.0 -rw-r--r--  1 root root     25064 May  6  2026 MQL5\Experts\Free Robots\BullishBearish Engulfing RSI.mq5
#13 130.0 -rw-r--r--  1 root root     47482 May  6  2026 MQL5\Experts\Free Robots\BullishBearish Engulfing Stoch.ex5
#13 130.0 -rw-r--r--  1 root root     25559 May  6  2026 MQL5\Experts\Free Robots\BullishBearish Engulfing Stoch.mq5
#13 130.0 -rw-r--r--  1 root root     47848 May  6  2026 MQL5\Experts\Free Robots\BullishBearish Harami CCI.ex5
#13 130.0 -rw-r--r--  1 root root     25144 May  6  2026 MQL5\Experts\Free Robots\BullishBearish Harami CCI.mq5
#13 130.0 -rw-r--r--  1 root root     47162 May  6  2026 MQL5\Experts\Free Robots\BullishBearish Harami MFI.ex5
#13 130.0 -rw-r--r--  1 root root     25140 May  6  2026 MQL5\Experts\Free Robots\BullishBearish Harami MFI.mq5
#13 130.0 -rw-r--r--  1 root root     48254 May  6  2026 MQL5\Experts\Free Robots\BullishBearish Harami RSI.ex5
#13 130.0 -rw-r--r--  1 root root     25138 May  6  2026 MQL5\Experts\Free Robots\BullishBearish Harami RSI.mq5
#13 130.0 -rw-r--r--  1 root root     48134 May  6  2026 MQL5\Experts\Free Robots\BullishBearish Harami Stoch.ex5
#13 130.0 -rw-r--r--  1 root root     25599 May  6  2026 MQL5\Experts\Free Robots\BullishBearish Harami Stoch.mq5
#13 130.0 -rw-r--r--  1 root root     46676 May  6  2026 MQL5\Experts\Free Robots\BullishBearish MeetingLines CCI.ex5
#13 130.0 -rw-r--r--  1 root root     23595 May  6  2026 MQL5\Experts\Free Robots\BullishBearish MeetingLines CCI.mq5
#13 130.0 -rw-r--r--  1 root root     46216 May  6  2026 MQL5\Experts\Free Robots\BullishBearish MeetingLines MFI.ex5
#13 130.0 -rw-r--r--  1 root root     23591 May  6  2026 MQL5\Experts\Free Robots\BullishBearish MeetingLines MFI.mq5
#13 130.0 -rw-r--r--  1 root root     47076 May  6  2026 MQL5\Experts\Free Robots\BullishBearish MeetingLines RSI.ex5
#13 130.0 -rw-r--r--  1 root root     23595 May  6  2026 MQL5\Experts\Free Robots\BullishBearish MeetingLines RSI.mq5
#13 130.0 -rw-r--r--  1 root root     47744 May  6  2026 MQL5\Experts\Free Robots\BullishBearish MeetingLines Stoch.ex5
#13 130.0 -rw-r--r--  1 root root     24047 May  6  2026 MQL5\Experts\Free Robots\BullishBearish MeetingLines Stoch.mq5
#13 130.0 -rw-r--r--  1 root root     47692 May  6  2026 MQL5\Experts\Free Robots\DarkCloud PiercingLine CCI.ex5
#13 130.0 -rw-r--r--  1 root root     25013 May  6  2026 MQL5\Experts\Free Robots\DarkCloud PiercingLine CCI.mq5
#13 130.0 -rw-r--r--  1 root root     46972 May  6  2026 MQL5\Experts\Free Robots\DarkCloud PiercingLine MFI.ex5
#13 130.0 -rw-r--r--  1 root root     25012 May  6  2026 MQL5\Experts\Free Robots\DarkCloud PiercingLine MFI.mq5
#13 130.0 -rw-r--r--  1 root root     47850 May  6  2026 MQL5\Experts\Free Robots\DarkCloud PiercingLine RSI.ex5
#13 130.0 -rw-r--r--  1 root root     25013 May  6  2026 MQL5\Experts\Free Robots\DarkCloud PiercingLine RSI.mq5
#13 130.0 -rw-r--r--  1 root root     48224 May  6  2026 MQL5\Experts\Free Robots\DarkCloud PiercingLine Stoch.ex5
#13 130.0 -rw-r--r--  1 root root     25492 May  6  2026 MQL5\Experts\Free Robots\DarkCloud PiercingLine Stoch.mq5
#13 130.0 -rw-r--r--  1 root root     47848 May  6  2026 MQL5\Experts\Free Robots\HangingMan Hammer CCI.ex5
#13 130.0 -rw-r--r--  1 root root     24583 May  6  2026 MQL5\Experts\Free Robots\HangingMan Hammer CCI.mq5
#13 130.0 -rw-r--r--  1 root root     46770 May  6  2026 MQL5\Experts\Free Robots\HangingMan Hammer MFI.ex5
#13 130.0 -rw-r--r--  1 root root     24583 May  6  2026 MQL5\Experts\Free Robots\HangingMan Hammer MFI.mq5
#13 130.0 -rw-r--r--  1 root root     47878 May  6  2026 MQL5\Experts\Free Robots\HangingMan Hammer RSI.ex5
#13 130.0 -rw-r--r--  1 root root     24592 May  6  2026 MQL5\Experts\Free Robots\HangingMan Hammer RSI.mq5
#13 130.0 -rw-r--r--  1 root root     47718 May  6  2026 MQL5\Experts\Free Robots\HangingMan Hammer Stoch.ex5
#13 130.0 -rw-r--r--  1 root root     25084 May  6  2026 MQL5\Experts\Free Robots\HangingMan Hammer Stoch.mq5
#13 130.0 -rw-r--r--  1 root root     47762 May  6  2026 MQL5\Experts\Free Robots\MorningEvening StarDoji CCI.ex5
#13 130.0 -rw-r--r--  1 root root     26197 May  6  2026 MQL5\Experts\Free Robots\MorningEvening StarDoji CCI.mq5
#13 130.0 -rw-r--r--  1 root root     46358 May  6  2026 MQL5\Experts\Free Robots\MorningEvening StarDoji MFI.ex5
#13 130.0 -rw-r--r--  1 root root     26170 May  6  2026 MQL5\Experts\Free Robots\MorningEvening StarDoji MFI.mq5
#13 130.0 -rw-r--r--  1 root root     47704 May  6  2026 MQL5\Experts\Free Robots\MorningEvening StarDoji RSI.ex5
#13 130.0 -rw-r--r--  1 root root     26192 May  6  2026 MQL5\Experts\Free Robots\MorningEvening StarDoji RSI.mq5
#13 130.0 -rw-r--r--  1 root root     47722 May  6  2026 MQL5\Experts\Free Robots\MorningEvening StarDoji Stoch.ex5
#13 130.0 -rw-r--r--  1 root root     26665 May  6  2026 MQL5\Experts\Free Robots\MorningEvening StarDoji Stoch.mq5
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 MQL5\Files\
#13 130.0 -rw-r--r--  1 root root      4152 May  6  2026 MQL5\Images\dollar.bmp
#13 130.0 -rw-r--r--  1 root root      4152 May  6  2026 MQL5\Images\euro.bmp
#13 130.0 -rw-r--r--  1 root root     24826 May  6  2026 MQL5\Include\Arrays\ArrayChar.mqh
#13 130.0 -rw-r--r--  1 root root     24841 May  6  2026 MQL5\Include\Arrays\ArrayColor.mqh
#13 130.0 -rw-r--r--  1 root root     25106 May  6  2026 MQL5\Include\Arrays\ArrayDatetime.mqh
#13 130.0 -rw-r--r--  1 root root     25433 May  6  2026 MQL5\Include\Arrays\ArrayDouble.mqh
#13 130.0 -rw-r--r--  1 root root     25358 May  6  2026 MQL5\Include\Arrays\ArrayFloat.mqh
#13 130.0 -rw-r--r--  1 root root     24704 May  6  2026 MQL5\Include\Arrays\ArrayInt.mqh
#13 130.0 -rw-r--r--  1 root root     24776 May  6  2026 MQL5\Include\Arrays\ArrayLong.mqh
#13 130.0 -rw-r--r--  1 root root      6965 May  6  2026 MQL5\Include\Arrays\Array.mqh
#13 130.0 -rw-r--r--  1 root root     25319 May  6  2026 MQL5\Include\Arrays\ArrayObj.mqh
#13 130.0 -rw-r--r--  1 root root     24921 May  6  2026 MQL5\Include\Arrays\ArrayShort.mqh
#13 130.0 -rw-r--r--  1 root root     25106 May  6  2026 MQL5\Include\Arrays\ArrayString.mqh
#13 130.0 -rw-r--r--  1 root root     24915 May  6  2026 MQL5\Include\Arrays\ArrayUChar.mqh
#13 130.0 -rw-r--r--  1 root root     24792 May  6  2026 MQL5\Include\Arrays\ArrayUInt.mqh
#13 130.0 -rw-r--r--  1 root root     24865 May  6  2026 MQL5\Include\Arrays\ArrayULong.mqh
#13 130.0 -rw-r--r--  1 root root     25009 May  6  2026 MQL5\Include\Arrays\ArrayUShort.mqh
#13 130.0 -rw-r--r--  1 root root     21080 May  6  2026 MQL5\Include\Arrays\List.mqh
#13 130.0 -rw-r--r--  1 root root     14241 May  6  2026 MQL5\Include\Arrays\Tree.mqh
#13 130.0 -rw-r--r--  1 root root      6801 May  6  2026 MQL5\Include\Arrays\TreeNode.mqh
#13 130.0 -rw-r--r--  1 root root     34652 May  6  2026 MQL5\Include\Canvas\Canvas3D.mqh
#13 130.0 -rw-r--r--  1 root root    156459 May  6  2026 MQL5\Include\Canvas\Canvas.mqh
#13 130.0 -rw-r--r--  1 root root     36139 May  6  2026 MQL5\Include\Canvas\Charts\ChartCanvas.mqh
#13 130.0 -rw-r--r--  1 root root     11932 May  6  2026 MQL5\Include\Canvas\Charts\HistogramChart.mqh
#13 130.0 -rw-r--r--  1 root root     12605 May  6  2026 MQL5\Include\Canvas\Charts\LineChart.mqh
#13 130.0 -rw-r--r--  1 root root     13713 May  6  2026 MQL5\Include\Canvas\Charts\PieChart.mqh
#13 130.0 -rw-r--r--  1 root root      3208 May  6  2026 MQL5\Include\Canvas\DX\DXBox.mqh
#13 130.0 -rw-r--r--  1 root root      4354 May  6  2026 MQL5\Include\Canvas\DX\DXBuffers.mqh
#13 130.0 -rw-r--r--  1 root root      4048 May  6  2026 MQL5\Include\Canvas\DX\DXData.mqh
#13 130.0 -rw-r--r--  1 root root     13160 May  6  2026 MQL5\Include\Canvas\DX\DXDispatcher.mqh
#13 130.0 -rw-r--r--  1 root root      8568 May  6  2026 MQL5\Include\Canvas\DX\DXHandle.mqh
#13 130.0 -rw-r--r--  1 root root      4968 May  6  2026 MQL5\Include\Canvas\DX\DXInput.mqh
#13 130.0 -rw-r--r--  1 root root    155150 May  6  2026 MQL5\Include\Canvas\DX\DXMath.mqh
#13 130.0 -rw-r--r--  1 root root     15738 May  6  2026 MQL5\Include\Canvas\DX\DXMesh.mqh
#13 130.0 -rw-r--r--  1 root root      2106 May  6  2026 MQL5\Include\Canvas\DX\DXObjectBase.mqh
#13 130.0 -rw-r--r--  1 root root      1744 May  6  2026 MQL5\Include\Canvas\DX\DXObject.mqh
#13 130.0 -rw-r--r--  1 root root     16984 May  6  2026 MQL5\Include\Canvas\DX\DXShader.mqh
#13 130.0 -rw-r--r--  1 root root      6405 May  6  2026 MQL5\Include\Canvas\DX\DXSurface.mqh
#13 130.0 -rw-r--r--  1 root root      6890 May  6  2026 MQL5\Include\Canvas\DX\DXTexture.mqh
#13 130.0 -rw-r--r--  1 root root     36695 May  6  2026 MQL5\Include\Canvas\DX\DXUtils.mqh
#13 130.0 -rw-r--r--  1 root root      3307 May  6  2026 MQL5\Include\Canvas\DX\Shaders\DefaultShaderPixel.hlsl
#13 130.0 -rw-r--r--  1 root root      2805 May  6  2026 MQL5\Include\Canvas\DX\Shaders\DefaultShaderVertex.hlsl
#13 130.0 -rw-r--r--  1 root root     27042 May  6  2026 MQL5\Include\Canvas\FlameCanvas.mqh
#13 130.0 -rw-r--r--  1 root root     41365 May  6  2026 MQL5\Include\ChartObjects\ChartObject.mqh
#13 130.0 -rw-r--r--  1 root root      8273 May  6  2026 MQL5\Include\ChartObjects\ChartObjectPanel.mqh
#13 130.0 -rw-r--r--  1 root root     23807 May  6  2026 MQL5\Include\ChartObjects\ChartObjectsArrows.mqh
#13 130.0 -rw-r--r--  1 root root     21277 May  6  2026 MQL5\Include\ChartObjects\ChartObjectsBmpControls.mqh
#13 130.0 -rw-r--r--  1 root root     11883 May  6  2026 MQL5\Include\ChartObjects\ChartObjectsChannels.mqh
#13 130.0 -rw-r--r--  1 root root      9567 May  6  2026 MQL5\Include\ChartObjects\ChartObjectsElliott.mqh
#13 130.0 -rw-r--r--  1 root root     17506 May  6  2026 MQL5\Include\ChartObjects\ChartObjectsFibo.mqh
#13 130.0 -rw-r--r--  1 root root     17095 May  6  2026 MQL5\Include\ChartObjects\ChartObjectsGann.mqh
#13 130.0 -rw-r--r--  1 root root     15506 May  6  2026 MQL5\Include\ChartObjects\ChartObjectsLines.mqh
#13 130.0 -rw-r--r--  1 root root      7288 May  6  2026 MQL5\Include\ChartObjects\ChartObjectsShapes.mqh
#13 130.0 -rw-r--r--  1 root root     38305 May  6  2026 MQL5\Include\ChartObjects\ChartObjectsTxtControls.mqh
#13 130.0 -rw-r--r--  1 root root     17330 May  6  2026 MQL5\Include\ChartObjects\ChartObjectSubChart.mqh
#13 130.0 -rw-r--r--  1 root root     63640 May  6  2026 MQL5\Include\Charts\Chart.mqh
#13 130.0 -rw-r--r--  1 root root     11453 May  6  2026 MQL5\Include\Controls\BmpButton.mqh
#13 130.0 -rw-r--r--  1 root root      6892 May  6  2026 MQL5\Include\Controls\Button.mqh
#13 130.0 -rw-r--r--  1 root root      7677 May  6  2026 MQL5\Include\Controls\CheckBox.mqh
#13 130.0 -rw-r--r--  1 root root     14144 May  6  2026 MQL5\Include\Controls\CheckGroup.mqh
#13 130.0 -rw-r--r--  1 root root     13397 May  6  2026 MQL5\Include\Controls\ComboBox.mqh
#13 130.0 -rw-r--r--  1 root root     15174 May  6  2026 MQL5\Include\Controls\DateDropList.mqh
#13 130.0 -rw-r--r--  1 root root     10702 May  6  2026 MQL5\Include\Controls\DatePicker.mqh
#13 130.0 -rw-r--r--  1 root root     12589 May  6  2026 MQL5\Include\Controls\Defines.mqh
#13 130.0 -rw-r--r--  1 root root     38884 May  6  2026 MQL5\Include\Controls\Dialog.mqh
#13 130.0 -rw-r--r--  1 root root      8938 May  6  2026 MQL5\Include\Controls\Edit.mqh
#13 130.0 -rw-r--r--  1 root root      4309 May  6  2026 MQL5\Include\Controls\Label.mqh
#13 130.0 -rw-r--r--  1 root root     20099 May  6  2026 MQL5\Include\Controls\ListView.mqh
#13 130.0 -rw-r--r--  1 root root      5871 May  6  2026 MQL5\Include\Controls\Panel.mqh
#13 130.0 -rw-r--r--  1 root root      5602 May  6  2026 MQL5\Include\Controls\Picture.mqh
#13 130.0 -rw-r--r--  1 root root      6989 May  6  2026 MQL5\Include\Controls\RadioButton.mqh
#13 130.0 -rw-r--r--  1 root root     13558 May  6  2026 MQL5\Include\Controls\RadioGroup.mqh
#13 130.0 -rw-r--r--  1 root root     11086 May  6  2026 MQL5\Include\Controls\Rect.mqh
#13 130.0 -rw-r--r--  1 root root       576 May  6  2026 MQL5\Include\Controls\res\CheckBoxOff.bmp
#13 130.0 -rw-r--r--  1 root root       576 May  6  2026 MQL5\Include\Controls\res\CheckBoxOn.bmp
#13 130.0 -rw-r--r--  1 root root      1080 May  6  2026 MQL5\Include\Controls\res\Close.bmp
#13 130.0 -rw-r--r--  1 root root      2104 May  6  2026 MQL5\Include\Controls\res\DateDropOff.bmp
#13 130.0 -rw-r--r--  1 root root      2104 May  6  2026 MQL5\Include\Controls\res\DateDropOn.bmp
#13 130.0 -rw-r--r--  1 root root       824 May  6  2026 MQL5\Include\Controls\res\Down.bmp
#13 130.0 -rw-r--r--  1 root root      1080 May  6  2026 MQL5\Include\Controls\res\DownTransp.bmp
#13 130.0 -rw-r--r--  1 root root      1080 May  6  2026 MQL5\Include\Controls\res\DropOff.bmp
#13 130.0 -rw-r--r--  1 root root      1080 May  6  2026 MQL5\Include\Controls\res\DropOn.bmp
#13 130.0 -rw-r--r--  1 root root       824 May  6  2026 MQL5\Include\Controls\res\Left.bmp
#13 130.0 -rw-r--r--  1 root root      1080 May  6  2026 MQL5\Include\Controls\res\LeftTransp.bmp
#13 130.0 -rw-r--r--  1 root root       632 May  6  2026 MQL5\Include\Controls\res\RadioButtonOff.bmp
#13 130.0 -rw-r--r--  1 root root       632 May  6  2026 MQL5\Include\Controls\res\RadioButtonOn.bmp
#13 130.0 -rw-r--r--  1 root root      1080 May  6  2026 MQL5\Include\Controls\res\Restore.bmp
#13 130.0 -rw-r--r--  1 root root       824 May  6  2026 MQL5\Include\Controls\res\Right.bmp
#13 130.0 -rw-r--r--  1 root root      1080 May  6  2026 MQL5\Include\Controls\res\RightTransp.bmp
#13 130.0 -rw-r--r--  1 root root       568 May  6  2026 MQL5\Include\Controls\res\SpinDec.bmp
#13 130.0 -rw-r--r--  1 root root       568 May  6  2026 MQL5\Include\Controls\res\SpinInc.bmp
#13 130.0 -rw-r--r--  1 root root      1144 May  6  2026 MQL5\Include\Controls\res\ThumbHor.bmp
#13 130.0 -rw-r--r--  1 root root      1112 May  6  2026 MQL5\Include\Controls\res\ThumbVert.bmp
#13 130.0 -rw-r--r--  1 root root      1080 May  6  2026 MQL5\Include\Controls\res\Turn.bmp
#13 130.0 -rw-r--r--  1 root root       824 May  6  2026 MQL5\Include\Controls\res\Up.bmp
#13 130.0 -rw-r--r--  1 root root      1080 May  6  2026 MQL5\Include\Controls\res\UpTransp.bmp
#13 130.0 -rw-r--r--  1 root root     26735 May  6  2026 MQL5\Include\Controls\Scrolls.mqh
#13 130.0 -rw-r--r--  1 root root     10338 May  6  2026 MQL5\Include\Controls\SpinEdit.mqh
#13 130.0 -rw-r--r--  1 root root     12237 May  6  2026 MQL5\Include\Controls\WndClient.mqh
#13 130.0 -rw-r--r--  1 root root     16382 May  6  2026 MQL5\Include\Controls\WndContainer.mqh
#13 130.0 -rw-r--r--  1 root root     30356 May  6  2026 MQL5\Include\Controls\Wnd.mqh
#13 130.0 -rw-r--r--  1 root root     10629 May  6  2026 MQL5\Include\Controls\WndObj.mqh
#13 130.0 -rw-r--r--  1 root root     27603 May  6  2026 MQL5\Include\Expert\ExpertBase.mqh
#13 130.0 -rw-r--r--  1 root root      4963 May  6  2026 MQL5\Include\Expert\ExpertMoney.mqh
#13 130.0 -rw-r--r--  1 root root    122604 May  6  2026 MQL5\Include\Expert\Expert.mqh
#13 130.0 -rw-r--r--  1 root root     20246 May  6  2026 MQL5\Include\Expert\ExpertSignal.mqh
#13 130.0 -rw-r--r--  1 root root      6502 May  6  2026 MQL5\Include\Expert\ExpertTrade.mqh
#13 130.0 -rw-r--r--  1 root root      1738 May  6  2026 MQL5\Include\Expert\ExpertTrailing.mqh
#13 130.0 -rw-r--r--  1 root root      3531 May  6  2026 MQL5\Include\Expert\Money\MoneyFixedLot.mqh
#13 130.0 -rw-r--r--  1 root root      3619 May  6  2026 MQL5\Include\Expert\Money\MoneyFixedMargin.mqh
#13 130.0 -rw-r--r--  1 root root      4454 May  6  2026 MQL5\Include\Expert\Money\MoneyFixedRisk.mqh
#13 130.0 -rw-r--r--  1 root root      3356 May  6  2026 MQL5\Include\Expert\Money\MoneyNone.mqh
#13 130.0 -rw-r--r--  1 root root      6231 May  6  2026 MQL5\Include\Expert\Money\MoneySizeOptimized.mqh
#13 130.0 -rw-r--r--  1 root root      7678 May  6  2026 MQL5\Include\Expert\Signal\SignalAC.mqh
#13 130.0 -rw-r--r--  1 root root     12351 May  6  2026 MQL5\Include\Expert\Signal\SignalAMA.mqh
#13 130.0 -rw-r--r--  1 root root     13681 May  6  2026 MQL5\Include\Expert\Signal\SignalAO.mqh
#13 130.0 -rw-r--r--  1 root root     11971 May  6  2026 MQL5\Include\Expert\Signal\SignalBearsPower.mqh
#13 130.0 -rw-r--r--  1 root root     11979 May  6  2026 MQL5\Include\Expert\Signal\SignalBullsPower.mqh
#13 130.0 -rw-r--r--  1 root root     17541 May  6  2026 MQL5\Include\Expert\Signal\SignalCCI.mqh
#13 130.0 -rw-r--r--  1 root root     11738 May  6  2026 MQL5\Include\Expert\Signal\SignalDEMA.mqh
#13 130.0 -rw-r--r--  1 root root     17303 May  6  2026 MQL5\Include\Expert\Signal\SignalDeMarker.mqh
#13 130.0 -rw-r--r--  1 root root      9652 May  6  2026 MQL5\Include\Expert\Signal\SignalEnvelopes.mqh
#13 130.0 -rw-r--r--  1 root root     11764 May  6  2026 MQL5\Include\Expert\Signal\SignalFrAMA.mqh
#13 130.0 -rw-r--r--  1 root root      4646 May  6  2026 MQL5\Include\Expert\Signal\SignalITF.mqh
#13 130.0 -rw-r--r--  1 root root     19863 May  6  2026 MQL5\Include\Expert\Signal\SignalMACD.mqh
#13 130.0 -rw-r--r--  1 root root     12007 May  6  2026 MQL5\Include\Expert\Signal\SignalMA.mqh
#13 130.0 -rw-r--r--  1 root root     18757 May  6  2026 MQL5\Include\Expert\Signal\SignalRSI.mqh
#13 130.0 -rw-r--r--  1 root root      8156 May  6  2026 MQL5\Include\Expert\Signal\SignalRVI.mqh
#13 130.0 -rw-r--r--  1 root root      7834 May  6  2026 MQL5\Include\Expert\Signal\SignalSAR.mqh
#13 130.0 -rw-r--r--  1 root root     20197 May  6  2026 MQL5\Include\Expert\Signal\SignalStoch.mqh
#13 130.0 -rw-r--r--  1 root root     11732 May  6  2026 MQL5\Include\Expert\Signal\SignalTEMA.mqh
#13 130.0 -rw-r--r--  1 root root     18103 May  6  2026 MQL5\Include\Expert\Signal\SignalTRIX.mqh
#13 130.0 -rw-r--r--  1 root root     16579 May  6  2026 MQL5\Include\Expert\Signal\SignalWPR.mqh
#13 130.0 -rw-r--r--  1 root root      5639 May  6  2026 MQL5\Include\Expert\Trailing\TrailingFixedPips.mqh
#13 130.0 -rw-r--r--  1 root root      6725 May  6  2026 MQL5\Include\Expert\Trailing\TrailingMA.mqh
#13 130.0 -rw-r--r--  1 root root      2139 May  6  2026 MQL5\Include\Expert\Trailing\TrailingNone.mqh
#13 130.0 -rw-r--r--  1 root root      5527 May  6  2026 MQL5\Include\Expert\Trailing\TrailingParabolicSAR.mqh
#13 130.0 -rw-r--r--  1 root root     20944 May  6  2026 MQL5\Include\Files\FileBin.mqh
#13 130.0 -rw-r--r--  1 root root      6995 May  6  2026 MQL5\Include\Files\FileBMP.mqh
#13 130.0 -rw-r--r--  1 root root     12206 May  6  2026 MQL5\Include\Files\File.mqh
#13 130.0 -rw-r--r--  1 root root     12879 May  6  2026 MQL5\Include\Files\FilePipe.mqh
#13 130.0 -rw-r--r--  1 root root      2825 May  6  2026 MQL5\Include\Files\FileTxt.mqh
#13 130.0 -rw-r--r--  1 root root     50384 May  6  2026 MQL5\Include\Generic\ArrayList.mqh
#13 130.0 -rw-r--r--  1 root root     25714 May  6  2026 MQL5\Include\Generic\HashMap.mqh
#13 130.0 -rw-r--r--  1 root root     37086 May  6  2026 MQL5\Include\Generic\HashSet.mqh
#13 130.0 -rw-r--r--  1 root root      1142 May  6  2026 MQL5\Include\Generic\Interfaces\ICollection.mqh
#13 130.0 -rw-r--r--  1 root root      1057 May  6  2026 MQL5\Include\Generic\Interfaces\IComparable.mqh
#13 130.0 -rw-r--r--  1 root root       998 May  6  2026 MQL5\Include\Generic\Interfaces\IComparer.mqh
#13 130.0 -rw-r--r--  1 root root      1012 May  6  2026 MQL5\Include\Generic\Interfaces\IEqualityComparable.mqh
#13 130.0 -rw-r--r--  1 root root      1027 May  6  2026 MQL5\Include\Generic\Interfaces\IEqualityComparer.mqh
#13 130.0 -rw-r--r--  1 root root      1352 May  6  2026 MQL5\Include\Generic\Interfaces\IList.mqh
#13 130.0 -rw-r--r--  1 root root      1391 May  6  2026 MQL5\Include\Generic\Interfaces\IMap.mqh
#13 130.0 -rw-r--r--  1 root root      2031 May  6  2026 MQL5\Include\Generic\Interfaces\ISet.mqh
#13 130.0 -rw-r--r--  1 root root      4695 May  6  2026 MQL5\Include\Generic\Internal\ArrayFunction.mqh
#13 130.0 -rw-r--r--  1 root root      7190 May  6  2026 MQL5\Include\Generic\Internal\CompareFunction.mqh
#13 130.0 -rw-r--r--  1 root root      1253 May  6  2026 MQL5\Include\Generic\Internal\DefaultComparer.mqh
#13 130.0 -rw-r--r--  1 root root      1457 May  6  2026 MQL5\Include\Generic\Internal\DefaultEqualityComparer.mqh
#13 130.0 -rw-r--r--  1 root root      1103 May  6  2026 MQL5\Include\Generic\Internal\EqualFunction.mqh
#13 130.0 -rw-r--r--  1 root root      7249 May  6  2026 MQL5\Include\Generic\Internal\HashFunction.mqh
#13 130.0 -rw-r--r--  1 root root      8348 May  6  2026 MQL5\Include\Generic\Internal\Introsort.mqh
#13 130.0 -rw-r--r--  1 root root      3601 May  6  2026 MQL5\Include\Generic\Internal\PrimeGenerator.mqh
#13 130.0 -rw-r--r--  1 root root     21261 May  6  2026 MQL5\Include\Generic\LinkedList.mqh
#13 130.0 -rw-r--r--  1 root root     29814 May  6  2026 MQL5\Include\Generic\Queue.mqh
#13 130.0 -rw-r--r--  1 root root     74814 May  6  2026 MQL5\Include\Generic\RedBlackTree.mqh
#13 130.0 -rw-r--r--  1 root root     14431 May  6  2026 MQL5\Include\Generic\SortedMap.mqh
#13 130.0 -rw-r--r--  1 root root     25573 May  6  2026 MQL5\Include\Generic\SortedSet.mqh
#13 130.0 -rw-r--r--  1 root root      8953 May  6  2026 MQL5\Include\Generic\Stack.mqh
#13 130.0 -rw-r--r--  1 root root     12581 May  6  2026 MQL5\Include\Graphics\Axis.mqh
#13 130.0 -rw-r--r--  1 root root      3120 May  6  2026 MQL5\Include\Graphics\ColorGenerator.mqh
#13 130.0 -rw-r--r--  1 root root     22801 May  6  2026 MQL5\Include\Graphics\Curve.mqh
#13 130.0 -rw-r--r--  1 root root    173136 May  6  2026 MQL5\Include\Graphics\Graphic.mqh
#13 130.0 -rw-r--r--  1 root root     33009 May  6  2026 MQL5\Include\Indicators\BillWilliams.mqh
#13 130.0 -rw-r--r--  1 root root      8168 May  6  2026 MQL5\Include\Indicators\Custom.mqh
#13 130.0 -rw-r--r--  1 root root     19825 May  6  2026 MQL5\Include\Indicators\Indicator.mqh
#13 130.0 -rw-r--r--  1 root root     12252 May  6  2026 MQL5\Include\Indicators\Indicators.mqh
#13 130.0 -rw-r--r--  1 root root     73859 May  6  2026 MQL5\Include\Indicators\Oscilators.mqh
#13 130.0 -rw-r--r--  1 root root     12629 May  6  2026 MQL5\Include\Indicators\Series.mqh
#13 130.0 -rw-r--r--  1 root root     63360 May  6  2026 MQL5\Include\Indicators\TimeSeries.mqh
#13 130.0 -rw-r--r--  1 root root     73766 May  6  2026 MQL5\Include\Indicators\Trend.mqh
#13 130.0 -rw-r--r--  1 root root     17740 May  6  2026 MQL5\Include\Indicators\Volumes.mqh
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 MQL5\Include\Math\
#13 130.0 -rw-r--r--  1 root root    593648 May  6  2026 MQL5\Include\Math\Alglib\alglibinternal.mqh
#13 130.0 -rw-r--r--  1 root root    122283 May  6  2026 MQL5\Include\Math\Alglib\alglibmisc.mqh
#13 130.0 -rw-r--r--  1 root root   2478270 May  6  2026 MQL5\Include\Math\Alglib\alglib.mqh
#13 130.0 -rw-r--r--  1 root root     92144 May  6  2026 MQL5\Include\Math\Alglib\ap.mqh
#13 130.0 -rw-r--r--  1 root root      4080 May  6  2026 MQL5\Include\Math\Alglib\arrayresize.mqh
#13 130.0 -rw-r--r--  1 root root     13809 May  6  2026 MQL5\Include\Math\Alglib\bitconvert.mqh
#13 130.0 -rw-r--r--  1 root root   1151833 May  6  2026 MQL5\Include\Math\Alglib\dataanalysis.mqh
#13 130.0 -rw-r--r--  1 root root     21765 May  6  2026 MQL5\Include\Math\Alglib\delegatefunctions.mqh
#13 130.0 -rw-r--r--  1 root root     33183 May  6  2026 MQL5\Include\Math\Alglib\diffequations.mqh
#13 130.0 -rw-r--r--  1 root root     94444 May  6  2026 MQL5\Include\Math\Alglib\fasttransforms.mqh
#13 130.0 -rw-r--r--  1 root root    119413 May  6  2026 MQL5\Include\Math\Alglib\integration.mqh
#13 130.0 -rw-r--r--  1 root root   1465208 May  6  2026 MQL5\Include\Math\Alglib\interpolation.mqh
#13 130.0 -rw-r--r--  1 root root   1490029 May  6  2026 MQL5\Include\Math\Alglib\linalg.mqh
#13 130.0 -rw-r--r--  1 root root     46475 May  6  2026 MQL5\Include\Math\Alglib\matrix.mqh
#13 130.0 -rw-r--r--  1 root root   2300652 May  6  2026 MQL5\Include\Math\Alglib\optimization.mqh
#13 130.0 -rw-r--r--  1 root root    302159 May  6  2026 MQL5\Include\Math\Alglib\solvers.mqh
#13 130.0 -rw-r--r--  1 root root    241208 May  6  2026 MQL5\Include\Math\Alglib\specialfunctions.mqh
#13 130.0 -rw-r--r--  1 root root    417177 May  6  2026 MQL5\Include\Math\Alglib\statistics.mqh
#13 130.0 -rw-r--r--  1 root root      9060 May  6  2026 MQL5\Include\Math\Fuzzy\dictionary.mqh
#13 130.0 -rw-r--r--  1 root root     18196 May  6  2026 MQL5\Include\Math\Fuzzy\fuzzyrule.mqh
#13 130.0 -rw-r--r--  1 root root      3819 May  6  2026 MQL5\Include\Math\Fuzzy\fuzzyterm.mqh
#13 130.0 -rw-r--r--  1 root root      5586 May  6  2026 MQL5\Include\Math\Fuzzy\fuzzyvariable.mqh
#13 130.0 -rw-r--r--  1 root root     12170 May  6  2026 MQL5\Include\Math\Fuzzy\genericfuzzysystem.mqh
#13 130.0 -rw-r--r--  1 root root      7352 May  6  2026 MQL5\Include\Math\Fuzzy\helper.mqh
#13 130.0 -rw-r--r--  1 root root      7238 May  6  2026 MQL5\Include\Math\Fuzzy\inferencemethod.mqh
#13 130.0 -rw-r--r--  1 root root     23075 May  6  2026 MQL5\Include\Math\Fuzzy\mamdanifuzzysystem.mqh
#13 130.0 -rw-r--r--  1 root root     44306 May  6  2026 MQL5\Include\Math\Fuzzy\membershipfunction.mqh
#13 130.0 -rw-r--r--  1 root root     37389 May  6  2026 MQL5\Include\Math\Fuzzy\ruleparser.mqh
#13 130.0 -rw-r--r--  1 root root     13323 May  6  2026 MQL5\Include\Math\Fuzzy\sugenofuzzysystem.mqh
#13 130.0 -rw-r--r--  1 root root     11248 May  6  2026 MQL5\Include\Math\Fuzzy\sugenovariable.mqh
#13 130.0 -rw-r--r--  1 root root     33321 May  6  2026 MQL5\Include\Math\Stat\Beta.mqh
#13 130.0 -rw-r--r--  1 root root     35728 May  6  2026 MQL5\Include\Math\Stat\Binomial.mqh
#13 130.0 -rw-r--r--  1 root root     25520 May  6  2026 MQL5\Include\Math\Stat\Cauchy.mqh
#13 130.0 -rw-r--r--  1 root root     24891 May  6  2026 MQL5\Include\Math\Stat\ChiSquare.mqh
#13 130.0 -rw-r--r--  1 root root     24774 May  6  2026 MQL5\Include\Math\Stat\Exponential.mqh
#13 130.0 -rw-r--r--  1 root root     27612 May  6  2026 MQL5\Include\Math\Stat\F.mqh
#13 130.0 -rw-r--r--  1 root root     32373 May  6  2026 MQL5\Include\Math\Stat\Gamma.mqh
#13 130.0 -rw-r--r--  1 root root     25490 May  6  2026 MQL5\Include\Math\Stat\Geometric.mqh
#13 130.0 -rw-r--r--  1 root root     35334 May  6  2026 MQL5\Include\Math\Stat\Hypergeometric.mqh
#13 130.0 -rw-r--r--  1 root root     27860 May  6  2026 MQL5\Include\Math\Stat\Logistic.mqh
#13 130.0 -rw-r--r--  1 root root     29245 May  6  2026 MQL5\Include\Math\Stat\Lognormal.mqh
#13 130.0 -rw-r--r--  1 root root    434790 May  6  2026 MQL5\Include\Math\Stat\Math.mqh
#13 130.0 -rw-r--r--  1 root root     29634 May  6  2026 MQL5\Include\Math\Stat\NegativeBinomial.mqh
#13 130.0 -rw-r--r--  1 root root     41195 May  6  2026 MQL5\Include\Math\Stat\NoncentralBeta.mqh
#13 130.0 -rw-r--r--  1 root root     37497 May  6  2026 MQL5\Include\Math\Stat\NoncentralChiSquare.mqh
#13 130.0 -rw-r--r--  1 root root     35826 May  6  2026 MQL5\Include\Math\Stat\NoncentralF.mqh
#13 130.0 -rw-r--r--  1 root root     47734 May  6  2026 MQL5\Include\Math\Stat\NoncentralT.mqh
#13 130.0 -rw-r--r--  1 root root     40671 May  6  2026 MQL5\Include\Math\Stat\Normal.mqh
#13 130.0 -rw-r--r--  1 root root     32547 May  6  2026 MQL5\Include\Math\Stat\Poisson.mqh
#13 130.0 -rw-r--r--  1 root root      1139 May  6  2026 MQL5\Include\Math\Stat\Stat.mqh
#13 130.0 -rw-r--r--  1 root root     28548 May  6  2026 MQL5\Include\Math\Stat\T.mqh
#13 130.0 -rw-r--r--  1 root root     25659 May  6  2026 MQL5\Include\Math\Stat\Uniform.mqh
#13 130.0 -rw-r--r--  1 root root     27532 May  6  2026 MQL5\Include\Math\Stat\Weibull.mqh
#13 130.0 -rw-r--r--  1 root root     10737 May  6  2026 MQL5\Include\MovingAverages.mqh
#13 130.0 -rw-r--r--  1 root root      2036 May  6  2026 MQL5\Include\Object.mqh
#13 130.0 -rw-r--r--  1 root root     28662 May  6  2026 MQL5\Include\OpenCL\OpenCL.mqh
#13 130.0 -rw-r--r--  1 root root       683 May  6  2026 MQL5\Include\StdLibErr.mqh
#13 130.0 -rw-r--r--  1 root root     13847 May  6  2026 MQL5\Include\Strings\String.mqh
#13 130.0 -rw-r--r--  1 root root     17883 May  6  2026 MQL5\Include\Tools\DateTime.mqh
#13 130.0 -rw-r--r--  1 root root     18130 May  6  2026 MQL5\Include\Trade\AccountInfo.mqh
#13 130.0 -rw-r--r--  1 root root     16313 May  6  2026 MQL5\Include\Trade\DealInfo.mqh
#13 130.0 -rw-r--r--  1 root root     20018 May  6  2026 MQL5\Include\Trade\HistoryOrderInfo.mqh
#13 130.0 -rw-r--r--  1 root root     22087 May  6  2026 MQL5\Include\Trade\OrderInfo.mqh
#13 130.0 -rw-r--r--  1 root root     15820 May  6  2026 MQL5\Include\Trade\PositionInfo.mqh
#13 130.0 -rw-r--r--  1 root root     35851 May  6  2026 MQL5\Include\Trade\SymbolInfo.mqh
#13 130.0 -rw-r--r--  1 root root     10730 May  6  2026 MQL5\Include\Trade\TerminalInfo.mqh
#13 130.0 -rw-r--r--  1 root root     69541 May  6  2026 MQL5\Include\Trade\Trade.mqh
#13 130.0 -rw-r--r--  1 root root      5755 May  6  2026 MQL5\Include\VirtualKeys.mqh
#13 130.0 -rw-r--r--  1 root root      1933 May  6  2026 MQL5\Include\WinAPI\errhandlingapi.mqh
#13 130.0 -rw-r--r--  1 root root      9487 May  6  2026 MQL5\Include\WinAPI\fileapi.mqh
#13 130.0 -rw-r--r--  1 root root      1165 May  6  2026 MQL5\Include\WinAPI\handleapi.mqh
#13 130.0 -rw-r--r--  1 root root      2952 May  6  2026 MQL5\Include\WinAPI\libloaderapi.mqh
#13 130.0 -rw-r--r--  1 root root      5838 May  6  2026 MQL5\Include\WinAPI\memoryapi.mqh
#13 130.0 -rw-r--r--  1 root root      1589 May  6  2026 MQL5\Include\WinAPI\processenv.mqh
#13 130.0 -rw-r--r--  1 root root     10250 May  6  2026 MQL5\Include\WinAPI\processthreadsapi.mqh
#13 130.0 -rw-r--r--  1 root root     16588 May  6  2026 MQL5\Include\WinAPI\securitybaseapi.mqh
#13 130.0 -rw-r--r--  1 root root      4827 May  6  2026 MQL5\Include\WinAPI\sysinfoapi.mqh
#13 130.0 -rw-r--r--  1 root root       827 May  6  2026 MQL5\Include\WinAPI\winapi.mqh
#13 130.0 -rw-r--r--  1 root root     44329 May  6  2026 MQL5\Include\WinAPI\winbase.mqh
#13 130.0 -rw-r--r--  1 root root      8839 May  6  2026 MQL5\Include\WinAPI\windef.mqh
#13 130.0 -rw-r--r--  1 root root     64995 May  6  2026 MQL5\Include\WinAPI\wingdi.mqh
#13 130.0 -rw-r--r--  1 root root     97680 May  6  2026 MQL5\Include\WinAPI\winnt.mqh
#13 130.0 -rw-r--r--  1 root root      6133 May  6  2026 MQL5\Include\WinAPI\winreg.mqh
#13 130.0 -rw-r--r--  1 root root     82945 May  6  2026 MQL5\Include\WinAPI\winuser.mqh
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 MQL5\Indicators\
#13 130.0 -rw-r--r--  1 root root      8872 May  6  2026 MQL5\Indicators\Examples\Accelerator.ex5
#13 130.0 -rw-r--r--  1 root root      5306 May  6  2026 MQL5\Indicators\Examples\Accelerator.mq5
#13 130.0 -rw-r--r--  1 root root      8298 May  6  2026 MQL5\Indicators\Examples\AD.ex5
#13 130.0 -rw-r--r--  1 root root      3375 May  6  2026 MQL5\Indicators\Examples\AD.mq5
#13 130.0 -rw-r--r--  1 root root     13284 May  6  2026 MQL5\Indicators\Examples\ADX.ex5
#13 130.0 -rw-r--r--  1 root root      5609 May  6  2026 MQL5\Indicators\Examples\ADX.mq5
#13 130.0 -rw-r--r--  1 root root     14140 May  6  2026 MQL5\Indicators\Examples\ADXW.ex5
#13 130.0 -rw-r--r--  1 root root      6836 May  6  2026 MQL5\Indicators\Examples\ADXW.mq5
#13 130.0 -rw-r--r--  1 root root     16098 May  6  2026 MQL5\Indicators\Examples\Alligator.ex5
#13 130.0 -rw-r--r--  1 root root      6064 May  6  2026 MQL5\Indicators\Examples\Alligator.mq5
#13 130.0 -rw-r--r--  1 root root     10452 May  6  2026 MQL5\Indicators\Examples\AMA.ex5
#13 130.0 -rw-r--r--  1 root root      5035 May  6  2026 MQL5\Indicators\Examples\AMA.mq5
#13 130.0 -rw-r--r--  1 root root      9154 May  6  2026 MQL5\Indicators\Examples\ASI.ex5
#13 130.0 -rw-r--r--  1 root root      4129 May  6  2026 MQL5\Indicators\Examples\ASI.mq5
#13 130.0 -rw-r--r--  1 root root      9596 May  6  2026 MQL5\Indicators\Examples\ATR.ex5
#13 130.0 -rw-r--r--  1 root root      3880 May  6  2026 MQL5\Indicators\Examples\ATR.mq5
#13 130.0 -rw-r--r--  1 root root      7478 May  6  2026 MQL5\Indicators\Examples\Awesome_Oscillator.ex5
#13 130.0 -rw-r--r--  1 root root      4591 May  6  2026 MQL5\Indicators\Examples\Awesome_Oscillator.mq5
#13 130.0 -rw-r--r--  1 root root     14192 May  6  2026 MQL5\Indicators\Examples\BB.ex5
#13 130.0 -rw-r--r--  1 root root      5817 May  6  2026 MQL5\Indicators\Examples\BB.mq5
#13 130.0 -rw-r--r--  1 root root      9492 May  6  2026 MQL5\Indicators\Examples\Bears.ex5
#13 130.0 -rw-r--r--  1 root root      3688 May  6  2026 MQL5\Indicators\Examples\Bears.mq5
#13 130.0 -rw-r--r--  1 root root      9040 May  6  2026 MQL5\Indicators\Examples\Bulls.ex5
#13 130.0 -rw-r--r--  1 root root      3693 May  6  2026 MQL5\Indicators\Examples\Bulls.mq5
#13 130.0 -rw-r--r--  1 root root      9004 May  6  2026 MQL5\Indicators\Examples\BW-ZoneTrade.ex5
#13 130.0 -rw-r--r--  1 root root      5020 May  6  2026 MQL5\Indicators\Examples\BW-ZoneTrade.mq5
#13 130.0 -rw-r--r--  1 root root     27112 May  6  2026 MQL5\Indicators\Examples\Canvas\FlameChart.ex5
#13 130.0 -rw-r--r--  1 root root      3577 May  6  2026 MQL5\Indicators\Examples\Canvas\FlameChart.mq5
#13 130.0 -rw-r--r--  1 root root     10726 May  6  2026 MQL5\Indicators\Examples\CCI.ex5
#13 130.0 -rw-r--r--  1 root root      3641 May  6  2026 MQL5\Indicators\Examples\CCI.mq5
#13 130.0 -rw-r--r--  1 root root     17492 May  6  2026 MQL5\Indicators\Examples\CHO.ex5
#13 130.0 -rw-r--r--  1 root root      5356 May  6  2026 MQL5\Indicators\Examples\CHO.mq5
#13 130.0 -rw-r--r--  1 root root     16576 May  6  2026 MQL5\Indicators\Examples\CHV.ex5
#13 130.0 -rw-r--r--  1 root root      4440 May  6  2026 MQL5\Indicators\Examples\CHV.mq5
#13 130.0 -rw-r--r--  1 root root      7302 May  6  2026 MQL5\Indicators\Examples\ColorBars.ex5
#13 130.0 -rw-r--r--  1 root root      3059 May  6  2026 MQL5\Indicators\Examples\ColorBars.mq5
#13 130.0 -rw-r--r--  1 root root      7294 May  6  2026 MQL5\Indicators\Examples\ColorCandlesDaily.ex5
#13 130.0 -rw-r--r--  1 root root      3136 May  6  2026 MQL5\Indicators\Examples\ColorCandlesDaily.mq5
#13 130.0 -rw-r--r--  1 root root      7852 May  6  2026 MQL5\Indicators\Examples\ColorLine.ex5
#13 130.0 -rw-r--r--  1 root root      5175 May  6  2026 MQL5\Indicators\Examples\ColorLine.mq5
#13 130.0 -rw-r--r--  1 root root     15026 May  6  2026 MQL5\Indicators\Examples\Custom Moving Average.ex5
#13 130.0 -rw-r--r--  1 root root      7326 May  6  2026 MQL5\Indicators\Examples\Custom Moving Average.mq5
#13 130.0 -rw-r--r--  1 root root     10462 May  6  2026 MQL5\Indicators\Examples\DEMA.ex5
#13 130.0 -rw-r--r--  1 root root      2940 May  6  2026 MQL5\Indicators\Examples\DEMA.mq5
#13 130.0 -rw-r--r--  1 root root     10474 May  6  2026 MQL5\Indicators\Examples\DeMarker.ex5
#13 130.0 -rw-r--r--  1 root root      4177 May  6  2026 MQL5\Indicators\Examples\DeMarker.mq5
#13 130.0 -rw-r--r--  1 root root      9978 May  6  2026 MQL5\Indicators\Examples\DPO.ex5
#13 130.0 -rw-r--r--  1 root root      2876 May  6  2026 MQL5\Indicators\Examples\DPO.mq5
#13 130.0 -rw-r--r--  1 root root     13584 May  6  2026 MQL5\Indicators\Examples\Envelopes.ex5
#13 130.0 -rw-r--r--  1 root root      4337 May  6  2026 MQL5\Indicators\Examples\Envelopes.mq5
#13 130.0 -rw-r--r--  1 root root     12546 May  6  2026 MQL5\Indicators\Examples\Force_Index.ex5
#13 130.0 -rw-r--r--  1 root root      3927 May  6  2026 MQL5\Indicators\Examples\Force_Index.mq5
#13 130.0 -rw-r--r--  1 root root      8088 May  6  2026 MQL5\Indicators\Examples\Fractals.ex5
#13 130.0 -rw-r--r--  1 root root      3424 May  6  2026 MQL5\Indicators\Examples\Fractals.mq5
#13 130.0 -rw-r--r--  1 root root      9280 May  6  2026 MQL5\Indicators\Examples\FrAMA.ex5
#13 130.0 -rw-r--r--  1 root root      3675 May  6  2026 MQL5\Indicators\Examples\FrAMA.mq5
#13 130.0 -rw-r--r--  1 root root     15944 May  6  2026 MQL5\Indicators\Examples\Gator_2.ex5
#13 130.0 -rw-r--r--  1 root root      9877 May  6  2026 MQL5\Indicators\Examples\Gator_2.mq5
#13 130.0 -rw-r--r--  1 root root     16188 May  6  2026 MQL5\Indicators\Examples\Gator.ex5
#13 130.0 -rw-r--r--  1 root root     10247 May  6  2026 MQL5\Indicators\Examples\Gator.mq5
#13 130.0 -rw-r--r--  1 root root      7828 May  6  2026 MQL5\Indicators\Examples\Heiken_Ashi.ex5
#13 130.0 -rw-r--r--  1 root root      3733 May  6  2026 MQL5\Indicators\Examples\Heiken_Ashi.mq5
#13 130.0 -rw-r--r--  1 root root     13202 May  6  2026 MQL5\Indicators\Examples\Ichimoku.ex5
#13 130.0 -rw-r--r--  1 root root      5204 May  6  2026 MQL5\Indicators\Examples\Ichimoku.mq5
#13 130.0 -rw-r--r--  1 root root     13860 May  6  2026 MQL5\Indicators\Examples\MACD.ex5
#13 130.0 -rw-r--r--  1 root root      4901 May  6  2026 MQL5\Indicators\Examples\MACD.mq5
#13 130.0 -rw-r--r--  1 root root      8586 May  6  2026 MQL5\Indicators\Examples\MarketFacilitationIndex.ex5
#13 130.0 -rw-r--r--  1 root root      5003 May  6  2026 MQL5\Indicators\Examples\MarketFacilitationIndex.mq5
#13 130.0 -rw-r--r--  1 root root     11628 May  6  2026 MQL5\Indicators\Examples\MFI.ex5
#13 130.0 -rw-r--r--  1 root root      4823 May  6  2026 MQL5\Indicators\Examples\MFI.mq5
#13 130.0 -rw-r--r--  1 root root     14832 May  6  2026 MQL5\Indicators\Examples\MI.ex5
#13 130.0 -rw-r--r--  1 root root      4876 May  6  2026 MQL5\Indicators\Examples\MI.mq5
#13 130.0 -rw-r--r--  1 root root      9560 May  6  2026 MQL5\Indicators\Examples\Momentum.ex5
#13 130.0 -rw-r--r--  1 root root      2870 May  6  2026 MQL5\Indicators\Examples\Momentum.mq5
#13 130.0 -rw-r--r--  1 root root      7882 May  6  2026 MQL5\Indicators\Examples\OBV.ex5
#13 130.0 -rw-r--r--  1 root root      3410 May  6  2026 MQL5\Indicators\Examples\OBV.mq5
#13 130.0 -rw-r--r--  1 root root     13804 May  6  2026 MQL5\Indicators\Examples\OsMA.ex5
#13 130.0 -rw-r--r--  1 root root      5177 May  6  2026 MQL5\Indicators\Examples\OsMA.mq5
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 MQL5\Indicators\Examples\Panels\
#13 130.0 -rw-r--r--  1 root root    136114 May  6  2026 MQL5\Indicators\Examples\Panels\ChartPanel\ChartPanel.ex5
#13 130.0 -rw-r--r--  1 root root      2757 May  6  2026 MQL5\Indicators\Examples\Panels\ChartPanel\ChartPanel.mq5
#13 130.0 -rw-r--r--  1 root root     15250 May  6  2026 MQL5\Indicators\Examples\Panels\ChartPanel\PanelDialog.mqh
#13 130.0 -rw-r--r--  1 root root     14765 May  6  2026 MQL5\Indicators\Examples\Panels\SimplePanel\PanelDialog.mqh
#13 130.0 -rw-r--r--  1 root root    138356 May  6  2026 MQL5\Indicators\Examples\Panels\SimplePanel\SimplePanel.ex5
#13 130.0 -rw-r--r--  1 root root      2757 May  6  2026 MQL5\Indicators\Examples\Panels\SimplePanel\SimplePanel.mq5
#13 130.0 -rw-r--r--  1 root root     11656 May  6  2026 MQL5\Indicators\Examples\ParabolicSAR.ex5
#13 130.0 -rw-r--r--  1 root root      7056 May  6  2026 MQL5\Indicators\Examples\ParabolicSAR.mq5
#13 130.0 -rw-r--r--  1 root root      9516 May  6  2026 MQL5\Indicators\Examples\Price_Channel.ex5
#13 130.0 -rw-r--r--  1 root root      4521 May  6  2026 MQL5\Indicators\Examples\Price_Channel.mq5
#13 130.0 -rw-r--r--  1 root root      7788 May  6  2026 MQL5\Indicators\Examples\PVT.ex5
#13 130.0 -rw-r--r--  1 root root      3309 May  6  2026 MQL5\Indicators\Examples\PVT.mq5
#13 130.0 -rw-r--r--  1 root root      9972 May  6  2026 MQL5\Indicators\Examples\ROC.ex5
#13 130.0 -rw-r--r--  1 root root      2682 May  6  2026 MQL5\Indicators\Examples\ROC.mq5
#13 130.0 -rw-r--r--  1 root root     11090 May  6  2026 MQL5\Indicators\Examples\RSI.ex5
#13 130.0 -rw-r--r--  1 root root      4453 May  6  2026 MQL5\Indicators\Examples\RSI.mq5
#13 130.0 -rw-r--r--  1 root root     11902 May  6  2026 MQL5\Indicators\Examples\RVI.ex5
#13 130.0 -rw-r--r--  1 root root      4258 May  6  2026 MQL5\Indicators\Examples\RVI.mq5
#13 130.0 -rw-r--r--  1 root root     13358 May  6  2026 MQL5\Indicators\Examples\StdDev.ex5
#13 130.0 -rw-r--r--  1 root root      5185 May  6  2026 MQL5\Indicators\Examples\StdDev.mq5
#13 130.0 -rw-r--r--  1 root root     11580 May  6  2026 MQL5\Indicators\Examples\Stochastic.ex5
#13 130.0 -rw-r--r--  1 root root      4996 May  6  2026 MQL5\Indicators\Examples\Stochastic.mq5
#13 130.0 -rw-r--r--  1 root root     10510 May  6  2026 MQL5\Indicators\Examples\TEMA.ex5
#13 130.0 -rw-r--r--  1 root root      3236 May  6  2026 MQL5\Indicators\Examples\TEMA.mq5
#13 130.0 -rw-r--r--  1 root root     10400 May  6  2026 MQL5\Indicators\Examples\TRIX.ex5
#13 130.0 -rw-r--r--  1 root root      3343 May  6  2026 MQL5\Indicators\Examples\TRIX.mq5
#13 130.0 -rw-r--r--  1 root root     13332 May  6  2026 MQL5\Indicators\Examples\Ultimate_Oscillator.ex5
#13 130.0 -rw-r--r--  1 root root      6907 May  6  2026 MQL5\Indicators\Examples\Ultimate_Oscillator.mq5
#13 130.0 -rw-r--r--  1 root root      9140 May  6  2026 MQL5\Indicators\Examples\VIDYA.ex5
#13 130.0 -rw-r--r--  1 root root      3799 May  6  2026 MQL5\Indicators\Examples\VIDYA.mq5
#13 130.0 -rw-r--r--  1 root root      8398 May  6  2026 MQL5\Indicators\Examples\Volumes.ex5
#13 130.0 -rw-r--r--  1 root root      3368 May  6  2026 MQL5\Indicators\Examples\Volumes.mq5
#13 130.0 -rw-r--r--  1 root root     10872 May  6  2026 MQL5\Indicators\Examples\VROC.ex5
#13 130.0 -rw-r--r--  1 root root      3968 May  6  2026 MQL5\Indicators\Examples\VROC.mq5
#13 130.0 -rw-r--r--  1 root root      7938 May  6  2026 MQL5\Indicators\Examples\W_AD.ex5
#13 130.0 -rw-r--r--  1 root root      3381 May  6  2026 MQL5\Indicators\Examples\W_AD.mq5
#13 130.0 -rw-r--r--  1 root root      9410 May  6  2026 MQL5\Indicators\Examples\WPR.ex5
#13 130.0 -rw-r--r--  1 root root      8490 May  6  2026 MQL5\Indicators\Examples\WPR.mq5
#13 130.0 -rw-r--r--  1 root root     13136 May  6  2026 MQL5\Indicators\Examples\ZigzagColor.ex5
#13 130.0 -rw-r--r--  1 root root      9567 May  6  2026 MQL5\Indicators\Examples\ZigzagColor.mq5
#13 130.0 -rw-r--r--  1 root root     13148 May  6  2026 MQL5\Indicators\Examples\ZigZag.ex5
#13 130.0 -rw-r--r--  1 root root     18820 May  6  2026 MQL5\Indicators\Examples\ZigZag.mq5
#13 130.0 -rw-r--r--  1 root root     19102 May  6  2026 MQL5\Indicators\Free Indicators\Camarilla Channel.ex5
#13 130.0 -rw-r--r--  1 root root     10647 May  6  2026 MQL5\Indicators\Free Indicators\Camarilla Channel.mq5
#13 130.0 -rw-r--r--  1 root root     14964 May  6  2026 MQL5\Indicators\Free Indicators\DeMark Channel.ex5
#13 130.0 -rw-r--r--  1 root root      8529 May  6  2026 MQL5\Indicators\Free Indicators\DeMark Channel.mq5
#13 130.0 -rw-r--r--  1 root root     14382 May  6  2026 MQL5\Indicators\Free Indicators\Donchian Channel.ex5
#13 130.0 -rw-r--r--  1 root root     14346 May  6  2026 MQL5\Indicators\Free Indicators\Donchian Channel.mq5
#13 130.0 -rw-r--r--  1 root root     19228 May  6  2026 MQL5\Indicators\Free Indicators\Fibonacci Channel.ex5
#13 130.0 -rw-r--r--  1 root root     11225 May  6  2026 MQL5\Indicators\Free Indicators\Fibonacci Channel.mq5
#13 130.0 -rw-r--r--  1 root root     16350 May  6  2026 MQL5\Indicators\Free Indicators\Keltner Channel.ex5
#13 130.0 -rw-r--r--  1 root root      9871 May  6  2026 MQL5\Indicators\Free Indicators\Keltner Channel.mq5
#13 130.0 -rw-r--r--  1 root root     35946 May  6  2026 MQL5\Indicators\Free Indicators\MarketProfile Canvas.ex5
#13 130.0 -rw-r--r--  1 root root     19467 May  6  2026 MQL5\Indicators\Free Indicators\MarketProfile Canvas.mq5
#13 130.0 -rw-r--r--  1 root root     18362 May  6  2026 MQL5\Indicators\Free Indicators\MarketProfile.ex5
#13 130.0 -rw-r--r--  1 root root      8828 May  6  2026 MQL5\Indicators\Free Indicators\MarketProfile.mq5
#13 130.0 -rw-r--r--  1 root root     25900 May  6  2026 MQL5\Indicators\Free Indicators\MurreyMath Channel.ex5
#13 130.0 -rw-r--r--  1 root root     15173 May  6  2026 MQL5\Indicators\Free Indicators\MurreyMath Channel.mq5
#13 130.0 -rw-r--r--  1 root root     16800 May  6  2026 MQL5\Indicators\Free Indicators\NRTR Channel.ex5
#13 130.0 -rw-r--r--  1 root root     11473 May  6  2026 MQL5\Indicators\Free Indicators\NRTR Channel.mq5
#13 130.0 -rw-r--r--  1 root root     15534 May  6  2026 MQL5\Indicators\Free Indicators\Parabolic Channel.ex5
#13 130.0 -rw-r--r--  1 root root      7589 May  6  2026 MQL5\Indicators\Free Indicators\Parabolic Channel.mq5
#13 130.0 -rw-r--r--  1 root root     19176 May  6  2026 MQL5\Indicators\Free Indicators\Pivot Channel.ex5
#13 130.0 -rw-r--r--  1 root root     11242 May  6  2026 MQL5\Indicators\Free Indicators\Pivot Channel.mq5
#13 130.0 -rw-r--r--  1 root root     18344 May  6  2026 MQL5\Indicators\Free Indicators\Woodie Channel.ex5
#13 130.0 -rw-r--r--  1 root root     10104 May  6  2026 MQL5\Indicators\Free Indicators\Woodie Channel.mq5
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 MQL5\Libraries\
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 MQL5\Logs\
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 MQL5\Profiles\
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 MQL5\Profiles\Charts\
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 MQL5\Profiles\Charts\Default\
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 MQL5\Profiles\deleted\
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 MQL5\Profiles\Symbolsets\
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 MQL5\Profiles\Templates\
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 MQL5\Profiles\Tester\
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 MQL5\Scripts\
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 MQL5\Scripts\Examples\
#13 130.0 -rw-r--r--  1 root root     20904 May  6  2026 MQL5\Scripts\Examples\AccountInfo\AccountInfoSample.ex5
#13 130.0 -rw-r--r--  1 root root       917 May  6  2026 MQL5\Scripts\Examples\AccountInfo\AccountInfoSampleInit.mqh
#13 130.0 -rw-r--r--  1 root root      5904 May  6  2026 MQL5\Scripts\Examples\AccountInfo\AccountInfoSample.mq5
#13 130.0 -rw-r--r--  1 root root     17048 May  6  2026 MQL5\Scripts\Examples\ArrayDouble\ArrayDoubleSample.ex5
#13 130.0 -rw-r--r--  1 root root      4195 May  6  2026 MQL5\Scripts\Examples\ArrayDouble\ArrayDoubleSample.mq5
#13 130.0 -rw-r--r--  1 root root     31018 May  6  2026 MQL5\Scripts\Examples\Canvas\CanvasSample.ex5
#13 130.0 -rw-r--r--  1 root root      6073 May  6  2026 MQL5\Scripts\Examples\Canvas\CanvasSample.mq5
#13 130.0 -rw-r--r--  1 root root     49734 May  6  2026 MQL5\Scripts\Examples\Canvas\Charts\HistogramChartSample.ex5
#13 130.0 -rw-r--r--  1 root root      2205 May  6  2026 MQL5\Scripts\Examples\Canvas\Charts\HistogramChartSample.mq5
#13 130.0 -rw-r--r--  1 root root     49880 May  6  2026 MQL5\Scripts\Examples\Canvas\Charts\LineChartSample.ex5
#13 130.0 -rw-r--r--  1 root root      2172 May  6  2026 MQL5\Scripts\Examples\Canvas\Charts\LineChartSample.mq5
#13 130.0 -rw-r--r--  1 root root     70486 May  6  2026 MQL5\Scripts\Examples\Canvas\Charts\PieChartSample.ex5
#13 130.0 -rw-r--r--  1 root root      3201 May  6  2026 MQL5\Scripts\Examples\Canvas\Charts\PieChartSample.mq5
#13 130.0 -rw-r--r--  1 root root      4182 May  6  2026 MQL5\Scripts\Examples\ObjectChart\ChartSampleInit.mqh
#13 130.0 -rw-r--r--  1 root root     56468 May  6  2026 MQL5\Scripts\Examples\ObjectChart\ObjChartSample.ex5
#13 130.0 -rw-r--r--  1 root root     25357 May  6  2026 MQL5\Scripts\Examples\ObjectChart\ObjChartSample.mq5
#13 130.0 -rw-r--r--  1 root root      8030 May  6  2026 MQL5\Scripts\Examples\ObjectSphere\Sphere.mqh
#13 130.0 -rw-r--r--  1 root root     20650 May  6  2026 MQL5\Scripts\Examples\ObjectSphere\SphereSample.ex5
#13 130.0 -rw-r--r--  1 root root      6030 May  6  2026 MQL5\Scripts\Examples\ObjectSphere\SphereSample.mq5
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 MQL5\Scripts\Examples\OpenCL\
#13 130.0 -rw-r--r--  1 root root     20222 May  6  2026 MQL5\Scripts\Examples\OpenCL\Double\BitonicSort.ex5
#13 130.0 -rw-r--r--  1 root root      7941 May  6  2026 MQL5\Scripts\Examples\OpenCL\Double\BitonicSort.mq5
#13 130.0 -rw-r--r--  1 root root     28020 May  6  2026 MQL5\Scripts\Examples\OpenCL\Double\FFT.ex5
#13 130.0 -rw-r--r--  1 root root     13394 May  6  2026 MQL5\Scripts\Examples\OpenCL\Double\FFT.mq5
#13 130.0 -rw-r--r--  1 root root      1516 May  6  2026 MQL5\Scripts\Examples\OpenCL\Double\Kernels\bitonicsort.cl
#13 130.0 -rw-r--r--  1 root root      6038 May  6  2026 MQL5\Scripts\Examples\OpenCL\Double\Kernels\fft.cl
#13 130.0 -rw-r--r--  1 root root      2808 May  6  2026 MQL5\Scripts\Examples\OpenCL\Double\Kernels\matrixmult.cl
#13 130.0 -rw-r--r--  1 root root      1792 May  6  2026 MQL5\Scripts\Examples\OpenCL\Double\Kernels\wavelet.cl
#13 130.0 -rw-r--r--  1 root root     21030 May  6  2026 MQL5\Scripts\Examples\OpenCL\Double\MatrixMult.ex5
#13 130.0 -rw-r--r--  1 root root      8654 May  6  2026 MQL5\Scripts\Examples\OpenCL\Double\MatrixMult.mq5
#13 130.0 -rw-r--r--  1 root root    107518 May  6  2026 MQL5\Scripts\Examples\OpenCL\Double\Wavelet.ex5
#13 130.0 -rw-r--r--  1 root root     15353 May  6  2026 MQL5\Scripts\Examples\OpenCL\Double\Wavelet.mq5
#13 130.0 -rw-r--r--  1 root root     19558 May  6  2026 MQL5\Scripts\Examples\OpenCL\Float\BitonicSort.ex5
#13 130.0 -rw-r--r--  1 root root      7718 May  6  2026 MQL5\Scripts\Examples\OpenCL\Float\BitonicSort.mq5
#13 130.0 -rw-r--r--  1 root root     27460 May  6  2026 MQL5\Scripts\Examples\OpenCL\Float\FFT.ex5
#13 130.0 -rw-r--r--  1 root root     13169 May  6  2026 MQL5\Scripts\Examples\OpenCL\Float\FFT.mq5
#13 130.0 -rw-r--r--  1 root root      1348 May  6  2026 MQL5\Scripts\Examples\OpenCL\Float\Kernels\bitonicsort.cl
#13 130.0 -rw-r--r--  1 root root      5862 May  6  2026 MQL5\Scripts\Examples\OpenCL\Float\Kernels\fft.cl
#13 130.0 -rw-r--r--  1 root root      2635 May  6  2026 MQL5\Scripts\Examples\OpenCL\Float\Kernels\matrixmult.cl
#13 130.0 -rw-r--r--  1 root root      1601 May  6  2026 MQL5\Scripts\Examples\OpenCL\Float\Kernels\wavelet.cl
#13 130.0 -rw-r--r--  1 root root     20904 May  6  2026 MQL5\Scripts\Examples\OpenCL\Float\MatrixMult.ex5
#13 130.0 -rw-r--r--  1 root root      8434 May  6  2026 MQL5\Scripts\Examples\OpenCL\Float\MatrixMult.mq5
#13 130.0 -rw-r--r--  1 root root    107428 May  6  2026 MQL5\Scripts\Examples\OpenCL\Float\Wavelet.ex5
#13 130.0 -rw-r--r--  1 root root     15753 May  6  2026 MQL5\Scripts\Examples\OpenCL\Float\Wavelet.mq5
#13 130.0 -rw-r--r--  1 root root      8264 May  6  2026 MQL5\Scripts\Examples\OpenCL\Seascape\Seascape.cl
#13 130.0 -rw-r--r--  1 root root     14704 May  6  2026 MQL5\Scripts\Examples\OpenCL\Seascape\Seascape.ex5
#13 130.0 -rw-r--r--  1 root root      6215 May  6  2026 MQL5\Scripts\Examples\OpenCL\Seascape\Seascape.mq5
#13 130.0 -rw-r--r--  1 root root      2486 May  6  2026 MQL5\Scripts\Examples\OpenCL\Seascape\Seascape.mqproj
#13 130.0 -rw-r--r--  1 root root     27072 May  6  2026 MQL5\Scripts\Examples\OrderInfo\OrderInfoSample.ex5
#13 130.0 -rw-r--r--  1 root root       931 May  6  2026 MQL5\Scripts\Examples\OrderInfo\OrderInfoSampleInit.mqh
#13 130.0 -rw-r--r--  1 root root      7459 May  6  2026 MQL5\Scripts\Examples\OrderInfo\OrderInfoSample.mq5
#13 130.0 -rw-r--r--  1 root root     21718 May  6  2026 MQL5\Scripts\Examples\PositionInfo\PositionInfoSample.ex5
#13 130.0 -rw-r--r--  1 root root       830 May  6  2026 MQL5\Scripts\Examples\PositionInfo\PositionInfoSampleInit.mqh
#13 130.0 -rw-r--r--  1 root root      7287 May  6  2026 MQL5\Scripts\Examples\PositionInfo\PositionInfoSample.mq5
#13 130.0 -rw-r--r--  1 root root     17082 May  6  2026 MQL5\Scripts\Examples\Remnant 3D\Remnant 3D.ex5
#13 130.0 -rw-r--r--  1 root root      5696 May  6  2026 MQL5\Scripts\Examples\Remnant 3D\Remnant 3D.mq5
#13 130.0 -rw-r--r--  1 root root      1744 May  6  2026 MQL5\Scripts\Examples\Remnant 3D\Remnant 3D.mqproj
#13 130.0 -rw-r--r--  1 root root     10093 May  6  2026 MQL5\Scripts\Examples\Remnant 3D\Shaders\pixel.hlsl
#13 130.0 -rw-r--r--  1 root root       254 May  6  2026 MQL5\Scripts\Examples\Remnant 3D\Shaders\vertex.hlsl
#13 130.0 -rw-r--r--  1 root root     34886 May  6  2026 MQL5\Scripts\Examples\SymbolInfo\SymbolInfoSample.ex5
#13 130.0 -rw-r--r--  1 root root      1774 May  6  2026 MQL5\Scripts\Examples\SymbolInfo\SymbolInfoSampleInit.mqh
#13 130.0 -rw-r--r--  1 root root      8811 May  6  2026 MQL5\Scripts\Examples\SymbolInfo\SymbolInfoSample.mq5
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 MQL5\Scripts\UnitTests\
#13 130.0 -rw-r--r--  1 root root     31148 May  6  2026 MQL5\Scripts\UnitTests\Alglib\TestClasses.mq5
#13 130.0 -rw-r--r--  1 root root   3105290 May  6  2026 MQL5\Scripts\UnitTests\Alglib\TestClasses.mqh
#13 130.0 -rw-r--r--  1 root root     17911 May  6  2026 MQL5\Scripts\UnitTests\Alglib\TestInterfaces.mq5
#13 130.0 -rw-r--r--  1 root root    695999 May  6  2026 MQL5\Scripts\UnitTests\Alglib\TestInterfaces.mqh
#13 130.0 -rw-r--r--  1 root root     27020 May  6  2026 MQL5\Scripts\UnitTests\Fuzzy\TestFuzzy.mq5
#13 130.0 -rw-r--r--  1 root root     34226 May  6  2026 MQL5\Scripts\UnitTests\Generic\TestArrayList.mq5
#13 130.0 -rw-r--r--  1 root root      6302 May  6  2026 MQL5\Scripts\UnitTests\Generic\TestHashMap.mq5
#13 130.0 -rw-r--r--  1 root root      5013 May  6  2026 MQL5\Scripts\UnitTests\Generic\TestHashSet.mq5
#13 130.0 -rw-r--r--  1 root root     62465 May  6  2026 MQL5\Scripts\UnitTests\Generic\TestLinkedList.mq5
#13 130.0 -rw-r--r--  1 root root      8756 May  6  2026 MQL5\Scripts\UnitTests\Generic\TestQueue.mq5
#13 130.0 -rw-r--r--  1 root root     12001 May  6  2026 MQL5\Scripts\UnitTests\Generic\TestRedBlackTree.mq5
#13 130.0 -rw-r--r--  1 root root      6443 May  6  2026 MQL5\Scripts\UnitTests\Generic\TestSortedMap.mq5
#13 130.0 -rw-r--r--  1 root root      5426 May  6  2026 MQL5\Scripts\UnitTests\Generic\TestSortedSet.mq5
#13 130.0 -rw-r--r--  1 root root      8752 May  6  2026 MQL5\Scripts\UnitTests\Generic\TestStack.mq5
#13 130.0 -rw-r--r--  1 root root     58178 May  6  2026 MQL5\Scripts\UnitTests\Stat\TestStatBenchmark.mq5
#13 130.0 -rw-r--r--  1 root root    216091 May  6  2026 MQL5\Scripts\UnitTests\Stat\TestStat.mq5
#13 130.0 -rw-r--r--  1 root root     16162 May  6  2026 MQL5\Scripts\UnitTests\Stat\TestStatPrecision.mq5
#13 130.0 drwxr-xr-x  2 root root      4096 May  6  2026 MQL5\Services\
#13 130.0 -rw-r--r--  1 root root 143962424 May  6  2026 terminal64.exe
#13 130.0 + [ -f /tmp/mt5-extract-1/terminal64.exe ]
#13 130.0 + echo Flat ZIP detected — moving files into '/opt/wineprefix/drive_c/Program Files/MetaTrader 5'
#13 130.0 + Flat ZIP detected — moving files into '/opt/wineprefix/drive_c/Program Files/MetaTrader 5'
#13 130.0 mkdir -p /opt/wineprefix/drive_c/Program Files/MetaTrader 5
#13 130.0 + mv /tmp/mt5-extract-1/MQL5\Experts\ /tmp/mt5-extract-1/MQL5\Experts\Advisors\ExpertMACD.ex5 /tmp/mt5-extract-1/MQL5\Experts\Advisors\ExpertMACD.mq5 /tmp/mt5-extract-1/MQL5\Experts\Advisors\ExpertMAMA.ex5 /tmp/mt5-extract-1/MQL5\Experts\Advisors\ExpertMAMA.mq5 /tmp/mt5-extract-1/MQL5\Experts\Advisors\ExpertMAPSAR.ex5 /tmp/mt5-extract-1/MQL5\Experts\Advisors\ExpertMAPSAR.mq5 /tmp/mt5-extract-1/MQL5\Experts\Advisors\ExpertMAPSARSizeOptimized.ex5 /tmp/mt5-extract-1/MQL5\Experts\Advisors\ExpertMAPSARSizeOptimized.mq5 /tmp/mt5-extract-1/MQL5\Experts\Examples\ /tmp/mt5-extract-1/MQL5\Experts\Examples\ChartInChart\ChartInChart.ex5 /tmp/mt5-extract-1/MQL5\Experts\Examples\ChartInChart\ChartInChart.mq5 /tmp/mt5-extract-1/MQL5\Experts\Examples\Controls\Controls.ex5 /tmp/mt5-extract-1/MQL5\Experts\Examples\Controls\Controls.mq5 /tmp/mt5-extract-1/MQL5\Experts\Examples\Controls\ControlsDialog.mqh /tmp/mt5-extract-1/MQL5\Experts\Examples\Correlation Matrix 3D\Correlation Matrix 3D.ex5 /tmp/mt5-extract-1/MQL5\Ex
#13 130.0 + find /opt/wineprefix/drive_c/Program Files/MetaTrader 5 -maxdepth 1 -name *\\*
#13 130.0 + grep -q .
#13 130.0 + echo Fixing Windows backslash paths -> POSIX directory structure...
#13 130.0 Fixing Windows backslash paths -> POSIX directory structure...
#13 130.0 + printf import os,shutil,sys\ntarget=sys.argv[1]\nentries=[e for e in os.listdir(target) if chr(92) in e]\nentries.sort(key=lambda x:x.count(chr(92)),reverse=True)\nfor name in entries:\n src=os.path.join(target,name)\n dst=os.path.join(target,name.replace(chr(92),"/"))\n os.makedirs(os.path.dirname(dst),exist_ok=True)\n if not os.path.exists(dst):\n  shutil.move(src,dst)\nprint("Moved",len(entries),"backslash entries to proper paths.")\n
#13 130.0 + python3 /tmp/fix_paths.py /opt/wineprefix/drive_c/Program Files/MetaTrader 5
#13 130.1 Moved 629 backslash entries to proper paths.
#13 130.1 + rm -f /tmp/fix_paths.py
#13 130.1 + echo Backslash path fix done.
#13 130.1 + rm -rfBackslash path fix done.
#13 130.1  /tmp/mt5-extract-1
#13 130.1 + + head -1
#13 130.1 find /opt/wineprefix/drive_c/Program Files/MetaTrader 5 -maxdepth 1 -name terminal64.exe
#13 130.1 terminal64.exe after normalization: /opt/wineprefix/drive_c/Program Files/MetaTrader 5/terminal64.exe
#13 130.1 === drive_c top-level ===
#13 130.1 + TERM_EXE=/opt/wineprefix/drive_c/Program Files/MetaTrader 5/terminal64.exe
#13 130.1 + echo terminal64.exe after normalization: /opt/wineprefix/drive_c/Program Files/MetaTrader 5/terminal64.exe
#13 130.1 + [ -z /opt/wineprefix/drive_c/Program Files/MetaTrader 5/terminal64.exe ]
#13 130.1 + echo === drive_c top-level ===
#13 130.1 + ls /opt/wineprefix/drive_c/
#13 130.1 ProgramData
#13 130.1 Program Files
#13 130.1 Program Files (x86)
#13 130.1 users
#13 130.1 windows
#13 130.1 + echo === Program Files ===
#13 130.1 + ls /opt/wineprefix/drive_c/Program Files/
#13 130.1 === Program Files ===
#13 130.1 Common Files
#13 130.1 Internet Explorer
#13 130.1 MetaTrader 5
#13 130.1 Python39
#13 130.1 Windows Media Player
#13 130.1 Windows NT
#13 130.1 + terminal64.exe: /opt/wineprefix/drive_c/Program Files/MetaTrader 5/terminal64.exe
#13 130.1 echo terminal64.exe: /opt/wineprefix/drive_c/Program Files/MetaTrader 5/terminal64.exe
#13 130.1 + [ -n /opt/wineprefix/drive_c/Program Files/MetaTrader 5/terminal64.exe ]
#13 130.1 + echo /opt/wineprefix/drive_c/Program Files/MetaTrader 5/terminal64.exe
#13 130.1 + touch /opt/mt5_terminal.preinstalled
#13 130.1 + chmod +x /bridge/smoke-test-ipc.sh
#13 130.1 + rm -f /tmp/python-installer.exe /tmp/mt5setup.exe
#13 130.1 + kill 10
#13 130.1 + rm -f /tmp/.X99-lock
#13 DONE 131.7s
#14 [8/9] RUN set -eux;     if [ ! -f /opt/mt5_terminal.preinstalled ]; then       echo "HAS_MT5 was false — skipping terminal first-run init";       exit 0;     fi;         TERM_EXE="$(tr -d '\r\n' < /opt/mt5_terminal_exe.path)";     TERM_DIR="$(dirname "${TERM_EXE}")";         rm -f /tmp/.X99-lock;     Xvfb :99 -screen 0 1280x720x24 &     XVFB_PID=$!;     sleep 3;     openbox --sm-disable > /tmp/openbox-init.log 2>&1 &     OPENBOX_PID=$!;     sleep 2;         echo "Locking terminal exes (read-only) to prevent self-update during init...";     find "${TERM_DIR}" ( -name '*.exe' -o -name '*.dll' )       -exec chmod a-w {} ; 2>/dev/null || true;     echo "Exes locked.";         WINE_HOSTS="/opt/wineprefix/drive_c/windows/system32/drivers/etc/hosts";     mkdir -p "$(dirname "${WINE_HOSTS}")" 2>/dev/null || true;     for _d in update.mql5.com updates.mql5.com                update.metatrader5.com updates.metatrader5.com                mt5-update.metaquotes.net; do       echo "127.0.0.1 ${_d}" >> "${WINE_HOSTS}" 2
#14 0.120 + [ ! -f /opt/mt5_terminal.preinstalled ]
#14 0.122 + tr -d \r\n
#14 0.123 + TERM_EXE=/opt/wineprefix/drive_c/Program Files/MetaTrader 5/terminal64.exe
#14 0.123 + dirname /opt/wineprefix/drive_c/Program Files/MetaTrader 5/terminal64.exe
#14 0.125 + TERM_DIR=/opt/wineprefix/drive_c/Program Files/MetaTrader 5
#14 0.126 + rm -f /tmp/.X99-lock
#14 0.128 + + Xvfb :99 -screen 0 1280x720x24
#14 0.128 XVFB_PID=9
#14 0.128 + sleep 3
#14 0.459 The XKEYBOARD keymap compiler (xkbcomp) reports:
#14 0.459 > Warning:          Could not resolve keysym XF86CameraAccessEnable
#14 0.459 > Warning:          Could not resolve keysym XF86CameraAccessDisable
#14 0.459 > Warning:          Could not resolve keysym XF86CameraAccessToggle
#14 0.460 > Warning:          Could not resolve keysym XF86NextElement
#14 0.460 > Warning:          Could not resolve keysym XF86PreviousElement
#14 0.460 > Warning:          Could not resolve keysym XF86AutopilotEngageToggle
#14 0.460 > Warning:          Could not resolve keysym XF86MarkWaypoint
#14 0.460 > Warning:          Could not resolve keysym XF86Sos
#14 0.460 > Warning:          Could not resolve keysym XF86NavChart
#14 0.460 > Warning:          Could not resolve keysym XF86FishingChart
#14 0.460 > Warning:          Could not resolve keysym XF86SingleRangeRadar
#14 0.460 > Warning:          Could not resolve keysym XF86DualRangeRadar
#14 0.460 > Warning:          Could not resolve keysym XF86RadarOverlay
#14 0.460 > Warning:          Could not resolve keysym XF86TraditionalSonar
#14 0.460 > Warning:          Could not resolve keysym XF86ClearvuSonar
#14 0.460 > Warning:          Could not resolve keysym XF86SidevuSonar
#14 0.460 > Warning:          Could not resolve keysym XF86NavInfo
#14 0.463 Errors from xkbcomp are not fatal to the X server
#14 3.131 + OPENBOX_PID=13
#14 3.131 + sleep 2
#14 3.132 + openbox --sm-disable
#14 5.133 + echo Locking terminal exes (read-only) to prevent self-update during init...
#14 5.133 Locking terminal exes (read-only) to prevent self-update during init...
#14 5.133 + find /opt/wineprefix/drive_c/Program Files/MetaTrader 5 ( -name *.exe -o -name *.dll ) -exec chmod a-w {} ;
#14 6.349 Exes locked.
#14 6.349 + echo Exes locked.
#14 6.349 + WINE_HOSTS=/opt/wineprefix/drive_c/windows/system32/drivers/etc/hosts
#14 6.349 + dirname /opt/wineprefix/drive_c/windows/system32/drivers/etc/hosts
#14 6.350 + mkdir -p /opt/wineprefix/drive_c/windows/system32/drivers/etc
#14 6.354 + echo 127.0.0.1 update.mql5.com
#14 6.354 + echo 127.0.0.1 updates.mql5.com
#14 6.354 + echo 127.0.0.1 update.metatrader5.com
#14 6.354 Update-check domains blocked; data CDNs remain open.
#14 6.354 + echo 127.0.0.1 updates.metatrader5.com
#14 6.354 + echo 127.0.0.1 mt5-update.metaquotes.net
#14 6.354 + echo Update-check domains blocked; data CDNs remain open.
#14 6.354 + mkdir -p /opt/wineprefix/drive_c/Program Files/MetaTrader 5/config
#14 6.356 + printf [Common]\r\nLogin=435609450\r\nPassword=Mznxbcv12#\r\nServer=Exness-MT5Trial9\r\nNewsEnable=0\r\nAutoSync=0\r\nAutoUpdate=0\r\n
#14 6.357 + printf [Startup]\r\nAutoStart=0\r\n\r\n[Common]\r\nLogin=435609450\r\nPassword=Mznxbcv12#\r\nServer=Exness-MT5Trial9\r\nNewsEnable=0\r\nAutoSync=0\r\nAutoUpdate=0\r\n\r\n[Experts]\r\nEnabled=1\r\nAllowLiveTrading=1\r\n\r\n[LiveUpdate]\r\nEnabled=0\r\nNextUpdate=9999999999\r\n
#14 6.357 + echo Pre-wrote portable terminal.ini and config/common.ini
#14 6.357 Pre-wrote portable terminal.ini and config/common.ini
#14 6.358 Removing liveme.exe (blocks IPC if present)...
#14 6.358 + echo Removing liveme.exe (blocks IPC if present)...
#14 6.358 + find /opt/wineprefix/drive_c/Program Files/MetaTrader 5 -maxdepth 1 -iname liveme*.exe -delete
#14 6.359 + ls -la /opt/wineprefix/drive_c/Program Files/MetaTrader 5/liveme.exe
#14 6.361 + echo liveme.exe removed (or was never present)
#14 6.361 + echo Launching terminal in portable mode (internet-accessible for data download)...
#14 6.361 liveme.exe removed (or was never present)
#14 6.361 Launching terminal in portable mode (internet-accessible for data download)...
#14 6.361 + TERM_PID=26
#14 6.362 + DISMISS_PID=27
#14 6.362 + DISPLAY=:99 WINEDEBUG=err+all WINEESYNC=0 WINEFSYNC=0 wine /opt/wineprefix/drive_c/Program Files/MetaTrader 5/terminal64.exe+ true
#14 6.363  /portable
#14 6.363 + + DISPLAY=:99 xdotool search --onlyvisible --name Select a company
#14 6.364 wc -c
#14 6.368 + PRE_TINI_SIZE=233
#14 6.369 Phase 1: Checking for pre-packaged installation...
#14 6.369 + echo Phase 1: Checking for pre-packaged installation...
#14 6.370 + find /opt/wineprefix/drive_c/Program Files/MetaTrader 5 -type f
#14 6.371 + wc -l
#14 6.380 + PFILES=599
#14 6.380 + echo Found 599 files in install dir.
#14 6.381 Found 599 files in install dir.
#14 6.381 + [ 599 -lt 50 ]
#14 6.382 + echo Phase 1 done: 599 files present.
#14 6.383 Phase 1 done: 599 files present.
#14 6.383 + echo Phase 2: Letting terminal complete self-restart cycle (90s)...
#14 6.384 Phase 2: Letting terminal complete self-restart cycle (90s)...
#14 6.384 + WARMUP_TOTAL=90
#14 6.384 + WARMUP_WAITED=0
#14 6.385 + RESTART_DETECTED=false
#14 6.385 + [ 0 -lt 90 ]
#14 6.387 + kill+ true
#14 6.388  -0+  26
#14 6.390 + _wids=
#14 6.392 echo   0s: terminal PID 26 alive
#14 6.394   0s: terminal PID 26 alive
#14 6.395 + sleep 10
#14 6.397 + DISPLAY=:99 xdotool search --onlyvisible --name Welcome to
#14 6.408 + true
#14 6.409 + _wids=
#14 6.411 + DISPLAY=:99 xdotool search --onlyvisible --name LiveUpdate
#14 6.420 + true
#14 6.420 + _wids=
#14 6.421 + DISPLAY=:99 xdotool search --onlyvisible --name Setup
#14 6.429 + true
#14 6.430 + _wids=
#14 6.432 + DISPLAY=:99 xdotool search --onlyvisible --name Login
#14 6.439 + true
#14 6.440 + _wids=
#14 6.441 + sleep 5
#14 11.44 + true
#14 11.44 + DISPLAY=:99 xdotool search --onlyvisible --name Select a company
#14 11.46 + true
#14 11.46 + _wids=
#14 11.46 + DISPLAY=:99 xdotool search --onlyvisible --name Welcome to
#14 11.47 + true
#14 11.48 + _wids=
#14 11.48 + DISPLAY=:99 xdotool search --onlyvisible --name LiveUpdate
#14 11.49 + true
#14 11.49 + _wids=
#14 11.49 + DISPLAY=:99 xdotool search --onlyvisible --name Setup
#14 11.51 + true
#14 11.51 + _wids=
#14 11.51 + DISPLAY=:99 xdotool search --onlyvisible --name Login
#14 11.52 + true
#14 11.52 + _wids=
#14 11.52 + sleep 5
#14 16.40 + WARMUP_WAITED=10
#14 16.40 + [ 10 -lt 90 ]
#14 16.40 + kill -0 26
#14 16.40 + echo   10s: terminal PID 26 alive
#14 16.40 + sleep 10
#14 16.40   10s: terminal PID 26 alive
#14 16.52 + true
#14 16.52 + DISPLAY=:99 xdotool search --onlyvisible --name Select a company
#14 16.54 + true
#14 16.54 + _wids=
#14 16.54 + DISPLAY=:99 xdotool search --onlyvisible --name Welcome to
#14 16.55 + true
#14 16.56 + _wids=
#14 16.56 + DISPLAY=:99 xdotool search --onlyvisible --name LiveUpdate
#14 16.57 + true
#14 16.57 + _wids=
#14 16.57 + DISPLAY=:99 xdotool search --onlyvisible --name Setup
#14 16.59 + true
#14 16.59 + _wids=
#14 16.59 + DISPLAY=:99 xdotool search --onlyvisible --name Login
#14 16.60 + true
#14 16.60 + _wids=
#14 16.60 + sleep 5
#14 21.60 + true
#14 21.60 + DISPLAY=:99 xdotool search --onlyvisible --name Select a company
#14 21.62 + true
#14 21.62 + _wids=
#14 21.62 + DISPLAY=:99 xdotool search --onlyvisible --name Welcome to
#14 21.64 + true
#14 21.64 + _wids=
#14 21.64 + DISPLAY=:99 xdotool search --onlyvisible --name LiveUpdate
#14 21.65 + true
#14 21.65 + _wids=
#14 21.65 + DISPLAY=:99 xdotool search --onlyvisible --name Setup
#14 21.67 + true
#14 21.67 + _wids=
#14 21.67 + DISPLAY=:99 xdotool search --onlyvisible --name Login
#14 21.68 + true
#14 21.69 + _wids=
#14 21.69 + sleep 5
#14 26.40 + WARMUP_WAITED=20
#14 26.40 + [ 20 -lt 90 ]
#14 26.40 + kill -0 26
#14 26.40 + echo   20s: terminal PID 26 alive
#14 26.40 + sleep 10
#14 26.40   20s: terminal PID 26 alive
#14 26.69 + true
#14 26.69 + DISPLAY=:99 xdotool search --onlyvisible --name Select a company
#14 26.70 + true
#14 26.70 + _wids=
#14 26.71 + DISPLAY=:99 xdotool search --onlyvisible --name Welcome to
#14 26.72 + true
#14 26.72 + _wids=
#14 26.72 + DISPLAY=:99 xdotool search --onlyvisible --name LiveUpdate
#14 26.74 + true
#14 26.74 + _wids=
#14 26.74 + DISPLAY=:99 xdotool search --onlyvisible --name Setup
#14 26.75 + true
#14 26.75 + _wids=
#14 26.75 + DISPLAY=:99 xdotool search --onlyvisible --name Login
#14 26.77 + true
#14 26.77 + _wids=
#14 26.77 + sleep 5
#14 31.77 + true
#14 31.77 + DISPLAY=:99 xdotool search --onlyvisible --name Select a company
#14 31.79 + true
#14 31.79 + _wids=
#14 31.79 + DISPLAY=:99 xdotool search --onlyvisible --name Welcome to
#14 31.80 + true
#14 31.80 + _wids=
#14 31.81 + DISPLAY=:99 xdotool search --onlyvisible --name LiveUpdate
#14 31.82 + true
#14 31.82 + _wids=
#14 31.82 + DISPLAY=:99 xdotool search --onlyvisible --name Setup
#14 31.84 + true
#14 31.84 + _wids=
#14 31.84 + DISPLAY=:99 xdotool search --onlyvisible --name Login
#14 31.85 + true
#14 31.85 + _wids=
#14 31.85 + sleep 5
#14 36.41 + WARMUP_WAITED=30
#14 36.41 + [ 30 -lt 90 ]
#14 36.41 + kill -0 26
#14 36.41   30s: terminal PID 26 alive
#14 36.41 + echo   30s: terminal PID 26 alive
#14 36.41 + sleep 10
#14 36.85 + true
#14 36.86 + DISPLAY=:99 xdotool search --onlyvisible --name Select a company
#14 36.87 + true
#14 36.87 + _wids=
#14 36.87 + DISPLAY=:99 xdotool search --onlyvisible --name Welcome to
#14 36.89 + true
#14 36.89 + _wids=
#14 36.89 + DISPLAY=:99 xdotool search --onlyvisible --name LiveUpdate
#14 36.90 + true
#14 36.90 + _wids=
#14 36.90 + DISPLAY=:99 xdotool search --onlyvisible --name Setup
#14 36.92 + true
#14 36.92 + _wids=
#14 36.92 + DISPLAY=:99 xdotool search --onlyvisible --name Login
#14 36.94 + true
#14 36.94 + _wids=
#14 36.94 + sleep 5
#14 41.94 + true
#14 41.94 + DISPLAY=:99 xdotool search --onlyvisible --name Select a company
#14 41.96 + true
#14 41.96 + _wids=
#14 41.96 + DISPLAY=:99 xdotool search --onlyvisible --name Welcome to
#14 41.97 + true
#14 41.97 + _wids=
#14 41.97 + DISPLAY=:99 xdotool search --onlyvisible --name LiveUpdate
#14 41.99 + true
#14 41.99 + _wids=
#14 41.99 + DISPLAY=:99 xdotool search --onlyvisible --name Setup
#14 42.00 + true
#14 42.00 + _wids=
#14 42.00 + DISPLAY=:99 xdotool search --onlyvisible --name Login
#14 42.02 + true
#14 42.02 + _wids=
#14 42.02 + sleep 5
#14 46.41 + WARMUP_WAITED=40
#14 46.41 + [ 40 -lt 90 ]
#14 46.41 + kill -0 26
#14 46.41   40s: terminal PID 26 alive
#14 46.41 + echo   40s: terminal PID 26 alive
#14 46.41 + sleep 10
#14 47.02 + true
#14 47.02 + DISPLAY=:99 xdotool search --onlyvisible --name Select a company
#14 47.04 + true
#14 47.04 + _wids=
#14 47.04 + DISPLAY=:99 xdotool search --onlyvisible --name Welcome to
#14 47.05 + true
#14 47.05 + _wids=
#14 47.06 + DISPLAY=:99 xdotool search --onlyvisible --name LiveUpdate
#14 47.07 + true
#14 47.07 + _wids=
#14 47.07 + DISPLAY=:99 xdotool search --onlyvisible --name Setup
#14 47.09 + true
#14 47.09 + _wids=
#14 47.09 + DISPLAY=:99 xdotool search --onlyvisible --name Login
#14 47.10 + true
#14 47.10 + _wids=
#14 47.10 + sleep 5
#14 52.11 + true
#14 52.11 + DISPLAY=:99 xdotool search --onlyvisible --name Select a company
#14 52.12 + true
#14 52.12 + _wids=
#14 52.12 + DISPLAY=:99 xdotool search --onlyvisible --name Welcome to
#14 52.14 + true
#14 52.14 + _wids=
#14 52.14 + DISPLAY=:99 xdotool search --onlyvisible --name LiveUpdate
#14 52.16 + true
#14 52.16 + _wids=
#14 52.16 + DISPLAY=:99 xdotool search --onlyvisible --name Setup
#14 52.17 + true
#14 52.17 + _wids=
#14 52.17 + DISPLAY=:99 xdotool search --onlyvisible --name Login
#14 52.19 + true
#14 52.19 + _wids=
#14 52.19 + sleep 5
#14 56.41 + WARMUP_WAITED=50
#14 56.41 + [ 50 -lt 90 ]
#14 56.41 + kill -0 26
#14 56.41 + echo   50s: terminal PID 26 alive
#14 56.41   50s: terminal PID 26 alive
#14 56.41 + sleep 10
#14 57.19 + true
#14 57.19 + DISPLAY=:99 xdotool search --onlyvisible --name Select a company
#14 57.21 + true
#14 57.21 + _wids=
#14 57.21 + DISPLAY=:99 xdotool search --onlyvisible --name Welcome to
#14 57.22 + true
#14 57.22 + _wids=
#14 57.22 + DISPLAY=:99 xdotool search --onlyvisible --name LiveUpdate
#14 57.24 + true
#14 57.24 + _wids=
#14 57.24 + DISPLAY=:99 xdotool search --onlyvisible --name Setup
#14 57.25 + true
#14 57.25 + _wids=
#14 57.26 + DISPLAY=:99 xdotool search --onlyvisible --name Login
#14 57.28 + true
#14 57.28 + _wids=
#14 57.28 + sleep 5
#14 62.28 + true
#14 62.28 + DISPLAY=:99 xdotool search --onlyvisible --name Select a company
#14 62.29 + true
#14 62.29 + _wids=
#14 62.29 + DISPLAY=:99 xdotool search --onlyvisible --name Welcome to
#14 62.31 + true
#14 62.31 + _wids=
#14 62.31 + DISPLAY=:99 xdotool search --onlyvisible --name LiveUpdate
#14 62.33 + true
#14 62.33 + _wids=
#14 62.33 + DISPLAY=:99 xdotool search --onlyvisible --name Setup
#14 62.34 + true
#14 62.34 + _wids=
#14 62.34 + DISPLAY=:99 xdotool search --onlyvisible --name Login
#14 62.36 + true
#14 62.36 + _wids=
#14 62.36 + sleep 5
#14 66.41 + WARMUP_WAITED=60
#14 66.41 + [ 60 -lt 90 ]
#14 66.41   60s: terminal PID 26 alive
#14 66.41 + kill -0 26
#14 66.41 + echo   60s: terminal PID 26 alive
#14 66.41 + sleep 10
#14 67.36 + true
#14 67.36 + DISPLAY=:99 xdotool search --onlyvisible --name Select a company
#14 67.38 + true
#14 67.38 + _wids=
#14 67.38 + DISPLAY=:99 xdotool search --onlyvisible --name Welcome to
#14 67.39 + true
#14 67.39 + _wids=
#14 67.40 + DISPLAY=:99 xdotool search --onlyvisible --name LiveUpdate
#14 67.41 + true
#14 67.41 + _wids=
#14 67.41 + DISPLAY=:99 xdotool search --onlyvisible --name Setup
#14 67.43 + true
#14 67.43 + _wids=
#14 67.43 + DISPLAY=:99 xdotool search --onlyvisible --name Login
#14 67.44 + true
#14 67.44 + _wids=
#14 67.44 + sleep 5
#14 72.44 + true
#14 72.45 + DISPLAY=:99 xdotool search --onlyvisible --name Select a company
#14 72.46 + true
#14 72.46 + _wids=
#14 72.46 + DISPLAY=:99 xdotool search --onlyvisible --name Welcome to
#14 72.48 + true
#14 72.48 + _wids=
#14 72.48 + DISPLAY=:99 xdotool search --onlyvisible --name LiveUpdate
#14 72.49 + true
#14 72.49 + _wids=
#14 72.49 + DISPLAY=:99 xdotool search --onlyvisible --name Setup
#14 72.51 + true
#14 72.51 + _wids=
#14 72.51 + DISPLAY=:99 xdotool search --onlyvisible --name Login
#14 72.52 + true
#14 72.52 + _wids=
#14 72.52 + sleep 5
#14 76.41 + WARMUP_WAITED=70
#14 76.41 + [ 70 -lt 90 ]
#14 76.41 + kill -0 26
#14 76.41   70s: terminal PID 26 alive
#14 76.42 + echo   70s: terminal PID 26 alive
#14 76.42 + sleep 10
#14 77.53 + true
#14 77.53 + DISPLAY=:99 xdotool search --onlyvisible --name Select a company
#14 77.54 + true
#14 77.54 + _wids=
#14 77.54 + DISPLAY=:99 xdotool search --onlyvisible --name Welcome to
#14 77.56 + true
#14 77.56 + _wids=
#14 77.56 + DISPLAY=:99 xdotool search --onlyvisible --name LiveUpdate
#14 77.58 + true
#14 77.58 + _wids=
#14 77.58 + DISPLAY=:99 xdotool search --onlyvisible --name Setup
#14 77.59 + true
#14 77.59 + _wids=
#14 77.59 + DISPLAY=:99 xdotool search --onlyvisible --name Login
#14 77.61 + true
#14 77.61 + _wids=
#14 77.61 + sleep 5
#14 82.61 + true
#14 82.61 + DISPLAY=:99 xdotool search --onlyvisible --name Select a company
#14 82.62 + true
#14 82.62 + _wids=
#14 82.63 + DISPLAY=:99 xdotool search --onlyvisible --name Welcome to
#14 82.64 + true
#14 82.64 + _wids=
#14 82.64 + DISPLAY=:99 xdotool search --onlyvisible --name LiveUpdate
#14 82.66 + true
#14 82.66 + _wids=
#14 82.66 + DISPLAY=:99 xdotool search --onlyvisible --name Setup
#14 82.67 + true
#14 82.67 + _wids=
#14 82.67 + DISPLAY=:99 xdotool search --onlyvisible --name Login
#14 82.69 + true
#14 82.69 + _wids=
#14 82.69 + sleep 5
#14 86.42 + WARMUP_WAITED=80
#14 86.42 + [ 80 -lt 90 ]
#14 86.42 + kill -0 26
#14 86.42 + echo   80s: terminal PID 26 alive
#14 86.42   80s: terminal PID 26 alive
#14 86.42 + sleep 10
#14 87.69 + true
#14 87.69 + DISPLAY=:99 xdotool search --onlyvisible --name Select a company
#14 87.71 + true
#14 87.71 + _wids=
#14 87.71 + DISPLAY=:99 xdotool search --onlyvisible --name Welcome to
#14 87.73 + true
#14 87.73 + _wids=
#14 87.73 + DISPLAY=:99 xdotool search --onlyvisible --name LiveUpdate
#14 87.74 + true
#14 87.74 + _wids=
#14 87.74 + DISPLAY=:99 xdotool search --onlyvisible --name Setup
#14 87.76 + true
#14 87.76 + _wids=
#14 87.76 + DISPLAY=:99 xdotool search --onlyvisible --name Login
#14 87.77 + true
#14 87.77 + _wids=
#14 87.77 + sleep 5
#14 92.78 + true
#14 92.78 + DISPLAY=:99 xdotool search --onlyvisible --name Select a company
#14 92.79 + true
#14 92.79 + _wids=
#14 92.79 + DISPLAY=:99 xdotool search --onlyvisible --name Welcome to
#14 92.81 + true
#14 92.81 + _wids=
#14 92.81 + DISPLAY=:99 xdotool search --onlyvisible --name LiveUpdate
#14 92.83 + true
#14 92.83 + _wids=
#14 92.83 + DISPLAY=:99 xdotool search --onlyvisible --name Setup
#14 92.84 + true
#14 92.84 + _wids=
#14 92.84 + DISPLAY=:99 xdotool search --onlyvisible --name Login
#14 92.86 + true
#14 92.86 + _wids=
