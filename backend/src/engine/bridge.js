'use strict';
/**
 * File-Based Bridge Adapter (Node.js side)
 * ──────────────────────────────────────────
 * Works alongside mt4_bridge.mq4 / mt5_bridge.mq5.
 *
 * How it works:
 *  1. Node.js writes a JSON command to:  <MT4_FILES_DIR>/adaptive_bot/cmd.json
 *  2. The MT4/MT5 EA picks it up on next tick (~1 s latency)
 *  3. EA writes result to:               <MT4_FILES_DIR>/adaptive_bot/resp.json
 *  4. Node.js polls for resp.json, reads it, deletes both files
 *
 * Set MT4_FILES_DIR in .env to your MT4 terminal's MQL4/Files/ path:
 *   MT4_FILES_DIR=C:\Users\You\AppData\Roaming\MetaQuotes\Terminal\<hash>\MQL4\Files
 *
 * If SIMULATION_MODE=true, no files are written and orders are faked locally.
 */
const fs     = require('fs');
const path   = require('path');
const config = require('../config');
const logger = require('../logger');

const MT4_FILES_DIR = process.env.MT4_FILES_DIR || '';
const BOT_DIR       = path.join(MT4_FILES_DIR, 'adaptive_bot');
const CMD_FILE      = path.join(BOT_DIR, 'cmd.json');
const RESP_FILE     = path.join(BOT_DIR, 'resp.json');
const POLL_MS       = 200;   // poll every 200 ms
const TIMEOUT_MS    = 10000; // give up after 10 s

// Ensure folder exists
if (MT4_FILES_DIR && !fs.existsSync(BOT_DIR)) {
  fs.mkdirSync(BOT_DIR, { recursive: true });
}

// ── Send command and wait for response ────────────────────────────────────────
async function _sendCmd(payload) {
  if (!MT4_FILES_DIR) {
    throw new Error('MT4_FILES_DIR not set in .env — cannot use file bridge');
  }

  // Delete stale files
  if (fs.existsSync(CMD_FILE))  fs.unlinkSync(CMD_FILE);
  if (fs.existsSync(RESP_FILE)) fs.unlinkSync(RESP_FILE);

  // Write command
  fs.writeFileSync(CMD_FILE, JSON.stringify(payload), 'utf8');
  logger.info(`[Bridge] CMD → ${JSON.stringify(payload)}`);

  // Poll for response
  const deadline = Date.now() + TIMEOUT_MS;
  return new Promise((resolve, reject) => {
    const iv = setInterval(() => {
      if (Date.now() > deadline) {
        clearInterval(iv);
        reject(new Error('Bridge timeout — no response from MT4/MT5 EA'));
        return;
      }
      if (!fs.existsSync(RESP_FILE)) return;
      clearInterval(iv);
      try {
        const raw  = fs.readFileSync(RESP_FILE, 'utf8');
        fs.unlinkSync(RESP_FILE);
        const data = JSON.parse(raw);
        logger.info(`[Bridge] RESP ← ${raw}`);
        resolve(data);
      } catch (e) {
        reject(new Error(`Bridge: failed to parse response: ${e.message}`));
      }
    }, POLL_MS);
  });
}

// ── Public API (same interface as HTTP broker.js) ─────────────────────────────
async function placeOrder({ symbol, direction, lotSize, stopLoss, takeProfit, currentPrice }) {
  if (config.SIMULATION_MODE) return _simulateOrder({ symbol, direction, lot: lotSize, entry: currentPrice, sl: stopLoss, tp: takeProfit });

  const data = await _sendCmd({ action: 'order', symbol, type: direction, volume: lotSize, stopLoss, takeProfit });
  if (data.error) return data;
  return {
    orderId:    String(data.ticket),
    symbol:     data.symbol    || symbol,
    direction,
    volume:     data.volume    || lotSize,
    openPrice:  data.openPrice || currentPrice,
    stopLoss:   data.sl        || stopLoss,
    takeProfit: data.tp        || takeProfit,
    simulated:  false,
  };
}

async function closePosition(ticket, volume) {
  if (config.SIMULATION_MODE) return { closed: true, ticket, simulated: true };
  return _sendCmd({ action: 'close', ticket, volume });
}

async function getAccountInfo() {
  if (config.SIMULATION_MODE) return { balance: 10000, equity: 10000, margin: 0, freeMargin: 10000, mode: 'SIMULATION' };
  return _sendCmd({ action: 'account' });
}

async function getOpenPositions() {
  if (config.SIMULATION_MODE) return [];
  const data = await _sendCmd({ action: 'positions' });
  return data.positions || [];
}

function _simulateOrder({ symbol, direction, lot, entry, sl, tp }) {
  const ticket = Math.floor(Math.random() * 900000) + 100000;
  return { orderId: String(ticket), symbol, direction, volume: lot, openPrice: entry, stopLoss: sl, takeProfit: tp, simulated: true };
}

module.exports = { placeOrder, closePosition, getAccountInfo, getOpenPositions };
