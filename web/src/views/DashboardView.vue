<script setup>
import { ref, onMounted } from 'vue'
import { dashboardApi } from '@/apis'

const stats = ref(null)
const loading = ref(false)
const error = ref(null)

onMounted(async () => {
  loading.value = true
  try {
    const data = await dashboardApi.getStats()
    stats.value = data
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="dashboard-view">
    <div class="dashboard-header">
      <h2>管理仪表盘</h2>
    </div>
    <div v-if="loading" class="dashboard-loading">
      <a-spin size="large" />
    </div>
    <div v-else-if="error" class="dashboard-error">
      <a-alert :message="error" type="error" />
    </div>
    <div v-else-if="stats" class="dashboard-stats">
      <a-row :gutter="16">
        <a-col :span="6">
          <a-card :bordered="false">
            <div class="stat-card">
              <div class="stat-value">{{ stats.total_users ?? '-' }}</div>
              <div class="stat-label">用户总数</div>
            </div>
          </a-card>
        </a-col>
        <a-col :span="6">
          <a-card :bordered="false">
            <div class="stat-card">
              <div class="stat-value">{{ stats.total_threads ?? '-' }}</div>
              <div class="stat-label">对话总数</div>
            </div>
          </a-card>
        </a-col>
        <a-col :span="6">
          <a-card :bordered="false">
            <div class="stat-card">
              <div class="stat-value">{{ stats.total_runs ?? '-' }}</div>
              <div class="stat-label">运行总数</div>
            </div>
          </a-card>
        </a-col>
        <a-col :span="6">
          <a-card :bordered="false">
            <div class="stat-card">
              <div class="stat-value">{{ stats.total_tokens ?? '-' }}</div>
              <div class="stat-label">Token 消耗</div>
            </div>
          </a-card>
        </a-col>
      </a-row>
    </div>
    <div v-else class="dashboard-empty">
      <a-empty description="暂无数据" />
    </div>
  </div>
</template>

<style lang="less" scoped>
.dashboard-view {
  flex: 1;
  overflow: auto;
  padding: 1.5rem;
  background: var(--gray-0);
}
.dashboard-header {
  h2 { margin: 0 0 1.5rem; font-size: 1.25rem; font-weight: 600; }
}
.dashboard-loading, .dashboard-error { display: flex; align-items: center; justify-content: center; min-height: 200px; }
.stat-card { text-align: center; padding: 0.5rem; }
.stat-value { font-size: 2rem; font-weight: 700; color: var(--main-600); }
.stat-label { font-size: 0.8125rem; color: var(--gray-500); margin-top: 0.25rem; }
</style>
