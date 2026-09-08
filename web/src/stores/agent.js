import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { agentApi } from '@/apis'

export const BUILTIN_AGENT_ID = 'default-chatbot'

export function isBuiltinAgent(agent) {
  return agent?.is_builtin || agent?.id === BUILTIN_AGENT_ID || agent?.slug === BUILTIN_AGENT_ID
}

function normalizeAgent(agent) {
  const agentId = agent?.agent_id || agent?.slug || agent?.id
  return agentId
    ? { ...agent, id: agentId, agent_id: agentId, slug: agent?.slug || agentId }
    : agent
}

function sortAgents(agents) {
  return [...agents].sort((a, b) => {
    if (isBuiltinAgent(a) !== isBuiltinAgent(b)) return isBuiltinAgent(a) ? -1 : 1
    return String(a.name || a.id).localeCompare(String(b.name || b.id), 'zh-CN')
  })
}

function getPreferredAgentId(agents, persistedId) {
  if (persistedId && agents.some((a) => a.id === persistedId)) return persistedId
  return agents.find(isBuiltinAgent)?.id || agents[0]?.id || null
}

export const useAgentStore = defineStore('agent', () => {
  const agents = ref([])
  const selectedAgentId = ref(localStorage.getItem('selected_agent_id') || null)
  const isLoadingAgents = ref(false)
  const isLoadingConfig = ref(false)
  const isLoadingAgentDetail = ref(false)
  const error = ref(null)
  const isInitialized = ref(false)
  const isInitializing = ref(false)

  const selectedAgent = computed(() => {
    const agentId = selectedAgentId.value
    return agentId ? agents.value.find((a) => a.id === agentId) || null : null
  })

  async function fetchAgents() {
    isLoadingAgents.value = true
    error.value = null
    try {
      const data = await agentApi.getAgents()
      agents.value = sortAgents((data?.agents || data || []).map(normalizeAgent))
      const persisted = localStorage.getItem('selected_agent_id')
      if (persisted && agents.value.some((a) => a.id === persisted)) {
        selectedAgentId.value = persisted
      } else {
        selectedAgentId.value = getPreferredAgentId(agents.value, persisted)
      }
      return agents.value
    } catch (err) {
      error.value = err
      throw err
    } finally {
      isLoadingAgents.value = false
    }
  }

  async function fetchAgentDetail(agentId, force = false) {
    if (!force && agentDetails.value[agentId]) return agentDetails.value[agentId]
    isLoadingAgentDetail.value = true
    try {
      const data = await agentApi.getAgentDetail(agentId)
      const normalized = normalizeAgent(data)
      agentDetails.value[normalized.id] = normalized
      return normalized
    } finally {
      isLoadingAgentDetail.value = false
    }
  }

  async function initialize() {
    if (isInitialized.value) return
    if (isInitializing.value) {
      while (isInitializing.value) await new Promise((r) => setTimeout(r, 100))
      return
    }
    isInitializing.value = true
    try {
      await fetchAgents()
      isInitialized.value = true
    } finally {
      isInitializing.value = false
    }
  }

  function setSelectedAgent(agentId) {
    selectedAgentId.value = agentId
    localStorage.setItem('selected_agent_id', agentId)
  }

  function reset() {
    agents.value = []
    selectedAgentId.value = null
    agentDetails.value = {}
    isInitialized.value = false
    error.value = null
  }

  const agentDetails = ref({})

  return {
    agents, selectedAgentId, isLoadingAgents, isLoadingConfig, isLoadingAgentDetail,
    error, isInitialized, selectedAgent, agentDetails,
    fetchAgents, fetchAgentDetail, initialize, setSelectedAgent, reset
  }
})
