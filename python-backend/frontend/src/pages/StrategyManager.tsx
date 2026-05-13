import React, { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, Radio, Settings } from 'lucide-react'
import { getStrategies, setStrategyLive, activateStrategy, deactivateStrategy, updateStrategyParams, getEnsembleConfig, updateEnsembleConfig } from '../api'
import { Card, Btn, Badge, SectionHeader, Input, Select, ConfirmModal, Spinner } from '../components'
import { useAppStore } from '../store'
import clsx from 'clsx'

export default function StrategyManager() {
  const qc = useQueryClient()
  const { addToast } = useAppStore()
  const [expanded, setExpanded] = useState<string | null>(null)
  const [confirmLive, setConfirmLive] = useState<string | null>(null)
  const [editParams, setEditParams] = useState<Record<string, any>>({})

  const { data: strategies, isLoading } = useQuery({ queryKey: ['strategies'], queryFn: getStrategies })
  const { data: ensemble } = useQuery({ queryKey: ['ensemble'], queryFn: getEnsembleConfig })
  const [ensembleEdit, setEnsembleEdit] = useState<any>(null)

  const liveMut = useMutation({
    mutationFn: (name: string) => setStrategyLive(name),
    onSuccess: () => { addToast('success', 'Strategy set as live'); qc.invalidateQueries() },
    onError: (e: any) => addToast('error', e.message),
  })
  const activateMut = useMutation({ mutationFn: activateStrategy, onSuccess: () => qc.invalidateQueries() })
  const deactivateMut = useMutation({ mutationFn: deactivateStrategy, onSuccess: () => qc.invalidateQueries() })
  const paramsMut = useMutation({
    mutationFn: ({ name, params }: any) => updateStrategyParams(name, params),
    onSuccess: () => { addToast('success', 'Params updated'); qc.invalidateQueries() },
  })
  const ensembleMut = useMutation({
    mutationFn: updateEnsembleConfig,
    onSuccess: () => { addToast('success', 'Ensemble config saved'); qc.invalidateQueries() },
  })

  const handleExpand = (name: string, params: any) => {
    if (expanded === name) { setExpanded(null); return }
    setExpanded(name)
    setEditParams({ ...params })
  }

  if (isLoading) return <div className="p-6 flex justify-center"><Spinner /></div>

  return (
    <div className="p-6 fade-in">
      <SectionHeader title="Strategy Manager" sub="Configure and manage all trading strategies" />

      {/* Strategy list */}
      <div className="flex flex-col gap-2 mb-6">
        {(strategies || []).map((s: any) => (
          <Card key={s.name} className="p-0 overflow-hidden">
            {/* Header row */}
            <div
              className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-white/5 transition-colors"
              onClick={() => handleExpand(s.name, s.params)}
            >
              {expanded === s.name ? <ChevronDown size={14} className="text-muted" /> : <ChevronRight size={14} className="text-muted" />}
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-sm">{s.display_name}</span>
                  {s.is_live && <Badge label="LIVE" color="bg-success/20 text-success" />}
                  {s.is_active && !s.is_live && <Badge label="SHADOW" color="bg-accent/20 text-accent" />}
                  {!s.is_active && <Badge label="INACTIVE" color="bg-muted/20 text-muted" />}
                </div>
                <p className="text-xs text-muted mt-0.5">{s.description}</p>
              </div>
              <div className="text-right text-xs hidden sm:block">
                <div className="text-muted">Win Rate</div>
                <div className="mono">{s.win_rate ?? 0}%</div>
              </div>
              <div className="text-right text-xs hidden sm:block">
                <div className="text-muted">PF</div>
                <div className="mono">{s.profit_factor ?? 0}</div>
              </div>
              <div className="text-right text-xs hidden sm:block">
                <div className="text-muted">Trades</div>
                <div className="mono">{s.total_trades ?? 0}</div>
              </div>
            </div>

            {/* Expanded params editor */}
            {expanded === s.name && (
              <div className="border-t border-border px-4 py-4">
                <div className="flex flex-wrap gap-2 mb-4">
                  {!s.is_live && (
                    <Btn size="sm" variant="success" onClick={() => setConfirmLive(s.name)}>
                      <Radio size={12} /> Set as LIVE
                    </Btn>
                  )}
                  {!s.is_active && (
                    <Btn size="sm" variant="outline" onClick={() => activateMut.mutate(s.name)}>Activate</Btn>
                  )}
                  {s.is_active && !s.is_live && (
                    <Btn size="sm" variant="ghost" onClick={() => deactivateMut.mutate(s.name)}>Deactivate</Btn>
                  )}
                </div>

                {/* Params grid */}
                <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3 mb-4">
                  {Object.entries(editParams)
                    .filter(([k]) => !k.startsWith('min_') && !k.startsWith('max_'))
                    .map(([k, v]) => (
                      <Input
                        key={k}
                        label={k.replace(/_/g, ' ')}
                        value={editParams[k]}
                        type="number"
                        step={typeof v === 'float' || String(v).includes('.') ? 0.001 : 1}
                        onChange={(val) => setEditParams((p: any) => ({ ...p, [k]: val }))}
                      />
                    ))}
                </div>
                <Btn
                  size="sm"
                  onClick={() => paramsMut.mutate({ name: s.name, params: editParams })}
                  disabled={paramsMut.isPending}
                >
                  <Settings size={12} /> Save Params
                </Btn>
              </div>
            )}
          </Card>
        ))}
      </div>

      {/* Ensemble config */}
      <Card>
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-medium text-sm">Ensemble Configuration</h3>
        </div>
        {ensemble && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <Select
              label="Mode"
              value={ensembleEdit?.mode ?? ensemble.mode}
              onChange={(v) => setEnsembleEdit((e: any) => ({ ...(e ?? ensemble), mode: v }))}
              options={[
                { label: 'Dominant', value: 'DOMINANT' },
                { label: 'Weighted Vote', value: 'WEIGHTED_VOTE' },
                { label: 'Unanimous', value: 'UNANIMOUS' },
              ]}
            />
            <Select
              label="Dominant Strategy"
              value={ensembleEdit?.dominant_strategy ?? ensemble.dominant_strategy}
              onChange={(v) => setEnsembleEdit((e: any) => ({ ...(e ?? ensemble), dominant_strategy: v }))}
              options={(strategies || []).map((s: any) => ({ label: s.display_name, value: s.name }))}
            />
            <Input
              label="Min Confirmations"
              value={ensembleEdit?.min_confirmations ?? ensemble.min_confirmations}
              type="number"
              min={1}
              max={10}
              onChange={(v) => setEnsembleEdit((e: any) => ({ ...(e ?? ensemble), min_confirmations: v }))}
            />
          </div>
        )}
        <div className="mt-3">
          <Btn size="sm" onClick={() => ensembleMut.mutate(ensembleEdit ?? ensemble)} disabled={ensembleMut.isPending}>
            Save Ensemble Config
          </Btn>
        </div>
      </Card>

      {confirmLive && (
        <ConfirmModal
          title={`Set ${confirmLive} as LIVE?`}
          message="The current live strategy will move to shadow mode. All real orders will now be placed by the new strategy."
          onConfirm={() => { liveMut.mutate(confirmLive); setConfirmLive(null) }}
          onCancel={() => setConfirmLive(null)}
        />
      )}
    </div>
  )
}
