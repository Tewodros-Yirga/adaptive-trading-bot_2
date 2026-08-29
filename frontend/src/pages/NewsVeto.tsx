import React, { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { getNewsVetoDecisions, getNewsVetoStatus } from '../api'
import { Card, SectionHeader, Badge, Spinner } from '../components'
import clsx from 'clsx'

// ── Decision row (news-veto audit) ─────────────────────────────────────────
function DecisionRow({ d }: { d: any }) {
  const [open, setOpen] = useState(false)
  const ni = d.news_influence_json || {}

  return (
    <>
      <tr
        className={clsx('border-b border-border/50 hover:bg-white/5 cursor-pointer', open && 'bg-white/5')}
        onClick={() => setOpen(p => !p)}
      >
        <td className="py-2 pr-4 text-muted">{d.timestamp?.slice(11, 16)} UTC</td>
        <td className="py-2 pr-4 mono font-medium">{d.symbol}</td>
        <td className="py-2 pr-4">
          {d.veto
            ? <Badge label="VETO" color="bg-danger/20 text-danger" />
            : <Badge label="PASS" color="bg-success/20 text-success" />}
        </td>
        <td className="py-2 pr-4 mono">{ni.news_bias?.toFixed(3) ?? '—'}</td>
        <td className="py-2 pr-4 mono">{ni.news_confidence?.toFixed(3) ?? '—'}</td>
        <td className="py-2 pr-4 mono text-muted">{d.trade_id ? `#${d.trade_id}` : '—'}</td>
        <td className="py-2">
          {open ? <ChevronDown size={13} className="text-muted" /> : <ChevronRight size={13} className="text-muted" />}
        </td>
      </tr>
      {open && (
        <tr className="border-b border-border">
          <td colSpan={7} className="px-4 py-3 bg-bg/50">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
              <div>
                <p className="text-muted mb-2">News Influence</p>
                <div className="grid grid-cols-2 gap-1.5">
                  <div className="bg-bg rounded p-1.5">
                    <p className="text-muted">Bias</p>
                    <p className="mono">{ni.news_bias?.toFixed(3) ?? '—'}</p>
                  </div>
                  <div className="bg-bg rounded p-1.5">
                    <p className="text-muted">Confidence</p>
                    <p className="mono">{ni.news_confidence?.toFixed(3) ?? '—'}</p>
                  </div>
                  {ni.bias_direction && (
                    <div className="bg-bg rounded p-1.5">
                      <p className="text-muted">Bias direction</p>
                      <p className="mono">{ni.bias_direction}</p>
                    </div>
                  )}
                  {d.veto_reason && (
                    <div className="bg-bg rounded p-1.5">
                      <p className="text-muted">Reason</p>
                      <p className="mono">{d.veto_reason}</p>
                    </div>
                  )}
                </div>
              </div>
              {ni.signaling_strategies && Object.keys(ni.signaling_strategies).length > 0 && (
                <div>
                  <p className="text-muted mb-2">Signalling strategies</p>
                  {Object.entries(ni.signaling_strategies).map(([k, v]) => (
                    <div key={k} className="flex items-center gap-2 mb-0.5">
                      <span className="w-28 truncate">{k}</span>
                      <span className="mono">{String(v)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────
export default function NewsVeto() {
  const [symbolFilter, setSymbolFilter] = useState('')
  const [page, setPage] = useState(1)

  const { data: status } = useQuery({
    queryKey: ['newsVetoStatus'],
    queryFn: getNewsVetoStatus,
    refetchInterval: 30000,
  })

  const { data: decisions, isLoading } = useQuery({
    queryKey: ['newsVetoDecisions', page, symbolFilter],
    queryFn: () => getNewsVetoDecisions({ page, limit: 20, symbol: symbolFilter || undefined }),
    refetchInterval: 15000,
  })

  const decisionItems = decisions?.items ?? (Array.isArray(decisions) ? decisions : [])
  const scores: Record<string, { composite_score: number; live_score: number }> = status?.strategy_scores ?? {}
  const sortedScores = Object.entries(scores).sort(
    (a, b) => b[1].composite_score - a[1].composite_score
  )
  const stats = status?.stats_7d ?? { total_news_checks: 0, veto_count: 0 }

  return (
    <div className="p-6 fade-in">
      <SectionHeader
        title="News Veto & Strategy Scores"
        sub="News-driven trade veto + live composite-score feedback (EnsembleVoter is the sole direction authority)"
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-6">
        {/* Strategy scores — the live score-feedback view */}
        <Card className="lg:col-span-2">
          <p className="font-medium text-sm mb-4">Strategy Scores</p>
          {sortedScores.length === 0 ? (
            <p className="text-muted text-xs">No active strategies yet.</p>
          ) : (
            <>
              <div className="flex items-center gap-2 mb-2 text-[10px] text-muted uppercase tracking-wider">
                <span className="w-32">Strategy</span>
                <span className="flex-1">Backtest score</span>
                <span className="w-20 text-right">Live R-EWMA</span>
              </div>
              {sortedScores.map(([name, s]) => (
                <div key={name} className="flex items-center gap-2 mb-2">
                  <span className="w-32 truncate text-xs">{name}</span>
                  <div className="flex-1 h-2 bg-border rounded-full overflow-hidden">
                    <div className="h-full bg-accent rounded-full"
                      style={{ width: `${Math.round(s.composite_score * 100)}%` }} />
                  </div>
                  <span className="mono text-xs w-12 text-right">{s.composite_score.toFixed(3)}</span>
                  <span className={clsx('mono text-xs w-8 text-right',
                    s.live_score > 0 ? 'text-success' : s.live_score < 0 ? 'text-danger' : 'text-muted')}>
                    {s.live_score > 0 ? '+' : ''}{s.live_score.toFixed(2)}
                  </span>
                </div>
              ))}
            </>
          )}
          <p className="text-xs text-muted mt-3">
            Live R-EWMA is a decaying average of realised R-multiples from live trades (reward per unit risk).
            It tilts each strategy's vote weight without touching its backtest score.
          </p>
        </Card>

        {/* Veto stats */}
        <Card>
          <p className="text-xs text-muted mb-3 uppercase tracking-wider">Last 7 Days</p>
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div className="bg-bg rounded p-2">
              <p className="text-muted">News checks</p>
              <p className="text-xl font-semibold mono">{stats.total_news_checks}</p>
            </div>
            <div className="bg-bg rounded p-2">
              <p className="text-muted">Vetoed</p>
              <p className={clsx('text-xl font-semibold mono', stats.veto_count > 0 ? 'text-danger' : 'text-muted')}>
                {stats.veto_count}
              </p>
            </div>
          </div>
        </Card>
      </div>

      {/* News-veto audit log */}
      <Card>
        <div className="flex items-center justify-between mb-3">
          <p className="font-medium text-sm">News-Veto Log</p>
          <input
            className="bg-bg border border-border rounded px-3 py-1 text-xs text-white outline-none focus:border-accent"
            placeholder="Filter by symbol…"
            value={symbolFilter}
            onChange={e => { setSymbolFilter(e.target.value); setPage(1) }}
          />
        </div>

        {isLoading ? <Spinner /> : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-muted border-b border-border">
                    <th className="text-left py-2 pr-4">Time</th>
                    <th className="text-left py-2 pr-4">Symbol</th>
                    <th className="text-left py-2 pr-4">Result</th>
                    <th className="text-left py-2 pr-4">Bias</th>
                    <th className="text-left py-2 pr-4">Confidence</th>
                    <th className="text-left py-2 pr-4">Trade</th>
                    <th className="py-2 w-6" />
                  </tr>
                </thead>
                <tbody>
                  {decisionItems.map((d: any) => <DecisionRow key={d.id} d={d} />)}
                  {decisionItems.length === 0 && (
                    <tr><td colSpan={7} className="py-8 text-center text-muted">No news-veto checks found</td></tr>
                  )}
                </tbody>
              </table>
            </div>

            <div className="flex items-center justify-between mt-3 text-xs text-muted">
              <span>Page {page}</span>
              <div className="flex gap-1">
                <button className="px-2 py-1 rounded hover:bg-white/5 disabled:opacity-40"
                  onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}>←</button>
                <button className="px-2 py-1 rounded hover:bg-white/5 disabled:opacity-40"
                  onClick={() => setPage(p => p + 1)} disabled={decisionItems.length < 20}>→</button>
              </div>
            </div>
          </>
        )}
      </Card>
    </div>
  )
}
