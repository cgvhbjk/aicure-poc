import React, { useState, useEffect, useRef, useCallback } from 'react'
import { AgGridReact } from 'ag-grid-react'
import { getGrants } from '../api'
import GrantDetailPanel from './GrantDetailPanel'
import FieldsPanel from './FieldsPanel'

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

function TruncatedText({ value }) {
  if (!value) return null
  const short = String(value).slice(0, 120)
  return <span title={value}>{short}{value.length > 120 ? '…' : ''}</span>
}

function DateCell({ value }) {
  if (!value) return <span style={{ color: '#cbd5e1' }}>—</span>
  return <span>{String(value).slice(0, 10)}</span>
}

// ── Column definitions ────────────────────────────────────────────────────────

const BASE = { sortable: true, resizable: true, filter: true }

const COLUMN_DEFS = [
  { ...BASE, field: 'has_trial_link',  headerName: '🔗',              width: 48,  hide: false, cellRenderer: TrialLinkDot,  filter: false, resizable: false, maxWidth: 48 },
  { ...BASE, field: 'source',          headerName: 'Source',          width: 130, hide: false, cellRenderer: SourceBadge },
  { ...BASE, field: 'therapeutic_area',headerName: 'Area',            width: 150, hide: false },
  { ...BASE, field: 'title',           headerName: 'Grant Title',     width: 320, hide: false },
  { ...BASE, field: 'status',          headerName: 'Status',          width: 110, hide: false, cellRenderer: StatusBadge },
  { ...BASE, field: 'sponsor_funder',  headerName: 'Funder',          width: 160, hide: false },
  { ...BASE, field: 'organization',    headerName: 'Recipient',       width: 200, hide: false },
  { ...BASE, field: 'pi_name',         headerName: 'PI',              width: 160, hide: false },
  { ...BASE, field: 'amount_usd',      headerName: 'Amount (USD)',    width: 130, hide: false, cellRenderer: AmountCell, type: 'numericColumn' },
  { ...BASE, field: 'country',         headerName: 'Country',         width: 100, hide: false },
  { ...BASE, field: 'award_date',      headerName: 'Awarded',         width: 100, hide: false, cellRenderer: DateCell },
  // Hidden by default
  { ...BASE, field: 'start_date',      headerName: 'Start',           width: 100, hide: true,  cellRenderer: DateCell },
  { ...BASE, field: 'end_date',        headerName: 'End',             width: 100, hide: true,  cellRenderer: DateCell },
  { ...BASE, field: 'linked_trial_id', headerName: 'Linked Trial',    width: 130, hide: true,  cellRenderer: NctLink },
  { ...BASE, field: 'abstract',        headerName: 'Abstract',        width: 300, hide: true,  cellRenderer: TruncatedText },
  { ...BASE, field: 'source_url',      headerName: 'Source URL',      width: 200, hide: true,  cellRenderer: SourceUrlCell },
  { ...BASE, field: 'award_id',        headerName: 'Award ID',        width: 160, hide: true },
]

const DEFAULT_COL_DEF = { sortable: true, resizable: true, filter: true }

function fmtMillions(n) {
  if (!n) return null
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `$${(n / 1_000).toFixed(0)}K`
  return `$${n.toLocaleString()}`
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function FundingTable({ filters, onSelectTrial }) {
  const gridRef = useRef(null)
  const [rowData, setRowData] = useState([])
  const [loading, setLoading] = useState(false)
  const [total, setTotal] = useState(0)
  const [totalFunding, setTotalFunding] = useState(0)
  const [selectedGrant, setSelectedGrant] = useState(null)
  const [fieldsOpen, setFieldsOpen] = useState(false)
  const [filterOpen, setFilterOpen] = useState(false)

  // Filter state
  const [searchText, setSearchText] = useState('')
  const [selectedSources, setSelectedSources] = useState([])
  const [selectedAreas, setSelectedAreas] = useState([])
  const [selectedStatuses, setSelectedStatuses] = useState([])
  const [selectedCountries, setSelectedCountries] = useState([])
  const [hasTrialOnly, setHasTrialOnly] = useState(false)
  const [minAmount, setMinAmount] = useState('')
  const [maxAmount, setMaxAmount] = useState('')

  const toggle = (setFn) => (item) =>
    setFn((prev) => prev.includes(item) ? prev.filter(x => x !== item) : [...prev, item])

  const apiFilters = {
    q: searchText || undefined,
    source: selectedSources.length ? selectedSources : undefined,
    therapeutic_area: selectedAreas.length ? selectedAreas : undefined,
    status: selectedStatuses.length ? selectedStatuses : undefined,
    country: selectedCountries.length ? selectedCountries : undefined,
    has_trial_link: hasTrialOnly || undefined,
    min_amount: minAmount ? Number(minAmount) : undefined,
    max_amount: maxAmount ? Number(maxAmount) : undefined,
    ...filters,
  }

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const res = await getGrants({ ...apiFilters, page_size: 10000 })
      const results = res.data.results
      setRowData(results)
      setTotal(res.data.total)
      setTotalFunding(results.reduce((sum, g) => sum + (g.amount_usd || 0), 0))
    } catch (e) {
      console.error('Failed to fetch grants:', e)
    } finally {
      setLoading(false)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(apiFilters)])

  useEffect(() => { fetchData() }, [fetchData])

  const onExport = () => gridRef.current?.api?.exportDataAsCsv()
  const onRowClicked = (e) => setSelectedGrant(e.data)

  const SOURCES = ['NIH_REPORTER', 'USASPENDING', 'PCORI', 'CORDIS', 'UKRI', 'AHA', 'ADA']
  const AREAS = ['Metabolic / GLP-1', 'Diabetes', 'Cardiovascular', 'Adherence / Outcomes', 'Other']
  const STATUSES = ['ACTIVE', 'COMPLETED']

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
        <button
          className={`btn-sm${filterOpen ? ' btn-active' : ''}`}
          onClick={() => setFilterOpen(v => !v)}
        >
          Filter
        </button>
        <input
          className="search-input"
          type="text"
          placeholder="Search grants…"
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
          style={{ width: 200 }}
        />
        <span className="toolbar-sep" />
        <button className="btn-sm" onClick={onExport}>Export CSV</button>
        <span className="row-count">
          {loading ? 'Loading…' : (
            <>
              {total.toLocaleString()} grants
              {totalFunding > 0 && (
                <span style={{ marginLeft: 6, color: '#16a34a', fontWeight: 600 }}>
                  · {fmtMillions(totalFunding)} total
                </span>
              )}
            </>
          )}
        </span>
      </div>

      <div style={{ flex: 1, minHeight: 0, position: 'relative', display: 'flex' }}>
        {filterOpen && (
          <div className="filter-sidebar">
            <div className="filter-sidebar-inner">
              <div className="filter-group">
                <div className="filter-label">Source</div>
                {SOURCES.map((s) => (
                  <label key={s} className="checkbox-label">
                    <input type="checkbox" checked={selectedSources.includes(s)} onChange={() => toggle(setSelectedSources)(s)} />
                    {s.replace(/_/g, ' ')}
                  </label>
                ))}
              </div>
              <div className="filter-group">
                <div className="filter-label">Therapeutic Area</div>
                {AREAS.map((a) => (
                  <label key={a} className="checkbox-label">
                    <input type="checkbox" checked={selectedAreas.includes(a)} onChange={() => toggle(setSelectedAreas)(a)} />
                    {a}
                  </label>
                ))}
              </div>
              <div className="filter-group">
                <div className="filter-label">Status</div>
                {STATUSES.map((s) => (
                  <label key={s} className="checkbox-label">
                    <input type="checkbox" checked={selectedStatuses.includes(s)} onChange={() => toggle(setSelectedStatuses)(s)} />
                    {s}
                  </label>
                ))}
              </div>
              <div className="filter-group">
                <label className="checkbox-label">
                  <input type="checkbox" checked={hasTrialOnly} onChange={() => setHasTrialOnly(v => !v)} />
                  Has trial link only
                </label>
              </div>
              <div className="filter-group">
                <div className="filter-label">Amount (USD)</div>
                <input
                  className="search-input" type="number" placeholder="Min $"
                  value={minAmount} onChange={(e) => setMinAmount(e.target.value)}
                  style={{ width: '100%', marginBottom: 4 }}
                />
                <input
                  className="search-input" type="number" placeholder="Max $"
                  value={maxAmount} onChange={(e) => setMaxAmount(e.target.value)}
                  style={{ width: '100%' }}
                />
              </div>
              <button className="btn-clear" onClick={() => {
                setSelectedSources([]); setSelectedAreas([]); setSelectedStatuses([])
                setSelectedCountries([]); setHasTrialOnly(false); setMinAmount(''); setMaxAmount('')
              }}>
                Clear all filters
              </button>
            </div>
          </div>
        )}

        <div className="ag-theme-alpine" style={{ flex: 1, minHeight: 0 }}>
          <AgGridReact
            ref={gridRef}
            rowData={rowData}
            columnDefs={COLUMN_DEFS}
            defaultColDef={DEFAULT_COL_DEF}
            pagination
            paginationPageSize={100}
            enableCellTextSelection
            rowSelection="single"
            onRowClicked={onRowClicked}
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
