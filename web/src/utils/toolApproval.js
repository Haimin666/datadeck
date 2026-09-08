/**
 * 工具审批状态工具
 */
export const hasPendingInterruptPayload = (pendingInterrupt) => {
  if (!pendingInterrupt) return false
  return Boolean(pendingInterrupt.tool_calls || pendingInterrupt.tool_names)
}
