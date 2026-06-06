import React, { useState, useEffect, useRef, useCallback } from 'react'
import { AgGridReact } from 'ag-grid-react'
import { getTrials } from '../api'
import DetailPanel from './DetailPanel'
import FieldsPanel from './FieldsPanel'
import FilterBar from './FilterBar'
import { attachGridStateListeners } from '../utils/gridEvents'

// ── Cell renderers ────────────────────────────────────────────────────────────

const STATUS_STYLES = {
  RECRUITING:             { background: '#dcfce7', color: '#166534' },
  NOT_YET_RECRUITING:     { background: '#dbeafe', color: '#1e40af' },
  ACTIVE_NOT_RECRUITING:  { background: '#fef9c3', color: '#854d0e' },
  COMPLETED:              { background: '#f1f5f9', color: '#475569' },
}
const PHASE_STYLES = {
  PHASE1: { background: '#ede9fe', color: '#7c3aed' },
  PHASE2: { background: '#dbeafe', color: '#1d4ed8' },
  PHASE3: { background: '#dcfce7', color: '#16a34a' },
  PHASE4: { background: '#ccfbf1', color: '#0f766e' },
}
const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']

function StatusBadge({ value }) {
  if (!value) return null
  const style = STATUS_STYLES[value] || { background: '#f1f5f9', color: '#475569' }
  return <span className="badge" style={style}>{value.replace(/_/g, ' ')}</span>
}

function PhaseBadge({ value }) {
  if (!value) return null
  const style = PHASE_STYLES[value] || { background: '#f1f5f9', color: '#475569' }
  return <span className="badge" style={style}>{value.replace('PHASE', 'Phase ')}</span>
}

function YesNoBadge({ value }) {
  const yes = value === 1 || value === true || value === 'Yes' || value === 'YES'
  return (
    <span className="badge" style={yes
      ? { background: '#dcfce7', color: '#166534' }
      : { background: '#f1f5f9', color: '#94a3b8' }}>
      {yes ? 'Yes' : 'No'}
    </span>
  )
}

function NewsDot({ value }) {
  return value ? <span style={{ color: '#16a34a', fontSize: 16, lineHeight: 1 }}>●</span> : null
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
  if (!value) return null
  return (
    <a href={value} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}
      title={value}>
      {value.replace(/^https?:\/\//, '').slice(0, 50)}
    </a>
  )
}

function JsonArrayCell({ value }) {
  if (!value) return null
  try {
    const arr = JSON.parse(value)
    const items = Array.isArray(arr) ? arr.filter(Boolean) : [String(value)]
    return <span title={items.join(', ')}>{items.join(', ')}</span>
  } catch {
    return <span>{String(value)}</span>
  }
}

function CountriesCell({ value }) {
  if (!value) return null
  try {
    const arr = JSON.parse(value).filter(Boolean)
    if (!arr.length) return null
    if (arr.length <= 3) return <span>{arr.join(', ')}</span>
    return <span title={arr.join(', ')}>{arr.slice(0, 3).join(', ')} +{arr.length - 3} more</span>
  } catch {
    return <span>{String(value)}</span>
  }
}

function TruncatedText({ value }) {
  if (!value) return null
  const short = String(value).slice(0, 120)
  return <span title={value}>{short}{value.length > 120 ? '…' : ''}</span>
}

function EnrollmentCell({ value }) {
  if (value == null || value === '') return <span style={{ color: '#cbd5e1' }}>—</span>
  return <span>{Number(value).toLocaleString()}</span>
}

function DateCell({ value }) {
  if (!value) return <span style={{ color: '#cbd5e1' }}>—</span>
  try {
    const d = new Date(value)
    if (isNaN(d.getTime())) return <span>{String(value).slice(0, 10)}</span>
    return <span>{MONTHS[d.getUTCMonth()]} {d.getUTCFullYear()}</span>
  } catch {
    return <span>{String(value).slice(0, 10)}</span>
  }
}

const REGISTRY_PILL_STYLES = {
  'ClinicalTrials.gov': { background: '#dbeafe', color: '#1e40af' },
  'CTIS':               { background: '#ede9fe', color: '#6d28d9' },
  'EU-CTR':             { background: '#fef3c7', color: '#92400e' },
  'ISRCTN':             { background: '#dcfce7', color: '#166534' },
  'CRIS':               { background: '#fdf4ff', color: '#7e22ce' },
}

function RegistryIdLink({ value, baseUrl }) {
  if (!value) return null
  return (
    <a href={`${baseUrl}${value}`} target="_blank" rel="noopener noreferrer"
      onClick={(e) => e.stopPropagation()}>
      {value}
    </a>
  )
}

function IsrctnLink({ value }) {
  return <RegistryIdLink value={value} baseUrl="https://www.isrctn.com/" />
}

function CrisLink({ value }) {
  if (!value) return null
  return (
    <a href={`https://cris.nih.go.kr/cris/search/detailSearch.do?seq=${value}&locale=en`}
      target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>
      {value}
    </a>
  )
}

function AnzctrLink({ value }) {
  return <RegistryIdLink value={value} baseUrl="https://www.anzctr.org.au/Trial/Registration/TrialReview.aspx?id=" />
}

function DrksLink({ value }) {
  if (!value) return null
  return (
    <a href={`https://www.drks.de/drks_web/navigate.do?navigationId=trial.HTML&TRIAL_ID=${value}`}
      target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>
      {value}
    </a>
  )
}

function JrctLink({ value }) {
  return <RegistryIdLink value={value} baseUrl="https://jrct.niph.go.jp/en-detail/" />
}

function RegistryPillsCell({ value }) {
  if (!value) return null
  try {
    const arr = JSON.parse(value)
    if (!Array.isArray(arr) || !arr.length) return null
    return (
      <span style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
        {arr.map((reg) => {
          const style = REGISTRY_PILL_STYLES[reg] || { background: '#f1f5f9', color: '#475569' }
          return (
            <span key={reg} className="badge" style={style}>
              {reg === 'ClinicalTrials.gov' ? 'CT.gov' : reg}
            </span>
          )
        })}
      </span>
    )
  } catch {
    return <span>{String(value)}</span>
  }
}

// ── Column definitions ────────────────────────────────────────────────────────

const BASE = { sortable: true, resizable: true, filter: true }

function FitScoreCell({ value }) {
  if (value == null || value === '') return null
  const color = value >= 70 ? '#16a34a' : value >= 40 ? '#d97706' : '#dc2626'
  return <span style={{ fontWeight: 700, color, fontVariantNumeric: 'tabular-nums' }}>{value}</span>
}

const COLUMN_DEFS = [
  { ...BASE, field: 'has_news',            headerName: '📰',                 width: 48,  hide: false, cellRenderer: NewsDot,       filter: false, resizable: false, maxWidth: 48 },
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
  { ...BASE, field: 'therapeutic_area',    headerName: 'Area',               width: 150, hide: false },
  { ...BASE, field: 'title_brief',         headerName: 'Trial Title',        width: 320, hide: false, tooltipField: 'brief_summary' },
  { ...BASE, field: 'status',              headerName: 'Status',             width: 140, hide: false, cellRenderer: StatusBadge },
  { ...BASE, field: 'phase',               headerName: 'Phase',              width: 90,  hide: false, cellRenderer: PhaseBadge },
  { ...BASE, field: 'sponsor',             headerName: 'Sponsor',            width: 200, hide: false },
  { ...BASE, field: 'sponsor_type',        headerName: 'Sponsor Type',       width: 120, hide: false },
  { ...BASE, field: 'lead_country',        headerName: 'Country',            width: 110, hide: false },
  { ...BASE, field: 'enrollment',          headerName: 'Enroll.',            width: 90,  hide: false, cellRenderer: EnrollmentCell, type: 'numericColumn' },
  { ...BASE, field: 'start_date',          headerName: 'Start',              width: 100, hide: false, cellRenderer: DateCell },
  { ...BASE, field: 'primary_completion',  headerName: 'Primary End',        width: 110, hide: false, cellRenderer: DateCell },
  { ...BASE, field: 'id',                  headerName: 'NCT ID',             width: 130, hide: false, cellRenderer: NctLink },
  { ...BASE, field: 'interventions',       headerName: 'Interventions',      width: 220, hide: false, cellRenderer: JsonArrayCell },
  { ...BASE, field: 'conditions',          headerName: 'Conditions',         width: 220, hide: false, cellRenderer: JsonArrayCell },
  // Hidden by default
  { ...BASE, field: 'study_completion',    headerName: 'Study End',          width: 110, hide: true,  cellRenderer: DateCell },
  { ...BASE, field: 'first_posted',        headerName: 'First Posted',       width: 110, hide: true,  cellRenderer: DateCell },
  { ...BASE, field: 'last_updated',        headerName: 'Last Updated',       width: 110, hide: true,  cellRenderer: DateCell },
  { ...BASE, field: 'title_official',      headerName: 'Official Title',     width: 360, hide: true },
  { ...BASE, field: 'study_type',          headerName: 'Study Type',         width: 130, hide: true },
  { ...BASE, field: 'randomized',          headerName: 'Randomized',         width: 110, hide: true,  cellRenderer: YesNoBadge },
  { ...BASE, field: 'masking',             headerName: 'Masking',            width: 130, hide: true },
  { ...BASE, field: 'num_arms',            headerName: 'Arms',               width: 70,  hide: true,  type: 'numericColumn' },
  { ...BASE, field: 'cro_named',           headerName: 'CRO',                width: 180, hide: true },
  { ...BASE, field: 'pi_name',             headerName: 'PI Name',            width: 180, hide: true },
  { ...BASE, field: 'pi_email',            headerName: 'PI Email',           width: 200, hide: true },
  { ...BASE, field: 'countries',           headerName: 'All Countries',      width: 220, hide: true,  cellRenderer: CountriesCell },
  { ...BASE, field: 'num_sites',           headerName: 'Sites',              width: 70,  hide: true,  cellRenderer: EnrollmentCell, type: 'numericColumn' },
  { ...BASE, field: 'min_age',             headerName: 'Min Age',            width: 80,  hide: true },
  { ...BASE, field: 'max_age',             headerName: 'Max Age',            width: 80,  hide: true },
  { ...BASE, field: 'sex_eligibility',     headerName: 'Sex',                width: 90,  hide: true },
  { ...BASE, field: 'is_pediatric',        headerName: 'Pediatric',          width: 100, hide: true,  cellRenderer: YesNoBadge },
  { ...BASE, field: 'inclusion_criteria',  headerName: 'Inclusion',          width: 260, hide: true,  cellRenderer: TruncatedText },
  { ...BASE, field: 'exclusion_criteria',  headerName: 'Exclusion',          width: 260, hide: true,  cellRenderer: TruncatedText },
  { ...BASE, field: 'mesh_terms',          headerName: 'MeSH Terms',         width: 220, hide: true,  cellRenderer: JsonArrayCell },
  { ...BASE, field: 'primary_endpoints',   headerName: 'Primary Endpoints',  width: 260, hide: true,  cellRenderer: TruncatedText },
  { ...BASE, field: 'secondary_endpoints', headerName: 'Secondary Endpoints',width: 260, hide: true,  cellRenderer: TruncatedText },
  { ...BASE, field: 'epro_ecoa',           headerName: 'ePRO/eCOA',          width: 110, hide: true,  cellRenderer: YesNoBadge },
  { ...BASE, field: 'digital_biomarkers',  headerName: 'Digital Biomarkers', width: 140, hide: true,  cellRenderer: YesNoBadge },
  { ...BASE, field: 'dct_elements',        headerName: 'DCT Elements',       width: 120, hide: true,  cellRenderer: YesNoBadge },
  { ...BASE, field: 'brief_summary',       headerName: 'Summary',            width: 300, hide: true,  cellRenderer: TruncatedText },
  { ...BASE, field: 'registry_id',         headerName: 'Registry IDs',       width: 180, hide: true },
  { ...BASE, field: 'source_url',          headerName: 'Source URL',         width: 200, hide: true,  cellRenderer: SourceUrlCell },
  { ...BASE, field: 'registry_sources',    headerName: 'Registries',         width: 200, hide: false, cellRenderer: RegistryPillsCell, filter: false },
  { ...BASE, field: 'euct_id',             headerName: 'EUCT ID',            width: 160, hide: true },
  { ...BASE, field: 'eudract_number',      headerName: 'EudraCT No.',        width: 140, hide: true },
  { ...BASE, field: 'isrctn_id',           headerName: 'ISRCTN ID',          width: 140, hide: true,  cellRenderer: IsrctnLink },
  { ...BASE, field: 'cris_id',             headerName: 'CRIS ID',            width: 120, hide: true,  cellRenderer: CrisLink },
  { ...BASE, field: 'anzctr_id',           headerName: 'ANZCTR',             width: 150, hide: true,  cellRenderer: AnzctrLink },
  { ...BASE, field: 'drks_id',             headerName: 'DRKS',               width: 150, hide: true,  cellRenderer: DrksLink },
  { ...BASE, field: 'jrct_id',             headerName: 'jRCT',               width: 150, hide: true,  cellRenderer: JrctLink },
  { ...BASE, field: 'ntr_id',              headerName: 'NTR',                width: 130, hide: true },
  { ...BASE, field: 'chictr_id',           headerName: 'ChiCTR',             width: 150, hide: true },
  { ...BASE, field: 'ctri_id',             headerName: 'CTRI',               width: 150, hide: true },
  { ...BASE, field: 'irct_id',             headerName: 'IRCT',               width: 150, hide: true },
  { ...BASE, field: 'rebec_id',            headerName: 'ReBec',              width: 130, hide: true },
  { ...BASE, field: 'pactr_id',            headerName: 'PACTR',              width: 130, hide: true },
  { ...BASE, field: 'ingested_at',         headerName: 'Ingested',           width: 130, hide: true,  cellRenderer: DateCell },
]

const DEFAULT_COL_DEF = { sortable: true, resizable: true, filter: true }

// ── Component ─────────────────────────────────────────────────────────────────

export default function TrialsTable({
  filters, agGridFilters, onGridReady: onGridReadyProp, onGridStateChange,
  conditions, onAddCondition, onEditCondition, onRemoveCondition, onClearConditions,
  therapeuticAreas, countries,
}) {
  const gridRef = useRef(null)
  const [rowData, setRowData] = useState([])
  const [loading, setLoading] = useState(false)
  const [total, setTotal] = useState(0)
  const [selectedTrial, setSelectedTrial] = useState(null)
  const [fieldsOpen, setFieldsOpen] = useState(false)

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const res = await getTrials({ ...filters, page_size: 10000 })
      setRowData(res.data.results)
      setTotal(res.data.total)
    } catch (e) {
      console.error('Failed to fetch trials:', e)
    } finally {
      setLoading(false)
    }
  }, [filters])

  useEffect(() => { fetchData() }, [fetchData])

  useEffect(() => {
    const api = gridRef.current?.api
    if (!api) return
    try { api.setFilterModel(agGridFilters || {}) } catch {}
  }, [agGridFilters])

  const handleGridReady = useCallback((params) => {
    onGridReadyProp?.(params.api)
    try { params.api.setFilterModel(agGridFilters || {}) } catch {}
    attachGridStateListeners(params.api, onGridStateChange)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onGridReadyProp, onGridStateChange])

  const onExport = () => gridRef.current?.api?.exportDataAsCsv()
  const onRowClicked = (e) => setSelectedTrial(e.data)

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
          therapeuticAreas={therapeuticAreas}
          countries={countries}
        />

        <span className="toolbar-sep" />
        <button className="btn-sm" onClick={onExport}>Export CSV</button>
        <span className="row-count">
          {loading ? 'Loading…' : `${total.toLocaleString()} trials`}
        </span>
      </div>

      <div style={{ flex: 1, minHeight: 0, position: 'relative', display: 'flex' }}>
        <div className="ag-theme-alpine" style={{ flex: 1, minHeight: 0 }}>
          <AgGridReact
            ref={gridRef}
            rowData={rowData}
            columnDefs={COLUMN_DEFS}
            defaultColDef={DEFAULT_COL_DEF}
            onGridReady={handleGridReady}
            pagination
            paginationPageSize={100}
            enableCellTextSelection
            rowSelection="single"
            onRowClicked={onRowClicked}
            tooltipShowDelay={500}
            animateRows
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

      {selectedTrial && (
        <DetailPanel trial={selectedTrial} onClose={() => setSelectedTrial(null)} />
      )}
    </div>
  )
}
