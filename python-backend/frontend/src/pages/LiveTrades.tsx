import React, { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import { getTrades, getClosedTrades, getStats } from '../api'
import { Card, SectionHeader, Badge, Pnl, Spinner } from '../components'
import clsx from 'clsx'

const dirColor = (d: string) => d === 'BUY' ? 'bg-success/20 text-success' : 'bg-danger/20 text-danger'
const resultColor = (r: string | null) => r === 'WIN' ? 'text-success' : r === 'LOSS' ? 'text-danger' : r === 'OPEN' ? 'text-warn' : 'text-muted'

export default function LiveTrades() {
  const [tab, setTab] = useState<'open' | 'closed'>('open')
  const [filter, setFilter] = useState('')

  const { data: allTrades, isLoading } = useQuery({ queryKey: ['trades', 200], queryFn: () => getTrades(200), refetchInterval: 10000 })
  const { data: stats } = useQuery({ queryKey: ['stats'], queryFn: getStats })

  const openTrades = (allTrades || []).filter((t: any) => t.result === 'OPEN')
  const closedTrades = (allTrades || []).filter((t: any) => ['WIN', 'LOSS', 'BLOCKED'].includes(t.result))
    .filter((t: any) => !filter || t.symbol?.toLowerCase().includes(filter.toLowerCase()) || t.strategy_name?.toLowerCase().includes(filter.toLowerCase()))

  const equityCurve = React.useMemo(() => {
    const sorted = [...closedTrades].filter(t => t.closed_at).reverse()
    let cum = 0
    return sorted.map((t: any) => ({ date: t.closed_at?.slice(5, 10), equity: +(cum += (t.pnl || 0)).toFixed(2) }))
  }, [closedTrades])

  if (isLoading) return <div className="p-6 flex justify-center"><Spinner /></div>

  return (
    <div className="p-6 fade-in">
      <SectionHeader title="Live Trades" sub="Open positions and trade history" />

      {/* PnL chart */}
      {equityCurve.length > 2 && (
        <Card className="mb-4">
          <p className="text-xs text-muted mb-2">Cumulative P&L</p>
          <ResponsiveContainer width="100%" height={120}>
            <LineChart data={equityCurve}>
              <XAxis dataKey="date" tick={{ fontSize: 9, fill: '#6b7280' }} tickLine={false} />
              <YAxis tick={{ fontSize: 9, fill: '#6b7280' }} tickLine={false} axisLine={false} width={50} />
              <Tooltip contentStyle={{ background: '#111827', border: '1px solid #1e2d3d', fontSize: 11 }} />
              <Line type="monotone" dataKey="equity" stroke="#3b82f6" strokeWidth={1.5} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </Card>
      )}

      {/* Tabs */}
      <div className="flex gap-1 mb-4 border-b border-border">
        {(['open', 'closed'] as const).map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={clsx('px-4 py-2 text-sm transition-colors -mb-px', tab === t ? 'text-accent border-b-2 border-accent' : 'text-muted hover:text-white')}
          >
            {t === 'open' ? `Open (${openTrades.length})` : `Closed (${closedTrades.length})`}
          </button>
        ))}
        {tab === 'closed' && (
          <input
            className="ml-auto bg-bg border border-border rounded px-3 py-1 text-xs text-white outline-none focus:border-accent mb-1"
            placeholder="Filter by symbol / strategy…"
            value={filter}
            onChange={e => setFilter(e.target.value)}
          />
        )}
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted border-b border-border">
              <th className="text-left py-2 pr-4">Symbol</th>
              <th className="text-left py-2 pr-4">Dir</th>
              <th className="text-left py-2 pr-4">Entry</th>
              {tab === 'open' ? (
                <>
                  <th className="text-left py-2 pr-4">SL</th>
                  <th className="text-left py-2 pr-4">TP</th>
                </>
              ) : (
                <>
                  <th className="text-left py-2 pr-4">Exit</th>
                  <th className="text-left py-2 pr-4">P&L</th>
                </>
              )}
              <th className="text-left py-2 pr-4">Strategy</th>
              <th className="text-left py-2 pr-4">Lots</th>
              <th className="text-left py-2 pr-4">Result</th>
              <th className="text-left py-2">Time</th>
            </tr>
          </thead>
          <tbody>
            {(tab === 'open' ? openTrades : closedTrades).map((t: any) => (
              <tr key={t.id} className="border-b border-border/50 hover:bg-white/5 transition-colors">
                <td className="py-2 pr-4 mono font-medium">{t.symbol}</td>
                <td className="py-2 pr-4"><Badge label={t.direction} color={dirColor(t.direction)} /></td>
                <td className="py-2 pr-4 mono">{t.entry_price?.toFixed(5)}</td>
                {tab === 'open' ? (
                  <>
                    <td className="py-2 pr-4 mono text-danger">{t.stop_loss?.toFixed(5)}</td>
                    <td className="py-2 pr-4 mono text-success">{t.take_profit?.toFixed(5)}</td>
                  </>
                ) : (
                  <>
                    <td className="py-2 pr-4 mono">{t.exit_price?.toFixed(5) ?? '—'}</td>
                    <td className="py-2 pr-4"><Pnl value={t.pnl} /></td>
                  </>
                )}
                <td className="py-2 pr-4 text-muted">{t.strategy_name || 'DTC'}</td>
                <td className="py-2 pr-4 mono">{t.lot_size}</td>
                <td className={clsx('py-2 pr-4 mono font-medium', resultColor(t.result))}>{t.result || '—'}</td>
                <td className="py-2 text-muted">{(t.opened_at || '').slice(0, 16).replace('T', ' ')}</td>
              </tr>
            ))}
            {(tab === 'open' ? openTrades : closedTrades).length === 0 && (
              <tr><td colSpan={9} className="py-8 text-center text-muted">No trades</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
