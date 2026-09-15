<template>
  <div class="role-management">
    <div class="header-section">
      <div>
        <div class="section-title">角色管理</div>
        <p class="section-description">角色决定用户可进入的模块；内置角色保持固定，避免系统权限被误改。</p>
      </div>
      <a-button type="primary" @click="openCreate"><Plus :size="16" /> 新增角色</a-button>
    </div>

    <a-spin :spinning="loading">
      <a-alert v-if="error" type="error" :message="error" show-icon class="error-alert" />
      <a-table :data-source="roles" :pagination="false" row-key="slug" class="settings-table">
        <a-table-column title="角色" key="role" width="26%">
          <template #default="{ record }">
            <div class="role-name"><span>{{ record.name }}</span><code>{{ record.slug }}</code></div>
            <span class="role-description">{{ record.description || '暂无描述' }}</span>
          </template>
        </a-table-column>
        <a-table-column title="开放模块" key="permissions">
          <template #default="{ record }">
            <a-tag v-for="permission in record.permissions" :key="permission" class="module-tag">{{ moduleLabels[permission] || permission }}</a-tag>
            <span v-if="!record.permissions.length" class="muted">未开放模块</span>
            <div class="agent-assignment">
              <span class="muted">Agent：</span>
              <template v-if="record.agent_slugs?.length">
                <a-tag v-for="slug in record.agent_slugs" :key="slug" color="blue">{{ agentLabels[slug] || slug }}</a-tag>
              </template>
              <span v-else class="muted">不限</span>
            </div>
          </template>
        </a-table-column>
        <a-table-column title="操作" key="actions" width="110px" align="center">
          <template #default="{ record }">
            <a-tooltip :title="record.is_builtin ? '内置角色不可编辑' : '编辑角色'">
              <a-button type="text" size="small" :disabled="record.is_builtin" @click="openEdit(record)"><SquarePen :size="15" /></a-button>
            </a-tooltip>
            <a-tooltip :title="record.is_builtin ? '内置角色不可删除' : '删除角色'">
              <a-button type="text" danger size="small" :disabled="record.is_builtin" @click="confirmDelete(record)"><Trash2 :size="15" /></a-button>
            </a-tooltip>
          </template>
        </a-table-column>
      </a-table>
    </a-spin>

    <a-modal v-model:open="modalOpen" :title="editing ? '编辑角色' : '新增角色'" :confirm-loading="saving" @ok="saveRole" width="560px">
      <a-form layout="vertical">
        <a-form-item label="角色名称" required><a-input v-model:value="form.name" :maxlength="64" placeholder="例如：知识库运营" /></a-form-item>
        <a-form-item v-if="!editing" label="角色标识" required extra="仅限小写字母、数字、连字符和下划线，创建后不可修改。">
          <a-input v-model:value="form.slug" :maxlength="32" placeholder="knowledge-operator" />
        </a-form-item>
        <a-form-item label="说明"><a-textarea v-model:value="form.description" :rows="2" :maxlength="500" /></a-form-item>
        <a-form-item label="可访问模块">
          <a-checkbox-group v-model:value="form.permissions" class="permission-grid">
            <a-checkbox v-for="module in modules" :key="module" :value="module">{{ moduleLabels[module] }}</a-checkbox>
          </a-checkbox-group>
        </a-form-item>
        <a-form-item label="可使用 Agent" extra="不选择表示不限制；选择后用户只能使用这些 Agent。">
          <a-checkbox-group v-model:value="form.agent_slugs" class="permission-grid">
            <a-checkbox v-for="agent in agents" :key="agent.slug" :value="agent.slug">{{ agent.name }}</a-checkbox>
          </a-checkbox-group>
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { Plus, SquarePen, Trash2 } from '@lucide/vue'
import { authApi } from '@/apis'
import { agentApi } from '@/apis/agent_api'

const roles = ref([])
const modules = ref([])
const agents = ref([])
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const modalOpen = ref(false)
const editing = ref(false)
const moduleLabels = {
  conversations: '对话', agents: '智能体', workspace: '个人空间', knowledge: '知识库', extensions: '技能与工具',
  scheduled_tasks: '定时任务', metrics: '指标口径', settings: '基本设置', users: '用户管理'
}
const agentLabels = computed(() => Object.fromEntries(agents.value.map((agent) => [agent.slug, agent.name])))
const form = reactive({ slug: '', name: '', description: '', permissions: [], agent_slugs: [] })

const load = async () => {
  loading.value = true
  try {
    const [data, agentData] = await Promise.all([authApi.getRoles(), agentApi.getAgents({ includeSubagents: true })])
    roles.value = data.roles || []
    modules.value = data.modules || []
    agents.value = (agentData.agents || []).filter((agent) => agent.slug)
    error.value = ''
  } catch (err) {
    error.value = err.message || '获取角色列表失败'
  } finally {
    loading.value = false
  }
}

const resetForm = () => Object.assign(form, { slug: '', name: '', description: '', permissions: [], agent_slugs: [] })
const openCreate = () => { resetForm(); editing.value = false; modalOpen.value = true }
const openEdit = (role) => {
  Object.assign(form, { slug: role.slug, name: role.name, description: role.description || '', permissions: [...role.permissions], agent_slugs: [...(role.agent_slugs || [])] })
  editing.value = true
  modalOpen.value = true
}
const saveRole = async () => {
  const payload = { name: form.name.trim(), description: form.description.trim(), permissions: form.permissions, agent_slugs: form.agent_slugs }
  if (!payload.name) return message.error('角色名称不能为空')
  if (!editing.value) {
    payload.slug = form.slug.trim()
    if (!/^[a-z][a-z0-9_-]*$/.test(payload.slug)) return message.error('角色标识格式不正确')
  }
  saving.value = true
  try {
    if (editing.value) await authApi.updateRole(form.slug, payload)
    else await authApi.createRole(payload)
    message.success(editing.value ? '角色已更新' : '角色已创建')
    modalOpen.value = false
    await load()
  } catch (err) {
    message.error(err.message || '保存角色失败')
  } finally {
    saving.value = false
  }
}
const confirmDelete = (role) => Modal.confirm({
  title: '删除角色', content: `确认删除“${role.name}”吗？`, okType: 'danger',
  async onOk () { await authApi.deleteRole(role.slug); message.success('角色已删除'); await load() }
})

onMounted(load)
</script>

<style lang="less" scoped>
.role-management { .header-section { display:flex; justify-content:space-between; align-items:flex-end; gap:16px; margin:12px 0 16px; } .section-title { font-size:16px; font-weight:500; color:var(--gray-900); } .section-description,.role-description,.muted { display:block; margin:6px 0 0; color:var(--gray-600); font-size:13px; } .error-alert { margin-bottom:12px; } .role-name { display:flex; align-items:center; gap:8px; font-weight:500; } code { color:var(--gray-600); font-size:12px; } .module-tag { margin:2px 4px 2px 0; } .agent-assignment { margin-top:6px; } .agent-assignment .muted { display:inline; margin-right:4px; } .permission-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px 16px; } }
</style>
