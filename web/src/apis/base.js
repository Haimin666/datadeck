import { useUserStore } from '@/stores/user'
import { message } from 'ant-design-vue'

function safeRequestMetadata(url, requestOptions, response = null) {
  let path = '[invalid-url]'
  try {
    path = new URL(url, 'http://datadeck.local').pathname
  } catch {
    // ignore
  }
  return {
    path,
    method: requestOptions?.method || 'GET',
    ...(response ? { status: response.status } : {})
  }
}

const SAFE_ERROR_RESPONSE_HEADERS = ['retry-after', 'www-authenticate']

function safeResponseHeaders(headers) {
  const safeHeaders = new Headers()
  for (const name of SAFE_ERROR_RESPONSE_HEADERS) {
    const value = headers.get(name)
    if (value !== null) safeHeaders.set(name, value)
  }
  return safeHeaders
}

function safeErrorData(errorData, status, publicMessage) {
  if (status !== 422) return { detail: publicMessage }
  const detail = errorData?.detail
  if (!Array.isArray(detail)) return { detail: publicMessage }
  return {
    detail: detail.map((item) => ({
      loc: Array.isArray(item?.loc) ? item.loc.map((part) => String(part)) : [],
      msg: '请求参数验证失败',
      type: typeof item?.type === 'string' ? item.type : 'validation_error'
    }))
  }
}

function publicErrorMessage(url, status, headers, requiresAuth) {
  const path = safeRequestMetadata(url, {}).path
  if (status === 400) return '请求参数错误'
  if (status === 401) {
    if (requiresAuth) return '登录已过期，请重新登录'
    return path === '/api/auth/token' ? '用户名或密码错误' : '认证请求失败'
  }
  if (status === 403) return '没有权限执行此操作'
  if (status === 404) return '请求资源不存在'
  if (status === 409) return '请求冲突，请刷新后重试'
  if (status === 422) return '请求参数验证失败'
  if (status === 429) return '请求过于频繁，请稍后重试'
  if (status >= 500) return '服务器内部错误'
  return `请求失败: ${status}`
}

export async function apiRequest(url, options = {}, requiresAuth = true, responseType = 'json') {
  try {
    const isFormData = options?.body instanceof FormData
    const requestOptions = {
      ...options,
      headers: {
        ...(!isFormData ? { 'Content-Type': 'application/json' } : {}),
        ...options.headers
      }
    }

    if (requiresAuth) {
      const userStore = useUserStore()
      if (!userStore.isLoggedIn) {
        throw new Error('用户未登录')
      }
      Object.assign(requestOptions.headers, userStore.getAuthHeaders())
    }

    const response = await fetch(url, requestOptions)

    if (!response.ok) {
      const errorMessage = publicErrorMessage(url, response.status, response.headers, requiresAuth)
      let errorData = null
      console.error('API请求失败:', safeRequestMetadata(url, requestOptions, response))
      try {
        errorData = await response.json()
        if (response.status === 422) {
          console.error('API请求校验失败:', safeRequestMetadata(url, requestOptions, response))
        }
      } catch {
        console.error('API错误响应无法解析:', safeRequestMetadata(url, requestOptions, response))
      }

      const error = new Error(errorMessage)
      error.status = response.status
      error.headers = safeResponseHeaders(response.headers)
      error.response = {
        status: response.status,
        data: safeErrorData(errorData, response.status, errorMessage),
        headers: error.headers
      }

      if (response.status === 401 && requiresAuth) {
        message.error('登录已过期，请重新登录')
        const userStore = useUserStore()
        if (userStore.isLoggedIn) {
          userStore.logout()
        }
        setTimeout(() => {
          window.location.href = '/login'
        }, 1500)
        throw error
      }
      throw error
    }

    if (responseType === 'blob') return response
    if (responseType === 'text') return await response.text()
    const contentType = response.headers.get('Content-Type')
    if (contentType && contentType.includes('application/json')) {
      return await response.json()
    }
    return await response.text()
  } catch (error) {
    if (error.name !== 'AbortError') {
      console.error('API请求异常:', {
        ...safeRequestMetadata(url, options),
        status: error?.status ?? null,
        errorType: error?.name || 'Error'
      })
    }
    throw error
  }
}

export function apiGet(url, options = {}, requiresAuth = true, responseType = 'json') {
  return apiRequest(url, { method: 'GET', ...options }, requiresAuth, responseType)
}

export function apiPost(url, data = {}, options = {}, requiresAuth = true, responseType = 'json') {
  return apiRequest(
    url,
    { method: 'POST', body: data instanceof FormData ? data : JSON.stringify(data), ...options },
    requiresAuth,
    responseType
  )
}

export function apiPut(url, data = {}, options = {}, requiresAuth = true, responseType = 'json') {
  return apiRequest(
    url,
    { method: 'PUT', body: data instanceof FormData ? data : JSON.stringify(data), ...options },
    requiresAuth,
    responseType
  )
}

export function apiDelete(url, options = {}, requiresAuth = true, responseType = 'json') {
  return apiRequest(url, { method: 'DELETE', ...options }, requiresAuth, responseType)
}

export function apiAdminGet(url, options = {}, responseType = 'json') {
  const userStore = useUserStore()
  if (!userStore.isAdmin) throw new Error('需要管理员权限')
  return apiGet(url, options, true, responseType)
}

export function apiAdminPost(url, data = {}, options = {}, responseType = 'json') {
  const userStore = useUserStore()
  if (!userStore.isAdmin) throw new Error('需要管理员权限')
  return apiPost(url, data, options, true, responseType)
}

export function apiSuperAdminGet(url, options = {}, responseType = 'json') {
  const userStore = useUserStore()
  if (!userStore.isSuperAdmin) throw new Error('需要超级管理员权限')
  return apiGet(url, options, true, responseType)
}

export function apiSuperAdminPost(url, data = {}, options = {}, responseType = 'json') {
  const userStore = useUserStore()
  if (!userStore.isSuperAdmin) throw new Error('需要超级管理员权限')
  return apiPost(url, data, options, true, responseType)
}
