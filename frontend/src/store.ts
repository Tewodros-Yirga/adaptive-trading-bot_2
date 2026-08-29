import { create } from 'zustand'

interface AuthUser {
  id: string | number  // MongoDB returns ObjectId string; SQL DBs return number
  username: string
  role: 'admin' | 'viewer'
  full_access: boolean
}

interface Toast {
  id: string
  type: 'success' | 'error' | 'warning' | 'info'
  message: string
}

interface AppState {
  // Sidebar
  sidebarCollapsed: boolean
  setSidebarCollapsed: (v: boolean) => void

  // Toasts
  toasts: Toast[]
  addToast: (type: Toast['type'], message: string) => void
  removeToast: (id: string) => void

  // WebSocket
  wsConnected: boolean
  setWsConnected: (v: boolean) => void

  // Live indicators for sidebar
  haltActive: boolean
  setHaltActive: (v: boolean) => void
  healthWarn: boolean
  setHealthWarn: (v: boolean) => void

  // Open trades count — shown as badge in sidebar nav
  openTradesCount: number
  setOpenTradesCount: (n: number) => void

  // Auth
  user: AuthUser | null
  token: string | null
  isAuthenticated: boolean
  login: (token: string, user: AuthUser) => void
  logout: () => void
  setUser: (user: AuthUser) => void
  canWrite: () => boolean
  isAdmin: () => boolean
}

const storedToken = localStorage.getItem('auth_token')
const storedUser = (() => {
  try {
    const raw = localStorage.getItem('auth_user')
    if (!raw) return null
    const parsed = JSON.parse(raw)
    // Validate it has the minimum required fields
    if (!parsed || typeof parsed !== 'object' || !parsed.username) return null
    // Ensure role exists, default to viewer
    if (!parsed.role) parsed.role = 'viewer'
    if (parsed.full_access === undefined) parsed.full_access = false
    return parsed
  } catch {
    return null
  }
})()

export const useAppStore = create<AppState>((set, get) => ({
  sidebarCollapsed: false,
  toasts: [],
  wsConnected: false,
  haltActive: false,
  healthWarn: false,
  openTradesCount: 0,

  user: storedUser,
  token: storedToken,
  isAuthenticated: !!storedToken,

  setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),

  addToast: (type, message) => {
    const id = Math.random().toString(36).slice(2)
    set((s) => ({ toasts: [...s.toasts, { id, type, message }] }))
    setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), 4000)
  },
  removeToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
  setWsConnected: (v) => set({ wsConnected: v }),
  setHaltActive: (v) => set({ haltActive: v }),
  setHealthWarn: (v) => set({ healthWarn: v }),
  setOpenTradesCount: (n) => set({ openTradesCount: n }),

  login: (token, user) => {
    localStorage.setItem('auth_token', token)
    localStorage.setItem('auth_user', JSON.stringify(user))
    set({ token, user, isAuthenticated: true })
  },

  logout: () => {
    localStorage.removeItem('auth_token')
    localStorage.removeItem('auth_user')
    set({ token: null, user: null, isAuthenticated: false, haltActive: false, openTradesCount: 0 })
  },

  setUser: (user) => {
    localStorage.setItem('auth_user', JSON.stringify(user))
    set({ user })
  },

  canWrite: () => {
    const { user } = get()
    if (!user) return false
    return user.role === 'admin' || user.full_access
  },

  isAdmin: () => {
    const { user } = get()
    return user?.role === 'admin' || false
  },
}))