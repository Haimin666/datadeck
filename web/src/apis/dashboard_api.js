import { apiAdminGet } from './base'

export const dashboardApi = {
  getStats: () => apiAdminGet('/api/dashboard/stats'),
  getUsers: (params) => apiAdminGet(`/api/dashboard/users?${new URLSearchParams(params).toString()}`),
  getTools: () => apiAdminGet('/api/dashboard/tools'),
  getCallsTimeseries: (params) =>
    apiAdminGet(`/api/dashboard/calls/timeseries?${new URLSearchParams(params).toString()}`),
  getThreads: (params) =>
    apiAdminGet(`/api/dashboard/threads?${new URLSearchParams(params).toString()}`),
  getFeedbacks: (params) =>
    apiAdminGet(`/api/dashboard/feedbacks?${new URLSearchParams(params).toString()}`),
  getConversations: (params) =>
    apiAdminGet(`/api/dashboard/conversations?${new URLSearchParams(params).toString()}`),
  getConversationDetail: (id) => apiAdminGet(`/api/dashboard/conversations/${id}`)
}
