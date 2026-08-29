import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { LogIn, AlertCircle } from 'lucide-react'
import { login as apiLogin, getMe } from '../api'
import { useAppStore } from '../store'

export default function LoginPage() {
  const navigate = useNavigate()
  const { login } = useAppStore()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      // POST /auth/login with JSON — returns { access_token, token_type }
      const res = await apiLogin(username, password)

      // Store the token first so getMe() can attach the Authorization header
      localStorage.setItem('auth_token', res.access_token)

      // The OAuth2 token endpoint only returns access_token + token_type.
      // Fetch the full user object separately, falling back to a minimal stub
      // if /auth/me fails (e.g. during first-boot before DB is seeded).
      let user = res.user ?? null
      if (!user) {
        try {
          user = await getMe()
        } catch {
          user = { username, role: 'viewer' }
        }
      }

      login(res.access_token, user)
      navigate('/')
    } catch (err: any) {
      localStorage.removeItem('auth_token')
      setError(err.message || 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-bg flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="w-14 h-14 rounded-xl bg-accent/20 flex items-center justify-center mx-auto mb-4">
            <LogIn size={28} className="text-accent" />
          </div>
          <h1 className="text-2xl font-bold text-white">AlgoTrade Pro</h1>
          <p className="text-sm text-muted mt-1">Sign in to continue</p>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="bg-panel border border-border rounded-xl p-6 shadow-2xl">
          {error && (
            <div className="flex items-center gap-2 bg-danger/10 border border-danger/30 rounded-lg px-3 py-2 mb-4">
              <AlertCircle size={14} className="text-danger flex-shrink-0" />
              <span className="text-sm text-danger">{error}</span>
            </div>
          )}

          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-1">
              <label className="text-xs text-muted">Username</label>
              <input
                type="text"
                value={username}
                onChange={e => setUsername(e.target.value)}
                autoFocus
                required
                className="bg-bg border border-border rounded px-3 py-2 text-sm text-white focus:border-accent outline-none transition-colors"
                placeholder="Enter username"
              />
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-xs text-muted">Password</label>
              <input
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                required
                className="bg-bg border border-border rounded px-3 py-2 text-sm text-white focus:border-accent outline-none transition-colors"
                placeholder="Enter password"
              />
            </div>

            <button
              type="submit"
              disabled={loading || !username || !password}
              className="mt-2 bg-accent hover:bg-blue-500 text-white font-medium py-2.5 px-4 rounded-lg transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
            >
              {loading ? (
                <svg className="animate-spin" width={16} height={16} viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.4 0 0 5.4 0 12h4z" />
                </svg>
              ) : (
                <LogIn size={16} />
              )}
              Sign In
            </button>
          </div>
        </form>

        <p className="text-xs text-muted text-center mt-6">
          Contact your administrator for access
        </p>
      </div>
    </div>
  )
}