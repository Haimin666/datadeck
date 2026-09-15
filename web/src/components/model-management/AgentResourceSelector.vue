<script setup>
import { computed, ref } from 'vue'
import { Check, Plus, Search } from '@lucide/vue'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  options: { type: Array, default: () => [] },
  placeholder: { type: String, default: '选择资源' },
  disabled: { type: Boolean, default: false }
})

const emit = defineEmits(['update:modelValue'])
const open = ref(false)
const search = ref('')
const category = ref('')
const draft = ref([])

const groups = computed(() => [...new Set(props.options.map((item) => item.group).filter(Boolean))])
const fixedValues = computed(() => new Set(props.options.filter((item) => item.disabled).map((item) => item.value)))
const filteredOptions = computed(() => {
  const keyword = search.value.trim().toLowerCase()
  return props.options.filter((item) => {
    if (category.value && item.group !== category.value) return false
    if (!keyword) return true
    return `${item.label} ${item.title}`.toLowerCase().includes(keyword)
  })
})

const openSelector = () => {
  if (props.disabled) return
  draft.value = [...(props.modelValue || [])]
  search.value = ''
  category.value = ''
  open.value = true
}

const toggle = (value) => {
  if (fixedValues.value.has(value)) return
  if (draft.value.includes(value)) draft.value = draft.value.filter((item) => item !== value)
  else draft.value = [...draft.value, value]
}

const selectAll = () => {
  const values = new Set(draft.value)
  filteredOptions.value.forEach((item) => values.add(item.value))
  draft.value = [...values]
}

const clearFiltered = () => {
  const visible = new Set(filteredOptions.value.map((item) => item.value))
  draft.value = draft.value.filter((value) => !visible.has(value) || fixedValues.value.has(value))
}

const confirm = () => {
  emit('update:modelValue', draft.value)
  open.value = false
}

const labelFor = (value) => props.options.find((item) => item.value === value)?.label || value
</script>

<template>
  <div class="resource-selector">
    <div class="selector-summary">
      <span v-if="!modelValue.length" class="selector-placeholder">{{ placeholder }}</span>
      <template v-if="modelValue.length">
        <a-tag v-for="value in modelValue" :key="value" class="selector-tag">
          {{ labelFor(value) }}
        </a-tag>
      </template>
      <a-button type="link" size="small" :disabled="disabled" @click="openSelector">
        {{ modelValue.length ? '编辑' : '选择' }}
      </a-button>
    </div>

    <a-modal v-model:open="open" :title="placeholder" :width="720" :footer="null">
      <div class="selector-toolbar">
        <a-input v-model:value="search" allow-clear placeholder="搜索资源">
          <template #prefix><Search :size="15" /></template>
        </a-input>
        <a-select v-model:value="category" allow-clear placeholder="全部分类" class="category-select">
          <a-select-option v-for="group in groups" :key="group" :value="group">
            {{ group }}
          </a-select-option>
        </a-select>
        <a-button size="small" @click="selectAll">全选</a-button>
        <a-button size="small" @click="clearFiltered">清空</a-button>
      </div>

      <div class="selector-list">
        <button
          v-for="item in filteredOptions"
          :key="item.value"
          type="button"
          class="selector-option"
          :class="{ selected: draft.includes(item.value), fixed: item.disabled }"
          :disabled="item.disabled"
          @click="toggle(item.value)"
        >
          <span>
            <small v-if="item.group">{{ item.group }} · </small>{{ item.label }}
            <small v-if="item.disabled" class="fixed-label">内置固定</small>
          </span>
          <Check v-if="draft.includes(item.value)" :size="16" />
          <Plus v-else :size="16" />
        </button>
        <a-empty v-if="!filteredOptions.length" description="暂无匹配资源" />
      </div>

      <div class="selector-footer">
        <span>已选择 {{ draft.length }} 项</span>
        <span>
          <a-button @click="open = false">取消</a-button>
          <a-button type="primary" @click="confirm">确认</a-button>
        </span>
      </div>
    </a-modal>
  </div>
</template>

<style scoped lang="less">
.resource-selector { width: 100%; }
.selector-summary {
  min-height: 32px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  padding: 4px 8px;
  border: 1px solid var(--gray-200);
  border-radius: 6px;
}
.selector-placeholder { color: var(--gray-500); font-size: 13px; flex: 1; }
.selector-tag { margin: 0; }
.selector-summary :deep(.ant-btn) { margin-left: auto; }
.selector-toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.selector-toolbar :deep(.ant-input-affix-wrapper) { flex: 1; }
.category-select { width: 130px; }
.selector-list { max-height: 360px; overflow: auto; display: grid; gap: 6px; }
.selector-option {
  display: flex; justify-content: space-between; align-items: center; width: 100%;
  padding: 9px 10px; border: 1px solid var(--gray-200); border-radius: 6px;
  background: var(--gray-50); color: var(--gray-800); text-align: left; cursor: pointer;
}
.selector-option.selected { border-color: var(--main-400); background: var(--main-50); color: var(--main-800); }
.selector-option.fixed { cursor: not-allowed; opacity: .75; }
.selector-option small { color: var(--gray-500); }
.selector-option .fixed-label { margin-left: 8px; color: var(--main-700); }
.selector-footer { display: flex; justify-content: space-between; align-items: center; margin-top: 16px; }
.selector-footer > span:last-child { display: flex; gap: 8px; }
</style>
