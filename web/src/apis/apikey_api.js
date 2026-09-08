import { apiGet, apiPost, apiPut, apiDelete } from './base'

export const apikeyApi = {
  listKeys: () => apiGet('/api/user/apikey'),
  createKey: (data) => apiPost('/api/user/apikey', data),
  updateKey: (keyId, data) => apiPut(`/api/user/apikey/${keyId}`, data),
  deleteKey: (keyId) => apiDelete(`/api/user/apikey/${keyId}`),
  rotateKey: (keyId) => apiPost(`/api/user/apikey/${keyId}/rotate`, {})
}
