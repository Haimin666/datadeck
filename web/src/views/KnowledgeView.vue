<template>
  <div class="knowledge-view layout-container">
    <PageHeader title="知识库" :show-border="true">
      <template #actions>
        <a-button type="primary" class="lucide-icon-btn" @click="showCreate = true">
          <template #icon><Plus :size="16" /></template>新建知识库
        </a-button>
      </template>
    </PageHeader>

    <PageShoulder v-model:search="search" search-placeholder="搜索知识库...">
      <template #filters><a-select v-model:value="selectedId" style="width: 220px" placeholder="选择知识库" @change="selectById"><a-select-option v-for="item in filteredDatabases" :key="item.id" :value="item.id">{{ item.name }}</a-select-option></a-select></template>
    </PageShoulder>

    <div class="knowledge-content">
      <a-spin :spinning="loading">
        <template v-if="selected">
          <section class="summary-card">
            <div class="summary-icon"><Database :size="20" /></div>
            <div class="summary-copy"><h2>{{ selected.name }}</h2><p>{{ selected.description || '暂无描述' }}</p></div>
            <div class="summary-stats"><span><b>{{ selected.document_count }}</b> 文档</span><span><b>{{ selected.chunk_count }}</b> 切片</span></div>
          </section>
          <div class="knowledge-grid">
            <section class="panel-card">
              <div class="panel-title"><div><h3>文档</h3><p>支持 TXT、Markdown、CSV、JSON，单文件不超过 20 MB。</p></div><a-upload :show-upload-list="false" :before-upload="handleUpload" accept=".txt,.md,.markdown,.csv,.json"><a-button class="lucide-icon-btn" :loading="uploading"><template #icon><Upload :size="15" /></template>上传文本</a-button></a-upload></div>
              <a-table v-if="documents.length" :data-source="documents" :pagination="false" row-key="id" size="small"><a-table-column title="文件名" data-index="filename" /><a-table-column title="切片" data-index="chunk_count" :width="90" /><a-table-column title="状态" :width="120"><template #default="{ record }"><a-tag :color="record.status === 'indexed' ? 'green' : 'blue'">{{ statusLabel(record.status) }}</a-tag></template></a-table-column><a-table-column title="操作" :width="64"><template #default="{ record }"><a-button type="text" danger @click="removeDocument(record)"><Trash2 :size="15" /></a-button></template></a-table-column></a-table>
              <a-empty v-else description="暂无文档，请上传文本资料" />
            </section>
            <section class="panel-card">
              <div class="panel-title"><div><h3>检索测试</h3><p>输入问题验证当前知识库的召回结果。</p></div></div>
              <a-textarea v-model:value="query" :rows="4" placeholder="输入一个问题..." @keydown.ctrl.enter="runQuery" />
              <a-button type="primary" class="query-button" :loading="querying" :disabled="!query.trim()" @click="runQuery">检索</a-button>
              <div v-if="queryResult" class="query-results"><p class="result-meta">{{ queryResult.strategy }} · {{ queryResult.results.length }} 条结果</p><div v-for="(result, index) in queryResult.results" :key="`${result.document_id}-${index}`" class="result-item"><b>{{ result.filename }}</b><p>{{ result.content }}</p></div><a-empty v-if="!queryResult.results.length" description="未检索到相关内容" /></div>
            </section>
          </div>
        </template>
        <a-empty v-else class="knowledge-empty" description="暂无知识库"><template #image><Database :size="42" /></template><a-button type="primary" @click="showCreate = true">新建知识库</a-button></a-empty>
      </a-spin>
    </div>

    <a-modal v-model:open="showCreate" title="新建知识库" ok-text="创建" cancel-text="取消" :confirm-loading="creating" @ok="createDatabase"><a-form layout="vertical"><a-form-item label="名称" required><a-input v-model:value="form.name" placeholder="例如：产品规范库" /></a-form-item><a-form-item label="描述"><a-textarea v-model:value="form.description" :rows="3" placeholder="说明知识库的内容和适用范围" /></a-form-item></a-form></a-modal>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { Database, Plus, Trash2, Upload } from '@lucide/vue'
import { knowledgeBaseApi } from '@/apis/knowledge_api'
import PageHeader from '@/components/shared/PageHeader.vue'
import PageShoulder from '@/components/shared/PageShoulder.vue'

const databases = ref([]); const selected = ref(null); const documents = ref([]); const selectedId = ref(); const search = ref('')
const loading = ref(false); const uploading = ref(false); const querying = ref(false); const query = ref(''); const queryResult = ref(null); const showCreate = ref(false); const creating = ref(false)
const form = reactive({ name: '', description: '' })
const filteredDatabases = computed(() => databases.value.filter((item) => item.name.toLowerCase().includes(search.value.toLowerCase())))
const load = async () => { loading.value = true; try { databases.value = (await knowledgeBaseApi.list()).databases || []; if (!selectedId.value && databases.value[0]) await selectDatabase(databases.value[0]) } catch (error) { message.error(error.message || '加载知识库失败') } finally { loading.value = false } }
const selectDatabase = async (item) => { selectedId.value = item.id; queryResult.value = null; const data = await knowledgeBaseApi.detail(item.id); selected.value = data; documents.value = data.documents || [] }
const selectById = () => { const item = databases.value.find((entry) => entry.id === selectedId.value); if (item) selectDatabase(item) }
const createDatabase = async () => { if (!form.name.trim()) return message.warning('请输入知识库名称'); creating.value = true; try { const item = await knowledgeBaseApi.create(form); showCreate.value = false; form.name = ''; form.description = ''; await load(); await selectDatabase(item); message.success('知识库已创建') } catch (error) { message.error(error.message || '创建失败') } finally { creating.value = false } }
const handleUpload = async (file) => { uploading.value = true; try { await knowledgeBaseApi.upload(selected.value.id, file); await selectDatabase(selected.value); await load(); message.success(`${file.name} 已加入索引`) } catch (error) { message.error(error.message || '上传失败') } finally { uploading.value = false } return false }
const removeDocument = (doc) => Modal.confirm({ title: '删除文档', content: `确定删除“${doc.filename}”吗？`, okType: 'danger', okText: '删除', cancelText: '取消', onOk: async () => { await knowledgeBaseApi.removeDocument(selected.value.id, doc.id); await selectDatabase(selected.value); await load(); message.success('文档已删除') } })
const runQuery = async () => { querying.value = true; try { queryResult.value = await knowledgeBaseApi.query(selected.value.id, { query: query.value, top_k: 5 }) } catch (error) { message.error(error.message || '检索失败') } finally { querying.value = false } }
const statusLabel = (status) => ({ indexed: '向量已索引', indexed_keyword: '关键词索引', indexing: '处理中' })[status] || status
onMounted(load)
</script>

<style scoped lang="less">
.knowledge-view { min-height: 100%; background: var(--light-60); }
.knowledge-content { padding: 16px var(--page-padding) 32px; }
.summary-card,.panel-card { border: 1px solid var(--gray-100); border-radius: 8px; background: var(--gray-0); }
.summary-card { display:flex; align-items:center; gap:12px; padding:18px; margin-bottom:16px; } .summary-icon { display:grid; place-items:center; width:38px; height:38px; color:var(--main-color); background:color-mix(in srgb,var(--main-color) 10%,var(--gray-0)); border-radius:8px; } .summary-copy { flex:1; min-width:0; } .summary-copy h2 { margin:0; color:var(--gray-2000); font-size:16px; } .summary-copy p { overflow:hidden; margin:5px 0 0; color:var(--gray-600); font-size:13px; text-overflow:ellipsis; white-space:nowrap; } .summary-stats { display:flex; gap:18px; color:var(--gray-500); font-size:12px; } .summary-stats b { margin-right:3px; color:var(--gray-1200); font-size:14px; }
.knowledge-grid { display:grid; grid-template-columns:minmax(0,1.3fr) minmax(300px,.7fr); gap:16px; } .panel-card { padding:18px; min-width:0; } .panel-title { display:flex; justify-content:space-between; gap:12px; align-items:flex-start; margin-bottom:16px; } .panel-title h3 { margin:0; color:var(--gray-1200); font-size:15px; } .panel-title p { margin:5px 0 0; color:var(--gray-500); font-size:12px; } .query-button { width:100%; margin-top:10px; } .query-results { margin-top:16px; } .result-meta { margin:0 0 8px; color:var(--gray-500); font-size:12px; } .result-item { padding:10px 0; border-top:1px solid var(--gray-100); } .result-item b { color:var(--gray-900); font-size:13px; } .result-item p { display:-webkit-box; overflow:hidden; margin:5px 0 0; color:var(--gray-600); font-size:12px; line-height:1.65; -webkit-box-orient:vertical; -webkit-line-clamp:4; } .knowledge-empty { padding:100px 0; }
@media (max-width: 900px) { .knowledge-grid { grid-template-columns:1fr; } .summary-stats { display:none; } }
</style>
