<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { Bot, RefreshCw, Save } from '@lucide/vue'
import { useRoute } from 'vue-router'

import { agentApi } from '@/apis/agent_api'
import AgentWorkflowCanvas from '@/components/model-management/AgentWorkflowCanvas.vue'
import PageShoulder from '@/components/shared/PageShoulder.vue'

const agents = ref([])
const selectedSlug = ref('')
const selectedDetail = ref(null)
const workflow = ref({ nodes: [], edges: [] })
const selectedSubagents = ref([])
const loading = ref(false)
const saving = ref(false)
const dirty = ref(false)
const route = useRoute()

const coordinators = computed(() => agents.value.filter((agent) => agent.execution_role !== 'subagent'))
const subagentOptions = computed(() => selectedDetail.value?.configurable_items?.subagents?.options || [])
const selectedName = computed(() => selectedDetail.value?.name || selectedSlug.value || '协调 Agent')
const stats = computed(() => ({
  total: coordinators.value.length,
  configured: coordinators.value.filter((agent) => agent.delegation_enabled).length,
  nodes: workflow.value.nodes.length
}))

const normalizeWorkflow = (context) => {
  const value = context?.subagent_workflow
  return value && Array.isArray(value.nodes) ? {
    version: value.version || 1,
    nodes: value.nodes.map((node) => ({ ...node })),
    edges: Array.isArray(value.edges) ? value.edges.map((edge) => ({ ...edge })) : []
  } : { version: 1, nodes: [], edges: [] }
}

const loadDetail = async (slug = selectedSlug.value) => {
  if (!slug) return
  loading.value = true
  try {
    const response = await agentApi.getAgentDetail(slug)
    selectedDetail.value = response.agent || response
    const context = selectedDetail.value?.config_json?.context || selectedDetail.value?.config_json || {}
    workflow.value = normalizeWorkflow(context)
    selectedSubagents.value = Array.isArray(context.subagents)
      ? [...context.subagents]
      : workflow.value.nodes.map((node) => node.subagent_slug)
    dirty.value = false
  } catch (error) {
    message.error(error.message || '加载编排失败')
  } finally { loading.value = false }
}

const loadAgents = async () => {
  loading.value = true
  try {
    const response = await agentApi.getAgents({ includeSubagents: true })
    agents.value = response.agents || []
    const requested = selectedSlug.value
    const requestedAgent = coordinators.value.find(
      (agent) => agent.slug === requested || agent.id === requested
    )
    if (requestedAgent) {
      selectedSlug.value = requestedAgent.slug
    } else {
      selectedSlug.value = coordinators.value[0]?.slug || ''
    }
    await loadDetail()
  } catch (error) {
    message.error(error.message || '加载智能体失败')
  } finally { loading.value = false }
}

const onWorkflowChange = (value) => { workflow.value = value; dirty.value = true }
const onSubagentsChange = (value) => { selectedSubagents.value = value; dirty.value = true }
const save = async () => {
  if (!selectedDetail.value || !selectedSlug.value) return
  saving.value = true
  try {
    const original = selectedDetail.value.config_json || {}
    const context = { ...(original.context || original), subagents: selectedSubagents.value }
    context.subagent_workflow = workflow.value.nodes.length ? workflow.value : null
    await agentApi.updateAgent(selectedSlug.value, {
      config_json: { ...original, context },
      delegation_enabled: workflow.value.nodes.length > 0,
      execution_role: 'standalone'
    })
    const current = agents.value.find((agent) => agent.slug === selectedSlug.value)
    if (current) current.delegation_enabled = workflow.value.nodes.length > 0
    dirty.value = false
    message.success('编排已保存')
  } catch (error) {
    message.error(error.message || '保存编排失败')
  } finally { saving.value = false }
}

watch(selectedSlug, (slug, previous) => {
  if (slug && slug !== previous) loadDetail(slug)
})
onMounted(() => {
  selectedSlug.value = String(route.query.agent || '')
  loadAgents()
})
defineExpose({ loading, stats, refresh: loadAgents })
</script>

<template>
  <div class="orchestration-panel">
    <PageShoulder :search-placeholder="''">
      <template #search></template>
      <template #actions>
        <a-select v-model:value="selectedSlug" class="coordinator-select" :loading="loading" placeholder="选择协调 Agent">
          <a-select-option v-for="agent in coordinators" :key="agent.slug" :value="agent.slug">{{ agent.name }}</a-select-option>
        </a-select>
        <a-button @click="loadAgents" :loading="loading"><RefreshCw :size="14" /> 刷新</a-button>
        <a-button type="primary" :disabled="!dirty" :loading="saving" @click="save"><Save :size="14" /> 保存编排</a-button>
      </template>
    </PageShoulder>
    <div v-if="!coordinators.length && !loading" class="orchestration-empty">
      <Bot :size="28" /><strong>还没有可编排的独立 Agent</strong><span>请先在“智能体”Tab 创建子智能体和协调 Agent。</span>
    </div>
    <div v-else class="orchestration-content">
      <div class="orchestration-heading">
        <div><span class="eyebrow">WORKFLOW BUILDER</span><h2>{{ selectedName }}<span v-if="dirty" class="dirty-mark">未保存</span></h2><p>没有连线的节点并行执行，有连线的节点按依赖执行。</p></div>
        <div class="orchestration-stats"><span><strong>{{ stats.nodes }}</strong> 节点</span><span><strong>{{ workflow.edges.length }}</strong> 依赖</span><span><strong>{{ selectedSubagents.length }}</strong> 已挂载</span></div>
      </div>
      <AgentWorkflowCanvas v-if="selectedDetail" :model-value="workflow" :subagents="subagentOptions" @update:model-value="onWorkflowChange" @update:subagents="onSubagentsChange" />
    </div>
  </div>
</template>

<style lang="less" scoped>
.orchestration-panel { min-height:100%; background:var(--gray-0); }
.coordinator-select { min-width:190px; }
.orchestration-content { padding:4px var(--page-padding) 28px; }
.orchestration-heading { display:flex; align-items:flex-end; justify-content:space-between; gap:24px; padding:18px 0 16px; }
.eyebrow { color:var(--main-700); font-size:10px; font-weight:700; letter-spacing:1.3px; }
h2 { margin:6px 0 0; color:var(--gray-950); font-size:22px; font-weight:650; }
.orchestration-heading p { margin:6px 0 0; color:var(--gray-600); font-size:12px; }
.dirty-mark { margin-left:8px; color:var(--color-warning-700); font-size:11px; font-weight:500; vertical-align:middle; }
.orchestration-stats { display:flex; gap:8px; span { padding:8px 11px; border:1px solid var(--gray-150); border-radius:8px; background:var(--gray-50); color:var(--gray-600); font-size:11px; } strong { margin-right:3px; color:var(--gray-900); font-size:14px; } }
.orchestration-empty { display:flex; flex-direction:column; align-items:center; gap:8px; padding:120px 20px; color:var(--gray-500); font-size:12px; text-align:center; .lucide { color:var(--main-600); } strong { color:var(--gray-800); font-size:14px; } }
@media (max-width:760px) { .orchestration-heading { align-items:flex-start; flex-direction:column; } }
</style>
