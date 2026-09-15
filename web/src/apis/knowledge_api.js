import { apiDelete, apiGet, apiPost, apiPut } from './base'

export const scheduledTaskApi = {
  list: () => apiGet('/api/scheduled-tasks'),
  create: (data) => apiPost('/api/scheduled-tasks', data),
  update: (id, data) => apiPut(`/api/scheduled-tasks/${id}`, data),
  remove: (id) => apiDelete(`/api/scheduled-tasks/${id}`)
}

/**
 * 统一知识库 API。
 * 文档、代码仓库和 RAG 物料都归属于同一 KnowledgeBase；只保留当前后端提供的基础文本接口。
 */
export const knowledgeBaseApi = {
  list: () => apiGet('/api/knowledge/databases'),
  create: (data) => apiPost('/api/knowledge/databases', data),
  detail: (kbId) => apiGet(`/api/knowledge/databases/${kbId}`),
  remove: (kbId) => apiDelete(`/api/knowledge/databases/${kbId}`),
  documents: (kbId) => apiGet(`/api/knowledge/databases/${kbId}/documents`),
  upload: (kbId, file) => {
    const form = new FormData()
    form.append('file', file)
    return apiPost(`/api/knowledge/databases/${kbId}/documents/upload`, form)
  },
  removeDocument: (kbId, documentId) =>
    apiDelete(`/api/knowledge/databases/${kbId}/documents/${documentId}`),
  query: (kbId, data) => apiPost(`/api/knowledge/databases/${kbId}/query`, data),
  repositories: (kbId) => apiGet(`/api/knowledge/databases/${kbId}/repositories`),
  createRepository: (kbId, data) =>
    apiPost(`/api/knowledge/databases/${kbId}/repositories`, data),
  updateRepository: (kbId, repoId, data) =>
    apiPut(`/api/knowledge/databases/${kbId}/repositories/${repoId}`, data),
  removeRepository: (kbId, repoId) =>
    apiDelete(`/api/knowledge/databases/${kbId}/repositories/${repoId}`),
  pullRepository: (kbId, repoId) =>
    apiPost(`/api/knowledge/databases/${kbId}/repositories/${repoId}/pull`, {}),
  materials: (params = {}) => {
    const query = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== '' && value !== undefined && value !== null) query.set(key, String(value))
    })
    const suffix = query.toString()
    return apiGet(`/api/knowledge/materials${suffix ? `?${suffix}` : ''}`)
  },
  material: (documentId) =>
    apiGet(`/api/knowledge/materials/${encodeURIComponent(documentId)}`)
}
