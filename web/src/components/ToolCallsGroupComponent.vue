<script setup>
import { ref } from 'vue'
import { ChevronDown, ChevronUp, Copy } from '@lucide/vue'

const props = defineProps({
  toolCalls: { type: Array, default: () => [] },
  isActive: { type: Boolean, default: false }
})

const collapsed = ref(true)
const copiedToolId = ref(null)

function copyToolCall(toolCall) {
  const text = `${toolCall.name}(${JSON.stringify(toolCall.arguments || {})})`
  navigator.clipboard.writeText(text)
  copiedToolId.value = toolCall.id
  setTimeout(() => { copiedToolId.value = null }, 2000)
}
</script>

<template>
  <div class="tool-calls-group">
    <button
      class="tool-calls-toggle"
      :class="{ active: !collapsed }"
      @click="collapsed = !collapsed"
    >
      <component :is="collapsed ? ChevronDown : ChevronUp" :size="14" />
      <span>工具调用 ({{ toolCalls.length }})</span>
    </button>
    <div v-if="!collapsed" class="tool-calls-list">
      <div
        v-for="(tc, idx) in toolCalls"
        :key="tc.id || idx"
        class="tool-call-item"
        :class="{ active: isActive && idx === toolCalls.length - 1 }"
      >
        <div class="tool-call-header">
          <span class="tool-call-name">
            <span class="tool-call-dot"></span>
            {{ tc.name || 'tool_call' }}
          </span>
          <button class="copy-btn" @click="copyToolCall(tc)">
            <Copy v-if="copiedToolId !== (tc.id || idx)" :size="12" />
            <Check v-else :size="12" />
          </button>
        </div>
        <pre v-if="tc.arguments" class="tool-call-args">{{ JSON.stringify(tc.arguments, null, 2) }}</pre>
        <pre v-if="tc.result" class="tool-call-result">{{ typeof tc.result === 'string' ? tc.result : JSON.stringify(tc.result, null, 2) }}</pre>
      </div>
    </div>
  </div>
</template>

<script>
import { Check } from '@lucide/vue'
export default { components: { Check } }
</script>

<style lang="less" scoped>
.tool-calls-group {
  margin: 8px 0;
  border: 1px solid var(--gray-100);
  border-radius: 8px;
  overflow: hidden;
}

.tool-calls-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 8px 12px;
  border: none;
  background: var(--gray-50);
  font-size: 12px;
  color: var(--gray-600);
  cursor: pointer;
  text-align: left;
  transition: background 0.15s;
  &:hover { background: var(--gray-100); }
  &.active { background: var(--main-50); color: var(--main-700); }
}

.tool-calls-list { padding: 8px; display: flex; flex-direction: column; gap: 8px; }

.tool-call-item {
  padding: 8px;
  background: var(--gray-0);
  border: 1px solid var(--gray-100);
  border-radius: 6px;
  font-size: 12px;
  &.active { border-color: var(--main-300); background: var(--main-50); }
}

.tool-call-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}

.tool-call-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  color: var(--gray-700);
}

.tool-call-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--main-500);
}

.copy-btn {
  display: flex;
  align-items: center;
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--gray-400);
  padding: 2px;
  &:hover { color: var(--gray-600); }
}

.tool-call-args,
.tool-call-result {
  margin: 4px 0 0;
  padding: 8px;
  background: var(--gray-900);
  color: var(--gray-100);
  border-radius: 4px;
  font-size: 11px;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.tool-call-result { background: var(--gray-800); color: var(--color-success-400); }
</style>
