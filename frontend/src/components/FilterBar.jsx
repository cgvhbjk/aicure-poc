import React, { useState } from 'react'
import { formatConditionLabel } from '../utils/conditions'
import ConditionBuilder from './ConditionBuilder'

export default function FilterBar({ conditions, onAdd, onEdit, onRemove, onClear, therapeuticAreas }) {
  const [builderOpen, setBuilderOpen] = useState(false)
  const [editingCondition, setEditingCondition] = useState(null)

  const handleApply = (condition) => {
    if (editingCondition) {
      onEdit(condition)
    } else {
      onAdd(condition)
    }
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
    <div className="filter-bar">
      {conditions.map(c => (
        <span
          key={c.id}
          className={`filter-pill${editingCondition?.id === c.id ? ' active' : ''}`}
          onClick={() => handlePillClick(c)}
        >
          {formatConditionLabel(c)}
          <button
            className="filter-pill-remove"
            onClick={(e) => { e.stopPropagation(); onRemove(c.id) }}
          >
            ×
          </button>
        </span>
      ))}

      <div style={{ position: 'relative' }}>
        <button className="btn-sm filter-add-btn" onClick={handleAddClick}>
          + Add filter
        </button>
        {builderOpen && (
          <ConditionBuilder
            initialCondition={editingCondition}
            onApply={handleApply}
            onCancel={handleCancel}
            therapeuticAreas={therapeuticAreas}
          />
        )}
      </div>

      {conditions.length > 0 && (
        <>
          <span className="filter-bar-sep" />
          <button className="btn-sm filter-clear-btn" onClick={onClear}>
            Clear all
          </button>
        </>
      )}
    </div>
  )
}
