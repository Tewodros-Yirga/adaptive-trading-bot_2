import React, { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts'
import { X, Filter } from 'lucide-react'
import clsx from 'clsx'
import { getTrades, getClosedTrades, getPendingOrders, getPendingOrderHistory } from '../api'
import { Card, SectionHeader, Spinner, Pnl, Badge, Select, Input } from '../components'

const CANCEL_REASON_LABEL: Record<string, string> = {
  ENSEMBLE_REVERSAL: 'Opposite ensemble signal',
  NEWS_OPPOSED: 'Opposing news signal',
  MISSED_ENTRY_TP_PROGRESS: 'Missed entry (price ran to TP)',
  MAX_AGE: 'Max age reached',
  BROKER_REMOVED: 'Removed at broker',
}
const PENDING_STATUS_COLOR: Record<string, string> = {
  CANCELLED: 'bg-warn/20 text-warn',
  EXPIRED: 'bg-muted/20 text-muted',
  FILLED: 'bg-success/20 text-success',
  PENDING: 'bg-accent/20 text-accent',
}

const DIR_COLOR: Record<string, string> = {
  BUY: 'bg-success/20 text-success',
  SELL: 'bg-danger/20 text-danger',
}
const RESULT_COLOR: Record<string, string> = {
  WIN: 'bg-success/20 text-success',
  LOSS: 'bg-danger/20 text-danger',
  BLOCKED: 'bg-warn/20 text-warn',
}

function TradeDetailModal({ trade, onClose }: { trade: any; onClose: () => void }) {
  const fields = [
    ['Symbol', trade.symbol],
    ['Direction', trade.direction],
    ['Entry Price', trade.entry_price?.toFixed(5)],
    ['Exit Price', trade.exit_price?.toFixed(5) ?? '—'],
    ['Stop Loss', trade.stop_loss?.toFixed(5)],
    ['Take Profit', trade.take_profit?.toFixed(5)],
    ['Lot Size', trade.lot_size],
    ['PnL', trade.pnl?.toFixed(4) ?? '—'],
    ['Result', trade.result ?? '—'],
    ['Duration', trade.duration_mins != null ? `${trade.duration_mins} min` : '—'],
    ['Strategy', trade.strategy_name ?? '—'],
    ['ATR @ Entry', trade.atr_at_entry?.toFixed(5) ?? '—'],
    ['EMA Fast', trade.ema_fast_at_entry?.toFixed(5) ?? '—'],
    ['EMA Slow', trade.ema_slow_at_entry?.toFixed(5) ?? '—'],
    ['Opened At', trade.opened_at ? new Date(trade.opened_at).toLocaleString() : '—'],
    ['Closed At', trade.closed_at ? new Date(trade.closed_at).toLocaleString() : '—'],
  ]

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
      <div className="bg-panel border border-border rounded-xl p-6 max-w-lg w-full fade-in max-h-[80vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold">Trade #{trade.id}</h3>
          <button onClick={onClose} className="text-muted hover:text-white"><X size={18} /></button>
        </div>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
          {fields.map(([label, val]) => (
            <div key={label as string}>
              <dt className="text-xs text-muted">{label}</dt>
              <dd className="text-sm mono mt-0.5">{val as string}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  )
}

export default function LiveTrades() {
  const [tab, setTab] = useState<'open' | 'pending' | 'closed' | 'chart'>('open')
  const [selectedTrade, setSelectedTrade] = useState<any>(null)
  const [filterSymbol, setFilterSymbol] = useState('')
  const [filterResult, setFilterResult] = useState('')
  const [filterStrategy, setFilterStrategy] = useState('')

  const { data: openTrades, isLoading: openLoading } = useQuery({
    queryKey: ['openTrades'],
    queryFn: () => getTrades(100),
    refetchInterval: 10000,
  })

  const { data: closedTrades, isLoading: closedLoading } = useQuery({
    queryKey: ['closedTrades'],
    queryFn: () => getClosedTrades(200),
    refetchInterval: 30000,
  })

  const { data: pendingOrders, isLoading: pendingLoading } = useQuery({
    queryKey: ['pendingOrders'],
    queryFn: getPendingOrders,
    refetchInterval: 10000,
  })

  const { data: pendingHistory } = useQuery({
    queryKey: ['pendingHistory'],
    queryFn: () => getPendingOrderHistory(200),
    refetchInterval: 30000,
  })

  const filteredClosed = (closedTrades ?? []).filter((t: any) => {
    if (filterSymbol && !t.symbol?.toLowerCase().includes(filterSymbol.toLowerCase())) return false
    if (filterResult && t.result !== filterResult) return false
    if (filterStrategy && t.strategy_name !== filterStrategy) return false
    return true
  })

  const strategies = [...new Set((closedTrades ?? []).map((t: any) => t.strategy_name).filter(Boolean))]

  // Build cumulative PnL chart data
  const chartData = [...(closedTrades ?? [])]
    .sort((a, b) => new Date(a.closed_at).getTime() - new Date(b.closed_at).getTime())
    .reduce((acc: any[], t: any, i: number) => {
      const last = acc[acc.length - 1]?.cumulative ?? 0
      acc.push({ i: i + 1, cumulative: last + (t.pnl ?? 0), strategy: t.strategy_name })
      return acc
    }, [])

  return (
    <div className="p-6 space-y-6">
      {selectedTrade && <TradeDetailModal trade={selectedTrade} onClose={() => setSelectedTrade(null)} />}

      <SectionHeader title="Live Trades" sub="Monitor open positions and review trade history" />

      <div className="flex gap-1 border-b border-border pb-0">
        {(['open', 'pending', 'closed', 'chart'] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={clsx(
              'px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px capitalize',
              tab === t ? 'border-accent text-accent' : 'border-transparent text-muted hover:text-white',
            )}
          >{t === 'open' ? `Open (${openTrades?.length ?? 0})`
            : t === 'pending' ? `Pending (${pendingOrders?.length ?? 0})`
            : t === 'closed' ? 'Closed History' : 'PnL Chart'}</button>
        ))}
      </div>

      {tab === 'open' && (
        <div>
          {openLoading ? (
            <div className="flex justify-center py-16"><Spinner size={32} /></div>
          ) : !openTrades?.length ? (
            <Card className="text-center py-12 text-muted text-sm">No open positions.</Card>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left">
                    {['Symbol', 'Dir', 'Entry', 'SL', 'TP', 'Lot', 'Unreal. PnL', 'Duration', 'Strategy'].map((h) => (
                      <th key={h} className="text-xs text-muted font-medium py-2 pr-4">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {openTrades.map((t: any) => (
                    <tr
                      key={t.id}
                      className="border-b border-border/50 hover:bg-white/3 cursor-pointer transition-colors"
                      onClick={() => setSelectedTrade(t)}
                    >
                      <td className="py-2 pr-4 font-medium">{t.symbol}</td>
                      <td className="py-2 pr-4"><Badge label={t.direction} color={DIR_COLOR[t.direction] ?? 'bg-muted/20 text-muted'} /></td>
                      <td className="py-2 pr-4 mono text-xs">{t.entry_price?.toFixed(5)}</td>
                      <td className="py-2 pr-4 mono text-xs">{t.stop_loss?.toFixed(5) ?? '—'}</td>
                      <td className="py-2 pr-4 mono text-xs">{t.take_profit?.toFixed(5) ?? '—'}</td>
                      <td className="py-2 pr-4 mono text-xs">{t.lot_size}</td>
                      <td className="py-2 pr-4"><Pnl value={t.pnl} /></td>
                      <td className="py-2 pr-4 text-xs text-muted">{t.duration_mins != null ? `${t.duration_mins}m` : '—'}</td>
                      <td className="py-2 pr-4 text-xs text-muted">{t.strategy_name ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {tab === 'pending' && (
        <div className="space-y-6">
          {/* Active pending limit orders */}
          <div>
            <p className="text-sm font-medium mb-2">Resting Limit Orders</p>
            {pendingLoading ? (
              <div className="flex justify-center py-12"><Spinner size={28} /></div>
            ) : !pendingOrders?.length ? (
              <Card className="text-center py-10 text-muted text-sm">No resting pending orders.</Card>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border text-left">
                      {['Symbol', 'Type', 'Limit', 'SL', 'TP1', 'Lot', 'Strategy', 'Ticket', 'Placed At'].map((h) => (
                        <th key={h} className="text-xs text-muted font-medium py-2 pr-4">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {pendingOrders.map((p: any) => (
                      <tr key={p.id} className="border-b border-border/50">
                        <td className="py-2 pr-4 font-medium">{p.symbol}</td>
                        <td className="py-2 pr-4"><Badge label={p.order_type} color={DIR_COLOR[p.direction] ?? 'bg-muted/20 text-muted'} /></td>
                        <td className="py-2 pr-4 mono text-xs">{p.limit_price?.toFixed(5)}</td>
                        <td className="py-2 pr-4 mono text-xs">{p.stop_loss?.toFixed(5) ?? '—'}</td>
                        <td className="py-2 pr-4 mono text-xs">{p.tp1?.toFixed(5) ?? '—'}</td>
                        <td className="py-2 pr-4 mono text-xs">{p.lot_size}</td>
                        <td className="py-2 pr-4 text-xs text-muted">{p.strategy_name ?? '—'}</td>
                        <td className="py-2 pr-4 text-xs text-muted">{p.mt5_ticket ?? '—'}</td>
                        <td className="py-2 pr-4 text-xs text-muted">{p.created_at ? new Date(p.created_at).toLocaleString() : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Cancellation report */}
          <div>
            <p className="text-sm font-medium mb-2">Cancellation Report</p>
            {!pendingHistory?.length ? (
              <Card className="text-center py-10 text-muted text-sm">No cancelled or expired pending orders yet.</Card>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border text-left">
                      {['Symbol', 'Type', 'Limit', 'Status', 'Reason', 'Strategy', 'Resolved At'].map((h) => (
                        <th key={h} className="text-xs text-muted font-medium py-2 pr-4">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {pendingHistory.map((p: any) => (
                      <tr key={p.id} className="border-b border-border/50">
                        <td className="py-2 pr-4 font-medium">{p.symbol}</td>
                        <td className="py-2 pr-4"><Badge label={p.order_type} color={DIR_COLOR[p.direction] ?? 'bg-muted/20 text-muted'} /></td>
                        <td className="py-2 pr-4 mono text-xs">{p.limit_price?.toFixed(5)}</td>
                        <td className="py-2 pr-4"><Badge label={p.status} color={PENDING_STATUS_COLOR[p.status] ?? 'bg-muted/20 text-muted'} /></td>
                        <td className="py-2 pr-4 text-xs">{CANCEL_REASON_LABEL[p.cancel_reason] ?? p.cancel_reason ?? '—'}</td>
                        <td className="py-2 pr-4 text-xs text-muted">{p.strategy_name ?? '—'}</td>
                        <td className="py-2 pr-4 text-xs text-muted">{p.resolved_at ? new Date(p.resolved_at).toLocaleString() : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {tab === 'closed' && (
        <div className="space-y-4">
          {/* Filters */}
          <Card>
            <div className="flex items-center gap-2 mb-3">
              <Filter size={14} className="text-muted" />
              <span className="text-xs text-muted font-medium uppercase tracking-wider">Filters</span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <Input label="Symbol" value={filterSymbol} onChange={setFilterSymbol} />
              <Select
                label="Result"
                value={filterResult}
                onChange={setFilterResult}
                options={[{ value: '', label: 'All' }, { value: 'WIN', label: 'Win' }, { value: 'LOSS', label: 'Loss' }, { value: 'BLOCKED', label: 'Blocked' }]}
              />
              <Select
                label="Strategy"
                value={filterStrategy}
                onChange={setFilterStrategy}
                options={[{ value: '', label: 'All' }, ...strategies.map((s) => ({ value: s as string, label: s as string }))]}
              />
            </div>
          </Card>

          {closedLoading ? (
            <div className="flex justify-center py-16"><Spinner size={32} /></div>
          ) : !filteredClosed.length ? (
            <Card className="text-center py-12 text-muted text-sm">No closed trades found.</Card>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left">
                    {['#', 'Symbol', 'Dir', 'Entry', 'Exit', 'PnL', 'Result', 'Duration', 'Strategy', 'Closed At'].map((h) => (
                      <th key={h} className="text-xs text-muted font-medium py-2 pr-4">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {filteredClosed.map((t: any) => (
                    <tr
                      key={t.id}
                      className="border-b border-border/50 hover:bg-white/3 cursor-pointer transition-colors"
                      onClick={() => setSelectedTrade(t)}
                    >
                      <td className="py-2 pr-4 text-muted text-xs">{t.id}</td>
                      <td className="py-2 pr-4 font-medium">{t.symbol}</td>
                      <td className="py-2 pr-4"><Badge label={t.direction} color={DIR_COLOR[t.direction] ?? 'bg-muted/20 text-muted'} /></td>
                      <td className="py-2 pr-4 mono text-xs">{t.entry_price?.toFixed(5)}</td>
                      <td className="py-2 pr-4 mono text-xs">{t.exit_price?.toFixed(5) ?? '—'}</td>
                      <td className="py-2 pr-4"><Pnl value={t.pnl} /></td>
                      <td className="py-2 pr-4"><Badge label={t.result ?? '—'} color={RESULT_COLOR[t.result] ?? 'bg-muted/20 text-muted'} /></td>
                      <td className="py-2 pr-4 text-xs text-muted">{t.duration_mins != null ? `${t.duration_mins}m` : '—'}</td>
                      <td className="py-2 pr-4 text-xs text-muted">{t.strategy_name ?? '—'}</td>
                      <td className="py-2 pr-4 text-xs text-muted">{t.closed_at ? new Date(t.closed_at).toLocaleDateString() : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {tab === 'chart' && (
        <Card>
          <p className="text-sm font-medium mb-4">Cumulative PnL</p>
          {chartData.length === 0 ? (
            <p className="text-muted text-sm text-center py-8">No closed trades to chart.</p>
          ) : (
            <ResponsiveContainer width="100%" height={320}>
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e2d3d" />
                <XAxis dataKey="i" tick={{ fontSize: 10, fill: '#6b7280' }} label={{ value: 'Trade #', position: 'insideBottom', fill: '#6b7280', fontSize: 10 }} />
                <YAxis tick={{ fontSize: 10, fill: '#6b7280' }} />
                <Tooltip
                  contentStyle={{ background: '#111827', border: '1px solid #1e2d3d', fontSize: 12 }}
                  formatter={(v: any) => [v.toFixed(4), 'Cum. PnL']}
                />
                <Line type="monotone" dataKey="cumulative" stroke="#3b82f6" dot={false} strokeWidth={2} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </Card>
      )}
    </div>
  )
}
