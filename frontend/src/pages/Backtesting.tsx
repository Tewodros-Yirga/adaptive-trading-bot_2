import React, { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar, Legend,
} from 'recharts'
import { Play, BarChart2, ChevronDown } from 'lucide-react'
import {
  runBacktest, getBacktestResults, getBacktestResult, compareBacktests, getStrategies,
} from '../api'
import {
  Card, SectionHeader, Btn, Spinner, Input, Select, Badge,
} from '../components'
import { useAppStore } from '../store'
import clsx from 'clsx'

const METRIC_LABELS: Record<string, string> = {
  total_trades: 'Total Trades',
  win_rate: 'Win Rate',
  profit_factor: 'Profit Factor',
  max_drawdown: 'Max Drawdown',
  sharpe_ratio: 'Sharpe Ratio',
  sortino_ratio: 'Sortino Ratio',
  calmar_ratio: 'Calmar Ratio',
  avg_rr: 'Avg R:R',
  expectancy: 'Expectancy',
  consecutive_wins: 'Max Consec. Wins',
  consecutive_losses: 'Max Consec. Losses',
  final_balance: 'Final Balance',
}

function MetricsGrid({ metrics }: { metrics: any }) {
  if (!metrics) return null
  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
      {Object.entries(METRIC_LABELS).map(([k, label]) => {
        const v = metrics[k]
        if (v === undefined || v === null) return null
        let display = typeof v === 'number' ? (
          k === 'win_rate' ? `${(v * 100).toFixed(1)}%` :
          k === 'max_drawdown' ? `${(v * 100).toFixed(2)}%` :
          v.toFixed(2)
        ) : String(v)
        return (
          <div key={k} className="bg-bg border border-border rounded-lg p-3">
            <p className="text-xs text-muted mb-1">{label}</p>
            <p className="text-lg font-semibold mono">{display}</p>
          </div>
        )
      })}
    </div>
  )
}

function ResultDetail({ id }: { id: number }) {
  const { data, isLoading } = useQuery({
    queryKey: ['backtestResult', id],
    queryFn: () => getBacktestResult(id),
  })

  if (isLoading) return <div className="flex justify-center py-8"><Spinner /></div>
  if (!data) return null

  const equity = data.metrics?.equity_curve ?? []
  const tradesByMonth = data.metrics?.trades_by_month ?? []

  return (
    <div className="space-y-4">
      <MetricsGrid metrics={data.metrics} />

      {equity.length > 0 && (
        <Card>
          <p className="text-sm font-medium mb-3">Equity Curve</p>
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={equity.map((v: number, i: number) => ({ i, pnl: v }))}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e2d3d" />
              <XAxis dataKey="i" tick={{ fontSize: 10, fill: '#6b7280' }} />
              <YAxis tick={{ fontSize: 10, fill: '#6b7280' }} />
              <Tooltip contentStyle={{ background: '#111827', border: '1px solid #1e2d3d', fontSize: 12 }} />
              <Line type="monotone" dataKey="pnl" stroke="#3b82f6" dot={false} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </Card>
      )}

      {tradesByMonth.length > 0 && (
        <Card>
          <p className="text-sm font-medium mb-3">Monthly Trade Distribution</p>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={tradesByMonth}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e2d3d" />
              <XAxis dataKey="month" tick={{ fontSize: 10, fill: '#6b7280' }} />
              <YAxis tick={{ fontSize: 10, fill: '#6b7280' }} />
              <Tooltip contentStyle={{ background: '#111827', border: '1px solid #1e2d3d', fontSize: 12 }} />
              <Bar dataKey="wins" fill="#10b981" />
              <Bar dataKey="losses" fill="#ef4444" />
              <Legend />
            </BarChart>
          </ResponsiveContainer>
        </Card>
      )}
    </div>
  )
}

export default function Backtesting() {
  const { addToast } = useAppStore()
  const qc = useQueryClient()
  const [tab, setTab] = useState<'run' | 'results' | 'compare'>('run')
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [compareData, setCompareData] = useState<any>(null)

  const { data: strategies } = useQuery({ queryKey: ['strategies'], queryFn: getStrategies })
  const { data: results, isLoading: resultsLoading } = useQuery({
    queryKey: ['backtestResults'],
    queryFn: () => getBacktestResults(20),
    refetchInterval: tab === 'results' ? 10000 : false,
  })

  const [form, setForm] = useState({
    strategy_name: '',
    symbol: 'XAUUSD',
    from_date: '2024-01-01',
    to_date: '2024-12-31',
    initial_balance: 10000,
    leverage: 100,
    risk_per_trade_pct: 1.0,
    use_news_filter: false,
  })

  const runMut = useMutation({
    mutationFn: () => runBacktest(form),
    onSuccess: () => {
      addToast('success', 'Backtest started')
      qc.invalidateQueries({ queryKey: ['backtestResults'] })
      setTab('results')
    },
    onError: (e: any) => addToast('error', e.message),
  })

  const compareMut = useMutation({
    mutationFn: () => compareBacktests(selectedIds),
    onSuccess: (data) => { setCompareData(data); addToast('info', 'Comparison ready') },
    onError: (e: any) => addToast('error', e.message),
  })

  const stratOptions = [
    { value: '', label: 'Select strategy…' },
    ...(strategies ?? []).map((s: any) => ({ value: s.name, label: s.display_name ?? s.name })),
  ]

  return (
    <div className="p-6 space-y-6">
      <SectionHeader title="Backtesting" sub="Run simulations against historical data and compare results" />

      <div className="flex gap-1 border-b border-border pb-0">
        {(['run', 'results', 'compare'] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={clsx(
              'px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px capitalize',
              tab === t ? 'border-accent text-accent' : 'border-transparent text-muted hover:text-white',
            )}
          >{t === 'run' ? 'Run Backtest' : t === 'results' ? 'Results' : 'Compare'}</button>
        ))}
      </div>

      {tab === 'run' && (
        <Card>
          <SectionHeader title="Configure Backtest" />
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-4">
            <Select
              label="Strategy"
              value={form.strategy_name}
              onChange={(v) => setForm((f) => ({ ...f, strategy_name: v }))}
              options={stratOptions}
            />
            <Input label="Symbol" value={form.symbol} onChange={(v) => setForm((f) => ({ ...f, symbol: v }))} />
            <Input label="From Date" type="date" value={form.from_date} onChange={(v) => setForm((f) => ({ ...f, from_date: v }))} />
            <Input label="To Date" type="date" value={form.to_date} onChange={(v) => setForm((f) => ({ ...f, to_date: v }))} />
            <Input label="Initial Balance ($)" type="number" value={form.initial_balance} onChange={(v) => setForm((f) => ({ ...f, initial_balance: v }))} min={100} />
            <Input label="Leverage" type="number" value={form.leverage} onChange={(v) => setForm((f) => ({ ...f, leverage: v }))} min={1} max={1000} />
            <Input label="Risk Per Trade (%)" type="number" value={form.risk_per_trade_pct} onChange={(v) => setForm((f) => ({ ...f, risk_per_trade_pct: v }))} min={0.1} max={10} step={0.1} />
            <div className="flex flex-col gap-1">
              <label className="text-xs text-muted">Apply News Filter</label>
              <button
                onClick={() => setForm((f) => ({ ...f, use_news_filter: !f.use_news_filter }))}
                className={clsx(
                  'mt-1 w-10 h-5 rounded-full transition-colors relative',
                  form.use_news_filter ? 'bg-accent' : 'bg-border',
                )}
              >
                <span className={clsx('absolute top-0.5 w-4 h-4 bg-white rounded-full transition-all shadow', form.use_news_filter ? 'left-5' : 'left-0.5')} />
              </button>
            </div>
          </div>
          <Btn onClick={() => runMut.mutate()} disabled={runMut.isPending || !form.strategy_name}>
            {runMut.isPending ? <Spinner size={14} /> : <Play size={14} />}
            Run Backtest
          </Btn>
        </Card>
      )}

      {tab === 'results' && (
        <div className="space-y-3">
          {resultsLoading ? (
            <div className="flex justify-center py-16"><Spinner size={32} /></div>
          ) : !results?.length ? (
            <Card className="text-center py-12 text-muted text-sm">No backtest results yet. Run your first backtest.</Card>
          ) : (
            results.map((r: any) => (
              <div key={r.id} className="border border-border rounded-lg overflow-hidden">
                <div
                  className="flex items-center gap-4 px-4 py-3 cursor-pointer hover:bg-white/3 transition-colors"
                  onClick={() => setExpandedId(expandedId === r.id ? null : r.id)}
                >
                  <span className="text-muted">{expandedId === r.id ? <ChevronDown size={16} /> : <BarChart2 size={16} />}</span>
                  <div className="flex-1 min-w-0">
                    <span className="text-sm font-medium">{r.strategy_name}</span>
                    <span className="text-xs text-muted ml-2">{r.symbol} · {r.from_date} → {r.to_date}</span>
                  </div>
                  <div className="hidden md:flex items-center gap-6 text-xs text-muted">
                    <span>WR <span className="mono text-white">{r.metrics?.win_rate != null ? `${(r.metrics.win_rate * 100).toFixed(1)}%` : '—'}</span></span>
                    <span>PF <span className="mono text-white">{r.metrics?.profit_factor?.toFixed(2) ?? '—'}</span></span>
                    <span>Sharpe <span className="mono text-white">{r.metrics?.sharpe_ratio?.toFixed(2) ?? '—'}</span></span>
                  </div>
                  <span className="text-xs text-muted ml-4">{new Date(r.created_at).toLocaleDateString()}</span>
                </div>
                {expandedId === r.id && (
                  <div className="border-t border-border bg-bg/50 p-4">
                    <ResultDetail id={r.id} />
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      )}

      {tab === 'compare' && (
        <div className="space-y-4">
          <Card>
            <SectionHeader title="Select Backtests to Compare" />
            <div className="space-y-2 mb-4">
              {(results ?? []).map((r: any) => (
                <label key={r.id} className="flex items-center gap-3 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={selectedIds.includes(r.id)}
                    onChange={(e) => setSelectedIds((ids) => e.target.checked ? [...ids, r.id] : ids.filter((i) => i !== r.id))}
                    className="accent-accent"
                  />
                  <span className="text-sm">{r.strategy_name} · {r.symbol} · {r.from_date}</span>
                </label>
              ))}
            </div>
            <Btn onClick={() => compareMut.mutate()} disabled={selectedIds.length < 2 || compareMut.isPending}>
              {compareMut.isPending ? <Spinner size={14} /> : null}
              Compare Selected ({selectedIds.length})
            </Btn>
          </Card>

          {compareData && (
            <Card>
              <SectionHeader title="Side-by-Side Comparison" />
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border">
                      <th className="text-left text-xs text-muted py-2 pr-4">Metric</th>
                      {compareData.results?.map((r: any) => (
                        <th key={r.id} className="text-left text-xs text-muted py-2 pr-4">{r.strategy_name}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(METRIC_LABELS).map(([k, label]) => (
                      <tr key={k} className="border-b border-border/50">
                        <td className="text-xs text-muted py-2 pr-4">{label}</td>
                        {compareData.results?.map((r: any) => {
                          const v = r.metrics?.[k]
                          const display = v == null ? '—' : (
                            k === 'win_rate' ? `${(v * 100).toFixed(1)}%` :
                            k === 'max_drawdown' ? `${(v * 100).toFixed(2)}%` :
                            typeof v === 'number' ? v.toFixed(2) : String(v)
                          )
                          return <td key={r.id} className="text-xs mono py-2 pr-4">{display}</td>
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
        </div>
      )}
    </div>
  )
}
