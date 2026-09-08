/**
 * datadeck 只有 web 入口，无 queue/worker 架构
 */
export const isSteerableMainChatRun = (run) =>
  run?.status === 'running'

export const isTerminalStatus = (status) => {
  const terminal = new Set(['completed', 'failed', 'cancelled'])
  return terminal.has(status)
}

export const isInterruptedStatus = (status) => status === 'interrupted'
