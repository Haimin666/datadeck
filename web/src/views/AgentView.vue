<script setup>
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import AgentChatComponent from '@/components/AgentChatComponent.vue'

const route = useRoute()
const chatComponentRef = ref(null)

const selectedAgentId = ref('default-chatbot')

function handleThreadChange(threadId) {
  if (threadId) {
    route.params.thread_id && route.replace({ params: { thread_id: threadId } })
  }
}
</script>

<template>
  <div class="agent-view">
    <AgentChatComponent
      ref="chatComponentRef"
      :selected-agent-id="selectedAgentId"
      @thread-change="handleThreadChange"
    />
  </div>
</template>

<style lang="less" scoped>
.agent-view {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100vh;
  overflow: hidden;
}
</style>
