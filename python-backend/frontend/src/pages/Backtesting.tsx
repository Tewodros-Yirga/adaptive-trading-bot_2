import React, { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { Play, BarChart2 } from 'lucide-react'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } from 'recharts'
import { getStrategies, runBacktest, getBacktestResults, getBacktestResult } from '../api'
import { Card, SectionHeader, Input, Select, Btn, Badge, Spinner } from '../components'
import { useAppStore } from '../store'

const COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6']

function MetricGrid({ metrics }: { metrics: any }) {
  if (!metrics || metrics.error) return <p className="text-danger text-sm">{metrics?.error || 'No metrics'}</p>
  const items = [
    ['Total Trades', metrics.total_trades],
    ['Win Rate', `${metrics.win_rate}%`],
    ['Profit Factor', metrics.profit_factor],
    ['Total P&L', `$${metrics.total_pnl?.toFixed(2)}`],
    ['Return', `${metrics.total_return_pct}%`],
    ['Max Drawdown', `${metrics.max_drawdown_pct}%`],
    ['Sharpe', metrics.sharpe_ratio],
    ['Sortino', metrics.sortino_ratio],
    ['Calmar', metrics.calmar_ratio],
    ['Expectancy', `$${metrics.expectancy?.toFixed(2)}`],
    ['Avg RR', metrics.avg_rr],
    ['Max Consec Wins', metrics.consecutive_wins],
    ['Max Consec Loss', metrics.consecutive_losses],
    ['Final Balance', `$${metrics.final_balance?.toFixed(2)}`],
  ]
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-7 gap-2">
      {items.map(([label, value]) => (
        <div key={label as string} className="bg-bg rounded p-2">
          <p className="text-xs text-muted">{label}</p>
          <p className="text-sm mono font-medium">{value ?? '—'}</p>
        </div>
      ))}
    </div>
  )
}

export default function Backtesting() {
  const { addToast } = useAppStore()
  const [form, setForm] = useState({
    strategy_name: 'DTC',
    symbol: 'XAUUSD',
    from_date: '2023-01-01',
    to_date: '2024-12-31',
    initial_balance: 10000,
    leverage: 100,
    risk_per_trade_pct: 1.0,
  })
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [compareIds, setCompareIds] = useState<number[]>([])

  const { data: strategies } = useQuery({ queryKey: ['strategies'], queryFn: getStrategies })
  const { data: results, refetch } = useQuery({ queryKey: ['backtestResults'], queryFn: () => getBacktestResults(20) })
  const { data: detail } = useQuery({
    queryKey: ['backtestDetail', selectedId],
    queryFn: () => getBacktestResult(selectedId!),
    enabled: !!selectedId,
  })

  const runMut = useMutation({
    mutationFn: runBacktest,
    onSuccess: (d: any) => {
      addToast('success', `Backtest started (ID: ${d.backtest_id})`)
      setTimeout(() => { refetch(); setSelectedId(d.backtest_id) }, 2000)
    },
    onError: (e: any) => addToast('error', e.message),
  })

  const set = (k: string, v: any) => setForm(f => ({ ...f, [k]: v }))

  // Compare equity curves
  const compareData = useQuery({
    queryKey: ['compare', compareIds],
    queryFn: async () => {
      const promises = compareIds.map(id => getBacktestResult(id))
      return Promise.all(promises)
    },
    enabled: compareIds.length > 1,
  })

  const mergedCurve = React.useMemo(() => {
    if (!compareData.data) return []
    const curves = compareData.data.map((d, i) => ({ data: d.equity_curve, name: `${d.strategy_name} (${d.id})`, color: COLORS[i] }))
    const maxLen = Math.max(...curves.map(c => c.data.length))
    return Array.from({ length: maxLen }, (_, i) => {
      const point: any = { i }
      curves.forEach(c => { point[c.name] = c.data[i]?.equity ?? null })
      return point
    })
  }, [compareData.data])

  return (
    <div className="p-6 fade-in">
      <SectionHeader title="Backtesting" sub="Test strategies on historical data" />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Config */}
        <Card className="lg:col-span-1">
          <p className="font-medium text-sm mb-4">Backtest Configuration</p>
          <div className="flex flex-col gap-3">
            <Select
              label="Strategy"
              value={form.strategy_name}
              onChange={v => set('strategy_name', v)}
              options={(strategies || []).map((s: any) => ({ label: s.display_name, value: s.name }))}
            />
            <Input label="Symbol" value={form.symbol} onChange={v => set('symbol', v)} />
            <Input label="From Date" value={form.from_date} onChange={v => set('from_date', v)} />
            <Input label="To Date" value={form.to_date} onChange={v => set('to_date', v)} />
            <Input label="Initial Balance ($)" value={form.initial_balance} type="number" step={1000} onChange={v => set('initial_balance', v)} />
            <Input label="Leverage" value={form.leverage} type="number" min={1} max={500} onChange={v => set('leverage', v)} />
            <Input label="Risk Per Trade (%)" value={form.risk_per_trade_pct} type="number" step={0.1} onChange={v => set('risk_per_trade_pct', v)} />
            <Btn onClick={() => runMut.mutate(form)} disabled={runMut.isPending}>
              {runMut.isPending ? <Spinner size={14} /> : <Play size={14} />} Run Backtest
            </Btn>
          </div>
        </Card>

        {/* Results */}
        <div className="lg:col-span-2 flex flex-col gap-4">
          {/* History */}
          <Card>
            <p className="font-medium text-sm mb-3">Recent Backtests</p>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-muted border-b border-border">
                    <th className="text-left py-1 pr-3">ID</th>
                    <th className="text-left py-1 pr-3">Strategy</th>
                    <th className="text-left py-1 pr-3">Symbol</th>
                    <th className="text-left py-1 pr-3">Win%</th>
                    <th className="text-left py-1 pr-3">PF</th>
                    <th className="text-left py-1 pr-3">Return</th>
                    <th className="text-left py-1 pr-3">Status</th>
                    <th className="text-left py-1"></th>
                  </tr>
                </thead>
                <tbody>
                  {(results || []).map((r: any) => (
                    <tr key={r.id} className="border-b border-border/50 hover:bg-white/5 cursor-pointer" onClick={() => setSelectedId(r.id)}>
                      <td className="py-1.5 pr-3 mono text-muted">{r.id}</td>
                      <td className="py-1.5 pr-3">{r.strategy_name}</td>
                      <td className="py-1.5 pr-3 mono">{r.symbol}</td>
                      <td className="py-1.5 pr-3 mono">{r.metrics?.win_rate ?? '—'}%</td>
                      <td className="py-1.5 pr-3 mono">{r.metrics?.profit_factor ?? '—'}</td>
                      <td className={`py-1.5 pr-3 mono ${(r.metrics?.total_return_pct ?? 0) >= 0 ? 'text-success' : 'text-danger'}`}>
                        {r.metrics?.total_return_pct != null ? `${r.metrics.total_return_pct > 0 ? '+' : ''}${r.metrics.total_return_pct}%` : '—'}
                      </td>
                      <td className="py-1.5 pr-3">
                        <Badge
                          label={r.status}
                          color={r.status === 'COMPLETED' ? 'bg-success/20 text-success' : r.status === 'FAILED' ? 'bg-danger/20 text-danger' : 'bg-warn/20 text-warn'}
                        />
                      </td>
                      <td className="py-1.5">
                        <input type="checkbox" checked={compareIds.includes(r.id)}
                          onChange={e => setCompareIds(prev => e.target.checked ? [...prev, r.id] : prev.filter(i => i !== r.id))}
                          onClick={ev => ev.stopPropagation()}
                          className="cursor-pointer"
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          {/* Detail */}
          {detail && (
            <Card>
              <p className="font-medium text-sm mb-3">Backtest #{detail.id} — {detail.strategy_name} / {detail.symbol}</p>
              <MetricGrid metrics={detail.metrics} />
              {detail.equity_curve?.length > 2 && (
                <div className="mt-4">
                  <ResponsiveContainer width="100%" height={160}>
                    <LineChart data={detail.equity_curve}>
                      <XAxis dataKey="date" tick={{ fontSize: 9, fill: '#6b7280' }} tickLine={false} />
                      <YAxis tick={{ fontSize: 9, fill: '#6b7280' }} tickLine={false} axisLine={false} />
                      <Tooltip contentStyle={{ background: '#111827', border: '1px solid #1e2d3d', fontSize: 11 }} />
                      <Line type="monotone" dataKey="equity" stroke="#3b82f6" strokeWidth={1.5} dot={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
            </Card>
          )}

          {/* Compare */}
          {compareIds.length > 1 && mergedCurve.length > 0 && (
            <Card>
              <p className="font-medium text-sm mb-3">Comparison — {compareIds.join(', ')}</p>
              <ResponsiveContainer width="100%" height={180}>
                <LineChart data={mergedCurve}>
                  <XAxis dataKey="i" tick={false} />
                  <YAxis tick={{ fontSize: 9, fill: '#6b7280' }} tickLine={false} axisLine={false} />
                  <Tooltip contentStyle={{ background: '#111827', border: '1px solid #1e2d3d', fontSize: 11 }} />
                  <Legend wrapperStyle={{ fontSize: 10 }} />
                  {(compareData.data || []).map((d, i) => (
                    <Line key={d.id} type="monotone" dataKey={`${d.strategy_name} (${d.id})`} stroke={COLORS[i]} strokeWidth={1.5} dot={false} />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}
