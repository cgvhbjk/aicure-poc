export const DEFAULT_SCORE_CONFIG = {
  weights: {
    // Area alignment is the strongest predictor of a real AiCure opportunity;
    // digital tech signals matter but are secondary.
    therapeutic_area: 30,
    digital_tech: 25,
    phase: 20,
    status: 15,
    enrollment: 10,
  },
  area_scores: {
    // AiCure's core proven market: oral adherence monitoring for CNS conditions.
    'CNS': 30,
    'Psychiatry': 30,
    'Neurology': 25,
    'Substance Abuse': 22,
    // Emerging: oral chemo adherence (OncoBay partnership).
    'Oncology': 20,
    'Adherence/Outcomes': 18,
    // Moderate fit: oral-only agents benefit; GLP-1 injectables do not.
    'Metabolic / GLP-1': 15,
    'Diabetes': 15,
    // Lower fit: mostly device/injectable therapies.
    'Cardiovascular': 8,
    'Other': 5,
  },
  phase_scores: {
    PHASE3: 20,
    PHASE2_3: 19,
    PHASE2: 17,       // sweet spot for AiCure engagement
    PHASE1_2: 10,
    PHASE1: 5,
    EARLY_PHASE1: 3,
    PHASE4: 10,       // post-approval; lower commercial priority
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

// ── Grant fit score ───────────────────────────────────────────────────────────

export const DEFAULT_GRANT_SCORE_CONFIG = {
  weights: {
    therapeutic_area: 35,  // most predictive of AiCure opportunity
    source: 30,            // funder alignment (PCORI/NIH > broad federal > disease societies)
    status: 20,            // active grant = actionable; completed = historical
    amount: 15,            // larger grants = more budget for tools
  },
  area_scores: {
    'CNS': 30,
    'Psychiatry': 30,
    'Neurology': 25,
    'Substance Abuse': 22,
    'Oncology': 20,
    'Adherence/Outcomes': 18,
    'Metabolic / GLP-1': 15,
    'Diabetes': 15,
    'Cardiovascular': 8,
    'Other': 5,
  },
  source_scores: {
    // PCORI funds patient-outcome research — adherence monitoring fits perfectly
    PCORI: 30,
    // NIH is broad but high scientific rigor; NCATS/NIMH/NIDA grants are ideal
    NIH_REPORTER: 28,
    // Broad US federal spending; variable fit
    USASPENDING: 15,
    // Disease-society grants: moderate fit for oral-adherence conditions
    ADA: 12,
    // EU programme; growing digital-health emphasis
    CORDIS: 10,
    // UK research; some digital-health focus
    UKRI: 8,
    // Cardiovascular focus; lower fit for AiCure
    AHA: 6,
  },
  status_scores: {
    ACTIVE: 20,
    COMPLETED: 5,
  },
}

export function loadGrantScoreConfig() {
  try {
    const stored = localStorage.getItem('aicure_grant_score_config')
    if (stored) return JSON.parse(stored)
  } catch {}
  return DEFAULT_GRANT_SCORE_CONFIG
}

export function saveGrantScoreConfig(cfg) {
  try { localStorage.setItem('aicure_grant_score_config', JSON.stringify(cfg)) } catch {}
}

export function computeGrantFitScore(grant, cfg) {
  if (!grant) return 0
  const c = cfg || loadGrantScoreConfig()

  const areaMax = Math.max(...Object.values(c.area_scores), 1)
  const areaRaw = c.area_scores[grant.therapeutic_area] ?? 0
  const areaPts = Math.round(c.weights.therapeutic_area * (areaRaw / areaMax))

  const sourceMax = Math.max(...Object.values(c.source_scores), 1)
  const sourceRaw = c.source_scores[grant.source] ?? 0
  const sourcePts = Math.round(c.weights.source * (sourceRaw / sourceMax))

  const statusMax = Math.max(...Object.values(c.status_scores), 1)
  const statusRaw = c.status_scores[grant.status] ?? 0
  const statusPts = Math.round(c.weights.status * (statusRaw / statusMax))

  // Log scale: $100K→~11pts, $1M→~13pts, $10M→15pts
  const amount = Number(grant.amount_usd) || 0
  let amountPts = 0
  if (amount > 0) {
    const logScore = Math.log10(amount) / Math.log10(10_000_000)
    amountPts = Math.round(c.weights.amount * Math.min(logScore, 1))
  }

  return Math.max(0, Math.min(100, areaPts + sourcePts + statusPts + amountPts))
}
