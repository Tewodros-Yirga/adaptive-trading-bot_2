import React, { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Eye, EyeOff, RefreshCw, Wifi, WifiOff, AlertTriangle } from 'lucide-react'
import { getRiskSettings, updateRiskSettings, getBridgeAccount } from '../api'
import { Card, SectionHeader, Btn, Input, Select, Spinner, ConfirmModal } from '../components'
import { useAppStore } from '../store'

function SecretInput({ label, value, onChange, placeholder }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string
}) {
  const [show, setShow] = useState(false)
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs text-muted">{label}</label>
      <div className="relative">
        <input
          type={show ? 'text' : 'password'}
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          className="w-full bg-bg border border-border rounded px-3 py-1.5 text-sm text-white focus:border-accent outline-none mono pr-8"
        />
        <button
          type="button"
          onClick={() => setShow(!show)}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-muted hover:text-white"
        >
          {show ? <EyeOff size={14} /> : <Eye size={14} />}
        </button>
      </div>
    </div>
  )
}

// API key settings are stored as AppSetting records via the risk settings endpoint
// In a full implementation each key would have its own endpoint; here we use
// a local state form that posts to /api/settings (extend as needed)
const DEFAULT_API_KEYS = {
  newsapi_key: '',
  alphavantage_key: '',
  finnhub_key: '',
  anthropic_api_key: '',
}

export default function SystemSettings() {
  const { addToast, wsConnected } = useAppStore()
  const qc = useQueryClient()
  const [showSimLiveConfirm, setShowSimLiveConfirm] = useState(false)
  const [pendingSimMode, setPendingSimMode] = useState<string | null>(null)
  const [apiKeys, setApiKeys] = useState(DEFAULT_API_KEYS)
  const [riskLocal, setRiskLocal] = useState<any>(null)

  const { data: riskSettings, isLoading } = useQuery({
    queryKey: ['riskSettings'],
    queryFn: getRiskSettings,
  })
  const { data: account, isLoading: accountLoading } = useQuery({
    queryKey: ['account'],
    queryFn: getBridgeAccount,
    retry: 1,
  })

  useEffect(() => {
    if (riskSettings && !riskLocal) {
      setRiskLocal(riskSettings)
      // Extract API keys if stored in settings
      const keys = { ...DEFAULT_API_KEYS }
      for (const k of Object.keys(DEFAULT_API_KEYS) as Array<keyof typeof DEFAULT_API_KEYS>) {
        if (riskSettings[k]) keys[k] = riskSettings[k]
      }
      setApiKeys(keys)
    }
  }, [riskSettings])

  const saveSettingsMut = useMutation({
    mutationFn: () => updateRiskSettings({ ...riskLocal, ...apiKeys }),
    onSuccess: () => { addToast('success', 'Settings saved'); qc.invalidateQueries({ queryKey: ['riskSettings'] }) },
    onError: (e: any) => addToast('error', e.message),
  })

  const handleSimModeChange = (val: string) => {
    if (val === 'live' && riskLocal?.simulation_mode !== 'live') {
      setPendingSimMode(val)
      setShowSimLiveConfirm(true)
    } else {
      setRiskLocal((s: any) => ({ ...s, simulation_mode: val }))
    }
  }

  if (isLoading || !riskLocal) {
    return <div className="flex justify-center items-center h-64"><Spinner size={32} /></div>
  }

  const isSimulation = riskLocal?.simulation_mode !== 'live'

  return (
    <div className="p-6 space-y-6">
      {showSimLiveConfirm && (
        <ConfirmModal
          title="Switch to LIVE Mode"
          message="You are switching from simulation to LIVE trading. Real orders will be placed with real money. Are you absolutely sure?"
          variant="danger"
          onConfirm={() => {
            setRiskLocal((s: any) => ({ ...s, simulation_mode: pendingSimMode }))
            setPendingSimMode(null)
            setShowSimLiveConfirm(false)
          }}
          onCancel={() => {
            setPendingSimMode(null)
            setShowSimLiveConfirm(false)
          }}
        />
      )}

      <SectionHeader title="System Settings" sub="Configure connections, API keys, and trading mode" />

      {/* Simulation mode banner */}
      <div className={`rounded-lg px-4 py-3 flex items-center gap-3 border ${
        isSimulation
          ? 'bg-accent/10 border-accent/30 text-accent'
          : 'bg-danger/10 border-danger/30 text-danger'
      }`}>
        <AlertTriangle size={16} />
        <div>
          <p className="text-sm font-semibold">{isSimulation ? 'Simulation Mode Active' : '⚠ LIVE TRADING MODE ACTIVE'}</p>
          <p className="text-xs opacity-80">
            {isSimulation
              ? 'All orders are simulated. No real money is at risk.'
              : 'Real orders are being placed. Monitor risk carefully.'}
          </p>
        </div>
        <div className="ml-auto">
          <Select
            label=""
            value={riskLocal.simulation_mode ?? 'simulation'}
            onChange={handleSimModeChange}
            options={[
              { value: 'simulation', label: 'Simulation' },
              { value: 'live', label: 'LIVE' },
            ]}
          />
        </div>
      </div>

      {/* Bridge Connection */}
      <Card>
        <div className="flex items-center justify-between mb-4">
          <SectionHeader title="MT5 Bridge Connection" />
          <div className="flex items-center gap-2">
            {wsConnected ? <Wifi size={14} className="text-success" /> : <WifiOff size={14} className="text-muted" />}
            <span className="text-xs text-muted">{wsConnected ? 'Connected' : 'Disconnected'}</span>
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
          {accountLoading ? (
            <div className="col-span-2 flex items-center gap-2 text-sm text-muted"><Spinner size={14} /> Testing connection…</div>
          ) : account ? (
            <>
              <div>
                <p className="text-xs text-muted">Account</p>
                <p className="text-sm mono mt-1">{account.login ?? '—'} · {account.name ?? '—'}</p>
              </div>
              <div>
                <p className="text-xs text-muted">Balance / Equity</p>
                <p className="text-sm mono mt-1">
                  {account.balance != null ? `$${Number(account.balance).toLocaleString(undefined, { minimumFractionDigits: 2 })}` : '—'}
                  {' / '}
                  {account.equity != null ? `$${Number(account.equity).toLocaleString(undefined, { minimumFractionDigits: 2 })}` : '—'}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted">Server</p>
                <p className="text-sm mono mt-1">{account.server ?? '—'}</p>
              </div>
              <div>
                <p className="text-xs text-muted">Leverage</p>
                <p className="text-sm mono mt-1">1:{account.leverage ?? '—'}</p>
              </div>
            </>
          ) : (
            <p className="text-sm text-danger col-span-2">Bridge connection failed. Check that the MT5 bridge service is running.</p>
          )}
        </div>
        <Btn
          variant="outline"
          size="sm"
          onClick={() => { qc.invalidateQueries({ queryKey: ['account'] }); addToast('info', 'Testing bridge connection…') }}
        >
          <RefreshCw size={12} />
          Test Connection
        </Btn>
      </Card>

      {/* API Keys */}
      <Card>
        <SectionHeader title="API Keys" sub="Stored securely as AppSetting records in the database, not in .env" />
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
          <SecretInput
            label="NewsAPI Key (newsapi.org)"
            value={apiKeys.newsapi_key}
            onChange={(v) => setApiKeys((k) => ({ ...k, newsapi_key: v }))}
            placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
          />
          <SecretInput
            label="Alpha Vantage Key"
            value={apiKeys.alphavantage_key}
            onChange={(v) => setApiKeys((k) => ({ ...k, alphavantage_key: v }))}
            placeholder="XXXXXXXXXXXXXXXX"
          />
          <SecretInput
            label="Finnhub Key"
            value={apiKeys.finnhub_key}
            onChange={(v) => setApiKeys((k) => ({ ...k, finnhub_key: v }))}
            placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
          />
          <SecretInput
            label="Anthropic API Key"
            value={apiKeys.anthropic_api_key}
            onChange={(v) => setApiKeys((k) => ({ ...k, anthropic_api_key: v }))}
            placeholder="sk-ant-xxxxxxxxxxxxxxxxxxxxxxxx"
          />
        </div>
      </Card>

      {/* General Settings */}
      <Card>
        <SectionHeader title="General Configuration" />
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-4">
          <Input
            label="Account Balance ($)"
            type="number"
            value={riskLocal.account_balance ?? 10000}
            onChange={(v) => setRiskLocal((s: any) => ({ ...s, account_balance: v }))}
          />
          <Input
            label="Leverage"
            type="number"
            value={riskLocal.leverage ?? 100}
            onChange={(v) => setRiskLocal((s: any) => ({ ...s, leverage: v }))}
            min={1}
            max={3000}
          />
          <Input
            label="News Fetch Interval (hours)"
            type="number"
            value={riskLocal.news_fetch_interval_hours ?? 1}
            onChange={(v) => setRiskLocal((s: any) => ({ ...s, news_fetch_interval_hours: v }))}
            min={0.25}
            step={0.25}
          />
          <Input
            label="News Block Threshold"
            type="number"
            value={riskLocal.news_block_threshold ?? 0.7}
            onChange={(v) => setRiskLocal((s: any) => ({ ...s, news_block_threshold: v }))}
            min={0}
            max={1}
            step={0.05}
          />
          <Input
            label="News Caution Factor"
            type="number"
            value={riskLocal.news_caution_factor ?? 0.5}
            onChange={(v) => setRiskLocal((s: any) => ({ ...s, news_caution_factor: v }))}
            min={0}
            max={1}
            step={0.05}
          />
          <Input
            label="Peer Keepalive URL"
            value={riskLocal.peer_keepalive_url ?? ''}
            onChange={(v) => setRiskLocal((s: any) => ({ ...s, peer_keepalive_url: v }))}
          />
        </div>
        <Btn onClick={() => saveSettingsMut.mutate()} disabled={saveSettingsMut.isPending}>
          {saveSettingsMut.isPending ? <Spinner size={14} /> : null}
          Save All Settings
        </Btn>
      </Card>
    </div>
  )
}
