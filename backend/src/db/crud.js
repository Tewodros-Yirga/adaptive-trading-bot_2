'use strict';
const { Trade, ParameterVersion, AdaptationLog } = require('./models');
const { Op } = require('sequelize');

// ── Trades ───────────────────────────────────────────────────────────────────

async function logTrade(fields) {
  return Trade.create(fields);
}

async function closeTrade(tradeId, exitPrice, pnl, result) {
  const trade = await Trade.findByPk(tradeId);
  if (!trade) return null;

  const now = new Date();
  const durationMins = trade.opened_at
    ? (now - new Date(trade.opened_at)) / 60000
    : null;

  await trade.update({
    exit_price:    exitPrice,
    pnl,
    result,
    closed_at:     now,
    duration_mins: durationMins !== null ? Math.round(durationMins * 10) / 10 : null,
  });
  return trade.reload();
}

async function getRecentTrades(n = 50) {
  return Trade.findAll({ order: [['opened_at', 'DESC']], limit: n });
}

async function getClosedTrades(n = 100) {
  return Trade.findAll({
    where:  { result: { [Op.in]: ['WIN', 'LOSS'] } },
    order:  [['closed_at', 'DESC']],
    limit:  n,
  });
}

async function getStats() {
  const trades = await getClosedTrades(1000);
  if (!trades.length) {
    return {
      total_trades: 0, wins: 0, losses: 0,
      win_rate: 0.0, profit_factor: 0.0,
      total_pnl: 0.0, max_drawdown: 0.0, avg_rr: 0.0,
    };
  }

  const wins   = trades.filter(t => t.result === 'WIN');
  const losses = trades.filter(t => t.result === 'LOSS');

  const grossProfit = wins.reduce((s, t)   => s + (t.pnl || 0), 0);
  const grossLoss   = Math.abs(losses.reduce((s, t) => s + (t.pnl || 0), 0));
  const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : Infinity;

  // Max drawdown
  let cumulative = 0, peak = 0, maxDrawdown = 0;
  [...trades].reverse().forEach(t => {
    cumulative += t.pnl || 0;
    if (cumulative > peak) peak = cumulative;
    const dd = peak - cumulative;
    if (dd > maxDrawdown) maxDrawdown = dd;
  });

  // Avg R:R
  const rrValues = trades
    .filter(t => t.entry_price && t.stop_loss && t.take_profit)
    .map(t => {
      const risk   = Math.abs(t.entry_price - t.stop_loss);
      const reward = Math.abs(t.take_profit - t.entry_price);
      return risk > 0 ? reward / risk : null;
    })
    .filter(v => v !== null);
  const avgRr = rrValues.length ? rrValues.reduce((a, b) => a + b, 0) / rrValues.length : 0;

  return {
    total_trades:  trades.length,
    wins:          wins.length,
    losses:        losses.length,
    win_rate:      Math.round((wins.length / trades.length) * 10000) / 100,
    profit_factor: Math.round(profitFactor * 1000) / 1000,
    total_pnl:     Math.round(trades.reduce((s, t) => s + (t.pnl || 0), 0) * 10000) / 10000,
    max_drawdown:  Math.round(maxDrawdown * 10000) / 10000,
    avg_rr:        Math.round(avgRr * 100) / 100,
  };
}


// ── Parameters ───────────────────────────────────────────────────────────────

async function saveParams(params, reason = '', trigger = 'AUTO') {
  const last    = await ParameterVersion.findOne({ order: [['version', 'DESC']] });
  const version = last ? last.version + 1 : 1;
  return ParameterVersion.create({
    version,
    params_json: JSON.stringify(params),
    reason,
    trigger,
    created_at: new Date(),
  });
}

async function getCurrentParams() {
  const last = await ParameterVersion.findOne({ order: [['version', 'DESC']] });
  if (!last) return null;
  return JSON.parse(last.params_json);
}

async function getParamsHistory(limit = 30) {
  return ParameterVersion.findAll({ order: [['version', 'DESC']], limit });
}


// ── Adaptation Logs ──────────────────────────────────────────────────────────

async function logAdaptation(fields) {
  return AdaptationLog.create({ ...fields, evaluated_at: new Date() });
}

module.exports = {
  logTrade,
  closeTrade,
  getRecentTrades,
  getClosedTrades,
  getStats,
  saveParams,
  getCurrentParams,
  getParamsHistory,
  logAdaptation,
};
