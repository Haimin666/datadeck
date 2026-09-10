<template>
  <div class="layout-container capability-page-root">
    <PageHeader title="定时任务" :show-border="true" />
    <CapabilityPlaceholder
      icon="hourglass"
      title="定时任务功能尚未启用"
      description="当前版本仅保留定时任务入口。后续启用时将提供任务调度、执行记录与失败告警。"
    />
  </div>
</template>

<script setup>
import CapabilityPlaceholder from '@/components/shared/CapabilityPlaceholder.vue'
import PageHeader from '@/components/shared/PageHeader.vue'
</script>

<style scoped>
.capability-page-root {
  display: flex;
  flex-direction: column;
  min-height: 0;
}
</style>
