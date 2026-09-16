import assert from 'node:assert/strict'
import test from 'node:test'

import { MessageProcessor } from '../../src/utils/messageProcessor.js'

test('交付物只归属于调用 present_artifacts 的对话', () => {
  const artifactConversation = {
    messages: [
      {
        type: 'ai',
        tool_calls: [
          {
            name: 'present_artifacts',
            tool_call_result: { content: '已将交付物展示给用户' },
            args: JSON.stringify({
              filepaths: [
                '/home/gem/user-data/outputs/bubble_sort.py',
                '/home/gem/user-data/outputs/bubble_sort.js'
              ]
            })
          },
          {
            function: { name: 'present_artifacts' },
            status: 'success',
            args: { filepaths: ['/home/gem/user-data/outputs/bubble_sort.py'] }
          }
        ]
      }
    ]
  }
  const laterConversation = {
    messages: [{ type: 'human', content: '运行 Python 的' }]
  }

  assert.deepEqual(MessageProcessor.extractArtifactsFromConversation(artifactConversation), [
    '/home/gem/user-data/outputs/bubble_sort.py',
    '/home/gem/user-data/outputs/bubble_sort.js'
  ])
  assert.deepEqual(MessageProcessor.extractArtifactsFromConversation(laterConversation), [])
})

test('上下文压缩事件显示为历史时间线中的系统分界消息', () => {
  const conversations = MessageProcessor.convertServerHistoryToMessages([
    { type: 'human', content: '第一轮问题' },
    { type: 'ai', content: '第一轮回答' },
    {
      type: 'system',
      message_type: 'context_compression',
      content: '上下文已压缩：前面的历史消息仍可查看。'
    },
    { type: 'human', content: '第二轮问题' },
    { type: 'ai', content: '第二轮回答' }
  ])

  assert.equal(conversations.length, 2)
  assert.equal(conversations[0].messages[2].type, 'system')
  assert.match(conversations[0].messages[2].content, /上下文已压缩/)
})

test('历史 AI 消息中的自动交付物可恢复', () => {
  const conversations = MessageProcessor.convertServerHistoryToMessages([
    { type: 'human', content: '生成文件' },
    { type: 'ai', content: '已生成', artifacts: ['/outputs/request-7/result.json'] }
  ])

  assert.deepEqual(
    MessageProcessor.extractArtifactsFromConversation(conversations[0]),
    ['/outputs/request-7/result.json']
  )
})

test('消息块包含空值时不会导致对话渲染崩溃', () => {
  assert.equal(MessageProcessor.mergeMessageChunk([]), null)
  assert.equal(MessageProcessor.mergeMessageChunk([undefined, null]), null)
  assert.deepEqual(
    MessageProcessor.mergeMessageChunk([undefined, { type: 'ai', content: '正常' }]),
    { type: 'ai', content: '正常' }
  )
})
