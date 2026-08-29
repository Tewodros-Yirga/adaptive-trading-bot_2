import React, { useState } from 'react'
import clsx from 'clsx'
import type { StrategyPairAnalysis } from '../api/types'

interface SynergyMatrixProps {
  strategies: string[]
  pairs: StrategyPairAnalysis[]
}

function synergyColor(score: number): string {
  if (score > 1.05) return 'bg-success/30 text-success border-success/20'
  if (score < 0.95) return 'bg-danger/20 text-danger border-danger/20'
  return 'bg-warn/20 text-warn border-warn/20'
}

export const SynergyMatrix = ({ strategies, pairs }: SynergyMatrixProps) => {
  const [hovered, setHovered] = useState<StrategyPairAnalysis | null>(null)

  const lookup = new Map<string, StrategyPairAnalysis>()
  pairs.forEach(p => {
    const key = [...p.strategy_names_json].sort().join('|')
    lookup.set(key, p)
  })

  const getPair = (a: string, b: string) => {
    const key = [a, b].sort().join('|')
    return lookup.get(key) ?? null
  }

  return (
    <div className="relative">
      <div className="overflow-x-auto">
        <table className="text-xs border-collapse">
          <thead>
            <tr>
              <th className="p-1.5 text-muted text-right w-28" />
              {strategies.map(s => (
                <th key={s} className="p-1.5 text-muted text-center max-w-16">
                  <span className="block truncate text-xs" title={s}>{s.slice(0, 8)}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {strategies.map(row => (
              <tr key={row}>
                <td className="p-1.5 text-muted text-right pr-3 text-xs truncate max-w-28">
                  {row.slice(0, 10)}
                </td>
                {strategies.map(col => {
                  if (row === col) {
                    return (
                      <td key={col} className="p-1 text-center">
                        <div className="w-10 h-8 bg-border/30 rounded flex items-center justify-center text-muted">
                          —
                        </div>
                      </td>
                    )
                  }
                  const pair = getPair(row, col)
                  if (!pair) {
                    return (
                      <td key={col} className="p-1 text-center">
                        <div className="w-10 h-8 bg-border/20 rounded" />
                      </td>
                    )
                  }
                  return (
                    <td key={col} className="p-1 text-center">
                      <div
                        className={clsx(
                          'w-10 h-8 rounded border cursor-pointer flex items-center justify-center font-mono font-semibold transition-all hover:scale-110',
                          synergyColor(pair.synergy_score),
                          hovered === pair && 'ring-2 ring-accent'
                        )}
                        onMouseEnter={() => setHovered(pair)}
                        onMouseLeave={() => setHovered(null)}
                        title={`${row} × ${col}: ${pair.synergy_score.toFixed(2)}`}
                      >
                        {pair.synergy_score.toFixed(2)}
                      </div>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Tooltip / hover panel */}
      {hovered && hovered.analysis_json && (
        <div className="mt-3 p-3 bg-bg border border-border rounded-lg text-xs">
          <p className="font-medium text-white mb-1">
            {hovered.strategy_names_json.join(' × ')}
            <span className={clsx('ml-2 px-1.5 py-0.5 rounded', synergyColor(hovered.synergy_score))}>
              synergy {hovered.synergy_score.toFixed(2)}
            </span>
          </p>
          <p className="text-muted/90 mb-1">{hovered.analysis_json.narrative}</p>
          <div className="grid grid-cols-2 gap-2 mt-2">
            <div>
              <p className="text-success text-xs mb-0.5">✓ Works well when</p>
              <p className="text-muted">{hovered.analysis_json.works_well_when}</p>
            </div>
            <div>
              <p className="text-danger text-xs mb-0.5">⚠ Watch out for</p>
              <p className="text-muted">{hovered.analysis_json.watch_out_for}</p>
            </div>
          </div>
        </div>
      )}

      {/* Legend */}
      <div className="flex items-center gap-4 mt-3 text-xs text-muted">
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded bg-success/30" /> High synergy (&gt;1.05)
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded bg-warn/20" /> Neutral (0.95–1.05)
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded bg-danger/20" /> Low synergy (&lt;0.95)
        </span>
      </div>
    </div>
  )
}