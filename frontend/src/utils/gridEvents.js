// AG Grid events worth surfacing as a single "user changed the grid state"
// signal — used by ViewsSidebar to mark a view as modified.
const GRID_STATE_EVENTS = [
  'columnVisible', 'columnMoved', 'columnPinned', 'columnResized',
  'sortChanged', 'filterChanged',
]

// Returns a disposer that detaches the listeners. AG Grid tears down its own
// event registry on destroy, but callers should still call the disposer on
// unmount to be symmetric (and to avoid onChange firing during teardown).
export function attachGridStateListeners(api, onChange) {
  if (!api || !onChange) return () => {}
  const bump = () => onChange()
  GRID_STATE_EVENTS.forEach(ev => {
    try { api.addEventListener(ev, bump) } catch {}
  })
  return () => {
    GRID_STATE_EVENTS.forEach(ev => {
      try { api.removeEventListener(ev, bump) } catch {}
    })
  }
}
