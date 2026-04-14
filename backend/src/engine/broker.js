'use strict';
/**
 * Custom MT4/MT5 Bridge Client
 * ─────────────────────────────
 * Replaces MetaAPI with a direct HTTP bridge to your MT4/MT5 EA.
 *
 * The EA running on your MT terminal must expose a local HTTP server
 * with the following endpoints (see bridge/mt_bridge.mq4 / mt_bridge.mq5):
 *
 *   POST   /order              — place a new market order
 *   POST   /close              — close an open position by ticket
 *   GET    /account            — get balance, equity, margin
 *   GET    /positions          — get all open positions
 *   GET    /history?from=&to=  — get closed trades in date range
 *
 * All requests carry header:  X-Bridge-Secret: <MT_BRIDGE_SECRET>
 * All responses are JSON.
 */
const axios  = require('axios');
const config = require('../config');
const logger = require('../logger');

function _client() {
  return axios.create({
    baseURL: config.MT_BRIDGE_URL,
    timeout: 10000,
    headers: {
      'Content-Type':    'application/json',
      'X-Bridge-Secret': config.MT_BRIDGE_SECRET,
    },
  });
}

// ── Place Order ───────────────────────────────────────────────────────────────
async function placeOrder({ symbol, direction, lotSize, stopLoss, takeProfit, currentPrice }) {
  if (config.SIMULATION_MODE) {
    return _simulateOrder({ symbol, direction, lot: lotSize, entry: currentPrice, sl: stopLoss, tp: takeProfit });
  }

  try {
    const { data } = await _client().post('/order', {
      symbol,
      type:       direction,   // 'BUY' | 'SELL'
      volume:     lotSize,
      stopLoss,
      takeProfit,
      comment:    'adaptive-bot',
    });

    logger.info(`Bridge order placed: ticket=${data.ticket} ${direction} ${symbol} vol=${lotSize}`);
    return {
      orderId:    String(data.ticket),
      symbol:     data.symbol    || symbol,
      direction:  data.type      || direction,
      volume:     data.volume    || lotSize,
      openPrice:  data.openPrice || currentPrice,
      stopLoss:   data.sl        || stopLoss,
      takeProfit: data.tp        || takeProfit,
      simulated:  false,
    };
  } catch (err) {
    logger.error(`Bridge placeOrder failed: ${err.response?.data?.error || err.message}`);
    return { error: err.response?.data?.error || err.message };
  }
}

// ── Close Position ─────────────────────────────────────────────────────────────
async function closePosition(ticket, lotSize) {
  if (config.SIMULATION_MODE) {
    return { closed: true, ticket, simulated: true };
  }

  try {
    const { data } = await _client().post('/close', { ticket, volume: lotSize });
    logger.info(`Bridge closed ticket=${ticket}`);
    return data;
  } catch (err) {
    logger.error(`Bridge closePosition failed: ${err.response?.data?.error || err.message}`);
    return { error: err.response?.data?.error || err.message };
  }
}

// ── Account Info ──────────────────────────────────────────────────────────────
async function getAccountInfo() {
  if (config.SIMULATION_MODE) {
    return { balance: 10000.0, equity: 10000.0, margin: 0.0, freeMargin: 10000.0, mode: 'SIMULATION' };
  }

  try {
    const { data } = await _client().get('/account');
    return {
      balance:    data.balance,
      equity:     data.equity,
      margin:     data.margin,
      freeMargin: data.freeMargin || data.free_margin,
      mode:       'LIVE',
    };
  } catch (err) {
    logger.error(`Bridge getAccountInfo failed: ${err.response?.data?.error || err.message}`);
    return { error: err.response?.data?.error || err.message };
  }
}

// ── Open Positions ────────────────────────────────────────────────────────────
async function getOpenPositions() {
  if (config.SIMULATION_MODE) return [];

  try {
    const { data } = await _client().get('/positions');
    return data.positions || data;
  } catch (err) {
    logger.error(`Bridge getOpenPositions failed: ${err.response?.data?.error || err.message}`);
    return [];
  }
}

// ── Simulation fallback ───────────────────────────────────────────────────────
function _simulateOrder({ symbol, direction, lot, entry, sl, tp }) {
  const ticket = Math.floor(Math.random() * 900000) + 100000;
  return {
    orderId:    String(ticket),
    symbol,
    direction,
    volume:     lot,
    openPrice:  entry,
    stopLoss:   sl,
    takeProfit: tp,
    simulated:  true,
  };
}

module.exports = { placeOrder, closePosition, getAccountInfo, getOpenPositions };
