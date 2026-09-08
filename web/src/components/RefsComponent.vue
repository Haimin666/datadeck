<script setup>
import { Copy, ExternalLink } from '@lucide/vue'
import { ref } from 'vue'

defineProps({
  message: { type: Object, default: null },
  showRefs: { type: Array, default: () => [] },
  isLatestMessage: { type: Boolean, default: false },
  sources: { type: Array, default: () => [] }
})

const copied = ref(false)

function copyContent() {
  if (!props.message?.content) return
  navigator.clipboard.writeText(props.message.content)
  copied.value = true
  setTimeout(() => { copied.value = false }, 2000)
}
</script>

<template>
  <div v-if="showRefs.includes('copy') && message" class="refs-row">
    <button class="ref-btn" @click="copyContent">
      <Copy v-if="!copied" :size="12" />
      <Check v-else :size="12" />
      <span>{{ copied ? '已复制' : '复制' }}</span>
    </button>
  </div>
</template>

<script>
import { Check } from '@lucide/vue'
export default { components: { Check } }
</script>

<style lang="less" scoped>
.refs-row { display: flex; gap: 8px; margin-top: 4px; }
.ref-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border: 1px solid var(--gray-200);
  background: var(--gray-0);
  border-radius: 4px;
  font-size: 11px;
  color: var(--gray-500);
  cursor: pointer;
  &:hover { background: var(--gray-50); color: var(--gray-700); }
}
</style>
