import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const readSource = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')

test('知识库列表和详情统一使用 KnowledgeView', () => {
  const source = readSource('../../src/router/index.js')
  assert.match(source, /path: '\/knowledge',[\s\S]*?name: 'KnowledgeComp',[\s\S]*?import\('\.\.\/views\/KnowledgeView\.vue'\)/)
  assert.match(source, /path: ':kbId',[\s\S]*?name: 'KnowledgeBaseDetail',[\s\S]*?import\('\.\.\/views\/KnowledgeView\.vue'\)/)
  assert.doesNotMatch(source, /DataBaseInfoView|extensions\/knowledgebase|ExtensionEvaluationBenchmarkDetail/)
})

test('知识库前端只暴露统一基础文本 API', () => {
  const source = readSource('../../src/apis/knowledge_api.js')
  assert.match(source, /export const knowledgeBaseApi/)
  assert.match(source, /documents\/upload/)
  assert.match(source, /export const scheduledTaskApi/)
  assert.doesNotMatch(source, /export const (databaseApi|documentApi|fileApi|evaluationApi|mindmapApi|graphBuildApi)/)
})
