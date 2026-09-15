import {
  Bot,
  Brain,
  CheckSquare,
  FileEdit,
  FileText,
  Folder,
  HelpCircle,
  RefreshCw,
  SquareTerminal
} from '@lucide/vue'

export const TOOL_ICON_MAP = {
  ask_user_question: HelpCircle,
  execute: SquareTerminal,
  list_directory: Folder,
  read_file: FileText,
  remember_memory: Brain,
  search_file_content: FileText,
  subagent_await: Bot,
  subagent_cancel: Bot,
  subagent_events: RefreshCw,
  subagent_orchestrate: Bot,
  subagent_start: Bot,
  subagent_status: RefreshCw,
  workspace_list_directory: Folder,
  workspace_read_file: FileText,
  workspace_search_files: FileText,
  workspace_write_file: FileEdit,
  write_todos: CheckSquare
}

// 前端兜底的工具显示名称：仅用于工具列表（availableTools）无法映射到 display name 的工具，
// 例如 FilesystemMiddleware / TodoListMiddleware 等 middleware 注入的工具。
// 内置工具与知识库工具的 display_name 由后端定义（@tool 装饰器），通过工具列表下发。
export const TOOL_NAME_MAP = {
  execute: '执行命令',
  list_directory: '列出目录',
  read_file: '读取文件',
  remember_memory: '更新记忆',
  search_file_content: '搜索文件内容',
  write_todos: '更新任务清单',
  subagent_start: '启动子智能体',
  subagent_status: '查询子智能体',
  subagent_events: '查看子智能体事件',
  subagent_cancel: '取消子智能体',
  subagent_await: '等待子智能体',
  subagent_orchestrate: '编排子智能体',
  ask_user_question: '向用户提问',
  workspace_list_directory: '列出工作区目录',
  workspace_read_file: '读取工作区文件',
  workspace_search_files: '搜索工作区文件',
  workspace_write_file: '写入工作区文件'
}

// Keep intentionally hidden tool calls centralized so group summaries and renderers stay consistent.
export const HIDDEN_TOOL_CALL_IDS = ['present_artifacts']

export const getToolCallId = (toolCall) => toolCall?.name || toolCall?.function?.name || ''

export const getToolName = (toolId) => TOOL_NAME_MAP[toolId] || toolId

// 从工具元数据列表（完整工具列表或 buildin options）中按工具 id 查找对应元数据
export const findToolInList = (toolId, toolsList) =>
  (toolsList || []).find((t) => (t.slug ?? t.key ?? t.id) === toolId)

export const isHiddenToolCall = (toolCall) => HIDDEN_TOOL_CALL_IDS.includes(getToolCallId(toolCall))

export const isValidToolCall = (toolCall) => {
  return Boolean(
    toolCall &&
    (toolCall.id || toolCall.name || toolCall.function?.name) &&
    (toolCall.args !== undefined ||
      toolCall.function?.arguments !== undefined ||
      toolCall.tool_call_result !== undefined)
  )
}

export const parseToolCallArgs = (toolCall) => {
  const args = toolCall?.args ?? toolCall?.function?.arguments
  if (!args) return {}
  if (typeof args === 'object') return args
  try {
    return JSON.parse(args)
  } catch {
    return {}
  }
}

export const SUBAGENT_TOOL_IDS = [
  'task',
  'subagent_start',
  'subagent_status',
  'subagent_events',
  'subagent_cancel',
  'subagent_await',
  'subagent_orchestrate'
]

export const isSubagentToolCall = (toolCall) => SUBAGENT_TOOL_IDS.includes(getToolCallId(toolCall))

export const parseToolCallResult = (toolCall) => {
  const content = toolCall?.tool_call_result?.content ?? toolCall?.result
  if (!content) return null
  if (typeof content === 'object') return content
  try {
    return JSON.parse(content)
  } catch {
    return null
  }
}

export const enrichSubagentToolCall = (
  toolCall,
  { subagentRunById, subagentRunByThreadId, subagentOptionBySlug } = {}
) => {
  if (!isSubagentToolCall(toolCall)) return toolCall

  const args = parseToolCallArgs(toolCall)
  const result = parseToolCallResult(toolCall)
  const subagentRun =
    (toolCall.id ? subagentRunById?.get?.(String(toolCall.id)) : null) ||
    (result?.run_id ? subagentRunById?.get?.(String(result.run_id)) : null) ||
    (args.thread_id ? subagentRunByThreadId?.get?.(String(args.thread_id)) : null) ||
    (result?.thread_id ? subagentRunByThreadId?.get?.(String(result.thread_id)) : null)
  const subagentOption = args.subagent_slug
    ? subagentOptionBySlug?.get?.(String(args.subagent_slug))
    : null
  const displayLabel =
    result?.subagent_name ||
    subagentRun?.subagent_name ||
    subagentOption?.name ||
    result?.subagent_slug ||
    subagentRun?.subagent_slug ||
    undefined

  return {
    ...toolCall,
    ...(subagentRun ? { subagent_run: subagentRun } : {}),
    ...(displayLabel ? { display_label: displayLabel } : {})
  }
}

export const normalizeToolCalls = (toolCalls, { includeHidden = false, mapToolCall } = {}) => {
  if (!Array.isArray(toolCalls)) return []

  return toolCalls
    .filter((toolCall) => {
      if (!isValidToolCall(toolCall)) return false
      return includeHidden || !isHiddenToolCall(toolCall)
    })
    .map((toolCall) => (mapToolCall ? mapToolCall(toolCall) : toolCall))
}

export const enrichTaskToolCalls = (toolCalls, options = {}) =>
  normalizeToolCalls(toolCalls, {
    mapToolCall: (toolCall) => enrichSubagentToolCall(toolCall, options)
  })

export const getToolIcon = (toolId) => TOOL_ICON_MAP[toolId] || null
