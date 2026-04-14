'use strict';
/**
 * GET  /trades            — paginated trade history
 * GET  /trades/stats      — win rate, profit factor, drawdown, PnL
 * POST /trades/:id/close  — manually close a simulated trade
 */
const { Router } = require('express');
const crud       = require('../db/crud');
const { Trade }  = require('../db/models');

const router = Router();

router.get('/trades', async (req, res) => {
  const limit  = Math.min(parseInt(req.query.limit || '50', 10), 500);
  const trades = await crud.getRecentTrades(limit);
  return res.json(trades.map(_serializeTrade));
});

router.get('/trades/stats', async (_req, res) => {
  return res.json(await crud.getStats());
});

router.post('/trades/:id/close', async (req, res) => {
  const tradeId = parseInt(req.params.id, 10);
  const trade   = await Trade.findByPk(tradeId);

  if (!trade)               return res.json({ error: 'Trade not found' });
  if (trade.result !== 'OPEN') return res.json({ error: 'Trade already closed' });

  // Simulate outcome — 55 % chance TP hit
  const hitTp     = Math.random() > 0.45;
  const exitPrice = hitTp
    ? (trade.take_profit || trade.entry_price * 1.01)
    : (trade.stop_loss   || trade.entry_price * 0.995);
  const pnl = hitTp
    ?  Math.abs(exitPrice - trade.entry_price) * trade.lot_size * 100000
    : -Math.abs(exitPrice - trade.entry_price) * trade.lot_size * 100000;
  const result = hitTp ? 'WIN' : 'LOSS';

  const updated = await crud.closeTrade(
    tradeId,
    Math.round(exitPrice * 100000) / 100000,
    Math.round(pnl * 100) / 100,
    result,
  );
  return res.json(_serializeTrade(updated));
});

function _serializeTrade(t) {
  return {
    id:               t.id,
    symbol:           t.symbol,
    direction:        t.direction,
    entry_price:      t.entry_price,
    exit_price:       t.exit_price,
    stop_loss:        t.stop_loss,
    take_profit:      t.take_profit,
    lot_size:         t.lot_size,
    pnl:              t.pnl,
    result:           t.result,
    duration_mins:    t.duration_mins,
    atr_at_entry:     t.atr_at_entry,
    ema_fast_at_entry: t.ema_fast_at_entry,
    ema_slow_at_entry: t.ema_slow_at_entry,
    params_version:   t.params_version,
    opened_at:        t.opened_at ? new Date(t.opened_at).toISOString() : null,
    closed_at:        t.closed_at ? new Date(t.closed_at).toISOString() : null,
  };
}

module.exports = router;
