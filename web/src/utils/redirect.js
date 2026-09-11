// 仅允许应用内相对路径，防止开放重定向。
export function sanitizeRedirect(value) {
  if (typeof value === 'string' && value.length > 0 && value[0] === '/' &&
      value[1] !== '/' && value[1] !== '\\') return value
  return '/'
}
