import React, { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertOctagon, Play, Calculator, Info } from 'lucide-react'
import {
  getRiskSettings, updateRiskSettings, getRiskStatus, getRiskLotPreview,
  haltTrading, resumeTrading,
} from '../api'
import { Card, SectionHeader, Btn, Input, Select, Spinner, GaugeBar, ConfirmModal } from '../components'
import { useAppStore } from '../store'

// ----------------------------------------------------------------------------
// Inline tooltip helper
// ----------------------------------------------------------------------------
function Tip({ text }: { text: string }) {
  const [show, setShow] = useState(false)
  return (
    <span className="relative inline-block ml-1">
      <Info size={12} className="text-muted cursor-help inline"
        onMouseEnter={() => setShow(true)} onMouseLeave={() => setShow(false)} />
      {show && (
        <span className="absolute left-4 top-0 z-20 bg-panel border border-border rounded px-2 py-1.5
                         text-xs text-muted w-52 shadow-xl leading-relaxed">
          {text}
        </span>
      )}
    </span>
  )
}

// ----------------------------------------------------------------------------
// Main page
// ----------------------------------------------------------------------------
export default function RiskControl() {
  const { addToast } = useAppStore()
  const qc = useQueryClient()
  const [showHaltConfirm, setShowHaltConfirm] = useState(false)
  const [settings, setSettings] = useState<any>(null)

  // Lot-size preview calculator
  const [calcSymbol, setCalcSymbol] = useState('XAUUSD')
  const [calcEntry, setCalcEntry] = useState(2000)
  const [calcSL, setCalcSL] = useState(1985)

  const { data: riskSettings, isLoading: settingsLoading } = useQuery({
    queryKey: ['riskSettings'], queryFn: getRiskSettings,
  })
  const { data: riskStatus } = useQuery({
    queryKey: ['riskStatus'], queryFn: getRiskStatus, refetchInterval: 10000,
  })
  const { data: lotPreview } = useQuery({
    queryKey: ['lotPreview', calcSymbol, calcEntry, calcSL],
    queryFn: () => getRiskLotPreview(calcSymbol, calcEntry, calcSL),
    enabled: calcEntry > 0 && calcSL > 0 && calcEntry !== calcSL,
  })

  useEffect(() => { if (riskSettings && !settings) setSettings(riskSettings) }, [riskSettings])

  const saveMut = useMutation({
    mutationFn: () => updateRiskSettings(settings),
    onSuccess: () => { addToast('success', 'Risk settings saved'); qc.invalidateQueries({ queryKey: ['riskSettings', 'riskStatus'] }) },
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

  const set = (k: string, v: any) => setSettings((s: any) => ({ ...s, [k]: v }))

  const dailyLossPct = riskStatus?.daily_loss_pct ?? 0
  const drawdownPct = riskStatus?.current_drawdown_pct ?? 0
  const openCount = riskStatus?.open_trades_count ?? 0
  const maxOpen = settings?.max_open_trades ?? riskStatus?.max_open_trades ?? 10

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

      {/* Header */}
      <div className="flex items-center justify-between">
        <SectionHeader title="Risk Control" sub="Configure and monitor all risk management parameters" />
        {riskStatus?.trading_halt ? (
          <Btn variant="success" onClick={() => resumeMut.mutate()} disabled={resumeMut.isPending}>
            {resumeMut.isPending ? <Spinner size={14} /> : <Play size={14} />} Resume Trading
          </Btn>
        ) : (
          <Btn variant="danger" size="lg" onClick={() => setShowHaltConfirm(true)}>
            <AlertOctagon size={16} /> EMERGENCY HALT
          </Btn>
        )}
      </div>

      {/* Live Exposure Gauges */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card>
          <p className="text-xs text-muted mb-2">Open Positions<Tip text="Current open trades vs. the maximum allowed at once." /></p>
          <div className="flex items-end gap-2 mb-2">
            <span className="text-2xl font-semibold mono">{openCount}</span>
            <span className="text-sm text-muted mb-1">/ {maxOpen}</span>
          </div>
          <GaugeBar value={openCount} max={maxOpen} color={openCount >= maxOpen ? 'bg-danger' : 'bg-accent'} />
        </Card>
        <Card>
          <p className="text-xs text-muted mb-2">Daily Loss<Tip text="Realised PnL today as % of daily loss limit. Halts at 100%." /></p>
          <div className="flex items-end gap-2 mb-2">
            <span className={`text-2xl font-semibold mono ${dailyLossPct > 80 ? 'text-danger' : ''}`}>
              {dailyLossPct.toFixed(1)}%
            </span>
            <span className="text-sm text-muted mb-1">of limit</span>
          </div>
          <GaugeBar value={dailyLossPct} max={100}
            color={dailyLossPct > 80 ? 'bg-danger' : dailyLossPct > 50 ? 'bg-warn' : 'bg-success'} />
        </Card>
        <Card>
          <p className="text-xs text-muted mb-2">Drawdown<Tip text="Peak-to-trough drawdown." /></p>
          <div className="flex items-end gap-2 mb-2">
            <span className={`text-2xl font-semibold mono ${drawdownPct > 70 ? 'text-danger' : ''}`}>
              {drawdownPct.toFixed(1)}%
            </span>
          </div>
          <GaugeBar value={drawdownPct} max={settings.max_drawdown_pct ?? 20}
            color={drawdownPct > 15 ? 'bg-danger' : 'bg-warn'} />
        </Card>
      </div>

      {/* Account + MT5 info */}
      {riskStatus && (
        <Card>
          <SectionHeader title="Live Account" sub="Values fetched directly from the MT5 bridge" />
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-3 text-sm">
            <div>
              <p className="text-xs text-muted">Balance</p>
              <p className="mono font-semibold mt-0.5">
                {riskStatus.account_balance != null ? `$${Number(riskStatus.account_balance).toLocaleString(undefined, { minimumFractionDigits: 2 })}` : '—'}
              </p>
              <p className="text-xs text-muted mt-0.5">{riskStatus.balance_source === 'mt5_bridge' ? '● live from MT5' : '○ unavailable'}</p>
            </div>
            <div>
              <p className="text-xs text-muted">Equity</p>
              <p className="mono font-semibold mt-0.5">
                {riskStatus.account_equity != null ? `$${Number(riskStatus.account_equity).toLocaleString(undefined, { minimumFractionDigits: 2 })}` : '—'}
              </p>
            </div>
            <div>
              <p className="text-xs text-muted">Today PnL</p>
              <p className={`mono font-semibold mt-0.5 ${(riskStatus.daily_pnl ?? 0) >= 0 ? 'text-success' : 'text-danger'}`}>
                {riskStatus.daily_pnl != null ? `${riskStatus.daily_pnl >= 0 ? '+' : ''}${riskStatus.daily_pnl.toFixed(2)}` : '—'}
              </p>
            </div>
            <div>
              <p className="text-xs text-muted">Status</p>
              <p className={`text-sm font-semibold mt-0.5 ${riskStatus.trading_halt ? 'text-danger' : 'text-success'}`}>
                {riskStatus.trading_halt ? '⏹ HALTED' : '▶ ACTIVE'}
              </p>
            </div>
          </div>
        </Card>
      )}

      {/* Risk Parameters */}
      <Card>
        <SectionHeader title="Risk Parameters" sub="Applied on every new trade. Changes take effect immediately on the next loop iteration." />
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-6 mt-4">
          <Input label="Leverage" type="number" value={settings.leverage ?? 100}
            onChange={(v) => set('leverage', v)} min={1} max={3000} />

          <div>
            <Input
              label="Risk Per Trade (%)"
              type="number"
              value={settings.risk_per_trade_pct ?? 1}
              onChange={(v) => set('risk_per_trade_pct', v)}
              min={0.01} max={100} step={0.1}
            />
            <p className="text-xs text-muted mt-1">
              Only used when Lot Size Mode = DYNAMIC. Has no effect in FIXED mode.
            </p>
          </div>

          <Select label="Lot Size Mode" value={settings.lot_size_mode ?? 'FIXED'}
            onChange={(v) => set('lot_size_mode', v)}
            options={[
              { value: 'FIXED', label: 'Fixed (strategy lot_size param)' },
              { value: 'DYNAMIC', label: 'Dynamic (risk % of balance ÷ SL distance)' },
            ]} />

          <Input label="Max Open Trades" type="number" value={settings.max_open_trades ?? 5}
            onChange={(v) => set('max_open_trades', v)} min={1} max={50} />
          <Input label="Symbol Exposure Limit (lots)" type="number" value={settings.symbol_exposure_limit ?? 1}
            onChange={(v) => set('symbol_exposure_limit', v)} min={0.01} step={0.01} />
          <Input label="Max Daily Loss (%)" type="number" value={settings.max_daily_loss_pct ?? 5}
            onChange={(v) => set('max_daily_loss_pct', v)} min={0.1} max={100} step={0.1} />
          <Input label="Max Drawdown (%)" type="number" value={settings.max_drawdown_pct ?? 20}
            onChange={(v) => set('max_drawdown_pct', v)} min={1} max={100} step={0.5} />
        </div>
        <Btn onClick={() => saveMut.mutate()} disabled={saveMut.isPending}>
          {saveMut.isPending ? <Spinner size={14} /> : null} Save Risk Settings
        </Btn>
      </Card>

      {/* Lot Size Calculator */}
      <Card>
        <div className="flex items-center gap-2 mb-4">
          <Calculator size={16} className="text-accent" />
          <SectionHeader title="Live Lot-Size Preview" />
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4 mb-4">
          <Input label="Symbol" value={calcSymbol} onChange={setCalcSymbol} />
          <Input label="Entry Price" type="number" value={calcEntry} onChange={setCalcEntry} step={0.00001} />
          <Input label="Stop Loss" type="number" value={calcSL} onChange={setCalcSL} step={0.00001} />
        </div>
        {lotPreview && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {[
              ['SL Pips', lotPreview.sl_pips?.toFixed(1) ?? '—'],
              ['Risk Amount', lotPreview.risk_amount_usd != null ? `$${lotPreview.risk_amount_usd.toFixed(2)}` : '—'],
              ['Fixed Lot', lotPreview.fixed_lot ?? '—'],
              ['Dynamic Lot', lotPreview.dynamic_lot ?? '—'],
            ].map(([lbl, val]) => (
              <div key={lbl} className="bg-bg border border-border rounded p-3">
                <p className="text-xs text-muted mb-1">{lbl}</p>
                <p className="text-lg font-semibold mono">{val}</p>
              </div>
            ))}
            {lotPreview.would_be_blocked && (
              <div className="col-span-4 flex items-center gap-2 text-xs text-danger bg-danger/10 border border-danger/20 rounded px-3 py-2">
                <AlertOctagon size={13} />
                <span>Would be blocked: {lotPreview.block_reason}</span>
              </div>
            )}
          </div>
        )}
        <p className="text-xs text-muted mt-3">
          Mode: <span className="text-white">{settings.lot_size_mode}</span>
          {' · '}{settings.risk_per_trade_pct}% risk
          {' · '}Leverage {settings.leverage}x
        </p>
      </Card>
    </div>
  )
}
