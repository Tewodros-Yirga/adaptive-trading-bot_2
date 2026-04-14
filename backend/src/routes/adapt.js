'use strict';
/**
 * POST /adapt/run  — manually trigger adaptation
 * GET  /adapt/log  — adaptation history
 */
const { Router }        = require('express');
const { AdaptationLog } = require('../db/models');
const { runAdaptation } = require('../engine/adaptation');

const router = Router();

router.post('/adapt/run', async (req, res) => {
  const window = Math.min(Math.max(parseInt(req.query.window || '20', 10), 5), 200);
  const result = await runAdaptation(window);
  return res.json(result);
});

router.get('/adapt/log', async (req, res) => {
  const limit = Math.min(Math.max(parseInt(req.query.limit || '20', 10), 1), 100);
  const logs  = await AdaptationLog.findAll({
    order: [['evaluated_at', 'DESC']],
    limit,
  });
  return res.json(logs.map(log => ({
    id:                 log.id,
    trades_evaluated:   log.trades_evaluated,
    win_rate:           log.win_rate,
    profit_factor:      log.profit_factor,
    avg_atr:            log.avg_atr,
    actions:            log.actions_taken ? JSON.parse(log.actions_taken) : [],
    new_params_version: log.new_params_version,
    evaluated_at:       log.evaluated_at ? new Date(log.evaluated_at).toISOString() : null,
  })));
});

module.exports = router;
