'use strict';
const { Sequelize } = require('sequelize');
const config        = require('../config');
const logger        = require('../logger');

let sequelize;

try {
  // Parse the DATABASE_URL manually so we handle both
  // postgresql:// and plain connection strings robustly
  const dbUrl = new URL(config.DATABASE_URL);

  const isLocalhost = ['localhost','127.0.0.1','::1'].includes(dbUrl.hostname);
  const sslOptions  = isLocalhost
    ? false
    : { require: true, rejectUnauthorized: false }; // Supabase / Neon / Railway

  sequelize = new Sequelize({
    dialect:  'postgres',
    host:     dbUrl.hostname,
    port:     Number(dbUrl.port) || 5432,
    database: dbUrl.pathname.replace(/^\//, ''),
    username: decodeURIComponent(dbUrl.username),
    password: decodeURIComponent(dbUrl.password),
    dialectOptions: sslOptions ? { ssl: sslOptions } : {},
    logging: (msg) => logger.info(`[SQL] ${msg}`),
    pool: { max: 5, min: 0, acquire: 30000, idle: 10000 },
  });
} catch (urlErr) {
  logger.error(
    `DATABASE_URL is not a valid PostgreSQL URL: "${config.DATABASE_URL}"\n` +
    `Expected format: postgresql://user:password@host:5432/database\n` +
    `Error: ${urlErr.message}`
  );
  process.exit(1);
}

module.exports = sequelize;
