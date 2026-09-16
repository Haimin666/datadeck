import { agentApi } from '@/apis'
import { processRunSseResponse } from '@/composables/useAgentRunStream'
import { IDLE_QUEUE_SNAPSHOT } from '@/composables/useAgentThreadState'
import { handleChatError } from '@/utils/errorHandler'

export function useAgentRequestQueue({
  getThreadState,
  resetOnGoingConv,
  startRunStream,
  onStreamError
}) {
  const removeRequestFromQueue = (ts, requestId) => {
    if (!ts || !ts.queuedRequests) return
    ts.queuedRequests = ts.queuedRequests.filter((r) => r.request_id !== requestId)
  }

  const stopRequestStream = (threadId, requestId) => {
    const ts = getThreadState(threadId)
    const entry = ts?.requestStreams?.[requestId]
    if (!entry) return
    entry.controller?.abort()
    if (entry.retryTimer) clearTimeout(entry.retryTimer)
    delete ts.requestStreams[requestId]
  }

  const stopAllRequestStreams = (threadId) => {
    const ts = getThreadState(threadId)
    if (!ts?.requestStreams) return
    for (const rid of Object.keys(ts.requestStreams)) {
      stopRequestStream(threadId, rid)
    }
  }

  const cancelRequest = async (threadId, requestId) => {
    const ts = getThreadState(threadId)
    if (!ts || !requestId) return false
    try {
      await agentApi.cancelRequest(requestId)
      stopRequestStream(threadId, requestId)
      removeRequestFromQueue(ts, requestId)
      if (ts.onGoingConv?.msgChunks) {
        delete ts.onGoingConv.msgChunks[requestId]
      }
      ts.queuedMessageProjections = (ts.queuedMessageProjections || []).filter(
        (message) => message?.extra_metadata?.request_id !== requestId
      )
      return true
    } catch (error) {
      if (error?.name !== 'AbortError') {
        handleChatError(error, 'cancel')
      }
      return false
    }
  }

  const syncQueuedRequests = async (threadId, agentSlug) => {
    const ts = getThreadState(threadId)
    if (!ts) return
    try {
      const resp = await agentApi.listThreadQueuedRequests(threadId, agentSlug)
      ts.queuedRequests = resp?.requests || []
      ts.queueSnapshot = resp?.queue || { ...IDLE_QUEUE_SNAPSHOT }
    } catch (e) {
      console.warn('Failed to sync queued requests:', e)
    }
  }

  const startRequestStream = async (threadId, requestId) => {
    if (!threadId || !requestId) return
    const ts = getThreadState(threadId)
    if (!ts) return

    ts.requestStreams = ts.requestStreams || {}
    if (ts.requestStreams[requestId]) return

    const controller = new AbortController()
    const entry = { controller, position: 0, status: 'queued', retryTimer: null, retries: 0 }
    ts.requestStreams[requestId] = entry

    try {
      const response = await agentApi.streamRequestEvents(requestId, {
        signal: controller.signal
      })
      if (!response.ok) {
        throw new Error(`Request SSE response not ok: ${response.status}`)
      }

      const handleEvent = (event, data) => {
        // 一次性取 ts/entry，避免每个分支重复 getThreadState 触发响应式追踪。
        const tsInner = getThreadState(threadId)
        const innerEntry = tsInner?.requestStreams?.[requestId]
        if (!tsInner || innerEntry?.controller !== controller) return
        // 所有队列 SSE 事件统一使用 { event, payload } 信封。
        const eventPayload = data?.payload || {}

        if (event === 'queued' && data) {
          entry.position = eventPayload.position || entry.position
          const queuedRequest = tsInner.queuedRequests?.find((r) => r.request_id === requestId)
          if (queuedRequest) queuedRequest.queue_position = entry.position
        } else if (event === 'run_created' && data) {
          entry.status = 'dispatched'
          if (eventPayload.run_id) {
            removeRequestFromQueue(tsInner, requestId)
            stopRequestStream(threadId, requestId)

            // 旧 Run 尚未 finalize 时保留已渲染内容；startRunStream 会 flush 并中止旧订阅。
            // 若旧 Run 已 finalize，则其 history 刷新已在途，可以清理残留的 ongoing 状态。
            if (!tsInner.activeRunId) {
              resetOnGoingConv(threadId, { preserveRequestStreams: true })
            }
            tsInner.pendingRequestId = requestId
            void startRunStream(threadId, eventPayload.run_id, '0-0')
          }
        } else if (event === 'cancelled' || event === 'rejected' || event === 'failed') {
          entry.status = event
          tsInner.isStreaming = false
          tsInner.replyLoadingVisible = false
          tsInner.pendingRequestId = null
          delete tsInner.onGoingConv.msgChunks[requestId]
          removeRequestFromQueue(tsInner, requestId)
          stopRequestStream(threadId, requestId)
          if (typeof onStreamError === 'function') {
            onStreamError(threadId, requestId, event)
          }
        }
      }

      await processRunSseResponse(response, handleEvent)
    } catch (error) {
      if (error?.name !== 'AbortError') {
        console.error('Request SSE stream error:', error)
        // 队列事件流断开时，请求通常仍停留在服务端队列；重新同步后再恢复监听。
        const current = getThreadState(threadId)?.requestStreams?.[requestId]
        if (current?.controller === controller) {
          delete current.controller
          current.retries = (current.retries || 0) + 1
          current.retryTimer = setTimeout(() => {
            const latest = getThreadState(threadId)
            if (!latest?.requestStreams?.[requestId]) return
            delete latest.requestStreams[requestId]
            void startRequestStream(threadId, requestId)
          }, Math.min(1000 * 2 ** Math.min(current.retries - 1, 4), 10000))
        }
      }
    } finally {
      const tsFinal = getThreadState(threadId)
      if (tsFinal?.requestStreams?.[requestId]?.controller === controller) {
        delete tsFinal.requestStreams[requestId]
      }
    }
  }

  const continueQueue = async (threadId, agentSlug) => {
    const ts = getThreadState(threadId)
    if (!ts || !threadId || !agentSlug || ts.continueQueueInFlight) return false

    ts.continueQueueInFlight = true
    try {
      const response = await agentApi.continueThreadQueue(threadId, agentSlug)
      await syncQueuedRequests(threadId, agentSlug)
      if (response?.request_id) {
        void startRequestStream(threadId, response.request_id)
      }
      return true
    } catch (error) {
      handleChatError(error, 'continue_queue')
      return false
    } finally {
      ts.continueQueueInFlight = false
    }
  }

  const steerRequest = async (threadId, agentSlug, requestId) => {
    const ts = getThreadState(threadId)
    if (!ts || !threadId || !agentSlug || !requestId) return false

    try {
      await agentApi.steerRequest(requestId)
      await syncQueuedRequests(threadId, agentSlug)
      void startRequestStream(threadId, requestId)
      return true
    } catch (error) {
      handleChatError(error, 'steer')
      return false
    }
  }

  return {
    startRequestStream,
    stopAllRequestStreams,
    cancelRequest,
    syncQueuedRequests,
    continueQueue,
    steerRequest
  }
}
