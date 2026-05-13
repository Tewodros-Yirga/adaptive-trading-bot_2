import React, { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertOctagon, Play, ShieldCheck } from 'lucide-react'
import { getRiskSettings, updateRiskSettings, getRiskStatus, haltTrading, resumeTrading } from '../api'
import { Card, SectionHeader, Input, Select, Btn, GaugeBar, KpiCard, ConfirmModal, Spinner } from '../components'
import { useAppStore } from '../store'

export default function RiskControl() {
  const qc = useQueryClient()
  const { addToast } = useAppStore()
  const [form, setForm] = useState<any>(null)
  const [confirmHalt, setConfirmHalt] = useState(false)

  const { data: settings, isLoading } = useQuery({ queryKey: ['riskSettings'], queryFn: getRiskSettings })
  const { data: status } = useQuery({ queryKey: ['riskStatus'], queryFn: getRiskStatus, refetchInterval: 10000 })

  useEffect(() => { if (settings && !form) setForm({ ...settings }) }, [settings])

  const updateMut = useMutation({
    mutationFn: updateRiskSettings,
    onSuccess: () => { addToast('success', 'Risk settings saved'); qc.invalidateQueries() },
    onError: (e: any) => addToast('error', e.message),
  })
  const haltMut = useMutation({ mutationFn: haltTrading, onSuccess: () => { addToast('warning', 'Trading HALTED'); qc.invalidateQueries() } })
  const resumeMut = useMutation({ mutationFn: resumeTrading, onSuccess: () => { addToast('success', 'Trading resumed'); qc.invalidateQueries() } })

  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }))

  if (isLoading || !form) return <div className="p-6 flex justify-center"><Spinner /></div>

  const isHalted = status?.trading_halt
  const dailyLossPct = status?.daily_loss_pct ?? 0
  const ddPct = status?.current_drawdown_pct ?? 0

  // Risk calculator state
  const [calcEntry, setCalcEntry] = useState(1900)
  const [calcSl, setCalcSl] = useState(1895)
  const [calcSymbol, setCalcSymbol] = useState('XAUUSD')
  const calcRisk = form.account_balance * (form.risk_per_trade_pct / 100)
  const calcSlDist = Math.abs(calcEntry - calcSl)
  const calcLots = calcSlDist > 0 ? Math.max(0.01, +(calcRisk / (calcSlDist * 100000)).toFixed(2)) : 0

  return (
    <div className="p-6 fade-in">
      <SectionHeader title="Risk Control Center" sub="Manage position sizing, exposure limits, and emergency controls" />

      {/* Status row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
        <KpiCard label="Open Trades" value={status?.open_trades_count ?? 0} sub={`limit: ${form.max_open_trades}`} />
        <KpiCard label="Daily P&L" value={status?.daily_pnl?.toFixed(2) ?? '—'} color={status?.daily_pnl >= 0 ? 'text-success' : 'text-danger'} />
        <KpiCard label="Daily Loss" value={`${dailyLossPct.toFixed(1)}%`} sub={`limit: ${form.max_daily_loss_pct}%`} color={dailyLossPct > form.max_daily_loss_pct * 0.8 ? 'text-danger' : 'text-warn'} />
        <KpiCard label="Drawdown" value={`${ddPct.toFixed(1)}%`} sub={`limit: ${form.max_drawdown_pct}%`} color={ddPct > form.max_drawdown_pct * 0.7 ? 'text-danger' : 'text-success'} />
      </div>

      {/* Gauges */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-6">
        <Card>
          <p className="text-xs text-muted mb-1">Daily Loss Progress</p>
          <GaugeBar value={dailyLossPct} max={form.max_daily_loss_pct} color={dailyLossPct > form.max_daily_loss_pct * 0.8 ? 'bg-danger' : 'bg-warn'} />
          <p className="text-xs text-muted mt-1">{dailyLossPct.toFixed(2)}% / {form.max_daily_loss_pct}%</p>
        </Card>
        <Card>
          <p className="text-xs text-muted mb-1">Drawdown</p>
          <GaugeBar value={ddPct} max={form.max_drawdown_pct} color={ddPct > form.max_drawdown_pct * 0.7 ? 'bg-danger' : 'bg-accent'} />
          <p className="text-xs text-muted mt-1">{ddPct.toFixed(2)}% / {form.max_drawdown_pct}%</p>
        </Card>
      </div>

      {/* Emergency halt */}
      <Card className="mb-6 border-danger/30">
        <div className="flex items-center justify-between">
          <div>
            <p className="font-medium text-sm mb-1">Emergency Controls</p>
            <p className="text-xs text-muted">Current status: <span className={isHalted ? 'text-danger font-medium' : 'text-success font-medium'}>{isHalted ? 'HALTED' : 'ACTIVE'}</span></p>
          </div>
          <div className="flex gap-2">
            {!isHalted
              ? <Btn variant="danger" onClick={() => setConfirmHalt(true)}><AlertOctagon size={14} /> EMERGENCY HALT</Btn>
              : <Btn variant="success" onClick={() => resumeMut.mutate()}><Play size={14} /> Resume Trading</Btn>}
          </div>
        </div>
      </Card>

      {/* Settings form */}
      <Card className="mb-6">
        <p className="font-medium text-sm mb-4">Risk Parameters</p>
        <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
          <Input label="Account Balance ($)" value={form.account_balance} type="number" step={100} onChange={v => set('account_balance', v)} />
          <Input label="Leverage (1:X)" value={form.leverage} type="number" min={1} max={2000} onChange={v => set('leverage', v)} />
          <Input label="Risk Per Trade (%)" value={form.risk_per_trade_pct} type="number" step={0.1} min={0.01} max={10} onChange={v => set('risk_per_trade_pct', v)} />
          <Input label="Max Open Trades" value={form.max_open_trades} type="number" min={1} max={100} onChange={v => set('max_open_trades', v)} />
          <Input label="Max Daily Loss (%)" value={form.max_daily_loss_pct} type="number" step={0.5} min={0.1} max={50} onChange={v => set('max_daily_loss_pct', v)} />
          <Input label="Max Drawdown (%)" value={form.max_drawdown_pct} type="number" step={1} min={1} max={100} onChange={v => set('max_drawdown_pct', v)} />
          <Input label="Symbol Exposure Limit (lots)" value={form.symbol_exposure_limit} type="number" step={0.01} min={0.01} onChange={v => set('symbol_exposure_limit', v)} />
          <Select
            label="Lot Size Mode"
            value={form.lot_size_mode}
            onChange={v => set('lot_size_mode', v)}
            options={[{ label: 'Fixed (use strategy params)', value: 'FIXED' }, { label: 'Dynamic (risk-based)', value: 'DYNAMIC' }]}
          />
        </div>
        <div className="mt-4">
          <Btn onClick={() => updateMut.mutate(form)} disabled={updateMut.isPending}>
            <ShieldCheck size={14} /> Save Risk Settings
          </Btn>
        </div>
      </Card>

      {/* Risk Calculator */}
      <Card>
        <p className="font-medium text-sm mb-3">Lot Size Calculator</p>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
          <Input label="Symbol" value={calcSymbol} onChange={setCalcSymbol} />
          <Input label="Entry Price" value={calcEntry} type="number" step={0.01} onChange={setCalcEntry} />
          <Input label="Stop Loss" value={calcSl} type="number" step={0.01} onChange={setCalcSl} />
          <div className="flex flex-col gap-1">
            <label className="text-xs text-muted">Computed Lot Size</label>
            <div className="bg-bg border border-border rounded px-3 py-1.5 text-success font-medium mono text-sm h-9 flex items-center">
              {calcLots}
            </div>
          </div>
        </div>
        <p className="text-xs text-muted">
          Risk amount: ${calcRisk.toFixed(2)} | SL distance: {calcSlDist.toFixed(5)} | Mode: {form.lot_size_mode}
        </p>
      </Card>

      {confirmHalt && (
        <ConfirmModal
          title="EMERGENCY HALT"
          message="This will immediately stop all new trade entries. Existing open positions will NOT be closed automatically. Are you sure?"
          onConfirm={() => { haltMut.mutate(); setConfirmHalt(false) }}
          onCancel={() => setConfirmHalt(false)}
          variant="danger"
        />
      )}
    </div>
  )
}
