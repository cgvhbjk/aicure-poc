export const DEFAULT_SCORE_CONFIG = {
  weights: {
    digital_tech: 30,
    therapeutic_area: 25,
    phase: 20,
    status: 15,
    enrollment: 10,
  },
  area_scores: {
    'Metabolic/GLP-1': 25,
    'Obesity': 22,
    'Diabetes': 20,
    'Cardiovascular': 15,
    'Adherence/Outcomes': 12,
    'CNS': 10,
    'Oncology': 5,
    'Other': 2,
  },
  phase_scores: {
    PHASE3: 20,
    PHASE2_3: 18,
    PHASE2: 15,
    PHASE1_2: 10,
    PHASE1: 5,
    EARLY_PHASE1: 3,
    PHASE4: 12,
  },
  status_scores: {
    RECRUITING: 15,
    NOT_YET_RECRUITING: 12,
    ACTIVE_NOT_RECRUITING: 10,
    COMPLETED: 3,
    TERMINATED: 0,
    WITHDRAWN: 0,
    SUSPENDED: 1,
  },
}

export function loadScoreConfig() {
  try {
    const stored = localStorage.getItem('aicure_score_config')
    if (stored) return JSON.parse(stored)
  } catch {}
  return DEFAULT_SCORE_CONFIG
}

export function saveScoreConfig(cfg) {
  try { localStorage.setItem('aicure_score_config', JSON.stringify(cfg)) } catch {}
}

export function computeFitScore(trial, cfg) {
  if (!trial) return 0
  const c = cfg || loadScoreConfig()

  // Digital tech component (max = c.weights.digital_tech)
  const signals = ['epro_ecoa', 'digital_biomarkers', 'dct_elements']
  const truthy = signals.filter(k => trial[k] && trial[k] !== 'none' && trial[k] !== '[]' && trial[k] !== 'None')
  const digitalRatio = signals.length > 0 ? truthy.length / signals.length : 0
  const digitalPts = Math.round(c.weights.digital_tech * digitalRatio)

  // Therapeutic area component
  const areaMax = Math.max(...Object.values(c.area_scores), 1)
  const areaRaw = c.area_scores[trial.therapeutic_area] ?? 0
  const areaPts = Math.round(c.weights.therapeutic_area * (areaRaw / areaMax))

  // Phase component
  const phaseMax = Math.max(...Object.values(c.phase_scores), 1)
  const phaseRaw = c.phase_scores[trial.phase] ?? 0
  const phasePts = Math.round(c.weights.phase * (phaseRaw / phaseMax))

  // Status component
  const statusMax = Math.max(...Object.values(c.status_scores), 1)
  const statusRaw = c.status_scores[trial.status] ?? 0
  const statusPts = Math.round(c.weights.status * (statusRaw / statusMax))

  // Enrollment component — log scale: 10→1pt, 100→5pt, 1000→8pt, 10000→10pt
  const enroll = Number(trial.enrollment) || 0
  let enrollPts = 0
  if (enroll > 0) {
    const logScore = Math.log10(enroll) / Math.log10(10000)
    enrollPts = Math.round(c.weights.enrollment * Math.min(logScore, 1))
  }

  const total = digitalPts + areaPts + phasePts + statusPts + enrollPts
  return Math.max(0, Math.min(100, total))
}
