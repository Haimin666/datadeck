<template>
  <div class="capability-placeholder">
    <div class="capability-card">
      <div class="capability-icon">
        <component :is="icon" :size="24" />
      </div>
      <h2>{{ title }}</h2>
      <p>{{ description }}</p>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Database, Hourglass, WandSparkles } from '@lucide/vue'

const props = defineProps({
  title: { type: String, required: true },
  description: { type: String, required: true },
  icon: { type: String, default: 'database' }
})

const icon = computed(() => ({
  database: Database,
  hourglass: Hourglass,
  skill: WandSparkles
})[props.icon] || Database)
</script>

<style scoped lang="less">
.capability-placeholder {
  display: flex;
  flex: 1;
  align-items: center;
  justify-content: center;
  min-height: 0;
  padding: var(--page-padding);
}

.capability-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: min(480px, 100%);
  min-height: 280px;
  padding: 40px 24px;
  border: 1px dashed var(--gray-150);
  border-radius: 16px;
  background: var(--gray-0);
  color: var(--gray-500);
  text-align: center;
}

.capability-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  margin-bottom: 12px;
  border-radius: 14px;
  background: var(--gray-50);
  color: var(--gray-600);
}

h2 {
  margin: 0;
  color: var(--gray-800);
  font-size: 16px;
  font-weight: 700;
}

p {
  max-width: 360px;
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 20px;
}
</style>
