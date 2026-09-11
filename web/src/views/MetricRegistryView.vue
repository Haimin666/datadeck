<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import PageHeader from '@/components/shared/PageHeader.vue'
import { metricApi } from '@/apis/metric_api'

const items = ref([])
const loading = ref(false)
const saving = ref(false)
const onlyConflict = ref(true)
const query = ref('')
const editing = ref(null)
const form = reactive({})

const load = async () => {
  loading.value = true
  try {
    const result = await metricApi.list({ q: query.value, conflict_only: onlyConflict.value })
    items.value = result.items || result.data?.items || []
  } catch (error) {
    message.error(error.message || '指标加载失败')
  } finally { loading.value = false }
}

const edit = (item) => {
  editing.value = item
  Object.assign(form, JSON.parse(JSON.stringify(item)))
  form.aliases = (item.aliases || []).join(', ')
  form.ossie_expression = item.ossie_expression
    ? JSON.stringify(item.ossie_expression, null, 2)
    : ''
}

const save = async () => {
  saving.value = true
  try {
    let ossieExpression = null
    if (String(form.ossie_expression || '').trim()) {
      try { ossieExpression = JSON.parse(form.ossie_expression) } catch { throw new Error('Ossie 表达式必须是合法 JSON') }
    }
    const payload = { ...form, ossie_expression: ossieExpression, aliases: String(form.aliases || '').split(',').map(x => x.trim()).filter(Boolean) }
    delete payload.id; delete payload.created_at; delete payload.updated_at; delete payload.metadata
    await metricApi.update(editing.value.id, payload)
    message.success('指标已保存')
    editing.value = null
    await load()
  } catch (error) { message.error(error.message || '保存失败') } finally { saving.value = false }
}

const conflictCount = computed(() => items.value.filter(x => x.conflict_status === 'conflict').length)
onMounted(load)
</script>

<template>
  <div class="metric-view">
    <PageHeader title="指标口径" :show-border="true">
      <template #info><span class="summary">当前 {{ items.length }} 条<span v-if="onlyConflict">，冲突 {{ conflictCount }} 条</span></span></template>
    </PageHeader>
    <div class="toolbar">
      <a-input v-model:value="query" placeholder="搜索指标名、Ossie 名称或定义" allow-clear @press-enter="load" />
      <a-checkbox v-model:checked="onlyConflict" @change="load">仅看冲突</a-checkbox>
      <a-button type="primary" :loading="loading" @click="load">刷新</a-button>
    </div>
    <a-table :data-source="items" :loading="loading" row-key="id" :pagination="{ pageSize: 20 }">
      <a-table-column title="指标" data-index="canonical_name" />
      <a-table-column title="Ossie" data-index="ossie_name" />
      <a-table-column title="状态" key="status"><template #default="{ record }"><a-tag :color="record.conflict_status === 'conflict' ? 'red' : record.status === 'approved' ? 'green' : 'orange'">{{ record.conflict_status === 'conflict' ? '冲突' : record.status }}</a-tag></template></a-table-column>
      <a-table-column title="定义" data-index="definition" ellipsis />
      <a-table-column title="操作" key="action"><template #default="{ record }"><a-button type="link" @click="edit(record)">审核/修改</a-button></template></a-table-column>
    </a-table>
    <a-modal v-model:open="editing" title="审核 Ossie 指标" :confirm-loading="saving" width="760px" @ok="save">
      <a-form layout="vertical">
        <a-form-item label="指标名称"><a-input v-model:value="form.canonical_name" /></a-form-item>
        <a-form-item label="别名（逗号分隔）"><a-input v-model:value="form.aliases" /></a-form-item>
        <a-form-item label="定义"><a-textarea v-model:value="form.definition" :rows="3" /></a-form-item>
        <a-form-item label="公式"><a-textarea v-model:value="form.formula" :rows="3" /></a-form-item>
        <a-form-item label="Ossie 表达式 JSON"><a-textarea v-model:value="form.ossie_expression" :rows="5" /></a-form-item>
        <a-form-item label="审核状态"><a-select v-model:value="form.status"><a-select-option value="approved">approved（作为 Agent 权威口径）</a-select-option><a-select-option value="needs_review">needs_review</a-select-option><a-select-option value="rejected">rejected</a-select-option></a-select></a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<style scoped>
.metric-view { min-height: 100%; background: var(--gray-0); color: var(--gray-1000); }
.toolbar { display: flex; gap: 12px; align-items: center; padding: 20px 24px 12px; }
.toolbar .ant-input { max-width: 420px; }
.metric-view :deep(.ant-table-wrapper) { padding: 0 24px 24px; }
.summary { color: var(--gray-600); font-size: 13px; }
</style>
