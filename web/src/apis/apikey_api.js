import { apiGet, apiPost, apiPut, apiDelete } from './base'

const API_KEY_BASE_PATH = '/api/user/apikey'

export const apikeyApi = {
  list: (skip = 0, limit = 100) => {
    const params = new URLSearchParams({ skip: String(skip), limit: String(limit) })
    return apiGet(`${API_KEY_BASE_PATH}?${params}`)
  },

  create: (data) => apiPost(`${API_KEY_BASE_PATH}`, data),

  get: (id) => apiGet(`${API_KEY_BASE_PATH}/${id}`),

  update: (id, data) => apiPut(`${API_KEY_BASE_PATH}/${id}`, data),

  delete: (id) => apiDelete(`${API_KEY_BASE_PATH}/${id}`),

  rotate: (id) => apiPost(`${API_KEY_BASE_PATH}/${id}/rotate`)
}
