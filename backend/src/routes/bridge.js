'use strict';
/**
 * GET  /bridge/account   — live account balance from MT bridge
 * GET  /bridge/positions — open positions from MT bridge
 */
const { Router } = require('express');
const broker     = require('../engine/broker');

const router = Router();

router.get('/bridge/account', async (_req, res) => {
  const data = await broker.getAccountInfo();
  return res.json(data);
});

router.get('/bridge/positions', async (_req, res) => {
  const data = await broker.getOpenPositions();
  return res.json(data);
});

module.exports = router;
