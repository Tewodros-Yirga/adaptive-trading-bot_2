import React, { useState, useEffect, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Eye, EyeOff, RefreshCw, Wifi, WifiOff, AlertTriangle,
  Database, CheckCircle2, XCircle, Loader2,
} from 'lucide-react'
import {
  getBridgeAccount, getSettingsBulk, setSettingsBulk, clearAccountData,
  getCacheStatus, triggerCachePreload, sendTestAlert, getAlertsDiagnostics,
  getStrategies,
} from '../api'
import { Card, SectionHeader, Btn, Input, Select, Spinner, ConfirmModal } from '../components'
import { useAppStore } from '../store'

function SecretInput({ label, value, onChange, placeholder }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string
}) {
  const [show, setShow] = useState(false)
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs text-muted">{label}</label>
      <div className="relative">
        <input type={show ? 'text' : 'password'} value={value} placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          className="w-full bg-bg border border-border rounded px-3 py-1.5 text-sm text-white focus:border-accent outline-none mono pr-8" />
        <button type="button" onClick={() => setShow(!show)}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-muted hover:text-white">
          {show ? <EyeOff size={14} /> : <Eye size={14} />}
        </button>
      </div>
    </div>
  )
}

function useBulk(keys: string[]) {
  const [local, setLocal] = useState<Record<string, string>>({})
  const { data } = useQuery({
    queryKey: ['bulkSettings', ...keys],
    queryFn: () => getSettingsBulk(keys),
    staleTime: 5000,
  })
  useEffect(() => { if (data) setLocal((p) => ({ ...data, ...p })) }, [data])
  const set = (k: string, v: string) => setLocal((s) => ({ ...s, [k]: v }))
  const boolVal = (k: string) => ['1','true','yes','on'].includes(String(local[k] ?? '').toLowerCase())
  return { local, set, boolVal }
}
// <<HELPERS_END>>

// ── API Keys ──────────────────────────────────────────────────────────────────
function ApiKeysCard() {
  const { addToast, isAdmin } = useAppStore()
  const qc = useQueryClient()
  const { local, set } = useBulk(['newsapi_key','alphavantage_key','finnhub_key','groq_api_key','twelve_data_key'])
  const saveMut = useMutation({
    mutationFn: () => setSettingsBulk(local),
    onSuccess: () => { addToast('success','API keys saved'); qc.invalidateQueries({ queryKey: ['bulkSettings'] }) },
    onError: (e: any) => addToast('error', e.message),
  })
  return (
    <Card>
      <SectionHeader title="API Keys" sub="Stored as AppSetting records in the database — not in .env" />
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4 mb-4">
        <SecretInput label="NewsAPI Key (newsapi.org)" value={local['newsapi_key'] ?? ''} onChange={(v) => set('newsapi_key', v)} placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" />
        <SecretInput label="Alpha Vantage Key" value={local['alphavantage_key'] ?? ''} onChange={(v) => set('alphavantage_key', v)} placeholder="XXXXXXXXXXXXXXXX" />
        <SecretInput label="Finnhub Key" value={local['finnhub_key'] ?? ''} onChange={(v) => set('finnhub_key', v)} placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxxxx" />
        <SecretInput label="Groq API Key (AI news sentiment)" value={local['groq_api_key'] ?? ''} onChange={(v) => set('groq_api_key', v)} placeholder="gsk_..." />
        <SecretInput label="Twelve Data Key" value={local['twelve_data_key'] ?? ''} onChange={(v) => set('twelve_data_key', v)} placeholder="xxxxxxxxxxxxxxxxxxxx" />
      </div>
      <Btn size="sm" onClick={() => saveMut.mutate()} disabled={saveMut.isPending || !isAdmin()}>
        {saveMut.isPending ? <Spinner size={12} /> : null} Save API Keys
      </Btn>
    </Card>
  )
}
// <<APIKEYS_END>>

// ── Live Trading Loop ─────────────────────────────────────────────────────────
function LiveTradingCard() {
  const { addToast, isAdmin } = useAppStore()
  const qc = useQueryClient()
  const { local, set } = useBulk(['live_trading_interval_seconds','live_trading_symbols','position_reconciliation_interval_seconds'])
  const saveMut = useMutation({
    mutationFn: () => setSettingsBulk(local),
    onSuccess: () => { addToast('success','Live trading settings saved'); qc.invalidateQueries({ queryKey: ['bulkSettings'] }) },
    onError: (e: any) => addToast('error', e.message),
  })
  return (
    <Card>
      <SectionHeader title="Live Trading Loop" sub="Controls the autonomous signal-to-order loop that runs every N seconds." />
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mt-4 mb-4">
        <div>
          <Input label="Loop interval (seconds)" type="number" value={local['live_trading_interval_seconds'] ?? '60'} onChange={(v) => set('live_trading_interval_seconds', String(v))} min={5} step={5} />
          <p className="text-xs text-muted mt-1">How often signals are evaluated. Default 60s.</p>
        </div>
        <div>
          <Input label="Trading symbols (comma-separated)" value={local['live_trading_symbols'] ?? 'XAUUSD'} onChange={(v) => set('live_trading_symbols', v)} />
          <p className="text-xs text-muted mt-1">XAUUSDc for cent accounts — maps to XAUUSD data automatically.</p>
        </div>
        <div>
          <Input label="Reconciliation interval (seconds)" type="number" value={local['position_reconciliation_interval_seconds'] ?? '120'} onChange={(v) => set('position_reconciliation_interval_seconds', String(v))} min={30} />
          <p className="text-xs text-muted mt-1">Ghost-trade detection loop frequency.</p>
        </div>
      </div>
      <Btn size="sm" onClick={() => saveMut.mutate()} disabled={saveMut.isPending || !isAdmin()}>
        {saveMut.isPending ? <Spinner size={12} /> : null} Save
      </Btn>
    </Card>
  )
}
// <<LIVETRADING_END>>

// ── SL / RR Guardrails ────────────────────────────────────────────────────────
function SlRrCard() {
  const { addToast, isAdmin } = useAppStore()
  const qc = useQueryClient()
  const { local, set } = useBulk(['max_sl_pips','min_rr_ratio_global'])
  const saveMut = useMutation({
    mutationFn: () => setSettingsBulk(local),
    onSuccess: () => { addToast('success','SL/RR guardrails saved'); qc.invalidateQueries({ queryKey: ['bulkSettings'] }) },
    onError: (e: any) => addToast('error', e.message),
  })
  return (
    <Card>
      <SectionHeader title="Stop-Loss Cap & RR Guardrails"
        sub="Applied after the level strategy computes its levels — clamps SL width and enforces minimum reward-to-risk." />
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4 mb-4">
        <div>
          <Input label="Max SL pips (0 = disabled)" type="number" value={local['max_sl_pips'] ?? '150'} onChange={(v) => set('max_sl_pips', String(v))} min={0} step={10} />
          <p className="text-xs text-muted mt-1">Hard ceiling on stop distance. 150 pips on XAUUSD = $15 risk/lot. Prevents the 500-pip stop problem.</p>
        </div>
        <div>
          <Input label="Min RR for TP1 (e.g. 3.0 = 1:3)" type="number" value={local['min_rr_ratio_global'] ?? '3.0'} onChange={(v) => set('min_rr_ratio_global', String(v))} min={1.0} max={10.0} step={0.5} />
          <p className="text-xs text-muted mt-1">TP1 is pushed out until reward / risk meets this minimum. 3.0 = 1:3 RR.</p>
        </div>
      </div>
      <Btn size="sm" onClick={() => saveMut.mutate()} disabled={saveMut.isPending || !isAdmin()}>
        {saveMut.isPending ? <Spinner size={12} /> : null} Save
      </Btn>
    </Card>
  )
}
// <<SLRR_END>>

// ── Score Feedback ────────────────────────────────────────────────────────────
function ScoreFeedbackCard() {
  const { addToast, isAdmin } = useAppStore()
  const qc = useQueryClient()
  const { local, set, boolVal } = useBulk([
    'score_feedback_enabled','score_feedback_alpha','score_feedback_score_bound',
    'score_feedback_weight_gain','score_feedback_weight_floor',
  ])
  const saveMut = useMutation({
    mutationFn: () => setSettingsBulk(local),
    onSuccess: () => { addToast('success','Score feedback saved'); qc.invalidateQueries({ queryKey: ['bulkSettings'] }) },
    onError: (e: any) => addToast('error', e.message),
  })
  return (
    <Card>
      <SectionHeader title="Live Score Feedback"
        sub="At trade close, strategies that voted correctly earn +R; wrong voters lose -R. Live scores tilt ensemble weights without touching backtest results." />
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mt-4 mb-4">
        <label className="flex items-start gap-2 bg-bg border border-border rounded p-2 cursor-pointer">
          <input type="checkbox" className="mt-0.5 accent-accent"
            checked={boolVal('score_feedback_enabled')}
            onChange={(e) => set('score_feedback_enabled', e.target.checked ? 'true' : 'false')} />
          <span>
            <span className="text-sm text-white">Enable score feedback</span>
            <span className="block text-xs text-muted">Reward/penalise strategies based on real trade outcomes.</span>
          </span>
        </label>
        <div>
          <Input label="Alpha (0–1 per trade)" type="number" value={local['score_feedback_alpha'] ?? '0.2'}
            onChange={(v) => set('score_feedback_alpha', String(v))} min={0.01} max={1} step={0.01} />
          <p className="text-xs text-muted mt-1">0.2 = each trade shifts score 20%. Higher = faster learning.</p>
        </div>
        <div>
          <Input label="Score bound (clamp ±)" type="number" value={local['score_feedback_score_bound'] ?? '3.0'}
            onChange={(v) => set('score_feedback_score_bound', String(v))} min={0.5} max={10} step={0.5} />
          <p className="text-xs text-muted mt-1">live_score is clamped to [-bound, +bound].</p>
        </div>
        <div>
          <Input label="Weight gain (amplification)" type="number" value={local['score_feedback_weight_gain'] ?? '0.25'}
            onChange={(v) => set('score_feedback_weight_gain', String(v))} min={0} max={2} step={0.05} />
          <p className="text-xs text-muted mt-1">weight × (1 + gain × live_score). Set 0 to disable blending.</p>
        </div>
        <div>
          <Input label="Weight floor (min multiplier)" type="number" value={local['score_feedback_weight_floor'] ?? '0.1'}
            onChange={(v) => set('score_feedback_weight_floor', String(v))} min={0.01} max={1} step={0.01} />
          <p className="text-xs text-muted mt-1">Prevents a bad strategy from being fully zeroed out.</p>
        </div>
      </div>
      <Btn size="sm" onClick={() => saveMut.mutate()} disabled={saveMut.isPending || !isAdmin()}>
        {saveMut.isPending ? <Spinner size={12} /> : null} Save
      </Btn>
    </Card>
  )
}
// <<SCOREFEEDBACK_END>>

// ── Ensemble & News Intelligence ──────────────────────────────────────────────
function EnsembleSettingsCard() {
  const { addToast, isAdmin } = useAppStore()
  const qc = useQueryClient()
  const { local, set, boolVal } = useBulk([
    'ensemble_voting_threshold',
    'news_signal_trading_enabled','news_signal_bias_threshold','news_signal_confidence_threshold',
    'news_signal_sl_atr_mult','news_signal_tp_atr_mult',
    'news_veto_bias_threshold','news_veto_threshold','news_caution_factor',
    'news_trade_learning_window_hours','news_learning_base_alpha','news_credibility_halflife_days',
  ])
  const saveMut = useMutation({
    mutationFn: () => setSettingsBulk(local),
    onSuccess: () => { addToast('success','Ensemble/news settings saved'); qc.invalidateQueries({ queryKey: ['bulkSettings'] }) },
    onError: (e: any) => addToast('error', e.message),
  })
  return (
    <Card>
      <SectionHeader title="Ensemble & News Intelligence"
        sub="Voting threshold, news veto, news-driven fallback signal, and learning parameters." />
      <div className="mt-4 mb-2 text-xs font-semibold text-white uppercase tracking-wider">Voting</div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
        <div>
          <Input label="Voting threshold (0.10–0.80)" type="number" value={local['ensemble_voting_threshold'] ?? '0.60'}
            onChange={(v) => set('ensemble_voting_threshold', String(v))} min={0.10} max={0.80} step={0.01} />
          <p className="text-xs text-muted mt-1">Min weighted score to fire a trade. Lower = more trades, higher = more selective.</p>
        </div>
      </div>
      <div className="mb-2 text-xs font-semibold text-white uppercase tracking-wider">News Veto</div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
        <div>
          <Input label="Veto bias threshold" type="number" value={local['news_veto_bias_threshold'] ?? '0.5'}
            onChange={(v) => set('news_veto_bias_threshold', String(v))} min={0} max={1} step={0.05} />
          <p className="text-xs text-muted mt-1">Min news bias magnitude to check for a veto.</p>
        </div>
        <div>
          <Input label="Veto confidence threshold" type="number" value={local['news_veto_threshold'] ?? '0.85'}
            onChange={(v) => set('news_veto_threshold', String(v))} min={0} max={1} step={0.05} />
          <p className="text-xs text-muted mt-1">Min confidence to actually block a trade.</p>
        </div>
        <div>
          <Input label="News caution lot factor (0–1)" type="number" value={local['news_caution_factor'] ?? '0.5'}
            onChange={(v) => set('news_caution_factor', String(v))} min={0} max={1} step={0.05} />
          <p className="text-xs text-muted mt-1">Lot multiplied by this when news confidence is high.</p>
        </div>
      </div>
      <div className="mb-2 text-xs font-semibold text-white uppercase tracking-wider">News-Driven Fallback Signal</div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
        <label className="flex items-start gap-2 bg-bg border border-border rounded p-2 cursor-pointer">
          <input type="checkbox" className="mt-0.5 accent-accent"
            checked={boolVal('news_signal_trading_enabled')}
            onChange={(e) => set('news_signal_trading_enabled', e.target.checked ? 'true' : 'false')} />
          <span>
            <span className="text-sm text-white">Enable fallback</span>
            <span className="block text-xs text-muted">Trade from news bias when no strategy fires.</span>
          </span>
        </label>
        <div>
          <Input label="Bias threshold" type="number" value={local['news_signal_bias_threshold'] ?? '0.3'}
            onChange={(v) => set('news_signal_bias_threshold', String(v))} min={0} max={1} step={0.05} />
        </div>
        <div>
          <Input label="Confidence threshold" type="number" value={local['news_signal_confidence_threshold'] ?? '0.5'}
            onChange={(v) => set('news_signal_confidence_threshold', String(v))} min={0} max={1} step={0.05} />
        </div>
        <div>
          <Input label="SL multiplier (x ATR)" type="number" value={local['news_signal_sl_atr_mult'] ?? '1.0'}
            onChange={(v) => set('news_signal_sl_atr_mult', String(v))} min={0.1} max={5} step={0.1} />
        </div>
        <div>
          <Input label="TP multiplier (x ATR)" type="number" value={local['news_signal_tp_atr_mult'] ?? '1.5'}
            onChange={(v) => set('news_signal_tp_atr_mult', String(v))} min={0.1} max={10} step={0.1} />
        </div>
      </div>
      <div className="mb-2 text-xs font-semibold text-white uppercase tracking-wider">News Learning</div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
        <div>
          <Input label="Learning window (hours)" type="number" value={local['news_trade_learning_window_hours'] ?? '4'}
            onChange={(v) => set('news_trade_learning_window_hours', String(v))} min={1} max={48} />
          <p className="text-xs text-muted mt-1">How far back from trade open to look for correlated news items.</p>
        </div>
        <div>
          <Input label="Learning base alpha" type="number" value={local['news_learning_base_alpha'] ?? '0.2'}
            onChange={(v) => set('news_learning_base_alpha', String(v))} min={0.01} max={1} step={0.01} />
          <p className="text-xs text-muted mt-1">EWMA rate for updating news impact weights per trade.</p>
        </div>
        <div>
          <Input label="Source credibility half-life (days)" type="number" value={local['news_credibility_halflife_days'] ?? '14'}
            onChange={(v) => set('news_credibility_halflife_days', String(v))} min={1} max={180} />
          <p className="text-xs text-muted mt-1">Old accuracy data decays; recent outcomes matter more.</p>
        </div>
      </div>
      <Btn size="sm" onClick={() => saveMut.mutate()} disabled={saveMut.isPending || !isAdmin()}>
        {saveMut.isPending ? <Spinner size={12} /> : null} Save
      </Btn>
    </Card>
  )
}
// <<ENSEMBLE_END>>

// ── Position Management (stacking / reversal / duplicate guard) ───────────────
function PositionMgmtCard() {
  const { addToast, isAdmin } = useAppStore()
  const qc = useQueryClient()
  const { local, set } = useBulk([
    'reversal_full_close_confidence','reversal_partial_close_confidence','reversal_partial_close_pct',
    'duplicate_min_confidence','duplicate_min_price_distance_atr',
    'duplicate_min_strategy_count','duplicate_confidence_escalation',
  ])
  const saveMut = useMutation({
    mutationFn: () => setSettingsBulk(local),
    onSuccess: () => { addToast('success','Position management saved'); qc.invalidateQueries({ queryKey: ['bulkSettings'] }) },
    onError: (e: any) => addToast('error', e.message),
  })
  return (
    <Card>
      <SectionHeader title="Position Stacking & Reversal"
        sub="Controls when opposite positions are closed and when same-direction stacking is allowed." />
      <div className="mt-4 mb-2 text-xs font-semibold text-white uppercase tracking-wider">Reversal</div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
        <div>
          <Input label="Full close confidence" type="number" value={local['reversal_full_close_confidence'] ?? '0.80'}
            onChange={(v) => set('reversal_full_close_confidence', String(v))} min={0} max={1} step={0.05} />
          <p className="text-xs text-muted mt-1">Above this, close ALL opposite positions then enter the new direction.</p>
        </div>
        <div>
          <Input label="Partial close confidence" type="number" value={local['reversal_partial_close_confidence'] ?? '0.65'}
            onChange={(v) => set('reversal_partial_close_confidence', String(v))} min={0} max={1} step={0.05} />
          <p className="text-xs text-muted mt-1">Between partial and full: close a fraction of opposite positions.</p>
        </div>
        <div>
          <Input label="Partial close fraction (0–1)" type="number" value={local['reversal_partial_close_pct'] ?? '0.50'}
            onChange={(v) => set('reversal_partial_close_pct', String(v))} min={0.01} max={1} step={0.05} />
          <p className="text-xs text-muted mt-1">Fraction of opposite volume to close. 0.5 = close half.</p>
        </div>
      </div>
      <div className="mb-2 text-xs font-semibold text-white uppercase tracking-wider">Duplicate / Stacking Guard</div>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
        <div>
          <Input label="Min confidence to stack" type="number" value={local['duplicate_min_confidence'] ?? '0.75'}
            onChange={(v) => set('duplicate_min_confidence', String(v))} min={0} max={1} step={0.05} />
          <p className="text-xs text-muted mt-1">Required confidence to add a same-direction position.</p>
        </div>
        <div>
          <Input label="Min distance from existing (x ATR)" type="number" value={local['duplicate_min_price_distance_atr'] ?? '1.0'}
            onChange={(v) => set('duplicate_min_price_distance_atr', String(v))} min={0} max={10} step={0.1} />
          <p className="text-xs text-muted mt-1">New entry must be this many ATRs from any existing position.</p>
        </div>
        <div>
          <Input label="Min agreeing strategies" type="number" value={local['duplicate_min_strategy_count'] ?? '2'}
            onChange={(v) => set('duplicate_min_strategy_count', String(v))} min={1} max={12} step={1} />
        </div>
        <div>
          <Input label="Confidence escalation per stack" type="number" value={local['duplicate_confidence_escalation'] ?? '0.10'}
            onChange={(v) => set('duplicate_confidence_escalation', String(v))} min={0} max={0.5} step={0.01} />
          <p className="text-xs text-muted mt-1">Requirement increases by this amount for each additional stacked position.</p>
        </div>
      </div>
      <Btn size="sm" onClick={() => saveMut.mutate()} disabled={saveMut.isPending || !isAdmin()}>
        {saveMut.isPending ? <Spinner size={12} /> : null} Save
      </Btn>
    </Card>
  )
}
// <<POSMGMT_END>>

// ── TP Ladder & Trailing Stop ─────────────────────────────────────────────────
function TpLadderCard() {
  const { addToast, isAdmin } = useAppStore()
  const qc = useQueryClient()
  const { local, set, boolVal } = useBulk([
    'tp_ladder_enabled','trailing_stop_enabled',
    'breakeven_trigger_pct','breakeven_buffer_pct',
    'trailing_distance_pct','trailing_poll_seconds',
  ])
  const saveMut = useMutation({
    mutationFn: () => setSettingsBulk(local),
    onSuccess: () => { addToast('success','TP ladder / trailing saved'); qc.invalidateQueries({ queryKey: ['bulkSettings'] }) },
    onError: (e: any) => addToast('error', e.message),
  })
  return (
    <Card>
      <SectionHeader title="TP Ladder & Trailing Stop"
        sub="Partial closes at TP1/TP2/TP3 then ATR-trailing on the remainder — mirrors how the backtester simulates exits. Enable one mode only." />
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mt-4 mb-4">
        <label className="flex items-start gap-2 bg-bg border border-border rounded p-2 cursor-pointer">
          <input type="checkbox" className="mt-0.5 accent-accent"
            checked={boolVal('tp_ladder_enabled')}
            onChange={(e) => set('tp_ladder_enabled', e.target.checked ? 'true' : 'false')} />
          <span>
            <span className="text-sm text-white">Enable TP ladder executor</span>
            <span className="block text-xs text-muted">Partial closes + breakeven + ATR trailing. Recommended.</span>
          </span>
        </label>
        <label className="flex items-start gap-2 bg-bg border border-border rounded p-2 cursor-pointer">
          <input type="checkbox" className="mt-0.5 accent-accent"
            checked={boolVal('trailing_stop_enabled')}
            onChange={(e) => set('trailing_stop_enabled', e.target.checked ? 'true' : 'false')} />
          <span>
            <span className="text-sm text-white">Enable % trailing stop</span>
            <span className="block text-xs text-muted">Simple percentage-based trailing. Disable when TP ladder is on.</span>
          </span>
        </label>
        <div>
          <Input label="Break-even trigger (%)" type="number" value={local['breakeven_trigger_pct'] ?? '0.3'}
            onChange={(v) => set('breakeven_trigger_pct', String(v))} min={0.01} max={5} step={0.05} />
          <p className="text-xs text-muted mt-1">Move SL to entry when profit reaches this % of price.</p>
        </div>
        <div>
          <Input label="Break-even buffer (%)" type="number" value={local['breakeven_buffer_pct'] ?? '0.02'}
            onChange={(v) => set('breakeven_buffer_pct', String(v))} min={0} max={1} step={0.01} />
          <p className="text-xs text-muted mt-1">SL = entry + buffer (small cushion above break-even).</p>
        </div>
        <div>
          <Input label="Trailing distance (%)" type="number" value={local['trailing_distance_pct'] ?? '0.4'}
            onChange={(v) => set('trailing_distance_pct', String(v))} min={0.01} max={10} step={0.05} />
          <p className="text-xs text-muted mt-1">How far behind price the trailing stop sits.</p>
        </div>
        <div>
          <Input label="Trailing poll interval (seconds)" type="number" value={local['trailing_poll_seconds'] ?? '15'}
            onChange={(v) => set('trailing_poll_seconds', String(v))} min={5} step={5} />
        </div>
      </div>
      <Btn size="sm" onClick={() => saveMut.mutate()} disabled={saveMut.isPending || !isAdmin()}>
        {saveMut.isPending ? <Spinner size={12} /> : null} Save
      </Btn>
    </Card>
  )
}
// <<TPLADDER_END>>

// ── Pending Limit Orders ──────────────────────────────────────────────────────
function PendingOrdersCard() {
  const { addToast, isAdmin } = useAppStore()
  const qc = useQueryClient()
  const { local, set, boolVal } = useBulk([
    'pending_orders_enabled','pending_entry_min_distance_atr','pending_entry_offset_atr',
    'pending_miss_entry_fraction','pending_order_monitor_interval_seconds',
    'pending_order_max_age_minutes','pending_cancel_on_opposite_signal','pending_cancel_on_opposing_news',
  ])
  const saveMut = useMutation({
    mutationFn: () => setSettingsBulk(local),
    onSuccess: () => { addToast('success','Pending-order settings saved'); qc.invalidateQueries({ queryKey: ['bulkSettings'] }) },
    onError: (e: any) => addToast('error', e.message),
  })
  return (
    <Card>
      <SectionHeader title="Pending Limit Orders"
        sub="Strategy entries can rest as MT5 limit orders with rule-based cancellation. News-driven signals always market-fill." />
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mt-4 mb-4">
        <Select label="Pending orders" value={local['pending_orders_enabled'] ?? 'false'}
          onChange={(v) => set('pending_orders_enabled', v)}
          options={[{ value: 'false', label: 'Disabled (market only)' }, { value: 'true', label: 'Enabled (limit orders)' }]} />
        <div>
          <Input label="Entry offset (x ATR)" type="number" value={local['pending_entry_offset_atr'] ?? '0.5'}
            onChange={(v) => set('pending_entry_offset_atr', String(v))} min={0} step={0.05} />
          <p className="text-xs text-muted mt-1">Pullback distance to place the limit entry.</p>
        </div>
        <div>
          <Input label="Min entry distance (x ATR)" type="number" value={local['pending_entry_min_distance_atr'] ?? '0.25'}
            onChange={(v) => set('pending_entry_min_distance_atr', String(v))} min={0} step={0.05} />
          <p className="text-xs text-muted mt-1">Min favourable gap to justify a limit over a market fill.</p>
        </div>
        <div>
          <Input label="Miss-entry cancel fraction (0–1)" type="number" value={local['pending_miss_entry_fraction'] ?? '0.40'}
            onChange={(v) => set('pending_miss_entry_fraction', String(v))} min={0} max={1} step={0.05} />
          <p className="text-xs text-muted mt-1">Cancel if price travels this fraction of entry→TP1 without filling.</p>
        </div>
        <div>
          <Input label="Monitor interval (seconds)" type="number" value={local['pending_order_monitor_interval_seconds'] ?? '15'}
            onChange={(v) => set('pending_order_monitor_interval_seconds', String(v))} min={5} step={1} />
        </div>
        <div>
          <Input label="Max age auto-cancel (minutes, 0=off)" type="number" value={local['pending_order_max_age_minutes'] ?? '0'}
            onChange={(v) => set('pending_order_max_age_minutes', String(v))} min={0} step={1} />
        </div>
        <label className="flex items-start gap-2 bg-bg border border-border rounded p-2 cursor-pointer">
          <input type="checkbox" className="mt-0.5 accent-accent"
            checked={boolVal('pending_cancel_on_opposite_signal')}
            onChange={(e) => set('pending_cancel_on_opposite_signal', e.target.checked ? 'true' : 'false')} />
          <span className="text-sm text-white">Cancel on opposite ensemble signal</span>
        </label>
        <label className="flex items-start gap-2 bg-bg border border-border rounded p-2 cursor-pointer">
          <input type="checkbox" className="mt-0.5 accent-accent"
            checked={boolVal('pending_cancel_on_opposing_news')}
            onChange={(e) => set('pending_cancel_on_opposing_news', e.target.checked ? 'true' : 'false')} />
          <span className="text-sm text-white">Cancel on opposing news signal</span>
        </label>
      </div>
      <Btn size="sm" onClick={() => saveMut.mutate()} disabled={saveMut.isPending || !isAdmin()}>
        {saveMut.isPending ? <Spinner size={12} /> : null} Save
      </Btn>
    </Card>
  )
}
// <<PENDING_END>>

// ── Automation & Alerts ───────────────────────────────────────────────────────
function AutomationCard() {
  const { addToast, isAdmin } = useAppStore()
  const qc = useQueryClient()
  const { local, set, boolVal } = useBulk([
    'alerts_telegram_bot_token','alerts_telegram_chat_id','alerts_webhook_url',
    'alerts_min_level','alerts_enabled_events','alerts_throttle_seconds',
    'alerts_proxy_url','alerts_telegram_api_base',
    'block_entries_on_bridge_outage','max_spread_pips',
    'use_sse_position_stream','job_queue_enabled',
  ])
  const saveMut = useMutation({
    mutationFn: () => setSettingsBulk(local),
    onSuccess: () => { addToast('success','Automation & alert settings saved'); qc.invalidateQueries({ queryKey: ['bulkSettings'] }) },
    onError: (e: any) => addToast('error', e.message),
  })
  const testMut = useMutation({
    mutationFn: sendTestAlert,
    onSuccess: (res: any) => addToast(res?.sent ? 'success' : 'warning',
      res?.sent ? `Test sent via: ${(res.channels || []).join(', ')}` : (res?.reason || 'No channel configured')),
    onError: (e: any) => addToast('error', e.message),
  })
  const diagMut = useMutation({
    mutationFn: getAlertsDiagnostics,
    onSuccess: (res: any) => addToast(res?.any_route_ok ? 'success' : 'error',
      res?.any_route_ok ? 'At least one delivery route works' : 'No delivery route works from this host'),
    onError: (e: any) => addToast('error', `Diagnostics failed (${e.message})`),
  })
  const BOOL_KEYS = new Set(['block_entries_on_bridge_outage','use_sse_position_stream','job_queue_enabled'])
  return (
    <Card>
      <SectionHeader title="Automation & Alerts" sub="Notifications (Telegram/webhook) and opt-in safety features." />
      <div className="mt-4 mb-2 text-xs font-semibold text-white uppercase tracking-wider">Notifications</div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
        <SecretInput label="Telegram Bot Token" value={local['alerts_telegram_bot_token'] ?? ''} onChange={(v) => set('alerts_telegram_bot_token', v)} placeholder="123456:ABC-DEF..." />
        <Input label="Telegram Chat ID" value={local['alerts_telegram_chat_id'] ?? ''} onChange={(v) => set('alerts_telegram_chat_id', v)} placeholder="123456789" />
        <Input label="Webhook URL (optional)" value={local['alerts_webhook_url'] ?? ''} onChange={(v) => set('alerts_webhook_url', v)} placeholder="https://..." />
        <Select label="Minimum alert level" value={local['alerts_min_level'] ?? 'warning'} onChange={(v) => set('alerts_min_level', v)}
          options={[{ value: 'info', label: 'info' }, { value: 'warning', label: 'warning' }, { value: 'critical', label: 'critical' }]} />
        <Input label="Enabled events (comma-sep, blank=all)" value={local['alerts_enabled_events'] ?? ''} onChange={(v) => set('alerts_enabled_events', v)} placeholder="service_started,bridge_outage" />
        <Input label="Throttle seconds" value={local['alerts_throttle_seconds'] ?? ''} onChange={(v) => set('alerts_throttle_seconds', v)} placeholder="300" />
        <SecretInput label="Outbound proxy (optional)" value={local['alerts_proxy_url'] ?? ''} onChange={(v) => set('alerts_proxy_url', v)} placeholder="http://user:pass@host:port" />
        <Input label="Telegram API base (optional relay)" value={local['alerts_telegram_api_base'] ?? ''} onChange={(v) => set('alerts_telegram_api_base', v)} placeholder="https://my-relay.workers.dev/..." />
      </div>
      <div className="flex gap-2 mb-4">
        <Btn size="sm" variant="ghost" onClick={() => testMut.mutate()} disabled={testMut.isPending}>
          {testMut.isPending ? <Spinner size={12} /> : null} Send test message
        </Btn>
        <Btn size="sm" variant="ghost" onClick={() => diagMut.mutate()} disabled={diagMut.isPending}>
          {diagMut.isPending ? <Spinner size={12} /> : null} Run diagnostics
        </Btn>
      </div>
      <div className="mb-2 text-xs font-semibold text-white uppercase tracking-wider">Safety & Automation</div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
        {[
          { key: 'block_entries_on_bridge_outage', label: 'Block entries on bridge outage', hint: 'Pause new entries when MT5 bridge circuit breaker is OPEN.' },
          { key: 'max_spread_pips', label: 'Max spread (pips, 0=off)', hint: 'Reject entries when live spread exceeds this.' },
          { key: 'use_sse_position_stream', label: 'Use SSE position stream', hint: 'Consume bridge SSE instead of 5s polling.' },
          { key: 'job_queue_enabled', label: 'Enable job queue worker', hint: 'Turn on the DB-backed job queue worker.' },
        ].map((f) => BOOL_KEYS.has(f.key) ? (
          <label key={f.key} className="flex items-start gap-2 bg-bg border border-border rounded p-2 cursor-pointer">
            <input type="checkbox" className="mt-0.5 accent-accent"
              checked={boolVal(f.key)} onChange={(e) => set(f.key, e.target.checked ? 'true' : 'false')} />
            <span>
              <span className="text-sm text-white">{f.label}</span>
              <span className="block text-xs text-muted">{f.hint}</span>
            </span>
          </label>
        ) : (
          <div key={f.key}>
            <Input label={f.label} value={local[f.key] ?? ''} onChange={(v) => set(f.key, v)} />
            <p className="text-xs text-muted mt-0.5">{f.hint}</p>
          </div>
        ))}
      </div>
      <Btn size="sm" onClick={() => saveMut.mutate()} disabled={saveMut.isPending || !isAdmin()}>
        {saveMut.isPending ? <Spinner size={12} /> : null} Save Automation & Alerts
      </Btn>
    </Card>
  )
}
// <<AUTOMATION_END>>

// ── OHLCV Cache ───────────────────────────────────────────────────────────────
const ALL_TIMEFRAMES = ['15m', '1h', '4h', '1d']

function OhlcvCacheCard() {
  const { addToast } = useAppStore()
  const [symbols, setSymbols] = useState('XAUUSD')
  const [selectedTfs, setSelectedTfs] = useState<string[]>(['15m', '1h', '4h', '1d'])
  const [years, setYears] = useState(4)
  const [feedback, setFeedback] = useState<string | null>(null)

  const { data: status, refetch: refetchStatus } = useQuery({
    queryKey: ['ohlcv-cache-status'],
    queryFn: getCacheStatus,
    refetchInterval: 10000,
    staleTime: 0,
  })

  const triggerMut = useMutation({
    mutationFn: () => triggerCachePreload(symbols, selectedTfs.join(','), years),
    onSuccess: (res: any) => { setFeedback(res.message); setTimeout(refetchStatus, 2000) },
    onError: (err: Error) => { setFeedback(`Error: ${err.message}`); addToast('error', err.message) },
  })

  const toggleTf = (tf: string) =>
    setSelectedTfs((prev) => prev.includes(tf) ? prev.filter((t) => t !== tf) : [...prev, tf])

  const isRunning = status?.running ?? false
  const coverage: any[] = status?.coverage ?? []
  const errors: any[] = status?.errors ?? []
  const lastUpd = status?.last_updated

  return (
    <Card>
      <SectionHeader title="OHLCV Cache"
        sub="Pre-load historical price data into MongoDB so Alchemist & MTF strategies fetch instantly." />
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4 mt-4">
        <div className="flex flex-col gap-3">
          <Input label="Symbols (comma-separated)" value={symbols} onChange={setSymbols} placeholder="XAUUSD,EURUSD" />
          <div className="flex flex-col gap-1">
            <label className="text-xs text-muted">Timeframes</label>
            <div className="flex gap-3 flex-wrap">
              {ALL_TIMEFRAMES.map((tf) => (
                <label key={tf} className="flex items-center gap-1.5 cursor-pointer select-none">
                  <input type="checkbox" checked={selectedTfs.includes(tf)} onChange={() => toggleTf(tf)} className="accent-accent w-3.5 h-3.5" />
                  <span className="text-sm text-white">{tf}</span>
                </label>
              ))}
            </div>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs text-muted">History: <strong className="text-white">{years} year{years !== 1 ? 's' : ''}</strong></label>
            <input type="range" min={1} max={10} step={1} value={years} onChange={(e) => setYears(Number(e.target.value))} className="w-full accent-accent" />
          </div>
          <div className="flex items-center gap-3">
            <Btn onClick={() => { setFeedback(null); triggerMut.mutate() }}
              disabled={triggerMut.isPending || isRunning || selectedTfs.length === 0}>
              {triggerMut.isPending || isRunning
                ? <><Loader2 size={14} className="animate-spin" /> {isRunning ? 'Preloading…' : 'Scheduling…'}</>
                : <><Database size={14} /> Trigger Preload</>}
            </Btn>
            <Btn variant="outline" size="sm" onClick={() => refetchStatus()}><RefreshCw size={13} /></Btn>
          </div>
          {feedback && <p className="text-xs text-muted bg-bg border border-border rounded px-2 py-1">{feedback}</p>}
        </div>
        <div className="flex flex-col gap-2">
          <p className="text-xs text-muted">
            {isRunning
              ? <span className="flex items-center gap-1"><Loader2 size={12} className="animate-spin" /> Preload running…</span>
              : lastUpd
                ? <span className="flex items-center gap-1 text-green-400"><CheckCircle2 size={12} /> Last run: {String(lastUpd).slice(0, 19).replace('T', ' ')} UTC</span>
                : <span>No preload run yet.</span>}
          </p>
          {errors.length > 0 && errors.slice(0, 3).map((e: any, i: number) => (
            <p key={i} className="text-xs text-red-300 font-mono truncate">{e}</p>
          ))}
        </div>
      </div>
      {coverage.length > 0 && (
        <div className="overflow-x-auto rounded border border-border">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-bg text-muted">
                {['Symbol', 'TF', 'Bars', 'From', 'To'].map((h) => (
                  <th key={h} className="px-3 py-2 text-left font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {coverage.map((r: any, i: number) => (
                <tr key={i} className="border-t border-border hover:bg-white/5">
                  <td className="px-3 py-1.5 font-mono text-white">{r.symbol}</td>
                  <td className="px-3 py-1.5 text-accent font-mono">{r.timeframe}</td>
                  <td className="px-3 py-1.5 text-green-300">{Number(r.bars).toLocaleString()}</td>
                  <td className="px-3 py-1.5 text-muted">{r.from}</td>
                  <td className="px-3 py-1.5 text-muted">{r.to}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}
// <<OHLCVCACHE_END>>

// ── Backtester Search Settings ────────────────────────────────────────────────
// The strategy list is fetched from the backend registry so any strategy added
// there (new strategy class, registry entry, etc.) shows up here automatically.
// The fallback list is only used while the fetch is pending or unreachable.
const BT_SETTING_SUFFIXES = [
  'backtest_interval_seconds', 'param_step_size',
  'qualify_threshold_win_rate', 'qualify_threshold_profit_factor',
  'score_weight_win_rate', 'score_weight_roi',
  'backtest_timeframes', 'backtest_symbols',
  'range_expansion_months', 'max_history_months',
]
const FALLBACK_BACKTEST_STRATEGIES = ['DTC','RSI_Reversal','MACD_Momentum','Bollinger_Breakout',
  'Multi_EMA_Scalper','VWAP_Reversion','Alchemist','ADX_Regime','OBV_Momentum',
  'StochRSI_Cross','HTF_Structure','Key_Level','Pullback_Sniper','SK_Unified','Ten_AM']

function BacktesterSearchCard() {
  const { addToast } = useAppStore()
  const qc = useQueryClient()
  const [sel, setSel] = useState('DTC')
  const [btSettings, setBtSettings] = useState<Record<string, string>>({})

  const { data: strategyRows } = useQuery({
    queryKey: ['strategies'],
    queryFn: getStrategies,
    staleTime: 60_000,
  })
  const backtestStrategies = useMemo(() => {
    const names = (strategyRows ?? [])
      .map((s: any) => s?.name)
      .filter((n: any): n is string => typeof n === 'string' && n.length > 0)
    return names.length > 0 ? names : FALLBACK_BACKTEST_STRATEGIES
  }, [strategyRows])

  // If the selected strategy is no longer in the live list, fall back to the first one.
  useEffect(() => {
    if (backtestStrategies.length > 0 && !backtestStrategies.includes(sel)) {
      setSel(backtestStrategies[0])
    }
  }, [backtestStrategies, sel])

  const btKey = (suffix: string) => `${sel}_${suffix}`
  const btVal = (suffix: string, def: string) => btSettings[btKey(suffix)] ?? def
  const setBt = (suffix: string, v: string) => setBtSettings((s) => ({ ...s, [btKey(suffix)]: v }))

  const allBtKeys = useMemo(
    () => backtestStrategies.flatMap((s) => BT_SETTING_SUFFIXES.map((sfx) => `${s}_${sfx}`)),
    [backtestStrategies],
  )
  const { data: storedBt } = useQuery({
    queryKey: ['btSettings', allBtKeys.join(',')],
    queryFn: () => getSettingsBulk(allBtKeys),
  })
  useEffect(() => { if (storedBt) setBtSettings(storedBt) }, [storedBt])

  const saveBtMut = useMutation({
    mutationFn: () => {
      const payload: Record<string, string> = {}
      for (const [k, v] of Object.entries(btSettings)) {
        if (k.startsWith(sel + '_')) payload[k] = v
      }
      return setSettingsBulk(payload)
    },
    onSuccess: () => {
      addToast('success', `${sel} backtester settings saved`)
      qc.invalidateQueries({ queryKey: ['btSettings'] })
    },
    onError: (e: any) => addToast('error', e.message),
  })

  return (
    <Card>
      <SectionHeader title="Backtester Search Settings"
        sub="Controls how the backtester microservice searches for optimal strategy parameters." />
      <div className="mb-4 mt-3">
        <label className="text-xs text-muted block mb-2">Strategy</label>
        <div className="flex flex-wrap gap-2">
          {backtestStrategies.map((s) => (
            <button key={s} onClick={() => setSel(s)}
              className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                sel === s ? 'bg-accent text-white' : 'bg-bg border border-border text-muted hover:text-white'
              }`}>
              {s}
            </button>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-4">
        <Input label="Run interval (seconds)" type="number" value={btVal('backtest_interval_seconds','300')} onChange={(v) => setBt('backtest_interval_seconds', String(v))} min={30} max={3600} />
        <Input label="Param step size (0.01–0.5)" type="number" value={btVal('param_step_size','0.05')} onChange={(v) => setBt('param_step_size', String(v))} min={0.01} max={0.5} step={0.01} />
        <Input label="Min win rate to qualify (%)" type="number" value={btVal('qualify_threshold_win_rate','55')} onChange={(v) => setBt('qualify_threshold_win_rate', String(v))} min={30} max={90} step={1} />
        <Input label="Min profit factor to qualify" type="number" value={btVal('qualify_threshold_profit_factor','1.0')} onChange={(v) => setBt('qualify_threshold_profit_factor', String(v))} min={0.5} max={5} step={0.1} />
        <Input label="Win rate score weight (0–1)" type="number" value={btVal('score_weight_win_rate','0.6')} onChange={(v) => setBt('score_weight_win_rate', String(v))} min={0} max={1} step={0.05} />
        <Input label="PF score weight (0–1)" type="number" value={btVal('score_weight_roi','0.4')} onChange={(v) => setBt('score_weight_roi', String(v))} min={0} max={1} step={0.05} />
        <Input label="Months per phase" type="number" value={btVal('range_expansion_months','6')} onChange={(v) => setBt('range_expansion_months', String(v))} min={1} max={24} />
        <Input label="Max history months" type="number" value={btVal('max_history_months','36')} onChange={(v) => setBt('max_history_months', String(v))} min={1} max={120} />
        <Input label="Timeframes (JSON array)" value={btVal('backtest_timeframes','["1h","4h","1d"]')} onChange={(v) => setBt('backtest_timeframes', v)} />
        <Input label="Symbols (JSON array)" value={btVal('backtest_symbols','["XAUUSD"]')} onChange={(v) => setBt('backtest_symbols', v)} />
      </div>
      <Btn onClick={() => saveBtMut.mutate()} disabled={saveBtMut.isPending}>
        {saveBtMut.isPending ? <Spinner size={14} /> : null} Save {sel} Settings
      </Btn>
    </Card>
  )
}
// <<BTSEARCH_END>>

// ── Page ──────────────────────────────────────────────────────────────────────
export default function SystemSettings() {
  return (
    <div className="flex flex-col gap-6 p-4 md:p-6">
      <h1 className="text-2xl font-semibold text-white">System Settings</h1>
      <ApiKeysCard />
      <LiveTradingCard />
      <SlRrCard />
      <ScoreFeedbackCard />
      <EnsembleSettingsCard />
      <PositionMgmtCard />
      <TpLadderCard />
      <PendingOrdersCard />
      <AutomationCard />
      <OhlcvCacheCard />
      <BacktesterSearchCard />
    </div>
  )
}
