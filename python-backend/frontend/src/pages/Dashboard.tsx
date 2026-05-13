import React from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertOctagon, Play, RefreshCw, Zap } from 'lucide-react'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import {
  getBridgeAccount, getStats, getTrades, getNewsContext,
  getStrategies, haltTrading, resumeTrading, triggerAdaptation, fetchNews,
} from '../api'
import { KpiCard, Card, Btn, SectionHeader, StatusDot, Pnl, Badge } from '../components'
import { useAppStore } from '../store'

export default function Dashboard() {
  const qc = useQueryClient()
  const { addToast } = useAppStore()

  const { data: account } = useQuery({ queryKey: ['account'], queryFn: getBridgeAccount, refetchInterval: 30000 })
  const { data: stats } = useQuery({ queryKey: ['stats'], queryFn: getStats, refetchInterval: 15000 })
  const { data: trades } = useQuery({ queryKey: ['trades'], queryFn: () => getTrades(100), refetchInterval: 10000 })
  const { data: context } = useQuery({ queryKey: ['newsContext'], queryFn: getNewsContext, refetchInterval: 60000 })
  const { data: strategies } = useQuery({ queryKey: ['strategies'], queryFn: getStrategies })

  const openTrades = trades?.filter((t: any) => t.result === 'OPEN') || []
  const liveStrategy = strategies?.find((s: any) => s.is_live)

  const haltMut = useMutation({ mutationFn: haltTrading, onSuccess: () => { addToast('warning', 'Trading halted'); qc.invalidateQueries() } })
  const resumeMut = useMutation({ mutationFn: resumeTrading, onSuccess: () => { addToast('success', 'Trading resumed'); qc.invalidateQueries() } })
  const adaptMut = useMutation({ mutationFn: triggerAdaptation, onSuccess: () => addToast('info', 'Adaptation triggered') })
  const newsMut = useMutation({ mutationFn: fetchNews, onSuccess: () => addToast('success', 'News fetched') })

  // Equity curve from closed trades
  const equityCurve = React.useMemo(() => {
    const closed = (trades || []).filter((t: any) => t.result !== 'OPEN' && t.closed_at).slice(0, 50).reverse()
    let cum = 0
    return closed.map((t: any) => ({ date: t.closed_at?.slice(0, 10), equity: +(cum += (t.pnl || 0)).toFixed(2) }))
  }, [trades])

  const riskStatus = useQuery({ queryKey: ['riskStatus'], queryFn: () => fetch('/api/risk/status').then(r => r.json()), refetchInterval: 15000 })
  const isHalted = riskStatus.data?.trading_halt

  return (
    <div className="p-6 fade-in">
      <SectionHeader title="Dashboard" sub="Live trading overview" />

      {/* KPI row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
        <KpiCard
          label="Account Balance"
          value={account?.balance != null ? `$${account.balance.toLocaleString()}` : '—'}
          sub={account?.mode === 'SIMULATION' ? 'Simulated' : 'Live'}
          color="text-accent"
        />
        <KpiCard
          label="Win Rate"
          value={stats?.win_rate != null ? `${stats.win_rate}%` : '—'}
          sub={`${stats?.total_trades || 0} trades`}
          color={stats?.win_rate >= 50 ? 'text-success' : 'text-danger'}
        />
        <KpiCard
          label="Open Positions"
          value={openTrades.length}
          sub="active trades"
          color="text-warn"
        />
        <KpiCard
          label="Total P&L"
          value={stats?.total_pnl != null ? `${stats.total_pnl > 0 ? '+' : ''}${stats.total_pnl.toFixed(2)}` : '—'}
          sub={`PF: ${stats?.profit_factor ?? '—'}`}
          color={stats?.total_pnl >= 0 ? 'text-success' : 'text-danger'}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-6">
        {/* Equity chart */}
        <Card className="lg:col-span-2">
          <p className="text-xs text-muted mb-3">Cumulative P&L</p>
          {equityCurve.length > 1 ? (
            <ResponsiveContainer width="100%" height={180}>
              <LineChart data={equityCurve}>
                <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#6b7280' }} tickLine={false} />
                <YAxis tick={{ fontSize: 10, fill: '#6b7280' }} tickLine={false} axisLine={false} />
                <Tooltip contentStyle={{ background: '#111827', border: '1px solid #1e2d3d', fontSize: 12 }} />
                <Line type="monotone" dataKey="equity" stroke="#3b82f6" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-44 flex items-center justify-center text-muted text-sm">No closed trades yet</div>
          )}
        </Card>

        {/* Status panel */}
        <div className="flex flex-col gap-3">
          <Card>
            <p className="text-xs text-muted mb-2">Active Strategy</p>
            {liveStrategy ? (
              <div className="flex items-center gap-2">
                <StatusDot live={true} />
                <span className="font-medium text-sm">{liveStrategy.display_name}</span>
                <Badge label="LIVE" color="bg-success/20 text-success" />
              </div>
            ) : (
              <span className="text-muted text-sm">No live strategy</span>
            )}
            {liveStrategy && (
              <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
                <div><span className="text-muted">Win Rate</span><br /><span className="mono">{liveStrategy.win_rate}%</span></div>
                <div><span className="text-muted">Profit Factor</span><br /><span className="mono">{liveStrategy.profit_factor}</span></div>
              </div>
            )}
          </Card>

          <Card>
            <p className="text-xs text-muted mb-2">Trading Status</p>
            <div className="flex items-center gap-2 mb-3">
              <StatusDot live={!isHalted} />
              <span className={`text-sm font-medium ${isHalted ? 'text-danger' : 'text-success'}`}>
                {isHalted ? 'HALTED' : 'ACTIVE'}
              </span>
            </div>
            <div className="flex gap-2">
              {!isHalted
                ? <Btn variant="danger" size="sm" onClick={() => haltMut.mutate()} disabled={haltMut.isPending}>
                    <AlertOctagon size={12} /> Halt
                  </Btn>
                : <Btn variant="success" size="sm" onClick={() => resumeMut.mutate()} disabled={resumeMut.isPending}>
                    <Play size={12} /> Resume
                  </Btn>
              }
            </div>
          </Card>
        </div>
      </div>

      {/* News context + quick actions */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card>
          <p className="text-xs text-muted mb-2">Global Market Context</p>
          {context ? (
            <>
              <div className="flex items-center gap-2 mb-2">
                <Badge
                  label={context.sentiment || 'NEUTRAL'}
                  color={context.sentiment === 'BULLISH' ? 'bg-success/20 text-success' : context.sentiment === 'BEARISH' ? 'bg-danger/20 text-danger' : 'bg-muted/20 text-muted'}
                />
                <span className="text-xs text-muted">Risk appetite: <span className="mono">{context.risk_appetite?.toFixed(2)}</span></span>
              </div>
              <p className="text-xs text-muted/80 leading-relaxed mb-2">{context.summary}</p>
              {context.key_themes?.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {context.key_themes.map((t: string) => (
                    <span key={t} className="px-2 py-0.5 bg-border rounded text-xs text-muted">{t}</span>
                  ))}
                </div>
              )}
            </>
          ) : <span className="text-muted text-sm">No context available</span>}
        </Card>

        <Card>
          <p className="text-xs text-muted mb-3">Quick Actions</p>
          <div className="flex flex-wrap gap-2">
            <Btn size="sm" variant="outline" onClick={() => newsMut.mutate()} disabled={newsMut.isPending}>
              <RefreshCw size={12} /> Fetch News
            </Btn>
            <Btn size="sm" variant="outline" onClick={() => adaptMut.mutate()} disabled={adaptMut.isPending}>
              <Zap size={12} /> Trigger Adaptation
            </Btn>
          </div>
          {stats && (
            <div className="mt-3 grid grid-cols-3 gap-2 text-xs border-t border-border pt-3">
              <div><span className="text-muted">Max DD</span><br /><span className="mono text-danger">{stats.max_drawdown?.toFixed(2)}</span></div>
              <div><span className="text-muted">Avg RR</span><br /><span className="mono">{stats.avg_rr}</span></div>
              <div><span className="text-muted">Wins/Loss</span><br /><span className="mono">{stats.wins}/{stats.losses}</span></div>
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}
