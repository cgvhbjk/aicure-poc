import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { AgGridReact } from 'ag-grid-react'
import { getGrants, apiBase } from '../api'
import GrantDetailPanel from './GrantDetailPanel'
import FieldsPanel from './FieldsPanel'
import FilterBar from './FilterBar'
import { FUNDING_FILTER_FIELDS } from '../utils/conditions'
import { attachGridStateListeners } from '../utils/gridEvents'
import { GRID_LOADING_TEMPLATE, GRID_EMPTY_TEMPLATE, gridErrorMessage } from '../utils/gridUi'
import { safeHref } from '../utils/url'

// Resolved API base for the direct-link export + filter-options fetch below
// (shared with api.js so it includes the /pipeline subpath in prod).
const _API_BASE = apiBase

// Columns the backend can ORDER BY (mirrors GRANT_SORTABLE_COLUMNS in api.py,
// plus the precomputed aicure_fit). Any column outside this set is display-only;
// marking it unsortable avoids showing a sort arrow the server would ignore.
const SORTABLE_FIELDS = new Set([
  'aicure_fit', 'amount_usd', 'award_date', 'start_date', 'end_date',
  'fiscal_year', 'title', 'status', 'source', 'organization', 'therapeutic_area',
  'sponsor_funder', 'agency_division', 'activity_code', 'org_type', 'country',
  'pi_name', 'has_trial_link',
])

// ── Cell renderers ────────────────────────────────────────────────────────────

const SOURCE_STYLES = {
  NIH_REPORTER: { background: '#dbeafe', color: '#1e40af' },
  USASPENDING:  { background: '#e0e7ff', color: '#3730a3' },
  PCORI:        { background: '#ccfbf1', color: '#0f766e' },
  CORDIS:       { background: '#dcfce7', color: '#166534' },
  UKRI:         { background: '#ede9fe', color: '#6d28d9' },
  AHA:          { background: '#fee2e2', color: '#991b1b' },
  ADA:          { background: '#ffedd5', color: '#9a3412' },
}

const ORG_TYPE_STYLES = {
  ACADEMIC:    { background: '#dbeafe', color: '#1e40af' },
  INDUSTRY:    { background: '#e0e7ff', color: '#3730a3' },
  NONPROFIT:   { background: '#ccfbf1', color: '#0f766e' },
  GOVERNMENT:  { background: '#f1f5f9', color: '#475569' },
  OTHER:       { background: '#f1f5f9', color: '#475569' },
}

function SourceBadge({ value }) {
  if (!value) return null
  const style = SOURCE_STYLES[value] || { background: '#f1f5f9', color: '#475569' }
  return <span className="badge" style={style}>{value.replace(/_/g, ' ')}</span>
}

function StatusBadge({ value }) {
  if (!value) return null
  const style = value === 'ACTIVE'
    ? { background: '#dcfce7', color: '#166534' }
    : { background: '#f1f5f9', color: '#475569' }
  return <span className="badge" style={style}>{value}</span>
}

function ActivityCodeBadge({ value }) {
  if (!value) return null
  return (
    <span className="badge" style={{
      background: '#f1f5f9', color: '#334155',
      fontFamily: 'monospace', fontSize: 11, letterSpacing: '0.02em',
    }}>{value}</span>
  )
}

function OrgTypeBadge({ value }) {
  if (!value) return null
  const style = ORG_TYPE_STYLES[value] || ORG_TYPE_STYLES.OTHER
  return <span className="badge" style={style}>{value}</span>
}

function TrialLinkDot({ value }) {
  return (
    <span style={{
      display: 'inline-block', width: 10, height: 10, borderRadius: '50%',
      background: value ? '#3b82f6' : '#e2e8f0',
    }} title={value ? 'Linked to trial' : 'No trial link'} />
  )
}

function AmountCell({ value }) {
  if (value == null || value === '') return <span style={{ color: '#cbd5e1' }}>—</span>
  const n = Number(value)
  if (isNaN(n)) return <span style={{ color: '#cbd5e1' }}>—</span>
  return <span style={{ textAlign: 'right', display: 'block' }}>${n.toLocaleString()}</span>
}

function OriginalAmountCell({ data }) {
  if (!data) return null
  const { amount_original, currency } = data
  if (amount_original == null) return <span style={{ color: '#cbd5e1' }}>—</span>
  const n = Number(amount_original)
  if (isNaN(n)) return <span style={{ color: '#cbd5e1' }}>—</span>
  const sym = currency === 'GBP' ? '£' : currency === 'EUR' ? '€' : '$'
  return (
    <span style={{ textAlign: 'right', display: 'block' }}>
      {sym}{n.toLocaleString()} {currency}
    </span>
  )
}

function PiEmailCell({ value }) {
  if (!value) return <span style={{ color: '#cbd5e1' }}>—</span>
  return <a href={`mailto:${value}`} onClick={(e) => e.stopPropagation()}>{value}</a>
}

function NctLink({ value }) {
  if (!value) return null
  return (
    <a href={`https://clinicaltrials.gov/study/${value}`} target="_blank" rel="noopener noreferrer"
      onClick={(e) => e.stopPropagation()}>
      {value}
    </a>
  )
}

function SourceUrlCell({ value }) {
  const href = safeHref(value)
  if (!href) return value ? <span title={value}>{String(value).slice(0, 50)}</span> : null
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}
      title={value}>
      {value.replace(/^https?:\/\//, '').slice(0, 50)}
    </a>
  )
}

function TruncatedText({ value }) {
  if (!value) return null
  const short = String(value).slice(0, 120)
  return <span title={value}>{short}{value.length > 120 ? '…' : ''}</span>
}

function JsonArrayCell({ value }) {
  if (!value) return null
  try {
    const arr = JSON.parse(value)
    const items = Array.isArray(arr) ? arr.filter(Boolean) : [String(value)]
    if (!items.length) return null
    return <span title={items.join(', ')}>{items.join(', ')}</span>
  } catch {
    return <span>{String(value)}</span>
  }
}

function DateCell({ value }) {
  if (!value) return <span style={{ color: '#cbd5e1' }}>—</span>
  return <span>{String(value).slice(0, 10)}</span>
}

function FitScoreCell({ value }) {
  if (value == null || value === '') return null
  const color = value >= 70 ? '#16a34a' : value >= 40 ? '#d97706' : '#dc2626'
  return <span style={{ fontWeight: 700, color, fontVariantNumeric: 'tabular-nums' }}>{value}</span>
}

// ── Column definitions ────────────────────────────────────────────────────────

const BASE = { sortable: true, resizable: true, filter: false }

const COLUMN_DEFS = [
  { ...BASE, field: 'has_trial_link',   headerName: '🔗',                  width: 48,  hide: false, cellRenderer: TrialLinkDot,        filter: false, resizable: false, maxWidth: 48 },
  {
    ...BASE,
    field: 'aicure_fit',
    headerName: 'Fit ★',
    width: 72,
    hide: false,
    cellStyle: { textAlign: 'right' },
    type: 'numericColumn',
    cellRenderer: FitScoreCell,
    filter: false,
  },
  { ...BASE, field: 'source',           headerName: 'Source',               width: 130, hide: false, cellRenderer: SourceBadge },
  { ...BASE, field: 'therapeutic_area', headerName: 'Area',                 width: 150, hide: false },
  { ...BASE, field: 'title',            headerName: 'Grant Title',          width: 320, hide: false },
  { ...BASE, field: 'status',           headerName: 'Status',               width: 110, hide: false, cellRenderer: StatusBadge },
  { ...BASE, field: 'sponsor_funder',   headerName: 'Funder',               width: 160, hide: false },
  { ...BASE, field: 'agency_division',  headerName: 'Division / Programme', width: 180, hide: false },
  { ...BASE, field: 'activity_code',    headerName: 'Award Type',           width: 110, hide: false, cellRenderer: ActivityCodeBadge },
  { ...BASE, field: 'organization',     headerName: 'Recipient',            width: 200, hide: false },
  { ...BASE, field: 'org_type',         headerName: 'Org Type',             width: 120, hide: false, cellRenderer: OrgTypeBadge },
  { ...BASE, field: 'pi_name',          headerName: 'PI',                   width: 160, hide: false },
  { ...BASE, field: 'amount_usd',       headerName: 'Amount (USD)',         width: 130, hide: false, cellRenderer: AmountCell, type: 'numericColumn' },
  { ...BASE, field: 'country',          headerName: 'Country',              width: 100, hide: false },
  { ...BASE, field: 'award_date',       headerName: 'Awarded',              width: 100, hide: false, cellRenderer: DateCell },
  // Hidden by default
  { ...BASE, field: 'fiscal_year',      headerName: 'Fiscal Year',          width: 100, hide: true },
  { ...BASE, field: 'start_date',       headerName: 'Start',                width: 100, hide: true,  cellRenderer: DateCell },
  { ...BASE, field: 'end_date',         headerName: 'End',                  width: 100, hide: true,  cellRenderer: DateCell },
  { ...BASE, field: 'project_acronym',  headerName: 'Acronym',              width: 110, hide: true },
  { ...BASE, field: 'research_type',    headerName: 'Research Type',        width: 160, hide: true },
  { ...BASE, field: 'conditions',       headerName: 'Conditions',           width: 220, hide: true,  cellRenderer: JsonArrayCell },
  { ...BASE, field: 'interventions',    headerName: 'Interventions',        width: 220, hide: true,  cellRenderer: JsonArrayCell },
  { ...BASE, field: 'phase_mentioned',  headerName: 'Phase',                width: 90,  hide: true },
  { ...BASE, field: 'pi_email',         headerName: 'PI Email',             width: 200, hide: true,  cellRenderer: PiEmailCell },
  { ...BASE, field: 'amount_original',  headerName: 'Orig. Amount',         width: 140, hide: true,  cellRenderer: OriginalAmountCell },
  { ...BASE, field: 'currency',         headerName: 'Currency',             width: 80,  hide: true },
  { ...BASE, field: 'linked_trial_id',  headerName: 'Linked Trial',         width: 130, hide: true,  cellRenderer: NctLink },
  { ...BASE, field: 'abstract',         headerName: 'Abstract',             width: 300, hide: true,  cellRenderer: TruncatedText },
  { ...BASE, field: 'source_url',       headerName: 'Source URL',           width: 200, hide: true,  cellRenderer: SourceUrlCell },
  { ...BASE, field: 'award_id',         headerName: 'Award ID',             width: 160, hide: true },
  { ...BASE, field: 'ingested_at',      headerName: 'Ingested',             width: 130, hide: true,  cellRenderer: DateCell },
].map(c => ({ ...c, sortable: SORTABLE_FIELDS.has(c.field) }))

const DEFAULT_COL_DEF = { sortable: true, resizable: true, filter: false }

function fmtMillions(n) {
  if (!n) return null
  if (n >= 1_000_000_000) return `$${(n / 1_000_000_000).toFixed(1)}B`
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `$${(n / 1_000).toFixed(0)}K`
  return `$${n.toLocaleString()}`
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function FundingTable({
  filters,
  onSelectTrial,
  conditions,
  onAddCondition,
  onEditCondition,
  onRemoveCondition,
  onClearConditions,
  onGridReady: onGridReadyProp,
  onGridStateChange,
}) {
  const gridRef = useRef(null)
  // Monotonic id of the most recent getRows call. The infinite row model can
  // have several block requests in flight at once (and a sort/filter change
  // purges + refetches), so responses can resolve out of order; only the latest
  // is allowed to write the header totals, preventing a stale response from
  // clobbering them with the wrong count/sum.
  const reqSeqRef = useRef(0)
  const disposeGridListenersRef = useRef(null)
  const [loading, setLoading] = useState(false)
  const [total, setTotal] = useState(0)
  const [totalFunding, setTotalFunding] = useState(0)
  // Last datasource error, shown in the toolbar so a failed fetch doesn't read
  // as an empty result ("No matching rows"); cleared on the next success.
  const [error, setError] = useState(null)
  const [selectedGrant, setSelectedGrant] = useState(null)
  const [fieldsOpen, setFieldsOpen] = useState(false)

  // Dynamic filter options from backend (org types / award types / research
  // types / agency divisions). Fed to the inline FilterBar's ConditionBuilder via
  // `dynamicOptions` so the single consolidated filter covers them too — the old
  // duplicate checkbox sidebar was removed.
  const [filterOptions, setFilterOptions] = useState({
    activity_codes: [], org_types: [], research_types: [], agency_divisions: [],
  })

  useEffect(() => {
    fetch(`${_API_BASE}/grants/filter-options`)
      .then((r) => r.json())
      .then(setFilterOptions)
      .catch(console.error)
  }, [])

  // All grant filtering now flows through the inline FilterBar (compiled in
  // App.jsx into `filters`).
  const apiFilters = { ...filters }

  // Server-side pagination via AG Grid's infinite row model: the grid pulls one
  // page at a time as the user pages through, instead of loading every grant up
  // front. Sorting — including the precomputed aicure_fit — is pushed to the API.
  const datasource = useMemo(() => ({
    getRows: async (params) => {
      const pageSize = params.endRow - params.startRow
      const page = Math.floor(params.startRow / pageSize) + 1
      const sm = params.sortModel && params.sortModel[0]
      const sort = sm ? sm.colId : 'aicure_fit'
      const dir = sm ? sm.sort : 'desc'
      const reqId = ++reqSeqRef.current
      setLoading(true)
      try {
        const res = await getGrants({ ...apiFilters, page, page_size: pageSize, sort, dir })
        const { results, total: totalCount, total_funding } = res.data
        // Only the latest request may update the shared header totals; an older
        // (slower) response must not overwrite a newer one's count/sum.
        if (reqId === reqSeqRef.current) {
          setTotal(totalCount)
          setTotalFunding(total_funding || 0)
          setError(null)
        }
        params.successCallback(results, totalCount)
      } catch (e) {
        console.error('Failed to fetch grants:', e)
        if (reqId === reqSeqRef.current) setError(gridErrorMessage(e))
        params.failCallback()
      } finally {
        if (reqId === reqSeqRef.current) setLoading(false)
      }
    },
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [JSON.stringify(apiFilters)])

  // Export the FULL filtered set via the backend (the infinite row model only
  // holds the loaded pages client-side, so api.exportDataAsCsv would miss rows).
  const onExport = () => {
    const sp = new URLSearchParams()
    Object.entries(apiFilters).forEach(([k, v]) => {
      if (v === undefined || v === null) return
      if (Array.isArray(v)) v.forEach((val) => sp.append(k, val))
      else sp.append(k, v)
    })
    const sorted = (gridRef.current?.api?.getColumnState?.() || []).find((c) => c.sort)
    sp.append('sort', sorted ? sorted.colId : 'aicure_fit')
    sp.append('dir', sorted ? sorted.sort : 'desc')
    const a = document.createElement('a')
    a.href = `${_API_BASE}/grants/export?${sp.toString()}`
    a.download = ''
    document.body.appendChild(a)
    a.click()
    a.remove()
  }
  const onRowClicked = (e) => setSelectedGrant(e.data)

  const handleGridReady = useCallback((params) => {
    onGridReadyProp?.(params.api)
    disposeGridListenersRef.current = attachGridStateListeners(params.api, onGridStateChange)
  }, [onGridReadyProp, onGridStateChange])

  // Detach the grid-state listeners on unmount (symmetric with attach above).
  useEffect(() => () => { disposeGridListenersRef.current?.() }, [])

  // Dynamic option arrays handed to the inline FilterBar (keys match the
  // `dynamic` names on FUNDING_FILTER_FIELDS).
  const dynamicOptions = {
    org_types: filterOptions.org_types.length
      ? filterOptions.org_types
      : ['ACADEMIC', 'INDUSTRY', 'NONPROFIT', 'GOVERNMENT', 'OTHER'],
    activity_codes: filterOptions.activity_codes || [],
    research_types: filterOptions.research_types || [],
    agency_divisions: filterOptions.agency_divisions || [],
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      <div className="toolbar toolbar-with-filters">
        <button
          className={`btn-sm${fieldsOpen ? ' btn-active' : ''}`}
          onClick={() => setFieldsOpen(v => !v)}
          title="Manage visible columns"
        >
          Fields
        </button>

        <FilterBar
          conditions={conditions}
          onAdd={onAddCondition}
          onEdit={onEditCondition}
          onRemove={onRemoveCondition}
          onClear={onClearConditions}
          filterFields={FUNDING_FILTER_FIELDS}
          dynamicOptions={dynamicOptions}
        />

        <span className="toolbar-sep" />
        <button className="btn-sm" onClick={onExport}>Export CSV</button>
        <span className="row-count">
          {loading ? 'Loading…'
            : error ? <span style={{ color: '#dc2626' }}>{error}</span>
            : (
            <>
              {total.toLocaleString()} grants
              {totalFunding > 0 && (
                <span
                  style={{ marginLeft: 6, color: '#16a34a', fontWeight: 600 }}
                  title="Sum of award amounts across the grants that disclose one — not every grant lists an amount"
                >
                  · {fmtMillions(totalFunding)} disclosed
                </span>
              )}
            </>
          )}
        </span>
      </div>

      <div style={{ flex: 1, minHeight: 0, position: 'relative', display: 'flex' }}>
        <div className="ag-theme-alpine" style={{ flex: 1, minHeight: 0 }}>
          <AgGridReact
            ref={gridRef}
            columnDefs={COLUMN_DEFS}
            defaultColDef={DEFAULT_COL_DEF}
            onGridReady={handleGridReady}
            rowModelType="infinite"
            datasource={datasource}
            cacheBlockSize={100}
            pagination
            paginationPageSize={100}
            enableCellTextSelection
            rowSelection="single"
            onRowClicked={onRowClicked}
            animateRows
            overlayLoadingTemplate={GRID_LOADING_TEMPLATE}
            overlayNoRowsTemplate={GRID_EMPTY_TEMPLATE}
          />
        </div>

        {fieldsOpen && (
          <FieldsPanel
            gridRef={gridRef}
            columnDefs={COLUMN_DEFS}
            onClose={() => setFieldsOpen(false)}
          />
        )}
      </div>

      {selectedGrant && (
        <GrantDetailPanel
          grant={selectedGrant}
          onClose={() => setSelectedGrant(null)}
          onSelectTrial={onSelectTrial}
        />
      )}
    </div>
  )
}
