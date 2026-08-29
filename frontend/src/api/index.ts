const BASE = import.meta.env.VITE_API_URL ?? '/api'

async function req<T>(path: string, opts?: RequestInit): Promise<T> {
  const token = localStorage.getItem('auth_token')
  const res = await fetch(`${BASE}${path}`, {
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...opts?.headers,
    },
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
const put = <T>(path: string, body?: unknown) =>
  req<T>(path, { method: 'PUT', body: body ? JSON.stringify(body) : undefined })
const del = <T>(path: string) => req<T>(path, { method: 'DELETE' })

// ── Auth ───────────────────────────────────────────────────────────────────
export const login = (username: string, password: string) =>
  post<{ access_token: string; token_type: string; user?: any }>('/auth/login', { username, password })
export const getMe = () => get<any>('/auth/me')
export const getUsers = () => get<any[]>('/auth/users')
export const createUser = (body: { username: string; password: string; role: string; full_access: boolean }) =>
  post<any>('/auth/users', body)
export const updateUser = (id: number, data: any) => put<any>(`/auth/users/${id}`, data)
export const deleteUser = (id: number) => del<any>(`/auth/users/${id}`)

// ── Trades ─────────────────────────────────────────────────────────────────
export const getTrades = (limit = 50) => get<any[]>(`/trades?limit=${limit}`)
export const getStats = () => get<any>('/trades/stats')
export const getClosedTrades = (limit = 100) => get<any[]>(`/trades/closed?limit=${limit}`)
export const getPendingOrders = () => get<any[]>('/trades/pending')
export const getPendingOrderHistory = (limit = 100) =>
  get<any[]>(`/trades/pending/history?limit=${limit}`)
export const getTradeAnalytics = (days = 30) => get<any>(`/trades/analytics?days=${days}`)
export const clearAccountData = () =>
  post<{ status: string; account: number; trades_deleted: number; orders_deleted: number }>('/trades/clear')

// ── Settings (generic key/value) ────────────────────────────────────────────
export const getSettingsBulk = (keys: string[]) =>
  get<Record<string, string>>(`/settings/bulk?${keys.map((k) => `keys=${encodeURIComponent(k)}`).join('&')}`)
export const setSettingsBulk = (settings: Record<string, string | number | boolean>) =>
  post<{ saved: string[] }>('/settings/bulk', settings)

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
export const getRiskLotPreview = (symbol: string, entry: number, stop_loss: number) =>
  get<any>(`/risk/lot-size-preview?symbol=${encodeURIComponent(symbol)}&entry=${entry}&stop_loss=${stop_loss}`)

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
export const getCacheStatus = () => get<any>('/backtest/cache-status')
export const triggerCachePreload = (symbols: string, timeframes: string, years: number) =>
  post<{ ok: boolean; message: string }>('/backtest/trigger-cache-preload', { symbols, timeframes, years })

// ── System (alerts) ─────────────────────────────────────────────────────────
export const sendTestAlert = () => post<any>('/system/alerts/test')
export const getAlertsDiagnostics = () => get<any>('/system/alerts/diagnostics')

// ── Adaptation ────────────────────────────────────────────────────────────
export const triggerAdaptation = () => post('/adapt')
export const getAdaptationLog = (limit = 30) => get<any[]>(`/adapt/log?limit=${limit}`)
export const getLearningSettings = () => get<any>('/params/learning')
export const updateLearningSettings = (s: any) => post('/params/learning', s)
export const getParamsHistory = (limit = 30) => get<any[]>(`/params/history?limit=${limit}`)

// ── Bridge ────────────────────────────────────────────────────────────────
export const getBridgeAccount = () => get<any>('/bridge/account')
export const getBridgePositions = () => get<any[]>('/bridge/positions')

// ── Ensemble ───────────────────────────────────────────────────────────────
export const getVoterSnapshot = () => get<any>('/ensemble/voter-snapshot')
export const getEnsembleWeights = () => get<any>('/ensemble/weights')
export const resetEnsembleWeights = () => post<any>('/ensemble/weights/reset')
export const setEnsembleSuspended = (suspended: string[]) =>
  post<any>('/ensemble/weights/suspend', { suspended })
export const getEnsembleDecisions = (limit = 50) => get<any[]>(`/ensemble/decisions?limit=${limit}`)

// ── News veto ──────────────────────────────────────────────────────────────
export const getNewsVetoStatus = () => get<any>('/news-veto/status')
export const getNewsVetoDecisions = (opts: { page?: number; limit?: number; symbol?: string } = {}) => {
  const { page = 1, limit = 20, symbol } = opts
  const q = new URLSearchParams({ page: String(page), limit: String(limit) })
  if (symbol) q.set('symbol', symbol)
  return get<any>(`/news-veto/decisions?${q.toString()}`)
}

// ── Shadow signals ────────────────────────────────────────────────────────
export const getShadowSignals = (limit = 50) => get<any[]>(`/shadow-signals?limit=${limit}`)

// ── News context refresh ────────────────────────────────────────────────────
export const getNewsSourceCredibility = () => get<any>('/news/credibility')