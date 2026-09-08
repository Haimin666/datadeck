/**
 * 自动滚动到消息底部
 */
export function scrollToBottom(element, behavior = 'smooth') {
  if (!element) return
  element.scrollTo({ top: element.scrollHeight, behavior })
}

export function forceScrollToBottom(element) {
  if (!element) return
  element.scrollTop = element.scrollHeight
}
