// Shared AG Grid overlay templates. The infinite row model shows the loading
// overlay automatically while the first block of a (new) datasource loads, and
// the no-rows overlay when a filter matches nothing.
export const GRID_LOADING_TEMPLATE =
  '<div class="grid-loading"><div class="grid-spinner"></div>Loading…</div>'

export const GRID_EMPTY_TEMPLATE =
  '<div class="grid-loading">No matching rows</div>'
