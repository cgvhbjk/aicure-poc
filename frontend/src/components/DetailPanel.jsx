import React, { useState, useEffect, useCallback } from 'react'
import { getTrial, getTrialNews, getTrialRegistries } from '../api'
import { safeHref } from '../utils/url'

const STATUS_STYLES = {
  RECRUITING:             { background: '#dcfce7', color: '#166534' },
  NOT_YET_RECRUITING:     { background: '#dbeafe', color: '#1e40af' },
  ACTIVE_NOT_RECRUITING:  { background: '#fef9c3', color: '#854d0e' },
  COMPLETED:              { background: '#f1f5f9', color: '#475569' },
}

function parseJsonArr(val) {
  if (!val) return []
  try {
    const arr = JSON.parse(val)
    return Array.isArray(arr) ? arr.filter(Boolean) : []
  } catch {
    return []
  }
}

function yesNo(val) {
  if (val === 1 || val === true) return 'Yes'
  if (val === 0 || val === false) return 'No'
  return null
}

const SECTIONS = [
  {
    title: '🔖 Identification',
    fields: [
      ['Registry Source', () => 'ClinicalTrials.gov'],
      ['Registry ID', (t) => t.id],
      ['Source URL', (t) => t.source_url, 'link'],
      ['Status', (t) => t.status],
    ],
  },
  {
    title: '🏢 Sponsor & Operational',
    fields: [
      ['Sponsor', (t) => t.sponsor],
      ['Sponsor Type', (t) => t.sponsor_type],
      ['CRO Named', (t) => t.cro_named],
      ['Principal Investigator', (t) => t.pi_name],
      ['Contact Email', (t) => t.pi_email],
      ['Lead Country', (t) => t.lead_country],
      ['All Countries', (t) => parseJsonArr(t.countries).join(', ')],
      ['Number of Sites', (t) => t.num_sites],
      ['DCT Elements', (t) => yesNo(t.dct_elements)],
    ],
  },
  {
    title: '🧪 Study Design',
    fields: [
      ['Study Type', (t) => t.study_type],
      ['Phase', (t) => t.phase],
      ['Randomized', (t) => t.randomized],
      ['Blinding / Masking', (t) => t.masking],
      ['Number of Arms', (t) => t.num_arms],
    ],
  },
  {
    title: '💊 Intervention & Disease',
    fields: [
      ['Therapeutic Area', (t) => t.therapeutic_area],
      ['Conditions', (t) => parseJsonArr(t.conditions).join('; ')],
      ['MeSH Terms', (t) => parseJsonArr(t.mesh_terms).join('; ')],
      ['Interventions', (t) => parseJsonArr(t.interventions).join('; ')],
    ],
  },
  {
    title: '👥 Patient Population',
    fields: [
      ['Estimated Enrollment', (t) => t.enrollment],
      ['Min Age', (t) => t.min_age],
      ['Max Age', (t) => t.max_age],
      ['Sex Eligibility', (t) => t.sex_eligibility],
      ['Pediatric Study', (t) => yesNo(t.is_pediatric)],
    ],
  },
  {
    title: '📅 Timeline',
    fields: [
      ['Registration Date', (t) => t.first_posted],
      ['Study Start Date', (t) => t.start_date],
      ['Primary Completion', (t) => t.primary_completion],
      ['Study Completion', (t) => t.study_completion],
      ['Last Updated', (t) => t.last_updated],
    ],
  },
  {
    title: '📊 Endpoints & Outcomes',
    fields: [
      ['Primary Endpoint(s)', (t) => t.primary_endpoints],
      ['Secondary Endpoint(s)', (t) => parseJsonArr(t.secondary_endpoints).join('; ')],
      ['ePRO / eCOA', (t) => yesNo(t.epro_ecoa)],
      ['Digital Biomarkers', (t) => yesNo(t.digital_biomarkers)],
    ],
  },
]

function CriteriaBlock({ label, text }) {
  if (!text) return null
  return (
    <div style={{ marginBottom: 12 }}>
      <div className="detail-field-label" style={{ textAlign: 'left', marginBottom: 4 }}>{label}</div>
      <pre style={{
        whiteSpace: 'pre-wrap', fontFamily: 'inherit', fontSize: 11, lineHeight: 1.6,
        color: '#334155', margin: 0, maxHeight: 280, overflowY: 'auto',
        background: '#f8fafc', borderRadius: 6, padding: '8px 10px',
      }}>
        {text}
      </pre>
    </div>
  )
}

function AllFields({ trial }) {
  const [open, setOpen] = useState(false)
  const entries = Object.entries(trial).filter(([, v]) => v != null && v !== '' && v !== '[]' && v !== '{}')
  return (
    <div style={{ marginBottom: 20 }}>
      <button className="all-fields-toggle" onClick={() => setOpen(v => !v)}>
        {open ? '▼' : '▶'} All Fields
      </button>
      {open && (
        <div className="all-fields-list">
          {entries.map(([key, val]) => (
            <React.Fragment key={key}>
              <span className="all-fields-key">{key}</span>
              <span className="all-fields-val">{String(val).slice(0, 300)}</span>
            </React.Fragment>
          ))}
        </div>
      )}
    </div>
  )
}

const REGISTRY_STYLES = {
  'ClinicalTrials.gov': { background: '#dbeafe', color: '#1e40af' },
  'CTIS':               { background: '#ede9fe', color: '#6d28d9' },
  'EU-CTR':             { background: '#fef3c7', color: '#92400e' },
}

const REGISTRY_URLS = {
  'ClinicalTrials.gov': (id) => `https://clinicaltrials.gov/study/${id}`,
  'CTIS':               (id) => `https://euclinicaltrials.eu/search-for-clinical-trials/?lang=en&query=ctNumber:${id}`,
  'EU-CTR':             (id) => `https://www.clinicaltrialsregister.eu/ctr-search/search?query=${id}`,
}

export default function DetailPanel({ trial: trialRow, onClose }) {
  const [news, setNews] = useState([])
  const [loadingNews, setLoadingNews] = useState(false)
  const [registries, setRegistries] = useState([])
  const [copied, setCopied] = useState(false)
  // The grid row omits fat fields (brief_summary, eligibility criteria, ...)
  // to keep list responses small, so fetch the full record; render the row's
  // fields meanwhile.
  const [fullTrial, setFullTrial] = useState(null)
  // Set when the full-record fetch fails, so the panel can say so rather than
  // silently rendering the trimmed grid row as if those fields were empty.
  const [fullError, setFullError] = useState(false)

  const copyNct = useCallback(() => {
    navigator.clipboard.writeText(trialRow.id).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }, [trialRow?.id])

  useEffect(() => {
    if (!trialRow?.id) return
    // Guard against out-of-order responses: clicking trial A then quickly B
    // must not let A's late response overwrite B's panel. The cleanup flips
    // `cancelled`, so a stale request's handlers (including the loading reset)
    // no-op.
    let cancelled = false
    setNews([])
    setLoadingNews(true)
    setRegistries([])
    setFullTrial(null)
    setFullError(false)
    getTrial(trialRow.id)
      .then((r) => { if (!cancelled) setFullTrial(r.data) })
      .catch((e) => { if (!cancelled) { console.error(e); setFullError(true) } })
    getTrialNews(trialRow.id)
      .then((r) => { if (!cancelled) setNews(r.data) })
      .catch((e) => { if (!cancelled) console.error(e) })
      .finally(() => { if (!cancelled) setLoadingNews(false) })
    getTrialRegistries(trialRow.id)
      .then((r) => { if (!cancelled) setRegistries(r.data) })
      .catch((e) => { if (!cancelled) console.error(e) })
    return () => { cancelled = true }
  }, [trialRow?.id])

  const trial = fullTrial || trialRow
  if (!trial) return null

  const statusStyle = STATUS_STYLES[trial.status] || { background: '#f1f5f9', color: '#475569' }

  return (
    <>
      <div className="detail-overlay" onClick={onClose} />
      <div className="detail-panel">
        <div className="detail-header">
          <div style={{ flex: 1, minWidth: 0 }}>
            <h2 className="detail-title">{trial.title_brief}</h2>
            {trial.title_official && trial.title_official !== trial.title_brief && (
              <p style={{ fontSize: 11, color: '#94a3b8', margin: '4px 0 0', lineHeight: 1.4 }}>
                {trial.title_official}
              </p>
            )}
          </div>
          <button className="detail-close" onClick={onClose}>×</button>
        </div>

        <div className="detail-body">
          {/* Status + NCT link + copy button */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16, flexWrap: 'wrap' }}>
            <span className="badge" style={statusStyle}>
              {(trial.status || '').replace(/_/g, ' ')}
            </span>
            <a
              href={`https://clinicaltrials.gov/study/${trial.id}`}
              target="_blank"
              rel="noopener noreferrer"
              style={{ fontSize: 12 }}
            >
              {trial.id} ↗
            </a>
            <button
              onClick={copyNct}
              style={{
                border: '1px solid #e2e8f0', borderRadius: 4, background: copied ? '#dcfce7' : '#fff',
                color: copied ? '#166534' : '#64748b', fontSize: 11, padding: '2px 8px',
                cursor: 'pointer', transition: 'all 0.2s',
              }}
            >
              {copied ? 'Copied!' : 'Copy NCT'}
            </button>
          </div>

          {/* Full-record fetch failed — the fat fields below come from the
              detail endpoint, so warn instead of showing them as blank. */}
          {fullError && !fullTrial && (
            <div style={{
              marginBottom: 16, padding: '8px 10px', borderRadius: 6,
              background: '#fef3c7', color: '#92400e', fontSize: 12, lineHeight: 1.5,
              border: '1px solid #fde68a',
            }}>
              ⚠ Couldn't load the full record — summary, eligibility criteria, and
              endpoints may be missing. Showing grid data only.
            </div>
          )}

          {/* Brief summary */}
          {trial.brief_summary && (
            <div style={{ marginBottom: 20 }}>
              <div className="detail-section-title">Summary</div>
              <p style={{ fontSize: 12, color: '#334155', lineHeight: 1.6, margin: 0 }}>
                {trial.brief_summary}
              </p>
            </div>
          )}

          {/* Structured sections */}
          {SECTIONS.map((section) => {
            const rows = section.fields
              .map(([label, getter, type]) => [label, getter(trial), type])
              .filter(([, val]) => val != null && val !== '' && val !== '[]')
            if (!rows.length) return null
            return (
              <div key={section.title} style={{ marginBottom: 20 }}>
                <div className="detail-section-title">{section.title}</div>
                <div className="detail-fields">
                  {rows.map(([label, value, type]) => (
                    <React.Fragment key={label}>
                      <span className="detail-field-label">{label}</span>
                      <span className="detail-field-value">
                        {type === 'link' && safeHref(value) ? (
                          <a href={safeHref(value)} target="_blank" rel="noopener noreferrer">
                            {value}
                          </a>
                        ) : (
                          String(value)
                        )}
                      </span>
                    </React.Fragment>
                  ))}
                </div>
              </div>
            )
          })}

          {/* Inclusion / exclusion criteria (long text — expandable) */}
          {(trial.inclusion_criteria || trial.exclusion_criteria) && (
            <div style={{ marginBottom: 20 }}>
              <div className="detail-section-title">👥 Eligibility Criteria</div>
              <CriteriaBlock label="Inclusion" text={trial.inclusion_criteria} />
              <CriteriaBlock label="Exclusion" text={trial.exclusion_criteria} />
            </div>
          )}

          {/* All Fields — power-user escape hatch */}
          <AllFields trial={trial} />

          {/* Registry sources */}
          {registries.length > 0 && (
            <div style={{ marginBottom: 20 }}>
              <div className="detail-section-title">🌐 Registry Sources</div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
                {registries.map((rec) => {
                  const style = REGISTRY_STYLES[rec.registry] || { background: '#f1f5f9', color: '#475569' }
                  const urlFn = REGISTRY_URLS[rec.registry]
                  const url = urlFn ? urlFn(rec.registry_trial_id) : null
                  return (
                    <a
                      key={rec.registry}
                      href={url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="badge"
                      style={{ ...style, textDecoration: 'none' }}
                    >
                      {rec.registry}: {rec.registry_trial_id}
                    </a>
                  )
                })}
              </div>
            </div>
          )}

          {/* Linked news */}
          <div className="detail-section-title">📰 Linked News</div>
          {loadingNews ? (
            <p className="muted">Loading…</p>
          ) : news.length === 0 ? (
            <p className="muted">No linked news articles</p>
          ) : (
            news.map((item) => (
              <div key={item.id} className="news-card">
                <div className="news-card-source">{item.source}</div>
                <a
                  className="news-card-title"
                  href={safeHref(item.url)}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {item.title}
                </a>
                <div className="news-card-date">
                  {item.published_at?.slice(0, 10)}
                  {item.match_method && (
                    <span style={{ color: '#cbd5e1' }}> · {item.match_method}</span>
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </>
  )
}
