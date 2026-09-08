import { message } from 'ant-design-vue'

export const handleChatError = (error, context = '操作') => {
  const msg = error?.response?.data?.detail?.[0]?.msg || error?.message || `${context}失败`
  console.error(`${context}失败:`, error)
  message.error(msg)
}

export const handleNetworkError = (error, context = '网络请求') => {
  let msg = `${context}失败`
  if (error?.status === 401) msg = '认证失败，请重新登录'
  else if (error?.status === 403) msg = '权限不足'
  else if (error?.status === 404) msg = '请求的资源不存在'
  else if (error?.status >= 500) msg = '服务器错误，请稍后重试'
  else if (error?.message) msg = error.message
  message.error(msg)
}
