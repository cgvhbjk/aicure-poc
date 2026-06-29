import React, { useState, useEffect, useRef, useCallback } from 'react'
import { getOrgTrials, getOrgContacts, addOrgContact, enrichOrgContacts, patchOrg } from '../api'
import { safeHref } from '../utils/url'

const ORG_TYPE_OPTIONS = ['PHARMA', 'BIOTECH', 'CRO', 'DCT_VENDOR', 'DIGITAL_HEALTH', 'RPM', 'TELEHEALTH', 'ACADEMIC', 'GOVERNMENT', 'OTHER']

const STATUS_STYLES = {
  RECRUITING:            { background: '#dcfce7', color: '#166534' },
  NOT_YET_RECRUITING:    { background: '#dbeafe', color: '#1e40af' },
  ACTIVE_NOT_RECRUITING: { background: '#fef9c3', color: '#854d0e' },
  COMPLETED:             { background: '#f1f5f9', color: '#475569' },
}

const ORG_TYPE_STYLES = {
  PHARMA:         { background: '#dbeafe', color: '#1e40af' },
  BIOTECH:        { background: '#ede9fe', color: '#5b21b6' },
  CRO:            { background: '#f3e8ff', color: '#7c3aed' },
  DCT_VENDOR:     { background: '#ccfbf1', color: '#0f766e' },
  DIGITAL_HEALTH: { background: '#cffafe', color: '#0e7490' },
  RPM:            { background: '#dcfce7', color: '#15803d' },
  TELEHEALTH:     { background: '#d1fae5', color: '#065f46' },
  ACADEMIC:       { background: '#ffedd5', color: '#9a3412' },
  GOVERNMENT:     { background: '#fef3c7', color: '#92400e' },
  OTHER:          { background: '#f1f5f9', color: '#475569' },
}

function parseJsonArr(val) {
  if (!val) return []
  try { return JSON.parse(val) } catch { return [] }
}

function InlineSelect({ value, options, onSave, renderLabel }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value || '')

  const commit = useCallback(async (val) => {
    if (val !== value) await onSave(val)
    setEditing(false)
  }, [value, onSave])

  if (editing) {
    return (
      <select
        autoFocus
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => commit(draft)}
        style={{ fontSize: 12, padding: '2px 6px', border: '1px solid #93c5fd', borderRadius: 4 }}
      >
        <option value="">—</option>
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    )
  }
  return (
    <span
      onClick={() => { setDraft(value || ''); setEditing(true) }}
      style={{ cursor: 'pointer', borderBottom: '1px dashed #cbd5e1', fontSize: 12 }}
      title="Click to edit"
    >
      {renderLabel ? renderLabel(value) : (value || '—')}
    </span>
  )
}

function InlineText({ value, onSave, placeholder }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value || '')

  const commit = useCallback(async () => {
    if (draft !== value) await onSave(draft)
    setEditing(false)
  }, [draft, value, onSave])

  if (editing) {
    return (
      <input
        autoFocus
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === 'Enter' && commit()}
        placeholder={placeholder}
        style={{ fontSize: 12, padding: '2px 6px', border: '1px solid #93c5fd', borderRadius: 4, width: '100%' }}
      />
    )
  }
  return (
    <span
      onClick={() => { setDraft(value || ''); setEditing(true) }}
      style={{ cursor: 'pointer', borderBottom: '1px dashed #cbd5e1', fontSize: 12, color: value ? '#334155' : '#94a3b8' }}
      title="Click to edit"
    >
      {value || placeholder || '—'}
    </span>
  )
}

function NotesArea({ value, onSave }) {
  const [draft, setDraft] = useState(value || '')

  useEffect(() => { setDraft(value || '') }, [value])

  return (
    <textarea
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => { if (draft !== value) onSave(draft) }}
      rows={3}
      placeholder="Analyst notes…"
      style={{
        width: '100%', fontSize: 12, padding: '6px 8px',
        border: '1px solid #e2e8f0', borderRadius: 6,
        resize: 'vertical', fontFamily: 'inherit', color: '#334155',
        boxSizing: 'border-box',
      }}
    />
  )
}

const EMPTY_CONTACT = { full_name: '', title: '', department: '', email: '', linkedin_url: '', source_url: '', is_decision_maker: 0, notes: '' }

export default function OrgDetailPanel({ org, onClose, onSelectTrial, onOrgUpdated }) {
  const [trials, setTrials] = useState([])
  const [contacts, setContacts] = useState([])
  const [loadingTrials, setLoadingTrials] = useState(false)
  const [showContactForm, setShowContactForm] = useState(false)
  const [contactDraft, setContactDraft] = useState(EMPTY_CONTACT)
  const [savingContact, setSavingContact] = useState(false)
  const [enriching, setEnriching] = useState(false)
  const [enrichMsg, setEnrichMsg] = useState(null)

  // Track mount so a slow enrichment that resolves after the panel closes
  // doesn't setState on an unmounted component.
  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  useEffect(() => {
    if (!org?.id) return
    // Clear stale enrichment UI from a previously-viewed org (the rest of the
    // panel's state is keyed off org?.id; keep this consistent).
    setEnrichMsg(null)
    setEnriching(false)
    setLoadingTrials(true)
    getOrgTrials(org.id).then((r) => setTrials(r.data)).catch(console.error).finally(() => setLoadingTrials(false))
    getOrgContacts(org.id).then((r) => setContacts(r.data)).catch(console.error)
  }, [org?.id])

  const patch = useCallback(async (field, value) => {
    try {
      const res = await patchOrg(org.id, { [field]: value })
      onOrgUpdated?.(res.data)
    } catch (e) {
      console.error('Patch failed', e)
    }
  }, [org?.id, onOrgUpdated])

  const handleEnrich = async () => {
    setEnriching(true)
    setEnrichMsg(null)
    try {
      const res = await enrichOrgContacts(org.id)
      if (!mountedRef.current) return
      if (Array.isArray(res.data?.contacts)) setContacts(res.data.contacts)
      const st = res.data?.status || {}
      if (st.ok) {
        const calls = st.api_calls ?? 0
        setEnrichMsg(`${st.source}: +${st.inserted ?? 0} added · ${calls} API call${calls === 1 ? '' : 's'}`)
      } else {
        setEnrichMsg(st.error || 'Enrichment unavailable')
      }
    } catch (e) {
      // Report the actual failure rather than always blaming auth. Log the raw
      // error so a Seamless outage / network fault is debuggable.
      console.error('enrichOrgContacts failed', e)
      if (!mountedRef.current) return
      const status = e?.response?.status
      const detail = e?.response?.data?.detail
      setEnrichMsg(
        detail
        || (status === 401 || status === 403 ? 'Not authorized to enrich'
            : status ? `Enrichment failed (HTTP ${status})`
            : 'Enrichment failed — network or server error')
      )
    } finally {
      if (mountedRef.current) setEnriching(false)
    }
  }

  const handleAddContact = async () => {
    if (!contactDraft.full_name.trim()) return
    setSavingContact(true)
    try {
      const res = await addOrgContact(org.id, contactDraft)
      setContacts((prev) => [...prev, res.data])
      setContactDraft(EMPTY_CONTACT)
      setShowContactForm(false)
    } catch (e) {
      console.error('Failed to add contact', e)
    } finally {
      setSavingContact(false)
    }
  }

  if (!org) return null

  const typeStyle = ORG_TYPE_STYLES[org.org_type] || ORG_TYPE_STYLES.OTHER
  const focuses = parseJsonArr(org.therapeutic_focus)

  return (
    <>
      <div className="detail-overlay" onClick={onClose} />
      <div className="detail-panel">
        <div className="detail-header">
          <div style={{ flex: 1, minWidth: 0 }}>
            <h2 className="detail-title">{org.canonical_name}</h2>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6, flexWrap: 'wrap' }}>
              {org.org_type && (
                <span className="badge" style={typeStyle}>{org.org_type.replace(/_/g, ' ')}</span>
              )}
              {safeHref(org.website) && (
                <a href={safeHref(org.website)} target="_blank" rel="noopener noreferrer" style={{ fontSize: 12 }}>
                  Website ↗
                </a>
              )}
              {safeHref(org.linkedin_url) && (
                <a href={safeHref(org.linkedin_url)} target="_blank" rel="noopener noreferrer" style={{ fontSize: 12 }}>
                  LinkedIn ↗
                </a>
              )}
            </div>
          </div>
          <button className="detail-close" onClick={onClose}>×</button>
        </div>

        <div className="detail-body">
          {/* Overview */}
          <div style={{ marginBottom: 20 }}>
            <div className="detail-section-title">Overview</div>
            <div className="detail-fields">
              <span className="detail-field-label">Org Type</span>
              <span className="detail-field-value">
                <InlineSelect
                  value={org.org_type}
                  options={ORG_TYPE_OPTIONS}
                  onSave={(v) => patch('org_type', v)}
                  renderLabel={(v) => v ? (
                    <span className="badge" style={ORG_TYPE_STYLES[v] || ORG_TYPE_STYLES.OTHER}>
                      {v.replace(/_/g, ' ')}
                    </span>
                  ) : '—'}
                />
              </span>

              <span className="detail-field-label">Website</span>
              <span className="detail-field-value">
                <InlineText value={org.website} onSave={(v) => patch('website', v)} placeholder="https://…" />
              </span>

              <span className="detail-field-label">LinkedIn</span>
              <span className="detail-field-value">
                <InlineText value={org.linkedin_url} onSave={(v) => patch('linkedin_url', v)} placeholder="https://linkedin.com/company/…" />
              </span>

              <span className="detail-field-label">Trials</span>
              <span className="detail-field-value">{org.trial_count ?? 0}</span>
            </div>

            {focuses.length > 0 && (
              <div style={{ marginTop: 8 }}>
                <div style={{ fontSize: 11, color: '#94a3b8', fontWeight: 600, marginBottom: 6 }}>THERAPEUTIC FOCUS</div>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {focuses.map((f) => (
                    <span key={f} className="badge" style={{ background: '#f1f5f9', color: '#475569' }}>{f}</span>
                  ))}
                </div>
              </div>
            )}

            <div style={{ marginTop: 12 }}>
              <div style={{ fontSize: 11, color: '#94a3b8', fontWeight: 600, marginBottom: 4 }}>NOTES</div>
              <NotesArea value={org.notes} onSave={(v) => patch('notes', v)} />
            </div>
          </div>

          {/* Linked Trials */}
          <div style={{ marginBottom: 20 }}>
            <div className="detail-section-title">Linked Trials</div>
            {loadingTrials ? (
              <p className="muted">Loading…</p>
            ) : trials.length === 0 ? (
              <p className="muted">No linked trials</p>
            ) : (
              <div style={{ maxHeight: 260, overflowY: 'auto' }}>
                {trials.map((t) => {
                  const statusStyle = STATUS_STYLES[t.status] || { background: '#f1f5f9', color: '#475569' }
                  return (
                    <div
                      key={t.id}
                      className="news-card"
                      style={{ cursor: 'pointer' }}
                      onClick={() => onSelectTrial?.(t)}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                        <span className="badge" style={statusStyle}>{(t.status || '').replace(/_/g, ' ')}</span>
                        {t.phase && <span style={{ fontSize: 11, color: '#94a3b8' }}>{t.phase.replace('PHASE', 'Phase ')}</span>}
                        {t.role && <span style={{ fontSize: 11, color: '#94a3b8' }}>{t.role}</span>}
                      </div>
                      <div style={{ fontSize: 12, color: '#1e293b', lineHeight: 1.4 }}>{t.title_brief}</div>
                      <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 2 }}>{t.id}</div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          {/* Contacts */}
          <div style={{ marginBottom: 20 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
              <div className="detail-section-title" style={{ margin: 0 }}>Contacts</div>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <button
                  className="btn-sm"
                  onClick={handleEnrich}
                  disabled={enriching}
                  style={{ fontSize: 11 }}
                  title="Find CMO / clinical decision-makers via Seamless.AI (cached to avoid re-spending credits)"
                >
                  {enriching ? 'Enriching…' : 'Enrich (Seamless)'}
                </button>
                <button
                  className="btn-sm"
                  onClick={() => setShowContactForm((v) => !v)}
                  style={{ fontSize: 11 }}
                >
                  {showContactForm ? 'Cancel' : '+ Add contact'}
                </button>
              </div>
            </div>
            {enrichMsg && (
              <div style={{ fontSize: 11, color: '#64748b', marginBottom: 8 }}>{enrichMsg}</div>
            )}

            {showContactForm && (
              <div style={{
                background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: 8,
                padding: '12px', marginBottom: 12,
              }}>
                {[
                  ['Name *', 'full_name', 'text'],
                  ['Title', 'title', 'text'],
                  ['Department', 'department', 'text'],
                  ['Email', 'email', 'email'],
                  ['LinkedIn URL', 'linkedin_url', 'url'],
                  ['Source URL', 'source_url', 'url'],
                  ['Notes', 'notes', 'text'],
                ].map(([label, field, type]) => (
                  <div key={field} style={{ display: 'flex', gap: 8, marginBottom: 6, alignItems: 'center' }}>
                    <span style={{ fontSize: 11, color: '#94a3b8', width: 90, flexShrink: 0 }}>{label}</span>
                    <input
                      type={type}
                      value={contactDraft[field]}
                      onChange={(e) => setContactDraft((d) => ({ ...d, [field]: e.target.value }))}
                      style={{ flex: 1, fontSize: 12, padding: '3px 7px', border: '1px solid #cbd5e1', borderRadius: 5 }}
                    />
                  </div>
                ))}
                <label style={{ fontSize: 12, color: '#334155', display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
                  <input
                    type="checkbox"
                    checked={!!contactDraft.is_decision_maker}
                    onChange={(e) => setContactDraft((d) => ({ ...d, is_decision_maker: e.target.checked ? 1 : 0 }))}
                  />
                  Decision maker
                </label>
                <button
                  className="btn-sm"
                  onClick={handleAddContact}
                  disabled={savingContact || !contactDraft.full_name.trim()}
                  style={{ background: '#2563eb', color: '#fff', border: 'none' }}
                >
                  {savingContact ? 'Saving…' : 'Save contact'}
                </button>
              </div>
            )}

            {contacts.length === 0 && !showContactForm && (
              <p className="muted">No contacts yet</p>
            )}

            {contacts.map((c) => (
              <div key={c.id} style={{
                border: '1px solid #e2e8f0', borderRadius: 8, padding: '10px 12px',
                marginBottom: 8, background: '#fff',
              }}>
                <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8 }}>
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 600, color: '#1e293b' }}>
                      {c.full_name}
                      {!!c.is_decision_maker && (
                        <span className="badge" style={{ background: '#dbeafe', color: '#1e40af', marginLeft: 6, fontSize: 10 }}>
                          Decision maker
                        </span>
                      )}
                    </div>
                    {c.title && <div style={{ fontSize: 12, color: '#475569' }}>{c.title}</div>}
                    {c.department && <div style={{ fontSize: 11, color: '#94a3b8' }}>{c.department}</div>}
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 10, marginTop: 6, flexWrap: 'wrap' }}>
                  {c.email && <a href={`mailto:${c.email}`} style={{ fontSize: 12 }}>{c.email}</a>}
                  {safeHref(c.linkedin_url) && (
                    <a href={safeHref(c.linkedin_url)} target="_blank" rel="noopener noreferrer" style={{ fontSize: 12 }}>
                      LinkedIn ↗
                    </a>
                  )}
                </div>
                {c.notes && <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 4 }}>{c.notes}</div>}
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
  )
}
