'use strict';
/**
 * POST   /simulate/batch  — generate N demo closed trades
 * DELETE /simulate/reset  — wipe all trades, params, and adaptation logs
 */
const { Router } = require('express');
const crud                = require('../db/crud');
const { DEFAULT_PARAMS }  = require('../engine/parameters');
const { runAdaptation }   = require('../engine/adaptation');
const { Trade, ParameterVersion, AdaptationLog, sequelize } = require('../db/models');

const router  = Router();
const SYMBOLS = ['EURUSD', 'GBPUSD', 'USDJPY', 'XAUUSD'];

/** Gaussian random using Box-Muller */
function _gauss(mean = 0, std = 1) {
  const u = 1 - Math.random();
  const v = Math.random();
  return mean + std * Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

function _brownianPrice(base = 1.085, steps = 1, volatility = 0.0005) {
  const prices = [base];
  for (let i = 0; i < steps; i++) {
    prices.push(Math.round((prices[prices.length - 1] + _gauss(0, volatility)) * 100000) / 100000);
  }
  return prices;
}

router.post('/simulate/batch', async (req, res) => {
  const count      = Math.min(Math.max(parseInt(req.query.count      || '20',   10), 1),  200);
  const winRatePct = Math.min(Math.max(parseFloat(req.query.win_rate_pct || '52'), 0), 100);

  let params = await crud.getCurrentParams();
  if (!params) {
    await crud.saveParams(DEFAULT_PARAMS, 'Initial defaults', 'SYSTEM');
    params = { ...DEFAULT_PARAMS };
  }

  const latestPv = await ParameterVersion.findOne({ order: [['version', 'DESC']] });
  const version  = latestPv ? latestPv.version : 1;

  let basePrice = 1.08500;
  const tradeIds = [];

  for (let i = 0; i < count; i++) {
    const symbol    = SYMBOLS[Math.floor(Math.random() * SYMBOLS.length)];
    const direction = Math.random() > 0.5 ? 'BUY' : 'SELL';
    const price     = _brownianPrice(basePrice, 1)[1];
    basePrice       = price;

    const atr    = Math.random() * (0.0025 - 0.0005) + 0.0005;
    const slDist = price * (params.stop_loss_pct  / 100);
    const tpDist = price * (params.take_profit_pct / 100);

    const sl = direction === 'BUY'
      ? Math.round((price - slDist) * 100000) / 100000
      : Math.round((price + slDist) * 100000) / 100000;
    const tp = direction === 'BUY'
      ? Math.round((price + tpDist) * 100000) / 100000
      : Math.round((price - tpDist) * 100000) / 100000;

    const isWin = Math.random() * 100 < winRatePct;
    const exitPrice = isWin ? tp : sl;
    const pnl = isWin
      ?  Math.round(Math.abs(tp - price) * params.lot_size * 100000 * 100) / 100
      : -Math.round(Math.abs(sl - price) * params.lot_size * 100000 * 100) / 100;
    const result = isWin ? 'WIN' : 'LOSS';

    const now         = Date.now();
    const offsetMs    = Math.floor(Math.random() * 1440 + 5) * 60000;
    const openedAt    = new Date(now - offsetMs);
    const durationMs  = Math.floor(Math.random() * (240 - 10) + 10) * 60000;
    const closedAt    = new Date(openedAt.getTime() + durationMs);
    const durationMin = durationMs / 60000;

    const trade = await crud.logTrade({
      symbol, direction,
      entry_price:      price,
      exit_price:       exitPrice,
      stop_loss:        sl,
      take_profit:      tp,
      lot_size:         params.lot_size,
      pnl,
      result,
      duration_mins:    Math.round(durationMin * 10) / 10,
      atr_at_entry:     Math.round(atr * 100000) / 100000,
      ema_fast_at_entry: Math.round((price - atr * 0.3) * 100000) / 100000,
      ema_slow_at_entry: Math.round((price - atr * 0.7) * 100000) / 100000,
      params_version:   version,
      opened_at:        openedAt,
      closed_at:        closedAt,
    });

    // Patch timestamps directly (Sequelize already wrote them via logTrade above)
    await Trade.update(
      { opened_at: openedAt, closed_at: closedAt },
      { where: { id: trade.id } },
    );

    tradeIds.push(trade.id);
  }

  const adaptResult = await runAdaptation(Math.min(count, 20));

  return res.json({
    simulated:  count,
    trade_ids:  tradeIds,
    adaptation: adaptResult,
    stats:      await crud.getStats(),
  });
});

router.delete('/simulate/reset', async (_req, res) => {
  await AdaptationLog.destroy({ where: {} });
  await ParameterVersion.destroy({ where: {} });
  await Trade.destroy({ where: {} });
  return res.json({ status: 'reset complete' });
});

module.exports = router;
