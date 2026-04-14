'use strict';
/**
 * Adaptation engine — rule-based parameter tuning.
 * Mirrors Python engine/adaptation.py exactly.
 */
const crud   = require('../db/crud');
const logger = require('../logger');

function _clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

async function runAdaptation(window = 20) {
  const params = await crud.getCurrentParams();
  if (!params) return { error: 'No parameters found in DB' };

  const trades = await crud.getClosedTrades(window);
  if (trades.length < 5) {
    return { skipped: true, reason: `Only ${trades.length} closed trades — need at least 5` };
  }

  const wins   = trades.filter(t => t.result === 'WIN');
  const losses = trades.filter(t => t.result === 'LOSS');
  const winRate = (wins.length / trades.length) * 100;

  const grossProfit = wins.reduce((s, t)   => s + (t.pnl || 0), 0);
  const grossLoss   = Math.abs(losses.reduce((s, t) => s + (t.pnl || 0), 0)) || 0.0001;
  const profitFactor = grossProfit / grossLoss;

  const atrValues = trades.filter(t => t.atr_at_entry).map(t => t.atr_at_entry);
  const avgAtr    = atrValues.length ? atrValues.reduce((a, b) => a + b, 0) / atrValues.length : null;

  const actions   = [];
  const newParams = { ...params };

  // ── Rule 1: Win-rate based SL tuning ────────────────────────────────────
  if (winRate < 40) {
    const oldSl = newParams.stop_loss_pct;
    const newSl = _clamp(oldSl + 0.1, params.min_stop_loss_pct, params.max_stop_loss_pct);
    newParams.stop_loss_pct = Math.round(newSl * 1000) / 1000;
    if (newSl !== oldSl) {
      actions.push({ rule: 'win_rate_sl', detail: `Win rate ${winRate.toFixed(1)}% < 40% → SL ${oldSl}% → ${newSl}%` });
    }
  } else if (winRate > 65 && profitFactor > 1.5) {
    const oldSl = newParams.stop_loss_pct;
    const newSl = _clamp(oldSl - 0.05, params.min_stop_loss_pct, params.max_stop_loss_pct);
    newParams.stop_loss_pct = Math.round(newSl * 1000) / 1000;
    if (newSl !== oldSl) {
      actions.push({ rule: 'win_rate_sl_tight', detail: `Win rate ${winRate.toFixed(1)}% > 65% + PF ${profitFactor.toFixed(2)} → SL ${oldSl}% → ${newSl}%` });
    }
  }

  // ── Rule 2: Volatility-based ATR multiplier ──────────────────────────────
  if (avgAtr !== null) {
    const priceValues = trades.filter(t => t.entry_price).map(t => t.entry_price);
    const avgPrice    = priceValues.length ? priceValues.reduce((a, b) => a + b, 0) / priceValues.length : 1.0;
    const atrPct      = (avgAtr / avgPrice) * 100;
    const oldMult     = newParams.atr_multiplier;

    if (atrPct > 0.5) {
      const newMult = _clamp(oldMult + 0.1, params.min_atr_multiplier, params.max_atr_multiplier);
      newParams.atr_multiplier = Math.round(newMult * 100) / 100;
      if (newMult !== oldMult) {
        actions.push({ rule: 'volatility_high', detail: `ATR% ${atrPct.toFixed(3)}% > 0.5% → widen ATR mult ${oldMult} → ${newMult}` });
      }
    } else if (atrPct < 0.2) {
      const newMult = _clamp(oldMult - 0.1, params.min_atr_multiplier, params.max_atr_multiplier);
      newParams.atr_multiplier = Math.round(newMult * 100) / 100;
      if (newMult !== oldMult) {
        actions.push({ rule: 'volatility_low', detail: `ATR% ${atrPct.toFixed(3)}% < 0.2% → tighten ATR mult ${oldMult} → ${newMult}` });
      }
    }
  }

  // ── Rule 3: EMA length tuning ────────────────────────────────────────────
  if (winRate < 45 && trades.length >= window) {
    const oldFast = newParams.ema_fast;
    const oldSlow = newParams.ema_slow;
    const newFast = Math.round(_clamp(oldFast + 1, params.min_ema_fast, params.max_ema_fast));
    const newSlow = Math.round(_clamp(oldSlow + 2, params.min_ema_slow, params.max_ema_slow));
    newParams.ema_fast = newFast;
    newParams.ema_slow = newSlow;
    if (newFast !== oldFast || newSlow !== oldSlow) {
      actions.push({ rule: 'ema_tune', detail: `EMA signals failing (WR ${winRate.toFixed(1)}%) → EMA ${oldFast}/${oldSlow} → ${newFast}/${newSlow}` });
    }
  }

  // ── Rule 4: TP ratio improvement ─────────────────────────────────────────
  if (profitFactor < 1.0 && winRate >= 40) {
    const oldTp = newParams.take_profit_pct;
    const newTp = _clamp(oldTp + 0.1, params.min_take_profit_pct, params.max_take_profit_pct);
    newParams.take_profit_pct = Math.round(newTp * 1000) / 1000;
    if (newTp !== oldTp) {
      actions.push({ rule: 'tp_improve', detail: `Profit factor ${profitFactor.toFixed(2)} < 1.0 → TP ${oldTp}% → ${newTp}%` });
    }
  }

  // ── Save & log ────────────────────────────────────────────────────────────
  const reason = actions.length ? actions.map(a => a.detail).join('; ') : 'No changes needed';
  const pv     = await crud.saveParams(newParams, reason, 'AUTO');

  await crud.logAdaptation({
    trades_evaluated:   trades.length,
    win_rate:           Math.round(winRate * 100) / 100,
    profit_factor:      Math.round(profitFactor * 1000) / 1000,
    avg_atr:            avgAtr !== null ? Math.round(avgAtr * 1000000) / 1000000 : null,
    actions_taken:      JSON.stringify(actions),
    new_params_version: pv.version,
  });

  logger.info(`Adaptation complete: ${actions.length} changes → version ${pv.version}`);

  return {
    trades_evaluated:   trades.length,
    win_rate:           Math.round(winRate * 100) / 100,
    profit_factor:      Math.round(profitFactor * 1000) / 1000,
    actions,
    new_params_version: pv.version,
    new_params:         newParams,
  };
}

module.exports = { runAdaptation };
