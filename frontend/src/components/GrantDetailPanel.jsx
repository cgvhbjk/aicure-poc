import React, { useState, useEffect } from 'react'
import { getGrant, getGrantTrials } from '../api'

const STATUS_STYLES = {
  ACTIVE:     { background: '#dcfce7', color: '#166534' },
  COMPLETED:  { background: '#f1f5f9', color: '#475569' },
  UNKNOWN:    { background: '#f1f5f9', color: '#94a3b8' },
}

const SOURCE_STYLES = {
  NIH_REPORTER: { background: '#dbeafe', color: '#1e40af' },
  USASPENDING:  { background: '#e0e7ff', color: '#3730a3' },
  PCORI:        { background: '#ccfbf1', color: '#0f766e' },
  CORDIS:       { background: '#dcfce7', color: '#166534' },
  UKRI:         { background: '#ede9fe', color: '#6d28d9' },
  AHA:          { background: '#fee2e2', color: '#991b1b' },
  ADA:          { background: '#ffedd5', color: '#9a3412' },
}

const TRIAL_STATUS_STYLES = {
  RECRUITING:             { background: '#dcfce7', color: '#166634' },
  NOT_YET_RECRUITING:     { background: '#dbeafe', color: '#1e40af' },
  ACTIVE_NOT_RECRUITING:  { background: '#fef9c3', color: '#854d0e' },
  COMPLETED:              { background: '#f1f5f9', color: '#475569' },
}

function fmtUsd(n) {
  if (n == null) return '—'
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `$${(n / 1_000).toFixed(0)}K`
  return `$${n.toLocaleString()}`
}

function fmtOriginalAmount(amount_original, currency) {
  if (amount_original == null || !currency) return null
  const n = Number(amount_original)
  if (isNaN(n)) return null
  const sym = currency === 'GBP' ? '£' : currency === 'EUR' ? '€' : '$'
  return `${sym}${n.toLocaleString()} ${currency}`
}

function TagList({ items }) {
  if (!items || !items.length) return <span style={{ color: '#94a3b8' }}>—</span>
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
      {items.map((item) => (
        <span key={item} className="badge" style={{
          background: '#f1f5f9', color: '#475569',
          fontSize: 11, border: '1px solid #e2e8f0',
        }}>
          {item}
        </span>
      ))}
    </div>
  )
}

function parseJsonArray(val) {
  if (!val) return []
  try {
    const arr = JSON.parse(val)
    return Array.isArray(arr) ? arr.filter(Boolean) : []
  } catch {
    return []
  }
}

export default function GrantDetailPanel({ grant: grantRow, onClose, onSelectTrial }) {
  const [linkedTrials, setLinkedTrials] = useState([])
  const [loadingTrials, setLoadingTrials] = useState(false)
  // The grid row omits fat fields (abstract) to keep list responses small, so
  // fetch the full record; render the row's fields meanwhile.
  const [fullGrant, setFullGrant] = useState(null)
  // Set when the full-record fetch fails, so the panel can say so rather than
  // silently rendering the trimmed grid row as if those fields were empty.
  const [fullError, setFullError] = useState(false)

  useEffect(() => {
    if (!grantRow?.id) return
    // Guard against out-of-order responses (rapid row clicks): the cleanup
    // flips `cancelled` so a stale request's handlers no-op.
    let cancelled = false
    setLinkedTrials([])
    setLoadingTrials(true)
    setFullGrant(null)
    setFullError(false)
    getGrant(grantRow.id)
      .then((r) => { if (!cancelled) setFullGrant(r.data) })
      .catch((e) => { if (!cancelled) { console.error(e); setFullError(true) } })
    getGrantTrials(grantRow.id)
      .then((r) => { if (!cancelled) setLinkedTrials(r.data) })
      .catch((e) => { if (!cancelled) console.error(e) })
      .finally(() => { if (!cancelled) setLoadingTrials(false) })
    return () => { cancelled = true }
  }, [grantRow?.id])

  const grant = fullGrant || grantRow
  if (!grant) return null

  const statusStyle = STATUS_STYLES[grant.status] || STATUS_STYLES.UNKNOWN
  const sourceStyle = SOURCE_STYLES[grant.source] || { background: '#f1f5f9', color: '#475569' }
  const conditions = parseJsonArray(grant.conditions)
  const interventions = parseJsonArray(grant.interventions)
  const showOriginal = grant.currency && grant.currency !== 'USD' && grant.amount_original != null
  const origFmt = fmtOriginalAmount(grant.amount_original, grant.currency)

  return (
    <>
      <div className="detail-overlay" onClick={onClose} />
      <div className="detail-panel">
        <div className="detail-header">
          <div style={{ flex: 1, minWidth: 0 }}>
            <h2 className="detail-title">{grant.title}</h2>
          </div>
          <button className="detail-close" onClick={onClose}>×</button>
        </div>

        <div className="detail-body">
          {/* Badges */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
            <span className="badge" style={sourceStyle}>{grant.source?.replace(/_/g, ' ')}</span>
            <span className="badge" style={statusStyle}>{grant.status || 'UNKNOWN'}</span>
            {grant.therapeutic_area && (
              <span className="badge" style={{ background: '#f8fafc', color: '#475569', border: '1px solid #e2e8f0' }}>
                {grant.therapeutic_area}
              </span>
            )}
            {grant.activity_code && (
              <span className="badge" style={{ background: '#f1f5f9', color: '#334155', fontFamily: 'monospace', fontSize: 11 }}>
                {grant.activity_code}
              </span>
            )}
          </div>

          {/* Full-record fetch failed — the abstract and other detail-only
              fields come from the detail endpoint, so warn rather than blank. */}
          {fullError && !fullGrant && (
            <div style={{
              marginBottom: 16, padding: '8px 10px', borderRadius: 6,
              background: '#fef3c7', color: '#92400e', fontSize: 12, lineHeight: 1.5,
              border: '1px solid #fde68a',
            }}>
              ⚠ Couldn't load the full record — the abstract and some fields may be
              missing. Showing grid data only.
            </div>
          )}

          {/* Funder section */}
          <div style={{ marginBottom: 20 }}>
            <div className="detail-section-title">💰 Funder</div>
            <div className="detail-fields">
              {grant.sponsor_funder && (
                <>
                  <span className="detail-field-label">Funding Agency</span>
                  <span className="detail-field-value">{grant.sponsor_funder}</span>
                </>
              )}
              {grant.agency_division && (
                <>
                  <span className="detail-field-label">Division</span>
                  <span className="detail-field-value">{grant.agency_division}</span>
                </>
              )}
              {grant.activity_code && (
                <>
                  <span className="detail-field-label">Award Type</span>
                  <span className="detail-field-value" style={{ fontFamily: 'monospace' }}>{grant.activity_code}</span>
                </>
              )}
              {grant.award_id && (
                <>
                  <span className="detail-field-label">Award ID</span>
                  <span className="detail-field-value">{grant.award_id}</span>
                </>
              )}
              {grant.amount_usd != null && (
                <>
                  <span className="detail-field-label">Amount (USD)</span>
                  <span className="detail-field-value" style={{ fontWeight: 600 }}>
                    {fmtUsd(grant.amount_usd)}
                    {showOriginal && origFmt && (
                      <span style={{ marginLeft: 8, color: '#64748b', fontWeight: 400, fontSize: 12 }}>
                        ({origFmt})
                      </span>
                    )}
                  </span>
                </>
              )}
              {grant.fiscal_year && (
                <>
                  <span className="detail-field-label">Fiscal Year</span>
                  <span className="detail-field-value">{grant.fiscal_year}</span>
                </>
              )}
              {grant.research_type && (
                <>
                  <span className="detail-field-label">Research Type</span>
                  <span className="detail-field-value">{grant.research_type}</span>
                </>
              )}
              {grant.project_acronym && (
                <>
                  <span className="detail-field-label">Acronym</span>
                  <span className="detail-field-value" style={{ fontFamily: 'monospace' }}>{grant.project_acronym}</span>
                </>
              )}
              {grant.award_date && (
                <>
                  <span className="detail-field-label">Award Date</span>
                  <span className="detail-field-value">{grant.award_date?.slice(0, 10)}</span>
                </>
              )}
              {(grant.start_date || grant.end_date) && (
                <>
                  <span className="detail-field-label">Period</span>
                  <span className="detail-field-value">
                    {grant.start_date?.slice(0, 10) || '?'} → {grant.end_date?.slice(0, 10) || '?'}
                  </span>
                </>
              )}
            </div>
          </div>

          {/* Recipient section */}
          <div style={{ marginBottom: 20 }}>
            <div className="detail-section-title">🏢 Recipient</div>
            <div className="detail-fields">
              {grant.organization && (
                <>
                  <span className="detail-field-label">Institution</span>
                  <span className="detail-field-value">{grant.organization}</span>
                </>
              )}
              {grant.org_type && (
                <>
                  <span className="detail-field-label">Org Type</span>
                  <span className="detail-field-value">{grant.org_type}</span>
                </>
              )}
              {grant.pi_name && (
                <>
                  <span className="detail-field-label">PI</span>
                  <span className="detail-field-value">{grant.pi_name}</span>
                </>
              )}
              {grant.pi_email && (
                <>
                  <span className="detail-field-label">PI Email</span>
                  <span className="detail-field-value">
                    <a href={`mailto:${grant.pi_email}`}>{grant.pi_email}</a>
                  </span>
                </>
              )}
              {grant.country && (
                <>
                  <span className="detail-field-label">Country</span>
                  <span className="detail-field-value">{grant.country}</span>
                </>
              )}
            </div>
          </div>

          {/* Abstract */}
          {grant.abstract && (
            <div style={{ marginBottom: 20 }}>
              <div className="detail-section-title">📄 Abstract</div>
              <div style={{
                fontSize: 12, color: '#334155', lineHeight: 1.7,
                maxHeight: 320, overflowY: 'auto',
                background: '#f8fafc', borderRadius: 6, padding: '10px 12px',
                marginTop: 6,
              }}>
                {grant.abstract}
              </div>
            </div>
          )}

          {/* Research focus */}
          {(conditions.length > 0 || interventions.length > 0 || grant.phase_mentioned) && (
            <div style={{ marginBottom: 20 }}>
              <div className="detail-section-title">🔬 Research Focus</div>
              <div className="detail-fields">
                {conditions.length > 0 && (
                  <>
                    <span className="detail-field-label">Conditions</span>
                    <span className="detail-field-value"><TagList items={conditions} /></span>
                  </>
                )}
                {interventions.length > 0 && (
                  <>
                    <span className="detail-field-label">Interventions</span>
                    <span className="detail-field-value"><TagList items={interventions} /></span>
                  </>
                )}
                {grant.phase_mentioned && (
                  <>
                    <span className="detail-field-label">Phase</span>
                    <span className="detail-field-value">{grant.phase_mentioned}</span>
                  </>
                )}
              </div>
            </div>
          )}

          {/* Linked trials */}
          <div style={{ marginBottom: 20 }}>
            <div className="detail-section-title">🔗 Linked Trials</div>
            {loadingTrials ? (
              <p className="muted">Loading…</p>
            ) : linkedTrials.length === 0 ? (
              <p className="muted">No linked trials found</p>
            ) : (
              linkedTrials.map((trial) => {
                const ts = TRIAL_STATUS_STYLES[trial.status] || { background: '#f1f5f9', color: '#475569' }
                return (
                  <div
                    key={trial.id}
                    className="news-card"
                    style={{ cursor: 'pointer' }}
                    onClick={() => onSelectTrial?.(trial)}
                  >
                    <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 4 }}>
                      <span style={{ fontSize: 11, fontWeight: 600, color: '#1e40af' }}>{trial.id}</span>
                      <span className="badge" style={{ ...ts, fontSize: 10 }}>
                        {(trial.status || '').replace(/_/g, ' ')}
                      </span>
                      {trial.phase && (
                        <span className="badge" style={{ background: '#ede9fe', color: '#7c3aed', fontSize: 10 }}>
                          {trial.phase.replace('PHASE', 'Phase ')}
                        </span>
                      )}
                      {trial.match_method && (
                        <span style={{ fontSize: 10, color: '#94a3b8' }}>{trial.match_method}</span>
                      )}
                    </div>
                    <div style={{ fontSize: 12, color: '#334155' }}>{trial.title_brief}</div>
                  </div>
                )
              })
            )}
          </div>

          {/* Source link */}
          {grant.source_url && (
            <div style={{ marginBottom: 20 }}>
              <a
                href={grant.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="btn-sm"
                style={{ textDecoration: 'none', display: 'inline-block' }}
              >
                View on {grant.source?.replace(/_/g, ' ')} ↗
              </a>
            </div>
          )}
        </div>
      </div>
    </>
  )
}
