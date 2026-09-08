/**
 * 兼容两种 seq 格式：
 * - Yuxi 格式："major-minor"（如 "0-0"）
 * - datadeck 格式：纯数字字符串（如 "12345"）
 */
const RUN_SEQ_PATTERN = /^\d+-\d+$/

export const normalizeRunSeq = (value) => {
  if (value === undefined || value === null) return '0-0'
  const text = String(value).trim()
  if (!text) return '0-0'
  // 纯数字格式：转成 "N-0" 以兼容比较逻辑
  if (/^\d+$/.test(text)) return `${text}-0`
  return RUN_SEQ_PATTERN.test(text) ? text : '0-0'
}

const parseRunSeq = (value) => {
  const text = normalizeRunSeq(value)
  if (!text.includes('-')) {
    return { major: BigInt(text || '0'), minor: BigInt(0) }
  }
  const [majorRaw, minorRaw] = text.split('-', 2)
  try {
    return {
      major: BigInt(majorRaw || '0'),
      minor: BigInt(minorRaw || '0')
    }
  } catch {
    return { major: BigInt(0), minor: BigInt(0) }
  }
}

export const compareRunSeq = (incoming, current) => {
  const left = parseRunSeq(incoming)
  const right = parseRunSeq(current)
  if (left.major > right.major) return 1
  if (left.major < right.major) return -1
  if (left.minor > right.minor) return 1
  if (left.minor < right.minor) return -1
  return 0
}

export const hasOngoingRunChunks = (threadState) => {
  const msgChunks = threadState?.onGoingConv?.msgChunks
  if (!msgChunks || typeof msgChunks !== 'object') return false
  return Object.values(msgChunks).some((chunks) =>
    Array.isArray(chunks) ? chunks.length > 0 : Boolean(chunks)
  )
}

export const resolveRunResumeAfterSeq = ({ snapshot, threadState }) => {
  if (!snapshot?.run_id || !hasOngoingRunChunks(threadState)) return '0-0'
  if (threadState?.activeRunId === snapshot.run_id) {
    return normalizeRunSeq(threadState.runLastSeq || snapshot.last_seq)
  }
  return '0-0'
}
