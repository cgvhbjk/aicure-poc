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
