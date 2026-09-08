<script setup>
import { ref, computed, watch } from 'vue'
import { Send, X, Paperclip } from '@lucide/vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  isLoading: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  sendButtonDisabled: { type: Boolean, default: false },
  mention: { type: Object, default: () => ( {}) },
  threadId: { type: String, default: '' },
  supportsFileUpload: { type: Boolean, default: false },
  attachments: { type: Array, default: () => [] }
})
const emit = defineEmits(['update:modelValue', 'send', 'upload-attachment', 'remove-attachment'])

const textareaRef = ref(null)
const inputHeight = ref('auto')

const displayValue = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val)
})

function autoResize() {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 200) + 'px'
}

function handleKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    if (!props.sendButtonDisabled && !props.isLoading && displayValue.value.trim()) {
      emit('send')
    }
  }
}

function handlePaste(e) {
  const files = e.clipboardData?.files
  if (files && files.length > 0 && props.supportsFileUpload) {
    e.preventDefault()
    for (const file of files) {
      emit('upload-attachment', file)
    }
  }
}

function handleFileUpload() {
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = 'image/*,.csv,.xlsx,.xls,.sql,.txt'
  input.onchange = (e) => {
    const file = e.target.files[0]
    if (file) emit('upload-attachment', file)
  }
  input.click()
}

function removeAttachment(idx) {
  emit('remove-attachment', idx)
}
</script>

<template>
  <div class="agent-input-area">
    <div class="input-attachments" v-if="attachments.length">
      <div
        v-for="(file, idx) in attachments"
        :key="idx"
        class="attachment-chip"
      >
        <span class="attachment-name">{{ file.name }}</span>
        <button class="attachment-remove" @click="removeAttachment(idx)">
          <X :size="12" />
        </button>
      </div>
    </div>

    <div class="input-wrapper">
      <textarea
        ref="textareaRef"
        v-model="displayValue"
        class="input-textarea"
        :disabled="disabled"
        :placeholder="isLoading ? '生成中，请稍候...' : '输入消息... (Enter 发送，Shift+Enter 换行)'"
        rows="1"
        @input="autoResize"
        @keydown="handleKeydown"
        @paste="handlePaste"
      />
      <div class="input-actions">
        <slot name="actions-left"></slot>
        <button
          type="button"
          class="upload-btn"
          :disabled="disabled || !supportsFileUpload"
          @click="handleFileUpload"
          title="上传附件"
        >
          <Paperclip :size="16" />
        </button>
        <button
          type="button"
          class="send-btn"
          :class="{ loading: isLoading, disabled: sendButtonDisabled }"
          :disabled="sendButtonDisabled"
          @click="emit('send')"
        >
          <Send v-if="!isLoading" :size="16" />
          <X v-else :size="16" />
        </button>
        <slot name="actions-right"></slot>
      </div>
    </div>
  </div>
</template>

<style lang="less" scoped>
.agent-input-area {
  width: 100%;
  background: var(--gray-0);
  border: 1px solid var(--gray-200);
  border-radius: 12px;
  padding: 8px;
  transition: border-color 0.15s, box-shadow 0.15s;
  &:focus-within {
    border-color: var(--main-400);
    box-shadow: 0 0 0 3px var(--main-50);
  }
}

.input-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 6px;
}

.attachment-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  background: var(--main-50);
  border: 1px solid var(--main-100);
  border-radius: 12px;
  font-size: 11px;
  color: var(--main-700);
}

.attachment-name {
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.attachment-remove {
  display: flex;
  align-items: center;
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--main-600);
  padding: 0;
  &:hover { color: var(--main-800); }
}

.input-wrapper {
  display: flex;
  align-items: flex-end;
  gap: 8px;
}

.input-textarea {
  flex: 1;
  min-height: 40px;
  max-height: 200px;
  padding: 8px 12px;
  border: none;
  background: transparent;
  font-size: 14px;
  line-height: 1.5;
  color: var(--gray-900);
  resize: none;
  outline: none;
  font-family: inherit;
  overflow-y: auto;
  &::placeholder { color: var(--gray-400); }
  &:disabled { opacity: 0.6; }
}

.input-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  padding-bottom: 4px;
}

.upload-btn,
.send-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  border-radius: 8px;
  cursor: pointer;
  color: var(--gray-500);
  transition: all 0.15s;

  &:hover:not(:disabled) { background: var(--gray-100); color: var(--gray-700); }
  &:disabled { opacity: 0.4; cursor: not-allowed; }
}

.send-btn {
  background: var(--main-600);
  color: white;
  &:hover:not(:disabled) { background: var(--main-700); }
  &.loading { background: var(--gray-400); }
  &.disabled { opacity: 0.5; }
}
</style>
