'use strict';
/**
 * GET  /params         — current active parameters
 * POST /params         — manual override (merges with current)
 * GET  /params/history — all parameter versions
 */
const { Router } = require('express');
const crud               = require('../db/crud');
const { DEFAULT_PARAMS } = require('../engine/parameters');

const router = Router();

router.get('/params', async (_req, res) => {
  const params = await crud.getCurrentParams();
  return res.json(params || DEFAULT_PARAMS);
});

router.post('/params', async (req, res) => {
  const current = (await crud.getCurrentParams()) || { ...DEFAULT_PARAMS };
  const merged  = { ...current, ...req.body };
  const pv      = await crud.saveParams(merged, 'Manual override via API', 'MANUAL');
  return res.json({ version: pv.version, params: merged });
});

router.get('/params/history', async (req, res) => {
  const limit   = Math.min(parseInt(req.query.limit || '30', 10), 100);
  const history = await crud.getParamsHistory(limit);
  return res.json(history.map(pv => ({
    version:    pv.version,
    params:     JSON.parse(pv.params_json),
    reason:     pv.reason,
    trigger:    pv.trigger,
    created_at: pv.created_at ? new Date(pv.created_at).toISOString() : null,
  })));
});

module.exports = router;
