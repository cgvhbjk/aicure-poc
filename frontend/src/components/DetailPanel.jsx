import React, { useState, useEffect } from 'react'
import { getTrialNews } from '../api'

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
  const [expanded, setExpanded] = useState(false)
  if (!text) return null
  const preview = text.slice(0, 200)
  const isLong = text.length > 200
  return (
    <div style={{ marginBottom: 12 }}>
      <div className="detail-field-label" style={{ textAlign: 'left', marginBottom: 4 }}>
        {label}
      </div>
      <div
        className="detail-field-value"
        style={{ whiteSpace: 'pre-wrap', fontSize: 11, lineHeight: 1.5 }}
      >
        {expanded ? text : preview}
        {isLong && (
          <button
            onClick={() => setExpanded((v) => !v)}
            style={{
              display: 'block', marginTop: 4, border: 'none', background: 'none',
              color: '#2563eb', cursor: 'pointer', fontSize: 11, padding: 0,
            }}
          >
            {expanded ? 'Show less ▲' : 'Show more ▼'}
          </button>
        )}
      </div>
    </div>
  )
}

export default function DetailPanel({ trial, onClose }) {
  const [news, setNews] = useState([])
  const [loadingNews, setLoadingNews] = useState(false)

  useEffect(() => {
    if (!trial?.id) return
    setNews([])
    setLoadingNews(true)
    getTrialNews(trial.id)
      .then((r) => setNews(r.data))
      .catch(console.error)
      .finally(() => setLoadingNews(false))
  }, [trial?.id])

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
          {/* Status + external link */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
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
          </div>

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
                        {type === 'link' ? (
                          <a href={value} target="_blank" rel="noopener noreferrer">
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
                  href={item.url}
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
