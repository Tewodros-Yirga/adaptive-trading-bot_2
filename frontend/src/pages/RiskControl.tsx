import React, { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertOctagon, Play, Calculator, Info } from 'lucide-react'
import { getRiskSettings, updateRiskSettings, getRiskStatus, haltTrading, resumeTrading } from '../api'
import { Card, SectionHeader, Btn, Input, Select, Spinner, GaugeBar, ConfirmModal } from '../components'
import { useAppStore } from '../store'

function Tooltip({ text }: { text: string }) {
  const [show, setShow] = useState(false)
  return (
    <span className="relative inline-block ml-1">
      <Info
        size={12}
        className="text-muted cursor-help inline"
        onMouseEnter={() => setShow(true)}
        onMouseLeave={() => setShow(false)}
      />
      {show && (
        <span className="absolute left-4 top-0 z-10 bg-panel border border-border rounded px-2 py-1 text-xs text-muted w-48 shadow-xl">
          {text}
        </span>
      )}
    </span>
  )
}

export default function RiskControl() {
  const { addToast } = useAppStore()
  const qc = useQueryClient()
  const [showHaltConfirm, setShowHaltConfirm] = useState(false)
  const [settings, setSettings] = useState<any>(null)

  // Risk calculator state
  const [calcEntry, setCalcEntry] = useState(1900)
  const [calcSL, setCalcSL] = useState(1895)

  const { data: riskSettings, isLoading: settingsLoading } = useQuery({
    queryKey: ['riskSettings'],
    queryFn: getRiskSettings,
  })
  const { data: riskStatus } = useQuery({
    queryKey: ['riskStatus'],
    queryFn: getRiskStatus,
    refetchInterval: 10000,
  })

  useEffect(() => {
    if (riskSettings && !settings) setSettings(riskSettings)
  }, [riskSettings])

  const saveMut = useMutation({
    mutationFn: () => updateRiskSettings(settings),
    onSuccess: () => { addToast('success', 'Risk settings saved'); qc.invalidateQueries({ queryKey: ['riskSettings'] }) },
    onError: (e: any) => addToast('error', e.message),
  })
  const haltMut = useMutation({
    mutationFn: haltTrading,
    onSuccess: () => { addToast('warning', 'Trading halted'); qc.invalidateQueries({ queryKey: ['riskStatus', 'riskSettings'] }); setShowHaltConfirm(false) },
    onError: (e: any) => addToast('error', e.message),
  })
  const resumeMut = useMutation({
    mutationFn: resumeTrading,
    onSuccess: () => { addToast('success', 'Trading resumed'); qc.invalidateQueries({ queryKey: ['riskStatus', 'riskSettings'] }) },
    onError: (e: any) => addToast('error', e.message),
  })

  const computedLot = React.useMemo(() => {
    if (!settings) return null
    const balance = settings.account_balance ?? 10000
    const riskAmt = balance * ((settings.risk_per_trade_pct ?? 1) / 100)
    const slDist = Math.abs(calcEntry - calcSL)
    if (slDist === 0) return null
    const lot = riskAmt / (slDist * 100)
    return Math.max(0.01, Math.min(lot, 100)).toFixed(2)
  }, [settings, calcEntry, calcSL])

  const dailyLossPct = riskStatus?.daily_loss_pct ?? 0
  const drawdownPct = riskStatus?.current_drawdown_pct ?? 0
  const openTradesCount = riskStatus?.open_trades_count ?? 0
  const maxOpenTrades = riskStatus?.max_open_trades ?? settings?.max_open_trades ?? 10

  if (settingsLoading || !settings) {
    return <div className="flex justify-center items-center h-64"><Spinner size={32} /></div>
  }

  return (
    <div className="p-6 space-y-6">
      {showHaltConfirm && (
        <ConfirmModal
          title="Emergency Halt"
          message="All trading will be immediately halted. No new orders will be placed until manually resumed."
          variant="danger"
          onConfirm={() => haltMut.mutate()}
          onCancel={() => setShowHaltConfirm(false)}
        />
      )}

      <div className="flex items-center justify-between">
        <SectionHeader title="Risk Control" sub="Configure and monitor all risk management parameters" />
        {riskStatus?.trading_halt ? (
          <Btn variant="success" onClick={() => resumeMut.mutate()} disabled={resumeMut.isPending}>
            {resumeMut.isPending ? <Spinner size={14} /> : <Play size={14} />}
            Resume Trading
          </Btn>
        ) : (
          <Btn variant="danger" size="lg" onClick={() => setShowHaltConfirm(true)}>
            <AlertOctagon size={16} />
            EMERGENCY HALT
          </Btn>
        )}
      </div>

      {/* Live Exposure */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card>
          <p className="text-xs text-muted mb-2">
            Open Positions
            <Tooltip text="Current number of open trades vs the maximum allowed" />
          </p>
          <div className="flex items-end gap-2 mb-2">
            <span className="text-2xl font-semibold mono">{openTradesCount}</span>
            <span className="text-sm text-muted mb-1">/ {maxOpenTrades}</span>
          </div>
          <GaugeBar value={openTradesCount} max={maxOpenTrades} color={openTradesCount >= maxOpenTrades ? 'bg-danger' : 'bg-accent'} />
        </Card>
        <Card>
          <p className="text-xs text-muted mb-2">
            Daily Loss
            <Tooltip text="Today's realized PnL as % of daily loss limit. Halts automatically at 100%." />
          </p>
          <div className="flex items-end gap-2 mb-2">
            <span className={`text-2xl font-semibold mono ${dailyLossPct > 80 ? 'text-danger' : ''}`}>
              {dailyLossPct.toFixed(1)}%
            </span>
            <span className="text-sm text-muted mb-1">of limit</span>
          </div>
          <GaugeBar value={dailyLossPct} max={100} color={dailyLossPct > 80 ? 'bg-danger' : dailyLossPct > 50 ? 'bg-warn' : 'bg-success'} />
        </Card>
        <Card>
          <p className="text-xs text-muted mb-2">
            Current Drawdown
            <Tooltip text="Peak-to-trough drawdown % vs max allowed before halt triggers." />
          </p>
          <div className="flex items-end gap-2 mb-2">
            <span className={`text-2xl font-semibold mono ${drawdownPct > 70 ? 'text-danger' : ''}`}>
              {drawdownPct.toFixed(1)}%
            </span>
          </div>
          <GaugeBar value={drawdownPct} max={settings.max_drawdown_pct ?? 20} color={drawdownPct > 15 ? 'bg-danger' : 'bg-warn'} />
        </Card>
      </div>

      {/* Settings Form */}
      <Card>
        <SectionHeader title="Risk Parameters" sub="Changes take effect on the next trade evaluation" />
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
          <Input
            label="Account Balance ($)"
            type="number"
            value={settings.account_balance ?? 10000}
            onChange={(v) => setSettings((s: any) => ({ ...s, account_balance: v }))}
            min={0}
          />
          <Input
            label="Leverage"
            type="number"
            value={settings.leverage ?? 100}
            onChange={(v) => setSettings((s: any) => ({ ...s, leverage: v }))}
            min={1}
            max={3000}
          />
          <Input
            label="Risk Per Trade (%)"
            type="number"
            value={settings.risk_per_trade_pct ?? 1}
            onChange={(v) => setSettings((s: any) => ({ ...s, risk_per_trade_pct: v }))}
            min={0.01}
            max={10}
            step={0.1}
          />
          <Input
            label="Max Open Trades"
            type="number"
            value={settings.max_open_trades ?? 5}
            onChange={(v) => setSettings((s: any) => ({ ...s, max_open_trades: v }))}
            min={1}
            max={50}
          />
          <Input
            label="Max Daily Loss (%)"
            type="number"
            value={settings.max_daily_loss_pct ?? 3}
            onChange={(v) => setSettings((s: any) => ({ ...s, max_daily_loss_pct: v }))}
            min={0.1}
            max={100}
            step={0.1}
          />
          <Input
            label="Max Drawdown (%)"
            type="number"
            value={settings.max_drawdown_pct ?? 20}
            onChange={(v) => setSettings((s: any) => ({ ...s, max_drawdown_pct: v }))}
            min={1}
            max={100}
            step={0.5}
          />
          <Input
            label="Symbol Exposure Limit (lots)"
            type="number"
            value={settings.symbol_exposure_limit ?? 1}
            onChange={(v) => setSettings((s: any) => ({ ...s, symbol_exposure_limit: v }))}
            min={0.01}
            step={0.01}
          />
          <Select
            label="Lot Size Mode"
            value={settings.lot_size_mode ?? 'FIXED'}
            onChange={(v) => setSettings((s: any) => ({ ...s, lot_size_mode: v }))}
            options={[
              { value: 'FIXED', label: 'Fixed (use strategy param)' },
              { value: 'DYNAMIC', label: 'Dynamic (risk-based sizing)' },
            ]}
          />
        </div>
        <Btn onClick={() => saveMut.mutate()} disabled={saveMut.isPending}>
          {saveMut.isPending ? <Spinner size={14} /> : null}
          Save Settings
        </Btn>
      </Card>

      {/* Risk Calculator */}
      <Card>
        <div className="flex items-center gap-2 mb-4">
          <Calculator size={16} className="text-accent" />
          <SectionHeader title="Lot Size Calculator" />
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
          <Input label="Entry Price" type="number" value={calcEntry} onChange={setCalcEntry} step={0.00001} />
          <Input label="Stop Loss" type="number" value={calcSL} onChange={setCalcSL} step={0.00001} />
          <div className="flex flex-col gap-1">
            <label className="text-xs text-muted">SL Distance (pips)</label>
            <div className="bg-bg border border-border rounded px-3 py-1.5 text-sm mono">
              {Math.abs(calcEntry - calcSL).toFixed(5)}
            </div>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs text-muted">Computed Lot Size</label>
            <div className="bg-accent/10 border border-accent/30 rounded px-3 py-1.5 text-sm mono text-accent font-semibold">
              {computedLot ?? '—'}
            </div>
          </div>
        </div>
        <p className="text-xs text-muted">
          Based on {settings.account_balance?.toLocaleString() ?? '—'} balance · {settings.risk_per_trade_pct ?? 1}% risk · {settings.leverage ?? 100}x leverage
        </p>
      </Card>
    </div>
  )
}
