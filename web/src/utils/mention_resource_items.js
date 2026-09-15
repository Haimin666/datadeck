import { formatMentionToken } from './mention_token.js'

const toResourceItem = (type, { value, label, extra = {} } = {}) => {
  if (!value && !label) return null
  const resolvedValue = value || label
  return {
    value: resolvedValue,
    label: label || resolvedValue,
    type,
    tokenLabel: formatMentionToken(type, label || resolvedValue),
    ...extra
  }
}

export const buildMentionResourceItems = (mention = {}) => {
  const { knowledgeBases = [], skills = [], subagents = [] } = mention

  return {
    knowledgeBases: knowledgeBases
      .map((kb) =>
        toResourceItem('knowledge', {
          // token 必须携带稳定的知识库 ID；名称可能重复且不能用于运行时授权。
          value: kb.kb_id || kb.id || kb.slug,
          label: kb.name,
          extra: { description: kb.description || '', resourceId: kb.kb_id }
        })
      )
      .filter(Boolean),
    skills: skills
      .map((s) =>
        toResourceItem('skill', {
          value: s.slug || s.value || s.id || s.name,
          label: s.name || s.label,
          extra: { description: s.description || '' }
        })
      )
      .filter(Boolean),
    subagents: subagents
      .map((s) =>
        toResourceItem('subagent', {
          value: s.slug || s.id || s.value || s.name,
          label: s.name || s.label,
          extra: { description: s.description || '' }
        })
      )
      .filter(Boolean)
  }
}
