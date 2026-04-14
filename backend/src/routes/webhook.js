'use strict';
/**
 * POST /webhook — receives TradingView alert JSON and executes a trade.
 *
 * Expected body:
 * {
 *   "secret":   "your_secret_token",
 *   "signal":   "BUY",          // BUY | SELL | CLOSE
 *   "symbol":   "EURUSD",
 *   "price":    1.08500,
 *   "atr":      0.00120,
 *   "ema_fast": 1.08480,
 *   "ema_slow": 1.08420
 * }
 */
const { Router } = require('express');
const crud    = require('../db/crud');
const broker  = require('../engine/broker');
const { DEFAULT_PARAMS }  = require('../engine/parameters');
const { runAdaptation }   = require('../engine/adaptation');
const { ParameterVersion } = require('../db/models');
const config  = require('../config');
const logger  = require('../logger');

const router = Router();

router.post('/webhook', async (req, res) => {
  const { secret = '', signal: rawSignal, symbol = config.SYMBOL,
          price: rawPrice = 0, atr = null, ema_fast = null, ema_slow = null } = req.body;

  // ── Auth ──────────────────────────────────────────────────────────────────
  if (config.WEBHOOK_SECRET && config.WEBHOOK_SECRET !== 'changeme' && secret !== config.WEBHOOK_SECRET) {
    return res.status(403).json({ error: 'Invalid webhook secret' });
  }

  let params = await crud.getCurrentParams();
  if (!params) {
    await crud.saveParams(DEFAULT_PARAMS, 'Initial defaults', 'SYSTEM');
    params = { ...DEFAULT_PARAMS };
  }

  const signal = (rawSignal || '').toUpperCase();
  if (!['BUY', 'SELL', 'CLOSE'].includes(signal)) {
    return res.status(400).json({ error: `Unknown signal: ${signal}` });
  }
  if (signal === 'CLOSE') {
    return res.json({ status: 'CLOSE signal received — manual close not yet wired to broker' });
  }

  // ── Calculate SL / TP ──────────────────────────────────────────────────────
  const price  = rawPrice || 1.0;
  const slDist = price * (params.stop_loss_pct / 100);
  const tpDist = price * (params.take_profit_pct / 100);
  const sl = signal === 'BUY' ? price - slDist : price + slDist;
  const tp = signal === 'BUY' ? price + tpDist : price - tpDist;

  // ── Place order ────────────────────────────────────────────────────────────
  const order = await broker.placeOrder({
    symbol,
    direction:    signal,
    lotSize:      params.lot_size,
    stopLoss:     Math.round(sl * 100000) / 100000,
    takeProfit:   Math.round(tp * 100000) / 100000,
    currentPrice: price,
  });

  // ── Get current param version ──────────────────────────────────────────────
  const latestPv = await ParameterVersion.findOne({ order: [['version', 'DESC']] });
  const version  = latestPv ? latestPv.version : 1;

  // ── Log trade ──────────────────────────────────────────────────────────────
  const trade = await crud.logTrade({
    symbol,
    direction:        signal,
    entry_price:      price,
    stop_loss:        Math.round(sl * 100000) / 100000,
    take_profit:      Math.round(tp * 100000) / 100000,
    lot_size:         params.lot_size,
    result:           'OPEN',
    atr_at_entry:     atr,
    ema_fast_at_entry: ema_fast,
    ema_slow_at_entry: ema_slow,
    params_version:   version,
    opened_at:        new Date(),
  });

  logger.info(`Trade #${trade.id} opened: ${signal} ${symbol} @ ${price}`);

  // ── Auto-adaptation trigger ────────────────────────────────────────────────
  const allClosed = await crud.getClosedTrades(100000);
  const closedCount = allClosed.length;
  let adaptationResult = null;
  if (closedCount > 0 && closedCount % config.ADAPTATION_INTERVAL === 0) {
    logger.info(`Triggering adaptation at ${closedCount} closed trades`);
    adaptationResult = await runAdaptation();
  }

  return res.json({
    status:               'ok',
    trade_id:             trade.id,
    signal,
    order,
    adaptation_triggered: adaptationResult !== null,
    adaptation:           adaptationResult,
  });
});

module.exports = router;
