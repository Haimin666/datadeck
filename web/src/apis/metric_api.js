import { apiAdminGet, apiAdminPut } from './base'

export const metricApi = {
  list: (params = {}) => {
    const query = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== '' && value !== undefined && value !== null) query.set(key, value)
    })
    return apiAdminGet(`/api/metrics/registry?${query}`)
  },
  update: (id, data) => apiAdminPut(`/api/metrics/registry/${id}`, data)
}
