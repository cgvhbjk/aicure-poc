import React, { useState, useEffect, useRef, useCallback } from 'react'
import { AgGridReact } from 'ag-grid-react'
import { getNews } from '../api'

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

const COLUMN_DEFS = [
  {
    field: 'is_trial_announcement',
    headerName: '★',
    width: 44,
    minWidth: 44,
    maxWidth: 44,
    cellRenderer: ({ value }) =>
      value ? <span style={{ color: '#f59e0b', fontSize: 16 }}>★</span> : null,
    sortable: true,
    filter: false,
    resizable: false,
  },
  { field: 'source', headerName: 'Source', width: 160 },
  { field: 'title', headerName: 'Title', width: 360, cellRenderer: TitleLink, tooltipField: 'title' },
  {
    field: 'published_at',
    headerName: 'Published',
    width: 110,
    valueFormatter: (p) => p.value?.slice(0, 10) ?? '',
  },
  { field: 'drug_mentioned', headerName: 'Drug', width: 130 },
  { field: 'phase_mentioned', headerName: 'Phase', width: 90 },
  { field: 'sponsor_mentioned', headerName: 'Sponsor', width: 160 },
  { field: 'trial_id', headerName: 'Linked Trial', width: 130, cellRenderer: TrialLink },
  {
    field: 'match_method',
    headerName: 'Match',
    width: 110,
    valueFormatter: (p) => p.value || '—',
  },
]

const DEFAULT_COL_DEF = {
  sortable: true,
  resizable: true,
  filter: true,
}

export default function NewsTable({ filters }) {
  const gridRef = useRef(null)
  const [rowData, setRowData] = useState([])
  const [loading, setLoading] = useState(false)
  const [total, setTotal] = useState(0)

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const params = { ...filters, page_size: 500 }
      if (params.linked_only === null) delete params.linked_only
      const res = await getNews(params)
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

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      <div className="toolbar">
        <button
          className="btn-sm"
          onClick={() => gridRef.current?.api?.exportDataAsCsv()}
        >
          Export CSV
        </button>
        <span className="row-count">
          {loading ? 'Loading…' : `${total.toLocaleString()} items`}
        </span>
      </div>

      <div className="ag-theme-alpine" style={{ flex: 1, minHeight: 0 }}>
        <AgGridReact
          ref={gridRef}
          rowData={rowData}
          columnDefs={COLUMN_DEFS}
          defaultColDef={DEFAULT_COL_DEF}
          pagination
          paginationPageSize={100}
          enableCellTextSelection
          tooltipShowDelay={500}
          animateRows
        />
      </div>
    </div>
  )
}
