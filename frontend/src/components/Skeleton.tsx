import React from 'react'
import clsx from 'clsx'

const shimmer = 'animate-pulse bg-white/5 rounded'

export const SkeletonCard = ({ className = '' }: { className?: string }) => (
  <div className={clsx('bg-panel border border-border rounded-lg p-4 flex flex-col gap-2', className)}>
    <div className={clsx(shimmer, 'h-3 w-20')} />
    <div className={clsx(shimmer, 'h-8 w-28')} />
    <div className={clsx(shimmer, 'h-2 w-16')} />
  </div>
)

export const SkeletonTable = ({ rows = 5, cols = 5 }: { rows?: number; cols?: number }) => (
  <div className="overflow-x-auto">
    <table className="w-full">
      <thead>
        <tr className="border-b border-border">
          {Array.from({ length: cols }).map((_, i) => (
            <th key={i} className="py-2 pr-4">
              <div className={clsx(shimmer, 'h-3', i === 0 ? 'w-24' : 'w-16')} />
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {Array.from({ length: rows }).map((_, row) => (
          <tr key={row} className="border-b border-border/40">
            {Array.from({ length: cols }).map((_, col) => (
              <td key={col} className="py-2.5 pr-4">
                <div className={clsx(shimmer, 'h-3', col === 0 ? 'w-20' : 'w-12')} />
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  </div>
)

export const SkeletonChart = ({ height = 180 }: { height?: number }) => (
  <div
    className={clsx(shimmer, 'w-full rounded-lg')}
    style={{ height }}
  />
)

export const SkeletonText = ({ lines = 3, className = '' }: { lines?: number; className?: string }) => (
  <div className={clsx('flex flex-col gap-2', className)}>
    {Array.from({ length: lines }).map((_, i) => (
      <div
        key={i}
        className={clsx(shimmer, 'h-3')}
        style={{ width: i === lines - 1 ? '60%' : '100%' }}
      />
    ))}
  </div>
)