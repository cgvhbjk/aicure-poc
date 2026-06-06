import { v4 as uuidv4 } from 'uuid'

const NEWS_SOURCES = [
  'Fierce Pharma', 'Endpoints News', 'PharmaVoice',
  'TrialSite News', 'BioPharma Dive', 'STAT News', 'BioSpace',
  'Google News — GLP-1', 'Google News — Semaglutide', 'Google News — Tirzepatide',
  'Google News — Obesity trial', 'Google News — Weight loss', 'Google News — T2D trial',
  'Google News — Heart failure', 'Google News — A-fib trial',
  'Google News — First patient', 'Google News — IND filing',
]

export const NEWS_FILTER_FIELDS = [
  { key: 'q',                 label: 'Search',             type: 'text'    },
  { key: 'source',            label: 'Source',             type: 'select',  options: NEWS_SOURCES },
  { key: 'published_at',      label: 'Published Date',     type: 'date'    },
  { key: 'drug_mentioned',    label: 'Drug Mentioned',     type: 'text'    },
  { key: 'phase_mentioned',   label: 'Phase Mentioned',    type: 'text'    },
  { key: 'sponsor_mentioned', label: 'Sponsor Mentioned',  type: 'text'    },
  { key: 'linked_only',       label: 'Linked to Trial',    type: 'boolean', hint: 'Show only articles linked to a trial.' },
  { key: 'is_announcement',   label: 'Trial Announcement', type: 'boolean', hint: 'Show only trial announcement articles (★).' },
  { key: 'is_results',        label: 'Trial Results',      type: 'boolean', hint: 'Show only trial results/findings articles (●).' },
]

export const FUNDING_FILTER_FIELDS = [
  { key: 'q',               label: 'Search',           type: 'text'    },
  {
    key: 'source', label: 'Source', type: 'select',
    options: ['NIH_REPORTER', 'USASPENDING', 'PCORI', 'CORDIS', 'UKRI', 'AHA', 'ADA'],
    displayFn: (v) => v.replace(/_/g, ' '),
  },
  { key: 'therapeutic_area', label: 'Therapeutic Area', type: 'select',
    options: ['Metabolic / GLP-1', 'Diabetes', 'Cardiovascular', 'Adherence / Outcomes', 'Other'],
  },
  { key: 'status',    label: 'Status',      type: 'select',  options: ['ACTIVE', 'COMPLETED', 'UNKNOWN'] },
  { key: 'country',   label: 'Country',     type: 'text'    },
  { key: 'award_date',label: 'Award Date',  type: 'date'    },
  { key: 'amount_usd',label: 'Amount (USD)',type: 'number'  },
  { key: 'has_trial_link', label: 'Has Trial Link', type: 'boolean', hint: 'Show only grants linked to a trial.' },
]

export const FILTER_FIELDS = [
  {
    key: 'status', label: 'Status', type: 'select',
    options: ['RECRUITING', 'NOT_YET_RECRUITING', 'ACTIVE_NOT_RECRUITING', 'COMPLETED', 'TERMINATED', 'WITHDRAWN', 'SUSPENDED'],
    displayFn: (v) => v.replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, c => c.toUpperCase()),
  },
  {
    key: 'phase', label: 'Phase', type: 'select',
    options: ['PHASE1', 'PHASE2', 'PHASE3', 'PHASE4', 'EARLY_PHASE1'],
    displayFn: (v) => v.replace('PHASE', 'Phase ').replace('EARLY_', 'Early '),
  },
  { key: 'therapeutic_area', label: 'Therapeutic Area', type: 'select', dynamic: 'therapeutic_areas' },
  { key: 'country', label: 'Country', type: 'select', dynamic: 'countries' },
  {
    key: 'registry', label: 'Registry', type: 'select',
    options: ['ClinicalTrials.gov', 'CTIS', 'ISRCTN', 'CRIS', 'ANZCTR', 'DRKS', 'jRCT', 'NTR', 'ChiCTR', 'CTRI', 'IRCT', 'ReBec', 'PACTR'],
  },
  { key: 'has_news', label: 'Has News', type: 'boolean', hint: 'Filters to trials that have linked news articles.' },
  { key: 'enrollment', label: 'Enrollment', type: 'number' },
  { key: 'start_date', label: 'Start Date', type: 'date' },
  { key: 'primary_completion', label: 'Completion Date', type: 'date' },
  { key: 'sponsor', label: 'Sponsor', type: 'text' },
  { key: 'q', label: 'Any text', type: 'text' },
]

export const OPERATORS_FOR_TYPE = {
  select:  [{ value: 'is', label: 'is' }, { value: 'is_not', label: 'is not' }],
  text:    [{ value: 'contains', label: 'contains' }, { value: 'not_contains', label: 'does not contain' }, { value: 'is', label: 'is exactly' }],
  number:  [
    { value: 'gte', label: '≥' },
    { value: 'lte', label: '≤' },
    { value: 'gt',  label: '>' },
    { value: 'lt',  label: '<' },
    { value: 'eq',  label: '=' },
  ],
  date:    [{ value: 'after', label: 'after' }, { value: 'before', label: 'before' }, { value: 'on', label: 'on' }],
  boolean: [{ value: 'is_true', label: 'is checked' }, { value: 'is_false', label: 'is not checked' }],
}

export function makeCondition(field, operator, value) {
  return { id: uuidv4(), field, operator, value }
}

// Compact pill label. Examples:
//   "Status: Recruiting"
//   "Status: Recruiting +2"
//   "Sponsor ∋ novo"        (∋ = contains)
//   "Enrollment ≥ 500"
//   "Start after 2024-01-01"
//   "Has news"
//   "Search: obesity"
export function formatConditionLabel(condition, fields = FILTER_FIELDS) {
  const fieldDef = fields.find(f => f.key === condition.field)
  const fieldLabel = fieldDef?.label ?? condition.field

  if (fieldDef?.type === 'boolean') {
    return condition.operator === 'is_false' ? `not ${fieldLabel}` : fieldLabel
  }

  if (fieldDef?.type === 'select') {
    const arr = Array.isArray(condition.value) ? condition.value : (condition.value != null ? [condition.value] : [])
    const head = arr[0] != null ? (fieldDef.displayFn ? fieldDef.displayFn(arr[0]) : arr[0]) : ''
    const more = arr.length > 1 ? ` +${arr.length - 1}` : ''
    const not = condition.operator === 'is_not' ? '≠ ' : ''
    return `${fieldLabel}: ${not}${head}${more}`
  }

  if (fieldDef?.type === 'text') {
    const op = condition.operator
    const v = Array.isArray(condition.value) ? condition.value[0] : condition.value
    if (condition.field === 'q') return `Search: ${v}`
    if (op === 'contains') return `${fieldLabel} ∋ ${v}`
    if (op === 'not_contains') return `${fieldLabel} ∌ ${v}`
    return `${fieldLabel}: ${v}`
  }

  if (fieldDef?.type === 'number') {
    const ops = { gte: '≥', lte: '≤', gt: '>', lt: '<', eq: '=' }
    return `${fieldLabel} ${ops[condition.operator] || '='} ${condition.value}`
  }

  if (fieldDef?.type === 'date') {
    const shortLabel = fieldLabel.replace(' Date', '')
    return `${shortLabel} ${condition.operator} ${condition.value}`
  }

  return `${fieldLabel}: ${condition.value}`
}

// ── Compile helpers ───────────────────────────────────────────────────────────
// Each helper mutates `apiParams` based on one condition. Shared across the
// trial / news / funding compilers so operator semantics stay consistent.

function hasValue(v) {
  if (v == null) return false
  if (Array.isArray(v)) return v.length > 0
  if (typeof v === 'string') return v.trim() !== ''
  return true
}

function singleValue(v) {
  return Array.isArray(v) ? v[0] : v
}

function applyMultiSelect(apiParams, c, apiKey) {
  const vals = Array.isArray(c.value) ? c.value : c.value != null ? [c.value] : []
  if (c.operator === 'is' && vals.length) {
    apiParams[apiKey] = [...(apiParams[apiKey] || []), ...vals]
  }
}

// Text fields with a contains / not_contains backend split.
// `not_contains` routes to an api param suffixed with `_not` which the
// backend reads as "exclude rows matching this substring".
function applyTextLike(apiParams, c, apiKey) {
  const v = singleValue(c.value)
  if (!hasValue(v)) return
  if (c.operator === 'not_contains') apiParams[`${apiKey}_not`] = v
  else apiParams[apiKey] = v
}

function applyDateRange(apiParams, c, fromKey, toKey) {
  if (!hasValue(c.value)) return
  if (c.operator === 'after')      apiParams[fromKey] = c.value
  else if (c.operator === 'before') apiParams[toKey]   = c.value
  else if (c.operator === 'on')   { apiParams[fromKey] = c.value; apiParams[toKey] = c.value }
}

function applyNumberRange(apiParams, c, minKey, maxKey) {
  if (!hasValue(c.value)) return
  const n = Number(c.value)
  if (isNaN(n)) return
  if (c.operator === 'gte')     apiParams[minKey] = n
  else if (c.operator === 'lte') apiParams[maxKey] = n
  else if (c.operator === 'gt')  apiParams[minKey] = n + 1
  else if (c.operator === 'lt')  apiParams[maxKey] = n - 1
  else if (c.operator === 'eq') { apiParams[minKey] = n; apiParams[maxKey] = n }
}

function applyBoolean(apiParams, c, apiKey) {
  apiParams[apiKey] = c.operator !== 'is_false'
}

// ── Compilers ─────────────────────────────────────────────────────────────────

export function compileConditions(conditions) {
  const apiParams = {}
  const agGridFilters = {}

  for (const c of conditions) {
    switch (c.field) {
      case 'status':            applyMultiSelect(apiParams, c, 'status'); break
      case 'phase':             applyMultiSelect(apiParams, c, 'phase'); break
      case 'therapeutic_area':  applyMultiSelect(apiParams, c, 'therapeutic_area'); break
      case 'country':           applyMultiSelect(apiParams, c, 'country'); break
      case 'registry':          applyMultiSelect(apiParams, c, 'registry'); break

      case 'has_news':          applyBoolean(apiParams, c, 'has_news'); break

      case 'enrollment':        applyNumberRange(apiParams, c, 'min_enrollment', 'max_enrollment'); break

      case 'start_date':         applyDateRange(apiParams, c, 'start_date_from', 'start_date_to'); break
      case 'primary_completion': applyDateRange(apiParams, c, 'completion_date_from', 'completion_date_to'); break

      case 'q':
        if (hasValue(c.value)) apiParams.q = singleValue(c.value)
        break

      case 'sponsor': {
        const v = singleValue(c.value)
        if (!hasValue(v)) break
        const agType = c.operator === 'is' ? 'equals'
          : c.operator === 'not_contains' ? 'notContains'
          : 'contains'
        agGridFilters['sponsor'] = { filterType: 'text', type: agType, filter: v }
        break
      }

      default:
        break
    }
  }

  return { apiParams, agGridFilters }
}

export function compileNewsConditions(conditions) {
  const apiParams = {}
  for (const c of conditions) {
    switch (c.field) {
      case 'q':
        if (hasValue(c.value)) apiParams.q = singleValue(c.value)
        break
      case 'source':            applyMultiSelect(apiParams, c, 'source'); break
      case 'published_at':      applyDateRange(apiParams, c, 'published_at_from', 'published_at_to'); break
      case 'drug_mentioned':    applyTextLike(apiParams, c, 'drug_mentioned'); break
      case 'phase_mentioned':   applyTextLike(apiParams, c, 'phase_mentioned'); break
      case 'sponsor_mentioned': applyTextLike(apiParams, c, 'sponsor_mentioned'); break
      case 'linked_only':       applyBoolean(apiParams, c, 'linked_only'); break
      case 'is_announcement':   applyBoolean(apiParams, c, 'is_trial_announcement'); break
      case 'is_results':        applyBoolean(apiParams, c, 'is_trial_results'); break
      default: break
    }
  }
  return { apiParams }
}

export function compileFundingConditions(conditions) {
  const apiParams = {}
  for (const c of conditions) {
    switch (c.field) {
      case 'q':
        if (hasValue(c.value)) apiParams.q = singleValue(c.value)
        break
      case 'source':           applyMultiSelect(apiParams, c, 'source'); break
      case 'therapeutic_area': applyMultiSelect(apiParams, c, 'therapeutic_area'); break
      case 'status':           applyMultiSelect(apiParams, c, 'status'); break
      case 'country':          applyTextLike(apiParams, c, 'country_q'); break
      case 'award_date':       applyDateRange(apiParams, c, 'award_date_from', 'award_date_to'); break
      case 'amount_usd':       applyNumberRange(apiParams, c, 'min_amount', 'max_amount'); break
      case 'has_trial_link':   applyBoolean(apiParams, c, 'has_trial_link'); break
      default: break
    }
  }
  return { apiParams }
}

// Stable fingerprint of conditions + grid state for "view is modified" detection.
export function fingerprint(obj) {
  if (obj == null) return ''
  if (Array.isArray(obj)) return '[' + obj.map(fingerprint).join(',') + ']'
  if (typeof obj === 'object') {
    return '{' + Object.keys(obj).sort().map(k => `${k}:${fingerprint(obj[k])}`).join(',') + '}'
  }
  return String(obj)
}
