<script setup>
import { ref, computed } from 'vue'
import { Check, Copy, ThumbsUp, ThumbsDown } from '@lucide/vue'
import { RefreshCw } from '@lucide/vue'
import { agentApi } from '@/apis'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import DOMPurify from 'dompurify'

const props = defineProps({
  message: { type: Object, required: true },
  isProcessing: { type: Boolean, default: false },
  showRefs: { type: Boolean, default: false },
  hideToolCalls: { type: Boolean, default: false }
})
const emit = defineEmits(['retry'])

const copied = ref(false)
const liked = ref(false)
const disliked = ref(false)

const md = new MarkdownIt({
  highlight(str, lang) {
    if (lang && hljs.getLanguage(lang)) {
      return hljs.highlight(str, { language: lang }).value
    }
    return md.utils.escapeHtml(str)
  }
})

const renderedContent = computed(() => {
  if (!props.message?.content) return ''
  return DOMPurify.sanitize(md.render(props.message.content))
})

function copyMessage() {
  navigator.clipboard.writeText(props.message.content || '')
  copied.value = true
  setTimeout(() => { copied.value = false }, 2000)
}

async function handleFeedback(type) {
  if (!props.message?.id) return
  try {
    await agentApi.submitMessageFeedback(props.message.id, type)
    if (type === 'like') liked.value = true
    if (type === 'dislike') disliked.value = true
  } catch (e) {
    console.error('反馈提交失败:', e)
  }
}
</script>

<template>
  <div class="message-wrapper" :class="`role-${message.role}`">
    <div class="message-bubble">
      <div class="message-avatar">
        <span class="avatar-letter">{{ message.role === 'user' ? 'U' : 'A' }}</span>
      </div>
      <div class="message-content">
        <div class="message-text" v-html="renderedContent" />
        <div v-if="isProcessing" class="processing-indicator">
          <div class="processing-dots">
            <span></span><span></span><span></span>
          </div>
        </div>
        <div v-if="!isProcessing && message.role === 'assistant'" class="message-actions">
          <button class="action-btn" :title="copied ? '已复制' : '复制'" @click="copyMessage">
            <Check v-if="copied" :size="12" />
            <Copy v-else :size="12" />
          </button>
          <button class="action-btn" :class="{ active: liked }" title="赞" @click="handleFeedback('like')">
            <ThumbsUp :size="12" />
          </button>
          <button class="action-btn" :class="{ active: disliked }" title="踩" @click="handleFeedback('dislike')">
            <ThumbsDown :size="12" />
          </button>
          <button class="action-btn" title="重试" @click="emit('retry', message)">
            <RefreshCw :size="12" />
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style lang="less" scoped>
.message-wrapper {
  display: flex;
  flex-direction: column;
  margin-bottom: 16px;
  &.role-user { align-items: flex-end; }
  &.role-assistant { align-items: flex-start; }
}

.message-bubble {
  display: flex;
  gap: 10px;
  max-width: 85%;
}
.role-user .message-bubble { flex-direction: row-reverse; }

.message-avatar {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  margin-top: 2px;
}
.role-assistant .message-avatar { background: linear-gradient(135deg, var(--main-600), var(--main-500)); color: white; }
.role-user .message-avatar { background: var(--gray-200); color: var(--gray-600); }

.message-content { display: flex; flex-direction: column; gap: 4px; }

.message-text {
  padding: 10px 14px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.6;
  word-break: break-word;
  :deep(pre) {
    background: var(--gray-900);
    color: var(--gray-100);
    padding: 12px;
    border-radius: 8px;
    overflow-x: auto;
    font-size: 13px;
    margin: 8px 0;
  }
  :deep(code) { background: var(--gray-100); padding: 1px 4px; border-radius: 3px; font-size: 13px; }
  :deep(pre code) { background: none; padding: 0; }
  :deep(p) { margin: 0 0 8px; }
  :deep(p:last-child) { margin-bottom: 0; }
  :deep(ul), :deep(ol) { margin: 4px 0; padding-left: 20px; }
  :deep(li) { margin: 2px 0; }
}
.role-user .message-text { background: var(--main-600); color: white; border-bottom-right-radius: 4px; }
.role-assistant .message-text { background: var(--gray-100); color: var(--gray-800); border-bottom-left-radius: 4px; }

.processing-indicator { padding: 8px 12px; }
.processing-dots { display: flex; gap: 4px; span {
  width: 6px; height: 6px; border-radius: 50%; background: var(--main-400); animation: dot-pulse 1.2s ease-in-out infinite;
  &:nth-child(2) { animation-delay: 0.2s; }
  &:nth-child(3) { animation-delay: 0.4s; }
}}

.message-actions {
  display: flex; gap: 2px; opacity: 0; transition: opacity 0.15s;
  .message-bubble:hover & { opacity: 1; }
}
.action-btn {
  display: flex; align-items: center; justify-content: center;
  width: 24px; height: 24px; border: none; background: transparent;
  border-radius: 4px; color: var(--gray-400); cursor: pointer;
  &:hover { background: var(--gray-100); color: var(--gray-600); }
  &.active { color: var(--main-600); }
}

@keyframes dot-pulse {
  0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
  40% { transform: scale(1); opacity: 1; }
}
</style>
