import React, { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Save, Wifi } from 'lucide-react'
import { Card, SectionHeader, Input, Btn, Badge, Spinner } from '../components'
import { useAppStore } from '../store'

async function getSystemSettings() {
  const keys = ['newsapi_key', 'alphavantage_key', 'finnhub_key', 'groq_api_key', 'news_block_threshold', 'news_caution_factor']
  const res = await fetch('/api/settings/bulk?' + keys.map(k => `keys=${k}`).join('&'))
  if (!res.ok) return {}
  return res.json()
}

async function saveSystemSettings(data: any) {
  const res = await fetch('/api/settings/bulk', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) })
  if (!res.ok) throw new Error('Failed to save')
  return res.json()
}

async function getBridgeStatus() {
  const res = await fetch('/api/bridge/account')
  if (!res.ok) throw new Error('Bridge unreachable')
  return res.json()
}

export default function SystemSettings() {
  const { addToast } = useAppStore()
  const [apiKeys, setApiKeys] = useState<any>({
    newsapi_key: '',
    alphavantage_key: '',
    finnhub_key: '',
    groq_api_key: '',
    news_block_threshold: '0.7',
    news_caution_factor: '0.5',
  })

  const { data: bridge, refetch: refetchBridge, isFetching: bridgeFetching } = useQuery({
    queryKey: ['bridgeStatus'],
    queryFn: getBridgeStatus,
    retry: 0,
  })

  const saveMut = useMutation({
    mutationFn: saveSystemSettings,
    onSuccess: () => addToast('success', 'Settings saved'),
    onError: () => {
      // Fallback: save each key individually
      Object.entries(apiKeys).forEach(([k, v]) => {
        if (v) fetch(`/api/settings/${k}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value: String(v) }) })
      })
      addToast('success', 'Settings saved (individual)')
    },
  })

  const set = (k: string, v: string) => setApiKeys((p: any) => ({ ...p, [k]: v }))

  return (
    <div className="p-6 fade-in">
      <SectionHeader title="System Settings" sub="API keys, bridge connection, and system configuration" />

      {/* Bridge status */}
      <Card className="mb-4">
        <div className="flex items-center justify-between mb-3">
          <p className="font-medium text-sm">MT5 Bridge Connection</p>
          <Btn size="sm" variant="outline" onClick={() => refetchBridge()} disabled={bridgeFetching}>
            <Wifi size={12} /> Test Connection
          </Btn>
        </div>
        {bridge ? (
          <div className="flex flex-wrap gap-4 text-xs">
            <span className="text-success flex items-center gap-1">● Connected</span>
            {bridge.mode === 'SIMULATION' && <Badge label="SIMULATION MODE" color="bg-warn/20 text-warn" />}
            <span className="text-muted">Balance: <span className="mono text-white">${bridge.balance?.toLocaleString()}</span></span>
            <span className="text-muted">Equity: <span className="mono text-white">${bridge.equity?.toLocaleString()}</span></span>
            <span className="text-muted">Free Margin: <span className="mono text-white">${bridge.freeMargin?.toLocaleString()}</span></span>
          </div>
        ) : (
          <span className="text-muted text-sm flex items-center gap-1">⚪ Not connected or simulation mode</span>
        )}
      </Card>

      {/* API keys */}
      <Card className="mb-4">
        <p className="font-medium text-sm mb-4">News & AI API Keys</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <Input
              label="NewsAPI Key (newsapi.org) — 100 req/day free"
              value={apiKeys.newsapi_key}
              onChange={v => set('newsapi_key', v)}
            />
          </div>
          <div>
            <Input
              label="Alpha Vantage Key — 25 req/day free"
              value={apiKeys.alphavantage_key}
              onChange={v => set('alphavantage_key', v)}
            />
          </div>
          <div>
            <Input
              label="Finnhub Key (finnhub.io) — 60 req/min free"
              value={apiKeys.finnhub_key}
              onChange={v => set('finnhub_key', v)}
            />
          </div>
          <div>
            <Input
              label="Groq API Key (groq.com) — Free LLM for sentiment"
              value={apiKeys.groq_api_key}
              onChange={v => set('groq_api_key', v)}
            />
            <p className="text-xs text-muted mt-1">Groq provides free access to Llama 3 for AI sentiment analysis. Get a key at console.groq.com</p>
          </div>
        </div>

        <div className="border-t border-border mt-4 pt-4 grid grid-cols-2 gap-4">
          <Input
            label="News Block Threshold (0–1, higher = stricter)"
            value={apiKeys.news_block_threshold}
            type="number"
            step={0.05}
            min={0}
            max={1}
            onChange={v => set('news_block_threshold', String(v))}
          />
          <Input
            label="News Caution Lot Factor (e.g. 0.5 = half lots)"
            value={apiKeys.news_caution_factor}
            type="number"
            step={0.1}
            min={0.1}
            max={1}
            onChange={v => set('news_caution_factor', String(v))}
          />
        </div>

        <div className="mt-4">
          <Btn onClick={() => saveMut.mutate(apiKeys)} disabled={saveMut.isPending}>
            <Save size={14} /> Save API Keys
          </Btn>
        </div>
      </Card>

      {/* Info */}
      <Card>
        <p className="font-medium text-sm mb-3">Free API Summary</p>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted border-b border-border">
                <th className="text-left py-1 pr-6">Service</th>
                <th className="text-left py-1 pr-6">Free Tier</th>
                <th className="text-left py-1 pr-6">Best For</th>
                <th className="text-left py-1">URL</th>
              </tr>
            </thead>
            <tbody>
              {[
                ['Groq (Llama 3)', 'Generous free tokens/day', 'AI sentiment analysis (replaces Claude)', 'console.groq.com'],
                ['NewsAPI', '100 req/day', 'General financial news', 'newsapi.org'],
                ['Alpha Vantage', '25 req/day', 'Pre-scored sentiment + forex', 'alphavantage.co'],
                ['Finnhub', '60 req/min', 'Forex-specific real-time', 'finnhub.io'],
                ['Reuters RSS', 'Unlimited', 'Global news, no key needed', 'Built-in'],
                ['ForexLive RSS', 'Unlimited', 'Forex breaking news', 'Built-in'],
              ].map(([svc, tier, best, url]) => (
                <tr key={svc} className="border-b border-border/50">
                  <td className="py-2 pr-6 font-medium">{svc}</td>
                  <td className="py-2 pr-6 text-success">{tier}</td>
                  <td className="py-2 pr-6 text-muted">{best}</td>
                  <td className="py-2 text-accent">{url}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}
