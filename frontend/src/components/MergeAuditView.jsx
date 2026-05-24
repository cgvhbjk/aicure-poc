import React, { useState, useEffect, useCallback, useRef } from 'react'
import { getMerges, getMergeStats, confirmMerge, rejectMerge, snoozeMerge, undoMerge } from '../api'

const ENTITY_TYPES = ['trials', 'organizations']
const STATUS_OPTIONS = ['PENDING', 'CONFIRMED_MERGE', 'REJECTED', 'SNOOZED']

function ConfidenceBadge({ score }) {
  const pct = Math.round(score * 100)
  const cls = pct >= 85 ? 'conf-high' : pct >= 70 ? 'conf-med' : 'conf-low'
  return <span className={`conf-badge ${cls}`}>{pct}%</span>
}

function FieldRow({ label, valA, valB }) {
  const differ = valA !== valB && valA != null && valB != null
  return (
    <tr className={differ ? 'field-row differ' : 'field-row'}>
      <td className="field-label">{label}</td>
      <td className={differ ? 'field-val differ-a' : 'field-val'}>{valA ?? <span className="field-empty">—</span>}</td>
      <td className={differ ? 'field-val differ-b' : 'field-val'}>{valB ?? <span className="field-empty">—</span>}</td>
    </tr>
  )
}

function MergeCard({ candidate, onAction, focused }) {
  const [loading, setLoading] = useState(null)
  const cardRef = useRef()

  useEffect(() => {
    if (focused && cardRef.current) {
      cardRef.current.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    }
  }, [focused])

  const act = async (fn, actionName) => {
    setLoading(actionName)
    try {
      await fn()
      onAction()
    } finally {
      setLoading(null)
    }
  }

  const a = candidate.record_a || {}
  const b = candidate.record_b || {}
  const matchScores = (() => {
    try { return typeof candidate.match_scores === 'string' ? JSON.parse(candidate.match_scores) : (candidate.match_scores || {}) } catch { return {} }
  })()
  const matchFields = (() => {
    try { return typeof candidate.match_fields === 'string' ? JSON.parse(candidate.match_fields) : (candidate.match_fields || []) } catch { return [] }
  })()

  const isTrials = candidate.entity_type === 'trials'
  const isPending = candidate.status === 'PENDING'
  const canUndo = candidate.status === 'CONFIRMED_MERGE' && !!candidate.loser_snapshot

  const FIELD_LABELS = isTrials
    ? [
        ['title_brief', 'Title'],
        ['sponsor', 'Sponsor'],
        ['start_date', 'Start date'],
        ['phase', 'Phase'],
        ['status', 'Status'],
        ['enrollment', 'Enrollment'],
        ['conditions', 'Conditions'],
        ['therapeutic_area', 'Therapeutic area'],
      ]
    : [
        ['canonical_name', 'Name'],
        ['trial_count', 'Trial count'],
        ['aliases', 'Aliases'],
        ['therapeutic_focus', 'Focus'],
        ['org_type', 'Type'],
        ['website', 'Website'],
      ]

  const JSON_ARRAY_FIELDS = new Set(['conditions', 'therapeutic_focus', 'aliases'])

  const formatVal = (key, val) => {
    if (val == null || val === '') return null
    if (JSON_ARRAY_FIELDS.has(key)) {
      try {
        const arr = JSON.parse(val)
        return Array.isArray(arr) ? (arr.length ? arr.join(', ') : null) : String(val)
      } catch { return String(val) }
    }
    return String(val)
  }

  return (
    <div ref={cardRef} className={`merge-card${focused ? ' merge-card-focused' : ''}`}>
      <div className="merge-card-header">
        <div className="merge-card-ids">
          <span className="merge-id">{candidate.record_a_id}</span>
          <span className="merge-arrow">↔</span>
          <span className="merge-id">{candidate.record_b_id}</span>
        </div>
        <div className="merge-card-meta">
          <ConfidenceBadge score={candidate.confidence} />
          <span className="merge-entity-tag">{candidate.entity_type}</span>
          {candidate.status !== 'PENDING' && (
            <span className={`merge-status-tag ${candidate.status.toLowerCase()}`}>
              {candidate.status.replace('_', ' ')}
            </span>
          )}
        </div>
      </div>

      {matchFields.length > 0 && (
        <div className="merge-match-fields">
          {matchFields.map(f => (
            <span key={f} className="match-field-pill">
              {f.replace(/_/g, ' ')}
              {matchScores[f] != null && ` ${Math.round(matchScores[f] * 100)}%`}
            </span>
          ))}
        </div>
      )}

      <div className="merge-comparison">
        <table className="merge-table">
          <thead>
            <tr>
              <th className="merge-th-label"></th>
              <th className="merge-th-rec">Record A</th>
              <th className="merge-th-rec">Record B</th>
            </tr>
          </thead>
          <tbody>
            {FIELD_LABELS.map(([key, label]) => (
              <FieldRow
                key={key}
                label={label}
                valA={formatVal(key, a[key])}
                valB={formatVal(key, b[key])}
              />
            ))}
          </tbody>
        </table>
      </div>

      {isPending && (
        <div className="merge-actions">
          <button
            className="btn-sm btn-confirm"
            disabled={loading != null}
            onClick={() => act(
              () => confirmMerge(candidate.id, { surviving_id: candidate.record_a_id }),
              'confirm'
            )}
          >
            {loading === 'confirm' ? '…' : '✓ Confirm merge'}
          </button>
          <button
            className="btn-sm btn-reject"
            disabled={loading != null}
            onClick={() => act(() => rejectMerge(candidate.id), 'reject')}
          >
            {loading === 'reject' ? '…' : '✗ Reject'}
          </button>
          <button
            className="btn-sm btn-snooze"
            disabled={loading != null}
            onClick={() => act(() => snoozeMerge(candidate.id), 'snooze')}
          >
            {loading === 'snooze' ? '…' : 'Snooze 30d'}
          </button>
        </div>
      )}

      {canUndo && (
        <div className="merge-actions">
          <button
            className="btn-sm btn-undo"
            disabled={loading != null}
            onClick={() => act(() => undoMerge(candidate.id), 'undo')}
          >
            {loading === 'undo' ? '…' : '↶ Undo merge'}
          </button>
        </div>
      )}
    </div>
  )
}

export default function MergeAuditView() {
  const [candidates, setCandidates] = useState([])
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(true)
  const [entityFilter, setEntityFilter] = useState('organizations')
  const [statusFilter, setStatusFilter] = useState('PENDING')
  const [minConf, setMinConf] = useState(0)
  const [focusedIdx, setFocusedIdx] = useState(0)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [mRes, sRes] = await Promise.all([
        getMerges({ entity_type: entityFilter, status: statusFilter }),
        getMergeStats(),
      ])
      setCandidates(mRes.data.results ?? [])
      setStats(sRes.data)
      setFocusedIdx(0)
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }, [entityFilter, statusFilter])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    const handler = (e) => {
      const active = document.activeElement
      const tag = active?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
      if (active?.isContentEditable) return

      const c = candidates[focusedIdx]
      if (!c) return

      if (e.key === 'ArrowDown' || e.key === 'j') {
        e.preventDefault()
        setFocusedIdx(i => Math.min(i + 1, candidates.length - 1))
      } else if (e.key === 'ArrowUp' || e.key === 'k') {
        e.preventDefault()
        setFocusedIdx(i => Math.max(i - 1, 0))
      } else if (c.status === 'PENDING' && e.shiftKey && !e.metaKey && !e.ctrlKey && !e.altKey) {
        // Destructive actions require Shift to avoid accidental triggers.
        if (e.key === 'M') {
          e.preventDefault()
          confirmMerge(c.id, { surviving_id: c.record_a_id }).then(load)
        } else if (e.key === 'R') {
          e.preventDefault()
          rejectMerge(c.id).then(load)
        } else if (e.key === 'S') {
          e.preventDefault()
          snoozeMerge(c.id).then(load)
        }
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [candidates, focusedIdx, load])

  const filtered = candidates.filter(c => c.confidence >= minConf)

  return (
    <div className="merge-audit-view">
      <div className="merge-audit-header">
        {stats && (
          <div className="merge-stats-bar">
            <span className="merge-stat">
              <strong>{stats.pending}</strong> pending
            </span>
            <span className="merge-stat accent-green">
              <strong>{stats.confirmed_this_week}</strong> confirmed (7d)
            </span>
            <span className="merge-stat accent-red">
              <strong>{stats.rejected_this_week}</strong> rejected (7d)
            </span>
            <span className="merge-stat accent-amber">
              <strong>{stats.snoozed}</strong> snoozed
            </span>
          </div>
        )}

        <div className="merge-filter-bar">
          <div className="merge-filter-group">
            <label className="merge-filter-label">Entity</label>
            <select
              className="merge-select"
              value={entityFilter}
              onChange={e => setEntityFilter(e.target.value)}
            >
              {ENTITY_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div className="merge-filter-group">
            <label className="merge-filter-label">Status</label>
            <select
              className="merge-select"
              value={statusFilter}
              onChange={e => setStatusFilter(e.target.value)}
            >
              {STATUS_OPTIONS.map(s => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}
            </select>
          </div>
          <div className="merge-filter-group">
            <label className="merge-filter-label">Min confidence</label>
            <select
              className="merge-select"
              value={minConf}
              onChange={e => setMinConf(Number(e.target.value))}
            >
              <option value={0}>All</option>
              <option value={0.7}>70%+</option>
              <option value={0.8}>80%+</option>
            </select>
          </div>
          <span className="merge-count">
            {loading ? 'Loading…' : `${filtered.length} candidate${filtered.length !== 1 ? 's' : ''}`}
          </span>
          {statusFilter === 'PENDING' && filtered.length > 0 && (
            <span className="merge-kbd-hint">
              Shift+M confirm · Shift+R reject · Shift+S snooze · ↑↓ navigate
            </span>
          )}
        </div>
      </div>

      <div className="merge-list">
        {!loading && filtered.length === 0 && (
          <div className="merge-empty">No candidates match the current filters.</div>
        )}
        {filtered.map((c, i) => (
          <MergeCard
            key={c.id}
            candidate={c}
            focused={i === focusedIdx}
            onAction={load}
          />
        ))}
      </div>
    </div>
  )
}
