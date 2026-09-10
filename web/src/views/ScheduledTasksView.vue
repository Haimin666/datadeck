<template>
  <div class="scheduled-page layout-container">
    <PageHeader title="定时任务" :show-border="true">
      <template #actions><a-button type="primary" @click="openCreate"><Plus :size="15" /> 新建任务</a-button></template>
    </PageHeader>
    <div class="scheduled-content">
      <div class="module-intro"><Clock :size="20" /><div><span>自动化</span><h2>按计划运行重复工作</h2><p>创建 Cron 任务，指定智能体和提示词，集中管理启用状态。</p></div></div>
      <div class="toolbar"><span>任务列表 <em>{{ tasks.length }}</em></span><a-button @click="load" :loading="loading">刷新</a-button></div>
      <a-spin :spinning="loading"><div v-if="tasks.length" class="task-list"><div v-for="task in tasks" :key="task.id" class="task-row"><div class="task-icon"><Clock :size="17" /></div><div class="task-main"><strong>{{ task.name }}</strong><small><code>{{ task.cron }}</code> · {{ task.agent_slug }} · {{ projectName(task.project_id) }}</small><p>{{ task.prompt }}</p><p v-if="task.last_run_at" class="task-status">最近执行：{{ task.last_status || 'pending' }} · {{ task.last_run_at }}</p></div><a-switch :checked="task.enabled" checked-children="启用" un-checked-children="停用" @change="(enabled) => toggleTask(task, enabled)" /><a-button type="text" @click="openEdit(task)"><Pencil :size="15" /></a-button><a-button type="text" danger @click="removeTask(task)"><Trash2 :size="15" /></a-button></div></div><div v-else class="empty-state"><Clock :size="34" /><h3>暂无定时任务</h3><p>创建第一个任务，让智能体按计划执行工作。</p><a-button type="primary" @click="openCreate">新建任务</a-button></div></a-spin>
    </div>
    <a-modal v-model:open="modalOpen" :title="editing ? '编辑定时任务' : '新建定时任务'" ok-text="保存" cancel-text="取消" :confirm-loading="saving" @ok="save"><a-form layout="vertical"><a-form-item label="任务名称" required><a-input v-model:value="form.name" placeholder="例如：每日业务摘要" /></a-form-item><a-form-item label="Cron 表达式" required><a-input v-model:value="form.cron" placeholder="0 9 * * *" /><span class="form-help">格式：分 时 日 月 周，例如每天 09:00 为 0 9 * * *</span></a-form-item><a-form-item label="智能体" required><a-select v-model:value="form.agent_slug" placeholder="请选择智能体" :options="agents.map((agent) => ({ value: agent.slug, label: agent.name + ' · ' + agent.slug }))" /></a-form-item><a-form-item label="项目工作目录"><a-select v-model:value="form.project_id" allow-clear placeholder="默认新建任务目录" :options="projects.map((project) => ({ value: project.id, label: project.name || project.workdir_path }))" /></a-form-item><a-form-item label="提示词" required><a-textarea v-model:value="form.prompt" :rows="4" placeholder="请生成今日业务摘要并列出异常项" /></a-form-item><a-form-item label="创建后启用"><a-switch v-model:checked="form.enabled" /></a-form-item></a-form></a-modal>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { Clock, Pencil, Plus, Trash2 } from '@lucide/vue'
import { useRoute } from 'vue-router'
import PageHeader from '@/components/shared/PageHeader.vue'
import { agentApi } from '@/apis/agent_api'
import { scheduledTaskApi } from '@/apis/knowledge_api'
import { projectApi } from '@/apis/project_api'

const route = useRoute()
const tasks = ref([])
const agents = ref([])
const projects = ref([])
const loading = ref(false)
const saving = ref(false)
const modalOpen = ref(false)
const editing = ref(null)
const form = reactive({ name: '', cron: '', prompt: '', agent_slug: 'default-chatbot', project_id: undefined, enabled: true })
const resetForm = () => Object.assign(form, { name: '', cron: '', prompt: '', agent_slug: 'default-chatbot', project_id: undefined, enabled: true })
const loadAgents = async () => { try { agents.value = (await agentApi.getAgents({ includeSubagents: false })).agents || [] } catch (error) { message.error(error.message || '加载智能体失败') } }
const loadProjects = async () => { try { projects.value = await projectApi.getProjects() || [] } catch (error) { message.error(error.message || '加载项目失败') } }
const load = async () => { loading.value = true; try { tasks.value = (await scheduledTaskApi.list()).tasks || [] } catch (error) { message.error(error.message || '加载定时任务失败') } finally { loading.value = false } }
const openCreate = () => { editing.value = null; resetForm(); const slug = String(route.query.agent_slug || ''); if (agents.value.some((agent) => agent.slug === slug)) form.agent_slug = slug; modalOpen.value = true }
const openEdit = (task) => { editing.value = task; Object.assign(form, task); modalOpen.value = true }
const projectName = (id) => projects.value.find((project) => project.id === id)?.name || (id ? '项目' : '自动创建项目')
const save = async () => { if (!form.name.trim() || !form.cron.trim() || !form.prompt.trim()) return message.warning('请填写任务名称、Cron 和提示词'); saving.value = true; try { if (editing.value) await scheduledTaskApi.update(editing.value.id, form); else await scheduledTaskApi.create(form); modalOpen.value = false; await load(); message.success('定时任务已保存') } catch (error) { message.error(error.message || '保存失败') } finally { saving.value = false } }
const toggleTask = async (task, enabled) => { try { await scheduledTaskApi.update(task.id, { name: task.name, cron: task.cron, prompt: task.prompt, agent_slug: task.agent_slug, project_id: task.project_id, enabled }); task.enabled = enabled } catch (error) { message.error(error.message || '更新失败') } }
const removeTask = (task) => Modal.confirm({ title: '删除定时任务', content: `确定删除“${task.name}”吗？`, okType: 'danger', okText: '删除', cancelText: '取消', onOk: async () => { await scheduledTaskApi.remove(task.id); await load(); message.success('任务已删除') } })
onMounted(async () => { await Promise.all([loadAgents(), loadProjects(), load()]) })
</script>

<style scoped lang="less">
.scheduled-page { min-height:100%; background:var(--light-60); color:var(--gray-2000); } .scheduled-content { padding:24px var(--page-padding) 56px; max-width:1160px; margin:auto; } .module-intro { display:flex; gap:14px; align-items:flex-start; padding:20px 22px; border:1px solid var(--gray-100); border-radius:10px; background:var(--gray-0); color:var(--main-color); } .module-intro span { color:var(--gray-500); font-size:12px; } .module-intro h2 { margin:4px 0; color:var(--gray-1200); font-size:18px; } .module-intro p { margin:0; color:var(--gray-600); font-size:13px; } .toolbar { display:flex; justify-content:space-between; align-items:center; margin:28px 0 12px; color:var(--gray-900); font-size:14px; font-weight:600; } .toolbar em { margin-left:5px; color:var(--gray-500); font-style:normal; font-size:12px; } .task-list { border:1px solid var(--gray-100); border-radius:10px; background:var(--gray-0); } .task-row { display:flex; align-items:center; gap:14px; padding:17px 18px; border-bottom:1px solid var(--gray-100); } .task-row:last-child { border-bottom:0; } .task-icon { display:grid; place-items:center; flex:none; width:34px; height:34px; border-radius:8px; color:var(--main-color); background:color-mix(in srgb,var(--main-color) 10%,var(--gray-0)); } .task-main { flex:1; min-width:0; } .task-main strong,.task-main small,.task-main p { display:block; } .task-main strong { color:var(--gray-1200); font-size:14px; } .task-main small { margin-top:4px; color:var(--gray-500); font-size:11px; } code { color:var(--main-color); } .task-main p { overflow:hidden; margin:7px 0 0; color:var(--gray-600); font-size:12px; text-overflow:ellipsis; white-space:nowrap; } .empty-state { display:grid; justify-items:center; padding:90px 20px; border:1px dashed var(--gray-200); border-radius:10px; color:var(--gray-500); background:var(--gray-0); text-align:center; } .empty-state h3 { margin:14px 0 4px; color:var(--gray-900); font-size:17px; } .empty-state p { margin:0 0 18px; font-size:13px; } .form-help { display:block; margin-top:5px; color:var(--gray-500); font-size:11px; }
</style>
