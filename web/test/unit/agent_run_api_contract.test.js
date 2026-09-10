import assert from 'node:assert/strict'
import test from 'node:test'

import { createPinia, setActivePinia } from 'pinia'
import { createServer } from 'vite'

test('审批恢复请求分别传递 run id 与审批决定', async () => {
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  const originalFetch = globalThis.fetch
  let capturedBody

  try {
    globalThis.localStorage = {
      getItem: () => 'token',
      setItem() {},
      removeItem() {}
    }
    globalThis.fetch = async (_url, options) => {
      capturedBody = JSON.parse(options.body)
      return new Response(JSON.stringify({ run_id: 'run-1' }), {
        headers: { 'content-type': 'application/json' }
      })
    }
    setActivePinia(createPinia())
    const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
    const userStore = useUserStore()
    userStore.token = 'token'
    userStore.userId = 1
    const { agentApi } = await server.ssrLoadModule('/src/apis/agent_api.js')

    await agentApi.createAgentRun({
      query: null,
      agent_slug: 'default-chatbot',
      thread_id: 'thread-1',
      resume: 'run-1',
      tool_approval: { decisions: [{ type: 'reject', message: 'no' }] }
    })

    assert.equal(capturedBody.resume, 'run-1')
    assert.deepEqual(capturedBody.tool_approval, {
      decisions: [{ type: 'reject', message: 'no' }]
    })
  } finally {
    globalThis.fetch = originalFetch
    await server.close()
  }
})
