<template>
  <component
    :is="currentRenderer"
    v-if="currentRenderer"
    :tool-call="toolCall"
    :appearance="appearance"
    :default-expanded="defaultExpanded"
    ref="toolRendererRef"
  />
  <BaseToolCall
    v-else-if="!isHidden"
    :tool-call="toolCall"
    :appearance="appearance"
    :default-expanded="defaultExpanded"
  />
</template>

<script setup>
import { computed, ref } from 'vue'
import BaseToolCall from './BaseToolCall.vue'

import TodoListTool from './tools/TodoListTool.vue'
import SubagentLifecycleTool from './tools/SubagentLifecycleTool.vue'
import ReadFileTool from './tools/ReadFileTool.vue'
import ListDirectoryTool from './tools/ListDirectoryTool.vue'
import SearchFileContentTool from './tools/SearchFileContentTool.vue'
import AskUserQuestionTool from './tools/AskUserQuestionTool.vue'
import ExecuteTool from './tools/ExecuteTool.vue'
import RememberMemoryTool from './tools/RememberMemoryTool.vue'
import { getToolCallId, isHiddenToolCall } from './toolRegistry'

const props = defineProps({
  toolCall: {
    type: Object,
    required: true
  },
  appearance: {
    type: String,
    default: 'card'
  },
  defaultExpanded: {
    type: Boolean,
    default: false
  }
})

const toolId = computed(() => getToolCallId(props.toolCall))

const TOOL_RENDERERS = {
  ask_user_question: AskUserQuestionTool,
  execute: ExecuteTool,
  read_file: ReadFileTool,
  list_directory: ListDirectoryTool,
  remember_memory: RememberMemoryTool,
  search_file_content: SearchFileContentTool,
  workspace_list_directory: ListDirectoryTool,
  workspace_read_file: ReadFileTool,
  workspace_search_files: SearchFileContentTool,
  subagent_await: SubagentLifecycleTool,
  subagent_cancel: SubagentLifecycleTool,
  subagent_events: SubagentLifecycleTool,
  subagent_start: SubagentLifecycleTool,
  subagent_status: SubagentLifecycleTool,
  subagent_orchestrate: SubagentLifecycleTool,
  write_todos: TodoListTool
}

const currentRenderer = computed(() => TOOL_RENDERERS[toolId.value] || null)
const isHidden = computed(() => isHiddenToolCall(props.toolCall))

const toolRendererRef = ref(null)
const refreshGraph = () => {
  if (toolRendererRef.value && typeof toolRendererRef.value.refreshGraph === 'function') {
    toolRendererRef.value.refreshGraph()
  }
}

defineExpose({ refreshGraph })
</script>

<style lang="less" scoped></style>
