import axios from 'axios'

// In prod the backend serves the SPA from the same origin, so use relative
// URLs. The app may be served at the domain root (standalone) OR under /pipeline
// (proxied behind the CRM on one domain) — derive the prefix from the path the
// SPA was loaded under so API calls hit the right place either way.
// In dev the backend lives on a different port. VITE_API_URL always wins.
const _prodBase =
  typeof window !== 'undefined' && window.location.pathname.startsWith('/pipeline')
    ? '/pipeline'
    : ''
const _defaultBase = import.meta.env.PROD ? _prodBase : 'http://localhost:8000'
const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL ?? _defaultBase,
  paramsSerializer: {
    serialize: (params) => {
      const sp = new URLSearchParams()
      Object.entries(params).forEach(([k, v]) => {
        if (v === undefined || v === null) return
        if (Array.isArray(v)) {
          v.forEach((val) => sp.append(k, val))
        } else {
          sp.append(k, v)
        }
      })
      return sp.toString()
    },
  },
})

// ── Request progress (drives the top ProgressBar) ────────────────────────────
// Tiny pub/sub fed by the axios interceptors below: `active` while any request
// is in flight; `fraction` (0..1) when the current download reports a usable
// total. Gzipped API responses usually hide Content-Length from XHR progress
// events, so the bar falls back to an indeterminate sweep in that case.
const progressListeners = new Set()
let _inflight = 0
let _fraction = null

const emitProgress = () => {
  const snapshot = { active: _inflight > 0, fraction: _fraction }
  progressListeners.forEach((fn) => fn(snapshot))
}

export const onRequestProgress = (fn) => {
  progressListeners.add(fn)
  fn({ active: _inflight > 0, fraction: _fraction })
  return () => progressListeners.delete(fn)
}

api.interceptors.request.use((config) => {
  _inflight += 1
  _fraction = null
  config.onDownloadProgress = (e) => {
    if (e.total) {
      _fraction = e.loaded / e.total
      emitProgress()
    }
  }
  emitProgress()
  return config
})

const _settle = (ok) => (value) => {
  _inflight = Math.max(0, _inflight - 1)
  if (_inflight === 0) _fraction = null
  emitProgress()
  return ok ? value : Promise.reject(value)
}
api.interceptors.response.use(_settle(true), _settle(false))

export const getTrials = (params) => api.get('/trials', { params })
export const getTrial = (trialId) => api.get(`/trials/${trialId}`)
export const getNews = (params) => api.get('/news', { params })
export const getTrialNews = (nctId) => api.get(`/trials/${nctId}/news`)
export const getTrialRegistries = (trialId) => api.get(`/trials/${trialId}/registries`)
export const getStats = () => api.get('/stats')

export const getOrgs = (params) => api.get('/orgs', { params })
export const getOrg = (orgId) => api.get(`/orgs/${orgId}`)
export const getOrgTrials = (orgId) => api.get(`/orgs/${orgId}/trials`)
export const getOrgContacts = (orgId) => api.get(`/orgs/${orgId}/contacts`)
export const addOrgContact = (orgId, body) => api.post(`/orgs/${orgId}/contacts`, body)
export const patchOrg = (orgId, body) => api.patch(`/orgs/${orgId}`, body)

export const getGrants = (params) => api.get('/grants', { params })
export const getGrant = (grantId) => api.get(`/grants/${grantId}`)
export const getGrantTrials = (grantId) => api.get(`/grants/${grantId}/trials`)
export const getGrantStats = () => api.get('/grants/stats')

export const uploadData = (formData) => api.post('/upload', formData, {
  headers: { 'Content-Type': 'multipart/form-data' },
})
export const getMerges = (params) => api.get('/merges', { params })
export const getMergeStats = () => api.get('/merges/stats')
export const confirmMerge = (id, body) => api.post(`/merges/${id}/confirm`, body)
export const rejectMerge = (id) => api.post(`/merges/${id}/reject`)
export const snoozeMerge = (id) => api.post(`/merges/${id}/snooze`)
export const undoMerge = (id) => api.post(`/merges/${id}/undo`)
