// AG Grid events worth surfacing as a single "user changed the grid state"
// signal — used by ViewsSidebar to mark a view as modified.
const GRID_STATE_EVENTS = [
  'columnVisible', 'columnMoved', 'columnPinned', 'columnResized',
  'sortChanged', 'filterChanged',
]

export function attachGridStateListeners(api, onChange) {
  if (!api || !onChange) return
  const bump = () => onChange()
  GRID_STATE_EVENTS.forEach(ev => {
    try { api.addEventListener(ev, bump) } catch {}
  })
}
