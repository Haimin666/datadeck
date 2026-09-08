<script setup>
import { computed } from 'vue'
import { Pin, Trash2 } from '@lucide/vue'

const props = defineProps({
  thread: { type: Object, required: true },
  isActive: { type: Boolean, default: false }
})
const emit = defineEmits(['select', 'pin', 'delete'])

const hasUnread = computed(() => !props.thread.viewed_at)
</script>

<template>
  <div
    class="nav-item"
    :class="{ active: isActive, pinned: thread.is_pinned, 'has-unread': hasUnread }"
    @click="emit('select', thread)"
  >
    <div class="nav-item-content">
      <div class="nav-item-title">{{ thread.title || '新的对话' }}</div>
      <div v-if="thread.agent_name" class="nav-item-agent">{{ thread.agent_name }}</div>
    </div>
    <div class="nav-item-actions" @click.stop>
      <button v-if="!thread.is_pinned" class="nav-action-btn" title="置顶" @click="emit('pin', thread)">
        <Pin :size="12" />
      </button>
      <button class="nav-action-btn" title="删除" @click="emit('delete', thread.id)">
        <Trash2 :size="12" />
      </button>
    </div>
    <div v-if="hasUnread" class="unread-dot" />
  </div>
</template>

<style lang="less" scoped>
.nav-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s;
  position: relative;
  &:hover { background: var(--gray-50); }
  &.active { background: var(--main-50); }
  &.pinned { border-left: 2px solid var(--main-400); }
}

.nav-item-content { flex: 1; min-width: 0; }
.nav-item-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--gray-800);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.nav-item-agent {
  font-size: 11px;
  color: var(--gray-400);
  margin-top: 2px;
}

.nav-item-actions { display: flex; gap: 2px; opacity: 0; transition: opacity 0.15s; }
.nav-item:hover .nav-item-actions { opacity: 1; }

.nav-action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: none;
  background: transparent;
  border-radius: 4px;
  color: var(--gray-400);
  cursor: pointer;
  &:hover { background: var(--gray-100); color: var(--gray-600); }
}

.unread-dot {
  position: absolute;
  left: 2px;
  top: 50%;
  transform: translateY(-50%);
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--main-500);
}
</style>
