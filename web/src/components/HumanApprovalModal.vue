<script setup>
import { ref } from 'vue'
import { Check, X } from '@lucide/vue'

const props = defineProps({
  visible: { type: Boolean, default: false },
  questions: { type: Array, default: () => [] },
  kind: { type: String, default: '' },
  actionRequests: { type: Array, default: () => [] }
})
const emit = defineEmits(['submit', 'cancel'])

const selectedTools = ref(new Set())

function toggleTool(id) {
  if (selectedTools.value.has(id)) {
    selectedTools.value.delete(id)
  } else {
    selectedTools.value.add(id)
  }
}

function handleApprove() {
  emit('submit', true, [...selectedTools.value])
}

function handleCancel() {
  emit('cancel')
}
</script>

<template>
  <div v-if="visible" class="approval-overlay">
    <div class="approval-modal">
      <div class="approval-header">
        <h3>需要审批</h3>
        <p class="approval-desc">以下工具调用需要您的确认</p>
      </div>
      <div class="approval-body">
        <div v-if="actionRequests.length" class="approval-list">
          <div
            v-for="req in actionRequests"
            :key="req.id || req.tool_name"
            class="approval-item"
            :class="{ selected: selectedTools.has(req.id || req.tool_name) }"
            @click="toggleTool(req.id || req.tool_name)"
          >
            <span class="approval-tool-name">{{ req.tool_name || req.name }}</span>
            <Check v-if="selectedTools.has(req.id || req.tool_name)" :size="14" />
          </div>
        </div>
        <div v-if="questions.length" class="approval-questions">
          <div v-for="(q, idx) in questions" :key="idx" class="approval-question">
            <p>{{ q.question || q }}</p>
          </div>
        </div>
      </div>
      <div class="approval-footer">
        <button class="btn btn-cancel" @click="handleCancel">取消</button>
        <button class="btn btn-approve" @click="handleApprove">批准选中</button>
      </div>
    </div>
  </div>
</template>

<style lang="less" scoped>
.approval-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.approval-modal {
  background: var(--gray-0);
  border-radius: 12px;
  padding: 20px;
  width: 90%;
  max-width: 480px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.15);
}

.approval-header { margin-bottom: 16px; }
.approval-header h3 { margin: 0 0 4px; font-size: 16px; }
.approval-desc { margin: 0; font-size: 13px; color: var(--gray-500); }

.approval-list { display: flex; flex-direction: column; gap: 6px; margin-bottom: 16px; }

.approval-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border: 1px solid var(--gray-200);
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s;
  &:hover { border-color: var(--main-300); }
  &.selected { border-color: var(--main-500); background: var(--main-50); }
}

.approval-tool-name { font-size: 13px; font-weight: 500; }
.approval-questions { margin-bottom: 16px; }
.approval-question p { font-size: 13px; color: var(--gray-600); margin: 0 0 8px; }

.approval-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.btn {
  padding: 6px 16px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  border: 1px solid transparent;
  transition: all 0.15s;
}
.btn-cancel {
  background: var(--gray-100);
  color: var(--gray-700);
  border-color: var(--gray-200);
  &:hover { background: var(--gray-200); }
}
.btn-approve {
  background: var(--main-600);
  color: white;
  &:hover { background: var(--main-700); }
}
</style>
