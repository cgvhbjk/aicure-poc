import React, { useState, useCallback, useEffect, useRef } from 'react'

export default function FieldsPanel({ gridRef, columnDefs, onClose }) {
  const [cols, setCols] = useState([])
  const [search, setSearch] = useState('')
  const dragId = useRef(null)
  const saveTimer = useRef(null)

  const getApi = () => gridRef?.current?.api

  const syncFromGrid = useCallback(() => {
    const a = getApi()
    if (!a) return
    const state = a.getColumnState()
    const nameMap = {}
    columnDefs.forEach(c => { if (c.field) nameMap[c.field] = c.headerName || c.field })
    setCols(state.map(s => ({
      colId: s.colId,
      headerName: nameMap[s.colId] || s.colId,
      hide: s.hide ?? false,
    })))
  }, [columnDefs, gridRef])

  useEffect(() => { syncFromGrid() }, [syncFromGrid])

  const applyState = useCallback((nextCols) => {
    const a = getApi()
    if (!a) return
    clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => {
      const state = a.getColumnState()
      const stateMap = {}
      state.forEach(s => { stateMap[s.colId] = s })
      const newState = nextCols.map(c => ({ ...(stateMap[c.colId] || {}), colId: c.colId, hide: c.hide }))
      a.applyColumnState({ state: newState, applyOrder: true })
    }, 100)
  }, [gridRef])

  const toggleVisible = useCallback((colId) => {
    setCols(prev => {
      const next = prev.map(c => c.colId === colId ? { ...c, hide: !c.hide } : c)
      const a = getApi()
      if (a) {
        const col = next.find(c => c.colId === colId)
        a.setColumnsVisible([colId], !col.hide)
      }
      return next
    })
  }, [gridRef])

  const filtered = search
    ? cols.filter(c => c.headerName.toLowerCase().includes(search.toLowerCase()))
    : cols

  const showAll = useCallback(() => {
    const ids = filtered.map(c => c.colId)
    setCols(prev => prev.map(c => ids.includes(c.colId) ? { ...c, hide: false } : c))
    const a = getApi()
    if (a) a.setColumnsVisible(ids, true)
  }, [filtered, gridRef])

  const hideAll = useCallback(() => {
    const ids = filtered.map(c => c.colId)
    setCols(prev => prev.map(c => ids.includes(c.colId) ? { ...c, hide: true } : c))
    const a = getApi()
    if (a) a.setColumnsVisible(ids, false)
  }, [filtered, gridRef])

  const onDragStart = (e, colId) => {
    dragId.current = colId
    e.dataTransfer.effectAllowed = 'move'
  }

  const onDragOver = (e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move' }

  const onDrop = useCallback((e, targetId) => {
    e.preventDefault()
    const fromId = dragId.current
    if (!fromId || fromId === targetId) return
    setCols(prev => {
      const next = [...prev]
      const fi = next.findIndex(c => c.colId === fromId)
      const ti = next.findIndex(c => c.colId === targetId)
      if (fi < 0 || ti < 0) return prev
      const [moved] = next.splice(fi, 1)
      next.splice(ti, 0, moved)
      applyState(next)
      return next
    })
  }, [applyState])

  return (
    <div className="fields-panel">
      <div className="fields-panel-header">
        <span>Fields</span>
        <button className="fields-close" onClick={onClose}>×</button>
      </div>

      <div className="fields-search-wrap">
        <input
          className="search-input"
          placeholder="Search fields…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      <div className="fields-actions">
        <button className="btn-sm" onClick={showAll}>Show all</button>
        <button className="btn-sm" onClick={hideAll}>Hide all</button>
      </div>

      <div className="fields-list">
        {filtered.map((col) => (
          <div
            key={col.colId}
            className="field-item"
            draggable
            onDragStart={(e) => onDragStart(e, col.colId)}
            onDragOver={onDragOver}
            onDrop={(e) => onDrop(e, col.colId)}
          >
            <span className="field-drag-handle">⠿</span>
            <input
              type="checkbox"
              checked={!col.hide}
              onChange={() => toggleVisible(col.colId)}
              style={{ accentColor: '#2563eb', cursor: 'pointer', flexShrink: 0 }}
            />
            <span className="field-name">{col.headerName}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
