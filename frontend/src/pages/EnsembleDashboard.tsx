import { useState, useEffect, useRef, useCallback } from "react";
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity, TrendingUp, TrendingDown, RefreshCw,
  ChevronDown, ChevronUp, AlertCircle, CheckCircle, Lock, Zap
} from "lucide-react";
import {
  getVoterSnapshot, getEnsembleDecisions, getTradeAnalytics, getStats,
} from '../api'
import { useWebSocket } from '../hooks/useWebSocket'
import { SectionHeader } from '../components'

// ── Shared sub-component: votes breakdown table ──────────────────────────
function VotesBreakdown({ votes }: { votes: any[] }) {
  if (!votes || votes.length === 0) {
    return (
      <div className="text-gray-500 text-xs italic px-3 py-2">
        No vote breakdown available.
      </div>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr className="text-gray-500 border-b border-gray-800">
            <th className="text-left py-1 px-2 font-mono font-medium">Strategy</th>
            <th className="text-left py-1 px-2 font-mono font-medium">Dir</th>
            <th className="text-right py-1 px-2 font-mono font-medium">Raw Conf</th>
            <th className="text-right py-1 px-2 font-mono font-medium">Weight</th>
            <th className="text-right py-1 px-2 font-mono font-medium">Contrib</th>
            <th className="text-center py-1 px-2 font-mono font-medium">Susp?</th>
            <th className="text-center py-1 px-2 font-mono font-medium">Agreed?</th>
          </tr>
        </thead>
        <tbody>
          {votes.map((v: any, i: number) => (
            <tr key={i} className="border-b border-gray-900 hover:bg-gray-800/40 transition-colors">
              <td className={`py-1 px-2 font-mono ${v.is_suspended ? "line-through text-gray-600" : "text-gray-300"}`}>
                {v.strategy_name || "—"}
              </td>
              <td className="py-1 px-2">
                {v.direction === "BUY" ? (
                  <span className="text-emerald-400 font-mono font-bold">BUY</span>
                ) : v.direction === "SELL" ? (
                  <span className="text-red-400 font-mono font-bold">SELL</span>
                ) : (
                  <span className="text-gray-500 font-mono">—</span>
                )}
              </td>
              <td className="py-1 px-2 text-right text-gray-400 font-mono">
                {/* Support both new field (raw_confidence) and legacy (confidence) */}
                {(v.raw_confidence ?? v.confidence) != null
                  ? ((v.raw_confidence ?? v.confidence) * 100).toFixed(1) + "%"
                  : "—"}
              </td>
              <td className="py-1 px-2 text-right text-gray-400 font-mono">
                {v.weight != null ? (v.weight * 100).toFixed(1) + "%" : "—"}
              </td>
              <td className="py-1 px-2 text-right text-amber-400 font-mono font-bold">
                {/* Support both new field (weighted_contribution) and legacy (weighted_vote) */}
                {(v.weighted_contribution ?? v.weighted_vote) != null
                  ? (v.weighted_contribution ?? v.weighted_vote).toFixed(4)
                  : "—"}
              </td>
              <td className="py-1 px-2 text-center">
                {v.is_suspended ? <span className="text-red-500">●</span> : <span className="text-gray-700">○</span>}
              </td>
              <td className="py-1 px-2 text-center">
                {/* Support both new field (contributed_to_winning_side) and legacy (was_agreeing) */}
                {(v.contributed_to_winning_side ?? v.was_agreeing) ? (
                  <CheckCircle size={12} className="text-emerald-500 inline" />
                ) : (
                  <span className="text-gray-700 text-xs">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Panel skeleton loader ────────────────────────────────────────────────
function Skeleton({ lines = 4, className = "" }: { lines?: number; className?: string }) {
  return (
    <div className={`space-y-2 ${className}`}>
      {Array.from({ length: lines }).map((_, i) => (
        <div
          key={i}
          className="h-4 bg-gray-800 rounded animate-pulse"
          style={{ width: `${60 + (i % 3) * 15}%`, opacity: 1 - i * 0.15 }}
        />
      ))}
    </div>
  );
}

// ── Panel error state ────────────────────────────────────────────────────
function PanelError({ msg }: { msg: string }) {
  return (
    <div className="flex items-center gap-2 text-red-400 text-xs font-mono py-2">
      <AlertCircle size={14} />
      <span>{msg}</span>
    </div>
  );
}

// ── Relative time formatter ──────────────────────────────────────────────
function relTime(ts: string | Date | null | undefined) {
  if (!ts) return "—";
  const diff = (Date.now() - new Date(ts).getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

// ── KPI card helper ──────────────────────────────────────────────────────
function KPICard({ label, value, sub, accent = "gray" }: {
  label: string; value: any; sub?: string; accent?: string
}) {
  const accentMap: Record<string, string> = {
    amber: "text-amber-400 border-amber-900",
    green: "text-emerald-400 border-emerald-900",
    red: "text-red-400 border-red-900",
    blue: "text-blue-400 border-blue-900",
    yellow: "text-yellow-400 border-yellow-900",
    gray: "text-gray-300 border-gray-800",
  };
  const colors = accentMap[accent] || accentMap.gray;
  return (
    <div className={`border rounded p-3 ${colors}`}>
      <div className="text-xs text-gray-500 mb-1 tracking-wider uppercase">{label}</div>
      <div className={`text-xl font-bold tabular-nums font-mono ${colors.split(" ")[0]}`}>{value}</div>
      {sub && <div className="text-xs text-gray-600 mt-0.5">{sub}</div>}
    </div>
  );
}

// ── Main Dashboard ───────────────────────────────────────────────────────
export default function EnsembleDashboard() {
  const qc = useQueryClient()
  const [expandedRow, setExpandedRow] = useState<string | null>(null);
  const [voteHistory, setVoteHistory] = useState<any[]>([]);
  const [, forceUpdate] = useState(0);
  const voteRef = useRef<HTMLDivElement>(null);

  // ── Data queries (using shared API layer) ────────────────────────────
  const { data: weights, isLoading: weightsLoading, error: weightsError } =
    useQuery({ queryKey: ['voterSnapshot'], queryFn: getVoterSnapshot, refetchInterval: 30000 })

  const { data: decisions, isLoading: decisionsLoading, error: decisionsError } =
    useQuery({ queryKey: ['ensembleDecisions'], queryFn: () => getEnsembleDecisions(50), refetchInterval: 30000 })

  const { data: analytics, isLoading: analyticsLoading, error: analyticsError } =
    useQuery({ queryKey: ['tradeAnalytics'], queryFn: () => getTradeAnalytics(30), refetchInterval: 60000 })

  const { data: stats } =
    useQuery({ queryKey: ['stats'], queryFn: getStats, refetchInterval: 15000 })

  // ── Shared WebSocket hook (handles auth token automatically) ──────────
  useWebSocket({
    ensemble_vote: (data) => {
      const payload = (data.data as any) ?? data
      setVoteHistory(prev => [payload, ...prev].slice(0, 20));
      setTimeout(() => {
        if (voteRef.current) voteRef.current.scrollTop = 0;
      }, 50);
    },
    ensemble_weights_updated: () => {
      qc.invalidateQueries({ queryKey: ['voterSnapshot'] })
    },
    '*': () => {},
  })

  // ── Relative time ticker (re-render every 30s) ────────────────────────
  useEffect(() => {
    const id = setInterval(() => forceUpdate(n => n + 1), 30000);
    return () => clearInterval(id);
  }, []);

  const refreshAll = () => {
    qc.invalidateQueries({ queryKey: ['voterSnapshot'] })
    qc.invalidateQueries({ queryKey: ['ensembleDecisions'] })
    qc.invalidateQueries({ queryKey: ['tradeAnalytics'] })
    qc.invalidateQueries({ queryKey: ['stats'] })
  }

  // ── Compute health KPIs ───────────────────────────────────────────────
  const healthKPIs = (() => {
    if (!decisions && !stats && !weights) return null;
    const decList = decisions ?? []
    const fired = decList.filter((d: any) => !d.news_blocked && !d.risk_blocked);
    const fireRate = decList.length > 0 ? (fired.length / decList.length * 100).toFixed(1) : "—";
    const suspended = weights?.suspended_strategies || [];
    const totalStrategies = weights ? Object.keys(weights.normalized_weights || {}).length : 0;
    const activeCount = totalStrategies - suspended.length;
    const alchemistWeight = weights?.normalized_weights?.Alchemist;
    const threshold = weights?.threshold ?? 0.60;
    return { fireRate, activeCount, totalStrategies, threshold, alchemistWeight, suspended };
  })();

  // ── Sorted strategy perf ──────────────────────────────────────────────
  const stratPerf = (() => {
    if (!analytics?.by_strategy) return [];
    const suspended = weights?.suspended_strategies || [];
    return Object.entries(analytics.by_strategy)
      .map(([name, v]: [string, any]) => ({ name, ...v, isSuspended: suspended.includes(name) }))
      .sort((a: any, b: any) => (a.isSuspended ? 1 : 0) - (b.isSuspended ? 1 : 0) || b.win_rate - a.win_rate);
  })();

  // ── Weight bar data ───────────────────────────────────────────────────
  const weightEntries = (() => {
    if (!weights?.normalized_weights) return [];
    const suspended = weights.suspended_strategies || [];
    return Object.entries(weights.normalized_weights)
      .map(([name, w]: [string, any]) => ({
        name, weight: w,
        isSuspended: suspended.includes(name),
        isAlchemist: name === "Alchemist"
      }))
      .sort((a: any, b: any) => b.weight - a.weight);
  })();

  const maxWeight = weightEntries.length ? Math.max(...weightEntries.map((e: any) => e.weight)) : 1;

  return (
    <div className="p-6 space-y-4">
      {/* ── Header */}
      <div className="flex items-center justify-between">
        <SectionHeader title="Ensemble Monitor" sub="Real-time voting, weights, and decision audit trail" />
        <button
          onClick={refreshAll}
          className="text-muted hover:text-white transition-colors p-1.5 rounded"
          title="Refresh all panels"
        >
          <RefreshCw size={14} />
        </button>
      </div>

      {/* ── Row 1: Weight Matrix (30%) + Live Vote Stream (70%) */}
      <div className="grid grid-cols-1 md:grid-cols-10 gap-4">

        {/* Panel — Weight Matrix */}
        <div className="md:col-span-3 bg-panel border border-border rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs text-muted tracking-wider uppercase font-medium">Weight Matrix</span>
            <Zap size={12} className="text-warn" />
          </div>

          {weights?.using_defaults && (
            <div className="text-xs text-warn bg-warn/10 border border-warn/20 rounded px-2 py-1 mb-3 flex items-center gap-1">
              <AlertCircle size={11} />
              <span>Using defaults — optimizer not yet run</span>
            </div>
          )}

          {weightsError ? (
            <PanelError msg={(weightsError as Error).message} />
          ) : weightsLoading ? (
            <Skeleton lines={8} />
          ) : (
            <>
              <div className="space-y-1.5 mb-3">
                {weightEntries.map((entry: any) => {
                  const pct = (entry.weight * 100).toFixed(1);
                  const barW = Math.round((entry.weight / maxWeight) * 100);
                  return (
                    <div key={entry.name}>
                      <div className="flex items-center justify-between mb-0.5">
                        <span className={`text-xs truncate max-w-[140px] ${
                          entry.isSuspended ? "line-through text-muted" :
                          entry.isAlchemist ? "text-warn" : "text-white"
                        }`}>
                          {entry.name}
                          {entry.isAlchemist && <Lock size={9} className="inline ml-1 text-warn/60" />}
                          {entry.isSuspended && <span className="ml-1 text-danger no-underline">●</span>}
                        </span>
                        <span className={`text-xs font-bold mono ${entry.isAlchemist ? "text-warn" : "text-muted"}`}>
                          {pct}%
                        </span>
                      </div>
                      <div className="h-1.5 bg-bg rounded-sm overflow-hidden">
                        <div
                          className={`h-full rounded-sm transition-all duration-500 ${
                            entry.isSuspended ? "bg-border" :
                            entry.isAlchemist ? "bg-warn" : "bg-accent"
                          }`}
                          style={{ width: `${barW}%` }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
              <div className="border-t border-border pt-2 space-y-1">
                <div className="flex justify-between text-xs">
                  <span className="text-muted">Threshold</span>
                  <span className="text-warn font-bold mono">{((weights.threshold ?? 0.60) * 100).toFixed(0)}%</span>
                </div>
                {weights.alchemist_min_weight != null && (
                  <div className="flex justify-between text-xs">
                    <span className="text-muted">Alchemist floor</span>
                    <span className={`font-bold mono ${weights.alchemist_has_floor ? "text-warn" : "text-danger"}`}>
                      {(weights.alchemist_min_weight * 100).toFixed(0)}%
                    </span>
                  </div>
                )}
                <div className="flex justify-between text-xs">
                  <span className="text-muted">Strategies</span>
                  <span className="text-white mono">{weightEntries.length}</span>
                </div>
              </div>
            </>
          )}
        </div>

        {/* Panel — Live Vote Stream */}
        <div className="md:col-span-7 bg-panel border border-border rounded-lg p-4 flex flex-col">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs text-muted tracking-wider uppercase font-medium">Live Vote Stream</span>
            <span className="text-xs text-muted">
              {voteHistory.length > 0 ? `${voteHistory.length} events` : "Waiting for events…"}
            </span>
          </div>

          {voteHistory.length === 0 && (
            <div className="flex-1 flex items-center justify-center text-muted text-xs py-8">
              Awaiting ensemble votes via WebSocket…
            </div>
          )}

          <div ref={voteRef} className="overflow-y-auto flex-1 space-y-1.5" style={{ maxHeight: "340px" }}>
            {voteHistory.map((ev: any, i: number) => {
              const isExpanded = expandedRow === `ws-${i}`;
              const isFired = ev.fired;
              return (
                <div
                  key={i}
                  className={`rounded border cursor-pointer transition-all duration-150 ${
                    isFired ? "border-warn/40 bg-warn/5" : "border-border bg-bg/50"
                  }`}
                  onClick={() => setExpandedRow(isExpanded ? null : `ws-${i}`)}
                >
                  <div className="flex items-center gap-2 px-2 py-1.5 text-xs">
                    <span className="text-muted tabular-nums w-14 shrink-0 mono">{relTime(ev.timestamp)}</span>
                    <span className="text-muted font-bold w-16 shrink-0">{ev.symbol || "—"}</span>
                    <span className={`font-bold w-10 shrink-0 ${
                      ev.direction === "BUY" ? "text-success" :
                      ev.direction === "SELL" ? "text-danger" : "text-muted"
                    }`}>
                      {ev.direction || "—"}
                    </span>
                    <div className="flex-1 flex items-center gap-1">
                      <div className="h-1.5 rounded-sm bg-border flex-1 overflow-hidden max-w-[80px]">
                        <div
                          className={`h-full rounded-sm ${
                            ev.direction === "BUY" ? "bg-success/60" :
                            ev.direction === "SELL" ? "bg-danger/60" : "bg-border"
                          }`}
                          style={{ width: `${Math.round((ev.confidence || 0) * 100)}%` }}
                        />
                      </div>
                      <span className="text-muted w-8 mono">{((ev.confidence || 0) * 100).toFixed(0)}%</span>
                    </div>
                    <div className="flex items-center gap-1 text-muted tabular-nums text-xs shrink-0 mono">
                      <span className="text-success/50">{(ev.buy_score || 0).toFixed(3)}</span>
                      <span>/</span>
                      <span className="text-danger/50">{(ev.sell_score || 0).toFixed(3)}</span>
                    </div>
                    {isFired && <Zap size={11} className="text-warn shrink-0" />}
                    {isExpanded ? <ChevronUp size={11} className="text-muted shrink-0" /> : <ChevronDown size={11} className="text-muted shrink-0" />}
                  </div>
                  {isExpanded && (
                    <div className="border-t border-border px-2 py-2">
                      <VotesBreakdown votes={ev.votes_breakdown || []} />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* ── Row 2: Health KPIs */}
      <div className="bg-panel border border-border rounded-lg p-4">
        <div className="flex items-center gap-2 mb-3">
          <span className="text-xs text-muted tracking-wider uppercase font-medium">Ensemble Health</span>
        </div>
        {!healthKPIs ? (
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            {[...Array(5)].map((_, i) => <Skeleton key={i} lines={2} />)}
          </div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <KPICard
              label="Fire Rate"
              value={`${healthKPIs.fireRate}%`}
              sub={`of ${(decisions ?? []).length} decisions`}
              accent="amber"
            />
            <KPICard
              label="Active Strategies"
              value={`${healthKPIs.activeCount} / ${healthKPIs.totalStrategies}`}
              sub={`${healthKPIs.suspended.length} suspended`}
              accent={healthKPIs.suspended.length > 0 ? "red" : "green"}
            />
            <KPICard
              label="Vote Threshold"
              value={`${(healthKPIs.threshold * 100).toFixed(0)}%`}
              sub="required to fire"
              accent="blue"
            />
            <KPICard
              label="Alchemist Weight"
              value={healthKPIs.alchemistWeight != null
                ? `${(healthKPIs.alchemistWeight * 100).toFixed(1)}%`
                : "—"}
              sub={weights?.alchemist_has_floor ? "✓ above floor" : "⚠ near floor"}
              accent="yellow"
            />
            <KPICard
              label="Open Trades"
              value={stats?.open_trades ?? "—"}
              sub={`Today PnL: ${stats?.today_pnl != null ? Number(stats.today_pnl).toFixed(2) : "—"}`}
              accent={(stats?.today_pnl ?? 0) >= 0 ? "green" : "red"}
            />
          </div>
        )}
      </div>

      {/* ── Row 3: Per-Strategy Stats (40%) + Decision History (60%) */}
      <div className="grid grid-cols-1 md:grid-cols-10 gap-4">

        {/* Panel — Per-Strategy Performance */}
        <div className="md:col-span-4 bg-panel border border-border rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs text-muted tracking-wider uppercase font-medium">Strategy Performance</span>
            <span className="text-xs text-muted">30d</span>
          </div>

          {analyticsError ? (
            <PanelError msg={(analyticsError as Error).message} />
          ) : analyticsLoading ? (
            <Skeleton lines={6} />
          ) : stratPerf.length === 0 ? (
            <div className="text-muted text-xs py-4 text-center">No trade data yet.</div>
          ) : (
            <div className="space-y-2">
              {stratPerf.map((s: any) => (
                <div key={s.name} className={`p-2 rounded border border-border ${s.isSuspended ? "opacity-50" : ""}`}>
                  <div className="flex items-center justify-between mb-1">
                    <span className={`text-xs font-bold ${s.isSuspended ? "line-through text-muted" : "text-white"}`}>
                      {s.isSuspended && <span className="text-danger mr-1">●</span>}
                      {s.name}
                    </span>
                    <span className={`text-xs mono ${(s.total_pnl ?? 0) >= 0 ? "text-success" : "text-danger"}`}>
                      {(s.total_pnl ?? 0) >= 0 ? "+" : ""}{(s.total_pnl ?? 0).toFixed(2)}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 mb-1">
                    <div className="h-1.5 flex-1 bg-bg rounded-sm overflow-hidden">
                      <div
                        className={`h-full rounded-sm ${
                          s.win_rate >= 55 ? "bg-success/70" :
                          s.win_rate >= 45 ? "bg-warn/70" : "bg-danger/60"
                        }`}
                        style={{ width: `${s.win_rate}%` }}
                      />
                    </div>
                    <span className="text-xs text-muted w-14 text-right mono">{s.win_rate?.toFixed(1)}% WR</span>
                  </div>
                  <div className="flex gap-3 text-xs text-muted">
                    <span>PF <span className="text-white mono">{s.profit_factor?.toFixed(2)}</span></span>
                    <span>Trades <span className="text-white mono">{s.trades}</span></span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Panel — Decision History */}
        <div className="md:col-span-6 bg-panel border border-border rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs text-muted tracking-wider uppercase font-medium">Decision History</span>
            <span className="text-xs text-muted">{(decisions ?? []).length} records</span>
          </div>

          {decisionsError ? (
            <PanelError msg={(decisionsError as Error).message} />
          ) : decisionsLoading ? (
            <Skeleton lines={5} />
          ) : (
            <div className="overflow-y-auto" style={{ maxHeight: "420px" }}>
              {(decisions ?? []).map((d: any, i: number) => {
                const key = `dec-${d.id ?? i}`;
                const isExpanded = expandedRow === key;
                const blocked = d.news_blocked || d.risk_blocked;
                // API returns 'strategy_votes'; fall back to 'strategy_votes_json'
                const votes = d.strategy_votes ?? d.strategy_votes_json ?? [];
                const parsedVotes = typeof votes === "string" ? JSON.parse(votes) : votes;
                const agreeing = Array.isArray(parsedVotes)
                  ? parsedVotes.filter((v: any) =>
                      v.direction === d.resolved_direction ||
                      v.was_agreeing ||
                      v.contributed_to_winning_side
                    ).length
                  : "—";
                return (
                  <div key={key}
                    className={`border-b border-border cursor-pointer hover:bg-white/3 transition-colors ${isExpanded ? "bg-white/2" : ""}`}
                    onClick={() => setExpandedRow(isExpanded ? null : key)}
                  >
                    <div className="flex items-center gap-2 px-1 py-1.5 text-xs">
                      <span className="text-muted tabular-nums w-14 shrink-0 mono">{relTime(d.timestamp)}</span>
                      <span className="text-white font-bold w-16 shrink-0">{d.symbol}</span>
                      <span className={`font-bold w-10 shrink-0 ${
                        d.resolved_direction === "BUY" ? "text-success" :
                        d.resolved_direction === "SELL" ? "text-danger" : "text-muted"
                      }`}>
                        {d.resolved_direction || "—"}
                      </span>
                      <div className="flex-1 flex items-center gap-1">
                        <div className="h-1.5 rounded-sm bg-border w-14 overflow-hidden">
                          <div className={`h-full ${d.resolved_direction === "BUY" ? "bg-success/60" : "bg-danger/60"}`}
                            style={{ width: `${Math.round((d.resolved_confidence || 0) * 100)}%` }} />
                        </div>
                        <span className="text-muted mono">{((d.resolved_confidence || 0) * 100).toFixed(0)}%</span>
                      </div>
                      <span className="w-6 text-center text-muted shrink-0">
                        {blocked
                          ? <span className="text-danger">✗</span>
                          : d.trade_id
                            ? <Zap size={10} className="text-warn inline" />
                            : "·"}
                      </span>
                      <span className="text-muted w-6 text-right shrink-0 mono">{typeof agreeing === "number" ? agreeing : "—"}</span>
                      {isExpanded ? <ChevronUp size={10} className="text-muted shrink-0" /> : <ChevronDown size={10} className="text-muted shrink-0" />}
                    </div>
                    {isExpanded && (
                      <div className="px-2 pb-2 border-t border-border">
                        {d.block_reason && (
                          <div className="text-xs text-danger mb-1 flex items-center gap-1 pt-1">
                            <AlertCircle size={10} />
                            <span>{d.block_reason}</span>
                          </div>
                        )}
                        <VotesBreakdown votes={parsedVotes} />
                        {d.final_entry && (
                          <div className="flex gap-4 mt-1 text-xs text-muted">
                            <span>Entry: <span className="text-white mono">{d.final_entry}</span></span>
                            <span>SL: <span className="text-white mono">{d.final_sl ?? "—"}</span></span>
                            <span>TP1: <span className="text-white mono">{d.final_tp1 ?? "—"}</span></span>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}