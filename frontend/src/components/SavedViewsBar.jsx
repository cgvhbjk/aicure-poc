import React, { useState } from 'react'

const STORAGE_KEY = 'aicure_saved_views'

function loadViews() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')
  } catch {
    return []
  }
}

export default function SavedViewsBar({ gridRef }) {
  const [views, setViews] = useState(loadViews)

  const persist = (updated) => {
    setViews(updated)
    localStorage.setItem(STORAGE_KEY, JSON.stringify(updated))
  }

  const saveCurrentView = () => {
    const name = window.prompt('Save view as:')
    if (!name?.trim()) return
    const api = gridRef.current?.api
    if (!api) return
    const columnState = api.getColumnState()
    const filterModel = api.getFilterModel()
    persist([...views, { name: name.trim(), columnState, filterModel }])
  }

  const restoreView = (view) => {
    const api = gridRef.current?.api
    if (!api) return
    api.applyColumnState({ state: view.columnState, applyOrder: true })
    api.setFilterModel(view.filterModel)
  }

  const deleteView = (idx, e) => {
    e.stopPropagation()
    persist(views.filter((_, i) => i !== idx))
  }

  return (
    <div className="saved-views-bar">
      {views.map((view, idx) => (
        <span key={idx} className="view-pill" onClick={() => restoreView(view)}>
          {view.name}
          <button className="pill-delete" onClick={(e) => deleteView(idx, e)}>
            ×
          </button>
        </span>
      ))}
      <button className="btn-sm" onClick={saveCurrentView}>
        + Save current view
      </button>
    </div>
  )
}
