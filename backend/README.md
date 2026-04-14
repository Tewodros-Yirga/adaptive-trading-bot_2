# Adaptive Trading Bot — Node.js Backend

Self-adaptive EMA crossover trading bot. Built with **Express + Sequelize (SQLite)**.  
Fully converted from the original Python/FastAPI implementation.

---

## Quick Start

```bash
# 1. Copy env file and fill in your credentials
cp .env.example .env

# 2. Install dependencies
npm install

# 3. Start the server (port 8000 by default)
npm start          # production
npm run dev        # auto-reload with nodemon
```

---

## Project Structure

```
backend/
├── src/
│   ├── index.js              # Express app entry point
│   ├── config.js             # .env loader
│   ├── logger.js             # Structured console logger
│   ├── db/
│   │   ├── sequelize.js      # SQLite connection (Sequelize)
│   │   ├── models.js         # Trade, ParameterVersion, AdaptationLog
│   │   └── crud.js           # All DB operations
│   ├── engine/
│   │   ├── parameters.js     # DEFAULT_PARAMS + adaptation bounds
│   │   ├── strategy.js       # EMA crossover + ATR signal generator
│   │   ├── adaptation.js     # Rule-based parameter tuning engine
│   │   └── broker.js         # MetaAPI HTTP client + simulation fallback
│   └── routes/
│       ├── webhook.js        # POST /webhook
│       ├── trades.js         # GET /trades, GET /trades/stats, POST /trades/:id/close
│       ├── params.js         # GET/POST /params, GET /params/history
│       ├── adapt.js          # POST /adapt/run, GET /adapt/log
│       └── simulate.js       # POST /simulate/batch, DELETE /simulate/reset
├── .env.example
├── .gitignore
└── package.json
```

---

## API Reference

| Method   | Endpoint                  | Description                              |
|----------|---------------------------|------------------------------------------|
| `GET`    | `/`                       | Health + bot status                      |
| `GET`    | `/health`                 | Simple health check                      |
| `POST`   | `/webhook`                | Receive TradingView signal               |
| `GET`    | `/trades`                 | Recent trade history (`?limit=50`)       |
| `GET`    | `/trades/stats`           | Win rate, PnL, drawdown, profit factor   |
| `POST`   | `/trades/:id/close`       | Simulate closing an open trade           |
| `GET`    | `/params`                 | Current active strategy parameters       |
| `POST`   | `/params`                 | Manual parameter override (merges)       |
| `GET`    | `/params/history`         | Parameter version history                |
| `POST`   | `/adapt/run`              | Manually trigger adaptation engine       |
| `GET`    | `/adapt/log`              | Adaptation history log                   |
| `POST`   | `/simulate/batch`         | Generate N demo trades (`?count=20&win_rate_pct=52`) |
| `DELETE` | `/simulate/reset`         | Wipe all trades, params, and logs        |

---

## Webhook Payload (TradingView Alert)

```json
{
  "secret":   "your_webhook_secret",
  "signal":   "BUY",
  "symbol":   "EURUSD",
  "price":    1.08500,
  "atr":      0.00120,
  "ema_fast": 1.08480,
  "ema_slow": 1.08420
}
```

---

## Adaptation Rules

The engine runs automatically every `ADAPTATION_INTERVAL` closed trades:

| Rule              | Condition                          | Action                          |
|-------------------|------------------------------------|---------------------------------|
| `win_rate_sl`     | Win rate < 40 %                    | Widen stop-loss by 0.1 %        |
| `win_rate_sl_tight` | Win rate > 65 % + PF > 1.5      | Tighten stop-loss by 0.05 %     |
| `volatility_high` | ATR% > 0.5 %                      | Increase ATR multiplier by 0.1  |
| `volatility_low`  | ATR% < 0.2 %                      | Decrease ATR multiplier by 0.1  |
| `ema_tune`        | Win rate < 45 %                    | Increase EMA periods            |
| `tp_improve`      | Profit factor < 1.0 + WR ≥ 40 %  | Widen take-profit by 0.1 %      |

---

## Environment Variables

| Variable             | Default          | Description                          |
|----------------------|------------------|--------------------------------------|
| `META_API_TOKEN`     | —                | MetaAPI auth token                   |
| `META_ACCOUNT_ID`    | —                | MT5 account ID                       |
| `WEBHOOK_SECRET`     | `changeme`       | TradingView webhook secret           |
| `SYMBOL`             | `EURUSD`         | Default trading symbol               |
| `ADAPTATION_INTERVAL`| `20`             | Trades between auto-adaptations      |
| `SIMULATION_MODE`    | `true`           | Skip real broker calls               |
| `DATABASE_URL`       | `./trading_bot.db` | SQLite file path                   |
| `PORT`               | `8000`           | HTTP server port                     |
