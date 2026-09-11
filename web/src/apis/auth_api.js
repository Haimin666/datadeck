/**
 * 认证相关 API
 */

import {
  apiAdminGet,
  apiDelete,
  apiGet,
  apiPost,
  apiPut,
  apiSuperAdminGet,
  apiSuperAdminPost
} from './base'

async function getUserAccessOptions() {
  return apiAdminGet('/api/auth/users/access-options')
}

async function login(credentials) {
  const formData = new FormData()
  formData.append('username', credentials.loginId)
  formData.append('password', credentials.password)
  return apiPost('/api/auth/token', formData, {}, false)
}

async function initialize(admin) {
  return apiPost('/api/auth/initialize', admin, {}, false)
}

async function checkFirstRun() {
  return apiGet('/api/auth/check-first-run', {}, false)
}

async function getUsers({ skip = 0, limit = 100 } = {}) {
  const params = new URLSearchParams({ skip: String(skip), limit: String(limit) })
  return apiGet(`/api/auth/users?${params}`)
}

async function getUsersPage({ offset = 0, limit = 50, search, departmentId, role } = {}) {
  const params = new URLSearchParams({ offset: String(offset), limit: String(limit) })
  if (search) params.set('search', search)
  if (departmentId) params.set('department_id', String(departmentId))
  if (role) params.set('role', role)
  return apiAdminGet(`/api/auth/users/page?${params}`)
}

async function createUser(userData) {
  return apiPost('/api/auth/users', userData)
}

async function updateUser(userId, userData) {
  return apiPut(`/api/auth/users/${encodeURIComponent(userId)}`, userData)
}

async function deleteUser(userId) {
  return apiDelete(`/api/auth/users/${encodeURIComponent(userId)}`)
}

async function validateUsername(username) {
  return apiPost('/api/auth/validate-username', { username })
}

async function uploadAvatar(file) {
  const formData = new FormData()
  formData.append('file', file)
  return apiPost('/api/auth/upload-avatar', formData)
}

async function getCurrentUser() {
  return apiGet('/api/auth/me')
}

async function updateProfile(profileData) {
  return apiPut('/api/auth/profile', profileData)
}

async function checkUid(uid) {
  return apiSuperAdminGet(`/api/auth/check-uid/${encodeURIComponent(uid)}`)
}

async function impersonateUser(userId) {
  return apiSuperAdminPost(`/api/auth/impersonate/${encodeURIComponent(userId)}`, {})
}

async function getCLIAuthSession(userCode) {
  const encoded = encodeURIComponent(userCode)
  return apiGet(`/api/auth/cli/sessions/${encoded}`)
}

async function approveCLIAuthSession(userCode) {
  const encoded = encodeURIComponent(userCode)
  return apiPost(`/api/auth/cli/sessions/${encoded}/approve`, {})
}

export const authApi = {
  login,
  initialize,
  checkFirstRun,
  getUsers,
  getUsersPage,
  createUser,
  updateUser,
  deleteUser,
  validateUsername,
  uploadAvatar,
  getCurrentUser,
  updateProfile,
  checkUid,
  impersonateUser,
  getUserAccessOptions,
  getCLIAuthSession,
  approveCLIAuthSession
}
