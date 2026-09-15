<script setup>
import { computed, nextTick, ref } from 'vue'
import { Bot, Link2, Plus, Trash2 } from '@lucide/vue'

const props = defineProps({
  subagents: { type: Array, default: () => [] },
  modelValue: { type: Object, default: () => ({ nodes: [], edges: [] }) },
  disabled: { type: Boolean, default: false }
})
const emit = defineEmits(['update:modelValue', 'update:subagents'])

const boardRef = ref(null)
const selectedNodeId = ref(null)
const connectingFrom = ref(null)
const dragging = ref(null)

const workflow = computed(() => ({
  version: 1,
  nodes: Array.isArray(props.modelValue?.nodes) ? props.modelValue.nodes : [],
  edges: Array.isArray(props.modelValue?.edges) ? props.modelValue.edges : []
}))
const normalizedSubagents = computed(() => props.subagents.map((agent) => ({
  ...agent,
  value: agent.value || agent.slug || agent.id,
  name: agent.name || agent.label || agent.slug || agent.id
})).filter((agent) => agent.value))
const selectedNode = computed(() => workflow.value.nodes.find((node) => node.id === selectedNodeId.value) || null)
const availableChildren = computed(() =>
  normalizedSubagents.value.filter((agent) => !workflow.value.nodes.some((node) => node.subagent_slug === agent.value))
)
const nodeById = (id) => workflow.value.nodes.find((node) => node.id === id)
const nodeId = () => `node-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
const publish = (nodes, edges = workflow.value.edges) => {
  emit('update:modelValue', { version: 1, nodes, edges })
  emit('update:subagents', [...new Set(nodes.map((node) => node.subagent_slug))])
}
const addNode = (agent) => {
  if (props.disabled || !agent || nodeById(agent.value)) return
  const index = workflow.value.nodes.length
  const node = {
    id: nodeId(),
    subagent_slug: agent.value,
    name: agent.name || agent.value,
    task_template: '围绕用户当前请求完成专业任务，输出清晰结论和依据。',
    x: 48 + (index % 3) * 214,
    y: 56 + Math.floor(index / 3) * 148
  }
  publish([...workflow.value.nodes, node])
  selectedNodeId.value = node.id
}
const onPaletteDrop = (event) => {
  const slug = event.dataTransfer?.getData('application/x-agent-slug')
  const agent = normalizedSubagents.value.find((item) => item.value === slug)
  addNode(agent)
}
const beginDrag = (event, node) => {
  if (props.disabled || event.button !== 0 || connectingFrom.value) return
  const board = boardRef.value?.getBoundingClientRect()
  if (!board) return
  selectedNodeId.value = node.id
  dragging.value = { id: node.id, offsetX: event.clientX - board.left - node.x, offsetY: event.clientY - board.top - node.y }
  window.addEventListener('pointermove', moveNode)
  window.addEventListener('pointerup', stopDrag, { once: true })
}
const moveNode = (event) => {
  if (!dragging.value || !boardRef.value) return
  const board = boardRef.value.getBoundingClientRect()
  const { id, offsetX, offsetY } = dragging.value
  const nodes = workflow.value.nodes.map((node) => node.id === id ? {
    ...node,
    x: Math.max(8, Math.min(board.width - 184, event.clientX - board.left - offsetX)),
    y: Math.max(8, Math.min(board.height - 96, event.clientY - board.top - offsetY))
  } : node)
  publish(nodes)
}
const stopDrag = () => {
  dragging.value = null
  window.removeEventListener('pointermove', moveNode)
}
const beginConnect = (event, node) => {
  event.stopPropagation()
  if (props.disabled) return
  connectingFrom.value = node.id
}
const finishConnect = (node) => {
  if (!connectingFrom.value) return
  if (connectingFrom.value === node.id) return
  const duplicate = workflow.value.edges.some((edge) => edge.source === connectingFrom.value && edge.target === node.id)
  if (!duplicate) publish(workflow.value.nodes, [...workflow.value.edges, { source: connectingFrom.value, target: node.id }])
  connectingFrom.value = null
}
const removeNode = () => {
  if (props.disabled || !selectedNode.value) return
  const id = selectedNode.value.id
  publish(workflow.value.nodes.filter((node) => node.id !== id), workflow.value.edges.filter((edge) => edge.source !== id && edge.target !== id))
  selectedNodeId.value = null
}
const updateTask = (task_template) => {
  if (!selectedNode.value) return
  publish(workflow.value.nodes.map((node) => node.id === selectedNode.value.id ? { ...node, task_template } : node))
}
const edgePath = (edge) => {
  const source = nodeById(edge.source)
  const target = nodeById(edge.target)
  if (!source || !target) return ''
  const x1 = source.x + 184, y1 = source.y + 48, x2 = target.x, y2 = target.y + 48
  const bend = Math.max(42, Math.abs(x2 - x1) * 0.45)
  return `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`
}
const removeEdge = (edge) => {
  if (props.disabled) return
  publish(workflow.value.nodes, workflow.value.edges.filter((item) => item !== edge))
}
const addFirst = async () => {
  await nextTick()
  addNode(availableChildren.value[0])
}
</script>

<template>
  <section class="workflow-editor">
    <header class="workflow-header">
      <div>
        <h3>多智能体编排</h3>
        <p>拖入子智能体；从节点右侧圆点拖到目标节点建立连线。连线表示“等待上游结果后再执行”。</p>
      </div>
      <a-tag color="blue">{{ workflow.nodes.length }} 个节点 · {{ workflow.edges.length }} 条依赖</a-tag>
    </header>

    <div class="workflow-layout">
      <aside class="workflow-palette">
        <div class="palette-title">可用子智能体</div>
        <a-empty v-if="!availableChildren.length" :image="false" description="没有可添加的子智能体" />
        <button
          v-for="agent in availableChildren"
          :key="agent.value"
          class="palette-agent"
          type="button"
          draggable="true"
          :disabled="disabled"
          @click="addNode(agent)"
          @dragstart="(event) => event.dataTransfer?.setData('application/x-agent-slug', agent.value)"
        >
          <Bot :size="15" />
          <span>{{ agent.name }}</span>
          <Plus :size="14" />
        </button>
        <a-button v-if="!workflow.nodes.length && availableChildren.length" type="link" size="small" @click="addFirst">
          添加第一个节点
        </a-button>
      </aside>

      <div
        ref="boardRef"
        class="workflow-board"
        :class="{ connecting: connectingFrom }"
        @dragover.prevent
        @drop.prevent="onPaletteDrop"
        @click.self="connectingFrom = null"
      >
        <svg class="workflow-edges" aria-hidden="true">
          <defs><marker id="workflow-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" /></marker></defs>
          <path v-for="edge in workflow.edges" :key="`${edge.source}-${edge.target}`" :d="edgePath(edge)" marker-end="url(#workflow-arrow)" @dblclick.stop="removeEdge(edge)" />
        </svg>
        <div v-if="!workflow.nodes.length" class="workflow-empty">从左侧拖入子智能体，开始编排</div>
        <article
          v-for="node in workflow.nodes"
          :key="node.id"
          class="workflow-node"
          :class="{ selected: selectedNodeId === node.id, connecting: connectingFrom === node.id }"
          :style="{ transform: `translate(${node.x}px, ${node.y}px)` }"
          @pointerdown="beginDrag($event, node)"
          @pointerup.stop="connectingFrom && finishConnect(node)"
          @click.stop="connectingFrom ? finishConnect(node) : (selectedNodeId = node.id)"
        >
          <Bot :size="16" />
          <span>{{ node.name }}</span>
          <button class="node-port" type="button" title="连接到下一节点" @click="beginConnect($event, node)"><Link2 :size="12" /></button>
        </article>
      </div>

      <aside class="workflow-inspector">
        <template v-if="selectedNode">
          <div class="palette-title">{{ selectedNode.name }}</div>
          <label>节点任务</label>
          <a-textarea :value="selectedNode.task_template" :rows="8" :disabled="disabled" placeholder="该子智能体接到用户请求后应完成什么？" @update:value="updateTask" />
          <p>运行时会自动把用户当前请求附加到这里的任务说明。</p>
          <a-button danger size="small" :disabled="disabled" @click="removeNode"><Trash2 :size="13" /> 移除节点</a-button>
        </template>
        <div v-else class="inspector-empty">点击画布中的节点，配置它的任务。</div>
      </aside>
    </div>
  </section>
</template>

<style lang="less" scoped>
.workflow-editor { padding: 4px 2px; }
.workflow-header { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; margin-bottom:14px; h3 { margin:0; font-size:14px; color:var(--gray-900); } p { margin:5px 0 0; color:var(--gray-600); font-size:12px; line-height:1.5; } }
.workflow-layout { display:grid; grid-template-columns:150px minmax(360px,1fr) 190px; min-height:390px; border:1px solid var(--gray-200); border-radius:10px; overflow:hidden; background:var(--gray-0); }
.workflow-palette,.workflow-inspector { padding:12px; background:var(--gray-50); }
.workflow-palette { border-right:1px solid var(--gray-200); }
.workflow-inspector { border-left:1px solid var(--gray-200); label { display:block; margin:12px 0 6px; color:var(--gray-700); font-size:12px; font-weight:600; } p { color:var(--gray-600); font-size:11px; line-height:1.5; } }
.palette-title { color:var(--gray-800); font-size:12px; font-weight:650; }
.palette-agent { display:flex; align-items:center; width:100%; gap:7px; margin-top:8px; padding:8px; border:1px solid var(--gray-200); border-radius:7px; background:var(--gray-0); color:var(--gray-800); cursor:grab; text-align:left; font-size:12px; .lucide:last-child { margin-left:auto; color:var(--main-700); } &:hover { border-color:var(--main-500); color:var(--main-800); } &:disabled { cursor:not-allowed; opacity:.55; } }
.workflow-board { position:relative; min-height:390px; overflow:hidden; background-color:#fff; background-image:radial-gradient(var(--gray-300) .7px, transparent .7px); background-size:14px 14px; &.connecting { cursor:crosshair; } }
.workflow-edges { position:absolute; width:100%; height:100%; overflow:visible; pointer-events:none; path { fill:none; stroke:var(--main-600); stroke-width:2; pointer-events:stroke; cursor:pointer; } :deep(marker path) { fill:var(--main-600); } }
.workflow-empty { position:absolute; top:50%; left:50%; transform:translate(-50%,-50%); color:var(--gray-500); font-size:12px; white-space:nowrap; }
.workflow-node { position:absolute; display:flex; align-items:center; width:184px; min-height:48px; gap:8px; padding:10px 8px 10px 12px; border:1px solid var(--gray-300); border-radius:8px; background:#fff; box-shadow:0 2px 8px rgb(30 41 59 / 8%); color:var(--gray-800); font-size:12px; font-weight:600; cursor:grab; user-select:none; &.selected { border-color:var(--main-600); box-shadow:0 0 0 3px color-mix(in srgb, var(--main-500) 18%, transparent); } &.connecting { border-color:var(--main-600); background:var(--main-50); } }
.node-port { display:grid; place-items:center; width:22px; height:22px; margin-left:auto; border:0; border-radius:50%; background:var(--main-700); color:#fff; cursor:crosshair; }
.inspector-empty { padding-top:120px; color:var(--gray-500); font-size:12px; text-align:center; line-height:1.6; }
@media (max-width: 760px) { .workflow-layout { grid-template-columns:120px minmax(260px,1fr); } .workflow-inspector { grid-column:1 / -1; border-top:1px solid var(--gray-200); border-left:0; } }
</style>
