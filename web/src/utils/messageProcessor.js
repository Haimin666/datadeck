/**
 * datadeck 事件 → 消息处理
 * 事件类型由 event_translator.py 定义：
 *   init / loading / stream_event(message_delta|tool_call|tool_call_delta)
 *   / error / human_approval_required / agent_state / context_compression
 *   / finished / interrupted / warning / end
 */

const EVENT_TYPES = {
  MESSAGE_DELTA: 'message_delta',
  TOOL_CALL: 'tool_call',
  TOOL_CALL_DELTA: 'tool_call_delta',
  ERROR: 'error',
  HUMAN_APPROVAL_REQUIRED: 'human_approval_required',
  AGENT_STATE: 'agent_state',
  CONTEXT_COMPRESSION: 'context_compression',
  FINISHED: 'finished',
  INTERRUPTED: 'interrupted',
  WARNING: 'warning',
  END: 'end',
  INIT: 'init',
  LOADING: 'loading'
}

/**
 * 处理 SSE 事件，更新 threadState
 * @param {Object} threadState - 当前线程状态
 * @param {Object} event - SSE 事件 { event, payload }
 * @param {Function} onUpdate - 状态更新回调
 */
export function processSSEEvent(threadState, event, onUpdate) {
  const { event: eventType, payload } = event
  if (!eventType || !payload) return

  switch (eventType) {
    case EVENT_TYPES.INIT:
      onUpdate({ init: payload })
      break

    case EVENT_TYPES.LOADING:
      onUpdate({ replyLoading: true })
      break

    case EVENT_TYPES.MESSAGE_DELTA: {
      const { message, delta } = payload
      onUpdate({
        streaming: true,
        messageDelta: { message, delta }
      })
      break
    }

    case EVENT_TYPES.TOOL_CALL: {
      const { tool_call } = payload
      onUpdate({
        streaming: true,
        toolCall: tool_call
      })
      break
    }

    case EVENT_TYPES.TOOL_CALL_DELTA: {
      const { tool_call_delta } = payload
      onUpdate({
        streaming: true,
        toolCallDelta: tool_call_delta
      })
      break
    }

    case EVENT_TYPES.ERROR:
      onUpdate({ error: payload, streaming: false, replyLoading: false })
      break

    case EVENT_TYPES.HUMAN_APPROVAL_REQUIRED: {
      const { interrupt } = payload
      onUpdate({
        interrupt,
        streaming: false,
        replyLoading: false
      })
      break
    }

    case EVENT_TYPES.AGENT_STATE: {
      const { state } = payload
      onUpdate({ agentState: state, streaming: false, replyLoading: false })
      break
    }

    case EVENT_TYPES.FINISHED: {
      const { run } = payload
      onUpdate({
        run,
        streaming: false,
        replyLoading: false,
        finished: true
      })
      break
    }

    case EVENT_TYPES.INTERRUPTED: {
      const { run } = payload
      onUpdate({
        run,
        interrupted: true,
        streaming: false,
        replyLoading: false
      })
      break
    }

    case EVENT_TYPES.WARNING:
      onUpdate({ warning: payload })
      break

    case EVENT_TYPES.END: {
      const { run } = payload
      onUpdate({ run, streaming: false, replyLoading: false })
      break
    }

    case EVENT_TYPES.CONTEXT_COMPRESSION:
      onUpdate({ contextCompressed: payload })
      break

    default:
      console.warn('未知 SSE 事件类型:', eventType, payload)
  }
}

export { EVENT_TYPES }
