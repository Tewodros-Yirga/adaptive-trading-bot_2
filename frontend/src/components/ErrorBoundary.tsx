import React, { Component, ReactNode } from 'react'
import { AlertTriangle } from 'lucide-react'

interface Props {
  children: ReactNode
  fallbackTitle?: string
}

interface State {
  hasError: boolean
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[ErrorBoundary]', error, info)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex items-center justify-center min-h-[40vh] p-6">
          <div className="bg-panel border border-danger/30 rounded-xl p-8 max-w-md w-full text-center">
            <div className="flex justify-center mb-4">
              <AlertTriangle size={40} className="text-danger" />
            </div>
            <h3 className="text-lg font-semibold mb-2 text-white">
              {this.props.fallbackTitle || 'Something went wrong'}
            </h3>
            <p className="text-sm text-muted mb-2">
              This page encountered an error and could not render.
            </p>
            {this.state.error?.message && (
              <p className="text-xs mono text-danger/80 bg-bg rounded p-2 mb-4 text-left overflow-x-auto">
                {this.state.error.message}
              </p>
            )}
            <div className="flex gap-3 justify-center">
              <button
                onClick={() => window.location.reload()}
                className="px-4 py-2 bg-accent/20 border border-accent text-accent text-sm rounded-lg hover:bg-accent/30 transition-colors"
              >
                Reload Page
              </button>
              <a
                href="/"
                className="px-4 py-2 bg-bg border border-border text-white text-sm rounded-lg hover:border-accent/50 transition-colors"
              >
                Go to Dashboard
              </a>
            </div>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}