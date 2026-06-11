import React, { useState, useRef, useMemo } from 'react'
import { AgGridReact } from 'ag-grid-react'
import { getOrgs } from '../api'
import OrgDetailPanel from './OrgDetailPanel'

// Columns the backend can ORDER BY (mirrors ORG_SORTABLE_COLUMNS in api.py).
const SORTABLE_FIELDS = new Set(['canonical_name', 'org_type', 'trial_count'])

const ORG_TYPE_STYLES = {
  PHARMA:         { background: '#dbeafe', color: '#1e40af' },
  BIOTECH:        { background: '#ede9fe', color: '#5b21b6' },
  CRO:            { background: '#f3e8ff', color: '#7c3aed' },
  DCT_VENDOR:     { background: '#ccfbf1', color: '#0f766e' },
  DIGITAL_HEALTH: { background: '#cffafe', color: '#0e7490' },
  RPM:            { background: '#dcfce7', color: '#15803d' },
  TELEHEALTH:     { background: '#d1fae5', color: '#065f46' },
  ACADEMIC:       { background: '#ffedd5', color: '#9a3412' },
  GOVERNMENT:     { background: '#fef3c7', color: '#92400e' },
  OTHER:          { background: '#f1f5f9', color: '#475569' },
}

function OrgTypeBadge({ value }) {
  if (!value) return null
  const style = ORG_TYPE_STYLES[value] || ORG_TYPE_STYLES.OTHER
  return <span className="badge" style={style}>{value.replace(/_/g, ' ')}</span>
}

function LinkCell({ value }) {
  if (!value) return null
  const display = value.replace(/^https?:\/\//, '').split('/')[0]
  return (
    <a href={value} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>
      {display}
    </a>
  )
}

function JsonArrayCell({ value }) {
  if (!value) return null
  try {
    const arr = JSON.parse(value)
    if (!Array.isArray(arr) || !arr.length) return null
    return <span title={arr.join(', ')}>{arr.join(', ')}</span>
  } catch {
    return <span>{String(value)}</span>
  }
}

function TruncatedText({ value }) {
  if (!value) return null
  const s = String(value)
  return <span title={s}>{s.slice(0, 120)}{s.length > 120 ? '…' : ''}</span>
}

// filter: false — column header filters are client-side and only see loaded
// rows under the infinite row model; the org search/type filters in the
// sidebar are server-side and cover filtering.
const BASE = { sortable: true, resizable: true, filter: false }

const COLUMN_DEFS = [
  { ...BASE, field: 'canonical_name',   headerName: 'Organization',   width: 220, hide: false },
  { ...BASE, field: 'org_type',         headerName: 'Type',           width: 130, hide: false, cellRenderer: OrgTypeBadge },
  { ...BASE, field: 'therapeutic_focus',headerName: 'Focus Areas',    width: 200, hide: false, cellRenderer: JsonArrayCell },
  { ...BASE, field: 'trial_count',      headerName: 'Trials',         width: 80,  hide: false, type: 'numericColumn' },
  { ...BASE, field: 'website',          headerName: 'Website',        width: 180, hide: true,  cellRenderer: LinkCell },
  { ...BASE, field: 'linkedin_url',     headerName: 'LinkedIn',       width: 160, hide: true,  cellRenderer: LinkCell },
  { ...BASE, field: 'notes',            headerName: 'Notes',          width: 260, hide: true,  cellRenderer: TruncatedText },
].map(c => ({ ...c, sortable: SORTABLE_FIELDS.has(c.field) }))

const DEFAULT_COL_DEF = { sortable: true, resizable: true, filter: false }

export default function OrgsTable({ filters, filterOpen, onToggleFilter, onSelectTrial }) {
  const gridRef = useRef(null)
  // Only the latest in-flight getRows response may write the header total
  // (see FundingTable for the rationale).
  const reqSeqRef = useRef(0)
  const [loading, setLoading] = useState(false)
  const [total, setTotal] = useState(0)
  const [selectedOrg, setSelectedOrg] = useState(null)

  // Server-side pagination via AG Grid's infinite row model; sorting is
  // pushed to the API (sort/dir params).
  const datasource = useMemo(() => ({
    getRows: async (params) => {
      const pageSize = params.endRow - params.startRow
      const page = Math.floor(params.startRow / pageSize) + 1
      const sm = params.sortModel && params.sortModel[0]
      const sort = sm ? sm.colId : 'trial_count'
      const dir = sm ? sm.sort : 'desc'
      const reqId = ++reqSeqRef.current
      setLoading(true)
      try {
        const res = await getOrgs({ ...filters, page, page_size: pageSize, sort, dir })
        const { results, total: totalCount } = res.data
        if (reqId === reqSeqRef.current) setTotal(totalCount)
        params.successCallback(results, totalCount)
      } catch (e) {
        console.error('Failed to fetch orgs:', e)
        params.failCallback()
      } finally {
        if (reqId === reqSeqRef.current) setLoading(false)
      }
    },
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [JSON.stringify(filters)])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      <div className="toolbar">
        <button
          className={`btn-sm${filterOpen ? ' btn-active' : ''}`}
          onClick={onToggleFilter}
        >
          Filter
        </button>
        <span className="toolbar-sep" />
        <span className="row-count">
          {loading ? 'Loading…' : `${total.toLocaleString()} organizations`}
        </span>
      </div>

      <div style={{ flex: 1, minHeight: 0 }}>
        <div className="ag-theme-alpine" style={{ height: '100%', width: '100%' }}>
          <AgGridReact
            ref={gridRef}
            columnDefs={COLUMN_DEFS}
            defaultColDef={DEFAULT_COL_DEF}
            rowModelType="infinite"
            datasource={datasource}
            cacheBlockSize={100}
            pagination
            paginationPageSize={100}
            enableCellTextSelection
            rowSelection="single"
            onRowClicked={(e) => { if (e.data) setSelectedOrg(e.data) }}
            animateRows
          />
        </div>
      </div>

      {selectedOrg && (
        <OrgDetailPanel
          org={selectedOrg}
          onClose={() => setSelectedOrg(null)}
          onSelectTrial={onSelectTrial}
          onOrgUpdated={(updated) => {
            setSelectedOrg(updated)
            // Infinite row model rows are owned by the block cache, not local
            // state — refetch the loaded blocks to show the edit.
            gridRef.current?.api?.refreshInfiniteCache()
          }}
        />
      )}
    </div>
  )
}
