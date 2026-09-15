import { unref } from 'vue'
import { agentApi } from '@/apis'
import { isSteerableMainChatRun } from '@/utils/agentRun'
import { compareRunSeq, normalizeRunSeq, resolveRunResumeAfterSeq } from '@/utils/runStreamResume'
import { hasPendingInterruptPayload } from '@/utils/toolApproval'

const RUN_INTERRUPTED_STATUS = 'interrupted'
const RUN_TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled'])
const RUN_RECONNECT_MAX_DELAY_MS = 10000
const ACTIVE_RUN_STORAGE_TTL_MS = 60 * 60 * 1000
const ACTIVE_RUN_CLIENT_ID = `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`

const resolveFailureMessage = (value) => {
  if (typeof value === 'string' && value.trim()) return value.trim()
  if (!value || typeof value !== 'object') return ''
  return String(
    value.error_message || value.message || value.error?.message || value.detail || ''
  ).trim()
}

const getActiveRunStorageKey = (threadId) => `active_run:${threadId}`

const getThreadIdFromObject = (value) => {
  if (!value || typeof value !== 'object') return ''
  if (typeof value.thread_id === 'string' && value.thread_id.trim()) return value.thread_id.trim()
  const nestedSources = [value.meta, value.metadata, value.configurable, value.stream_event]
  for (const source of nestedSources) {
    const nestedThreadId = getThreadIdFromObject(source)
    if (nestedThreadId) return nestedThreadId
  }
  return ''
}

const resolveChunkThreadId = ({ envelope, payload, chunk, fallbackThreadId }) => {
  return (
    getThreadIdFromObject(envelope) ||
    getThreadIdFromObject(payload) ||
    getThreadIdFromObject(chunk) ||
    fallbackThreadId
  )
}

export const processRunSseResponse = async (response, onEvent) => {
  if (!response || !response.body) return
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let eventType = 'message'
  let eventId = null
  let dataLines = []

  const dispatch = () => {
    if (dataLines.length === 0) return
    const dataText = dataLines.join('\n')
    try {
      const parsed = JSON.parse(dataText)
      onEvent(eventType, parsed, eventId)
    } catch (e) {
      console.warn('Failed to parse run SSE data:', e, dataText)
    }
  }

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const rawLine of lines) {
        const line = rawLine.replace(/\r$/, '')
        if (!line) {
          dispatch()
          eventType = 'message'
          eventId = null
          dataLines = []
          continue
        }

        if (line.startsWith(':')) {
          continue
        }
        if (line.startsWith('event:')) {
          eventType = line.slice(6).trim() || 'message'
        } else if (line.startsWith('data:')) {
          dataLines.push(line.slice(5).trimStart())
        } else if (line.startsWith('id:')) {
          eventId = line.slice(3).trim()
        }
      }
    }

    dispatch()
  } finally {
    try {
      reader.releaseLock()
    } catch {
      // ignore
    }
  }
}

export function useAgentRunStream({
  getThreadState,
  currentAgentId,
  handleStreamChunk,
  fetchThreadMessages,
  fetchAgentState,
  resetOnGoingConv,
  onScrollToBottom,
  streamSmoother,
  onInterruptDetected = null,
  onTerminalDetected = null,
  onRunStarted = null
}) {
  const saveActiveRunSnapshot = (threadId, runId, lastSeq = '0-0') => {
    if (!threadId || !runId) return
    localStorage.setItem(
      getActiveRunStorageKey(threadId),
      JSON.stringify({
        run_id: runId,
        last_seq: normalizeRunSeq(lastSeq),
        created_at: Date.now(),
        client_id: ACTIVE_RUN_CLIENT_ID
      })
    )
  }

  const loadActiveRunSnapshot = (threadId) => {
    if (!threadId) return null
    try {
      const raw = localStorage.getItem(getActiveRunStorageKey(threadId))
      return raw ? JSON.parse(raw) : null
    } catch {
      return null
    }
  }

  const clearActiveRunSnapshot = (threadId) => {
    if (!threadId) return
    localStorage.removeItem(getActiveRunStorageKey(threadId))
  }

  const stopRunStreamSubscription = (threadId) => {
    const ts = getThreadState(threadId)
    if (!ts) return
    streamSmoother?.flushThread(threadId)
    if (ts.runReconnectTimer) {
      clearTimeout(ts.runReconnectTimer)
      ts.runReconnectTimer = null
    }
    if (ts.runStreamAbortController) {
      ts.runStreamAbortController.abort()
      ts.runStreamAbortController = null
    }
  }

  const notifyInterruptDetected = (threadId, runId, run = null) => {
    if (typeof onInterruptDetected !== 'function') return
    onInterruptDetected({ threadId, runId, run })
  }

  const notifyTerminalDetected = (threadId, runId, touchedThreadIds) => {
    if (typeof onTerminalDetected !== 'function') return
    onTerminalDetected({ threadId, runId, touchedThreadIds: [...touchedThreadIds] })
  }

  const hasPendingInterruptForRun = (threadState, runId) => {
    const pendingInterrupt = threadState?.pendingInterrupt
    if (!hasPendingInterruptPayload(pendingInterrupt)) return false
    return !pendingInterrupt.interruptedRunId || pendingInterrupt.interruptedRunId === runId
  }

  const hasPendingInterruptInThreads = (threadIds, runId) => {
    return [...threadIds].some((id) => hasPendingInterruptForRun(getThreadState(id), runId))
  }

  const clearPendingInterruptForRun = (threadId, runId) => {
    const threadState = getThreadState(threadId)
    if (hasPendingInterruptForRun(threadState, runId)) {
      threadState.pendingInterrupt = null
    }
  }

  const resolveRunSteerable = async (run) => {
    if (!run?.request_id || run.status !== 'running' || run.run_type !== 'chat') return false
    try {
      const response = await agentApi.getRequest(run.request_id)
      return response?.request?.source === 'chat'
    } catch {
      return false
    }
  }

  const finalizeRunStream = (
    threadId,
    runId,
    touchedThreadIds,
    { delay = 200, scroll = false, status = '' } = {}
  ) => {
    const ts = getThreadState(threadId)
    if (!ts || ts.activeRunId !== runId) return
    const isInterrupted =
      status === RUN_INTERRUPTED_STATUS && hasPendingInterruptInThreads(touchedThreadIds, runId)
    touchedThreadIds.forEach((id) => streamSmoother?.flushThread(id))
    ts.isStreaming = false
    ts.activeRunSteerable = false
    if (isInterrupted) {
      ts.activeRunId = runId
      saveActiveRunSnapshot(threadId, runId, ts.runLastSeq)
    } else {
      ts.activeRunId = null
      clearActiveRunSnapshot(threadId)
      touchedThreadIds.forEach((id) => clearPendingInterruptForRun(id, runId))
    }
    ts.lastRetryableJobTry = null
    ts.replyLoadingVisible = false
    ts.pendingRequestId = null
    fetchThreadMessages({ agentId: unref(currentAgentId), threadId, delay }).finally(() => {
      const latest = getThreadState(threadId)
      if (!latest?.activeRunId || latest.activeRunId === runId) {
        resetOnGoingConv(threadId, { preserveRequestStreams: true })
      }
      fetchAgentState(unref(currentAgentId), threadId)
      if (scroll) onScrollToBottom()
      if (isInterrupted) {
        notifyInterruptDetected(threadId, runId)
      } else {
        notifyTerminalDetected(threadId, runId, touchedThreadIds)
      }
    })
  }

  const preserveInterruptedRun = async (threadId, run, snapshot = null) => {
    const ts = getThreadState(threadId)
    if (!ts || !run?.id) return false

    streamSmoother?.flushThread(threadId)
    ts.activeRunId = run.id
    ts.activeRunSteerable = false
    ts.runLastSeq = normalizeRunSeq(snapshot?.last_seq || ts.runLastSeq || '0-0')
    ts.lastRetryableJobTry = null
    ts.isStreaming = false
    ts.replyLoadingVisible = false
    ts.pendingRequestId = null
    saveActiveRunSnapshot(threadId, run.id, ts.runLastSeq)

    try {
      await fetchThreadMessages({ agentId: unref(currentAgentId), threadId })
    } catch (e) {
      console.warn('Failed to refresh messages for interrupted run:', threadId, e)
    }
    fetchAgentState(unref(currentAgentId), threadId)
    notifyInterruptDetected(threadId, run.id, run)
    return true
  }

  const scheduleRunReconnect = (threadId, runId, delay = 500) => {
    const ts = getThreadState(threadId)
    if (!ts || ts.activeRunId !== runId) return
    if (ts.runReconnectTimer) return
    ts.runReconnectAttempt = Number(ts.runReconnectAttempt || 0) + 1
    const retryDelay = Math.min(
      Math.max(delay, 500) * 2 ** Math.min(ts.runReconnectAttempt - 1, 4),
      RUN_RECONNECT_MAX_DELAY_MS
    )
    ts.runConnectionStatus = 'reconnecting'
    ts.runReconnectTimer = setTimeout(() => {
      ts.runReconnectTimer = null
      const latest = getThreadState(threadId)
      if (latest?.activeRunId === runId && !latest.runStreamAbortController) {
        void startRunStream(threadId, runId, latest.runLastSeq)
      }
    }, retryDelay)
  }

  const startRunStream = async (threadId, runId, afterSeq = '0-0', options = {}) => {
    if (!threadId || !runId) return
    const ts = getThreadState(threadId)
    if (!ts) return

    const isSameRun = ts.activeRunId === runId
    // 页面切换、历史会话恢复和状态轮询可能同时尝试恢复同一个 Run。
    // 同一 Run 只允许一个 SSE 订阅，否则后启动的订阅会取消前一个订阅，
    // 但后台 Run 仍会继续执行，最终造成前端一直显示 loading。
    if (isSameRun && ts.runStreamAbortController) return
    const initialSteerable = options.steerable ?? (isSameRun && ts.activeRunSteerable === true)
    stopRunStreamSubscription(threadId)
    const runController = new AbortController()
    ts.runStreamAbortController = runController
    ts.activeRunId = runId
    ts.activeRunSteerable = initialSteerable
    ts.runLastSeq = normalizeRunSeq(afterSeq)
    ts.runConnectionStatus = 'connecting'
    ts.runLastError = ''
    ts.runFailureMessage = ''
    const continuingSameRun = isSameRun && normalizeRunSeq(afterSeq) !== '0-0'
    if (!continuingSameRun) {
      ts.traceEvents = []
      ts.runtimeSnapshot = null
      ts.runtimeDiagnostics = []
    }
    ts.lastRetryableJobTry = null
    ts.isStreaming = true
    saveActiveRunSnapshot(threadId, runId, ts.runLastSeq)
    if (typeof onRunStarted === 'function') {
      onRunStarted({ threadId, runId })
    }
    const touchedThreadIds = new Set([threadId])
    let sawTerminalEvent = false

    try {
      const response = await agentApi.streamAgentRunEvents(runId, ts.runLastSeq, {
        signal: runController.signal
      })
      if (!response.ok) {
        throw new Error(`SSE response not ok: ${response.status}`)
      }
      ts.runConnectionStatus = 'connected'
      ts.runReconnectAttempt = 0

      await processRunSseResponse(response, (event, data, eventId) => {
        if (!data || ts.activeRunId !== runId) return

        if (eventId) {
          const incomingSeq = normalizeRunSeq(eventId)
          if (compareRunSeq(incomingSeq, ts.runLastSeq) <= 0) return
          ts.runLastSeq = incomingSeq
          saveActiveRunSnapshot(threadId, runId, incomingSeq)
        }

        const payload = data.payload || {}
        if (event === 'runtime_snapshot') {
          handleStreamChunk({
            status: 'runtime_snapshot',
            snapshot: payload.snapshot,
            runtime_snapshot: payload.snapshot,
            run_id: runId,
            thread_id: threadId
          }, threadId)
        } else if (event === 'runtime_diagnostic') {
          handleStreamChunk({
            status: 'runtime_diagnostic',
            diagnostic: payload,
            ...payload,
            run_id: runId,
            thread_id: threadId
          }, threadId)
        }
        if (event === 'metadata') {
          ts.activeRunSteerable = isSteerableMainChatRun({
            status: 'running',
            run_type: payload.run_type,
            source: payload.source
          })
        }
        const terminalStatus = event === 'end' ? payload.status : data.status
        const isRetryableError =
          event === 'error' && (payload?.retryable === true || payload?.chunk?.retryable === true)
        if (isRetryableError) {
          const parsedJobTry = Number.parseInt(payload?.chunk?.job_try, 10)
          const retryJobTry = Number.isNaN(parsedJobTry) ? null : parsedJobTry
          if (retryJobTry !== null && ts.lastRetryableJobTry === retryJobTry) {
            return
          }
          ts.lastRetryableJobTry = retryJobTry
          console.warn('Run encountered retryable error, waiting for worker retry', {
            threadId,
            runId,
            retryJobTry,
            errorType: payload?.chunk?.error_type
          })
          return
        }

        if (Array.isArray(payload.items)) {
          payload.items.forEach((chunk) => {
            const routeThreadId = resolveChunkThreadId({
              envelope: data,
              payload,
              chunk,
              fallbackThreadId: threadId
            })
            touchedThreadIds.add(routeThreadId)
            handleStreamChunk(
              {
                ...chunk,
                request_id: chunk.request_id || data.request_id,
                run_id: chunk.run_id || data.run_id || runId,
                thread_id: routeThreadId
              },
              routeThreadId
            )
          })
        } else if (payload.chunk) {
          const routeThreadId = resolveChunkThreadId({
            envelope: data,
            payload,
            chunk: payload.chunk,
            fallbackThreadId: threadId
          })
          touchedThreadIds.add(routeThreadId)
          handleStreamChunk(
            {
              ...payload.chunk,
              request_id: payload.chunk.request_id || data.request_id,
              run_id: payload.chunk.run_id || data.run_id || runId,
              thread_id: routeThreadId
            },
            routeThreadId
          )
        }

        if (event === 'end') {
          sawTerminalEvent = true
          if (terminalStatus === 'failed') {
            ts.runFailureMessage =
              resolveFailureMessage(payload) ||
              resolveFailureMessage(payload.chunk) ||
              ts.runFailureMessage ||
              'Agent 执行失败'
          }
          if (terminalStatus === RUN_INTERRUPTED_STATUS) {
            finalizeRunStream(threadId, runId, touchedThreadIds, { status: terminalStatus })
          } else if (RUN_TERMINAL_STATUSES.has(terminalStatus)) {
            finalizeRunStream(threadId, runId, touchedThreadIds, { status: terminalStatus })
          } else {
            touchedThreadIds.forEach((id) => streamSmoother?.flushThread(id))
            ts.isStreaming = false
          }
        }

        if (event === 'error') {
          sawTerminalEvent = true
          ts.runFailureMessage =
            resolveFailureMessage(payload) ||
            resolveFailureMessage(payload.chunk) ||
            ts.runFailureMessage ||
            'Agent 执行失败'
          finalizeRunStream(threadId, runId, touchedThreadIds, { delay: 300, scroll: true })
        }
      })

      if (!sawTerminalEvent && !runController.signal.aborted && ts.activeRunId === runId) {
        try {
          const runRes = await agentApi.getAgentRun(runId)
          const run = runRes
          if (run?.status === RUN_INTERRUPTED_STATUS) {
            if (hasPendingInterruptInThreads(touchedThreadIds, run.id)) {
              await preserveInterruptedRun(threadId, run)
            } else {
              finalizeRunStream(threadId, runId, touchedThreadIds, { status: run.status })
            }
          } else if (run && RUN_TERMINAL_STATUSES.has(run.status)) {
            if (run.status === 'failed') {
              ts.runFailureMessage = resolveFailureMessage(run) || 'Agent 执行失败'
            }
            finalizeRunStream(threadId, runId, touchedThreadIds, { status: run.status })
          } else {
            scheduleRunReconnect(threadId, runId)
          }
        } catch (e) {
          console.warn(
            'Run SSE closed before terminal event; reconnecting after status check failed:',
            e
          )
          scheduleRunReconnect(threadId, runId)
        }
      }
    } catch (error) {
      if (error?.name !== 'AbortError') {
        streamSmoother?.flushThread(threadId)
        ts.runConnectionStatus = 'disconnected'
        ts.runLastError = error?.message || '连接中断'
        console.error('Run SSE stream error; run will be resumed from persisted cursor:', error)
        // SSE 断开不代表后台 Run 失败。查询持久化状态，只有未结束时才退避重连。
        try {
          const runRes = await agentApi.getAgentRun(runId)
          const run = runRes
          if (run?.status === RUN_INTERRUPTED_STATUS) {
            if (hasPendingInterruptInThreads(touchedThreadIds, run.id)) {
              await preserveInterruptedRun(threadId, run)
            } else {
              finalizeRunStream(threadId, runId, touchedThreadIds, { status: run.status })
            }
          } else if (run && RUN_TERMINAL_STATUSES.has(run.status)) {
            if (run.status === 'failed') {
              ts.runFailureMessage = resolveFailureMessage(run) || 'Agent 执行失败'
            }
            finalizeRunStream(threadId, runId, touchedThreadIds, { status: run.status })
          } else {
            scheduleRunReconnect(threadId, runId)
          }
        } catch (statusError) {
          console.warn('Run status check failed while recovering SSE:', statusError)
          scheduleRunReconnect(threadId, runId)
        }
      }
    } finally {
      if (ts.runStreamAbortController === runController) {
        ts.runStreamAbortController = null
      }
      if (!ts.activeRunId) {
        ts.isStreaming = false
        ts.replyLoadingVisible = false
        ts.pendingRequestId = null
      }
    }
  }

  const resumeActiveRunForThread = async (threadId) => {
    if (!threadId) return
    const ts = getThreadState(threadId)
    if (!ts) return

    if (ts.runStreamAbortController) {
      if (!ts.activeRunId) return
      try {
        const runRes = await agentApi.getAgentRun(ts.activeRunId)
        const run = runRes
        if (run?.status === RUN_INTERRUPTED_STATUS) {
          stopRunStreamSubscription(threadId)
          const snapshot = loadActiveRunSnapshot(threadId)
          if (hasPendingInterruptForRun(ts, run.id)) {
            await preserveInterruptedRun(threadId, run, snapshot)
          } else {
            resetOnGoingConv(threadId)
            await startRunStream(threadId, run.id, '0-0')
          }
        } else if (run && RUN_TERMINAL_STATUSES.has(run.status)) {
          stopRunStreamSubscription(threadId)
          ts.activeRunId = null
          ts.activeRunSteerable = false
          ts.isStreaming = false
          ts.replyLoadingVisible = false
          ts.pendingRequestId = null
          clearPendingInterruptForRun(threadId, run.id)
          clearActiveRunSnapshot(threadId)
          notifyTerminalDetected(threadId, run.id, new Set([threadId]))
        }
      } catch (e) {
        console.warn('Failed to refresh active run while stream is open:', threadId, e)
      }
      return
    }

    const snapshot = loadActiveRunSnapshot(threadId)
    if (snapshot?.run_id) {
      if (Date.now() - Number(snapshot.created_at || 0) > ACTIVE_RUN_STORAGE_TTL_MS) {
        clearActiveRunSnapshot(threadId)
      } else {
        try {
          const runRes = await agentApi.getAgentRun(snapshot.run_id)
          const run = runRes
          if (run?.status === RUN_INTERRUPTED_STATUS) {
            // 仅当本地仍持有该中断时才据快照恢复；否则不能仅凭快照重放旧中断
            // （可能已被回复），交由下方 active_run 做权威判定。
            if (hasPendingInterruptForRun(ts, run.id)) {
              await preserveInterruptedRun(threadId, run, snapshot)
              return
            }
          } else if (run && !RUN_TERMINAL_STATUSES.has(run.status)) {
            const afterSeq = resolveRunResumeAfterSeq({
              snapshot,
              threadState: ts
            })
            if (afterSeq === '0-0') {
              resetOnGoingConv(threadId)
            }
            await startRunStream(threadId, run.id, afterSeq, {
              steerable: await resolveRunSteerable(run)
            })
            return
          }
        } catch {
          // ignore
        }
        clearActiveRunSnapshot(threadId)
      }
    }

    try {
      const active = await agentApi.getThreadActiveRun(threadId)
      const run = active?.run
      if (run?.status === RUN_INTERRUPTED_STATUS) {
        if (hasPendingInterruptForRun(ts, run.id)) {
          await preserveInterruptedRun(threadId, run)
          return
        }
        resetOnGoingConv(threadId)
        await startRunStream(threadId, run.id, '0-0', {
          steerable: await resolveRunSteerable(run)
        })
        return
      }
      if (run && !RUN_TERMINAL_STATUSES.has(run.status)) {
        resetOnGoingConv(threadId)
        await startRunStream(threadId, run.id, '0-0')
        return
      }
    } catch (e) {
      console.warn('Failed to load active run for thread:', threadId, e)
    }

    ts.activeRunId = null
    ts.activeRunSteerable = false
    ts.runLastSeq = '0-0'
    ts.runConnectionStatus = 'idle'
    ts.runReconnectAttempt = 0
    ts.runLastError = ''
    ts.isStreaming = false
    ts.replyLoadingVisible = false
    ts.pendingRequestId = null
    ts.pendingInterrupt = null
    clearActiveRunSnapshot(threadId)
    notifyTerminalDetected(threadId, null, new Set([threadId]))
  }

  return {
    startRunStream,
    resumeActiveRunForThread,
    stopRunStreamSubscription,
    retryRunStream: async (threadId) => {
      const ts = getThreadState(threadId)
      if (!ts?.activeRunId) return false
      stopRunStreamSubscription(threadId)
      await startRunStream(threadId, ts.activeRunId, ts.runLastSeq, {
        steerable: ts.activeRunSteerable
      })
      return true
    }
  }
}
