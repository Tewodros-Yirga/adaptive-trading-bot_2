import { useEffect, useRef } from 'react'
import { useAppStore } from '../store'

type WSEventHandler = (event: Record<string, unknown>) => void

export function useWebSocket(handlers: Record<string, WSEventHandler>) {
  const wsRef = useRef<WebSocket | null>(null)
  const handlersRef = useRef(handlers)
  handlersRef.current = handlers

  useEffect(() => {
    const token = localStorage.getItem('auth_token')
    if (!token) return

    let reconnectTimer: ReturnType<typeof setTimeout>
    let destroyed = false

    const connect = () => {
      if (destroyed) return

      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const base = import.meta.env.VITE_API_URL || `${proto}://${window.location.host}`
      const wsBase = base.replace(/^http/, 'ws')
      const url = `${wsBase}/ws?token=${encodeURIComponent(token)}`

      try {
        const ws = new WebSocket(url)
        wsRef.current = ws

        ws.onopen = () => {
          useAppStore.getState().setWsConnected(true)
        }

        ws.onmessage = (msg) => {
          try {
            const data = JSON.parse(msg.data)
            // Support both `event` and `type` keys (backend may use either)
            const eventKey = data.event ?? data.type
            const handler = handlersRef.current[eventKey]
            if (handler) handler(data)
            // Also call wildcard handler if present
            const wildcard = handlersRef.current['*']
            if (wildcard) wildcard(data)
          } catch {
            // ignore malformed messages
          }
        }

        ws.onclose = (e) => {
          useAppStore.getState().setWsConnected(false)
          if (!destroyed) {
            // Reconnect after 3s on unexpected close
            reconnectTimer = setTimeout(connect, 3000)
          }
        }

        ws.onerror = () => {
          ws.close()
        }
      } catch {
        if (!destroyed) {
          reconnectTimer = setTimeout(connect, 5000)
        }
      }
    }

    connect()

    return () => {
      destroyed = true
      clearTimeout(reconnectTimer)
      wsRef.current?.close()
    }
  }, [])
}