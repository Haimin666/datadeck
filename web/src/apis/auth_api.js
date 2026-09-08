import { apiGet, apiPost, apiPut, apiAdminGet, apiAdminPost } from './base'

async function login(credentials) {
  const formData = new FormData()
  formData.append('username', credentials.loginId || credentials.username)
  formData.append('password', credentials.password)
  return apiPost('/api/auth/token', formData, {}, false)
}

async function initialize(admin) {
  return apiPost('/api/auth/initialize', admin, {}, false)
}

async function checkFirstRun() {
  return apiGet('/api/auth/check-first-run', {}, false)
}

async function getCurrentUser() {
  return apiGet('/api/auth/me')
}

async function updateProfile(profileData) {
  return apiPut('/api/auth/profile', profileData)
}

async function getUsers({ skip = 0, limit = 100 } = {}) {
  const params = new URLSearchParams({ skip: String(skip), limit: String(limit) })
  return apiAdminGet(`/api/auth/users?${params}`)
}

async function createUser(userData) {
  return apiAdminPost('/api/auth/users', userData)
}

async function updateUser(userId, userData) {
  return apiPut(`/api/auth/users/${encodeURIComponent(userId)}`, userData)
}

async function deleteUser(userId) {
  return apiAdminGet(`/api/auth/users/${encodeURIComponent(userId)}`, { method: 'DELETE' })
}

export const authApi = {
  login,
  initialize,
  checkFirstRun,
  getCurrentUser,
  updateProfile,
  getUsers,
  createUser,
  updateUser,
  deleteUser
}
