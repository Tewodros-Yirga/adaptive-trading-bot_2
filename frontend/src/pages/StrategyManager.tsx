import React, { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ChevronDown, ChevronRight, Eye, EyeOff, Sliders, RefreshCw,
  TrendingUp, AlertCircle, Lock, RotateCcw,
} from 'lucide-react'
import clsx from 'clsx'
import {
  getStrategies, activateStrategy, deactivateStrategy,
  updateStrategyParams, getStrategyParamsHistory,
  getEnsembleWeights, resetEnsembleWeights, setEnsembleSuspended,
  getVoterSnapshot, getSettingsBulk, setSettingsBulk,
} from '../api'
import { Card, SectionHeader, Btn, Badge, Spinner, Input, Select, ConfirmModal } from '../components'
import { useAppStore } from '../store'

// ── Helpers ──────────────────────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  ACTIVE:   'bg-success/20 text-success',
  INACTIVE: 'bg-muted/20 text-muted',
}

function stratStatus(s: any): string {
  return s.is_active ? 'ACTIVE' : 'INACTIVE'
}

// ── Strategy Row ──────────────────────────────────────────────────────────────

function StrategyRow({ strategy, suspended }: { strategy: any; suspended: string[] }) {
  const [expanded, setExpanded] = useState(false)
  const [params, setParams] = useState<Record<string, any>>(strategy.params ?? {})
  const [historyOpen, setHistoryOpen] = useState(false)
  const { addToast, isAdmin } = useAppStore()
  const qc = useQueryClient()

  const activateMut = useMutation({
    mutationFn: () => activateStrategy(strategy.name),
    onSuccess: () => { addToast('success', `${strategy.name} activated`); qc.invalidateQueries({ queryKey: ['strategies'] }) },
    onError: (e: any) => addToast('error', e.message),
  })
  const deactivateMut = useMutation({
    mutationFn: () => deactivateStrategy(strategy.name),
    onSuccess: () => { addToast('info', `${strategy.name} deactivated`); qc.invalidateQueries({ queryKey: ['strategies'] }) },
    onError: (e: any) => addToast('error', e.message),
  })
  const saveMut = useMutation({
    mutationFn: () => updateStrategyParams(strategy.name, params),
    onSuccess: () => { addToast('success', 'Parameters saved'); qc.invalidateQueries({ queryKey: ['strategies'] }) },
    onError: (e: any) => addToast('error', e.message),
  })

  const { data: history } = useQuery({
    queryKey: ['stratParamsHistory', strategy.name],
    queryFn: () => getStrategyParamsHistory(strategy.name),
    enabled: historyOpen,
  })

  const isSuspended = suspended.includes(strategy.name)
  const status = stratStatus(strategy)
  const liveScore = strategy.live_score

  return (
    <div className="border border-border rounded-lg overflow-hidden mb-2">
      {/* Header row */}
      <div
        className="flex items-center gap-4 px-4 py-3 cursor-pointer hover:bg-white/3 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <span className="text-muted">{expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={clsx('text-sm font-medium', isSuspended && 'line-through text-muted')}>
              {strategy.display_name ?? strategy.name}
            </span>
            <Badge label={status} color={STATUS_COLORS[status]} />
            {isSuspended && <Badge label="SUSPENDED" color="bg-danger/20 text-danger" />}
            {strategy.live_timeframe && (
              <span className="text-xs text-muted px-1.5 py-0.5 bg-bg border border-border rounded">{strategy.live_timeframe}</span>
            )}
          </div>
          {strategy.description && (
            <p className="text-xs text-muted mt-0.5 truncate">{strategy.description}</p>
          )}
        </div>
        {/* Stats */}
        <div className="hidden md:flex items-center gap-5 text-xs text-muted">
          <span>Win Rate <span className="text-white mono">
            {strategy.win_rate != null ? `${(strategy.win_rate * 100).toFixed(1)}%` : '—'}
          </span></span>
          <span>PF <span className="text-white mono">{strategy.profit_factor?.toFixed(2) ?? '—'}</span></span>
          <span>Trades <span className="text-white mono">{strategy.total_trades ?? 0}</span></span>
          {liveScore != null && (
            <span>Live Score <span className={clsx('mono font-bold', liveScore >= 0 ? 'text-success' : 'text-danger')}>
              {liveScore >= 0 ? '+' : ''}{liveScore.toFixed(3)}
            </span></span>
          )}
        </div>
        {/* Action buttons */}
        <div className="flex items-center gap-2 ml-2" onClick={(e) => e.stopPropagation()}>
          {strategy.is_active ? (
            <Btn size="sm" variant="ghost" onClick={() => deactivateMut.mutate()} disabled={deactivateMut.isPending || !isAdmin()}>
              <EyeOff size={12} /> Deactivate
            </Btn>
          ) : (
            <Btn size="sm" variant="outline" onClick={() => activateMut.mutate()} disabled={activateMut.isPending || !isAdmin()}>
              <Eye size={12} /> Activate
            </Btn>
          )}
        </div>
      </div>

      {/* Expanded body */}
      {expanded && (
        <div className="border-t border-border bg-bg/50 px-4 py-4 space-y-4">
          {/* Mobile stats */}
          <div className="flex md:hidden flex-wrap gap-4 text-xs text-muted pb-2 border-b border-border">
            <span>Win Rate <span className="text-white mono">{strategy.win_rate != null ? `${(strategy.win_rate * 100).toFixed(1)}%` : '—'}</span></span>
            <span>PF <span className="text-white mono">{strategy.profit_factor?.toFixed(2) ?? '—'}</span></span>
            <span>Trades <span className="text-white mono">{strategy.total_trades ?? 0}</span></span>
            {liveScore != null && (
              <span>Live Score <span className={clsx('mono font-bold', liveScore >= 0 ? 'text-success' : 'text-danger')}>
                {liveScore >= 0 ? '+' : ''}{liveScore.toFixed(3)}
              </span></span>
            )}
          </div>

          {/* Parameters */}
          <div>
            <div className="flex items-center gap-2 mb-3">
              <Sliders size={14} className="text-muted" />
              <span className="text-xs text-muted font-medium uppercase tracking-wider">Parameters</span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
              {Object.entries(params).map(([k, v]) => {
                // Skip internal/meta keys
                if (['min_', 'max_'].some(p => k.startsWith(p))) return null
                const isFloat = k.includes('pct') || k.includes('multiplier') || k.includes('ratio') || k.includes('buffer') || k.includes('threshold')
                return (
                  <Input
                    key={k}
                    label={k.replace(/_/g, ' ')}
                    type="number"
                    value={v as number}
                    onChange={(val) => setParams((p) => ({ ...p, [k]: Number(val) }))}
                    step={isFloat ? 0.01 : 1}
                  />
                )
              })}
            </div>
            <div className="flex gap-2 mt-4">
              <Btn size="sm" onClick={() => saveMut.mutate()} disabled={saveMut.isPending || !isAdmin()}>
                {saveMut.isPending ? <Spinner size={12} /> : null} Save Params
              </Btn>
              <Btn size="sm" variant="ghost" onClick={() => setParams(strategy.params ?? {})}>
                Reset
              </Btn>
              <Btn size="sm" variant="ghost" onClick={() => setHistoryOpen(!historyOpen)}>
                {historyOpen ? 'Hide History' : 'Show History'}
              </Btn>
            </div>
          </div>

          {/* Param history */}
          {historyOpen && (
            <div>
              <p className="text-xs text-muted font-medium uppercase tracking-wider mb-2">Parameter History</p>
              {!history?.length ? (
                <p className="text-xs text-muted">No history yet.</p>
              ) : (
                <div className="space-y-1 max-h-48 overflow-y-auto">
                  {history.slice(0, 10).map((h: any) => (
                    <div key={h.id ?? h.version} className="flex items-center justify-between text-xs border border-border rounded px-3 py-2">
                      <span className="text-muted">v{h.version}</span>
                      <span className="text-muted">{h.reason ?? '—'}</span>
                      <span className="text-muted">{h.created_at ? new Date(h.created_at).toLocaleDateString() : '—'}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Ensemble Weights Panel ──────────────────────────────────────────────────

function EnsembleWeightsPanel() {
  const { addToast, isAdmin } = useAppStore()
  const qc = useQueryClient()

  const { data: weightsData, isLoading } = useQuery({
    queryKey: ['ensembleWeights'],
    queryFn: getEnsembleWeights,
    refetchInterval: 15000,
  })
  const { data: voterSnap } = useQuery({
    queryKey: ['voterSnapshot'],
    queryFn: getVoterSnapshot,
    refetchInterval: 15000,
  })

  const [thresholdLocal, setThresholdLocal] = useState<string | null>(null)
  const [suspendedLocal, setSuspendedLocal] = useState<string[] | null>(null)

  React.useEffect(() => {
    if (weightsData && thresholdLocal === null) {
      setThresholdLocal(String(weightsData.voting_threshold ?? 0.60))
    }
    if (weightsData && suspendedLocal === null) {
      setSuspendedLocal(weightsData.suspended_strategies ?? [])
    }
  }, [weightsData])

  const { data: thresholdSetting } = useQuery({
    queryKey: ['ensembleThresholdSetting'],
    queryFn: () => getSettingsBulk(['ensemble_voting_threshold']),
  })
  React.useEffect(() => {
    if (thresholdSetting?.ensemble_voting_threshold && thresholdLocal === null) {
      setThresholdLocal(thresholdSetting.ensemble_voting_threshold)
    }
  }, [thresholdSetting])

  const saveThresholdMut = useMutation({
    mutationFn: () => setSettingsBulk({ ensemble_voting_threshold: thresholdLocal ?? '0.60' }),
    onSuccess: () => { addToast('success', 'Voting threshold saved'); qc.invalidateQueries({ queryKey: ['voterSnapshot', 'ensembleWeights'] }) },
    onError: (e: any) => addToast('error', e.message),
  })

  const saveSuspendedMut = useMutation({
    mutationFn: () => setEnsembleSuspended(suspendedLocal ?? []),
    onSuccess: () => { addToast('success', 'Suspended strategies updated'); qc.invalidateQueries({ queryKey: ['voterSnapshot', 'ensembleWeights'] }) },
    onError: (e: any) => addToast('error', e.message),
  })

  const resetMut = useMutation({
    mutationFn: resetEnsembleWeights,
    onSuccess: () => { addToast('success', 'Weights reset to equal defaults'); qc.invalidateQueries({ queryKey: ['voterSnapshot', 'ensembleWeights'] }) },
    onError: (e: any) => addToast('error', e.message),
  })

  if (isLoading) return <div className="flex justify-center py-8"><Spinner /></div>

  const weights = voterSnap?.normalized_weights ?? weightsData?.weights ?? {}
  const suspended = voterSnap?.suspended_strategies ?? weightsData?.suspended_strategies ?? []
  const sortedEntries = Object.entries(weights).sort(([, a], [, b]) => (b as number) - (a as number))
  const maxW = sortedEntries.length ? Math.max(...sortedEntries.map(([, v]) => v as number)) : 1

  const allStratNames = sortedEntries.map(([n]) => n)

  return (
    <div className="space-y-6">
      {/* Current weights visual */}
      <Card>
        <div className="flex items-center justify-between mb-4">
          <SectionHeader title="Active Ensemble Weights" sub="Computed from live win-rate, profit factor, and backtest score. Read-only — updated by the optimizer." />
          <Btn size="sm" variant="ghost" onClick={() => resetMut.mutate()} disabled={resetMut.isPending || !isAdmin()}>
            {resetMut.isPending ? <Spinner size={12} /> : <RotateCcw size={12} />} Reset to Defaults
          </Btn>
        </div>

        {weightsData?.using_defaults && (
          <div className="flex items-center gap-2 text-xs text-warn bg-warn/10 border border-warn/20 rounded px-3 py-2 mb-4">
            <AlertCircle size={13} />
            <span>Using equal default weights — the optimizer hasn't run yet or weights were reset.</span>
          </div>
        )}

        <div className="space-y-2">
          {sortedEntries.map(([name, w]) => {
            const pct = ((w as number) * 100).toFixed(1)
            const barW = Math.round(((w as number) / maxW) * 100)
            const isSusp = suspended.includes(name)
            const isAlch = name === 'Alchemist'
            return (
              <div key={name}>
                <div className="flex items-center justify-between mb-0.5">
                  <span className={clsx('text-xs', isSusp ? 'line-through text-muted' : isAlch ? 'text-warn' : 'text-white')}>
                    {name}
                    {isAlch && <Lock size={9} className="inline ml-1 text-warn/60" />}
                    {isSusp && <span className="ml-1 text-danger not-[.no-under]:no-underline">●</span>}
                  </span>
                  <span className={clsx('text-xs font-bold mono', isAlch ? 'text-warn' : 'text-muted')}>{pct}%</span>
                </div>
                <div className="h-1.5 bg-bg rounded-sm overflow-hidden">
                  <div
                    className={clsx('h-full rounded-sm transition-all', isSusp ? 'bg-border' : isAlch ? 'bg-warn' : 'bg-accent')}
                    style={{ width: `${barW}%` }}
                  />
                </div>
              </div>
            )
          })}
        </div>

        {weightsData?.saved_at && (
          <p className="text-xs text-muted mt-4">Last updated by optimizer: {new Date(weightsData.saved_at).toLocaleString()}</p>
        )}
      </Card>

      {/* Voting threshold */}
      <Card>
        <SectionHeader title="Voting Threshold" sub="Minimum weighted buy or sell score needed for the ensemble to fire a trade." />
        <div className="flex items-center gap-4 mt-4">
          <div className="w-48">
            <Input
              label="Threshold (0.10 – 0.80)"
              type="number"
              value={thresholdLocal ?? '0.60'}
              onChange={setThresholdLocal}
              min={0.10}
              max={0.80}
              step={0.01}
            />
          </div>
          <div className="mt-5">
            <Btn size="sm" onClick={() => saveThresholdMut.mutate()} disabled={saveThresholdMut.isPending || !isAdmin()}>
              {saveThresholdMut.isPending ? <Spinner size={12} /> : null} Save
            </Btn>
          </div>
        </div>
        <p className="text-xs text-muted mt-2">
          Current live threshold: <span className="text-white mono">{voterSnap?.threshold ?? '—'}</span>
          {voterSnap?.threshold != null && <span className="ml-2 text-muted">({(voterSnap.threshold * 100).toFixed(0)}%)</span>}
        </p>
      </Card>

      {/* Manual suspension override */}
      <Card>
        <SectionHeader
          title="Strategy Suspension Override"
          sub="Suspended strategies contribute 0 weight. The optimizer auto-suspends below 40% win-rate. You can override manually here."
        />
        <div className="mt-4 flex flex-wrap gap-2">
          {allStratNames.map((name) => {
            const isSusp = (suspendedLocal ?? suspended).includes(name)
            return (
              <button
                key={name}
                onClick={() => {
                  const cur = suspendedLocal ?? suspended
                  setSuspendedLocal(isSusp ? cur.filter((n) => n !== name) : [...cur, name])
                }}
                className={clsx(
                  'px-3 py-1 rounded text-xs font-medium border transition-colors',
                  isSusp
                    ? 'border-danger/50 bg-danger/10 text-danger'
                    : 'border-border text-muted hover:text-white hover:border-accent',
                )}
              >
                {isSusp ? '● ' : ''}{name}
              </button>
            )
          })}
        </div>
        <div className="flex gap-2 mt-4">
          <Btn size="sm" onClick={() => saveSuspendedMut.mutate()} disabled={saveSuspendedMut.isPending || !isAdmin()}>
            {saveSuspendedMut.isPending ? <Spinner size={12} /> : null} Save Suspension List
          </Btn>
          <Btn size="sm" variant="ghost" onClick={() => setSuspendedLocal(suspended)}>Reset</Btn>
        </div>
      </Card>
    </div>
  )
}

// ── Main Page ──────────────────────────────────────────────────────────────────

export default function StrategyManager() {
  const [tab, setTab] = useState<'strategies' | 'ensemble'>('strategies')
  const qc = useQueryClient()

  const { data: strategies, isLoading } = useQuery({
    queryKey: ['strategies'],
    queryFn: getStrategies,
    refetchInterval: 30000,
  })
  const { data: weightsData } = useQuery({
    queryKey: ['ensembleWeights'],
    queryFn: getEnsembleWeights,
    refetchInterval: 30000,
  })

  const suspended: string[] = weightsData?.suspended_strategies ?? []

  const sorted = [...(strategies ?? [])].sort((a: any, b: any) => {
    if (a.is_active !== b.is_active) return a.is_active ? -1 : 1
    return 0
  })

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <SectionHeader title="Strategy Manager" sub="Activate, configure, and tune every trading strategy" />
        <Btn variant="ghost" size="sm" onClick={() => { qc.invalidateQueries({ queryKey: ['strategies'] }); qc.invalidateQueries({ queryKey: ['ensembleWeights'] }) }}>
          <RefreshCw size={13} />
        </Btn>
      </div>

      <div className="flex gap-1 border-b border-border pb-0">
        {(['strategies', 'ensemble'] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={clsx(
              'px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px',
              tab === t ? 'border-accent text-accent' : 'border-transparent text-muted hover:text-white',
            )}
          >
            {t === 'strategies' ? 'Strategies' : 'Ensemble Weights'}
          </button>
        ))}
      </div>

      {tab === 'strategies' && (
        <div>
          {isLoading ? (
            <div className="flex justify-center py-16"><Spinner size={32} /></div>
          ) : !sorted.length ? (
            <Card className="text-center py-12 text-muted text-sm">No strategies found.</Card>
          ) : (
            sorted.map((s: any) => (
              <StrategyRow key={s.name} strategy={s} suspended={suspended} />
            ))
          )}
        </div>
      )}

      {tab === 'ensemble' && <EnsembleWeightsPanel />}
    </div>
  )
}
