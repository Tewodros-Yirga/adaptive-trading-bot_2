import React, { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Zap } from 'lucide-react'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } from 'recharts'
import { getAdaptationLog, getLearningSettings, updateLearningSettings, triggerAdaptation, getParamsHistory } from '../api'
import { Card, SectionHeader, Input, Btn, Spinner } from '../components'
import { useAppStore } from '../store'

export default function Adaptation() {
  const qc = useQueryClient()
  const { addToast } = useAppStore()
  const [lsEdit, setLsEdit] = useState<any>(null)

  const { data: log, isLoading: logLoading } = useQuery({ queryKey: ['adaptLog'], queryFn: () => getAdaptationLog(30) })
  const { data: ls } = useQuery({ queryKey: ['learningSettings'], queryFn: getLearningSettings })
  const { data: history } = useQuery({ queryKey: ['paramsHistory'], queryFn: () => getParamsHistory(30) })

  const lsMut = useMutation({
    mutationFn: updateLearningSettings,
    onSuccess: () => { addToast('success', 'Learning settings saved'); qc.invalidateQueries() },
  })
  const adaptMut = useMutation({
    mutationFn: triggerAdaptation,
    onSuccess: (d: any) => {
      if (d.skipped) addToast('warning', `Skipped: ${d.reason}`)
      else addToast('success', `Adapted to v${d.new_params_version}`)
      qc.invalidateQueries()
    },
  })

  // Build param drift chart from history
  const driftData = React.useMemo(() => {
    if (!history) return []
    return [...history].reverse().map((v: any, i: number) => ({
      v: `v${v.version}`,
      stop_loss_pct: v.params?.stop_loss_pct,
      tp1: v.params?.tp1_multiplier,
      tp2: v.params?.tp2_multiplier,
      ema_1: v.params?.ema_1,
      ema_6: v.params?.ema_6,
    }))
  }, [history])

  return (
    <div className="p-6 fade-in">
      <SectionHeader title="Adaptation & Learning" sub="Monitor how the bot learns from its trading history" />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Controls */}
        <div className="flex flex-col gap-4">
          <Card>
            <div className="flex items-center justify-between mb-3">
              <p className="font-medium text-sm">Learning Controls</p>
              <Btn size="sm" onClick={() => adaptMut.mutate()} disabled={adaptMut.isPending}>
                <Zap size={12} /> Adapt Now
              </Btn>
            </div>
            {ls && (
              <div className="flex flex-col gap-3">
                {[
                  ['adaptation_interval', 'Adapt every N closed trades', 1, 500, 1],
                  ['adaptation_min_closed_trades', 'Min trades for adaptation', 5, 1000, 1],
                  ['adaptation_cooldown_trades', 'Cooldown (trades)', 0, 1000, 1],
                  ['adaptation_lr', 'Learning rate', 0.00001, 0.1, 0.0001],
                  ['adaptation_max_change_pct', 'Max change per step (%)', 0.01, 5.0, 0.01],
                  ['adaptation_confidence_threshold', 'Min confidence threshold', 0, 1.0, 0.01],
                ].map(([k, label, min, max, step]) => (
                  <Input
                    key={k}
                    label={label as string}
                    value={lsEdit?.[k] ?? ls[k]}
                    type="number"
                    min={min as number}
                    max={max as number}
                    step={step as number}
                    onChange={v => setLsEdit((e: any) => ({ ...(e ?? ls), [k]: v }))}
                  />
                ))}
                <Btn size="sm" onClick={() => lsMut.mutate(lsEdit ?? ls)} disabled={lsMut.isPending}>
                  Save Settings
                </Btn>
              </div>
            )}
          </Card>
        </div>

        {/* Right side */}
        <div className="lg:col-span-2 flex flex-col gap-4">
          {/* Param drift chart */}
          {driftData.length > 1 && (
            <Card>
              <p className="font-medium text-sm mb-3">Parameter Drift Over Time</p>
              <ResponsiveContainer width="100%" height={180}>
                <LineChart data={driftData}>
                  <XAxis dataKey="v" tick={{ fontSize: 9, fill: '#6b7280' }} />
                  <YAxis tick={{ fontSize: 9, fill: '#6b7280' }} axisLine={false} tickLine={false} />
                  <Tooltip contentStyle={{ background: '#111827', border: '1px solid #1e2d3d', fontSize: 11 }} />
                  <Legend wrapperStyle={{ fontSize: 10 }} />
                  <Line type="monotone" dataKey="stop_loss_pct" stroke="#ef4444" dot={false} strokeWidth={1.5} name="SL%" />
                  <Line type="monotone" dataKey="tp1" stroke="#10b981" dot={false} strokeWidth={1.5} name="TP1 mult" />
                  <Line type="monotone" dataKey="tp2" stroke="#3b82f6" dot={false} strokeWidth={1.5} name="TP2 mult" />
                  <Line type="monotone" dataKey="ema_1" stroke="#f59e0b" dot={false} strokeWidth={1.5} name="EMA1" />
                </LineChart>
              </ResponsiveContainer>
            </Card>
          )}

          {/* Adaptation log */}
          <Card>
            <p className="font-medium text-sm mb-3">Adaptation Log</p>
            {logLoading ? <Spinner /> : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-muted border-b border-border">
                      <th className="text-left py-1 pr-3">Date</th>
                      <th className="text-left py-1 pr-3">Trades</th>
                      <th className="text-left py-1 pr-3">Win%</th>
                      <th className="text-left py-1 pr-3">PF</th>
                      <th className="text-left py-1 pr-3">Conf</th>
                      <th className="text-left py-1 pr-3">Delta</th>
                      <th className="text-left py-1">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(log || []).map((l: any) => (
                      <tr key={l.id} className="border-b border-border/50 hover:bg-white/5">
                        <td className="py-1.5 pr-3 text-muted">{l.evaluated_at?.slice(0, 16).replace('T', ' ')}</td>
                        <td className="py-1.5 pr-3 mono">{l.trades_evaluated}</td>
                        <td className="py-1.5 pr-3 mono">{l.win_rate}%</td>
                        <td className="py-1.5 pr-3 mono">{l.profit_factor}</td>
                        <td className="py-1.5 pr-3 mono">{l.confidence_score?.toFixed(3)}</td>
                        <td className="py-1.5 pr-3 mono">{l.delta_magnitude?.toFixed(4)}</td>
                        <td className="py-1.5 text-muted max-w-xs truncate">
                          {(() => { try { return JSON.parse(l.actions_taken).map((a: any) => a.detail).join('; ') } catch { return l.actions_taken } })()}
                        </td>
                      </tr>
                    ))}
                    {(!log || log.length === 0) && <tr><td colSpan={7} className="text-center text-muted py-4">No adaptation history</td></tr>}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  )
}
