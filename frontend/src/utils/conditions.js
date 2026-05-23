import { v4 as uuidv4 } from 'uuid'

export const FILTER_FIELDS = [
  {
    key: 'status', label: 'Status', type: 'select',
    options: ['RECRUITING', 'NOT_YET_RECRUITING', 'ACTIVE_NOT_RECRUITING', 'COMPLETED', 'TERMINATED', 'WITHDRAWN', 'SUSPENDED'],
    displayFn: (v) => v.replace(/_/g, ' '),
  },
  {
    key: 'phase', label: 'Phase', type: 'select',
    options: ['PHASE1', 'PHASE2', 'PHASE3', 'PHASE4', 'EARLY_PHASE1'],
    displayFn: (v) => v.replace('PHASE', 'Phase ').replace('EARLY_', 'Early '),
  },
  { key: 'therapeutic_area', label: 'Therapeutic Area', type: 'select', dynamic: true },
  {
    key: 'registry', label: 'Registry', type: 'select',
    options: ['ClinicalTrials.gov', 'CTIS', 'EU-CTR', 'ISRCTN', 'NTR', 'ANZCTR', 'DRKS', 'jRCT', 'CRIS'],
  },
  { key: 'has_news', label: 'Has News', type: 'boolean' },
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
  date:    [{ value: 'after', label: 'is after' }, { value: 'before', label: 'is before' }, { value: 'on', label: 'is on' }],
  boolean: [{ value: 'is_true', label: 'is checked' }],
}

export function makeCondition(field, operator, value) {
  return { id: uuidv4(), field, operator, value }
}

export function formatConditionLabel(condition) {
  const fieldDef = FILTER_FIELDS.find(f => f.key === condition.field)
  const fieldLabel = fieldDef?.label ?? condition.field

  if (condition.field === 'has_news') return 'Has News'

  const ops = OPERATORS_FOR_TYPE[fieldDef?.type ?? 'text'] ?? []
  const opLabel = ops.find(o => o.value === condition.operator)?.label ?? condition.operator

  const rawVal = condition.value
  let valueLabel = ''
  if (Array.isArray(rawVal)) {
    valueLabel = rawVal.map(v => fieldDef?.displayFn ? fieldDef.displayFn(v) : v).join(', ')
  } else {
    valueLabel = fieldDef?.displayFn ? fieldDef.displayFn(String(rawVal ?? '')) : String(rawVal ?? '')
  }

  return `${fieldLabel} ${opLabel} ${valueLabel}`
}

export function compileConditions(conditions) {
  const apiParams = {}
  const agGridFilters = {}

  for (const c of conditions) {
    const vals = Array.isArray(c.value) ? c.value : c.value != null ? [c.value] : []

    switch (c.field) {
      case 'status':
        if (c.operator === 'is' && vals.length) {
          apiParams.status = [...(apiParams.status || []), ...vals]
        }
        break

      case 'phase':
        if (c.operator === 'is' && vals.length) {
          apiParams.phase = [...(apiParams.phase || []), ...vals]
        }
        break

      case 'therapeutic_area':
        if (c.operator === 'is' && vals.length) {
          apiParams.therapeutic_area = [...(apiParams.therapeutic_area || []), ...vals]
        }
        break

      case 'registry':
        if (c.operator === 'is' && vals.length) {
          apiParams.registry = [...(apiParams.registry || []), ...vals]
        }
        break

      case 'has_news':
        apiParams.has_news = true
        break

      case 'enrollment': {
        const n = Number(c.value)
        if (!isNaN(n)) {
          if (c.operator === 'gte') apiParams.min_enrollment = n
          else if (c.operator === 'lte') apiParams.max_enrollment = n
          else if (c.operator === 'gt')  apiParams.min_enrollment = n + 1
          else if (c.operator === 'lt')  apiParams.max_enrollment = n - 1
          else if (c.operator === 'eq') {
            apiParams.min_enrollment = n
            apiParams.max_enrollment = n
          }
        }
        break
      }

      case 'start_date':
        if (c.operator === 'after')       apiParams.start_date_from = c.value
        else if (c.operator === 'before') apiParams.start_date_to   = c.value
        else if (c.operator === 'on') {
          apiParams.start_date_from = c.value
          apiParams.start_date_to   = c.value
        }
        break

      case 'primary_completion':
        if (c.operator === 'after')       apiParams.completion_date_from = c.value
        else if (c.operator === 'before') apiParams.completion_date_to   = c.value
        else if (c.operator === 'on') {
          apiParams.completion_date_from = c.value
          apiParams.completion_date_to   = c.value
        }
        break

      case 'q':
        apiParams.q = Array.isArray(c.value) ? c.value[0] : c.value
        break

      case 'sponsor': {
        const agType = c.operator === 'is' ? 'equals'
          : c.operator === 'not_contains' ? 'notContains'
          : 'contains'
        agGridFilters['sponsor'] = { filterType: 'text', type: agType, filter: Array.isArray(c.value) ? c.value[0] : c.value }
        break
      }

      default:
        break
    }
  }

  return { apiParams, agGridFilters }
}
