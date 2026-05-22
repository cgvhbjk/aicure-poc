import React, { useState, useEffect, useRef } from 'react'
import { v4 as uuidv4 } from 'uuid'

const VIEW_COLORS = [
  '#4f46e5', '#0891b2', '#16a34a', '#d97706',
  '#dc2626', '#7c3aed', '#0d9488', '#db2777',
]

const LS_NEW = 'aicure_saved_views_v2'
const LS_OLD = 'aicure_saved_views'

function loadViews() {
  const oldRaw = localStorage.getItem(LS_OLD)
  if (oldRaw) {
    try {
      const old = JSON.parse(oldRaw)
      const migrated = old.map((v, i) => ({
        id: uuidv4(),
        name: v.name,
        color: VIEW_COLORS[i % VIEW_COLORS.length],
        columnState: v.columnState || [],
        filterModel: v.filterModel || {},
        createdAt: new Date().toISOString(),
      }))
      localStorage.setItem(LS_NEW, JSON.stringify(migrated))
      localStorage.removeItem(LS_OLD)
      return migrated
    } catch {}
  }
  try { return JSON.parse(localStorage.getItem(LS_NEW) || '[]') } catch { return [] }
}

export default function ViewsSidebar({ gridApiRef }) {
  const [views, setViews] = useState([])
  const [activeId, setActiveId] = useState('default')
  const [creatingNew, setCreatingNew] = useState(false)
  const [newName, setNewName] = useState('')
  const [renamingId, setRenamingId] = useState(null)
  const [renameVal, setRenameVal] = useState('')
  const [menuId, setMenuId] = useState(null)
  const newInputRef = useRef(null)

  useEffect(() => { setViews(loadViews()) }, [])

  useEffect(() => {
    const close = (e) => { if (!e.target.closest?.('.view-menu-wrap')) setMenuId(null) }
    document.addEventListener('click', close)
    return () => document.removeEventListener('click', close)
  }, [])

  // Plain helpers — always capture latest state/props from render closure
  const persist = (next) => localStorage.setItem(LS_NEW, JSON.stringify(next))
  const getApi = () => { try { return gridApiRef?.current || null } catch { return null } }

  const applyView = (view) => {
    setActiveId(view.id)
    const a = getApi()
    if (!a) return
    try {
      if (view.id === 'default') {
        a.resetColumnState()
        a.setFilterModel({})
      } else {
        if (view.columnState?.length) a.applyColumnState({ state: view.columnState, applyOrder: true })
        a.setFilterModel(view.filterModel || {})
      }
    } catch {}
  }

  const createView = () => {
    const name = newName.trim()
    if (!name) return
    const a = getApi()
    let columnState = []
    let filterModel = {}
    try { columnState = a?.getColumnState() || [] } catch {}
    try { filterModel = a?.getFilterModel() || {} } catch {}
    const view = {
      id: uuidv4(),
      name,
      color: VIEW_COLORS[views.length % VIEW_COLORS.length],
      columnState,
      filterModel,
      createdAt: new Date().toISOString(),
    }
    const next = [...views, view]
    setViews(next)
    persist(next)
    setActiveId(view.id)
    setCreatingNew(false)
    setNewName('')
  }

  const deleteView = (id) => {
    const next = views.filter(v => v.id !== id)
    setViews(next)
    persist(next)
    if (activeId === id) applyView({ id: 'default' })
    setMenuId(null)
  }

  const duplicateView = (view) => {
    const dup = {
      ...view,
      id: uuidv4(),
      name: `Copy of ${view.name}`,
      color: VIEW_COLORS[views.length % VIEW_COLORS.length],
      createdAt: new Date().toISOString(),
    }
    const next = [...views, dup]
    setViews(next)
    persist(next)
    setMenuId(null)
  }

  const saveToView = (id) => {
    const a = getApi()
    let columnState = []
    let filterModel = {}
    try { columnState = a?.getColumnState() || [] } catch {}
    try { filterModel = a?.getFilterModel() || {} } catch {}
    const next = views.map(v => v.id === id ? { ...v, columnState, filterModel } : v)
    setViews(next)
    persist(next)
    setMenuId(null)
  }

  const confirmRename = (id) => {
    const name = renameVal.trim()
    if (name) {
      const next = views.map(v => v.id === id ? { ...v, name } : v)
      setViews(next)
      persist(next)
    }
    setRenamingId(null)
    setRenameVal('')
  }

  const startCreate = () => {
    setCreatingNew(true)
    setNewName('')
    setTimeout(() => newInputRef.current?.focus(), 40)
  }

  return (
    <div className="views-sidebar">
      <div className="views-sidebar-header">Views</div>

      <button className="views-new-btn" onClick={startCreate}>+ New view</button>

      {/* Default Grid view */}
      <div
        className={`view-row${activeId === 'default' ? ' active' : ''}`}
        onClick={() => applyView({ id: 'default' })}
      >
        <span className="view-dot" style={{ background: '#64748b' }} />
        <span className="view-row-name">Grid</span>
      </div>

      {/* User-saved views */}
      {views.map((view) => (
        <div
          key={view.id}
          className={`view-row${activeId === view.id ? ' active' : ''}`}
          onClick={() => applyView(view)}
        >
          <span className="view-dot" style={{ background: view.color }} />

          {renamingId === view.id ? (
            <input
              className="view-inline-input"
              value={renameVal}
              onChange={(e) => setRenameVal(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') confirmRename(view.id)
                if (e.key === 'Escape') { setRenamingId(null); setRenameVal('') }
              }}
              onBlur={() => confirmRename(view.id)}
              autoFocus
              onClick={(e) => e.stopPropagation()}
            />
          ) : (
            <span className="view-row-name">{view.name}</span>
          )}

          <div className="view-menu-wrap" onClick={(e) => e.stopPropagation()}>
            <button
              className="view-menu-btn"
              onClick={() => setMenuId(menuId === view.id ? null : view.id)}
            >
              …
            </button>
            {menuId === view.id && (
              <div className="view-menu-dropdown">
                <button onClick={() => { setRenamingId(view.id); setRenameVal(view.name); setMenuId(null) }}>
                  Rename
                </button>
                <button onClick={() => saveToView(view.id)}>Save current state</button>
                <button onClick={() => duplicateView(view)}>Duplicate</button>
                <button className="view-menu-danger" onClick={() => deleteView(view.id)}>Delete</button>
              </div>
            )}
          </div>
        </div>
      ))}

      {/* New view input row */}
      {creatingNew && (
        <div className="view-row" onClick={(e) => e.stopPropagation()}>
          <span className="view-dot" style={{ background: VIEW_COLORS[views.length % VIEW_COLORS.length] }} />
          <input
            ref={newInputRef}
            className="view-inline-input"
            placeholder="View name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') createView()
              if (e.key === 'Escape') { setCreatingNew(false); setNewName('') }
            }}
          />
          <button className="view-confirm-btn" onClick={createView}>✓</button>
          <button className="view-cancel-btn" onClick={() => { setCreatingNew(false); setNewName('') }}>×</button>
        </div>
      )}
    </div>
  )
}
