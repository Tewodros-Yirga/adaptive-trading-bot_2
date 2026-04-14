'use strict';
/**
 * Strategy engine — EMA crossover with ATR-based SL/TP.
 * Works on arrays of closing prices (most recent last).
 */

function _ema(prices, period) {
  if (prices.length < period) return prices.map(() => prices[prices.length - 1]);
  const k = 2 / (period + 1);
  let emaVals = [prices.slice(0, period).reduce((a, b) => a + b, 0) / period];
  for (let i = period; i < prices.length; i++) {
    emaVals.push(prices[i] * k + emaVals[emaVals.length - 1] * (1 - k));
  }
  const padding = new Array(prices.length - emaVals.length).fill(emaVals[0]);
  return [...padding, ...emaVals];
}

function _atr(highs, lows, closes, period = 14) {
  const trs = [];
  for (let i = 1; i < closes.length; i++) {
    trs.push(Math.max(
      highs[i]  - lows[i],
      Math.abs(highs[i]  - closes[i - 1]),
      Math.abs(lows[i]   - closes[i - 1]),
    ));
  }
  if (!trs.length) return 0.001;
  const slice = trs.slice(-period);
  return slice.reduce((a, b) => a + b, 0) / slice.length;
}

/**
 * generateSignal({ closes, highs, lows, params })
 * Returns { signal, ema_fast, ema_slow, atr, stop_loss_price, take_profit_price, current_price }
 */
function generateSignal({ closes, highs, lows, params }) {
  const emaFastPeriod  = parseInt(params.ema_fast      ?? 9);
  const emaSlowPeriod  = parseInt(params.ema_slow      ?? 21);
  const atrMultiplier  = parseFloat(params.atr_multiplier ?? 1.5);
  const slPct          = parseFloat(params.stop_loss_pct  ?? 0.5) / 100;
  const tpPct          = parseFloat(params.take_profit_pct ?? 1.0) / 100;

  const fast = _ema(closes, emaFastPeriod);
  const slow = _ema(closes, emaSlowPeriod);
  const atr  = _atr(highs, lows, closes);

  const price       = closes[closes.length - 1];
  const emaFastNow  = fast[fast.length - 1];
  const emaFastPrev = fast.length >= 2 ? fast[fast.length - 2] : emaFastNow;
  const emaSlowNow  = slow[slow.length - 1];
  const emaSlowPrev = slow.length >= 2 ? slow[slow.length - 2] : emaSlowNow;

  const bullishCross = emaFastPrev <= emaSlowPrev && emaFastNow > emaSlowNow;
  const bearishCross = emaFastPrev >= emaSlowPrev && emaFastNow < emaSlowNow;

  let signal, sl, tp;
  if (bullishCross) {
    signal = 'BUY';
    sl = price - Math.max(atr * atrMultiplier, price * slPct);
    tp = price + Math.max(atr * atrMultiplier * 1.5, price * tpPct);
  } else if (bearishCross) {
    signal = 'SELL';
    sl = price + Math.max(atr * atrMultiplier, price * slPct);
    tp = price - Math.max(atr * atrMultiplier * 1.5, price * tpPct);
  } else {
    signal = 'HOLD';
    sl = 0.0;
    tp = 0.0;
  }

  const r = (v, d = 5) => Math.round(v * 10 ** d) / 10 ** d;
  return {
    signal,
    ema_fast:         r(emaFastNow),
    ema_slow:         r(emaSlowNow),
    atr:              r(atr),
    stop_loss_price:  r(sl),
    take_profit_price: r(tp),
    current_price:    r(price),
  };
}

module.exports = { generateSignal };
