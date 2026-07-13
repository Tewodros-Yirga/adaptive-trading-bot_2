
# API Reference — Phase 2 Endpoints

All endpoints except `/auth/login` and `/health` require JWT authentication via `Authorization: Bearer <token>`.

Endpoints marked **[WRITE]** additionally require `write_access` role. Endpoints marked **[ADMIN]** require `admin` role.

Base URL: `http://<host>:<port>`

---

## Authentication (`/auth`)

### POST /auth/login
**Auth:** None (public)  
**Request:**
```json
{ "username": "string", "password": "string" }
```
**Response:**
```json
{ "access_token": "string", "token_type": "bearer", "role": "string" }
```
**Description:** Authenticate with username/password. Returns a JWT bearer token valid for 24 hours.

### POST /auth/register
**Auth:** Admin  
**Request:**
```json
{ "username": "string", "password": "string", "role": "viewer|writer|admin" }
```
**Response:** `{ "id": int, "username": "string", "role": "string" }`  
**Description:** Create a new user account. Only admins may create accounts.

---

## System (`/system`)

### GET /system/health
**Auth:** Any authenticated user  
**Response:**
```json
{
  "status": "OK | DEGRADED | ERROR",
  "uptime_seconds": 3600.0,
  "startup_checks": [
    { "name": "string", "status": "OK|WARN|ERROR", "message": "string", "ts": "ISO8601" }
  ],
  "live": {
    "active_strategies": 3,
    "open_trades": 1,
    "db_ok": true,
    "timestamp_utc": "ISO8601"
  }
}
```
**Description:** Returns the cached startup diagnostics plus live system stats. `status` is `OK` if all checks passed, `DEGRADED` if any warnings, `ERROR` if any errors. Startup checks cover: DB connectivity, Alembic migration state, strategy registry consistency, critical AppSetting presence, MT5 bridge reachability, WeasyPrint availability, and active strategy parameter validity.

---

## Strategies (`/strategies`)

### GET /strategies
**Auth:** Any  
**Response:** List of strategy objects  
**Description:** Returns all strategies from the database including active/live flags and current params.

### GET /strategies/{name}
**Auth:** Any  
**Response:** Single strategy object  
**Description:** Returns a specific strategy by name.

### PUT /strategies/{name}/activate **[WRITE]**
**Auth:** Write  
**Description:** Mark a strategy as active (participating in live signal generation).

### PUT /strategies/{name}/deactivate **[WRITE]**
**Auth:** Write  
**Description:** Deactivate a strategy. Running continuous backtest loops for it are stopped.

### PUT /strategies/{name}/params **[WRITE]**
**Auth:** Write  
**Request:** Strategy-specific params dict  
**Description:** Update the live parameters for a strategy. Triggers a new `ParameterVersion` row.

### GET /strategies/{name}/params/history
**Auth:** Any  
**Response:** List of `ParameterVersion` objects  
**Description:** Returns the parameter change history for a strategy, newest first.

### GET /strategies/{name}/candidates
**Auth:** Any  
**Query params:** `page`, `limit`, `qualified_only`  
**Response:** Paginated list of `BacktestCandidate` objects  
**Description:** Returns parameter candidates evaluated by the continuous backtest engine.

---

## Backtest (`/backtest`)

### POST /backtest/run **[WRITE]**
**Auth:** Write  
**Status:** 202  
**Request (single):**
```json
{
  "strategy_name": "DTC",
  "symbol": "XAUUSD",
  "from_date": "2024-01-01",
  "to_date": "2024-12-31",
  "params": {},
  "initial_balance": 10000,
  "leverage": 100,
  "risk_per_trade_pct": 1.0
}
```
**Request (batch):**
```json
{
  "runs": [
    { "strategy_name": "DTC", "symbol": "XAUUSD", "from_date": "2024-01-01", "to_date": "2024-12-31" },
    { "strategy_name": "Alchemist", "symbol": "XAUUSD", "from_date": "2024-01-01", "to_date": "2024-12-31" }
  ],
  "shared_settings": { "initial_balance": 10000, "leverage": 100 }
}
```
**Response (single):** `{ "backtest_id": int, "status": "started" }`  
**Response (batch):** `{ "batch_id": "uuid", "run_count": int }`  
**Description:** Runs one or more backtests asynchronously. If the `runs` key is present, batch mode is activated and all runs execute in a background task with cross-strategy analysis on completion.

### GET /backtest/results
**Auth:** Any  
**Query params:** `limit` (default 20)  
**Response:** List of backtest result summaries (no equity curve / trade log — use individual endpoint for those)  
**Description:** Lists recent backtest results ordered by creation date descending.

### GET /backtest/results/{id}
**Auth:** Any  
**Response:**
```json
{
  "id": int,
  "strategy_name": "string",
  "symbol": "string",
  "from_date": "string",
  "to_date": "string",
  "params": {},
  "initial_balance": float,
  "leverage": int,
  "risk_per_trade_pct": float,
  "status": "PENDING|RUNNING|COMPLETE|FAILED",
  "metrics": {},
  "equity_curve": [],
  "batch_id": "uuid|null",
  "created_at": "ISO8601",
  "completed_at": "ISO8601|null"
}
```
**Description:** Full backtest result including metrics dict and equity curve array.

### GET /backtest/results/{id}/trade-log
**Auth:** Any  
**Query params:** `page` (default 1), `limit` (default 50, max 500)  
**Response:** `{ "total": int, "page": int, "limit": int, "trades": [] }`  
**Description:** Paginated trade-by-trade log for the backtest. Each trade includes entry, exit, pnl, duration, exit_reason.

### GET /backtest/results/{id}/parameter-evolution
**Auth:** Any  
**Response:** `{ "adaptation_events": [ { "after_trade_index": int, "win_rate_at_time": float, "param_deltas": {} } ] }`  
**Description:** Parameter adaptation events recorded during the backtest simulation.

### GET /backtest/results/{id}/monthly-breakdown
**Auth:** Any  
**Response:** Dict keyed by `"YYYY-MM"`: `{ "wins": int, "losses": int, "net_pnl": float }`  
**Description:** Month-by-month performance breakdown.

### GET /backtest/results/{id}/strategy-breakdown
**Auth:** Any  
**Response:** `{ "strategy_name": str, "metrics": {}, "drawdown_periods": [], "strategy_performance_timeline": {} }`  
**Description:** Detailed per-strategy breakdown including drawdown periods.

### GET /backtest/results/{id}/report.pdf
**Auth:** Any authenticated user  
**Response:** Binary `application/pdf`  
**Description:** Generates and streams a 5-page professional PDF report. Requires WeasyPrint (`pip install weasyprint`). Returns HTTP 501 if WeasyPrint is not installed. Pages: (1) Summary + equity curve SVG, (2) Monthly breakdown, (3) Parameter evolution, (4) Top 10 winning trades, (5) Top 10 losing trades. Download filename: `backtest_{id}_{strategy}_{symbol}.pdf`.

### GET /backtest/batch/{batch_id}
**Auth:** Any  
**Response:** Batch object with embedded individual result summaries  
**Description:** Returns the batch status, strategy names, shared settings, and a summary of each constituent run.

### GET /backtest/batch/{batch_id}/report
**Auth:** Any  
**Response:**
```json
{
  "batch": { "id": int, "batch_id": "uuid", "strategy_names": [], "shared_settings": {}, "status": "string", "created_at": "ISO8601", "completed_at": "ISO8601|null" },
  "individual_results": [ { /* full BacktestResultOut per run */ } ],
  "cross_analysis": { /* ensemble simulation + market regime analysis */ },
  "pair_analyses": [ { /* StrategyPairAnalysis objects */ } ]
}
```
**Description:** Complete combined JSON report for a batch, including all sub-components. This is the primary endpoint for the frontend's batch report view.

### GET /backtest/batch/{batch_id}/pair-analysis
**Auth:** Any  
**Response:** List of `StrategyPairAnalysis` objects ordered by `synergy_score` descending  
**Description:** Strategy pair/triple combination analysis. Each entry includes correlation, agreement rate, synergy score, recommendation flag, and the Claude-generated narrative (`analysis.narrative`, `analysis.works_well_when`, `analysis.watch_out_for`).

### GET /backtest/batch/{batch_id}/ensemble-simulation
**Auth:** Any  
**Response:** Ensemble simulation sub-section from `cross_analysis_json`  
**Description:** Simulated ensemble performance as if all strategies in the batch had been run together with weighted voting.

### POST /backtest/compare
**Auth:** Any  
**Request:** `{ "ids": [int, int, ...] }`  
**Response:** `{ "comparisons": [ { /* BacktestResult with equity curve */ } ] }`  
**Description:** Side-by-side comparison of multiple backtest results. Returns full metrics and equity curves for charting overlays.

---

## Ensemble (`/ensemble`)

### GET /ensemble/decisions
**Auth:** Any  
**Query params:** `page`, `limit`, `symbol`  
**Response:** Paginated list of `EnsembleDecision` objects  
**Description:** Returns the log of every ensemble signal evaluation (whether or not a trade was opened).

### GET /ensemble/decisions/{id}
**Auth:** Any  
**Response:** Single `EnsembleDecision` with full `strategy_votes_json`  
**Description:** Detailed view of one ensemble decision including per-strategy votes, weights, and level computation.

---

## Strategy Picker (`/picker`)

### GET /picker/decisions
**Auth:** Any  
**Query params:** `page`, `limit`, `symbol`  
**Response:** Paginated list of `StrategyPickerDecision` objects  
**Description:** Log of every picker evaluation — which strategies were scored, selected, and what weights were used.

### GET /picker/decisions/{id}
**Auth:** Any  
**Response:** Single `StrategyPickerDecision` with full `strategy_scores_json`  
**Description:** Detailed picker decision including all factor scores for each evaluated strategy.

### GET /picker/weights/history
**Auth:** Any  
**Query params:** `limit` (default 50)  
**Response:** List of `PickerWeightHistory` objects ordered by `updated_at` descending  
**Description:** Audit trail of online weight updates — one row per trade that triggered a weight update, showing weights before and after.

### GET /picker/weights/current
**Auth:** Any  
**Response:** `{ "strategy_name": float, ... }` — normalised weights currently used by the picker  
**Description:** Returns the current ensemble weights for all registered strategies.

---

## Trades (`/trades`)

### GET /trades
**Auth:** Any  
**Query params:** `limit`  
**Response:** List of recent `Trade` objects  
**Description:** Returns the most recent trades (open and closed) ordered by open time descending.

### GET /trades/stats
**Auth:** Any  
**Response:** `{ "total_trades", "wins", "losses", "win_rate", "profit_factor", "total_pnl", "max_drawdown", "avg_rr" }`  
**Description:** Aggregate statistics over the last 1000 closed trades.

### GET /trades/{id}
**Auth:** Any  
**Response:** Single `Trade` object  
**Description:** Individual trade detail.

### POST /trades/{id}/close **[WRITE]**
**Auth:** Write  
**Request:** `{ "exit_price": float, "pnl": float, "result": "WIN|LOSS" }`  
**Description:** Manually close a trade (used when bridge webhook is unavailable).

---

## Risk (`/risk`)

### GET /risk/status
**Auth:** Any  
**Response:** `{ "trading_halt": bool, "open_trades": int, "daily_pnl": float, "drawdown_pct": float, "account_balance": float }`  
**Description:** Current risk dashboard — trading halt state, daily PnL, current drawdown, open trade count.

### POST /risk/halt **[WRITE]**
**Auth:** Write  
**Description:** Immediately set `trading_halt = true`. All new entries are blocked.

### POST /risk/resume **[WRITE]**
**Auth:** Write  
**Description:** Clear the trading halt (`trading_halt = false`).

---

## News (`/news`)

### GET /news/items
**Auth:** Any  
**Query params:** `limit`, `symbol`  
**Response:** List of `NewsItem` objects with AI sentiment scores  
**Description:** Returns fetched news items with both raw and AI-scored sentiment.

### GET /news/sentiment/{symbol}
**Auth:** Any  
**Response:** `{ "symbol": str, "bias": float, "label": str, "confidence": float, "item_count": int }`  
**Description:** Aggregated sentiment for a symbol based on recent news items.

### POST /news/fetch **[WRITE]**
**Auth:** Write  
**Request:** `{ "symbol": "XAUUSD" }`  
**Description:** Manually trigger a news fetch cycle for the given symbol.

---

## Webhook (`/webhook`)

### POST /webhook/signal
**Auth:** API key (`X-API-Key` header — machine-to-machine only)  
**Request:** Signal payload from MT5 or external signal provider  
**Description:** Entry point for external trade signals. Passes through risk checks, picker scoring, and ensemble vote before potentially opening a trade.

### POST /webhook/close
**Auth:** API key  
**Request:** `{ "ticket": int, "exit_price": float, "result": "WIN|LOSS", "pnl": float }`  
**Description:** Receives trade close notifications from the MT5 bridge. Triggers weight updates in the picker.

---

## Parameters (`/params`)

### GET /params/current
**Auth:** Any  
**Response:** Current DTC strategy parameter dict  
**Description:** Returns the most recent `ParameterVersion` params for the DTC strategy.

### GET /params/history
**Auth:** Any  
**Query params:** `limit`  
**Response:** List of `ParameterVersion` objects  
**Description:** Full parameter change history.

### POST /params/rollback **[WRITE]**
**Auth:** Write  
**Request:** `{ "version": int }`  
**Description:** Roll back DTC parameters to a specific historical version.

---

## Adaptation (`/adapt`)

### POST /adapt/run **[WRITE]**
**Auth:** Write  
**Description:** Manually trigger one adaptation cycle for DTC — evaluates recent trade performance and adjusts parameters.

### GET /adapt/logs
**Auth:** Any  
**Query params:** `limit`  
**Response:** List of `AdaptationLog` objects  
**Description:** History of all adaptation events.

---

## Settings (`/settings`)

### GET /settings
**Auth:** Any  
**Response:** `{ "key": "value", ... }` — all `AppSetting` rows  
**Description:** Returns all application settings as a flat key-value dict.

### PUT /settings **[WRITE]**
**Auth:** Write  
**Request:** `{ "key": "value", ... }`  
**Description:** Batch-update one or more settings. Only provided keys are updated; others are unchanged.

### GET /settings/{key}
**Auth:** Any  
**Response:** `{ "key": str, "value": str }`

### PUT /settings/{key} **[WRITE]**
**Auth:** Write  
**Request:** `{ "value": str }`

---

## Bridge (`/bridge`)

### GET /bridge/account
**Auth:** Any  
**Response:** MT5 account info (balance, equity, margin, free margin)  
**Description:** Returns live account details from the MT5 bridge. Returns simulated values when `simulation_mode = true`.

### GET /bridge/positions
**Auth:** Any  
**Response:** List of open MT5 positions  
**Description:** Returns all currently open positions from MT5.

---

## Shadow Signals (`/shadow-signals`)

### GET /shadow-signals
**Auth:** Any  
**Query params:** `strategy_name`, `limit`  
**Response:** List of `ShadowSignal` objects  
**Description:** Returns shadow (paper) signals logged by inactive or observer-mode strategies. Useful for evaluating a strategy's hypothetical performance before going live.

---

## WebSocket (`/ws`)

### WS /ws/live
**Auth:** `?token=<jwt>` query parameter  
**Messages sent by server:**
```json
{ "type": "trade_opened|trade_closed|signal|system", "data": {} }
```
**Description:** Real-time push channel for live trade events, signals, and system alerts. Connect from the frontend dashboard for live updates without polling.

---

## Health (public)

### GET /health
**Auth:** None  
**Response:** `{ "status": "ok", "version": "2.0.0" }`  
**Description:** Lightweight health check for load balancers and container orchestrators. Does not perform DB queries.

---

## Error Responses

All endpoints return standard error shapes:

| Status | Meaning |
|--------|---------|
| 400 | Bad request — malformed payload |
| 401 | Missing or invalid JWT token |
| 403 | Insufficient role (write or admin required) |
| 404 | Resource not found |
| 422 | Validation error — request body schema mismatch |
| 501 | Feature not available (e.g. WeasyPrint not installed) |
| 500 | Internal server error — check server logs |

Error body: `{ "detail": "string" }`

---

## Notes for Frontend Integration

- **Backtest PDF:** `GET /backtest/results/{id}/report.pdf` — check for HTTP 501 and show install instructions if WeasyPrint is missing.
- **Batch report polling:** After `POST /backtest/run` (batch mode), poll `GET /backtest/batch/{batch_id}` until `status == "COMPLETE"`, then fetch the full report from `GET /backtest/batch/{batch_id}/report`.
- **Health indicator:** Display the badge from `GET /system/health` → `status` field. Drill-down via `startup_checks` array for diagnostics panel.
- **Settings form binding:** Use the key names from `docs/app_settings.md` directly as form field `name` attributes. All values are strings; parse floats/ints/booleans client-side.
- **Picker weight sliders:** Bind to keys prefixed `picker_weight_` — there are 7 of them. Normalise to sum-to-1 before display (or show raw and note that the API normalises on read).