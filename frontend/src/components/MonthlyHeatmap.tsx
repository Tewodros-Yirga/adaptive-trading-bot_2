import React from 'react'
import type { MonthlyBreakdown } from '../api/types'

interface MonthlyHeatmapProps {
  data: MonthlyBreakdown
}

export const MonthlyHeatmap = ({ data }: MonthlyHeatmapProps) => {
  if (!data || Object.keys(data).length === 0) {
    return <p className="text-muted text-xs">No monthly data available.</p>
  }

  const entries = Object.entries(data).sort(([a], [b]) => a.localeCompare(b))
  const pnls = entries.map(([, v]) => v.net_pnl ?? 0)
  const maxAbs = Math.max(...pnls.map(Math.abs), 1)

  const cellColor = (pnl: number) => {
    const intensity = Math.min(Math.abs(pnl) / maxAbs, 1)
    if (pnl > 0) return `rgba(16,185,129,${0.15 + intensity * 0.55})`
    if (pnl < 0) return `rgba(239,68,68,${0.15 + intensity * 0.55})`
    return 'rgba(107,114,128,0.15)'
  }

  return (
    <div className="flex flex-wrap gap-1.5">
      {entries.map(([month, stats]) => {
        const pnl = stats.net_pnl ?? 0
        const wins = stats.wins ?? 0
        const losses = stats.losses ?? 0
        return (
          <div
            key={month}
            className="rounded p-2 min-w-[72px] border border-white/5 cursor-default group relative"
            style={{ background: cellColor(pnl) }}
            title={`${month}: ${wins}W / ${losses}L`}
          >
            <p className="text-xs text-white/80 font-mono">{month.slice(5)}</p>
            <p className="text-sm font-semibold mono text-white">
              {pnl > 0 ? '+' : ''}{pnl.toFixed(0)}
            </p>
            <p className="text-xs text-white/60">{wins}W {losses}L</p>

            {/* Tooltip on hover */}
            <div className="absolute bottom-full left-0 mb-1 hidden group-hover:block z-10 bg-panel border border-border rounded p-2 text-xs whitespace-nowrap shadow-xl">
              <p className="font-medium">{month}</p>
              <p className="text-success">Wins: {wins}</p>
              <p className="text-danger">Losses: {losses}</p>
              <p className={pnl >= 0 ? 'text-success' : 'text-danger'}>
                PnL: {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}
              </p>
            </div>
          </div>
        )
      })}
    </div>
  )
}