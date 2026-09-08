<script setup>
import { ref } from 'vue'
import { Download, Eye } from '@lucide/vue'
import { agentApi } from '@/apis'

const props = defineProps({
  artifacts: { type: Array, default: () => [] },
  threadId: { type: String, default: '' }
})
const emit = defineEmits(['saved', 'open-preview'])

const previewPath = ref(null)

async function handleDownload(artifact) {
  if (!props.threadId || !artifact.path) return
  try {
    const response = await agentApi.downloadThreadArtifact(props.threadId, artifact.path)
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = artifact.name || artifact.path.split('/').pop() || 'download'
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    console.error('下载失败:', e)
  }
}

async function handlePreview(artifact) {
  if (!props.threadId || !artifact.path) return
  previewPath.value = artifact.path
  emit('open-preview', artifact)
}
</script>

<template>
  <div v-if="artifacts.length" class="artifacts-card">
    <div class="artifacts-header">
      <span class="artifacts-title">交付物</span>
      <span class="artifacts-count">{{ artifacts.length }}</span>
    </div>
    <div class="artifacts-list">
      <div
        v-for="(artifact, idx) in artifacts"
        :key="idx"
        class="artifact-item"
      >
        <div class="artifact-info">
          <span class="artifact-name">{{ artifact.name || artifact.path?.split('/').pop() || '文件' }}</span>
          <span v-if="artifact.size" class="artifact-size">{{ formatSize(artifact.size) }}</span>
        </div>
        <div class="artifact-actions">
          <button class="action-btn" title="预览" @click="handlePreview(artifact)">
            <Eye :size="14" />
          </button>
          <button class="action-btn" title="下载" @click="handleDownload(artifact)">
            <Download :size="14" />
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
function formatSize(bytes) {
  if (!bytes) return ''
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let size = bytes
  while (size >= 1024 && i < units.length - 1) { size /= 1024; i++ }
  return `${size.toFixed(1)} ${units[i]}`
}
export default { methods: { formatSize } }
</script>

<style lang="less" scoped>
.artifacts-card {
  margin: 12px 16px;
  border: 1px solid var(--gray-100);
  border-radius: 8px;
  overflow: hidden;
}

.artifacts-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  background: var(--gray-50);
  border-bottom: 1px solid var(--gray-100);
}

.artifacts-title { font-size: 12px; font-weight: 600; color: var(--gray-600); }
.artifacts-count {
  font-size: 11px;
  padding: 1px 6px;
  background: var(--gray-100);
  border-radius: 10px;
  color: var(--gray-500);
}

.artifacts-list { padding: 8px; display: flex; flex-direction: column; gap: 4px; }

.artifact-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  border-radius: 6px;
  transition: background 0.15s;
  &:hover { background: var(--gray-50); }
}

.artifact-info {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.artifact-name {
  font-size: 13px;
  color: var(--gray-700);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.artifact-size { font-size: 11px; color: var(--gray-400); flex-shrink: 0; }

.artifact-actions { display: flex; gap: 4px; flex-shrink: 0; }

.action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  border-radius: 4px;
  color: var(--gray-400);
  cursor: pointer;
  &:hover { background: var(--gray-100); color: var(--gray-600); }
}
</style>
