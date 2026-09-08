<script setup>
import { ref, computed } from 'vue'
import ConversationNavItem from './ConversationNavItem.vue'

const props = defineProps({
  threads: { type: Array, default: () => [] },
  activeThreadId: { type: String, default: '' },
  isLoading: { type: Boolean, default: false }
})
const emit = defineEmits(['select', 'pin', 'delete'])

const searchQuery = ref('')
const searchTerm = computed(() => searchQuery.value.toLowerCase())

const filteredThreads = computed(() => {
  let list = props.threads
  if (searchTerm.value) {
    list = list.filter(t =>
      (t.title || '').toLowerCase().includes(searchTerm.value) ||
      (t.last_message || '').toLowerCase().includes(searchTerm.value)
    )
  }
  const pinned = list.filter(t => t.is_pinned)
  const unpinned = list.filter(t => !t.is_pinned)
  return [...pinned, ...unpinned]
})
</script>

<template>
  <div class="nav-section">
    <div class="nav-search">
      <input
        v-model="searchQuery"
        type="text"
        placeholder="搜索对话..."
        class="search-input"
      />
    </div>
    <div v-if="isLoading" class="nav-loading">
      <div class="loading-spinner" />
    </div>
    <div v-else-if="filteredThreads.length" class="nav-list">
      <ConversationNavItem
        v-for="thread in filteredThreads"
        :key="thread.id"
        :thread="thread"
        :is-active="thread.id === activeThreadId"
        @select="emit('select', thread)"
        @pin="emit('pin', thread)"
        @delete="emit('delete', $event)"
      />
    </div>
    <div v-else class="nav-empty">
      <p>暂无对话</p>
    </div>
  </div>
</template>

<style lang="less" scoped>
.nav-section { display: flex; flex-direction: column; height: 100%; }

.nav-search { padding: 8px; }

.search-input {
  width: 100%;
  padding: 6px 10px;
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

.nav-loading { display: flex; align-items: center; justify-content: center; padding: 24px; }

.loading-spinner {
  width: 20px;
  height: 20px;
  border: 2px solid var(--gray-200);
  border-top-color: var(--main-500);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.nav-list { flex: 1; overflow-y: auto; }

.nav-empty {
  padding: 24px;
  text-align: center;
  color: var(--gray-400);
  font-size: 13px;
}

@keyframes spin { to { transform: rotate(360deg); } }
</style>
