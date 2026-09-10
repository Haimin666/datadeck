import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { discoveryApi } from '@/apis/system_api'

const DISABLED_FEATURES = Object.freeze({
  knowledge: false,
  skills: false,
  mcp: false,
  workspace: false,
  scheduled_tasks: false
})

function readFeatures(payload) {
  return {
    knowledge: payload?.capabilities?.features?.knowledge === true,
    skills: payload?.capabilities?.features?.skills === true,
    mcp: payload?.capabilities?.features?.mcp === true,
    workspace: payload?.capabilities?.features?.workspace === true,
    scheduled_tasks: payload?.capabilities?.features?.scheduled_tasks === true
  }
}

export const useRuntimeCapabilitiesStore = defineStore('runtime-capabilities', () => {
  const features = ref({ ...DISABLED_FEATURES })
  const status = ref('idle')
  const error = ref(null)
  let loadingPromise = null

  const knowledgeEnabled = computed(() => features.value.knowledge)
  const skillsEnabled = computed(() => features.value.skills)
  const mcpEnabled = computed(() => features.value.mcp)
  const workspaceEnabled = computed(() => features.value.workspace)
  const scheduledTasksEnabled = computed(() => features.value.scheduled_tasks)

  async function ensureLoaded() {
    if (status.value === 'ready') {
      return features.value
    }
    if (loadingPromise) return loadingPromise

    status.value = 'loading'
    error.value = null
    loadingPromise = discoveryApi
      .getCapabilities()
      .then((payload) => {
        features.value = readFeatures(payload)
        status.value = 'ready'
        return features.value
      })
      .catch((cause) => {
        features.value = { ...DISABLED_FEATURES }
        error.value = cause
        status.value = 'error'
        console.warn('加载运行时能力失败，已关闭可选能力:', cause)
        return features.value
      })
      .finally(() => {
        loadingPromise = null
      })

    return loadingPromise
  }

  return {
    features,
    status,
    error,
    knowledgeEnabled,
    skillsEnabled,
    mcpEnabled,
    workspaceEnabled,
    scheduledTasksEnabled,
    ensureLoaded
  }
})
