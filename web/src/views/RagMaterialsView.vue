<script setup>
import { onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import PageHeader from '@/components/shared/PageHeader.vue'
import { knowledgeBaseApi } from '@/apis/knowledge_api'

defineProps({ embedded: { type: Boolean, default: false } })

const items = ref([])
const total = ref(0)
const hasMore = ref(false)
const loading = ref(false)
const previewVisible = ref(false)
const preview = ref(null)
const previewLoading = ref(false)
const query = ref('')
const sourceType = ref('')
const layer = ref('')
const sourceLabels = { wiki: 'Wiki', wiki_extract: 'Wiki 摘要', business_doc: '业务文档', code: '数仓代码', omd: 'OMD 元数据', ossie: 'Ossie 指标', ossie_candidate: 'Ossie 候选' }
const showPreview = async (item) => {
  preview.value = item
  previewVisible.value = true
  previewLoading.value = true
  try {
    preview.value = await knowledgeBaseApi.material(item.id)
  } catch (error) {
    message.error(error.message || '物料预览加载失败')
  } finally {
    previewLoading.value = false
  }
}

const load = async () => {
  loading.value = true
  try {
    const result = await knowledgeBaseApi.materials({ q: query.value, source_type: sourceType.value, layer: layer.value, limit: 10000, offset: 0 })
    items.value = result.items || []
    total.value = result.total ?? items.value.length
    hasMore.value = result.has_more ?? items.value.length < total.value
  } catch (error) {
    message.error(error.message || 'RAG 物料加载失败')
  } finally { loading.value = false }
}

onMounted(load)
</script>

<template>
  <div class="rag-materials-view" :class="{ 'embedded-view': embedded }">
    <PageHeader v-if="!embedded" title="RAG 数据物料" :show-border="true">
      <template #info><span class="summary">已整理 {{ total }} 条物料</span></template>
    </PageHeader>
    <div class="toolbar">
      <a-input v-model:value="query" placeholder="搜索标题或来源" allow-clear @press-enter="load" />
      <a-select v-model:value="sourceType" style="width: 150px" @change="load">
        <a-select-option value="">全部来源</a-select-option>
        <a-select-option v-for="(label, value) in sourceLabels" :key="value" :value="value">{{ label }}</a-select-option>
      </a-select>
      <a-select v-model:value="layer" style="width: 120px" @change="load">
        <a-select-option value="">全部分层</a-select-option>
        <a-select-option value="hot">热数据</a-select-option>
        <a-select-option value="cold">冷数据</a-select-option>
      </a-select>
      <a-button type="primary" :loading="loading" @click="load">刷新</a-button>
    </div>
    <a-table :data-source="items" :loading="loading" row-key="id" :pagination="{ pageSize: 20 }">
      <a-table-column title="标题" data-index="title" ellipsis />
      <a-table-column title="来源" key="source"><template #default="{ record }">{{ sourceLabels[record.source_type] || record.source_type }}</template></a-table-column>
      <a-table-column title="分层" key="layer"><template #default="{ record }"><a-tag :color="record.layer === 'hot' ? 'blue' : 'default'">{{ record.layer === 'hot' ? '热' : '冷' }}</a-tag></template></a-table-column>
      <a-table-column title="状态" data-index="status" />
      <a-table-column title="来源标识" data-index="source_id" ellipsis />
      <a-table-column title="更新时间" data-index="updated_at" />
      <a-table-column title="操作" :width="80"><template #default="{ record }"><a-button type="link" @click="showPreview(record)">预览</a-button></template></a-table-column>
    </a-table>
    <div class="material-footnote">已加载 {{ items.length }} / {{ total }} 条物料<span v-if="hasMore">，当前最多展示 10000 条，请缩小搜索范围</span></div>
    <a-modal v-model:open="previewVisible" :title="preview?.title || 'RAG 物料预览'" width="860px" :footer="null">
      <div v-if="preview" class="preview-meta">{{ sourceLabels[preview.source_type] || preview.source_type }} · {{ preview.source_id }}</div>
      <a-spin :spinning="previewLoading">
        <pre v-if="preview" class="preview-content">{{ preview.content || '暂无正文内容' }}</pre>
      </a-spin>
    </a-modal>
  </div>
</template>

<style scoped>
.rag-materials-view { min-height: 100%; background: var(--gray-0); color: var(--gray-1000); }
.toolbar { display: flex; gap: 12px; align-items: center; padding: 20px 24px 12px; }
.toolbar .ant-input { max-width: 360px; }
.rag-materials-view :deep(.ant-table-wrapper) { padding: 0 24px 24px; }
.summary { color: var(--gray-600); font-size: 13px; }
.material-footnote { padding: 0 24px 24px; color: var(--gray-500); font-size: 12px; text-align: center; }
.preview-meta { margin-bottom: 12px; color: var(--gray-500); font-size: 12px; }
.preview-content { max-height: 60vh; margin: 0; padding: 14px; overflow: auto; white-space: pre-wrap; word-break: break-word; color: var(--gray-900); background: var(--gray-50); border-radius: 6px; font: inherit; line-height: 1.6; }
</style>
