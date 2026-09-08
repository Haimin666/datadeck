<script setup>
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { ChevronDown, Trash2, ListCollapse, RefreshCw } from '@lucide/vue'
import AgentInputArea from './AgentInputArea.vue'
import AgentMessageComponent from './AgentMessageComponent.vue'
import ToolCallsGroupComponent from './ToolCallsGroupComponent.vue'
import AgentArtifactsCard from './AgentArtifactsCard.vue'
import HumanApprovalModal from './HumanApprovalModal.vue'
import ConversationNavSection from './ConversationNavSection.vue'
import { agentApi } from '@/apis'
import { useAgentStore } from '@/stores/agent'
import { useThreadStore } from '@/stores/thread'
import { useUserStore } from '@/stores/user'
import { useThemeStore } from '@/stores/theme'
import { handleChatError } from '@/utils/errorHandler'
import { isTerminalStatus, isInterruptedStatus } from '@/utils/agentRun'
import { normalizeRunSeq } from '@/utils/runStreamResume'
import { scrollToBottom } from '@/utils/logScroll'

const props = defineProps({
  selectedAgentId: { type: String, default: '' },
  singleMode: { type: Boolean, default: false }
})
const emit = defineEmits(['thread-change'])

const route = useRoute()
const router = useRouter()
const agentStore = useAgentStore()
const threadStore = useThreadStore()
const userStore = useUserStore()
const themeStore = useThemeStore()

// ─── 状态 ────────────────────────────────────────────────
const userInput = ref('')
const isLoadingMessages = ref(false)
const conversations = ref([])
const isStreaming = ref(false)
const replyLoading = ref(false)
const replyLoadingText = ref('思考中...')
const replyElapsedMs = ref(0)
const currentChatId = ref(route.params.thread_id || '')
const activeRunId = ref(null)
const lastSeq = ref('0-0')
const interrupted = ref(false)
const approvalModalVisible = ref(false)
const approvalQuestions = ref([])
const approvalState = ref({ kind: '', actionRequests: [] })
const statePanelOpen = ref(false)
const agentState = ref(null)
const tokenUsage = ref(null)
const isRefreshingState = ref(false)
const chatMainRef = ref(null)
const agentInputAreaRef = ref(null)
const threadCreationInFlight = ref(false)
const cancelInFlight = ref(false)
const sidebarOpen = ref(true)
const isSearchMode = ref(false)
const searchQuery = ref('')

let elapsedTimer = null
let streamController = null
let reconnectTimer = null

// ─── 计算属性 ─────────────────────────────────────────────
const currentThread = computed(() =>
  threadStore.threads.find(t => t.id === currentChatId.value) || null
)
const currentAgent = computed(() =>
  agentStore.agents.find(a => a.id === props.selectedAgentId) || agentStore.agents[0] || null
)
const currentThreadAgentName = computed(() => currentAgent.value?.name || currentAgent.value?.slug || '默认智能体')
const isSendButtonDisabled = computed(() => !userInput.value.trim() || isStreaming.value || threadCreationInFlight.value)
const shouldShowStopButton = computed(() => isStreaming.value)
const supportsFileUpload = computed(() => true)

const greetingTexts = [
  '你好，有什么可以帮你的？',
  '欢迎使用 DataDeck',
  '今天想解决什么问题？',
  '准备好开始了吗？'
]
const randomGreeting = computed(() => greetingTexts[Math.floor(Math.random() * greetingTexts.length)])

const displayThreads = computed(() => {
  if (isSearchMode.value && searchQuery.value.trim()) {
    return threadStore.threads.filter(t =>
      (t.title || '').toLowerCase().includes(searchQuery.value.toLowerCase())
    )
  }
  return threadStore.threads
})

// ─── 路由同步 ─────────────────────────────────────────────
watch(() => route.params.thread_id, async (newId) => {
  if (newId) {
    await selectThread(newId)
  }
}, { immediate: true })

// ─── 线程操作 ─────────────────────────────────────────────
async function selectThread(threadId) {
  currentChatId.value = threadId
  stopStream()
  clearInterval(elapsedTimer)
  replyElapsedMs.value = 0
  conversations.value = []
  activeRunId.value = null
  lastSeq.value = '0-0'
  interrupted.value = false
  agentState.value = null
  tokenUsage.value = null
  statePanelOpen.value = false

  if (!threadId) return

  isLoadingMessages.value = true
  try {
    const data = await agentApi.getAgentHistory(threadId)
    const msgs = data?.messages || data?.threads?.[0]?.messages || []
    conversations.value = msgs.map(normalizeMessage)
    lastSeq.value = normalizeRunSeq(data?.last_seq || '0-0')
    await nextTick()
    scrollToBottom(chatMainRef.value)
  } catch (e) {
    conversations.value = []
  } finally {
    isLoadingMessages.value = false
  }

  threadStore.markThreadViewed(threadId)
  emit('thread-change', threadId)
}

async function createNewThread() {
  const agentId = props.selectedAgentId || agentStore.agents[0]?.id
  if (!agentId) { message.warning('请先选择智能体'); return }
  threadCreationInFlight.value = true
  try {
    const data = await agentApi.createThread(agentId, '新的对话')
    const thread = data?.thread || data
    if (thread) {
      threadStore.threads.unshift(thread)
      await selectThread(thread.id)
      router.replace({ name: 'AgentCompWithThreadId', params: { thread_id: thread.id } })
    }
  } catch (e) {
    handleChatError(e, '创建对话')
  } finally {
    threadCreationInFlight.value = false
  }
}

async function deleteCurrentThread() {
  if (!currentChatId.value) return
  const threadId = currentChatId.value
  await threadStore.deleteThread(threadId)
  conversations.value = []
  activeRunId.value = null
  currentChatId.value = ''
  if (threadStore.threads.length > 0) {
    await selectThread(threadStore.threads[0].id)
    router.replace({ name: 'AgentComp' })
  } else {
    await createNewThread()
  }
}

// ─── 消息发送 ──────────────────────────────────────────────
async function handleSendOrStop() {
  if (isStreaming.value) { await cancelRun(); return }
  const query = userInput.value.trim()
  if (!query) return
  const agentId = props.selectedAgentId || agentStore.agents[0]?.id
  if (!agentId) { message.warning('请先选择智能体'); return }

  let threadId = currentChatId.value
  if (!threadId) {
    threadCreationInFlight.value = true
    try {
      const data = await agentApi.createThread(agentId, query.slice(0, 30))
      const thread = data?.thread || data
      if (thread) {
        threadStore.threads.unshift(thread)
        threadId = thread.id
        await selectThread(threadId)
        router.replace({ name: 'AgentCompWithThreadId', params: { thread_id: threadId } })
      }
    } finally {
      threadCreationInFlight.value = false
    }
    if (!threadId) return
  }

  userInput.value = ''
  await startRun(threadId, query)
}

async function startRun(threadId, query) {
  isStreaming.value = true
  replyLoading.value = true
  replyElapsedMs.value = 0
  startElapsedTimer()
  try {
    const runData = await agentApi.createAgentRun({
      query,
      agent_slug: props.selectedAgentId,
      thread_id: threadId,
      queue_policy: 'enqueue'
    })
    const run = runData?.run || runData
    if (!run?.id) throw new Error('未获取到 run_id')
    activeRunId.value = run.id
    lastSeq.value = '0-0'
    await connectSSE(threadId, run.id)
  } catch (e) {
    isStreaming.value = false
    replyLoading.value = false
    handleChatError(e, '发送消息')
  }
}

// ─── SSE 连接 ──────────────────────────────────────────────
async function connectSSE(threadId, runId) {
  if (streamController) streamController.abort()
  streamController = new AbortController()

  const url = agentApi.getAgentRunEventsUrl(runId)
  const headers = {
    ...userStore.getAuthHeaders(),
    ...(lastSeq.value !== '0-0' ? { 'Last-Event-ID': lastSeq.value } : {})
  }

  try {
    const response = await fetch(url, { headers, signal: streamController.signal })
    if (!response.ok) throw new Error(`SSE 请求失败: ${response.status}`)

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        const trimmed = line.replace(/\r$/, '')
        if (!trimmed || trimmed.startsWith(':')) continue
        if (trimmed.startsWith('id:')) {
          lastSeq.value = normalizeRunSeq(trimmed.slice(3).trim())
        } else if (trimmed.startsWith('data:')) {
          const dataText = trimmed.slice(5).trimStart()
          try {
            const parsed = JSON.parse(dataText)
            handleSSEEvent(threadId, parsed)
          } catch (e) {
            console.warn('SSE 数据解析失败:', e, dataText)
          }
        }
      }
    }
    await checkRunTerminal(threadId, runId)
  } catch (error) {
    if (error?.name !== 'AbortError') {
      console.error('SSE 流错误:', error)
      message.error('连接中断，请稍后重试')
    }
  } finally {
    if (streamController?.signal.aborted) return
    isStreaming.value = false
    replyLoading.value = false
    clearInterval(elapsedTimer)
    streamController = null
  }
}

function handleSSEEvent(threadId, event) {
  const { event: eventType, payload } = event
  if (!eventType || !payload) return

  switch (eventType) {
    case 'init':
      replyLoadingText.value = payload?.text || '初始化中...'
      break
    case 'loading':
      replyLoadingText.value = '思考中...'
      break
    case 'stream_event': {
      const { type, delta } = payload
      if (type === 'message_delta' && delta?.content) {
        appendStreamContent(delta.content)
      }
      break
    }
    case 'agent_state': {
      const { state } = payload
      if (state?.token_usage) tokenUsage.value = state.token_usage
      if (state?.todos) agentState.value = state
      break
    }
    case 'human_approval_required': {
      const { interrupt } = payload
      if (interrupt?.tool_names || interrupt?.tool_calls) {
        approvalState.value = interrupt
        approvalModalVisible.value = true
        replyLoading.value = false
      }
      break
    }
    case 'interrupted': {
      interrupted.value = true
      replyLoading.value = false
      isStreaming.value = false
      message.warning('任务被中断，请确认后继续')
      break
    }
    case 'error': {
      replyLoading.value = false
      isStreaming.value = false
      message.error(payload?.error?.message || '生成失败')
      break
    }
    case 'warning':
      if (payload?.warning) message.warning(payload.warning)
      break
    case 'finished':
    case 'end':
      finalizeRun(threadId, payload?.run)
      break
    default:
      console.warn('未知 SSE 事件:', eventType)
  }
}

function finalizeRun(threadId, run) {
  if (run?.status === 'completed') message.success('生成完成')
  else if (run?.status === 'failed') message.error('生成失败')
  isStreaming.value = false
  replyLoading.value = false
  clearInterval(elapsedTimer)
  activeRunId.value = null
  selectThread(threadId)
}

async function checkRunTerminal(threadId, runId) {
  try {
    const data = await agentApi.getAgentRun(runId)
    const run = data?.run || data
    if (isTerminalStatus(run?.status)) {
      finalizeRun(threadId, run)
    } else if (isInterruptedStatus(run?.status)) {
      interrupted.value = true
      message.warning('任务已中断')
    } else {
      scheduleReconnect(threadId, runId)
    }
  } catch {
    scheduleReconnect(threadId, runId)
  }
}

function scheduleReconnect(threadId, runId) {
  if (reconnectTimer) clearTimeout(reconnectTimer)
  reconnectTimer = setTimeout(async () => {
    if (streamController?.signal.aborted) return
    await connectSSE(threadId, runId)
  }, 3000)
}

// ─── 消息缓冲 ──────────────────────────────────────────────
const streamBuffer = ref('')

function appendStreamContent(content) {
  streamBuffer.value += content
  const msgs = conversations.value
  if (msgs.length > 0 && msgs[msgs.length - 1].role === 'assistant') {
    msgs[msgs.length - 1].content = streamBuffer.value
  } else {
    conversations.value.push({
      role: 'assistant', content: streamBuffer.value,
      created_at: Date.now(), is_streaming: true
    })
  }
  nextTick(() => scrollToBottom(chatMainRef.value, 'auto'))
}

// ─── 工具函数 ──────────────────────────────────────────────
function normalizeMessage(msg) {
  if (!msg) return null
  return {
    id: msg.id || msg.message_id,
    role: msg.role || (msg.type === 'assistant' ? 'assistant' : 'user'),
    content: msg.content || '',
    tool_calls: msg.tool_calls || [],
    created_at: msg.created_at || msg.timestamp || Date.now()
  }
}

function startElapsedTimer() {
  clearInterval(elapsedTimer)
  const start = Date.now()
  elapsedTimer = setInterval(() => {
    replyElapsedMs.value = Date.now() - start
  }, 500)
}

function formatElapsed(ms) {
  if (ms < 1000) return `${ms}ms`
  return `${Math.floor(ms / 1000)}s`
}

async function cancelRun() {
  if (!activeRunId.value || cancelInFlight.value) return
  cancelInFlight.value = true
  try {
    await agentApi.cancelAgentRun(activeRunId.value)
    message.info('已取消')
  } catch (e) {
    handleChatError(e, '取消运行')
  } finally {
    cancelInFlight.value = false
  }
  stopStream()
}

function stopStream() {
  if (streamController) { streamController.abort(); streamController = null }
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
  isStreaming.value = false
  replyLoading.value = false
  clearInterval(elapsedTimer)
  activeRunId.value = null
}

async function handleAgentStateRefresh() {
  if (!currentChatId.value || isRefreshingState.value) return
  isRefreshingState.value = true
  try {
    const data = await agentApi.getAgentState(currentChatId.value, { includeMessages: false })
    agentState.value = data
    if (data?.token_usage) tokenUsage.value = data.token_usage
  } catch (e) {
    handleChatError(e, '获取状态')
  } finally {
    isRefreshingState.value = false
  }
}

function toggleStatePanel() {
  statePanelOpen.value = !statePanelOpen.value
}

async function handleApprovalSubmit(approved, toolCallIds) {
  approvalModalVisible.value = false
  if (approved && activeRunId.value) {
    try {
      const data = await agentApi.createAgentRun({
        query: '', agent_slug: props.selectedAgentId,
        thread_id: currentChatId.value, resume: activeRunId.value,
        tool_approval: { approved: true, tool_call_ids: toolCallIds }
      })
      const run = data?.run || data
      if (run?.id) {
        activeRunId.value = run.id
        lastSeq.value = '0-0'
        replyLoading.value = true
        await connectSSE(currentChatId.value, run.id)
      }
    } catch (e) { handleChatError(e, '提交审批') }
  } else {
    interrupted.value = false
    replyLoading.value = false
  }
}

function handleApprovalCancel() {
  approvalModalVisible.value = false
  interrupted.value = false
  replyLoading.value = false
}

async function handleThreadSelect(thread) {
  await selectThread(thread.id)
  router.replace({ name: 'AgentCompWithThreadId', params: { thread_id: thread.id } })
}

async function handleThreadPin(thread) {
  await threadStore.updateThread(thread.id, { is_pinned: !thread.is_pinned })
}

async function handleThreadDelete(threadId) {
  await threadStore.deleteThread(threadId)
  if (threadStore.threads.length > 0) {
    const next = threadStore.threads[0]
    await selectThread(next.id)
    router.replace({ name: 'AgentCompWithThreadId', params: { thread_id: next.id } })
  } else {
    conversations.value = []
    currentChatId.value = ''
  }
}

async function handleSearch() {
  if (!searchQuery.value.trim()) {
    isSearchMode.value = false
    await threadStore.fetchThreads()
  } else {
    isSearchMode.value = true
    await threadStore.searchThreads(searchQuery.value)
  }
}

onMounted(async () => {
  if (!agentStore.isInitialized) await agentStore.initialize()
  await threadStore.fetchThreads()
  const threadId = route.params.thread_id
  if (threadId) {
    await selectThread(threadId)
  } else if (threadStore.threads.length > 0) {
    await selectThread(threadStore.threads[0].id)
  }
})

onUnmounted(() => {
  stopStream()
  clearInterval(elapsedTimer)
})
</script>

<template>
  <div class="chat-container">
    <!-- 侧边栏：对话列表 -->
    <aside class="sidebar" :class="{ collapsed: !sidebarOpen }">
      <div class="sidebar-header">
        <div class="sidebar-logo">
          <img src="/favicon.svg" alt="logo" class="logo-img" />
          <span class="logo-text" v-show="sidebarOpen">DataDeck</span>
        </div>
        <button class="sidebar-toggle" @click="sidebarOpen = !sidebarOpen" title="折叠侧边栏">
          <ChevronDown :size="16" :class="{ rotated: !sidebarOpen }" />
        </button>
      </div>

      <div class="sidebar-new-btn-wrap">
        <button class="new-thread-btn" :disabled="threadCreationInFlight" @click="createNewThread">
          + 新建对话
        </button>
      </div>

      <div class="sidebar-search">
        <input
          v-model="searchQuery"
          type="text"
          placeholder="搜索对话..."
          class="search-input"
          @input="handleSearch"
          @focus="() => { if (!searchQuery) { isSearchMode = true } }"
        />
        <button v-if="searchQuery" class="search-clear" @click="searchQuery = ''; isSearchMode = false; handleSearch()">
          ×
        </button>
      </div>

      <div class="sidebar-threads">
        <div v-if="threadStore.isLoadingThreads" class="sidebar-loading">
          <div class="spinner" />
        </div>
        <template v-else>
          <ConversationNavItem
            v-for="thread in displayThreads"
            :key="thread.id"
            :thread="thread"
            :is-active="thread.id === currentChatId"
            @select="handleThreadSelect"
            @pin="handleThreadPin"
            @delete="handleThreadDelete"
          />
        </template>
        <div v-if="!threadStore.isLoadingThreads && displayThreads.length === 0" class="sidebar-empty">
          <p>暂无对话</p>
          <button class="empty-new-btn" @click="createNewThread">+ 新建对话</button>
        </div>
      </div>
    </aside>

    <!-- 主聊天区 -->
    <div class="chat" :class="{ 'has-state-panel': statePanelOpen }">
      <div class="chat-header" :class="{ 'has-active-thread': !!currentChatId }">
        <div class="header__left">
          <button
            v-if="sidebarOpen"
            class="sidebar-open-btn"
            @click="sidebarOpen = false"
            title="展开侧边栏"
          >
            <ChevronDown :size="16" />
          </button>
          <div v-if="currentThread?.title && currentThread.title !== '新的对话'" class="conversation-title">
            {{ currentThread.title }}
          </div>
        </div>
        <div class="header__right">
          <button type="button" class="agent-nav-btn" :class="{ active: statePanelOpen }" title="查看状态" @click.stop="toggleStatePanel">
            <ListCollapse :size="16" />
          </button>
          <button v-if="currentChatId" type="button" class="agent-nav-btn" title="删除对话" @click.stop="deleteCurrentThread">
            <Trash2 :size="16" />
          </button>
        </div>
      </div>

      <div class="chat-content-container">
        <div class="chat-main" ref="chatMainRef">
          <div class="chat-box">
            <template v-for="(msg, idx) in conversations" :key="idx">
              <div class="conv-box" :class="{ 'is-streaming': msg.is_streaming }">
                <AgentMessageComponent
                  :message="msg"
                  :is-processing="isStreaming && idx === conversations.length - 1"
                  :show-refs="false"
                  :hide-tool-calls="false"
                  @retry="(m) => { userInput = m.content; handleSendOrStop() }"
                />
              </div>
            </template>

            <div class="generating-status" v-if="replyLoading && conversations.length > 0">
              <div class="generating-indicator">
                <div class="loading-dots"><div></div><div></div><div></div></div>
                <span class="generating-text">{{ replyLoadingText }}</span>
                <span v-if="replyElapsedMs" class="generating-elapsed">{{ formatElapsed(replyElapsedMs) }}</span>
              </div>
            </div>

            <div v-if="!conversations.length && !isLoadingMessages" class="chat-empty">
              <p>{{ randomGreeting }}</p>
            </div>
          </div>

          <div class="bottom" :class="{ 'start-screen': !conversations.length }">
            <div class="message-input-wrapper">
              <div v-if="isLoadingMessages" class="chat-loading">
                <div class="loading-spinner" /><span>正在加载消息...</span>
              </div>

              <div v-if="!conversations.length" class="chat-greeting-input">
                <h1>{{ randomGreeting }}</h1>
              </div>

              <div class="message-input-stage">
                <HumanApprovalModal
                  v-if="approvalModalVisible"
                  :visible="approvalModalVisible"
                  :questions="approvalQuestions"
                  :kind="approvalState.kind"
                  :action-requests="approvalState.actionRequests"
                  @submit="handleApprovalSubmit"
                  @cancel="handleApprovalCancel"
                />
                <div class="message-input-surface">
                  <AgentInputArea
                    ref="agentInputAreaRef"
                    v-model="userInput"
                    :is-loading="shouldShowStopButton"
                    :disabled="!currentAgent"
                    :send-button-disabled="isSendButtonDisabled"
                    :mention="{}"
                    :thread-id="currentChatId"
                    :supports-file-upload="supportsFileUpload"
                    @send="handleSendOrStop"
                  >
                    <template #actions-left>
                      <button v-if="!currentChatId" type="button" class="new-thread-btn-sm" :disabled="threadCreationInFlight" @click="createNewThread">
                        新对话
                      </button>
                    </template>
                    <template #actions-right>
                      <button v-if="shouldShowStopButton" type="button" class="stop-btn" :disabled="cancelInFlight" @click="cancelRun">
                        <RefreshCw :size="14" :class="{ spinning: cancelInFlight }" /> 停止
                      </button>
                    </template>
                  </AgentInputArea>
                </div>
              </div>

              <div v-if="conversations.length > 0" class="bottom-actions">
                <p class="note">当前智能体：{{ currentThreadAgentName }}；请注意辨别内容的可靠性</p>
              </div>
            </div>
          </div>
        </div>

        <!-- 状态面板 -->
        <div v-if="statePanelOpen" class="side-panel side-panel--state is-visible is-docked">
          <div class="state-panel">
            <div class="state-panel-header">
              <span class="state-panel-title">状态</span>
              <button class="state-refresh-btn" :disabled="isRefreshingState" @click="handleAgentStateRefresh">
                <RefreshCw :size="14" :class="{ spinning: isRefreshingState }" />
              </button>
            </div>
            <div class="state-panel-body">
              <div v-if="tokenUsage" class="state-section token-usage-section">
                <div class="token-usage-card">
                  <strong>Token 使用</strong>
                  <span>{{ tokenUsage.prompt_tokens ?? 0 }} prompt / {{ tokenUsage.completion_tokens ?? 0 }} completion</span>
                  <div class="token-usage-track">
                    <span class="token-usage-fill" :style="{ width: `${Math.min(100, ((tokenUsage.prompt_tokens || 0) / 1000) * 100)}%` }" />
                  </div>
                </div>
              </div>
              <div v-if="agentState" class="state-section">
                <pre class="state-json">{{ JSON.stringify(agentState, null, 2) }}</pre>
              </div>
              <div v-if="!tokenUsage && !agentState" class="state-empty">
                <p>暂无状态数据</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style lang="less" scoped>
@import '@/assets/css/main.css';
@import '@/assets/css/animations.less';

.chat-container {
  display: flex;
  width: 100%;
  height: 100vh;
  overflow: hidden;
  background: var(--gray-0);
}

/* ─── 侧边栏 ─── */
.sidebar {
  width: 260px;
  min-width: 260px;
  height: 100vh;
  background: var(--gray-50);
  border-right: 1px solid var(--gray-100);
  display: flex;
  flex-direction: column;
  transition: width 0.2s ease, min-width 0.2s ease, opacity 0.2s ease;
  overflow: hidden;
  z-index: 100;

  &.collapsed {
    width: 0;
    min-width: 0;
    border-right: none;
  }
}

.sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 12px 12px 16px;
  height: var(--header-height);
  border-bottom: 1px solid var(--gray-100);
  flex-shrink: 0;
}

.sidebar-logo {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 700;
  font-size: 15px;
  color: var(--gray-800);
}

.logo-img { width: 24px; height: 24px; flex-shrink: 0; }
.logo-text { white-space: nowrap; overflow: hidden; }

.sidebar-toggle {
  width: 24px; height: 24px; border: none; background: transparent;
  cursor: pointer; border-radius: 4px; color: var(--gray-500);
  display: flex; align-items: center; justify-content: center;
  &:hover { background: var(--gray-100); }
  .rotated { transform: rotate(180deg); }
}

.sidebar-new-btn-wrap { padding: 8px 10px; flex-shrink: 0; }

.new-thread-btn {
  width: 100%;
  padding: 8px 12px;
  border: 1px dashed var(--gray-200);
  background: transparent;
  border-radius: 8px;
  font-size: 13px;
  color: var(--gray-500);
  cursor: pointer;
  transition: all 0.15s;
  &:hover:not(:disabled) { border-color: var(--main-400); color: var(--main-600); background: var(--main-50); }
  &:disabled { opacity: 0.5; cursor: not-allowed; }
}

.sidebar-search {
  padding: 0 10px 8px;
  position: relative;
  flex-shrink: 0;
}

.search-input {
  width: 100%;
  padding: 6px 28px 6px 10px;
  border: 1px solid var(--gray-200);
  border-radius: 6px;
  font-size: 13px;
  background: var(--gray-0);
  color: var(--gray-800);
  outline: none;
  transition: border-color 0.15s;
  &:focus { border-color: var(--main-400); }
  &::placeholder { color: var(--gray-400); }
}

.search-clear {
  position: absolute;
  right: 18px;
  top: 50%;
  transform: translateY(-50%);
  border: none;
  background: transparent;
  font-size: 16px;
  color: var(--gray-400);
  cursor: pointer;
  padding: 0;
}

.sidebar-threads {
  flex: 1;
  overflow-y: auto;
}

.sidebar-loading, .sidebar-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  color: var(--gray-400);
  font-size: 13px;
  flex-direction: column;
  gap: 8px;
}

.empty-new-btn {
  padding: 4px 12px;
  border: 1px solid var(--gray-200);
  background: transparent;
  border-radius: 6px;
  font-size: 12px;
  color: var(--main-600);
  cursor: pointer;
  &:hover { background: var(--main-50); }
}

.spinner {
  width: 16px; height: 16px;
  border: 2px solid var(--gray-200);
  border-top-color: var(--main-500);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

/* ─── 聊天主区 ─── */
.chat {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  height: 100vh;
  overflow: hidden;
  background: var(--gray-0);

  &.has-state-panel {
    .chat-content-container { margin-right: 320px; }
  }
}

.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  height: var(--header-height);
  border-bottom: 1px solid var(--gray-100);
  background: var(--gray-0);
  flex-shrink: 0;

  .conversation-title { color: var(--gray-700); }
}

.header__left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}

.conversation-title {
  font-size: 14px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.header__right {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.sidebar-open-btn {
  display: none;
  width: 28px; height: 28px;
  border: none; background: transparent;
  border-radius: 4px; color: var(--gray-500);
  cursor: pointer; align-items: center; justify-content: center;
  &:hover { background: var(--gray-100); }
}

.agent-nav-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px; height: 32px;
  border: none; background: transparent;
  border-radius: 6px;
  color: var(--gray-500);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
  &:hover { background: var(--gray-100); color: var(--gray-700); }
  &.active { background: var(--main-50); color: var(--main-600); }
}

.chat-content-container {
  flex: 1;
  display: flex;
  overflow: hidden;
  position: relative;
  transition: margin-right 0.2s ease;
}

.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
}

.chat-box {
  flex: 1;
  overflow-y: auto;
  padding: 16px 0;
  scroll-behavior: smooth;
}

.conv-box {
  padding: 0 16px;
  margin-bottom: 8px;
  &.is-streaming { opacity: 0.85; }
}

.chat-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 80px;
  color: var(--gray-400);
  font-size: 14px;
}

.chat-greeting-input {
  text-align: center; padding: 16px;
  h1 { font-size: 1.125rem; font-weight: 500; color: var(--gray-500); margin: 0; }
}

.bottom {
  flex-shrink: 0;
  padding: 12px 16px 16px;
  background: var(--gray-0);
  border-top: 1px solid var(--gray-100);
  &.start-screen { padding-top: 24px; }
}

.message-input-wrapper { max-width: 800px; margin: 0 auto; width: 100%; }

.chat-loading {
  display: flex; align-items: center; gap: 8px; padding: 8px 0;
  color: var(--gray-500); font-size: 13px;
}
.loading-spinner {
  width: 16px; height: 16px;
  border: 2px solid var(--gray-200); border-top-color: var(--main-500);
  border-radius: 50%; animation: spin 0.8s linear infinite;
}

.message-input-stage { position: relative; }
.message-input-surface { position: relative; z-index: 1; }

.new-thread-btn-sm, .stop-btn {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 4px 10px; border: 1px solid var(--gray-200);
  background: var(--gray-50); border-radius: 6px;
  font-size: 12px; color: var(--gray-600); cursor: pointer;
  transition: all 0.15s;
  &:hover:not(:disabled) { background: var(--gray-100); border-color: var(--gray-300); }
  &:disabled { opacity: 0.5; cursor: not-allowed; }
}
.stop-btn { color: var(--color-error-600); border-color: var(--color-error-200); background: var(--color-error-50); }
.stop-btn:hover:not(:disabled) { background: var(--color-error-100); }

.bottom-actions { text-align: center; margin-top: 8px; }
.note { font-size: 11px; color: var(--gray-400); margin: 0; }

.generating-status { padding: 8px 16px; display: flex; justify-content: center; }
.generating-indicator {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 12px; background: var(--gray-50);
  border-radius: 20px; font-size: 13px; color: var(--gray-600);
}
.loading-dots { display: flex; gap: 3px; div {
  width: 6px; height: 6px; border-radius: 50%; background: var(--main-500);
  animation: dot-pulse 1.2s ease-in-out infinite;
  &:nth-child(2) { animation-delay: 0.2s; }
  &:nth-child(3) { animation-delay: 0.4s; }
}}
.generating-text { font-weight: 500; }
.generating-elapsed { font-size: 11px; color: var(--gray-400); }

/* ─── 状态面板 ─── */
.side-panel {
  position: absolute;
  right: 0; top: 0; bottom: 0;
  width: 320px;
  background: var(--gray-50);
  border-left: 1px solid var(--gray-100);
  display: flex; flex-direction: column;
  z-index: 10;
  transition: transform 0.2s ease;
}
.state-panel { display: flex; flex-direction: column; height: 100%; overflow: hidden; }
.state-panel-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 12px; border-bottom: 1px solid var(--gray-100);
  flex-shrink: 0;
}
.state-panel-title { font-size: 13px; font-weight: 600; color: var(--gray-700); }
.state-refresh-btn {
  width: 28px; height: 28px; border: none; background: transparent;
  border-radius: 4px; color: var(--gray-500); cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  &:hover { background: var(--gray-100); }
  &.spinning { animation: spin 1s linear infinite; }
}
.state-panel-body { flex: 1; overflow-y: auto; padding: 12px; }
.state-section { margin-bottom: 12px; }
.token-usage-card {
  display: flex; flex-direction: column; gap: 6px;
  padding: 10px; background: var(--gray-0); border: 1px solid var(--gray-100);
  border-radius: 8px; font-size: 12px;
  strong { color: var(--gray-700); }
  span { color: var(--gray-500); }
}
.token-usage-track { height: 4px; background: var(--gray-100); border-radius: 2px; overflow: hidden; }
.token-usage-fill { display: block; height: 100%; background: var(--main-500); border-radius: 2px; transition: width 0.3s ease; }
.state-json {
  font-size: 11px; font-family: monospace; color: var(--gray-600);
  background: var(--gray-0); padding: 8px; border-radius: 6px;
  overflow-x: auto; white-space: pre-wrap; word-break: break-all; margin: 0;
}
.state-empty { text-align: center; padding: 24px; color: var(--gray-400); font-size: 13px; }

@keyframes spin { to { transform: rotate(360deg); } }
@keyframes dot-pulse {
  0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
  40% { transform: scale(1); opacity: 1; }
}

:global(.dark) {
  .sidebar { background: var(--dark-50); border-color: var(--dark-100); }
  .chat { background: var(--gray-50); }
  .chat-header { background: var(--gray-50); border-color: var(--gray-100); }
  .side-panel { background: var(--gray-50); border-color: var(--gray-100); }
  .new-thread-btn { border-color: var(--dark-200); color: var(--dark-400); }
  .search-input { background: var(--dark-50); border-color: var(--dark-200); color: var(--dark-100); }
}
</style>
