import React from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AlertOctagon, Play, Zap, RefreshCw, TrendingUp, TrendingDown,
  Minus, Globe, Shield, Activity,
} from 'lucide-react'
import {
  getStats, getBridgeAccount, getNewsContext, getRiskStatus,
  haltTrading, resumeTrading, triggerAdaptation, fetchNews,
} from '../api'
import {
  KpiCard, Card, Btn, SectionHeader, StatusDot, Spinner, Badge, ConfirmModal,
} from '../components'
import { useAppStore } from '../store'

export default function Dashboard() {
  const { addToast } = useAppStore()
  const qc = useQueryClient()
  const [showHaltConfirm, setShowHaltConfirm] = React.useState(false)

  const { data: stats, isLoading: statsLoading } = useQuery({
    queryKey: ['stats'],
    queryFn: getStats,
    refetchInterval: 15000,
  })
  const { data: account, isLoading: accountLoading } = useQuery({
    queryKey: ['account'],
    queryFn: getBridgeAccount,
    refetchInterval: 30000,
  })
  const { data: newsCtx } = useQuery({
    queryKey: ['newsContext'],
    queryFn: getNewsContext,
    refetchInterval: 60000,
  })
  const { data: riskStatus } = useQuery({
    queryKey: ['riskStatus'],
    queryFn: getRiskStatus,
    refetchInterval: 15000,
  })

  const haltMut = useMutation({
    mutationFn: haltTrading,
    onSuccess: () => { addToast('warning', 'Trading halted'); qc.invalidateQueries({ queryKey: ['riskStatus'] }) },
    onError: (e: any) => addToast('error', e.message),
  })
  const resumeMut = useMutation({
    mutationFn: resumeTrading,
    onSuccess: () => { addToast('success', 'Trading resumed'); qc.invalidateQueries({ queryKey: ['riskStatus'] }) },
    onError: (e: any) => addToast('error', e.message),
  })
  const adaptMut = useMutation({
    mutationFn: triggerAdaptation,
    onSuccess: () => { addToast('info', 'Adaptation triggered'); qc.invalidateQueries({ queryKey: ['adaptLog'] }) },
    onError: (e: any) => addToast('error', e.message),
  })
  const newsMut = useMutation({
    mutationFn: () => fetchNews(),
    onSuccess: () => { addToast('info', 'News fetch triggered'); qc.invalidateQueries({ queryKey: ['news'] }) },
    onError: (e: any) => addToast('error', e.message),
  })

  const loading = statsLoading || accountLoading

  const riskAppetiteLabel = (v?: number) => {
    if (v === undefined || v === null) return { label: 'Unknown', color: 'text-muted' }
    if (v > 0.3) return { label: 'Risk-On', color: 'text-success' }
    if (v < -0.3) return { label: 'Risk-Off', color: 'text-danger' }
    return { label: 'Neutral', color: 'text-warn' }
  }
  const ra = riskAppetiteLabel(newsCtx?.risk_appetite)

  return (
    <div className="p-6 space-y-6">
      {showHaltConfirm && (
        <ConfirmModal
          title="Emergency Halt"
          message="This will immediately halt all trading activity. No new orders will be placed until you resume."
          variant="danger"
          onConfirm={() => { haltMut.mutate(); setShowHaltConfirm(false) }}
          onCancel={() => setShowHaltConfirm(false)}
        />
      )}

      <div className="flex items-center justify-between">
        <SectionHeader title="Dashboard" sub="Real-time overview of trading activity" />
        <div className="flex items-center gap-2">
          <StatusDot live={!riskStatus?.trading_halt} />
          <span className="text-xs text-muted">{riskStatus?.trading_halt ? 'Halted' : 'Active'}</span>
        </div>
      </div>

      {/* KPI Row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {loading ? (
          Array.from({ length: 4 }).map((_, i) => (
            <Card key={i} className="flex items-center justify-center h-24"><Spinner /></Card>
          ))
        ) : (
          <>
            <KpiCard
              label="Account Balance"
              value={account?.balance != null ? `$${Number(account.balance).toLocaleString(undefined, { minimumFractionDigits: 2 })}` : '—'}
              sub={account?.currency ?? ''}
              color="text-white"
            />
            <KpiCard
              label="Today's PnL"
              value={stats?.today_pnl != null ? `${stats.today_pnl >= 0 ? '+' : ''}${Number(stats.today_pnl).toFixed(2)}` : '—'}
              sub="unrealized + realized"
              color={stats?.today_pnl >= 0 ? 'text-success' : 'text-danger'}
            />
            <KpiCard
              label="Open Positions"
              value={riskStatus?.open_trades_count ?? stats?.open_trades ?? '—'}
              sub={`max ${riskStatus?.max_open_trades ?? '—'}`}
              color="text-white"
            />
            <KpiCard
              label="Win Rate"
              value={stats?.win_rate != null ? `${(stats.win_rate * 100).toFixed(1)}%` : '—'}
              sub="last 50 trades"
              color={stats?.win_rate >= 0.5 ? 'text-success' : 'text-warn'}
            />
          </>
        )}
      </div>

      {/* Mid row */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* News Context */}
        <Card className="lg:col-span-2 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Globe size={16} className="text-accent" />
              <span className="text-sm font-medium">Global Market Context</span>
            </div>
            {newsCtx?.updated_at && (
              <span className="text-xs text-muted">
                Updated {new Date(newsCtx.updated_at).toLocaleTimeString()}
              </span>
            )}
          </div>

          <div className="flex items-center gap-3">
            <span className="text-xs text-muted">Risk Appetite:</span>
            <span className={`text-sm font-semibold ${ra.color}`}>{ra.label}</span>
            {newsCtx?.risk_appetite != null && (
              <div className="flex-1 h-1.5 bg-bg rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all ${newsCtx.risk_appetite >= 0 ? 'bg-success' : 'bg-danger'}`}
                  style={{ width: `${Math.abs(newsCtx.risk_appetite) * 100}%`, marginLeft: newsCtx.risk_appetite < 0 ? `${(1 + newsCtx.risk_appetite) * 100}%` : '50%' }}
                />
              </div>
            )}
          </div>

          {newsCtx?.key_themes?.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {newsCtx.key_themes.map((t: string, i: number) => (
                <span key={i} className="px-2 py-0.5 bg-accent/10 text-accent text-xs rounded-full border border-accent/20">{t}</span>
              ))}
            </div>
          )}

          {newsCtx?.summary && (
            <p className="text-xs text-muted leading-relaxed border-t border-border pt-3">{newsCtx.summary}</p>
          )}

          {!newsCtx && (
            <p className="text-xs text-muted italic">No context available — try fetching news.</p>
          )}
        </Card>

        {/* Risk Status */}
        <Card className="space-y-3">
          <div className="flex items-center gap-2">
            <Shield size={16} className="text-warn" />
            <span className="text-sm font-medium">Risk Monitor</span>
          </div>
          <div className="space-y-3">
            <div>
              <div className="flex justify-between text-xs mb-1">
                <span className="text-muted">Daily Loss</span>
                <span className={riskStatus?.daily_loss_pct > 80 ? 'text-danger' : 'text-white'}>
                  {riskStatus?.daily_pnl != null ? `${riskStatus.daily_pnl.toFixed(2)}` : '—'}
                </span>
              </div>
              <div className="h-1.5 bg-bg rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all ${(riskStatus?.daily_loss_pct ?? 0) > 80 ? 'bg-danger' : 'bg-warn'}`}
                  style={{ width: `${Math.min(riskStatus?.daily_loss_pct ?? 0, 100)}%` }}
                />
              </div>
            </div>
            <div>
              <div className="flex justify-between text-xs mb-1">
                <span className="text-muted">Drawdown</span>
                <span className="text-white">{riskStatus?.current_drawdown_pct != null ? `${riskStatus.current_drawdown_pct.toFixed(1)}%` : '—'}</span>
              </div>
              <div className="h-1.5 bg-bg rounded-full overflow-hidden">
                <div
                  className="h-full bg-danger/70 rounded-full transition-all"
                  style={{ width: `${Math.min(riskStatus?.current_drawdown_pct ?? 0, 100)}%` }}
                />
              </div>
            </div>
            <div className="flex items-center justify-between text-xs pt-1">
              <span className="text-muted">Open Exposure</span>
              <span className="text-white mono">{riskStatus?.open_trades_count ?? 0} / {riskStatus?.max_open_trades ?? '—'}</span>
            </div>
          </div>
        </Card>
      </div>

      {/* Quick Actions */}
      <Card>
        <div className="flex items-center gap-2 mb-4">
          <Activity size={16} className="text-muted" />
          <span className="text-sm font-medium">Quick Actions</span>
        </div>
        <div className="flex flex-wrap gap-3">
          {riskStatus?.trading_halt ? (
            <Btn
              variant="success"
              onClick={() => resumeMut.mutate()}
              disabled={resumeMut.isPending}
            >
              {resumeMut.isPending ? <Spinner size={14} /> : <Play size={14} />}
              Resume Trading
            </Btn>
          ) : (
            <Btn
              variant="danger"
              onClick={() => setShowHaltConfirm(true)}
              disabled={haltMut.isPending}
            >
              <AlertOctagon size={14} />
              Emergency Halt
            </Btn>
          )}
          <Btn
            variant="outline"
            onClick={() => adaptMut.mutate()}
            disabled={adaptMut.isPending}
          >
            {adaptMut.isPending ? <Spinner size={14} /> : <Zap size={14} />}
            Trigger Adaptation
          </Btn>
          <Btn
            variant="outline"
            onClick={() => newsMut.mutate()}
            disabled={newsMut.isPending}
          >
            {newsMut.isPending ? <Spinner size={14} /> : <RefreshCw size={14} />}
            Fetch News
          </Btn>
          <Btn
            variant="ghost"
            onClick={() => { qc.invalidateQueries(); addToast('info', 'Data refreshed') }}
          >
            <RefreshCw size={14} />
            Refresh All
          </Btn>
        </div>
      </Card>
    </div>
  )
}
