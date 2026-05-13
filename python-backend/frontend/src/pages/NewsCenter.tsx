import React, { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { RefreshCw, ExternalLink } from 'lucide-react'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import { getNewsItems, getNewsBias, getNewsContext, fetchNews, getLearningStats } from '../api'
import { Card, SectionHeader, Btn, Badge, GaugeBar, Spinner } from '../components'
import { useAppStore } from '../store'
import clsx from 'clsx'

const sentimentColor = (l: string | null) =>
  l === 'BULLISH' ? 'bg-success/20 text-success' : l === 'BEARISH' ? 'bg-danger/20 text-danger' : 'bg-muted/20 text-muted'

export default function NewsCenter() {
  const qc = useQueryClient()
  const { addToast } = useAppStore()
  const [symbol, setSymbol] = useState('XAUUSD')

  const { data: items, isLoading } = useQuery({ queryKey: ['news', symbol], queryFn: () => getNewsItems(symbol, 50, 48), refetchInterval: 120000 })
  const { data: bias } = useQuery({ queryKey: ['bias', symbol], queryFn: () => getNewsBias(symbol), refetchInterval: 60000 })
  const { data: context } = useQuery({ queryKey: ['newsContext'], queryFn: getNewsContext, refetchInterval: 60000 })
  const { data: learningStats } = useQuery({ queryKey: ['learningStats'], queryFn: getLearningStats })

  const fetchMut = useMutation({
    mutationFn: () => fetchNews(symbol),
    onSuccess: (d: any) => { addToast('success', `Stored ${d.stored} news items`); qc.invalidateQueries({ queryKey: ['news'] }) },
  })

  const biasVal = bias?.bias ?? 0
  const biasNorm = (biasVal + 1) / 2  // 0..1

  return (
    <div className="p-6 fade-in">
      <SectionHeader title="News Intelligence Center" sub="AI-analyzed market news and sentiment" />

      <div className="flex gap-3 mb-4 items-end">
        <div>
          <label className="text-xs text-muted block mb-1">Symbol Filter</label>
          <input
            value={symbol}
            onChange={e => setSymbol(e.target.value.toUpperCase())}
            className="bg-bg border border-border rounded px-3 py-1.5 text-sm text-white mono outline-none focus:border-accent w-32"
          />
        </div>
        <Btn size="sm" variant="outline" onClick={() => fetchMut.mutate()} disabled={fetchMut.isPending}>
          <RefreshCw size={12} /> Fetch News
        </Btn>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* News feed */}
        <div className="lg:col-span-2 flex flex-col gap-2 max-h-[70vh] overflow-y-auto pr-1">
          {isLoading ? <div className="flex justify-center py-8"><Spinner /></div> : (items || []).length === 0
            ? <Card><p className="text-muted text-sm text-center py-4">No news. Click "Fetch News" to load.</p></Card>
            : (items || []).map((item: any) => (
              <Card key={item.id} className="p-3">
                <div className="flex items-start justify-between gap-2 mb-1">
                  <a href={item.url} target="_blank" rel="noopener noreferrer" className="text-sm font-medium hover:text-accent flex items-start gap-1 leading-tight">
                    {item.headline}
                    {item.url && <ExternalLink size={10} className="text-muted mt-0.5 flex-shrink-0" />}
                  </a>
                  <Badge label={item.ai_sentiment_label || 'NEUTRAL'} color={sentimentColor(item.ai_sentiment_label)} />
                </div>
                <div className="flex items-center gap-3 text-xs text-muted">
                  <span>{item.source}</span>
                  <span>{item.published_at?.slice(0, 16).replace('T', ' ')}</span>
                  {item.ai_confidence != null && <span>conf: <span className="mono">{(item.ai_confidence * 100).toFixed(0)}%</span></span>}
                  {item.market_impact_predicted != null && (
                    <span className={item.market_impact_predicted > 0 ? 'text-success' : 'text-danger'}>
                      impact: {item.market_impact_predicted > 0 ? '+' : ''}{item.market_impact_predicted.toFixed(2)}
                    </span>
                  )}
                </div>
                {item.market_impact_predicted != null && (
                  <div className="mt-2">
                    <GaugeBar
                      value={Math.abs(item.market_impact_predicted)}
                      max={1}
                      color={item.market_impact_predicted > 0 ? 'bg-success' : 'bg-danger'}
                    />
                  </div>
                )}
              </Card>
          ))}
        </div>

        {/* Right column */}
        <div className="flex flex-col gap-4">
          {/* Bias gauge */}
          <Card>
            <p className="text-xs text-muted mb-2">Sentiment Bias — {symbol}</p>
            <div className="flex items-center gap-2 mb-1">
              <span className="text-xs text-danger">BEAR</span>
              <div className="flex-1 h-3 bg-bg rounded-full relative">
                <div className="absolute inset-0 rounded-full overflow-hidden">
                  <div className="h-full bg-gradient-to-r from-danger via-muted to-success" />
                </div>
                <div
                  className="absolute top-0.5 w-2 h-2 rounded-full bg-white shadow-lg -translate-x-1/2"
                  style={{ left: `${biasNorm * 100}%` }}
                />
              </div>
              <span className="text-xs text-success">BULL</span>
            </div>
            <p className="text-xs text-center mono mt-1">{biasVal.toFixed(3)}</p>
            <p className="text-xs text-muted text-center">conf: {((bias?.confidence ?? 0) * 100).toFixed(0)}% · {bias?.item_count ?? 0} items</p>
          </Card>

          {/* Global context */}
          <Card>
            <p className="text-xs text-muted mb-2">Global Market Context</p>
            {context ? (
              <>
                <div className="flex items-center gap-2 mb-2">
                  <Badge
                    label={context.sentiment || 'NEUTRAL'}
                    color={sentimentColor(context.sentiment)}
                  />
                  <span className="text-xs mono">appetite: {context.risk_appetite?.toFixed(2)}</span>
                </div>
                <p className="text-xs text-muted leading-relaxed mb-2">{context.summary}</p>
                <div className="flex flex-wrap gap-1">
                  {(context.key_themes || []).map((t: string) => (
                    <span key={t} className="px-1.5 py-0.5 bg-border rounded text-xs text-muted">{t}</span>
                  ))}
                </div>
                <p className="text-xs text-muted mt-2">Updated: {context.updated_at?.slice(0, 16).replace('T', ' ') ?? 'never'}</p>
              </>
            ) : <p className="text-muted text-xs">No context yet</p>}
          </Card>

          {/* Learning stats */}
          {learningStats && learningStats.length > 0 && (
            <Card>
              <p className="text-xs text-muted mb-2">Source Accuracy (learning weight)</p>
              <ResponsiveContainer width="100%" height={120}>
                <BarChart data={learningStats.slice(0, 8)} layout="vertical">
                  <XAxis type="number" domain={[0, 1.2]} tick={{ fontSize: 9, fill: '#6b7280' }} />
                  <YAxis type="category" dataKey="source" tick={{ fontSize: 9, fill: '#6b7280' }} width={70} />
                  <Tooltip contentStyle={{ background: '#111827', border: '1px solid #1e2d3d', fontSize: 11 }} />
                  <Bar dataKey="avg_learning_weight" fill="#3b82f6" radius={[0, 3, 3, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}
