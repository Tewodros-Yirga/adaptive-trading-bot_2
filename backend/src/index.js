'use strict';
/**
 * Express application entry point — Adaptive Trading Bot (Node.js)
 */
require('dotenv').config();
const express = require('express');
const cors    = require('cors');
const path    = require('path');

const config          = require('./config');
const logger          = require('./logger');
const { initDb }      = require('./db/models');
const crud            = require('./db/crud');
const { DEFAULT_PARAMS } = require('./engine/parameters');

// ── Routes ────────────────────────────────────────────────────────────────────
const webhookRouter  = require('./routes/webhook');
const tradesRouter   = require('./routes/trades');
const paramsRouter   = require('./routes/params');
const adaptRouter    = require('./routes/adapt');
const simulateRouter = require('./routes/simulate');
const bridgeRouter   = require('./routes/bridge');

const app  = express();
const PORT = process.env.PORT || 8000;

// ── Middleware ────────────────────────────────────────────────────────────────
app.use(cors());
app.use(express.json());

// ── Serve Frontend ────────────────────────────────────────────────────────────
const frontendPath = path.join(__dirname, '../../frontend');
app.use(express.static(frontendPath));

// ── Mount API Routers ─────────────────────────────────────────────────────────
app.use('/', webhookRouter);
app.use('/', tradesRouter);
app.use('/', paramsRouter);
app.use('/', adaptRouter);
app.use('/', simulateRouter);
app.use('/', bridgeRouter);

// ── Health ────────────────────────────────────────────────────────────────────
app.get('/api/status', (_req, res) => {
  res.json({
    status: 'running',
    mode:   config.SIMULATION_MODE ? 'SIMULATION' : 'LIVE',
    symbol: config.SYMBOL,
    bridge: config.MT_BRIDGE_URL,
  });
});

app.get('/health', (_req, res) => res.json({ status: 'ok' }));

// ── SPA fallback ──────────────────────────────────────────────────────────────
app.get('*', (req, res, next) => {
  if (req.path.startsWith('/api') || req.path.startsWith('/webhook') ||
      req.path.startsWith('/trades') || req.path.startsWith('/params') ||
      req.path.startsWith('/adapt') || req.path.startsWith('/simulate') ||
      req.path.startsWith('/bridge') || req.path === '/health') {
    return next();
  }
  res.sendFile(path.join(frontendPath, 'index.html'));
});

// ── Error Handler ─────────────────────────────────────────────────────────────
app.use((err, _req, res, _next) => {
  logger.error(err.stack || err.message);
  res.status(500).json({ error: 'Internal server error' });
});

// ── Startup ───────────────────────────────────────────────────────────────────
async function start() {
  logger.info('Initializing PostgreSQL database...');
  await initDb();

  const existing = await crud.getCurrentParams();
  if (!existing) {
    await crud.saveParams(DEFAULT_PARAMS, 'Initial seed on startup', 'SYSTEM');
    logger.info('Default parameters seeded to DB');
  } else {
    const history = await crud.getParamsHistory(1);
    logger.info(`Loaded existing parameters (latest version: ${history[0]?.version ?? '?'})`);
  }

  logger.info(`Bot starting | Symbol: ${config.SYMBOL} | Mode: ${config.SIMULATION_MODE ? 'SIMULATION' : 'LIVE'} | Bridge: ${config.MT_BRIDGE_URL}`);

  app.listen(PORT, () => {
    logger.info(`Server listening on http://localhost:${PORT}`);
    logger.info(`Dashboard: http://localhost:${PORT}`);
  });
}

start().catch(err => {
  logger.error(`Startup failed: ${err.message}`);
  process.exit(1);
});
