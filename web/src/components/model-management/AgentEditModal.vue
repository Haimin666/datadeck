<script setup>
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { useRouter } from 'vue-router'
import {
  Bot,
  Microscope,
  RefreshCw,
  SlidersHorizontal,
  Upload,
  Wrench,
  Workflow
} from '@lucide/vue'

import { userApi } from '@/apis/user_api'
import AgentRuntimeConfigForm from '@/components/AgentRuntimeConfigForm.vue'
import AgentCreationConfig from '@/components/model-management/AgentCreationConfig.vue'
import AgentWorkflowCanvas from '@/components/model-management/AgentWorkflowCanvas.vue'
import ShareConfigForm from '@/components/ShareConfigForm.vue'
import FallbackAvatar from '@/components/common/FallbackAvatar.vue'
import { isBuiltinAgent, useAgentStore } from '@/stores/agent'
import { useUserStore } from '@/stores/user'
import { generatePixelAvatar } from '@/utils/pixelAvatar'
import { MAX_IMAGE_UPLOAD_SIZE_BYTES, MAX_IMAGE_UPLOAD_SIZE_MB } from '@/utils/upload_limits'

const props = defineProps({
  backendOptions: { type: Array, default: () => [] }
})

const emit = defineEmits(['saved'])

const userStore = useUserStore()
const agentStore = useAgentStore()
const router = useRouter()

const DEFAULT_AGENT_BACKEND_ID = 'ChatbotAgent'
const runtimeAgentModalTabs = ['model', 'tools', 'workflow']

const showAgentModal = ref(false)
const editingAgentId = ref(null)
const agentModalActiveTab = ref('basic')
const agentIconUploading = ref(false)
const saving = ref(false)
const agentShareConfigFormRef = ref(null)
const runtimeConfigFormRef = ref(null)
const agentNameInputRef = ref(null)
const agentShareConfig = ref({
  version: 2,
  read_scope: { access_level: 'user', user_uids: [] },
  manage_scope: null
})
const agentForm = reactive({
  slug: '',
  name: '',
  backend_id: DEFAULT_AGENT_BACKEND_ID,
  execution_role: 'standalone',
  delegation_enabled: false,
  description: '',
  icon: ''
})
const draftConfigItems = ref({})
const draftConfigLoading = ref(false)
const draftAgentConfig = ref({})

// 基本配置的原始基线，用于在标题栏显示「有修改」状态。slug / backend_id
// 仅在创建模式可编辑，因此新建时不参与比对。
const originalAgentForm = ref({ name: '', description: '', icon: '' })
const originalShareConfig = ref(null)

const snapshotAgentForm = () => ({
  name: (agentForm.name || '').trim(),
  description: (agentForm.description || '').trim(),
  icon: (agentForm.icon || '').trim()
})

const cloneShareConfig = (share) => {
  if (!share) return null
  const cloneScope = (scope) =>
    scope
      ? {
          access_level: scope.access_level,
          user_uids: [...(scope.user_uids || [])]
        }
      : null
  return {
    version: share.version,
    read_scope: cloneScope(share.read_scope),
    manage_scope: cloneScope(share.manage_scope)
  }
}

const snapshotShareConfig = () => {
  if (!editingAgentId.value) return null
  if (isBuiltinAgent({ id: editingAgentId.value })) {
    return cloneShareConfig({
      version: 2,
      read_scope: { access_level: 'global', user_uids: [] },
      manage_scope: null
    })
  }
  return cloneShareConfig(agentShareConfig.value)
}

const stringifyShareConfig = (share) => {
  if (!share) return ''
  const sortIds = (arr) => [...(arr || [])].map((v) => String(v)).sort()
  return JSON.stringify({
    version: share.version,
    read_scope: {
      access_level: share.read_scope?.access_level || null,
      user_uids: sortIds(share.read_scope?.user_uids)
    },
    manage_scope: share.manage_scope
      ? {
          access_level: share.manage_scope.access_level,
          user_uids: sortIds(share.manage_scope.user_uids)
        }
      : null
  })
}

const hasProfileChanges = computed(() => {
  if (!editingAgentId.value) return false
  const currentForm = snapshotAgentForm()
  const baselineForm = originalAgentForm.value
  if (
    currentForm.name !== baselineForm.name ||
    currentForm.description !== baselineForm.description ||
    currentForm.icon !== baselineForm.icon
  ) {
    return true
  }
  if (!canEditAgentShareConfig.value) return false
  const currentShare = snapshotShareConfig()
  const baselineShare = originalShareConfig.value
  if (!currentShare || !baselineShare) return false
  return stringifyShareConfig(currentShare) !== stringifyShareConfig(baselineShare)
})

const captureProfileBaseline = () => {
  originalAgentForm.value = snapshotAgentForm()
  originalShareConfig.value = snapshotShareConfig()
}

const hasAnyUnsavedChanges = computed(() => agentStore.hasConfigChanges || hasProfileChanges.value)

const normalizeAgent = (agent) => {
  const agentId = agent?.id || agent?.agent_id || agent?.slug
  return agentId
    ? { ...agent, id: agentId, agent_id: agentId, slug: agent?.slug || agentId }
    : agent
}

const agentModalMenuItems = computed(() => {
  const items = [{ key: 'basic', label: '基本信息', icon: Bot }]
  if (!editingAgentId.value) {
    items.push(
      { key: 'tools', label: '能力与资源', icon: Wrench },
      { key: 'workflow', label: '编排', icon: Workflow }
    )
    return items
  }
  items.push(
    { key: 'model', label: '模型配置', icon: SlidersHorizontal },
    { key: 'tools', label: '工具包配置', icon: Wrench },
    { key: 'workflow', label: '编排', icon: Workflow }
  )
  return items
})

const showAgentModalSidebar = computed(() => agentModalMenuItems.value.length > 1)
const runtimeConfigSegment = computed(() =>
  runtimeAgentModalTabs.includes(agentModalActiveTab.value) ? agentModalActiveTab.value : 'model'
)
const isRuntimeAgentModalTab = (key) => runtimeAgentModalTabs.includes(key)
const getDefaultBackendId = () => DEFAULT_AGENT_BACKEND_ID
const isSubagent = computed(() => agentForm.execution_role === 'subagent')
const workflowConfig = computed(() =>
  editingAgentId.value ? agentStore.agentConfig : draftAgentConfig.value
)
const workflowSubagents = computed(() => {
  const items = editingAgentId.value ? agentStore.configurableItems : draftConfigItems.value
  return items?.subagents?.options || []
})

const updateWorkflow = (workflow) => {
  const next = workflow?.nodes?.length ? workflow : null
  if (editingAgentId.value) agentStore.updateAgentConfig({ subagent_workflow: next })
  else draftAgentConfig.value = { ...draftAgentConfig.value, subagent_workflow: next }
  agentForm.delegation_enabled = Boolean(next)
}

const updateWorkflowSubagents = (subagents) => {
  if (editingAgentId.value) agentStore.updateAgentConfig({ subagents })
  else draftAgentConfig.value = { ...draftAgentConfig.value, subagents }
}

const openWorkflowTab = () => {
  if (!editingAgentId.value) return
  showAgentModal.value = false
  router.push({ name: 'agent-manage', query: { tab: 'orchestration', agent: editingAgentId.value } })
}

const getInitialShareConfig = () => ({
  version: 2,
  read_scope: {
    access_level: 'user',
    user_uids: userStore.uid ? [userStore.uid] : []
  },
  manage_scope: null
})

const normalizeShareConfigForPayload = () => {
  if (isBuiltinAgent({ id: editingAgentId.value })) {
    return {
      version: 2,
      read_scope: { access_level: 'global', user_uids: [] },
      manage_scope: null
    }
  }
  return agentShareConfig.value || getInitialShareConfig()
}

const isEditingBuiltinAgent = computed(() => isBuiltinAgent({ id: editingAgentId.value }))
const canEditAgentShareConfig = computed(() => !isEditingBuiltinAgent.value)
const getAgentShareAllowedLevels = () => {
  if (isEditingBuiltinAgent.value) return ['global']
  if (userStore.isAdmin) return ['global', 'user']
  return ['user']
}

const agentModalTitle = computed(() => (editingAgentId.value ? '编辑智能体' : '新增智能体'))
const agentPreviewDefaultIcon = computed(() =>
  editingAgentId.value ? generatePixelAvatar(editingAgentId.value) : ''
)
const agentPreviewName = computed(() => agentForm.name || editingAgentId.value || '智能体')
const selectedBackendOption = computed(() =>
  props.backendOptions.find((backend) => backend.value === agentForm.backend_id)
)
const selectedBackendLabel = computed(
  () => selectedBackendOption.value?.label || agentForm.backend_id || '未选择'
)
const selectedBackendIcon = computed(() => {
  const backendText = `${agentForm.backend_id} ${selectedBackendLabel.value}`.toLowerCase()
  return backendText.includes('deep') || backendText.includes('search') ? Microscope : Bot
})

const generateDefaultAgentProfile = () => {
  const stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, '')
  return {
    name: '新建智能体',
    slug: `agent-${stamp}`
  }
}

const resetAgentForm = () => {
  const defaults = editingAgentId.value ? {} : generateDefaultAgentProfile()
  Object.assign(agentForm, {
    slug: '',
    name: '',
    backend_id: getDefaultBackendId(),
    execution_role: 'standalone',
    delegation_enabled: false,
    description: '',
    icon: '',
    ...defaults
  })
  agentShareConfig.value = getInitialShareConfig()
  draftConfigItems.value = {}
  draftAgentConfig.value = {}
}

const loadDraftConfig = async () => {
  draftConfigLoading.value = true
  try {
    const items = await agentStore.fetchConfigurableItems(agentForm.backend_id)
    draftConfigItems.value = items
    const config = {}
    Object.entries(items).forEach(([key, item]) => {
      if (item.kind === 'llm') config[key] = item.default || ''
      else if (item.type === 'list') {
        config[key] = (item.options || []).filter((option) => option.fixed).map((option) => option.slug)
      }
      else if (item.default !== undefined) config[key] = item.default
    })
    draftAgentConfig.value = config
  } catch (error) {
    message.error(error.message || '加载可选能力失败')
  } finally {
    draftConfigLoading.value = false
  }
}

const focusAgentNameInput = async () => {
  await nextTick()
  let el = agentNameInputRef.value
  if (!el) {
    // after-open-change 可能在 input 还没挂载时触发，这里兜底
    await new Promise((resolve) => setTimeout(resolve, 50))
    el = agentNameInputRef.value
  }
  if (!el) return
  el.focus?.()
  el.select?.()
}

const handleAgentModalAfterOpenChange = (open) => {
  if (open && !editingAgentId.value) focusAgentNameInput()
}

const openCreate = () => {
  editingAgentId.value = null
  agentModalActiveTab.value = 'basic'
  resetAgentForm()
  agentStore.resetAgentConfig()
  showAgentModal.value = true
  loadDraftConfig()
  focusAgentNameInput()
}

watch(
  () => agentForm.execution_role,
  (role) => {
    if (role === 'subagent') agentForm.delegation_enabled = false
  }
)

const openEdit = async (agent) => {
  const agentId = typeof agent === 'string' ? agent : agent?.id
  if (!agentId) return

  const detail = await agentStore.fetchAgentDetail(agentId, true)
  if (!detail?.can_manage) {
    message.warning('当前智能体不可编辑')
    return
  }

  editingAgentId.value = detail.id
  agentModalActiveTab.value = 'basic'
  Object.assign(agentForm, {
    slug: detail.id || detail.slug || '',
    name: detail.name || '',
    backend_id: detail.backend_id || DEFAULT_AGENT_BACKEND_ID,
    execution_role: detail.execution_role || 'standalone',
    delegation_enabled: Boolean(detail.delegation_enabled),
    description: detail.description || '',
    icon: detail.icon || ''
  })
  agentShareConfig.value = isBuiltinAgent(detail)
    ? {
        version: 2,
        read_scope: { access_level: 'global', user_uids: [] },
        manage_scope: null
      }
    : detail.share_config || getInitialShareConfig()
  await agentStore.selectAgent(detail.id, { allowSubagent: true })
  captureProfileBaseline()
  showAgentModal.value = true
}

const restoreChatAgentSelectionIfNeeded = async () => {
  if (agentStore.selectedAgent?.execution_role !== 'subagent') return
  const fallbackAgentId = (agentStore.agents || []).find((agent) => agent.execution_role !== 'subagent')?.id
  if (fallbackAgentId) await agentStore.selectAgent(fallbackAgentId)
}

const closeAgentModal = async () => {
  if (saving.value || agentIconUploading.value) return
  showAgentModal.value = false
  await restoreChatAgentSelectionIfNeeded()
}

const beforeAgentIconUpload = (file) => {
  if (!file.type.startsWith('image/')) {
    message.error('只能上传图片文件')
    return false
  }

  if (file.size > MAX_IMAGE_UPLOAD_SIZE_BYTES) {
    message.error(`图片大小不能超过 ${MAX_IMAGE_UPLOAD_SIZE_MB}MB`)
    return false
  }

  uploadAgentIcon(file)
  return false
}

const uploadAgentIcon = async (file) => {
  agentIconUploading.value = true
  try {
    const data = await userApi.uploadImage(file)
    agentForm.icon = data.image_url || data.url || ''
    message.success('图标上传成功')
  } catch (error) {
    message.error(error.message || '图标上传失败')
  } finally {
    agentIconUploading.value = false
  }
}

const buildAgentPayload = () => {
  const payload = {
    name: agentForm.name.trim(),
    description: agentForm.description.trim() || null,
    icon: agentForm.icon.trim() || null,
    share_config: normalizeShareConfigForPayload(),
    execution_role: agentForm.execution_role,
    delegation_enabled: agentForm.execution_role === 'standalone' && agentForm.delegation_enabled
  }

  if (!editingAgentId.value) {
    payload.slug = agentForm.slug.trim() || undefined
    payload.backend_id = agentForm.backend_id
    payload.config_json = { context: runtimeConfigFormRef.value?.validateAndFilterConfig?.() || draftAgentConfig.value }
  }

  return payload
}

const saveAgent = async () => {
  if (!agentForm.name.trim()) {
    agentModalActiveTab.value = 'basic'
    message.error('请填写智能体名称')
    return
  }

  const validation = canEditAgentShareConfig.value
    ? agentShareConfigFormRef.value?.validate?.()
    : null
  if (validation && !validation.valid) {
    agentModalActiveTab.value = 'basic'
    message.error(validation.message)
    return
  }

  saving.value = true
  try {
    const payload = buildAgentPayload()
    if (editingAgentId.value) {
      const validatedConfig = runtimeConfigFormRef.value?.validateAndFilterConfig?.()
      if (
        validatedConfig &&
        JSON.stringify(validatedConfig) !== JSON.stringify(agentStore.agentConfig)
      ) {
        agentStore.updateAgentConfig(validatedConfig)
      }
      if (agentStore.hasConfigChanges) {
        payload.config_json = { context: agentStore.agentConfig }
      }
      const updated = await agentStore.updateAgentProfile(editingAgentId.value, payload)
      agentStore.originalAgentConfig = { ...agentStore.agentConfig }
      captureProfileBaseline()
      emit('saved', { mode: 'edit', agent: updated })
      message.success('智能体已保存')
    } else {
      const created = await agentStore.createAgent(payload)
      emit('saved', { mode: 'create', agent: normalizeAgent(created) })
      message.success('智能体已创建')
    }
    showAgentModal.value = false
    await restoreChatAgentSelectionIfNeeded()
  } catch (error) {
    message.error(error.message || '保存智能体失败')
  } finally {
    saving.value = false
  }
}

defineExpose({
  openCreate,
  openEdit,
  close: closeAgentModal
})
</script>

<template>
  <a-modal
    v-model:open="showAgentModal"
    class="agent-edit-modal"
    :width="820"
    :footer="null"
    :closable="false"
    @cancel="closeAgentModal"
    @after-open-change="handleAgentModalAfterOpenChange"
  >
    <template #title>
      <div class="agent-modal-titlebar">
        <span class="agent-modal-title">{{ agentModalTitle }}</span>
        <div class="agent-modal-actions" v-if="hasAnyUnsavedChanges || !editingAgentId">
          <a-button size="small" :disabled="saving" @click="closeAgentModal">取消</a-button>
          <a-button size="small" type="primary" :loading="saving" @click="saveAgent">
            {{ editingAgentId ? '保存（有修改）' : '创建' }}
          </a-button>
        </div>
      </div>
    </template>
    <div
      class="agent-modal-content"
      :class="{
        'without-sidebar': !showAgentModalSidebar,
        'create-mode': !editingAgentId
      }"
    >
      <aside v-if="showAgentModalSidebar" class="agent-modal-sidebar" aria-label="智能体配置分组">
        <button
          v-for="item in agentModalMenuItems"
          :key="item.key"
          type="button"
          class="agent-modal-nav-item"
          :class="{ active: agentModalActiveTab === item.key }"
          @click="agentModalActiveTab = item.key"
        >
          <span class="nav-item-main">
            <component :is="item.icon" :size="16" />
            <span>{{ item.label }}</span>
          </span>
          <span v-if="item.key === 'model' && agentStore.hasConfigChanges" class="nav-dirty-dot" />
        </button>
      </aside>

      <div class="agent-modal-main">
        <section v-show="agentModalActiveTab === 'basic'" class="agent-modal-section">
          <div class="agent-profile-header">
            <div class="agent-icon-preview" aria-label="智能体图标、名称与后端">
              <div class="agent-profile-main">
                <a-upload
                  :show-upload-list="false"
                  :before-upload="beforeAgentIconUpload"
                  :disabled="agentIconUploading"
                  accept="image/*"
                >
                  <div
                    class="agent-icon-upload"
                    :class="{
                      uploading: agentIconUploading,
                      'is-empty': !agentForm.icon && !editingAgentId
                    }"
                  >
                    <FallbackAvatar
                      v-if="agentForm.icon || editingAgentId"
                      :src="agentForm.icon"
                      :default-src="agentPreviewDefaultIcon"
                      :name="agentPreviewName"
                      :seed="editingAgentId || agentForm.slug || agentForm.name"
                      kind="agent"
                      :size="56"
                      shape="rounded"
                      :alt="`${agentForm.name || '智能体'}图标`"
                      class="agent-icon-preview-avatar"
                    />
                    <div class="agent-icon-mask">
                      <RefreshCw v-if="agentIconUploading" :size="16" class="spinning" />
                      <Upload v-else :size="16" />
                      <span>{{ agentForm.icon ? '更换图标' : '上传图标' }}</span>
                    </div>
                  </div>
                </a-upload>
                <div class="agent-icon-preview-text">
                  <input
                    ref="agentNameInputRef"
                    v-model="agentForm.name"
                    class="agent-inline-name-input"
                    type="text"
                    placeholder="点击输入智能体名称"
                    aria-label="智能体名称"
                  />
                  <input
                    v-if="!editingAgentId"
                    v-model="agentForm.slug"
                    class="agent-inline-slug-input"
                    type="text"
                    placeholder="标识可选，留空自动生成"
                    aria-label="智能体标识"
                  />
                  <span v-else class="agent-inline-slug">{{
                    agentForm.slug || editingAgentId
                  }}</span>
                </div>
              </div>
              <div
                class="agent-backend-summary"
                :class="{ editable: !editingAgentId }"
                aria-label="智能体后端"
              >
                <span class="agent-backend-icon">
                  <component :is="selectedBackendIcon" :size="16" />
                </span>
                <div class="agent-backend-text">
                  <span class="agent-backend-label">智能体后端</span>
                  <a-select
                    v-if="!editingAgentId"
                    v-model:value="agentForm.backend_id"
                    class="agent-backend-select"
                    :bordered="false"
                    :options="backendOptions"
                    @change="loadDraftConfig"
                  />
                  <span v-else class="agent-backend-name">{{ selectedBackendLabel }}</span>
                </div>
              </div>
            </div>
          </div>
          <div class="modal-form">
            <label class="form-label full-width">
              <span>描述</span>
              <a-textarea
                v-model:value="agentForm.description"
                class="agent-description-textarea"
                :rows="3"
                placeholder="可选"
              />
            </label>
            <label class="form-label full-width">
              <span>运行角色</span>
              <a-radio-group v-model:value="agentForm.execution_role" class="agent-role-group" :disabled="isEditingBuiltinAgent">
                <a-radio value="standalone">独立 Agent</a-radio>
                <a-radio value="subagent">子智能体</a-radio>
              </a-radio-group>
              <span class="form-hint">子智能体仅由已授权的协调 Agent 调用，不出现在新建对话入口。</span>
            </label>
            <div v-if="!isSubagent" class="delegation-setting">
              <div>
                <span class="setting-title">任务编排</span>
                <span class="form-hint">在编排画布添加节点后自动启用；连线定义子智能体的执行顺序。</span>
              </div>
              <a-button size="small" @click="agentModalActiveTab = 'workflow'">
                {{ workflowConfig.subagent_workflow?.nodes?.length ? '编辑编排' : '开始编排' }}
              </a-button>
            </div>
          </div>

          <div v-if="canEditAgentShareConfig" class="share-config-block">
            <div class="section-heading">
              <span>共享权限</span>
            </div>
            <ShareConfigForm
              ref="agentShareConfigFormRef"
              v-model="agentShareConfig"
              :auto-select-current-user="true"
              :allowed-access-levels="getAgentShareAllowedLevels()"
            />
          </div>
        </section>

        <section
          v-show="isRuntimeAgentModalTab(agentModalActiveTab)"
          class="agent-modal-section runtime-section"
        >
          <AgentRuntimeConfigForm
            v-if="editingAgentId && agentModalActiveTab !== 'workflow'"
            ref="runtimeConfigFormRef"
            :segment="runtimeConfigSegment"
            :show-segmented="false"
            :allow-subagents="agentForm.execution_role === 'standalone' && agentForm.delegation_enabled"
          />
          <AgentCreationConfig
            v-else-if="!editingAgentId && agentModalActiveTab !== 'workflow'"
            ref="runtimeConfigFormRef"
            v-model="draftAgentConfig"
            :configurable-items="draftConfigItems"
            :loading="draftConfigLoading"
            :allow-subagents="agentForm.execution_role === 'standalone' && agentForm.delegation_enabled"
          />
          <template v-else>
            <a-alert
              v-if="isSubagent"
              type="info"
              show-icon
              message="子智能体不能继续委派任务"
              description="请将该智能体设为“独立 Agent”，再使用可视化编排。"
            />
            <AgentWorkflowCanvas
              v-else
              :disabled="Boolean(editingAgentId)"
              :model-value="workflowConfig.subagent_workflow || { nodes: [], edges: [] }"
              :subagents="workflowSubagents"
              @update:model-value="updateWorkflow"
              @update:subagents="updateWorkflowSubagents"
            />
            <a-alert v-if="editingAgentId" class="workflow-preview-hint" type="info" show-icon message="这是编排拓扑预览" description="请到“智能体管理 → 智能体编排”进行拖拽、连线和节点任务编辑。">
              <template #action><a-button type="link" size="small" @click="openWorkflowTab">打开编排</a-button></template>
            </a-alert>
          </template>
        </section>
      </div>
    </div>
  </a-modal>
</template>

<style lang="less" scoped>
.agent-modal-titlebar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  width: 100%;
}

.agent-modal-title {
  color: var(--gray-900);
  font-size: 16px;
  font-weight: 600;
}

.agent-modal-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;

  :deep(.ant-btn) {
    min-width: 56px;
    border-radius: 6px;
    font-weight: 500;
  }

  :deep(.ant-btn-primary) {
    border-color: var(--main-700);
    background: var(--main-700);

    &:hover,
    &:focus {
      border-color: var(--main-800);
      background: var(--main-800);
    }
  }
}

.agent-role-group {
  display: flex;
  gap: 20px;
  padding-top: 8px;
}

.form-hint {
  display: block;
  margin-top: 6px;
  color: var(--gray-600);
  font-size: 12px;
  line-height: 1.5;
}

.delegation-setting {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-50);

  .setting-title {
    display: block;
    color: var(--gray-800);
    font-size: 13px;
    font-weight: 500;
  }

  .form-hint {
    margin-top: 3px;
  }
}

.agent-modal-content {
  display: grid;
  grid-template-columns: 144px minmax(0, 1fr);
  height: min(72vh, 640px);
  min-height: 0;
  overflow: hidden;
  background: var(--gray-0);

  &.without-sidebar {
    grid-template-columns: minmax(0, 1fr);
  }

  &.create-mode {
    height: auto;
    min-height: 360px;
  }
}

.agent-modal-sidebar {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-height: 0;
  padding: 14px 10px;
  overflow-y: auto;
  border-right: 1px solid var(--gray-150);
  background: transparent;
}

.agent-modal-nav-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  min-height: 34px;
  padding: 6px 9px;
  border: 1px solid transparent;
  border-radius: 7px;
  background: transparent;
  color: var(--gray-800);
  font-size: 13px;
  font-weight: 500;
  text-align: left;
  cursor: pointer;
  transition:
    background 0.16s ease,
    border-color 0.16s ease,
    color 0.16s ease;

  &:hover {
    background: var(--gray-50);
    color: var(--gray-900);
  }

  &:focus-visible {
    outline: 2px solid var(--main-100);
    outline-offset: 1px;
    border-color: var(--main-200);
  }

  &.active {
    background: var(--gray-100);
    color: var(--gray-900);

    span {
      font-weight: 600;
    }
  }
}

.nav-item-main {
  display: inline-flex;
  align-items: center;
  min-width: 0;
  gap: 8px;

  svg {
    flex-shrink: 0;
    color: var(--gray-600);
  }
}

.agent-modal-nav-item.active .nav-item-main svg {
  color: var(--gray-700);
}

.nav-dirty-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--color-warning-600);
}

.agent-modal-main {
  min-width: 0;
  min-height: 0;
  overflow: hidden auto;
  overscroll-behavior: contain;
  padding: 22px 18px 24px 24px;
  scrollbar-gutter: stable;
  scrollbar-width: thin;
  scrollbar-color: var(--gray-300) transparent;

  &::-webkit-scrollbar {
    width: 6px;
  }

  &::-webkit-scrollbar-track {
    background: transparent;
  }

  &::-webkit-scrollbar-thumb {
    border: 2px solid transparent;
    border-radius: 999px;
    background: var(--gray-300);
    background-clip: content-box;
  }

  &::-webkit-scrollbar-thumb:hover {
    background: var(--gray-400);
    background-clip: content-box;
  }
}

.agent-modal-section {
  min-height: 0;
  background: var(--gray-0);
}

.runtime-section {
  display: flex;
  flex-direction: column;
  min-height: 100%;

  :deep(.agent-runtime-config-form) {
    display: flex;
    flex: 1;
    flex-direction: column;
    min-height: 0;
    background: transparent;
  }

  :deep(.runtime-config-content) {
    flex: 1;
    min-width: 0;
    min-height: 0;
    padding: 0;
    overflow: visible;
  }
}

.section-heading {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
  color: var(--gray-900);
  font-size: 14px;
  font-weight: 600;
}

.agent-profile-header {
  margin-bottom: 16px;
}

.agent-icon-preview {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  min-width: 0;
  gap: 16px;

  :deep(.ant-upload) {
    display: block;
  }
}

.agent-profile-main {
  display: inline-flex;
  align-items: center;
  min-width: 0;
  gap: 10px;
}

.agent-icon-upload {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 56px;
  height: 56px;
  overflow: hidden;
  border: 1px solid var(--gray-200);
  border-radius: 12px;
  background: var(--main-30);
  cursor: pointer;
  transition:
    border-color 0.16s ease,
    box-shadow 0.16s ease;

  .agent-icon-preview-avatar {
    width: 100%;
    height: 100%;
    border: 0;
  }

  &:hover,
  &:focus-within,
  &.uploading {
    border-color: var(--main-300);
    box-shadow: 0 0 0 3px var(--main-50);
  }

  &:hover .agent-icon-mask,
  &:focus-within .agent-icon-mask,
  &.uploading .agent-icon-mask,
  &.is-empty .agent-icon-mask {
    opacity: 1;
  }

  &.is-empty {
    border-style: dashed;
    background: var(--gray-0);
  }
}

.agent-icon-mask {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  background: color-mix(in srgb, var(--gray-900) 62%, transparent);
  color: var(--gray-0);
  font-size: 11px;
  font-weight: 600;
  opacity: 0;
  transition: opacity 0.16s ease;
}

.agent-icon-upload.is-empty .agent-icon-mask {
  background: transparent;
  color: var(--gray-600);
}

.agent-icon-preview-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
  gap: 4px;
  line-height: 1.25;
}

.agent-inline-name-input {
  width: 200px;
  max-width: 100%;
  padding: 1px 4px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--gray-900);
  caret-color: var(--main-700);
  font-size: 14px;
  font-weight: 600;
  line-height: 1.35;
  transition:
    border-color 0.16s ease,
    background 0.16s ease,
    box-shadow 0.16s ease;

  &::placeholder {
    color: var(--gray-400);
  }

  &:hover {
    border-color: var(--gray-300);
    background: var(--gray-0);
  }

  &:focus {
    border-color: var(--main-300);
    background: var(--gray-0);
    box-shadow: 0 0 0 3px var(--main-50);
    outline: none;
  }
}

.agent-inline-slug,
.agent-inline-slug-input {
  padding: 1px 4px;
  width: 200px;
  max-width: 100%;
  overflow: hidden;
  color: var(--gray-500);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-inline-slug-input {
  border: 1px solid transparent;
  border-radius: 2px;
  background: transparent;

  &::placeholder {
    color: var(--gray-400);
  }

  &:hover,
  &:focus {
    border-color: var(--gray-300);
    background: var(--gray-0);
    outline: none;
  }
}

.agent-backend-summary {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
  gap: 10px;
  width: 190px;
  min-height: 56px;
  padding: 10px 12px;
  border: 1px solid var(--gray-200);
  border-radius: 12px;
  background: var(--gray-10);
  color: var(--gray-700);

  &.editable {
    padding-right: 8px;
  }
}

.agent-backend-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  border-radius: 10px;
  background: var(--gray-100);
  color: var(--gray-700);
}

.agent-backend-text {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  gap: 3px;
  line-height: 1.2;
}

.agent-backend-label {
  color: var(--gray-500);
  font-size: 11px;
}

.agent-backend-name {
  max-width: 128px;
  overflow: hidden;
  color: var(--gray-900);
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-backend-select {
  width: 128px;
  margin: -3px 0 -5px -11px;

  :deep(.ant-select-selector) {
    background: transparent !important;
    box-shadow: none !important;
  }

  :deep(.ant-select-selection-item) {
    color: var(--gray-900);
    font-size: 13px;
    font-weight: 600;
  }

  :deep(.ant-select-arrow) {
    color: var(--gray-500);
  }
}

.share-config-block {
  margin-top: 22px;
  padding-top: 18px;
  border-top: 1px solid var(--gray-150);
}

.modal-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.form-label {
  display: flex;
  flex-direction: column;
  gap: 6px;

  > span {
    color: var(--gray-700);
    font-size: 12px;
    font-weight: 500;
  }
}

.agent-description-textarea {
  min-height: 80px;
  padding: 10px 12px;
  border-color: var(--gray-200);
  border-radius: 8px;
  background: var(--gray-10);
  color: var(--gray-900);
  font-size: 13px;
  line-height: 1.6;
  resize: vertical;
  transition:
    border-color 0.16s ease,
    background 0.16s ease,
    box-shadow 0.16s ease;

  &::placeholder {
    color: var(--gray-400);
  }

  &:hover {
    border-color: var(--gray-300);
    background: var(--gray-0);
  }

  &:focus {
    border-color: var(--main-300);
    background: var(--gray-0);
    box-shadow: 0 0 0 3px var(--main-50);
  }
}

.full-width {
  grid-column: 1 / -1;
}

.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 768px) {
  .agent-modal-content {
    grid-template-columns: 1fr;
    height: min(78vh, 680px);
  }

  .agent-modal-sidebar {
    flex-direction: row;
    overflow-x: auto;
    border-right: 0;
    border-bottom: 1px solid var(--gray-150);
  }
}

:global(.agent-edit-modal .ant-modal-content) {
  overflow: hidden;
  padding: 0;
  border-radius: 12px;
}

:global(.agent-edit-modal .ant-modal-header) {
  margin: 0;
  padding: 18px 24px;
  border-bottom: 1px solid var(--gray-150);
  background: var(--gray-0);
}

:global(.agent-edit-modal .ant-modal-title) {
  width: 100%;
}

:global(.agent-edit-modal .ant-modal-body) {
  padding: 0;
}
</style>
