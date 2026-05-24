import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || 'http://localhost:8000',
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

export const getTrials = (params) => api.get('/trials', { params })
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

export const uploadData = (formData) => api.post('/upload', formData, {
  headers: { 'Content-Type': 'multipart/form-data' },
})
export const getMerges = (params) => api.get('/merges', { params })
export const getMergeStats = () => api.get('/merges/stats')
export const confirmMerge = (id, body) => api.post(`/merges/${id}/confirm`, body)
export const rejectMerge = (id) => api.post(`/merges/${id}/reject`)
export const snoozeMerge = (id) => api.post(`/merges/${id}/snooze`)
export const undoMerge = (id) => api.post(`/merges/${id}/undo`)
