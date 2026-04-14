'use strict';
const { DataTypes } = require('sequelize');
const sequelize     = require('./sequelize');

// ── Trade ────────────────────────────────────────────────────────────────────
const Trade = sequelize.define('Trade', {
  id:                 { type: DataTypes.INTEGER, primaryKey: true, autoIncrement: true },
  symbol:             { type: DataTypes.STRING,  allowNull: false },
  direction:          { type: DataTypes.STRING,  allowNull: false },   // BUY / SELL
  entry_price:        { type: DataTypes.FLOAT,   allowNull: false },
  exit_price:         { type: DataTypes.FLOAT,   allowNull: true },
  stop_loss:          { type: DataTypes.FLOAT,   allowNull: true },
  take_profit:        { type: DataTypes.FLOAT,   allowNull: true },
  lot_size:           { type: DataTypes.FLOAT,   defaultValue: 0.01 },
  pnl:                { type: DataTypes.FLOAT,   allowNull: true },
  result:             { type: DataTypes.STRING,  allowNull: true },    // WIN / LOSS / OPEN
  duration_mins:      { type: DataTypes.FLOAT,   allowNull: true },
  atr_at_entry:       { type: DataTypes.FLOAT,   allowNull: true },
  ema_fast_at_entry:  { type: DataTypes.FLOAT,   allowNull: true },
  ema_slow_at_entry:  { type: DataTypes.FLOAT,   allowNull: true },
  params_version:     { type: DataTypes.INTEGER, allowNull: true },
  opened_at:          { type: DataTypes.DATE,    defaultValue: DataTypes.NOW },
  closed_at:          { type: DataTypes.DATE,    allowNull: true },
}, { tableName: 'trades', timestamps: false });


// ── ParameterVersion ─────────────────────────────────────────────────────────
const ParameterVersion = sequelize.define('ParameterVersion', {
  id:          { type: DataTypes.INTEGER, primaryKey: true, autoIncrement: true },
  version:     { type: DataTypes.INTEGER, allowNull: false },
  params_json: { type: DataTypes.TEXT,    allowNull: false },
  reason:      { type: DataTypes.TEXT,    allowNull: true },
  trigger:     { type: DataTypes.STRING,  allowNull: true },   // AUTO / MANUAL / SYSTEM
  created_at:  { type: DataTypes.DATE,    defaultValue: DataTypes.NOW },
}, { tableName: 'parameter_versions', timestamps: false });


// ── AdaptationLog ────────────────────────────────────────────────────────────
const AdaptationLog = sequelize.define('AdaptationLog', {
  id:                 { type: DataTypes.INTEGER, primaryKey: true, autoIncrement: true },
  trades_evaluated:   { type: DataTypes.INTEGER },
  win_rate:           { type: DataTypes.FLOAT },
  profit_factor:      { type: DataTypes.FLOAT },
  avg_atr:            { type: DataTypes.FLOAT,  allowNull: true },
  actions_taken:      { type: DataTypes.TEXT },
  new_params_version: { type: DataTypes.INTEGER },
  evaluated_at:       { type: DataTypes.DATE,   defaultValue: DataTypes.NOW },
}, { tableName: 'adaptation_logs', timestamps: false });


async function initDb() {
  // sync creates tables if they don't exist; safe to run repeatedly
  await sequelize.sync({ alter: false });
}

module.exports = { sequelize, Trade, ParameterVersion, AdaptationLog, initDb };
