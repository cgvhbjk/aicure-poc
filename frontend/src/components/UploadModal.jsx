import React, { useState, useRef } from 'react'
import { uploadData } from '../api'

const ENTITY_OPTIONS = [
  { value: 'trials', label: 'Trials' },
  { value: 'organizations', label: 'Organizations' },
  { value: 'contacts', label: 'Contacts' },
]

export default function UploadModal({ onClose, onDone }) {
  const [step, setStep] = useState('select') // 'select' | 'result'
  const [entityType, setEntityType] = useState('trials')
  const [file, setFile] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)
  const fileRef = useRef()

  const handleSubmit = async () => {
    if (!file) return
    setLoading(true)
    setError(null)
    try {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('entity_type', entityType)
      const r = await uploadData(fd)
      setResult(r.data)
      setStep('result')
      if (onDone) onDone()
    } catch (e) {
      setError(e.response?.data?.detail || e.message || 'Upload failed')
    } finally {
      setLoading(false)
    }
  }

  const downloadErrors = () => {
    if (!result?.errors?.length) return
    const lines = ['row,field,message', ...result.errors.map(e =>
      `${e.row},"${e.field}","${e.message.replace(/"/g, '""')}"`
    )]
    const blob = new Blob([lines.join('\n')], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'upload_errors.csv'
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal-box">
        <div className="modal-header">
          <span className="modal-title">Upload Data</span>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        {step === 'select' && (
          <div className="modal-body">
            <div className="modal-field">
              <label className="modal-label">Entity type</label>
              <div className="modal-radio-group">
                {ENTITY_OPTIONS.map(opt => (
                  <label key={opt.value} className="modal-radio-label">
                    <input
                      type="radio"
                      name="entity_type"
                      value={opt.value}
                      checked={entityType === opt.value}
                      onChange={() => setEntityType(opt.value)}
                    />
                    {opt.label}
                  </label>
                ))}
              </div>
            </div>

            <div className="modal-field">
              <label className="modal-label">File (CSV or XLSX)</label>
              <div
                className={`modal-dropzone${file ? ' has-file' : ''}`}
                onClick={() => fileRef.current.click()}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault()
                  const f = e.dataTransfer.files[0]
                  if (f) setFile(f)
                }}
              >
                {file
                  ? <span className="dropzone-filename">{file.name}</span>
                  : <span className="dropzone-hint">Click or drag a file here</span>
                }
                <input
                  ref={fileRef}
                  type="file"
                  accept=".csv,.xlsx"
                  style={{ display: 'none' }}
                  onChange={(e) => setFile(e.target.files[0] || null)}
                />
              </div>
            </div>

            {error && <div className="modal-error">{error}</div>}

            <div className="modal-footer">
              <button className="btn-sm" onClick={onClose}>Cancel</button>
              <button
                className="btn-sm btn-primary"
                onClick={handleSubmit}
                disabled={!file || loading}
              >
                {loading ? 'Uploading…' : 'Upload'}
              </button>
            </div>
          </div>
        )}

        {step === 'result' && result && (
          <div className="modal-body">
            <div className="upload-result-grid">
              <div className="upload-stat-card">
                <div className="upload-stat-value">{result.row_count}</div>
                <div className="upload-stat-label">Rows read</div>
              </div>
              <div className="upload-stat-card green">
                <div className="upload-stat-value">{result.matched}</div>
                <div className="upload-stat-label">Matched &amp; updated</div>
              </div>
              <div className="upload-stat-card blue">
                <div className="upload-stat-value">{result.inserted}</div>
                <div className="upload-stat-label">Inserted</div>
              </div>
              <div className="upload-stat-card amber">
                <div className="upload-stat-value">{result.skipped}</div>
                <div className="upload-stat-label">Skipped</div>
              </div>
            </div>

            {result.merge_candidates > 0 && (
              <div className="upload-note">
                {result.merge_candidates} merge candidate{result.merge_candidates !== 1 ? 's' : ''} queued for review.
              </div>
            )}

            {result.preview?.length > 0 && (
              <div className="upload-preview">
                <div className="modal-label">Preview (first 5 rows)</div>
                {result.preview.map((p, i) => (
                  <div key={i} className="upload-preview-row">
                    <span className={`upload-action-badge ${p.action}`}>{p.action.replace(/_/g, ' ')}</span>
                    <span className="upload-preview-id">{p.id || p.name}</span>
                    {p.score != null && (
                      <span className="upload-preview-score">score {p.score.toFixed(2)}</span>
                    )}
                  </div>
                ))}
              </div>
            )}

            {result.errors?.length > 0 && (
              <div className="upload-errors">
                <div className="modal-label">
                  {result.errors.length} error{result.errors.length !== 1 ? 's' : ''}
                  <button className="btn-link" onClick={downloadErrors}>Download CSV</button>
                </div>
                <div className="upload-error-list">
                  {result.errors.slice(0, 5).map((e, i) => (
                    <div key={i} className="upload-error-row">
                      Row {e.row}{e.field ? ` · ${e.field}` : ''}: {e.message}
                    </div>
                  ))}
                  {result.errors.length > 5 && (
                    <div className="upload-error-more">+{result.errors.length - 5} more</div>
                  )}
                </div>
              </div>
            )}

            <div className="modal-footer">
              <button className="btn-sm btn-primary" onClick={onClose}>Done</button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
