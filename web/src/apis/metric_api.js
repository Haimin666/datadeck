import { apiDelete, apiGet, apiPost, apiPut } from './base'

export const metricApi = {
  list: (params = {}) => {
    const query = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== '' && value !== undefined && value !== null) query.set(key, value)
    })
    return apiGet(`/api/metrics/registry?${query}`)
  },
  create: (data) => apiPost('/api/metrics/registry', data),
  update: (id, data) => apiPut(`/api/metrics/registry/${id}`, data),
  remove: (id) => apiDelete(`/api/metrics/registry/${id}`)
}
