'use strict';
/**
 * Default starting parameters for the strategy.
 * Loaded into the DB on first run and updated by the adaptation engine.
 */
const DEFAULT_PARAMS = {
  ema_fast:          9,
  ema_slow:          21,
  stop_loss_pct:     0.5,    // % of price
  take_profit_pct:   1.0,    // % of price
  atr_multiplier:    1.5,    // how many ATRs for SL/TP
  lot_size:          0.01,   // micro lot for demo safety

  // Adaptation bounds — never exceed these
  min_stop_loss_pct:    0.2,
  max_stop_loss_pct:    2.0,
  min_take_profit_pct:  0.4,
  max_take_profit_pct:  4.0,
  min_ema_fast:         5,
  max_ema_fast:         20,
  min_ema_slow:         15,
  max_ema_slow:         50,
  min_atr_multiplier:   1.0,
  max_atr_multiplier:   3.0,
};

module.exports = { DEFAULT_PARAMS };
