'use strict';
/** Simple console logger that matches Python's logging format. */

function _ts() {
  return new Date().toISOString().replace('T', ' ').slice(0, 23);
}

const logger = {
  info:  (msg) => console.log(`${_ts()} [INFO]  ${msg}`),
  warn:  (msg) => console.warn(`${_ts()} [WARN]  ${msg}`),
  error: (msg) => console.error(`${_ts()} [ERROR] ${msg}`),
};

module.exports = logger;
