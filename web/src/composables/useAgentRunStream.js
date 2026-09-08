import { ref, unref } from 'vue'
import { agentApi } from '@/apis'
import { handleChatError } from '@/utils/errorHandler'
import { isTerminalStatus, isInterruptedStatus } from '@/utils/agentRun'
import { processSSEEvent } from '@/utils/messageProcessor'

const ACTIVE_RUN_TTL_MS = 60 * 60 * 1000

const getActiveRunKey = (threadId) => `active_run:${threadId}`

const saveActiveRunSnapshot = (threadId, runId, lastSeq = '0-0') => {
  if (!threadId || !runId) return
  localStorage.setItem(
    getActiveRunKey(threadId),
    JSON.stringify({ run_id: runId, last_seq: lastSeq, created_at: Date.now() })
  )
}

const loadActiveRunSnapshot = (threadId) => {
  if (!threadId) return null
  try {
    const raw = localStorage.getItem(getActiveRunKey(threadId))
    return raw ? JSON.parse(raw) : null
  } catch { return null }
}

const clearActiveRunSnapshot = (threadId) => {
  if (!threadId) localStorage.removeItem(getActiveRunKey(threadId))
}

export function useAgentRunStream({
  getThreadState,
  handleStreamChunk,
  resetOnGoingConv,
  onTerminalDetected
}) {
  let currentController = null

  const startRunStream = async (threadId, runId, afterSeq = '0-0') => {
    if (!threadId || !runId) return
    const ts = getThreadState(threadId)
    if (!ts) return

    // 取消旧流
    if (currentController) currentController.abort()
    currentController = new AbortController()
    ts.runStreamAbortController = currentController

    const url = agentApi.getAgentRunEventsUrl(runId)
    const headers = { ...(ts.getAuthHeaders?.() || {}) }
    if (afterSeq && afterSeq !== '0-0') {
      headers['Last-Event-ID'] = afterSeq
    }

    try {
      const response = await fetch(url, {
        headers,
        signal: currentController.signal
      })

      if (!response.ok) {
        const err = new Error(`SSE 请求失败: ${response.status}`)
        err.status = response.status
        throw err
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let lastSeq = afterSeq

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          const trimmed = line.replace(/\r$/, '')
          if (!trimmed) continue
          if (trimmed.startsWith('id:')) {
            lastSeq = trimmed.slice(3).trim()
          } else if (trimmed.startsWith('data:')) {
            const dataText = trimmed.slice(5).trimStart()
            try {
              const parsed = JSON.parse(dataText)
              processSSEEvent(ts, parsed, (updates) => {
                Object.assign(ts, updates)
                if (updates.messageDelta) handleStreamChunk(updates.messageDelta, threadId)
              })
            } catch (e) {
              console.warn('SSE 数据解析失败:', e, dataText)
            }
          }
        }
      }

      // 流结束后检查终态
      try {
        const runRes = await agentApi.getAgentRun(runId)
        const run = runRes?.run
        if (isTerminalStatus(run?.status)) {
          clearActiveRunSnapshot(threadId)
          ts.activeRunId = null
          ts.isStreaming = false
          ts.replyLoading = false
          onTerminalDetected?.(threadId, runId, new Set([threadId]))
        } else if (isInterruptedStatus(run?.status)) {
          saveActiveRunSnapshot(threadId, runId, lastSeq)
          ts.interrupted = true
        } else {
          saveActiveRunSnapshot(threadId, runId, lastSeq)
        }
      } catch (e) {
        console.warn('SSE 结束后检查 run 状态失败:', e)
      }
    } catch (error) {
      if (error?.name !== 'AbortError') {
        handleChatError(error, 'SSE 流')
        ts.isStreaming = false
        ts.replyLoading = false
      }
    } finally {
      if (currentController === ts.runStreamAbortController) {
        ts.runStreamAbortController = null
      }
    }
  }

  const stopRunStream = (threadId) => {
    const ts = getThreadState(threadId)
    if (!ts) return
    if (ts.runStreamAbortController) {
      ts.runStreamAbortController.abort()
      ts.runStreamAbortController = null
    }
  }

  const resumeActiveRun = async (threadId) => {
    if (!threadId) return
    const ts = getThreadState(threadId)
    if (!ts) return

    const snapshot = loadActiveRunSnapshot(threadId)
    if (snapshot?.run_id) {
      if (Date.now() - Number(snapshot.created_at || 0) > ACTIVE_RUN_TTL_MS) {
        clearActiveRunSnapshot(threadId)
        return
      }
      try {
        const runRes = await agentApi.getAgentRun(snapshot.run_id)
        const run = runRes?.run
        if (isInterruptedStatus(run?.status)) {
          ts.interrupted = true
          return
        }
        if (run && !isTerminalStatus(run?.status)) {
          await startRunStream(threadId, run.id, snapshot.last_seq)
          return
        }
      } catch { /* ignore */ }
      clearActiveRunSnapshot(threadId)
    }

    ts.activeRunId = null
    ts.isStreaming = false
    ts.replyLoading = false
    ts.interrupted = false
  }

  return { startRunStream, stopRunStream, resumeActiveRun }
}
