import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { agentApi } from '@/apis'

export const useThreadStore = defineStore('thread', () => {
  const threads = ref([])
  const activeThreadId = ref(localStorage.getItem('active_thread_id') || '')
  const isLoadingThreads = ref(false)
  const threadStates = ref({})
  const currentThread = computed(() =>
    threads.value.find((t) => t.id === activeThreadId.value) || null
  )
  const isSearching = ref(false)

  async function fetchThreads({ limit = 100, offset = 0 } = {}) {
    isLoadingThreads.value = true
    try {
      const data = await agentApi.listThreads(limit, offset)
      const items = data?.threads || data || []
      // 保持已有线程的修改（标题等）
      const existingMap = new Map(threads.value.map((t) => [t.id, t]))
      items.forEach((item) => {
        if (existingMap.has(item.id)) {
          Object.assign(existingMap.get(item.id), item)
        } else {
          existingMap.set(item.id, item)
        }
      })
      threads.value = [...existingMap.values()]
      return threads.value
    } finally {
      isLoadingThreads.value = false
    }
  }

  async function searchThreads(query, { limit = 20, offset = 0 } = {}) {
    isSearching.value = true
    try {
      const data = await agentApi.searchThreads(query, { limit, offset })
      const items = data?.threads || data || []
      // 搜索结果是快照，直接替换
      threads.value = items
      return items
    } finally {
      isSearching.value = false
    }
  }

  async function createThread(agentId, title) {
    const data = await agentApi.createThread(agentId, title)
    const thread = data?.thread || data
    if (thread) {
      const idx = threads.value.findIndex((t) => t.id === thread.id)
      if (idx >= 0) {
        threads.value[idx] = thread
      } else {
        threads.value.unshift(thread)
      }
    }
    return thread
  }

  async function updateThread(threadId, updates) {
    const data = await agentApi.updateThread(threadId, updates.title, updates.is_pinned ?? false)
    const thread = data?.thread || data
    if (thread) {
      const idx = threads.value.findIndex((t) => t.id === threadId)
      if (idx >= 0) threads.value[idx] = thread
    }
    return thread
  }

  async function deleteThread(threadId) {
    await agentApi.deleteThread(threadId)
    threads.value = threads.value.filter((t) => t.id !== threadId)
    if (activeThreadId.value === threadId) {
      activeThreadId.value = threads.value[0]?.id || ''
      localStorage.setItem('active_thread_id', activeThreadId.value)
    }
  }

  async function markThreadViewed(threadId) {
    await agentApi.markThreadViewed(threadId)
    const thread = threads.value.find((t) => t.id === threadId)
    if (thread) thread.unread = false
  }

  function setActiveThreadId(id) {
    activeThreadId.value = id
    localStorage.setItem('active_thread_id', id)
  }

  function reset() {
    threads.value = []
    activeThreadId.value = ''
    threadStates.value = {}
    localStorage.removeItem('active_thread_id')
  }

  return {
    threads, activeThreadId, isLoadingThreads, isSearching, threadStates, currentThread,
    fetchThreads, searchThreads, createThread, updateThread, deleteThread,
    markThreadViewed, setActiveThreadId, reset
  }
})
