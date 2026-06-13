# Adaptive Trading Bot — Full System Architecture

> **Status:** Living document. Last updated 2026-06-13.
> **Scope:** Every service, every integration boundary, the data layer, end-to-end runtime flows, deployment topology, trading frequency tuning, MT5 bridge deployment, release process, and a prioritized list of issues + missing pieces.

---

## 0. Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [High-Level Topology](#2-high-level-topology)
3. [Service A — Python Backend (Core API + Brain)](#3-service-a--python-backend-core-api--brain)
4. [Service B — MT5 Bridge Service](#4-service-b--mt5-bridge-service)
5. [Service C — Backtester Service](#5-service-c--backtester-service)
6. [Service D — Frontend (React/Vite)](#6-service-d--frontend-reactvite)
7. [The Data Layer (MongoDB)](#7-the-data-layer-mongodb)
8. [Strategy Framework, Ensemble, Picker & Adaptation](#8-strategy-framework-ensemble-picker--adaptation)
9. [End-to-End Integration Flows](#9-end-to-end-integration-flows)
10. [Cross-Service Integration Matrix](#10-cross-service-integration-matrix)
11. [Trading Frequency Tuning](#11-trading-frequency-tuning)
12. [MT5 Bridge Deployment & IPC](#12-mt5-bridge-deployment--ipc)
13. [Release & Build Process](#13-release--build-process)
14. [Build History Notes](#14-build-history-notes)
15. [Deployment Topology & Configuration](#15-deployment-topology--configuration)
16. [Issues That Must Be Solved](#16-issues-that-must-be-solved)
17. [Missing / Recommended Additions](#17-missing--recommended-additions)

---

## 1. Executive Summary

This is a **multi-service, self-adapting algorithmic trading system** that trades primarily **XAUUSD (gold)** through **MetaTrader 5**. It is built around four independently deployed services plus a shared MongoDB database:

| # | Service | Tech | Role | Default Port |
|---|---------|------|------|--------------|
| A | **python-backend** | FastAPI (async) | The "brain" — REST/WebSocket API, live-trading loop, orchestration, risk, news intelligence, adaptation, auth | 8000 |
| B | **mt5-bridge-service** | FastAPI + Wine + MT5 | Executes orders & streams prices/positions from a real MT5 terminal running under Wine in Linux | 5555 (7860 on HF) |
| C | **backtester_service** | FastAPI + multiprocessing | Continuously optimizes strategy parameters and ensemble weights offline; preloads OHLCV cache | 8001 (7860 on HF) |
| D | **frontend** | React 18 + Vite + Zustand + TanStack Query | Operator dashboard for monitoring & control | 5173 (dev) |

**Core idea (the "adaptive" part):**

- **11 trading strategies** each emit `(direction, confidence)` signals.
- An **Ensemble Voter** combines the selected strategies' signals using **backtest-optimized weights** to produce one final decision.
- A **news veto** check can block trades when high-confidence news opposes every signalling strategy.
- The **Backtester Service** runs an endless **Iterated Local Search** over each strategy's parameters and over ensemble weights, promoting better configurations into the live DB.
- An **Adaptation** routine nudges live strategy parameters based on recent closed-trade outcomes, with automatic rollback on performance drops.
- A **News Intelligence** subsystem fetches/scoring news with an LLM (Groq) and learns which sources actually predict trade outcomes.

Everything is wired through **MongoDB** (shared truth) and **HTTP/SSE** between services.

---

## 2. High-Level Topology

```
                         ┌─────────────────────────────┐
                         │   Frontend (React/Vite)      │
                         │   Zustand + TanStack Query   │
                         └───────┬─────────────┬────────┘
                       HTTPS /api │             │ WSS /ws?token=JWT
                                  ▼             ▼
        ┌──────────────────────────────────────────────────────────┐
        │              A. PYTHON BACKEND  (FastAPI)                  │
        │                                                            │
        │  Routers: auth, trades, strategies, backtest, risk,        │
        │   news, ensemble, adapt, params, bridge,                   │
        │   settings, system, webhook (X-API-Key), websocket         │
        │                                                            │
        │  Background loops:                                         │
        │   • live_trading (60s)     • position_stream (5s SSE/poll) │
        │   • position_reconciler (120s)                            │
        │   • news_fetch (30m) • news_learning (2h) • context (30m) │
        │   • backtester_keepalive (4m)                             │
        │                                                            │
        │  Services: orchestrator, risk_manager,                     │
        │   adaptation, news_intelligence, ohlcv, bridge_client,     │
        │   backtester_client, position_stream/reconciler            │
        └───┬───────────────┬────────────────────┬──────────────────┘
            │ X-Bridge-Secret│ Bearer (optional)  │  pymongo
            │ (+Bearer HF)   │                    │
            ▼                ▼                    ▼
 ┌──────────────────┐ ┌────────────────────┐ ┌─────────────────────────┐
 │ B. MT5 BRIDGE    │ │ C. BACKTESTER SVC  │ │   MongoDB Atlas         │
 │ FastAPI+Wine+MT5 │ │ FastAPI+multiproc  │ │   (shared by A & C)     │
 │                  │ │                    │ │                         │
 │ /order /close    │ │ ILS param search   │ │ trades, strategies,     │
 │ /modify /candles │ │ ensemble optimizer │ │ backtest_candidates,    │
 │ /positions       │ │ OHLCV preloader    │ │ ensemble_weights,       │
 │ /deals/{ticket}  │ │ pause/resume/      │ │ news_items, users,      │
 │ /stream/positions│ │   trigger API      │ │ ohlcv_cache, …          │
 │  (SSE)           │ │                    │ │                         │
 └────────┬─────────┘ └─────────┬──────────┘ └─────────────────────────┘
          │                     │ X-Bridge-Secret (OHLCV)
          │ RPyC 127.0.0.1:18812│
          ▼                     ▼
 ┌──────────────────┐    ┌──────────────────┐
 │ mt5linux (Wine)  │    │  MT5 Bridge (B)  │  ← backtester pulls candles
 │ terminal64.exe   │    └──────────────────┘
 │ Xvfb + Openbox   │
 └────────┬─────────┘
          ▼
   ┌──────────────┐
   │  Broker MT5  │ (e.g., Exness)
   └──────────────┘

 External APIs (from A): NewsAPI, Finnhub, Alpha Vantage, Twelve Data,
                         yfinance, OANDA, Groq (LLM sentiment)
```

**Key topology facts**

- The **backend (A)** is the only service the frontend talks to.
- **Both A and C share the same MongoDB** — this is the primary integration channel between the backend and the backtester (C writes candidates/weights; A reads & promotes them). C's HTTP API is only used for status/control/keepalive.
- **A and C both call B** over HTTP for candles (C for backtest data; A for live data/orders).
- The **MT5 terminal is a Windows GUI app running under Wine**, reached via an RPyC server (`mt5linux`) on `127.0.0.1:18812`.

---

## 3. Service A — Python Backend (Core API + Brain)

**Path:** `python-backend/` · **Framework:** FastAPI (async) · **DB:** MongoDB via `pymongo`

### 3.1 App lifecycle (`app/main.py`)

On startup (lifespan), in order:

1. Create MongoDB indexes (idempotent).
2. Seed admin user from `ADMIN_USERNAME` / `ADMIN_PASSWORD`.
3. Seed default `ParameterVersion` (DTC defaults) if none exists.
4. Seed `Strategy` documents from the Python `STRATEGY_REGISTRY` (`_ensure_strategies_exist()`); DTC set `is_active`/`is_live`.
5. Seed default `AppSettings` (news-veto, learning, risk, continuous-backtest config).
6. Run startup checks (`services/startup_checks.py`): DB, strategy registry, critical settings, bridge connectivity, backtester health, WeasyPrint availability. Result stored in `app.state.startup_checks`.
7. Launch background loops (below).

**Background loops:**

| Loop | Interval | Purpose |
|------|----------|---------|
| `_live_trading_loop` | 60s (`live_trading_interval_seconds`) | Main signal→trade cycle per active symbol |
| `start_position_stream` | 5s poll | Detect open/close/modify; broadcast WS; reconcile closes in real time |
| `_position_reconciliation_loop` | 120s | Fallback ghost-trade detection (deal history → close in DB) |
| `_news_fetch_loop` | 30 min | Fetch & store news |
| `_news_learning_loop` | 2 h | Retrospective news→trade correlation learning |
| `_global_context_loop` | 30 min | Update macro market context |
| `_backtester_keepalive_loop` | 4 min | Ping backtester `/ping` (prevents HF Space sleep) |
| `trailing_stop_loop` | opt-in | Trailing-stop / break-even manager |
| `start_job_worker` | opt-in | DB-backed job queue worker |

**CORS:** allow-all origins/methods/headers (see [§16](#16-issues-that-must-be-solved)).

### 3.2 Configuration (`app/config.py`)

Critical env vars: `MONGODB_URI`, `MT_BRIDGE_URL`, `MT_BRIDGE_SECRET`, `MT_BRIDGE_HF_TOKEN` (optional), `BACKTESTER_SERVICE_URL`, `BACKTESTER_HF_TOKEN` (optional), `ADMIN_USERNAME`/`ADMIN_PASSWORD`, `JWT_SECRET_KEY`, plus `ADAPTATION_*` tuning knobs. Many runtime values also live in the `app_settings` collection (hot-reconfigurable without redeploy).

### 3.3 Routers (REST surface)

| Prefix | Auth | Highlights |
|--------|------|-----------|
| `/auth` | public login, JWT after | login, `/me`, full user CRUD (admin) |
| `/health`, `/system` | mixed | `/health/db` (public ping), `/system/health` (startup checks + live metrics) |
| `/strategies` | JWT (+write) | list w/ stats, activate/deactivate/set-live, params + history + rollback, performance timeline, `simulate-signal`, backtest candidates, search settings/status/runs, ensemble config |
| `/backtest` | JWT (+write) | `run` (single/batch, async), results + trade-log + parameter-evolution + monthly/strategy breakdown + PDF, batch pair-analysis + ensemble-simulation, compare, cache-status, trigger-cache-preload |
| `/risk` | JWT (+write) | settings, status, halt/resume, lot-size-preview |
| `/adapt` | JWT (+write) | trigger adaptation, `/log` |
| `/params` | JWT (+write) | learning settings + param history |
| `/ensemble` | JWT (+write) | decisions, config, weights, voter-snapshot, weights reset/suspend |
| `/news` | JWT (+write) | items, bias/{symbol}, context, fetch, learn, learning-stats, trade-correlation, source-credibility, trade-impact-timeline |
| `/shadow-signals` | JWT | hypothetical signals from non-live strategies |
| `/bridge` | no JWT | `/account`, `/positions`, `/status` (circuit-breaker state) |
| `/settings` | JWT (+admin/write) | bulk + single AppSettings get/set |
| `/webhook` | **X-API-Key** | `/signal` (TradingView ingestion, rate-limited), `/close` |
| `/ws` | JWT via `?token=` | live event stream |

### 3.4 Services (the engine room)

- **`orchestrator.py`** — `process_signal()`: enrich market data with indicators → gather signals from all active strategies (incl. MTF build for Alchemist) → news veto → ensemble vote → choose level-strategy → risk check & lot sizing → place order via bridge → persist `Trade` + `EnsembleDecision` → `news_intelligence.learn_from_trade`. All bridge calls run via `_to_thread()` (non-blocking). Per-symbol `asyncio.Lock` prevents TOCTOU double-fills. WebSocket broadcasts use `_fire_and_forget()` for GC safety.
- **`risk_manager.py`** — `check_and_compute_lot_size()`; FIXED vs DYNAMIC sizing; blocks on `trading_halt`, `max_open_trades`, daily-loss limit.
- **`adaptation.py`** — `run_adaptation()`: confidence-gated, bounded "tiny step" parameter nudging with rollback.
- **`news_intelligence.py`** — multi-source fetch, Groq LLM sentiment (keyword fallback), bias per symbol, source-credibility learning, global macro context.
- **`ohlcv.py`** — `fetch_ohlcv_with_fallback()`: Bridge → cache → yfinance → Alpha Vantage → Finnhub → Twelve Data → OANDA.
- **`bridge_client.py`** — HTTP client to B with **circuit breaker** (open after 5 failures, 90s cooldown), 3 timeout-tiered httpx clients, last-known cache for account/positions.
- **`backtester_client.py`** — thin REST client to C (`ping/health/status/pause/resume/trigger`); raises `BacktesterUnavailable` if URL unset.
- **`position_stream.py`** — 5s bridge poll, diff vs previous snapshot, broadcast WS events, reconcile closes via deal history.
- **`position_reconciler.py`** — 120s fallback; primary pass by `mt5_ticket`, secondary fuzzy match by symbol/direction/time for tickets not yet recorded.

---

## 4. Service B — MT5 Bridge Service

**Path:** `mt5-bridge-service/` (+ base image `mt5-bridge-base/`) · **Auth:** `X-Bridge-Secret` header on all but `/`, `/health`.

### 4.1 Why it's complex

MetaTrader 5 is a **Windows GUI application**. To run it on Linux/containers it is launched under **Wine** with a virtual display, and a persistent **`mt5linux` RPyC server** (running inside the same Wine session) exposes the `MetaTrader5` Python API over TCP `127.0.0.1:18812`. The FastAPI process (Linux side) talks to that RPyC server.

**Runtime processes (supervised in one container):**
- `Xvfb :99` (virtual X display) + `Openbox` (WM, so `xdotool` can dismiss dialogs)
- `terminal64.exe` under Wine (the actual MT5 terminal)
- `wine python.exe -m mt5linux` (RPyC server on 18812)
- `uvicorn` (FastAPI bridge on `0.0.0.0:$PORT`, starts immediately while MT5 boots in background)

### 4.2 Endpoints (`app/main.py`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/`, `/health`, `/ready` | liveness / IPC-readiness (`mt5_ipc.ready` sentinel + live `account()` check) |
| GET | `/account` | balance/equity/margin |
| GET | `/positions`, `/positions/current` | open positions snapshot |
| GET | `/stream/positions` | **SSE** diff stream (5s poll): `connected`/`snapshot`/`diff`/`error` |
| POST | `/order` | market BUY/SELL (HTTP 429 if broker retcode 10027 after retries) |
| POST | `/order/limit` | pending BUY/SELL_LIMIT / BUY/SELL_STOP |
| POST | `/close` | full/partial close |
| POST | `/modify` | change SL/TP (open positions only) |
| GET | `/candles` | OHLCV (`1m…1w`), from/to ISO |
| GET | `/deals/{ticket}` | fills for a closed position (for PnL reconciliation) |
| POST | `/reset` | hard adapter reconnect |
| GET | `/debug/*` | mt5 state, processes, ipc-test, pipes, screenshot |

### 4.3 Adapter (`app/mt5_adapter.py`)

- Dual backend: native `MetaTrader5` on Windows (never in Docker) → **`mt5linux` RPyC** in containers.
- `ensure_connection()`: exponential backoff `[5,10,20,30,60,120,180,300]s`; waits for `mt5_ipc.ready`; tries bare `initialize()` then credentialed; classifies errors (`ipc_timeout`, `terminal_not_found`, `auth_failure`, `build_mismatch`, …).
- **Order rate-limit handling:** process-wide `threading.Lock` serializes `order_send()`; global 3s cooldown after retcode 10027; **max 3 retries** (5/10/20s, ~35s worst case — deliberately under the backend's 60s order-client read timeout so the circuit breaker doesn't trip mid-recovery).
- `copy_rates_range` and tick resolution retry (terminal data feed lags after restart); broker-suffix symbol variants (e.g., `XAUUSDm`).

### 4.4 Deployment fragility (Wine/MT5)

- **Binaries locked immutable** (`chattr +i`) + update domains blocked (hosts + iptables) so MT5 **LiveUpdate cannot self-overwrite** the terminal and break the IPC build match.
- **Build-mismatch self-heal:** if the `MetaTrader5` pip build ≠ terminal build, IPC returns `-10005` forever; service tries to download a matching portable ZIP (`MT5_PORTABLE_ZIP_URL` / GitHub release) and re-lock. If no ZIP is available, the service is **dead until rebuilt** — see [§16](#16-issues-that-must-be-solved).
- `servers.dat` (broker endpoints, e.g. Exness) is baked in (base64+gzip in `config/servers.dat.b64`) because the generic installer's file can't resolve the broker.
- A **base image** (`ghcr.io/loriloha/mt5-bridge-base`) pre-bakes Wine + Python + MetaTrader5 + terminal to cut cold start from ~25–35 min to ~2 min.

---

## 5. Service C — Backtester Service

**Path:** `backtester_service/trading-backtester/` · **Auth:** **none** (CORS allow-all) · **DB:** same MongoDB as Service A.

### 5.1 Layout (two packages in one image)

- `app/` — a **duplicated copy** of the 11 strategies + registry + a backtesting engine + bridge/ohlcv clients (so worker subprocesses can import strategy classes directly).
- `backtester_service/` — the FastAPI app + optimization engines + DB/CRUD.

### 5.2 Endpoints (`backtester_service/main.py`)

`GET /ping`, `GET /health`, `GET /status`, `GET /status/{strategy}`, `POST /pause|resume|trigger/{strategy}`, `GET /ensemble-status`, `GET /bridge/test`, `POST /preload-ohlcv-cache`, `GET /cache/status`.

### 5.3 Optimization engines

- **`param_search.py` (`ParamSearchEngine`)** — Iterated Local Search with coordinate-ascent: nudge one parameter per iteration with momentum; two-stage escape (partial perturbation after 1 stagnant cycle, full restart after 2); enforces EMA ordering for DTC/Multi_EMA_Scalper; **persists engine state** to `search_engine_state` so optimization survives redeploys.
- **`continuous_backtest.py`** — per-strategy loop: generate candidate → evaluate on **3 horizons simultaneously** (`~9 / ~18 / ~48 months`, each 80/20 train/val) via `asyncio.gather` + `ProcessPoolExecutor` → blended `final_score = w_short·s + w_med·m + w_wide·w` → promote if beats best → write `backtest_candidates` (+ audit `backtester_runs`). All thresholds/weights are per-strategy `app_settings` keys.
- **`ensemble_backtest.py`** — same ILS but optimizes the **11 strategy weights + voting threshold (0.40–0.80)** jointly; promotes to `ensemble_weights` (live trading reads these immediately).

### 5.4 How C reaches the backend's world

C **writes to MongoDB** (candidates, promoted params on `strategies`, `ensemble_weights`) — that's how results reach live trading. C **reads OHLCV from the MT5 bridge** (`bridge_client.py`, sync HTTP, `X-Bridge-Secret`) and caches to `ohlcv_cache`. The backend's only HTTP calls to C are status/keepalive.

---

## 6. Service D — Frontend (React/Vite)

**Path:** `frontend/` · **Stack:** React 18, Vite, TypeScript, Zustand, TanStack Query, React Router, Recharts, Tailwind, lucide-react.

### 6.1 API client (`src/api/index.ts`, `api/types.ts`)

- Base URL `import.meta.env.VITE_API_URL ?? '/api'`; JWT in `localStorage['auth_token']`; `Authorization: Bearer` on every call; **401 → clear token → redirect `/login`**.
- Domain-organized wrappers mirror every backend router (auth, trades, strategies, ensemble, backtest, risk, news, adaptation, params, bridge, shadow-signals, system, settings, analytics).
- Vite dev proxy: `/api` → `http://localhost:8000`, `/ws` (websocket) → `ws://localhost:8000`.

### 6.2 Pages (`src/pages/*.tsx`)

`Dashboard` (health/halt/uptime), `Backtesting`, `LiveTrades` (+analytics), `NewsCenter`, `RiskControl`, `StrategyManager`, `SystemSettings`, `EnsembleDashboard` (voter snapshot, weights), `Adaptation`, `LoginPage`, `UsersPage` (admin).

### 6.3 Realtime & state

- **`hooks/useWebSocket.ts`** — connects `wss://{apiBase}/ws?token=<JWT>`; supports `event`/`type` keys + `"*"` wildcard; 30s ping; auto-reconnect; sets `useAppStore.wsConnected`.
- **`store.ts`** (Zustand) — auth (`login/logout/canWrite/isAdmin`), UI (sidebar/toasts), `wsConnected`, `haltActive`, `openTradesCount`; persisted to localStorage.

---

## 7. The Data Layer (MongoDB)

Single shared database (`MONGODB_URI`, pool 50, 5s server-selection timeout). Auto-increment integer `_id`s via a `counters` collection.

| Collection | Written by | Read by | Purpose |
|------------|-----------|---------|---------|
| `trades` | A (orchestrator, reconciler, webhook) | A, C | Live/paper trade log; `mt5_ticket` links to bridge |
| `parameter_versions` | A (adaptation), C (promotion) | A | Versioned strategy params + trigger/reason |
| `adaptation_logs` | A | A | Adaptation audit trail |
| `app_settings` | A, C | A, C | Hot config (learning, risk, backtest thresholds, API keys) |
| `strategies` | A (seed), C (promotion meta) | A, C | Strategy metadata: `is_active`, `is_live`, `params_json` |
| `shadow_signals` | A | A, D | Hypothetical non-live signals |
| `backtest_results` | A, C | A, D | Single backtest output (metrics, equity, trade log, breakdowns) |
| `backtest_candidates` | C | A, D | Param candidates + composite score; TTL 48h for unqualified |
| `backtest_batches` | A | A, D | Batch orchestration + cross-analysis |
| `strategy_pair_analyses` | A | A, D | Cross-strategy correlation/synergy |
| `ensemble_decisions` | A | A, D | Per-bar ensemble vote records |
| `ensemble_weights` | C | A | **Active live ensemble weights** (`is_active`) |
| `search_engine_state` | C | C | ILS engine persistence across redeploys |
| `backtester_runs` / `ensemble_backtest_runs` | C | A, D | Optimization audit (TTL 1d / 7d) |
| `ohlcv_cache` | A, C | A, C | Cached candles by (symbol, interval, range, source) |
| `news_items` | A | A, D | News + AI sentiment + learned impact |
| `users` | A | A | Accounts + roles (admin/viewer, `full_access`) |
| `counters` | A, C | A, C | Auto-increment emulation |

---

## 8. Strategy Framework, Ensemble, Picker & Adaptation

### 8.1 Base interface (`app/strategy/base.py`)

Every strategy implements:
- `signal(market_data) -> (direction|None, confidence∈[0,1])`
- `compute_levels(direction, price, params) -> {sl, tp1..tp4}`
- optional `adapt(trades, settings)` (only **Alchemist** truly implements it)
- `PARAM_BOUNDS` (search space for the optimizer) + `default_params()`.

### 8.2 The 11 strategies

| Strategy | Family | Core indicators / logic | Adaptive | MTF |
|----------|--------|------------------------|----------|-----|
| **DTC** (Dynamic Trend Cascade) | Trend | 6-EMA cascade; fire on cascade flip | no-op | No |
| **Multi_EMA_Scalper** | Trend | DTC with faster EMAs/tighter TPs | no-op | No |
| **HTF_Structure** | Structure | HTF EMA + swing HH/HL or LH/LL | no-op | No |
| **ADX_Regime_Filter** | Regime | ADX/+DI/−DI; signal only when trending | no-op | No |
| **MACD_Momentum** | Momentum | MACD/signal cross + histogram gate | no-op | No |
| **OBV_Momentum** | Momentum | OBV EMA cross + price-EMA confirm | no-op | No |
| **RSI_Reversal** | Mean-rev | RSI OS/OB crosses | no-op | No |
| **StochRSI_Cross** | Mean-rev | Stoch-RSI %K/%D cross in extremes | no-op | No |
| **VWAP_Reversion** | Mean-rev | Price ± % deviation from VWAP (crossover) | no-op | No |
| **Bollinger_Breakout** | Breakout | BB band break + ATR stop | no-op | No |
| **Alchemist** | Confluence | MSNR zones + CRT sweeps + ICT structure + Fib + optional SMT/killzone; graded confidence | **Yes** | **Yes** |

### 8.3 Ensemble Voter (`strategy/ensemble/voter.py`)

- Normalizes weights, **zeros suspended strategies**, enforces an **Alchemist floor of 0.15**.
- For each signal: `contribution = weight × confidence`, accumulated into `buy_score`/`sell_score`.
- Fires the higher side **only if it exceeds `threshold` (default 0.60)**; otherwise no trade.
- `get_level_strategy()` picks who computes SL/TP (Alchemist if it agreed, else highest-weighted agreeing strategy).

### 8.4 Weight Manager (`strategy/ensemble/weight_manager.py`)

`raw_weight = w_wr·win_rate_norm + w_pf·min(pf/3,1) + w_bt·backtest_score` (factor weights default 0.4/0.3/0.3, configurable). **Suspends** strategies with win-rate < 40% over last 30 trades (min 10 trades). Persists active weights + suspended list.

### 8.5 Adaptation (`services/adaptation.py`)

Per strategy, gated by cooldown + min closed trades. `confidence = |win_rate−0.5| + max(0,pf−1)·0.05`. If above threshold, apply **bounded tiny steps** to `stop_loss_pct`, `tp*_multiplier`, EMA periods (clamped by `PARAM_BOUNDS` and `max_change_pct`). **Auto-rollback** if profit factor drops ≥15%. Alchemist has its own richer `adapt()`.

> **Two-tier weighting (important):** the **news veto** decides whether to block the trade outright; the **Ensemble Voter** decides *how strongly* each strategy's vote counts (backtest-optimized weights tilted by live performance). They are distinct systems.

---

## 9. End-to-End Integration Flows

### 9.1 Live trade (every 60s)

```
live_trading_loop → for each symbol:
  ohlcv (bridge→fallbacks) → enrich indicators
  orchestrator.process_signal():
    gather signals from all active strategies (Alchemist gets MTF stack)
    news_veto_check() → may block
    EnsembleVoter.vote() (weights from ensemble_weights) → direction/confidence
    level_strategy.compute_levels() → sl/tp1..4
    risk_manager.check_and_compute_lot_size()
    bridge_client.place_order()  [skipped if SIMULATION_MODE]
    persist Trade + EnsembleDecision
    news_intelligence.learn_from_trade()
  broadcast WS event
```

### 9.2 Position close & reconciliation (real-time + fallback)

```
position_stream (5s): poll bridge /positions → diff
  on close: bridge /deals/{ticket} → compute PnL → crud.close_trade
            → score_feedback.run_trade_close_hooks → WS position_closed
position_reconciler (120s): backstop for missed closes
  primary: match by mt5_ticket; secondary: fuzzy match (symbol/dir/time) to backfill mt5_ticket
```

### 9.3 Backtest → live promotion (the optimization loop)

```
Backtester C (continuous):
  ParamSearchEngine → candidate params
  evaluate on 3 horizons (ProcessPool) → blended composite_score
  if better: write backtest_candidates + promote params to strategies/parameter_versions
  ensemble_backtest → promote ensemble_weights (is_active=true)

Backend A:
  WeightManager / orchestrator read promoted params + ensemble_weights from Mongo
  → applied on next live cycle (no redeploy needed)
```

### 9.4 News intelligence learning (every 2h)

```
news_fetch (30m): NewsAPI/Finnhub/AlphaVantage/RSS → Groq sentiment → news_items
news_learning (2h): for recent closed trades, set market_impact_actual from PnL
  → recompute per-source credibility → adjust bias weighting
orchestrator consumes get_news_bias() for veto/adjustment
```

### 9.5 External signal ingestion (webhook)

```
TradingView → POST /webhook/signal (X-API-Key, rate-limited 60/min global, 10/min/symbol)
  → log Trade, compute SL/TP from ATR
POST /webhook/close → close_trade → score feedback
```

---

## 10. Cross-Service Integration Matrix

| Caller | Callee | Transport | Auth | What |
|--------|--------|-----------|------|------|
| Frontend D | Backend A | HTTPS REST | JWT Bearer | all UI data/control |
| Frontend D | Backend A | WSS `/ws` | JWT in query | live events |
| Backend A | Bridge B | HTTPS REST | `X-Bridge-Secret` (+ `Bearer` HF token) | account, positions, order, close, modify, candles, deals |
| Backend A | Bridge B | SSE/poll | `X-Bridge-Secret` | position stream (currently polled via `/positions`) |
| Backend A | Backtester C | HTTPS REST | optional `Bearer` HF token | ping/health/status/pause/resume/trigger |
| Backend A | MongoDB | TCP (pymongo) | URI creds | everything |
| Backtester C | Bridge B | HTTPS REST | `X-Bridge-Secret` (+ `Bearer` HF) | candles (OHLCV) |
| Backtester C | MongoDB | TCP (pymongo) | URI creds | candidates, weights, params, cache |
| Bridge B | mt5linux | RPyC TCP 18812 | localhost | MT5 API calls |
| Bridge B | Backend A | HTTPS | optional `Bearer` | peer keepalive (anti-sleep) |
| Backend A | External | HTTPS | per-provider keys | news (NewsAPI/Finnhub/AV), OHLCV (yfinance/AV/TwelveData/OANDA), Groq LLM |

---

## 11. Trading Frequency Tuning

How to make the bot **open trades more often** (loosen) or **less often** (tighten). All keys live in the `app_settings` collection and are read **live at runtime** — no restart needed. Change via the Settings page or `PUT /settings`.

### Quick reference

| Want to trade… | Lower these | Raise these |
|----------------|-------------|-------------|
| **MORE often** | `ensemble_voting_threshold`, `duplicate_min_confidence`, `duplicate_min_price_distance_atr`, `news_signal_bias_threshold`, `news_signal_confidence_threshold` | `max_open_trades`, `symbol_exposure_limit`, `news_veto_threshold` |
| **LESS often** | `max_open_trades`, `symbol_exposure_limit` | `ensemble_voting_threshold`, `duplicate_min_confidence`, `duplicate_min_strategy_count`, `news_block_threshold` |

**The single biggest lever is `ensemble_voting_threshold`.** Start there.

### Signal pipeline gates (in order)

1. **News veto** → blocks when high-confidence news opposes every signalling strategy
2. **Ensemble vote** → weighted BUY/SELL score must beat `ensemble_voting_threshold`
3. **News-driven fallback** → synthesize from news bias alone when no strategy fired
4. **Risk checks** → halt flag, max open trades, exposure, daily-loss, drawdown, spread
5. **Duplicate guard** → same-direction position already open requires extra conviction
6. **Reversal handling** → opposite position → full/partial close or tighten stops

### Key settings by gate

#### Ensemble voting threshold (master switch)

| Key | Default | Range | Effect |
|-----|---------|-------|--------|
| `ensemble_voting_threshold` | `0.60` | 0.40–0.80 (clamped) | Combined weighted confidence must exceed this. **Lower → more trades.** |

#### Live score feedback

| Key | Default | Effect |
|-----|---------|--------|
| `score_feedback_enabled` | `true` | Master on/off for live-score tilting of vote weights |
| `score_feedback_weight_gain` | `0.25` | How hard `live_score` tilts the weight (gain=0.25, score=+2 ≈ 1.5× weight) |
| `score_feedback_weight_floor` | `0.1` | Min weight multiplier (dampened, never zeroed) |
| `score_feedback_alpha` | `0.2` | EWMA smoothing — higher = reacts faster |

#### News gates

| Key | Default | Effect |
|-----|---------|--------|
| `news_block_threshold` | `0.70` | Block all trades when `\|sentiment\|` exceeds this |
| `news_veto_threshold` | `0.85` | Min news confidence before veto fires |
| `news_veto_bias_threshold` | `0.50` | Min `\|bias\|` for veto consideration |
| `news_signal_trading_enabled` | `true` | Allow news-only fallback trades |
| `news_signal_bias_threshold` | `0.30` | Min `\|bias\|` for news-only trade |
| `news_signal_confidence_threshold` | `0.50` | Min confidence for news-only trade |

#### Risk gates

| Key | Default | Effect |
|-----|---------|--------|
| `trading_halt` | `false` | When true, no new entries |
| `max_open_trades` | `5` | Hard cap on simultaneous open trades |
| `symbol_exposure_limit` | `1.0` | Max total lots per symbol |
| `max_daily_loss_pct` | `5.0` | Daily loss cap (% of balance) |
| `max_drawdown_pct` | `20.0` | Triggers `trading_halt` |
| `max_spread_pips` | `0` (OFF) | Skip entries when spread wider |

#### Duplicate guard

| Key | Default | Effect |
|-----|---------|--------|
| `duplicate_min_confidence` | `0.75` | Base confidence for additional same-direction position |
| `duplicate_confidence_escalation` | `0.10` | Added per stacked position (capped 0.95) |
| `duplicate_min_price_distance_atr` | `1.0` | Min distance from existing entries (× ATR) |
| `duplicate_min_strategy_count` | `2` | Strategies that must agree for a duplicate |

#### Reversal handling

| Key | Default | Effect |
|-----|---------|--------|
| `reversal_full_close_confidence` | `0.80` | Close all opposite + open new |
| `reversal_partial_close_confidence` | `0.65` | Partial close opposite + smaller new |
| `reversal_partial_close_pct` | `0.50` | Fraction closed on partial reversal |

#### Cadence & universe

| Key | Default | Effect |
|-----|---------|--------|
| `live_trading_interval_seconds` | `60` | How often signals are evaluated |
| `live_trading_symbols` | `XAUUSD` | CSV list of symbols to trade |

### Recommended presets

**Aggressive** (more trades, higher risk):
```
ensemble_voting_threshold=0.45  news_block_threshold=0.85  news_veto_threshold=0.95
news_signal_bias_threshold=0.20  news_signal_confidence_threshold=0.40
duplicate_min_confidence=0.60  duplicate_min_price_distance_atr=0.5
duplicate_min_strategy_count=1  max_open_trades=10  symbol_exposure_limit=3.0
live_trading_interval_seconds=30
```

**Conservative** (fewer, high-conviction trades):
```
ensemble_voting_threshold=0.75  news_block_threshold=0.55  news_veto_threshold=0.70
news_signal_trading_enabled=false  duplicate_min_confidence=0.90
duplicate_min_strategy_count=3  max_open_trades=2  symbol_exposure_limit=1.0
max_spread_pips=30  live_trading_interval_seconds=300
```

### Troubleshooting

- **Bot won't trade:** Check `trading_halt`, `max_open_trades` cap, `max_daily_loss_pct`/`max_drawdown_pct`, bridge status, `ensemble_voting_threshold`.
- **Too many trades:** Raise `ensemble_voting_threshold`, tighten duplicate guard, lower `max_open_trades`.
- **One dial at a time** — watch 20–30 trades before judging.

---

## 12. MT5 Bridge Deployment & IPC

### Architecture: Pre-Baked Base Image

To avoid 30-minute cold starts, a custom base image (`ghcr.io/loriloha/mt5-bridge-base:latest`) is built via GitHub Actions, containing:

- Wine 11.6 devel + Xvfb
- Windows Python 3.9 (installed via Wine)
- `MetaTrader5` + `mt5linux` Python packages (Wine-side)
- MT5 terminal64.exe pre-installed
- Saved MetaQuotes demo session (terminal auto-connects on startup)

The bridge service Dockerfile builds FROM this base and only adds the FastAPI app layer. Cold starts are ~2 minutes instead of 30+.

### Startup Sequence

```
Container start
    │
    ├─ Xvfb :99              (virtual display for Wine GUI apps)
    ├─ uvicorn               (FastAPI on port 7860, starts immediately)
    ├─ bootstrap-mt5.sh      (background — skips all installs on pre-baked image)
    │       └─ writes bootstrap.ready sentinel
    ├─ mt5linux-launcher     (waits for bootstrap.ready)
    │       └─ wine python.exe -m mt5linux --host 127.0.0.1 --port 18812
    │               → RPyC server listening on 18812
    └─ mt5-terminal          (if MT5_LAUNCH_TERMINAL=true)
            └─ wine terminal64.exe
                    → loads saved session → connects to broker
                    → updates MQL5 files (~8 min cold start)
                    → IPC pipe active
```

### IPC Timeout (-10005) Debugging

The `MetaTrader5` Python module communicates with `terminal64.exe` via Windows named pipes. Error `-10005` means the pipe was not found within the 60s window. Root cause is typically:

1. **Path mismatch** — `initialize(path="C:\\...")` vs terminal registered under a different path. Fix: call bare `initialize()` first (finds any running terminal).
2. **Build mismatch** — pip package build ≠ terminal build. Fix: pin builds in base image.
3. **Wine prefix ownership** — `chown root:root` at build time.

### Environment Variables (HF Spaces)

| Variable | Secret? | Notes |
|----------|---------|-------|
| `PORT` | No | `7860` (HF required) |
| `MT5_LAUNCH_TERMINAL` | No | `true` to launch terminal64.exe |
| `MT_LOGIN` | **Yes** | Broker account number |
| `MT_PASSWORD` | **Yes** | Broker password |
| `MT_SERVER` | **Yes** | Broker server name |
| `MT_BRIDGE_SECRET` | **Yes** | Shared API auth secret |
| `WINEPREFIX` | No | `/opt/wineprefix` |
| `DISPLAY` | No | `:99` |

### Diagnostic Endpoints (require `X-Bridge-Secret`)

| Endpoint | Shows |
|----------|-------|
| `GET /health` | Basic liveness (no auth) |
| `GET /debug/mt5` | Port status, adapter state, last error, log tails |
| `GET /debug/processes` | `ps aux` filtered for wine/terminal/python |
| `GET /debug/screenshot` | Base64 PNG of the Xvfb display |
| `POST /reset` | Force adapter reconnect |

### Keep-Alive

HF Spaces free tier sleeps after ~15 min inactivity. Use **UptimeRobot** (every 5 min, HTTP, `GET /health`) to prevent sleep. The backend also runs a peer-keepalive loop.

---

## 13. Release & Build Process

### Base Image Build (GitHub Actions)

The base image is built via two workflows in `.github/workflows/`:

- **`build-mt5-base.yml`** — Triggered on push to `main` when files under `mt5-bridge-base/` change. Builds `ghcr.io/loriloha/mt5-bridge-base:latest` with Wine + Python + MT5 terminal. Takes ~25 min.
- **`build-mt5-base-dispatch.yml`** — Manual dispatch variant for on-demand rebuilds.
- **`package-mt5-windows.yml`** — Packages a portable MT5 ZIP for build-mismatch self-heal.

### Deploy to HF Spaces (subtree split)

Both the backend and bridge are deployed via `git subtree split` to their respective HF Spaces:

**Python Backend:**
```powershell
git add python-backend
git commit -m "feat: <description>"
git subtree split --prefix=python-backend -b hf-backend-deploy
git push --force "https://loriloha:<HF_TOKEN>@huggingface.co/spaces/loriloha/mt5-backend-service" hf-backend-deploy:main
git branch -D hf-backend-deploy
```

**MT5 Bridge:**
```powershell
git add mt5-bridge-service/app
git commit -m "fix: <description>"
git subtree split --prefix=mt5-bridge-service -b hf-bridge-deploy
git push --force "https://loriloha:<HF_TOKEN>@huggingface.co/spaces/loriloha/mt5-bridge-service" hf-bridge-deploy:main
git branch -D hf-bridge-deploy
```

### Former PS1 Release Scripts (archived)

The `scripts/` directory previously contained PowerShell scripts for:

- **`create-mt5-release*.ps1`** — Packaged portable MT5 terminal ZIPs for GitHub Releases (used for build-mismatch self-heal)
- **`package-mt5-portable.ps1` / `package-and-build-mt5.ps1`** — Built and packaged the MT5 portable distribution
- **`check-mt5-pypi-match.ps1`** — Verified MetaTrader5 pip package build matches terminal build
- **`upload-mt5-asset.ps1`** — Uploaded packaged assets to GitHub Releases
- **`init-mt5-portable.ps1`** — Initialized a portable MT5 installation
- **`list-ghcr-tags.ps1`** — Listed GHCR container image tags
- **`debug-release*.ps1`** — Debugging helpers for release packaging

These have been consolidated into the GitHub Actions workflows and this documentation. The `telegram-relay-worker.js` Cloudflare Worker script for Telegram notifications remains in `scripts/`.

---

## 14. Build History Notes

The system was developed through 8 sequential phases, each building on the previous:

| Phase | Focus | Key deliverables |
|-------|-------|-------------------|
| 0 | Bug fixes | Fixed `signal()` return types, VWAP crossover, param persistence, drawdown scoring |
| 1 | New strategies | Added ADX_Regime_Filter, OBV_Momentum, StochRSI_Cross, HTF_Structure (4→11 strategies) |
| 2 | Ensemble Voter | Weighted voting system, EnsembleVoter class, level-strategy selection |
| 3 | Ensemble Backtester | Joint weight+threshold optimization via ILS, multi-horizon evaluation |
| 4 | Live Integration | Orchestrator wiring, ensemble weights from MongoDB, news veto, score feedback |
| 5 | Dashboard | EnsembleDashboard page, voter snapshot visualization, weight charts |
| 6 | (skipped) | — |
| 7 | System Hardening | Blocking I/O fix (`_to_thread`), TOCTOU race fix (per-symbol locks), GC-safe tasks, doc consolidation |

The original build prompts and AI implementation chain documents have been retired.

---

## 15. Deployment Topology & Configuration

- **Backend A** — HF Spaces Docker (`python-backend/`), port 8000. Also configurable via `render.yaml` for Render.
- **Bridge B** — HF Spaces Docker (`mt5-bridge-service/`), port 7860. Also configurable via `render.bridge.yaml`. Depends on `mt5-bridge-base` image built by GitHub Actions.
- **Backtester C** — HF Spaces Docker (`backtester_service/trading-backtester/`), port 7860. Backend pings every 4 min to keep awake.
- **Frontend D** — Vite static build; deployed separately.
- **MongoDB** — Atlas (shared by A & C).
- **Base image** — `ghcr.io/loriloha/mt5-bridge-base` built via `.github/workflows/build-mt5-base*.yml`.

**Key env vars** — A: `MONGODB_URI`, `MT_BRIDGE_URL`, `MT_BRIDGE_SECRET`, `MT_BRIDGE_HF_TOKEN`, `BACKTESTER_SERVICE_URL`, `BACKTESTER_HF_TOKEN`, `JWT_SECRET_KEY`, `ADMIN_*`, `ADAPTATION_*`, `SIMULATION_MODE`. B: `MT_LOGIN/PASSWORD/SERVER`, `MT_BRIDGE_SECRET`, `MT5_INSTALLER_URL`, `MT_TERMINAL_EXE`, `MT5LINUX_*`, `MT_FALLBACK_MODE`, `PEER_HEALTHCHECK_*`. C: `MONGODB_URI`, `MT_BRIDGE_*`, `MAX_WORKERS`, `STRATEGIES_TO_RUN`.

See `PRODUCTION_DEPLOY.md` for the full step-by-step deployment guide.

---

## 16. Issues That Must Be Solved

Ordered by severity.

### 🔴 Critical

1. ~~**Blocking I/O in async orchestrator.**~~ **✅ DONE** — All bridge calls routed through `_to_thread()`.

2. ~~**TOCTOU race on position checks.**~~ **✅ DONE** — Per-symbol `asyncio.Lock` serializes the read-check-place sequence.

3. ~~**Fire-and-forget `create_task` GC risk.**~~ **✅ DONE** — Module-level `_background_tasks` set + `_fire_and_forget()` helper.

4. **DB config mismatch (PostgreSQL vs MongoDB) — VERIFIED.** Code uses MongoDB, but `render.yaml` injects `DATABASE_URL` (never read) and `.env.example` shows `postgresql://…`. **Fix:** standardize on `MONGODB_URI`, set it in `render.yaml`, delete `DATABASE_URL`/`WEBHOOK_SECRET`-as-Postgres and all `postgresql://` references.

5. **Backtester Service has no authentication** and CORS allow-all. Anyone who can reach the URL can `POST /trigger`. **Fix:** require `X-Bridge-Secret`/JWT; restrict CORS.

6. **Secrets weak/empty by default.** `JWT_SECRET_KEY` and `ADMIN_PASSWORD` default to empty. **Fix:** fail fast at startup if either is empty in non-simulation mode.

7. **MT5 build-mismatch single point of failure.** If the `MetaTrader5` pip build ≠ terminal build and no `MT5_PORTABLE_ZIP_URL` is available, all live trading stops silently. **Fix:** pin builds in base image; always publish a matching portable ZIP; alert on `build_mismatch`.

8. **No automated kill-switch on bridge outage during open positions.** When circuit breaker is OPEN, the backend serves *cached* positions/account. **Fix:** surface circuit-breaker state prominently, auto-halt new entries, reconcile on recovery.

### 🟠 High

9. **Strategy code duplicated** between `python-backend/app/strategy` and `backtester_service/.../app/strategy`. Copies are byte-identical today but nothing enforces sync. **Fix:** shared package or CI diff check.

10. **CORS allow-all on the backend** with JWT-in-query for WebSocket. **Fix:** restrict CORS; move WS auth to subprotocol.

11. **Free-tier sleep fragility.** Three services on free plans depend on keepalive loops. **Fix:** paid always-on for A and B, or external uptime pings + alerting.

12. **Adaptation vs. Backtester can fight.** Both write `parameter_versions`/`strategies` params. **Fix:** define ownership (backtester = baseline, adaptation = fine-tune within band).

### 🟡 Medium

13. **Position stream is polling, not true streaming.** Up to 5s blind windows. **Fix:** consume the bridge SSE stream (opt-in via `use_sse_position_stream`).

14. **No terminal-crash auto-restart** in the bridge. **Fix:** supervise terminal PID.

15. **Debug endpoints expose internals** behind only the shared secret. **Fix:** gate behind `DEBUG_ENABLED` flag.

---

## 17. Missing / Recommended Additions

**Reliability & ops**
- **Centralized structured logging + alerting** (trade failures, circuit-breaker opens, build mismatch, adaptation rollbacks).
- **Health aggregation dashboard tile** showing each service's reachability, last successful order, last reconciliation, last promotion.
- **Automated tests for the money path** — orchestrator decision → risk sizing → order payload.
- **Idempotency keys on order placement** to prevent duplicate fills on retries/timeouts.

**Risk management depth**
- **Per-symbol & portfolio exposure caps**, max correlated-position limits, and a **max daily drawdown auto-halt** that actually flips `trading_halt`.
- **Slippage / spread guards** before entry.
- **Trailing-stop / break-even management** post-entry (manager exists, opt-in via `trailing_stop_enabled`).

**Data & correctness**
- **Single source of truth for strategy code** (resolve duplication, item 9).
- **OHLCV cache invalidation / freshness policy** beyond TTL.
- **Walk-forward / out-of-sample guardrails surfaced in the UI**.

**Security**
- Bridge & backtester **auth + CORS hardening** (items 5, 10, 15).
- **Secret rotation** for `MT_BRIDGE_SECRET` / MT5 credentials.
- **Rate-limiting** on the bridge `/order` endpoint.

**Product/UX**
- **Frontend deployment config** committed to the repo.
- **Mobile/alerting hooks** (Telegram/email) for halts, large losses, and promotions.
- **A "dry-run/paper vs live" indicator** everywhere, driven by `SIMULATION_MODE`.

---

*Consolidated from: SYSTEM_ARCHITECTURE.md, DEPLOYMENT.md, TRADING_FREQUENCY_TUNING_GUIDE.md, ai_implementation_chain.md, prompt/ build plans, and scripts/ release documentation. Source of truth: the codebase in `python-backend/`, `mt5-bridge-service/`, `mt5-bridge-base/`, `backtester_service/`, and `frontend/`.*