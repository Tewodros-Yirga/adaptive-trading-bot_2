import React, { useEffect, useState } from 'react'
import { BrowserRouter, NavLink, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import {
  Activity, BarChart2, BookOpen, BrainCircuit, ChevronLeft, ChevronRight,
  Cpu, Home, LogOut, Radio, Settings, ShieldAlert, Target, Users, Wifi, WifiOff,
} from 'lucide-react'
import clsx from 'clsx'
import { ToastContainer } from './components'
import { useAppStore } from './store'
import { useWebSocket } from './hooks/useWebSocket'
import { ErrorBoundary } from './components/ErrorBoundary'
import { useQueryClient } from '@tanstack/react-query'
import Dashboard from './pages/Dashboard'
import StrategyManager from './pages/StrategyManager'
import Backtesting from './pages/Backtesting'
import LiveTrades from './pages/LiveTrades'
import RiskControl from './pages/RiskControl'
import NewsCenter from './pages/NewsCenter'
import Adaptation from './pages/Adaptation'
import SystemSettings from './pages/SystemSettings'
import LoginPage from './pages/LoginPage'
import UsersPage from './pages/UsersPage'
import NewsVeto from './pages/NewsVeto'
import EnsembleDashboard from './pages/EnsembleDashboard'

// ── Sidebar ───────────────────────────────────────────────────────────────
function Sidebar() {
  const {
    sidebarCollapsed, setSidebarCollapsed, wsConnected,
    user, logout, isAdmin, canWrite,
    haltActive, healthWarn, openTradesCount,
  } = useAppStore()
  const navigate = useNavigate()

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  const NAV = [
    { to: '/', icon: <Home size={18} />, label: 'Dashboard', show: true },
    { to: '/strategies', icon: <Cpu size={18} />, label: 'Strategies', show: true },
    { to: '/news-veto', icon: <Target size={18} />, label: 'News Veto', show: true },
    { to: '/ensemble', icon: <Activity size={18} />, label: 'Ensemble', show: true },
    { to: '/backtest', icon: <BarChart2 size={18} />, label: 'Backtesting', show: true },
    {
      to: '/trades',
      icon: <Activity size={18} />,
      label: (
        <span className="flex items-center gap-1.5">
          Live Trades
          {openTradesCount > 0 && (
            <span className="inline-flex items-center justify-center min-w-[1rem] h-4 px-1 text-[10px] font-bold rounded-full bg-accent text-white leading-none">
              {openTradesCount}
            </span>
          )}
        </span>
      ),
      show: true,
    },
    {
      to: '/risk',
      icon: (
        <span className="relative">
          <ShieldAlert size={18} />
          {haltActive && (
            <span className="absolute -top-1 -right-1 w-2 h-2 rounded-full bg-danger animate-pulse" />
          )}
        </span>
      ),
      label: 'Risk Control',
      show: true,
    },
    { to: '/news', icon: <Radio size={18} />, label: 'News Intel', show: true },
    { to: '/adaptation', icon: <BrainCircuit size={18} />, label: 'Adaptation', show: true },
    {
      to: '/settings',
      icon: (
        <span className="relative">
          <Settings size={18} />
          {healthWarn && (
            <span className="absolute -top-1 -right-1 w-2 h-2 rounded-full bg-warn" />
          )}
        </span>
      ),
      label: 'Settings',
      show: isAdmin(),
    },
    { to: '/users', icon: <Users size={18} />, label: 'Users', show: isAdmin() },
  ]

  return (
    <aside className={clsx(
      'h-screen flex flex-col bg-panel border-r border-border transition-all duration-200 flex-shrink-0',
      sidebarCollapsed ? 'w-16' : 'w-56',
    )}>
      {/* Logo */}
      <div className="h-14 flex items-center px-4 border-b border-border gap-3 overflow-hidden">
        <div className="w-7 h-7 rounded bg-accent flex items-center justify-center flex-shrink-0">
          <BookOpen size={14} className="text-white" />
        </div>
        {!sidebarCollapsed && <span className="font-semibold text-sm tracking-tight whitespace-nowrap">AlgoTrade Pro</span>}
      </div>

      {/* Nav */}
      <nav className="flex-1 py-3 overflow-y-auto">
        {NAV.filter(n => n.show).map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            end={n.to === '/'}
            className={({ isActive }) => clsx(
              'flex items-center gap-3 px-4 py-2.5 text-sm transition-colors mx-2 rounded-lg mb-0.5',
              isActive ? 'bg-accent/20 text-accent' : 'text-muted hover:text-white hover:bg-white/5',
            )}
          >
            <span className="flex-shrink-0">{n.icon}</span>
            {!sidebarCollapsed && <span className="whitespace-nowrap">{n.label}</span>}
          </NavLink>
        ))}
      </nav>

      {/* User info + WS status + collapse */}
      <div className="border-t border-border p-3 space-y-2">
        {user && !sidebarCollapsed && (
          <div className="flex items-center gap-2 px-1">
            <div className="w-6 h-6 rounded-full bg-accent/20 flex items-center justify-center flex-shrink-0">
              <span className="text-xs font-medium text-accent">{user.username[0].toUpperCase()}</span>
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-xs font-medium text-white truncate">{user.username}</p>
              <p className="text-xs text-muted">{user.role}</p>
            </div>
            <button onClick={handleLogout} className="text-muted hover:text-danger transition-colors" title="Logout">
              <LogOut size={14} />
            </button>
          </div>
        )}
        {user && sidebarCollapsed && (
          <button onClick={handleLogout} className="text-muted hover:text-danger transition-colors w-full flex justify-center" title="Logout">
            <LogOut size={14} />
          </button>
        )}

        <div className="flex items-center gap-2">
          {wsConnected
            ? <Wifi size={14} className="text-success flex-shrink-0" />
            : <WifiOff size={14} className="text-muted flex-shrink-0" />}
          {!sidebarCollapsed && (
            <span className="text-xs text-muted flex-1">{wsConnected ? 'Live' : 'Offline'}</span>
          )}
          <button
            onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
            className="text-muted hover:text-white ml-auto"
          >
            {sidebarCollapsed ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
          </button>
        </div>
      </div>
    </aside>
  )
}

// ── WebSocket provider ────────────────────────────────────────────────────
function WsProvider() {
  const { addToast, setHaltActive } = useAppStore()
  const qc = useQueryClient()

  useWebSocket({
    trade_opened: (data) => {
      addToast('info', `Trade opened: ${data.symbol ?? (data.data as any)?.symbol}`)
      qc.invalidateQueries({ queryKey: ['trades'] })
      qc.invalidateQueries({ queryKey: ['openTrades'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
    },
    trade_closed: (data) => {
      const pnl = data.pnl ?? (data.data as any)?.pnl
      addToast(pnl >= 0 ? 'success' : 'warning', `Trade closed: ${data.symbol ?? (data.data as any)?.symbol} ${pnl != null ? `($${Number(pnl).toFixed(2)})` : ''}`)
      qc.invalidateQueries({ queryKey: ['trades'] })
      qc.invalidateQueries({ queryKey: ['openTrades'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
    },
    adaptation_triggered: (data) => {
      addToast('info', `Adaptation triggered: ${data.strategy_name} → v${data.new_version}`)
      qc.invalidateQueries({ queryKey: ['adaptLog'] })
    },
    params_promoted: (data) => {
      addToast('success', `Params promoted: ${data.strategy_name} v${data.new_version}`)
      qc.invalidateQueries({ queryKey: ['strategies'] })
    },
    news_fetched: (data) => {
      addToast('info', `News fetched: ${data.count} items from ${data.source}`)
      qc.invalidateQueries({ queryKey: ['news'] })
    },
    halt_toggled: (data) => {
      const halted = data.halt_active ?? (data.data as any)?.halted
      setHaltActive(!!halted)
      addToast(halted ? 'warning' : 'success', `Trading ${halted ? 'HALTED' : 'resumed'}`)
      qc.invalidateQueries({ queryKey: ['riskStatus'] })
    },
    ensemble_vote: () => {
      qc.invalidateQueries({ queryKey: ['ensembleDecisions'] })
      qc.invalidateQueries({ queryKey: ['voterSnapshot'] })
    },
    ensemble_weights_updated: () => {
      qc.invalidateQueries({ queryKey: ['voterSnapshot'] })
      addToast('info', 'Ensemble weights updated')
    },
    '*': () => {},
  })

  return null
}

// ── Auth guard ────────────────────────────────────────────────────────────
function AuthGuard({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useAppStore()
  const location = useLocation()
  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />
  }
  return <>{children}</>
}

function AdminGuard({ children }: { children: React.ReactNode }) {
  const { isAdmin } = useAppStore()
  if (!isAdmin()) return <Navigate to="/" replace />
  return <>{children}</>
}

// ── Main app ──────────────────────────────────────────────────────────────
export default function App() {
  const { isAuthenticated } = useAppStore()

  return (
    <BrowserRouter>
      <ToastContainer />
      <Routes>
        <Route path="/login" element={
          isAuthenticated ? <Navigate to="/" replace /> : <LoginPage />
        } />

        <Route path="/*" element={
          <AuthGuard>
            <WsProvider />
            <div className="flex h-screen overflow-hidden">
              <Sidebar />
              <main className="flex-1 overflow-y-auto bg-bg">
                <Routes>
                  <Route path="/" element={<ErrorBoundary><Dashboard /></ErrorBoundary>} />
                  <Route path="/strategies" element={<ErrorBoundary><StrategyManager /></ErrorBoundary>} />
                  <Route path="/news-veto" element={<ErrorBoundary><NewsVeto /></ErrorBoundary>} />
                  <Route path="/ensemble" element={<ErrorBoundary><EnsembleDashboard /></ErrorBoundary>} />
                  <Route path="/backtest" element={<ErrorBoundary><Backtesting /></ErrorBoundary>} />
                  <Route path="/trades" element={<ErrorBoundary><LiveTrades /></ErrorBoundary>} />
                  <Route path="/risk" element={<ErrorBoundary><RiskControl /></ErrorBoundary>} />
                  <Route path="/news" element={<ErrorBoundary><NewsCenter /></ErrorBoundary>} />
                  <Route path="/adaptation" element={<ErrorBoundary><Adaptation /></ErrorBoundary>} />
                  <Route path="/settings" element={<AdminGuard><ErrorBoundary><SystemSettings /></ErrorBoundary></AdminGuard>} />
                  <Route path="/users" element={<AdminGuard><ErrorBoundary><UsersPage /></ErrorBoundary></AdminGuard>} />
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
              </main>
            </div>
          </AuthGuard>
        } />
      </Routes>
    </BrowserRouter>
  )
}