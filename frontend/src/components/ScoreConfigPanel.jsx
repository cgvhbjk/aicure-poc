import React, { useState } from 'react'
import { DEFAULT_SCORE_CONFIG, loadScoreConfig, saveScoreConfig } from '../utils/scoreConfig'

const WEIGHT_LABELS = {
  digital_tech:       { label: 'Digital Health Tech', desc: 'ePRO/eCOA, digital biomarkers, DCT elements' },
  therapeutic_area:   { label: 'Therapeutic Area',    desc: 'How well the disease area matches AiCure targets' },
  phase:              { label: 'Phase',                desc: 'Preference for later-phase trials' },
  status:             { label: 'Status',               desc: 'Preference for actively recruiting trials' },
  enrollment:         { label: 'Enrollment Size',      desc: 'Log-scaled preference for larger trials' },
}

export default function ScoreConfigPanel({ onClose, onConfigChange }) {
  const [cfg, setCfg] = useState(() => loadScoreConfig())
  const [openSection, setOpenSection] = useState(null)

  const setWeight = (key, val) => {
    const n = Math.max(0, Math.min(100, Number(val) || 0))
    setCfg(prev => ({ ...prev, weights: { ...prev.weights, [key]: n } }))
  }

  const setAreaScore = (area, val) => {
    const n = Math.max(0, Math.min(100, Number(val) || 0))
    setCfg(prev => ({ ...prev, area_scores: { ...prev.area_scores, [area]: n } }))
  }

  const setPhaseScore = (phase, val) => {
    const n = Math.max(0, Math.min(100, Number(val) || 0))
    setCfg(prev => ({ ...prev, phase_scores: { ...prev.phase_scores, [phase]: n } }))
  }

  const setStatusScore = (status, val) => {
    const n = Math.max(0, Math.min(100, Number(val) || 0))
    setCfg(prev => ({ ...prev, status_scores: { ...prev.status_scores, [status]: n } }))
  }

  const handleSave = () => {
    saveScoreConfig(cfg)
    onConfigChange?.()
    onClose()
  }

  const handleReset = () => {
    setCfg(DEFAULT_SCORE_CONFIG)
  }

  const totalWeight = Object.values(cfg.weights).reduce((a, b) => a + b, 0)

  return (
    <>
      <div className="detail-overlay" onClick={onClose} />
      <div className="detail-panel" style={{ width: 480 }}>
        <div className="detail-header">
          <h2 className="detail-title">AiCure Fit Score Config</h2>
          <button className="detail-close" onClick={onClose}>×</button>
        </div>

        <div className="detail-body">
          <p style={{ fontSize: 12, color: '#64748b', marginBottom: 16 }}>
            Each factor contributes a weighted portion of the 0–100 fit score.
            Total weight: <strong style={{ color: totalWeight === 100 ? '#16a34a' : '#d97706' }}>{totalWeight}</strong>
            {totalWeight !== 100 && <span style={{ color: '#d97706' }}> (ideally 100)</span>}
          </p>

          <div className="detail-section-title">Factor Weights</div>
          <div style={{ marginBottom: 20 }}>
            {Object.entries(cfg.weights).map(([key, val]) => {
              const meta = WEIGHT_LABELS[key] || { label: key, desc: '' }
              const hasSubScores = key === 'therapeutic_area' || key === 'phase' || key === 'status'
              return (
                <div key={key} style={{ marginBottom: 14 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 2 }}>
                    <span style={{ flex: 1, fontSize: 13, fontWeight: 500 }}>{meta.label}</span>
                    <input
                      type="number"
                      min="0" max="100"
                      value={val}
                      onChange={e => setWeight(key, e.target.value)}
                      style={{ width: 52, padding: '2px 6px', border: '1px solid #cbd5e1', borderRadius: 4, fontSize: 12 }}
                    />
                    <span style={{ fontSize: 11, color: '#94a3b8' }}>pts</span>
                    {hasSubScores && (
                      <button
                        className="btn-sm"
                        style={{ fontSize: 11, padding: '1px 6px' }}
                        onClick={() => setOpenSection(openSection === key ? null : key)}
                      >
                        {openSection === key ? '▲' : '▼'}
                      </button>
                    )}
                  </div>
                  <div style={{ fontSize: 11, color: '#94a3b8', paddingLeft: 2 }}>{meta.desc}</div>

                  {openSection === 'therapeutic_area' && key === 'therapeutic_area' && (
                    <div style={{ marginTop: 8, paddingLeft: 12, borderLeft: '2px solid #e2e8f0' }}>
                      <div style={{ fontSize: 11, color: '#64748b', marginBottom: 4 }}>Per-area sub-scores (scaled to weight above):</div>
                      {Object.entries(cfg.area_scores).map(([area, score]) => (
                        <div key={area} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                          <span style={{ flex: 1, fontSize: 12 }}>{area}</span>
                          <input
                            type="number" min="0" max="100"
                            value={score}
                            onChange={e => setAreaScore(area, e.target.value)}
                            style={{ width: 48, padding: '2px 4px', border: '1px solid #e2e8f0', borderRadius: 3, fontSize: 12 }}
                          />
                        </div>
                      ))}
                    </div>
                  )}

                  {openSection === 'phase' && key === 'phase' && (
                    <div style={{ marginTop: 8, paddingLeft: 12, borderLeft: '2px solid #e2e8f0' }}>
                      <div style={{ fontSize: 11, color: '#64748b', marginBottom: 4 }}>Per-phase sub-scores (scaled to weight above):</div>
                      {Object.entries(cfg.phase_scores).map(([phase, score]) => (
                        <div key={phase} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                          <span style={{ flex: 1, fontSize: 12 }}>{phase.replace('PHASE', 'Phase ').replace('_', '/')}</span>
                          <input
                            type="number" min="0" max="100"
                            value={score}
                            onChange={e => setPhaseScore(phase, e.target.value)}
                            style={{ width: 48, padding: '2px 4px', border: '1px solid #e2e8f0', borderRadius: 3, fontSize: 12 }}
                          />
                        </div>
                      ))}
                    </div>
                  )}

                  {openSection === 'status' && key === 'status' && (
                    <div style={{ marginTop: 8, paddingLeft: 12, borderLeft: '2px solid #e2e8f0' }}>
                      <div style={{ fontSize: 11, color: '#64748b', marginBottom: 4 }}>Per-status sub-scores (scaled to weight above):</div>
                      {Object.entries(cfg.status_scores).map(([status, score]) => (
                        <div key={status} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                          <span style={{ flex: 1, fontSize: 12 }}>{status.replace(/_/g, ' ')}</span>
                          <input
                            type="number" min="0" max="100"
                            value={score}
                            onChange={e => setStatusScore(status, e.target.value)}
                            style={{ width: 48, padding: '2px 4px', border: '1px solid #e2e8f0', borderRadius: 3, fontSize: 12 }}
                          />
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>

          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn-sm btn-primary" onClick={handleSave}>Save & Apply</button>
            <button className="btn-sm" onClick={handleReset}>Reset to defaults</button>
          </div>
        </div>
      </div>
    </>
  )
}
