import React, { useState, useEffect, useRef, useCallback } from 'react'
import { AgGridReact } from 'ag-grid-react'
import { getTrials } from '../api'
import DetailPanel from './DetailPanel'
import SavedViewsBar from './SavedViewsBar'

const STATUS_STYLES = {
  RECRUITING: { background: '#dcfce7', color: '#166534' },
  NOT_YET_RECRUITING: { background: '#dbeafe', color: '#1e40af' },
  ACTIVE_NOT_RECRUITING: { background: '#fef9c3', color: '#854d0e' },
  COMPLETED: { background: '#f1f5f9', color: '#475569' },
}

function StatusBadge({ value }) {
  if (!value) return null
  const style = STATUS_STYLES[value] || { background: '#f1f5f9', color: '#475569' }
  return (
    <span
      className="badge"
      style={style}
    >
      {value.replace(/_/g, ' ')}
    </span>
  )
}

function NewsDot({ value }) {
  return value ? (
    <span style={{ color: '#16a34a', fontSize: 16, lineHeight: 1 }}>●</span>
  ) : null
}

function NctLink({ value }) {
  if (!value) return null
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

function JsonArrayCell({ value }) {
  if (!value) return null
  try {
    const arr = JSON.parse(value)
    return <span>{Array.isArray(arr) ? arr.join(', ') : String(value)}</span>
  } catch {
    return <span>{String(value)}</span>
  }
}

const COLUMN_DEFS = [
  {
    field: 'has_news',
    headerName: '📰',
    width: 44,
    minWidth: 44,
    maxWidth: 44,
    cellRenderer: NewsDot,
    sortable: true,
    filter: false,
    resizable: false,
  },
  { field: 'therapeutic_area', headerName: 'Area', width: 140 },
  { field: 'title_brief', headerName: 'Trial Title', width: 300, tooltipField: 'title_brief' },
  { field: 'status', headerName: 'Status', width: 130, cellRenderer: StatusBadge },
  { field: 'phase', headerName: 'Phase', width: 80 },
  { field: 'sponsor', headerName: 'Sponsor', width: 180 },
  { field: 'lead_country', headerName: 'Country', width: 100 },
  { field: 'enrollment', headerName: 'Enroll.', width: 80, type: 'numericColumn' },
  { field: 'start_date', headerName: 'Start', width: 100 },
  { field: 'primary_completion', headerName: 'Primary End', width: 110 },
  { field: 'id', headerName: 'NCT ID', width: 120, cellRenderer: NctLink },
  {
    field: 'interventions',
    headerName: 'Intervention',
    width: 200,
    cellRenderer: JsonArrayCell,
  },
  {
    field: 'conditions',
    headerName: 'Conditions',
    width: 200,
    cellRenderer: JsonArrayCell,
  },
  { field: 'pi_name', headerName: 'PI', width: 160 },
  { field: 'last_updated', headerName: 'Updated', width: 100 },
]

const DEFAULT_COL_DEF = {
  sortable: true,
  resizable: true,
  filter: true,
}

export default function TrialsTable({ filters }) {
  const gridRef = useRef(null)
  const [rowData, setRowData] = useState([])
  const [loading, setLoading] = useState(false)
  const [total, setTotal] = useState(0)
  const [selectedTrial, setSelectedTrial] = useState(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const res = await getTrials({ ...filters, page_size: 500 })
      setRowData(res.data.results)
      setTotal(res.data.total)
    } catch (e) {
      console.error('Failed to fetch trials:', e)
    } finally {
      setLoading(false)
    }
  }, [filters])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const onExport = () => gridRef.current?.api?.exportDataAsCsv()
  const onColumnChooser = () => gridRef.current?.api?.showColumnChooser()
  const onRowClicked = (e) => setSelectedTrial(e.data)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      <SavedViewsBar gridRef={gridRef} />

      <div className="toolbar">
        <button className="btn-sm" onClick={onExport}>
          Export CSV
        </button>
        <button className="btn-sm" onClick={onColumnChooser}>
          Columns
        </button>
        <span className="row-count">
          {loading ? 'Loading…' : `${total.toLocaleString()} trials`}
        </span>
      </div>

      <div
        className="ag-theme-alpine"
        style={{ flex: 1, minHeight: 0 }}
      >
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
          tooltipShowDelay={500}
          animateRows
        />
      </div>

      {selectedTrial && (
        <DetailPanel
          trial={selectedTrial}
          onClose={() => setSelectedTrial(null)}
        />
      )}
    </div>
  )
}
