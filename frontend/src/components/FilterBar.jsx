import React, { useState } from 'react'
import { formatConditionLabel } from '../utils/conditions'
import ConditionBuilder from './ConditionBuilder'

export default function FilterBar({ conditions, onAdd, onEdit, onRemove, onClear, therapeuticAreas, countries }) {
  const [builderOpen, setBuilderOpen] = useState(false)
  const [editingCondition, setEditingCondition] = useState(null)

  const handleApply = (condition) => {
    if (editingCondition) onEdit(condition)
    else onAdd(condition)
    setBuilderOpen(false)
    setEditingCondition(null)
  }

  const handleCancel = () => {
    setBuilderOpen(false)
    setEditingCondition(null)
  }

  const handlePillClick = (condition) => {
    setEditingCondition(condition)
    setBuilderOpen(true)
  }

  const handleAddClick = () => {
    setEditingCondition(null)
    setBuilderOpen(true)
  }

  return (
    <div className="filter-bar-inline">
      <div style={{ position: 'relative' }}>
        <button className="btn-sm filter-add-btn" onClick={handleAddClick} title="Add a filter">
          + Filter
        </button>
        {builderOpen && (
          <ConditionBuilder
            initialCondition={editingCondition}
            onApply={handleApply}
            onCancel={handleCancel}
            therapeuticAreas={therapeuticAreas}
            countries={countries}
          />
        )}
      </div>

      {conditions.map(c => (
        <span
          key={c.id}
          className={`filter-pill${editingCondition?.id === c.id ? ' active' : ''}`}
          onClick={() => handlePillClick(c)}
          title="Click to edit"
        >
          <span className="filter-pill-label">{formatConditionLabel(c)}</span>
          <button
            className="filter-pill-remove"
            onClick={(e) => { e.stopPropagation(); onRemove(c.id) }}
            title="Remove"
          >
            ×
          </button>
        </span>
      ))}

      {conditions.length > 0 && (
        <button className="btn-sm filter-clear-btn" onClick={onClear} title="Remove all filters">
          Clear
        </button>
      )}
    </div>
  )
}
