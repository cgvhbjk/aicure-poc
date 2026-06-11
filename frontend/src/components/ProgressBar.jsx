import React, { useEffect, useState } from 'react'
import { onRequestProgress } from '../api'

// Thin top-of-viewport loading bar for all API traffic, subscribed to the
// axios interceptors in api.js. Determinate (with a % label) when the download
// reports a total; indeterminate sweep otherwise.
export default function ProgressBar() {
  const [state, setState] = useState({ active: false, fraction: null })
  useEffect(() => onRequestProgress(setState), [])

  if (!state.active) return null
  const pct = state.fraction != null ? Math.min(100, Math.round(state.fraction * 100)) : null
  return (
    <div className="progress-track" role="progressbar"
      aria-valuenow={pct ?? undefined} aria-valuemin={0} aria-valuemax={100}>
      <div
        className={pct != null ? 'progress-fill' : 'progress-fill progress-indeterminate'}
        style={pct != null ? { width: `${pct}%` } : undefined}
      />
      {pct != null && <span className="progress-pct">{pct}%</span>}
    </div>
  )
}
