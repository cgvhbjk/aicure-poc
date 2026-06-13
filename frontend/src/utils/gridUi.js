// Shared AG Grid overlay templates. The infinite row model shows the loading
// overlay automatically while the first block of a (new) datasource loads, and
// the no-rows overlay when a filter matches nothing.
export const GRID_LOADING_TEMPLATE =
  '<div class="grid-loading"><div class="grid-spinner"></div>Loading…</div>'

export const GRID_EMPTY_TEMPLATE =
  '<div class="grid-loading">No matching rows</div>'

// Turn an axios/datasource error into a short, human message for the grid
// toolbar. A failed fetch must not look like an empty result set ("No matching
// rows") — surface the backend's detail (e.g. a 422 "Invalid date: …") or the
// HTTP status, falling back to a connection hint.
export function gridErrorMessage(e) {
  const detail = e?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  if (e?.response?.status) return `Request failed (${e.response.status})`
  return 'Failed to load — is the API reachable?'
}
