import React, { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, Radio, Eye, EyeOff, Zap, Sliders } from 'lucide-react'
import clsx from 'clsx'
import {
  getStrategies, activateStrategy, deactivateStrategy, setStrategyLive,
  updateStrategyParams, getEnsembleConfig, updateEnsembleConfig,
} from '../api'
import {
  Card, SectionHeader, Btn, Badge, Spinner, Input, Select, ConfirmModal,
} from '../components'
import { useAppStore } from '../store'

const STATUS_COLORS: Record<string, string> = {
  LIVE: 'bg-success/20 text-success',
  SHADOW: 'bg-accent/20 text-accent',
  INACTIVE: 'bg-muted/20 text-muted',
}

function StrategyRow({ strategy, onSetLive }: { strategy: any; onSetLive: (name: string) => void }) {
  const [expanded, setExpanded] = useState(false)
  const [params, setParams] = useState<Record<string, any>>(strategy.params_json ?? {})
  const { addToast } = useAppStore()
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
    onSuccess: () => { addToast('success', 'Params saved'); qc.invalidateQueries({ queryKey: ['strategies'] }) },
    onError: (e: any) => addToast('error', e.message),
  })

  const status = strategy.is_live ? 'LIVE' : strategy.is_active ? 'SHADOW' : 'INACTIVE'

  return (
    <div className="border border-border rounded-lg overflow-hidden mb-2">
      <div
        className="flex items-center gap-4 px-4 py-3 cursor-pointer hover:bg-white/3 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <span className="text-muted">{expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">{strategy.display_name ?? strategy.name}</span>
            <Badge label={status} color={STATUS_COLORS[status]} />
          </div>
          {strategy.description && (
            <p className="text-xs text-muted mt-0.5 truncate">{strategy.description}</p>
          )}
        </div>
        <div className="hidden md:flex items-center gap-6 text-xs text-muted">
          <span>Win Rate <span className="text-white mono">{strategy.win_rate != null ? `${(strategy.win_rate * 100).toFixed(1)}%` : '—'}</span></span>
          <span>PF <span className="text-white mono">{strategy.profit_factor?.toFixed(2) ?? '—'}</span></span>
          <span>Trades <span className="text-white mono">{strategy.total_trades ?? 0}</span></span>
        </div>
        <div className="flex items-center gap-2 ml-2" onClick={(e) => e.stopPropagation()}>
          {!strategy.is_live && strategy.is_active && (
            <Btn size="sm" variant="success" onClick={() => onSetLive(strategy.name)}>
              <Radio size={12} /> Set Live
            </Btn>
          )}
          {strategy.is_active && !strategy.is_live && (
            <Btn size="sm" variant="ghost" onClick={() => deactivateMut.mutate()} disabled={deactivateMut.isPending}>
              <EyeOff size={12} /> Deactivate
            </Btn>
          )}
          {!strategy.is_active && (
            <Btn size="sm" variant="outline" onClick={() => activateMut.mutate()} disabled={activateMut.isPending}>
              <Eye size={12} /> Activate
            </Btn>
          )}
        </div>
      </div>

      {expanded && (
        <div className="border-t border-border bg-bg/50 px-4 py-4">
          <div className="flex items-center gap-2 mb-3">
            <Sliders size={14} className="text-muted" />
            <span className="text-xs text-muted font-medium uppercase tracking-wider">Parameters</span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
            {Object.entries(params).map(([k, v]) => (
              <Input
                key={k}
                label={k.replace(/_/g, ' ')}
                type="number"
                value={v as number}
                onChange={(val) => setParams((p) => ({ ...p, [k]: val }))}
                step={k.includes('pct') || k.includes('multiplier') ? 0.01 : 1}
              />
            ))}
          </div>
          <div className="flex gap-2 mt-4">
            <Btn size="sm" onClick={() => saveMut.mutate()} disabled={saveMut.isPending}>
              {saveMut.isPending ? <Spinner size={12} /> : null}
              Save Params
            </Btn>
            <Btn size="sm" variant="ghost" onClick={() => setParams(strategy.params_json ?? {})}>
              Reset
            </Btn>
          </div>
        </div>
      )}
    </div>
  )
}

function EnsemblePanel() {
  const { addToast } = useAppStore()
  const qc = useQueryClient()
  const { data: cfg, isLoading } = useQuery({ queryKey: ['ensembleConfig'], queryFn: getEnsembleConfig })
  const [local, setLocal] = useState<any>(null)

  React.useEffect(() => { if (cfg && !local) setLocal(cfg) }, [cfg])

  const saveMut = useMutation({
    mutationFn: () => updateEnsembleConfig(local),
    onSuccess: () => { addToast('success', 'Ensemble config saved'); qc.invalidateQueries({ queryKey: ['ensembleConfig'] }) },
    onError: (e: any) => addToast('error', e.message),
  })

  if (isLoading) return <div className="flex justify-center py-8"><Spinner /></div>
  if (!local) return null

  const modeOptions = [
    { value: 'DOMINANT', label: 'Dominant' },
    { value: 'WEIGHTED_VOTE', label: 'Weighted Vote' },
    { value: 'UNANIMOUS', label: 'Unanimous' },
  ]

  return (
    <Card>
      <SectionHeader title="Ensemble Configuration" sub="How multiple strategy signals combine into one trading decision" />
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
        <Select
          label="Combination Mode"
          value={local.mode ?? 'DOMINANT'}
          onChange={(v) => setLocal((c: any) => ({ ...c, mode: v }))}
          options={modeOptions}
        />
        {local.mode === 'DOMINANT' && (
          <Input
            label="Min Confirmations Required"
            type="number"
            value={local.min_confirmations ?? 1}
            onChange={(v) => setLocal((c: any) => ({ ...c, min_confirmations: v }))}
            min={1}
            max={10}
          />
        )}
      </div>

      {local.strategy_weights && (
        <div>
          <p className="text-xs text-muted mb-3">Per-strategy weights (Direction / Entry / SL / TP)</p>
          <div className="space-y-2">
            {Object.entries(local.strategy_weights).map(([name, w]: [string, any]) => (
              <div key={name} className="grid grid-cols-5 gap-2 items-center">
                <span className="text-xs text-white font-medium truncate">{name}</span>
                {['direction', 'entry', 'sl', 'tp'].map((field) => (
                  <Input
                    key={field}
                    label={field.toUpperCase()}
                    type="number"
                    value={w[field] ?? 0}
                    onChange={(v) => setLocal((c: any) => ({
                      ...c,
                      strategy_weights: { ...c.strategy_weights, [name]: { ...c.strategy_weights[name], [field]: v } },
                    }))}
                    min={0}
                    max={1}
                    step={0.05}
                  />
                ))}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flex gap-2 mt-4">
        <Btn onClick={() => saveMut.mutate()} disabled={saveMut.isPending}>
          {saveMut.isPending ? <Spinner size={14} /> : <Zap size={14} />}
          Save Config
        </Btn>
      </div>
    </Card>
  )
}

export default function StrategyManager() {
  const { addToast } = useAppStore()
  const qc = useQueryClient()
  const [pendingLive, setPendingLive] = useState<string | null>(null)
  const [tab, setTab] = useState<'strategies' | 'ensemble'>('strategies')

  const { data: strategies, isLoading } = useQuery({
    queryKey: ['strategies'],
    queryFn: getStrategies,
    refetchInterval: 30000,
  })

  const setLiveMut = useMutation({
    mutationFn: (name: string) => setStrategyLive(name),
    onSuccess: (_, name) => {
      addToast('success', `${name} is now LIVE`)
      qc.invalidateQueries({ queryKey: ['strategies'] })
      setPendingLive(null)
    },
    onError: (e: any) => { addToast('error', e.message); setPendingLive(null) },
  })

  return (
    <div className="p-6 space-y-6">
      {pendingLive && (
        <ConfirmModal
          title="Set Strategy as Live"
          message={`"${pendingLive}" will become the live trading strategy. The current live strategy will be demoted to shadow mode.`}
          variant="default"
          onConfirm={() => setLiveMut.mutate(pendingLive)}
          onCancel={() => setPendingLive(null)}
        />
      )}

      <SectionHeader title="Strategy Manager" sub="Manage, configure, and orchestrate your trading strategies" />

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
            {t === 'strategies' ? 'Strategies' : 'Ensemble Config'}
          </button>
        ))}
      </div>

      {tab === 'strategies' && (
        <div>
          {isLoading ? (
            <div className="flex justify-center py-16"><Spinner size={32} /></div>
          ) : !strategies?.length ? (
            <Card className="text-center py-12 text-muted text-sm">No strategies found.</Card>
          ) : (
            strategies.map((s: any) => (
              <StrategyRow key={s.name} strategy={s} onSetLive={(name) => setPendingLive(name)} />
            ))
          )}
        </div>
      )}

      {tab === 'ensemble' && <EnsemblePanel />}
    </div>
  )
}
