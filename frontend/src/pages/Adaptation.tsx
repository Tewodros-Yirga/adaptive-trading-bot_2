import React, { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { Zap, ChevronDown, ChevronRight } from 'lucide-react'
import { getAdaptationLog, triggerAdaptation, getLearningSettings, updateLearningSettings, getParamsHistory, getStrategies } from '../api'
import { Card, SectionHeader, Btn, Input, Spinner, Select, Badge } from '../components'
import { useAppStore } from '../store'
import clsx from 'clsx'

const COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4', '#ec4899']

function AdaptLogRow({ log }: { log: any }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="border border-border rounded-lg overflow-hidden mb-2">
      <div
        className="flex items-center gap-4 px-4 py-3 cursor-pointer hover:bg-white/3 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <span className="text-muted">{expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</span>
        <div className="flex-1 min-w-0">
          <span className="text-xs text-muted">{new Date(log.evaluated_at).toLocaleString()}</span>
          {log.strategy_name && (
            <span className="ml-2 px-1.5 py-0.5 bg-accent/10 text-accent text-xs rounded">{log.strategy_name}</span>
          )}
        </div>
        <div className="hidden md:flex items-center gap-6 text-xs">
          <span className="text-muted">Trades <span className="text-white mono">{log.trades_evaluated}</span></span>
          <span className="text-muted">WR <span className="text-white mono">{log.win_rate != null ? `${(log.win_rate * 100).toFixed(1)}%` : '—'}</span></span>
          <span className="text-muted">PF <span className="text-white mono">{log.profit_factor?.toFixed(2) ?? '—'}</span></span>
          <span className="text-muted">Δ <span className="text-white mono">{log.delta_magnitude?.toFixed(4) ?? '—'}</span></span>
          {log.rollback_triggered && <Badge label="ROLLBACK" color="bg-danger/20 text-danger" />}
        </div>
        <span className="text-xs text-muted ml-2">v{log.new_params_version ?? '—'}</span>
      </div>
      {expanded && (
        <div className="border-t border-border bg-bg/50 px-4 py-3">
          <p className="text-xs text-muted font-medium mb-2">Actions Taken</p>
          {log.actions_taken ? (
            <pre className="text-xs mono bg-bg border border-border rounded p-3 overflow-x-auto">
              {JSON.stringify(log.actions_taken, null, 2)}
            </pre>
          ) : (
            <p className="text-xs text-muted">No actions recorded.</p>
          )}
        </div>
      )}
    </div>
  )
}

export default function Adaptation() {
  const { addToast } = useAppStore()
  const qc = useQueryClient()
  const [tab, setTab] = useState<'log' | 'params' | 'settings'>('log')
  const [selectedStrategy, setSelectedStrategy] = useState('')
  const [learningLocal, setLearningLocal] = useState<any>(null)

  const { data: strategies } = useQuery({ queryKey: ['strategies'], queryFn: getStrategies })
  const { data: adaptLog, isLoading: logLoading } = useQuery({
    queryKey: ['adaptLog'],
    queryFn: () => getAdaptationLog(30),
    refetchInterval: 30000,
  })
  const { data: paramsHistory, isLoading: paramsLoading } = useQuery({
    queryKey: ['paramsHistory', selectedStrategy],
    queryFn: () => getParamsHistory(30),
    refetchInterval: 30000,
  })
  const { data: learningSettings } = useQuery({
    queryKey: ['learningSettings'],
    queryFn: getLearningSettings,
  })

  React.useEffect(() => {
    if (learningSettings && !learningLocal) setLearningLocal(learningSettings)
  }, [learningSettings])

  const adaptMut = useMutation({
    mutationFn: triggerAdaptation,
    onSuccess: () => { addToast('success', 'Adaptation triggered'); qc.invalidateQueries({ queryKey: ['adaptLog', 'paramsHistory'] }) },
    onError: (e: any) => addToast('error', e.message),
  })

  const saveLearningMut = useMutation({
    mutationFn: () => updateLearningSettings(learningLocal),
    onSuccess: () => { addToast('success', 'Learning settings saved'); qc.invalidateQueries({ queryKey: ['learningSettings'] }) },
    onError: (e: any) => addToast('error', e.message),
  })

  // Build drift chart: one line per param key
  const driftData = React.useMemo(() => {
    if (!paramsHistory?.length) return { series: [], data: [] }
    const keys: Set<string> = new Set()
    paramsHistory.forEach((v: any) => {
      try { Object.keys(JSON.parse(v.params_json ?? '{}')).forEach((k) => keys.add(k)) } catch {}
    })
    const data = paramsHistory.map((v: any, i: number) => {
      let parsed: any = {}
      try { parsed = JSON.parse(v.params_json ?? '{}') } catch {}
      return { i: i + 1, version: v.version, ...parsed }
    })
    return { series: Array.from(keys), data }
  }, [paramsHistory])

  const stratOptions = [
    { value: '', label: 'All Strategies' },
    ...(strategies ?? []).map((s: any) => ({ value: s.name, label: s.display_name ?? s.name })),
  ]

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <SectionHeader title="Adaptation & Learning" sub="Track how strategies self-optimize over time" />
        <Btn onClick={() => adaptMut.mutate()} disabled={adaptMut.isPending}>
          {adaptMut.isPending ? <Spinner size={14} /> : <Zap size={14} />}
          Trigger Adaptation
        </Btn>
      </div>

      <div className="flex gap-1 border-b border-border pb-0">
        {(['log', 'params', 'settings'] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={clsx(
              'px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px',
              tab === t ? 'border-accent text-accent' : 'border-transparent text-muted hover:text-white',
            )}
          >{t === 'log' ? 'Adaptation Log' : t === 'params' ? 'Parameter Drift' : 'Learning Settings'}</button>
        ))}
      </div>

      {tab === 'log' && (
        <div>
          {logLoading ? (
            <div className="flex justify-center py-16"><Spinner size={32} /></div>
          ) : !adaptLog?.length ? (
            <Card className="text-center py-12 text-muted text-sm">No adaptation runs yet.</Card>
          ) : (
            adaptLog.map((log: any) => <AdaptLogRow key={log.id} log={log} />)
          )}
        </div>
      )}

      {tab === 'params' && (
        <div className="space-y-4">
          <div className="flex items-center gap-3">
            <Select
              label="Strategy"
              value={selectedStrategy}
              onChange={setSelectedStrategy}
              options={stratOptions}
            />
          </div>

          {paramsLoading ? (
            <div className="flex justify-center py-16"><Spinner size={32} /></div>
          ) : !driftData.data.length ? (
            <Card className="text-center py-12 text-muted text-sm">No parameter history yet.</Card>
          ) : (
            <Card>
              <p className="text-sm font-medium mb-4">Parameter Drift Over Versions</p>
              <ResponsiveContainer width="100%" height={320}>
                <LineChart data={driftData.data}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e2d3d" />
                  <XAxis dataKey="i" tick={{ fontSize: 10, fill: '#6b7280' }} label={{ value: 'Version', position: 'insideBottom', fill: '#6b7280', fontSize: 10 }} />
                  <YAxis tick={{ fontSize: 10, fill: '#6b7280' }} />
                  <Tooltip
                    contentStyle={{ background: '#111827', border: '1px solid #1e2d3d', fontSize: 12 }}
                    labelFormatter={(v) => `Version ${driftData.data[v - 1]?.version ?? v}`}
                  />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  {driftData.series.map((key, i) => (
                    <Line
                      key={key}
                      type="monotone"
                      dataKey={key}
                      stroke={COLORS[i % COLORS.length]}
                      dot={false}
                      strokeWidth={1.5}
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </Card>
          )}
        </div>
      )}

      {tab === 'settings' && learningLocal && (
        <Card>
          <SectionHeader title="Learning Hyperparameters" sub="Control how aggressively the adaptation engine updates strategy parameters" />
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
            {Object.entries(learningLocal).map(([k, v]) => (
              <Input
                key={k}
                label={k.replace(/_/g, ' ')}
                type="number"
                value={v as number}
                onChange={(val) => setLearningLocal((s: any) => ({ ...s, [k]: val }))}
                step={0.001}
              />
            ))}
          </div>
          <Btn onClick={() => saveLearningMut.mutate()} disabled={saveLearningMut.isPending}>
            {saveLearningMut.isPending ? <Spinner size={14} /> : null}
            Save Learning Settings
          </Btn>
        </Card>
      )}
    </div>
  )
}
