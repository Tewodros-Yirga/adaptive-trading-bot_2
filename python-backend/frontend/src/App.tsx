import React, { useEffect, useState } from 'react'
import { BrowserRouter, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import {
  Activity, BarChart2, BookOpen, BrainCircuit, ChevronLeft, ChevronRight,
  Cpu, Home, Radio, Settings, ShieldAlert, Wifi, WifiOff,
} from 'lucide-react'
import clsx from 'clsx'
import { ToastContainer } from './components'
import { useAppStore } from './store'
import Dashboard from './pages/Dashboard'
import StrategyManager from './pages/StrategyManager'
import Backtesting from './pages/Backtesting'
import LiveTrades from './pages/LiveTrades'
import RiskControl from './pages/RiskControl'
import NewsCenter from './pages/NewsCenter'
import Adaptation from './pages/Adaptation'
import SystemSettings from './pages/SystemSettings'

const NAV = [
  { to: '/', icon: <Home size={18} />, label: 'Dashboard' },
  { to: '/strategies', icon: <Cpu size={18} />, label: 'Strategies' },
  { to: '/backtest', icon: <BarChart2 size={18} />, label: 'Backtesting' },
  { to: '/trades', icon: <Activity size={18} />, label: 'Live Trades' },
  { to: '/risk', icon: <ShieldAlert size={18} />, label: 'Risk Control' },
  { to: '/news', icon: <Radio size={18} />, label: 'News Intel' },
  { to: '/adaptation', icon: <BrainCircuit size={18} />, label: 'Adaptation' },
  { to: '/settings', icon: <Settings size={18} />, label: 'Settings' },
]

function Sidebar() {
  const { sidebarCollapsed, setSidebarCollapsed, wsConnected } = useAppStore()
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
        {NAV.map((n) => (
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

      {/* WS status + collapse */}
      <div className="border-t border-border p-3 flex items-center gap-2">
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
    </aside>
  )
}

function WsProvider() {
  const { setWsConnected, addToast } = useAppStore()
  useEffect(() => {
    const connect = () => {
      try {
        const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
        const ws = new WebSocket(`${proto}://${window.location.host}/ws`)
        ws.onopen = () => setWsConnected(true)
        ws.onclose = () => { setWsConnected(false); setTimeout(connect, 5000) }
        ws.onmessage = (e) => {
          try {
            const msg = JSON.parse(e.data)
            if (msg.type === 'trade_opened') addToast('info', `Trade opened: ${msg.data?.symbol}`)
            if (msg.type === 'trade_closed') addToast('success', `Trade closed: ${msg.data?.symbol}`)
            if (msg.type === 'halt_toggled') addToast('warning', `Trading halt: ${msg.data?.halted}`)
          } catch {}
        }
      } catch {}
    }
    connect()
  }, [])
  return null
}

export default function App() {
  return (
    <BrowserRouter>
      <WsProvider />
      <ToastContainer />
      <div className="flex h-screen overflow-hidden">
        <Sidebar />
        <main className="flex-1 overflow-y-auto bg-bg">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/strategies" element={<StrategyManager />} />
            <Route path="/backtest" element={<Backtesting />} />
            <Route path="/trades" element={<LiveTrades />} />
            <Route path="/risk" element={<RiskControl />} />
            <Route path="/news" element={<NewsCenter />} />
            <Route path="/adaptation" element={<Adaptation />} />
            <Route path="/settings" element={<SystemSettings />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
