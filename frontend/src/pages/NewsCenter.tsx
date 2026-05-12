import React, { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { RefreshCw, ExternalLink, Filter } from 'lucide-react'
import clsx from 'clsx'
import {
  getNewsItems, getNewsBias, getNewsContext, fetchNews, getLearningStats,
} from '../api'
import { Card, SectionHeader, Btn, Spinner, Input, Select, GaugeBar } from '../components'
import { useAppStore } from '../store'

const SENTIMENT_COLOR: Record<string, string> = {
  BULLISH: 'bg-success/20 text-success',
  BEARISH: 'bg-danger/20 text-danger',
  NEUTRAL: 'bg-muted/20 text-muted',
}

function SentimentBadge({ label, confidence }: { label: string; confidence?: number }) {
  return (
    <span className={clsx('inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium', SENTIMENT_COLOR[label] ?? 'bg-muted/20 text-muted')}>
      {label}
      {confidence != null && <span className="opacity-70">{(confidence * 100).toFixed(0)}%</span>}
    </span>
  )
}

function BiasGauge({ symbol }: { symbol: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['newsBias', symbol],
    queryFn: () => getNewsBias(symbol, 12),
    enabled: !!symbol,
    refetchInterval: 60000,
  })

  if (isLoading) return <div className="flex justify-center py-4"><Spinner /></div>
  if (!data) return <p className="text-xs text-muted text-center py-4">No bias data</p>

  const bias: number = data.bias ?? 0
  const conf: number = data.confidence ?? 0
  const color = bias > 0.2 ? 'text-success' : bias < -0.2 ? 'text-danger' : 'text-warn'

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted">Bias Score</span>
        <span className={clsx('text-xl font-semibold mono', color)}>{bias >= 0 ? '+' : ''}{bias.toFixed(3)}</span>
      </div>
      <div className="relative h-3 bg-bg rounded-full overflow-hidden border border-border">
        <div className="absolute left-1/2 top-0 w-px h-full bg-border z-10" />
        <div
          className={`absolute top-0 h-full transition-all ${bias >= 0 ? 'bg-success' : 'bg-danger'}`}
          style={{
            width: `${Math.abs(bias) * 50}%`,
            left: bias >= 0 ? '50%' : `${50 + bias * 50}%`,
          }}
        />
      </div>
      <div className="flex justify-between text-xs text-muted">
        <span>-1 Bearish</span><span>0</span><span>+1 Bullish</span>
      </div>
      <div>
        <span className="text-xs text-muted">Confidence: </span>
        <span className="text-xs mono">{(conf * 100).toFixed(1)}%</span>
      </div>
      {data.contributing_items?.length > 0 && (
        <div className="space-y-1.5 border-t border-border pt-3">
          <p className="text-xs text-muted">Contributing items:</p>
          {data.contributing_items.slice(0, 5).map((item: any, i: number) => (
            <div key={i} className="flex items-center justify-between text-xs">
              <span className="text-muted truncate flex-1 mr-2">{item.headline}</span>
              <span className={clsx('mono flex-shrink-0', item.ai_sentiment_score >= 0 ? 'text-success' : 'text-danger')}>
                {item.ai_sentiment_score >= 0 ? '+' : ''}{item.ai_sentiment_score?.toFixed(2)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function NewsCenter() {
  const { addToast } = useAppStore()
  const qc = useQueryClient()
  const [filterSymbol, setFilterSymbol] = useState('')
  const [filterSentiment, setFilterSentiment] = useState('')
  const [filterSource, setFilterSource] = useState('')
  const [biasSymbol, setBiasSymbol] = useState('XAUUSD')
  const [tab, setTab] = useState<'feed' | 'learning'>('feed')

  const { data: newsItems, isLoading: newsLoading } = useQuery({
    queryKey: ['news', filterSymbol],
    queryFn: () => getNewsItems(filterSymbol || undefined, 50, 48),
    refetchInterval: 120000,
  })
  const { data: newsCtx } = useQuery({
    queryKey: ['newsContext'],
    queryFn: getNewsContext,
    refetchInterval: 120000,
  })
  const { data: learningStats } = useQuery({
    queryKey: ['learningStats'],
    queryFn: getLearningStats,
  })

  const fetchMut = useMutation({
    mutationFn: () => fetchNews(filterSymbol || undefined),
    onSuccess: () => { addToast('info', 'News fetch triggered'); qc.invalidateQueries({ queryKey: ['news'] }) },
    onError: (e: any) => addToast('error', e.message),
  })

  const filteredItems = (newsItems ?? []).filter((item: any) => {
    if (filterSentiment && item.ai_sentiment_label !== filterSentiment) return false
    if (filterSource && item.source !== filterSource) return false
    return true
  })

  const sources = [...new Set((newsItems ?? []).map((i: any) => i.source).filter(Boolean))]

  return (
    <div className="p-6 space-y-6">
      <SectionHeader title="News Intelligence" sub="AI-analyzed market news and real-time sentiment bias" />

      <div className="flex gap-1 border-b border-border pb-0">
        {(['feed', 'learning'] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={clsx(
              'px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px',
              tab === t ? 'border-accent text-accent' : 'border-transparent text-muted hover:text-white',
            )}
          >{t === 'feed' ? 'News Feed' : 'Learning Stats'}</button>
        ))}
      </div>

      {tab === 'feed' && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Left: News feed */}
          <div className="lg:col-span-2 space-y-4">
            {/* Filters */}
            <Card>
              <div className="flex items-center gap-2 mb-3">
                <Filter size={14} className="text-muted" />
                <span className="text-xs text-muted uppercase tracking-wider">Filters</span>
                <div className="ml-auto">
                  <Btn size="sm" variant="outline" onClick={() => fetchMut.mutate()} disabled={fetchMut.isPending}>
                    {fetchMut.isPending ? <Spinner size={12} /> : <RefreshCw size={12} />}
                    Fetch Now
                  </Btn>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-3">
                <Input label="Symbol" value={filterSymbol} onChange={setFilterSymbol} />
                <Select
                  label="Sentiment"
                  value={filterSentiment}
                  onChange={setFilterSentiment}
                  options={[
                    { value: '', label: 'All' },
                    { value: 'BULLISH', label: 'Bullish' },
                    { value: 'BEARISH', label: 'Bearish' },
                    { value: 'NEUTRAL', label: 'Neutral' },
                  ]}
                />
                <Select
                  label="Source"
                  value={filterSource}
                  onChange={setFilterSource}
                  options={[{ value: '', label: 'All' }, ...sources.map((s) => ({ value: s as string, label: s as string }))]}
                />
              </div>
            </Card>

            {/* News list */}
            {newsLoading ? (
              <div className="flex justify-center py-16"><Spinner size={32} /></div>
            ) : !filteredItems.length ? (
              <Card className="text-center py-12 text-muted text-sm">No news items. Try fetching.</Card>
            ) : (
              <div className="space-y-2">
                {filteredItems.map((item: any) => (
                  <div key={item.id} className="bg-panel border border-border rounded-lg p-4 hover:border-accent/40 transition-colors">
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium leading-snug mb-1">{item.headline}</p>
                        {item.summary && (
                          <p className="text-xs text-muted leading-relaxed mb-2 line-clamp-2">{item.summary}</p>
                        )}
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-xs text-muted">{item.source}</span>
                          <span className="text-muted">·</span>
                          <span className="text-xs text-muted">
                            {item.published_at ? new Date(item.published_at).toLocaleString() : '—'}
                          </span>
                          {item.symbols_mentioned?.map((s: string) => (
                            <span key={s} className="px-1.5 py-0.5 bg-accent/10 text-accent text-xs rounded">{s}</span>
                          ))}
                        </div>
                      </div>
                      <div className="flex flex-col items-end gap-2 flex-shrink-0">
                        {item.ai_sentiment_label && (
                          <SentimentBadge label={item.ai_sentiment_label} confidence={item.ai_confidence} />
                        )}
                        {item.market_impact_predicted != null && (
                          <div className="w-16">
                            <div className="text-xs text-muted text-right mb-0.5">Impact</div>
                            <GaugeBar
                              value={Math.abs(item.market_impact_predicted) * 100}
                              max={100}
                              color={item.market_impact_predicted >= 0 ? 'bg-success' : 'bg-danger'}
                            />
                          </div>
                        )}
                        {item.url && (
                          <a href={item.url} target="_blank" rel="noopener noreferrer" className="text-muted hover:text-accent">
                            <ExternalLink size={12} />
                          </a>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Right: bias + context */}
          <div className="space-y-4">
            <Card>
              <div className="flex items-center justify-between mb-3">
                <span className="text-sm font-medium">Symbol Bias</span>
              </div>
              <Input label="Symbol" value={biasSymbol} onChange={setBiasSymbol} />
              <div className="mt-4">
                <BiasGauge symbol={biasSymbol} />
              </div>
            </Card>

            <Card>
              <p className="text-sm font-medium mb-3">Global Context</p>
              {newsCtx ? (
                <div className="space-y-3">
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-muted">Risk Appetite:</span>
                    <span className={clsx('text-sm font-medium', newsCtx.risk_appetite > 0 ? 'text-success' : newsCtx.risk_appetite < 0 ? 'text-danger' : 'text-warn')}>
                      {newsCtx.risk_appetite > 0.3 ? 'Risk-On' : newsCtx.risk_appetite < -0.3 ? 'Risk-Off' : 'Neutral'}
                    </span>
                  </div>
                  {newsCtx.key_themes?.map((t: string, i: number) => (
                    <span key={i} className="inline-block mr-1.5 mb-1.5 px-2 py-0.5 bg-accent/10 text-accent text-xs rounded-full">{t}</span>
                  ))}
                  {newsCtx.summary && (
                    <p className="text-xs text-muted leading-relaxed border-t border-border pt-3">{newsCtx.summary}</p>
                  )}
                  {newsCtx.updated_at && (
                    <p className="text-xs text-muted">Updated: {new Date(newsCtx.updated_at).toLocaleTimeString()}</p>
                  )}
                </div>
              ) : (
                <p className="text-xs text-muted">No context available.</p>
              )}
            </Card>
          </div>
        </div>
      )}

      {tab === 'learning' && (
        <div className="space-y-4">
          <Card>
            <SectionHeader title="Source Accuracy Weights" sub="How much each news source's impact predictions have proven accurate over time" />
            {!learningStats?.length ? (
              <p className="text-muted text-sm text-center py-8">No learning data yet.</p>
            ) : (
              <ResponsiveContainer width="100%" height={280}>
                <BarChart data={learningStats} layout="vertical">
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e2d3d" horizontal={false} />
                  <XAxis type="number" domain={[0, 1]} tick={{ fontSize: 10, fill: '#6b7280' }} />
                  <YAxis dataKey="source" type="category" tick={{ fontSize: 11, fill: '#e2e8f0' }} width={80} />
                  <Tooltip
                    contentStyle={{ background: '#111827', border: '1px solid #1e2d3d', fontSize: 12 }}
                    formatter={(v: any) => [`${(v * 100).toFixed(1)}%`, 'Learning Weight']}
                  />
                  <Bar dataKey="avg_weight" fill="#3b82f6" radius={[0, 3, 3, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </Card>

          <Card>
            <SectionHeader title="Raw Learning Data" />
            {!learningStats?.length ? (
              <p className="text-muted text-sm">No data.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border">
                    <th className="text-left text-xs text-muted py-2 pr-4">Source</th>
                    <th className="text-left text-xs text-muted py-2 pr-4">Avg Weight</th>
                    <th className="text-left text-xs text-muted py-2 pr-4">Items</th>
                    <th className="text-left text-xs text-muted py-2 pr-4">Avg Accuracy</th>
                  </tr>
                </thead>
                <tbody>
                  {learningStats.map((row: any) => (
                    <tr key={row.source} className="border-b border-border/50">
                      <td className="py-2 pr-4 font-medium">{row.source}</td>
                      <td className="py-2 pr-4 mono">{(row.avg_weight * 100).toFixed(1)}%</td>
                      <td className="py-2 pr-4 mono">{row.item_count ?? '—'}</td>
                      <td className="py-2 pr-4 mono">{row.avg_accuracy != null ? `${(row.avg_accuracy * 100).toFixed(1)}%` : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
        </div>
      )}
    </div>
  )
}
