import React, { useState, useEffect, useRef } from 'react'
import { FILTER_FIELDS, OPERATORS_FOR_TYPE, makeCondition } from '../utils/conditions'

export default function ConditionBuilder({ initialCondition, onApply, onCancel, therapeuticAreas, countries, filterFields = FILTER_FIELDS, dynamicOptions = {} }) {
  const firstField = filterFields[0]
  const [fieldKey, setFieldKey] = useState(initialCondition?.field ?? firstField.key)
  const [operator, setOperator] = useState(initialCondition?.operator ?? '')
  const [value, setValue] = useState(initialCondition?.value ?? '')
  const wrapRef = useRef(null)

  const fieldDef = filterFields.find(f => f.key === fieldKey) ?? firstField
  const operators = OPERATORS_FOR_TYPE[fieldDef.type] ?? []
  const effectiveOperator = operator || operators[0]?.value

  // When field changes, reset operator + value
  useEffect(() => {
    if (!initialCondition || initialCondition.field !== fieldKey) {
      setOperator(operators[0]?.value ?? '')
      setValue(fieldDef.type === 'select' ? [] : '')
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fieldKey])

  // Click-outside closes
  useEffect(() => {
    const handler = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) onCancel()
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [onCancel])

  const dynamicSource =
    // Caller-supplied option arrays (e.g. grants org_types / activity_codes from
    // /grants/filter-options) take priority, keyed by the field's `dynamic` name.
    (typeof fieldDef.dynamic === 'string' && Array.isArray(dynamicOptions[fieldDef.dynamic]))
      ? dynamicOptions[fieldDef.dynamic]
    : fieldDef.dynamic === 'countries' ? (countries ?? [])
    : fieldDef.dynamic === 'therapeutic_areas' ? (therapeuticAreas ?? [])
    : fieldDef.dynamic === true ? (therapeuticAreas ?? [])  // legacy
    : null
  const options = dynamicSource
    ? dynamicSource.map(a => ({ value: a, label: a }))
    : (fieldDef.options ?? []).map(o => ({ value: o, label: fieldDef.displayFn ? fieldDef.displayFn(o) : o }))

  // Search-as-you-type inside the option list (useful for long country lists).
  const [optionSearch, setOptionSearch] = useState('')
  const filteredOptions = optionSearch
    ? options.filter(o => o.label.toLowerCase().includes(optionSearch.toLowerCase()))
    : options

  const handleToggleOption = (opt) => {
    const arr = Array.isArray(value) ? value : []
    setValue(arr.includes(opt) ? arr.filter(x => x !== opt) : [...arr, opt])
  }

  const canApply = () => {
    if (fieldDef.type === 'boolean') return true
    if (fieldDef.type === 'select') return Array.isArray(value) && value.length > 0
    return value !== '' && value != null
  }

  const handleApply = () => {
    if (!canApply()) return
    const cond = initialCondition
      ? { ...initialCondition, field: fieldKey, operator: effectiveOperator, value }
      : makeCondition(fieldKey, effectiveOperator, value)
    onApply(cond)
  }

  return (
    <div className="condition-builder" ref={wrapRef}>
      {/* Field row */}
      <div className="cond-row">
        <label className="cond-label">Field</label>
        <select
          className="cond-select"
          value={fieldKey}
          onChange={e => setFieldKey(e.target.value)}
        >
          {filterFields.map(f => (
            <option key={f.key} value={f.key}>{f.label}</option>
          ))}
        </select>
      </div>

      {/* Operator row — shown for every type that has operators, including
          boolean (is checked / is not checked). */}
      {operators.length > 0 && (
        <div className="cond-row">
          <label className="cond-label">Condition</label>
          <select
            className="cond-select"
            value={effectiveOperator}
            onChange={e => setOperator(e.target.value)}
          >
            {operators.map(op => (
              <option key={op.value} value={op.value}>{op.label}</option>
            ))}
          </select>
        </div>
      )}

      {/* Value row */}
      {fieldDef.type === 'boolean' && fieldDef.hint && (
        <p className="cond-boolean-hint">{fieldDef.hint}</p>
      )}

      {fieldDef.type === 'select' && (
        <div className="cond-row cond-row-options">
          <label className="cond-label">Value</label>
          <div style={{ flex: 1, minWidth: 0 }}>
            {options.length > 8 && (
              <input
                className="cond-input"
                type="text"
                placeholder="Search…"
                value={optionSearch}
                onChange={e => setOptionSearch(e.target.value)}
                style={{ width: '100%', marginBottom: 6 }}
              />
            )}
            <div className="cond-options-list">
              {filteredOptions.map(opt => (
                <label key={opt.value} className="cond-option-label">
                  <input
                    type="checkbox"
                    checked={Array.isArray(value) && value.includes(opt.value)}
                    onChange={() => handleToggleOption(opt.value)}
                  />
                  {opt.label}
                </label>
              ))}
              {filteredOptions.length === 0 && (
                <div style={{ fontSize: 12, color: '#94a3b8', padding: '4px 0' }}>
                  {options.length === 0 ? 'No options available' : 'No matches'}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {fieldDef.type === 'text' && (
        <div className="cond-row">
          <label className="cond-label">Value</label>
          <input
            className="cond-input"
            type="text"
            value={value}
            onChange={e => setValue(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleApply()}
            autoFocus
          />
        </div>
      )}

      {fieldDef.type === 'number' && (
        <div className="cond-row">
          <label className="cond-label">Value</label>
          <input
            className="cond-input"
            type="number"
            value={value}
            onChange={e => setValue(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleApply()}
            autoFocus
          />
        </div>
      )}

      {fieldDef.type === 'date' && (
        <div className="cond-row">
          <label className="cond-label">Date</label>
          <input
            className="cond-input"
            type="date"
            value={value}
            onChange={e => setValue(e.target.value)}
            autoFocus
          />
        </div>
      )}

      {/* Actions */}
      <div className="cond-actions">
        <button className="btn-sm" onClick={onCancel}>Cancel</button>
        <button
          className="btn-sm btn-primary"
          onClick={handleApply}
          disabled={!canApply()}
        >
          Apply
        </button>
      </div>
    </div>
  )
}
