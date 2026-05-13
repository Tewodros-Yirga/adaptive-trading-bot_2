const BASE = '/api'

async function req<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || res.statusText)
  }
  return res.json()
}

const get = <T>(path: string) => req<T>(path)
const post = <T>(path: string, body?: unknown) =>
  req<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined })

// ── Trades ─────────────────────────────────────────────────────────────────
export const getTrades = (limit = 50) => get<any[]>(`/trades?limit=${limit}`)
export const getStats = () => get<any>('/trades/stats')
export const getClosedTrades = (limit = 100) => get<any[]>(`/trades/closed?limit=${limit}`)

// ── Strategies ─────────────────────────────────────────────────────────────
export const getStrategies = () => get<any[]>('/strategies')
export const getStrategy = (name: string) => get<any>(`/strategies/${name}`)
export const activateStrategy = (name: string) => post(`/strategies/${name}/activate`)
export const deactivateStrategy = (name: string) => post(`/strategies/${name}/deactivate`)
export const setStrategyLive = (name: string) => post(`/strategies/${name}/set-live`)
export const updateStrategyParams = (name: string, params: any) => post(`/strategies/${name}/params`, params)
export const getStrategyParamsHistory = (name: string) => get<any[]>(`/strategies/${name}/params/history`)
export const getEnsembleConfig = () => get<any>('/strategies/ensemble/config')
export const updateEnsembleConfig = (cfg: any) => post('/strategies/ensemble/config', cfg)

// ── Risk ──────────────────────────────────────────────────────────────────
export const getRiskSettings = () => get<any>('/risk/settings')
export const updateRiskSettings = (s: any) => post('/risk/settings', s)
export const getRiskStatus = () => get<any>('/risk/status')
export const haltTrading = () => post('/risk/halt')
export const resumeTrading = () => post('/risk/resume')

// ── News ──────────────────────────────────────────────────────────────────
export const getNewsItems = (symbol?: string, limit = 50, hours = 24) =>
  get<any[]>(`/news/items?limit=${limit}&hours=${hours}${symbol ? `&symbol=${symbol}` : ''}`)
export const getNewsBias = (symbol: string, hours = 12) => get<any>(`/news/bias/${symbol}?hours=${hours}`)
export const getNewsContext = () => get<any>('/news/context')
export const fetchNews = (symbol?: string) => post('/news/fetch', symbol ? { symbol } : undefined)
export const refreshContext = () => post('/news/context/refresh')
export const getLearningStats = () => get<any[]>('/news/learning-stats')

// ── Backtest ──────────────────────────────────────────────────────────────
export const runBacktest = (body: any) => post<any>('/backtest/run', body)
export const getBacktestResults = (limit = 20) => get<any[]>(`/backtest/results?limit=${limit}`)
export const getBacktestResult = (id: number) => get<any>(`/backtest/results/${id}`)
export const compareBacktests = (ids: number[]) => post<any>('/backtest/compare', { ids })

// ── Adaptation ────────────────────────────────────────────────────────────
export const triggerAdaptation = () => post('/adapt')
export const getAdaptationLog = (limit = 30) => get<any[]>(`/adapt/log?limit=${limit}`)
export const getLearningSettings = () => get<any>('/params/learning')
export const updateLearningSettings = (s: any) => post('/params/learning', s)
export const getParamsHistory = (limit = 30) => get<any[]>(`/params/history?limit=${limit}`)

// ── Bridge ────────────────────────────────────────────────────────────────
export const getBridgeAccount = () => get<any>('/bridge/account')
export const getBridgePositions = () => get<any[]>('/bridge/positions')

// ── Shadow signals ────────────────────────────────────────────────────────
export const getShadowSignals = (limit = 50) => get<any[]>(`/shadow-signals?limit=${limit}`)
