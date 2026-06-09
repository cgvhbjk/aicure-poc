import React, { useState, useEffect, useRef, useCallback } from 'react'
import { AgGridReact } from 'ag-grid-react'
import { getNews } from '../api'
import FieldsPanel from './FieldsPanel'
import FilterBar from './FilterBar'
import { NEWS_FILTER_FIELDS } from '../utils/conditions'
import { attachGridStateListeners } from '../utils/gridEvents'

const STATUS_COLORS = {
  RECRUITING:             { background: '#dcfce7', color: '#166534' },
  NOT_YET_RECRUITING:     { background: '#dbeafe', color: '#1e40af' },
  ACTIVE_NOT_RECRUITING:  { background: '#fef9c3', color: '#854d0e' },
  COMPLETED:              { background: '#f1f5f9', color: '#475569' },
}

function TitleLink({ value, data }) {
  if (!value) return null
  return (
    <a
      href={data?.url}
      target="_blank"
      rel="noopener noreferrer"
      onClick={(e) => e.stopPropagation()}
    >
      {value}
    </a>
  )
}

function TrialLink({ value }) {
  if (!value) return <span style={{ color: '#cbd5e1' }}>—</span>
  return (
    <a
      href={`https://clinicaltrials.gov/study/${value}`}
      target="_blank"
      rel="noopener noreferrer"
      onClick={(e) => e.stopPropagation()}
    >
      {value}
    </a>
  )
}

function TrialStatusBadge({ value }) {
  if (!value) return <span style={{ color: '#cbd5e1' }}>—</span>
  const style = STATUS_COLORS[value] || { background: '#f1f5f9', color: '#475569' }
  return (
    <span className="badge" style={style}>
      {value.replace(/_/g, ' ')}
    </span>
  )
}

function NctListCell({ value }) {
  if (!value) return <span style={{ color: '#cbd5e1' }}>—</span>
  try {
    const arr = JSON.parse(value)
    if (!arr.length) return <span style={{ color: '#cbd5e1' }}>—</span>
    return (
      <span>
        {arr.map((nct) => (
          <a
            key={nct}
            href={`https://clinicaltrials.gov/study/${nct}`}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            style={{ marginRight: 6 }}
          >
            {nct}
          </a>
        ))}
      </span>
    )
  } catch {
    return <span style={{ color: '#cbd5e1' }}>—</span>
  }
}

const COLUMN_DEFS = [
  {
    field: 'is_trial_announcement',
    headerName: 'Type',
    width: 58,
    minWidth: 58,
    maxWidth: 58,
    cellRenderer: ({ data }) => {
      if (data?.is_trial_announcement) return <span style={{ color: '#f59e0b', fontSize: 15, fontWeight: 700 }} title="New trial announcement">★</span>
      if (data?.is_trial_results)      return <span style={{ color: '#60a5fa', fontSize: 13, fontWeight: 700 }} title="Trial results / findings">●</span>
      return null
    },
    sortable: true,
    filter: false,
    resizable: false,
  },
  { field: 'source', headerName: 'Source', width: 150 },
  {
    field: 'title',
    headerName: 'Title',
    width: 340,
    cellRenderer: TitleLink,
    tooltipField: 'body_snippet',
    tooltipComponentParams: { type: 'snippet' },
  },
  {
    field: 'body_snippet',
    headerName: 'Snippet',
    width: 260,
    tooltipField: 'body_snippet',
    valueFormatter: (p) => p.value ? p.value.slice(0, 120) + (p.value.length > 120 ? '…' : '') : '',
  },
  {
    field: 'published_at',
    headerName: 'Published',
    width: 100,
    valueFormatter: (p) => p.value?.slice(0, 10) ?? '',
  },
  { field: 'drug_mentioned', headerName: 'Drug', width: 120 },
  { field: 'phase_mentioned', headerName: 'Phase', width: 80 },
  { field: 'sponsor_mentioned', headerName: 'Sponsor', width: 150 },
  {
    field: 'nct_ids_found',
    headerName: 'NCTs in Article',
    width: 150,
    cellRenderer: NctListCell,
    filter: false,
  },
  { field: 'trial_id', headerName: 'Linked NCT', width: 120, cellRenderer: TrialLink },
  { field: 'trial_title', headerName: 'Linked Trial Title', width: 240, tooltipField: 'trial_title' },
  { field: 'trial_status', headerName: 'Trial Status', width: 160, cellRenderer: TrialStatusBadge },
  { field: 'trial_phase', headerName: 'Trial Phase', width: 90, hide: true },
  { field: 'trial_therapeutic_area', headerName: 'Trial Area', width: 130 },
  { field: 'trial_sponsor', headerName: 'Trial Sponsor', width: 160, hide: true },
  {
    field: 'match_method',
    headerName: 'Match',
    width: 100,
    valueFormatter: (p) => p.value || '—',
  },
]

const DEFAULT_COL_DEF = {
  sortable: true,
  resizable: true,
  filter: true,
}

export default function NewsTable({
  filters,
  conditions,
  onAddCondition,
  onEditCondition,
  onRemoveCondition,
  onClearConditions,
  onGridReady: onGridReadyProp,
  onGridStateChange,
}) {
  const gridRef = useRef(null)
  const disposeGridListenersRef = useRef(null)
  const [rowData, setRowData] = useState([])
  const [loading, setLoading] = useState(false)
  const [total, setTotal] = useState(0)
  const [fieldsOpen, setFieldsOpen] = useState(false)

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const res = await getNews({ ...filters, page_size: 10000 })
      setRowData(res.data.results)
      setTotal(res.data.total)
    } catch (e) {
      console.error('Failed to fetch news:', e)
    } finally {
      setLoading(false)
    }
  }, [filters])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const handleGridReady = useCallback((params) => {
    onGridReadyProp?.(params.api)
    disposeGridListenersRef.current = attachGridStateListeners(params.api, onGridStateChange)
  }, [onGridReadyProp, onGridStateChange])

  useEffect(() => () => { disposeGridListenersRef.current?.() }, [])

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
          filterFields={NEWS_FILTER_FIELDS}
        />

        <span className="toolbar-sep" />
        <button className="btn-sm" onClick={() => gridRef.current?.api?.exportDataAsCsv()}>
          Export CSV
        </button>
        <span className="row-count">
          {loading ? 'Loading…' : `${total.toLocaleString()} items`}
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
    </div>
  )
}
