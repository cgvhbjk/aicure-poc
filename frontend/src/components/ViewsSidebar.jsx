import React, { useState, useEffect, useRef, useMemo } from 'react'
import { v4 as uuidv4 } from 'uuid'

const VIEW_COLORS = [
  '#4f46e5', '#0891b2', '#16a34a', '#d97706',
  '#dc2626', '#7c3aed', '#0d9488', '#db2777',
]

const LS_NEW = 'aicure_saved_views_v2'
const LS_OLD = 'aicure_saved_views'
const LS_ACTIVE = 'aicure_active_view_id'
const LS_SESSION = 'aicure_session_state'  // last-applied state for default Grid

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
        conditions: v.conditions || [],
        createdAt: new Date().toISOString(),
      }))
      localStorage.setItem(LS_NEW, JSON.stringify(migrated))
      localStorage.removeItem(LS_OLD)
      return migrated
    } catch {}
  }
  try { return JSON.parse(localStorage.getItem(LS_NEW) || '[]') } catch { return [] }
}

function loadActiveId() {
  return localStorage.getItem(LS_ACTIVE) || 'default'
}

function loadSession() {
  try { return JSON.parse(localStorage.getItem(LS_SESSION) || 'null') } catch { return null }
}

export default function ViewsSidebar({
  gridApiRef, getCurrentConditions, onApplyConditions,
  conditions, gridStateBump,
}) {
  const [views, setViews] = useState([])
  const [activeId, setActiveIdState] = useState('default')
  const [creatingNew, setCreatingNew] = useState(false)
  const [newName, setNewName] = useState('')
  const [renamingId, setRenamingId] = useState(null)
  const [renameVal, setRenameVal] = useState('')
  const [menuId, setMenuId] = useState(null)
  const newInputRef = useRef(null)
  const restoringRef = useRef(false)
  const mountedRef = useRef(false)
  // Mirror of activeId/views for the auto-save effect to avoid stale closures.
  const viewsRef = useRef([])
  const activeIdRef = useRef('default')
  useEffect(() => { viewsRef.current = views }, [views])
  useEffect(() => { activeIdRef.current = activeId }, [activeId])

  const setActiveId = (id) => {
    setActiveIdState(id)
    try { localStorage.setItem(LS_ACTIVE, id) } catch {}
  }

  // Initial load + state restoration.
  useEffect(() => {
    const v = loadViews()
    setViews(v)
    const aid = loadActiveId()
    setActiveIdState(aid)

    // Defer restoration to next tick so the grid API is ready.
    setTimeout(() => {
      const found = v.find(x => x.id === aid)
      if (found) {
        restoringRef.current = true
        applyStateToGrid(found)
        onApplyConditions?.(found.conditions || [])
        setTimeout(() => { restoringRef.current = false; mountedRef.current = true }, 60)
      } else {
        // Default Grid — restore session state if present
        const sess = loadSession()
        if (sess) {
          restoringRef.current = true
          applyStateToGrid(sess)
          onApplyConditions?.(sess.conditions || [])
          setTimeout(() => { restoringRef.current = false; mountedRef.current = true }, 60)
        } else {
          mountedRef.current = true
        }
      }
    }, 50)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const close = (e) => { if (!e.target.closest?.('.view-menu-wrap')) setMenuId(null) }
    document.addEventListener('click', close)
    return () => document.removeEventListener('click', close)
  }, [])

  const persist = (next) => localStorage.setItem(LS_NEW, JSON.stringify(next))
  const getApi = () => { try { return gridApiRef?.current || null } catch { return null } }

  const captureState = () => {
    const a = getApi()
    let columnState = []
    let filterModel = {}
    try { columnState = a?.getColumnState() || [] } catch {}
    try { filterModel = a?.getFilterModel() || {} } catch {}
    return {
      columnState,
      filterModel,
      conditions: getCurrentConditions?.() || [],
    }
  }

  const applyStateToGrid = (snap) => {
    const a = getApi()
    try {
      if (snap.columnState?.length) a?.applyColumnState({ state: snap.columnState, applyOrder: true })
      else a?.resetColumnState()
      a?.setFilterModel(snap.filterModel || {})
    } catch {}
  }

  // Auto-save effect: whenever conditions or grid state change, persist either
  // to the active named view or to the session state (Default Grid).
  useEffect(() => {
    if (!mountedRef.current) return
    if (restoringRef.current) return
    const aid = activeIdRef.current
    const snap = captureState()
    if (aid === 'default') {
      try { localStorage.setItem(LS_SESSION, JSON.stringify(snap)) } catch {}
    } else {
      const next = viewsRef.current.map(v => v.id === aid ? { ...v, ...snap } : v)
      setViews(next)
      persist(next)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conditions, gridStateBump])

  const applyView = (view) => {
    setActiveId(view.id)
    restoringRef.current = true
    if (view.id === 'default') {
      const a = getApi()
      try { a?.resetColumnState(); a?.setFilterModel({}) } catch {}
      onApplyConditions?.([])
      try { localStorage.removeItem(LS_SESSION) } catch {}
    } else {
      applyStateToGrid(view)
      onApplyConditions?.(view.conditions || [])
    }
    setTimeout(() => { restoringRef.current = false }, 60)
  }

  const createView = () => {
    const name = newName.trim()
    if (!name) return
    const snap = captureState()
    const view = {
      id: uuidv4(),
      name,
      color: VIEW_COLORS[views.length % VIEW_COLORS.length],
      ...snap,
      createdAt: new Date().toISOString(),
    }
    const next = [...views, view]
    setViews(next)
    persist(next)
    setActiveId(view.id)
    setCreatingNew(false)
    setNewName('')
    // Once the named view exists, drop the Grid session so it doesn't shadow.
    try { localStorage.removeItem(LS_SESSION) } catch {}
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

  const sessionHasState = () => {
    const s = loadSession()
    if (!s) return false
    return (s.conditions?.length > 0) || Object.keys(s.filterModel || {}).length > 0 || (s.columnState?.length > 0)
  }

  // Indicator: only used on Default Grid to surface "you have unsaved scratch
  // state — save it as a named view." Named views auto-save, so they're never
  // dirty.
  const showGridSavePrompt = useMemo(() => {
    if (activeId !== 'default') return false
    return (conditions || []).length > 0 || sessionHasState()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId, conditions, gridStateBump])

  return (
    <div className="views-sidebar">
      <div className="views-sidebar-header">
        <span>Views</span>
        {showGridSavePrompt && (
          <button className="views-save-btn" onClick={startCreate} title="Save current state as a new view">
            Save as…
          </button>
        )}
      </div>

      <button className="views-new-btn" onClick={startCreate}>+ New view</button>

      {/* Default Grid view */}
      <div
        className={`view-row${activeId === 'default' ? ' active' : ''}`}
        onClick={() => applyView({ id: 'default' })}
      >
        <span className="view-dot" style={{ background: '#64748b' }} />
        <span className="view-row-name">Grid</span>
      </div>

      {/* User-saved views — auto-saved on edits */}
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
