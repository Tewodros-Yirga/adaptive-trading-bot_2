'use strict';
require('dotenv').config();

module.exports = {
  META_API_TOKEN:      process.env.META_API_TOKEN      || '',
  META_ACCOUNT_ID:     process.env.META_ACCOUNT_ID     || '',
  WEBHOOK_SECRET:      process.env.WEBHOOK_SECRET      || 'changeme',
  SYMBOL:              process.env.SYMBOL              || 'XAUUSDm',
  ADAPTATION_INTERVAL: parseInt(process.env.ADAPTATION_INTERVAL || '20', 10),
  SIMULATION_MODE:     (process.env.SIMULATION_MODE    || 'true').toLowerCase() === 'true',
  DATABASE_URL:        process.env.DATABASE_URL        || 'postgresql://postgres:password@localhost:5432/trading_bot',

  // Custom MT4/MT5 Bridge
  MT_BRIDGE_URL:       process.env.MT_BRIDGE_URL       || 'http://localhost:5555',
  MT_BRIDGE_SECRET:    process.env.MT_BRIDGE_SECRET    || 'bridge_secret_token',
};
