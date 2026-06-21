// Allowlist URL schemes before using an external/ingested value as an href.
// Trial/grant feed data and analyst-entered website/linkedin/source_url fields
// are untrusted: a `javascript:` or `data:` value rendered as <a href> executes
// script in the app's origin (stored XSS). Returns undefined for anything that
// isn't http(s)/mailto so the caller renders a non-clickable value instead.
export function safeHref(raw) {
  if (!raw) return undefined
  try {
    const u = new URL(String(raw), window.location.origin)
    return ['http:', 'https:', 'mailto:'].includes(u.protocol) ? u.href : undefined
  } catch {
    return undefined
  }
}
