import { create } from 'zustand'

interface Toast {
  id: string
  type: 'success' | 'error' | 'warning' | 'info'
  message: string
}

interface AppState {
  sidebarCollapsed: boolean
  toasts: Toast[]
  wsConnected: boolean
  setSidebarCollapsed: (v: boolean) => void
  addToast: (type: Toast['type'], message: string) => void
  removeToast: (id: string) => void
  setWsConnected: (v: boolean) => void
}

export const useAppStore = create<AppState>((set) => ({
  sidebarCollapsed: false,
  toasts: [],
  wsConnected: false,
  setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),
  addToast: (type, message) => {
    const id = Math.random().toString(36).slice(2)
    set((s) => ({ toasts: [...s.toasts, { id, type, message }] }))
    setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), 4000)
  },
  removeToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
  setWsConnected: (v) => set({ wsConnected: v }),
}))
