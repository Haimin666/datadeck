<script setup>
import { computed } from 'vue'
import ModelSelectorComponent from '@/components/ModelSelectorComponent.vue'

const props = defineProps({
  configurableItems: { type: Object, default: () => ({}) },
  modelValue: { type: Object, required: true },
  allowSubagents: { type: Boolean, default: false },
  loading: { type: Boolean, default: false }
})

const emit = defineEmits(['update:modelValue'])

const resourceKeys = computed(() =>
  props.allowSubagents ? ['tools', 'knowledges', 'skills', 'subagents'] : ['tools', 'knowledges', 'skills']
)

const update = (key, value) => emit('update:modelValue', { ...props.modelValue, [key]: value })
const optionsFor = (key) =>
  (props.configurableItems[key]?.options || []).map((item) => ({
    value: item.slug,
    label: item.group ? `${item.group} · ${item.name}` : item.name,
    title: item.fixed ? `${item.description || item.name}（内置固定）` : item.description || item.name,
    disabled: Boolean(item.fixed)
  }))
</script>

<template>
  <section class="creation-config" aria-label="智能体能力配置">
    <div class="creation-config-intro">
      <div>
        <h3>能力与资源</h3>
        <p>创建时一次配置模型与挂载资源；之后仍可随时修改。</p>
      </div>
      <a-spin v-if="loading" size="small" />
    </div>

    <a-form v-if="!loading" layout="vertical" class="creation-config-form">
      <a-form-item label="默认模型" :help="configurableItems.model?.description">
        <ModelSelectorComponent
          :model_spec="modelValue.model || ''"
          clearable
          @select-model="(value) => update('model', value)"
        />
      </a-form-item>

      <a-form-item
        v-for="key in resourceKeys"
        :key="key"
        :label="configurableItems[key]?.name"
        :help="configurableItems[key]?.description"
      >
        <a-select
          :value="modelValue[key] || []"
          mode="multiple"
          allow-clear
          show-search
          :placeholder="`选择${configurableItems[key]?.name || '资源'}`"
          :options="optionsFor(key)"
          :filter-option="(input, option) => String(option?.label || '').toLowerCase().includes(input.toLowerCase())"
          @update:value="(value) => update(key, value)"
        />
      </a-form-item>
    </a-form>
  </section>
</template>

<style lang="less" scoped>
.creation-config {
  padding: 4px 2px;
}

.creation-config-intro {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 4px 0 14px;
  border-bottom: 1px solid var(--gray-150);

  h3 {
    margin: 0;
    color: var(--gray-900);
    font-size: 14px;
    font-weight: 600;
  }

  p {
    margin: 5px 0 0;
    color: var(--gray-600);
    font-size: 12px;
  }
}

.creation-config-form {
  padding-top: 14px;

  :deep(.ant-form-item) {
    margin-bottom: 16px;
  }

  :deep(.ant-form-item-explain) {
    font-size: 12px;
  }
}
</style>
