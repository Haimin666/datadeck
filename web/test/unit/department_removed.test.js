import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const readSource = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')

test('前端不再保留已移除的部门管理契约', () => {
  const api = readSource('../../src/apis/auth_api.js')
  const store = readSource('../../src/stores/user.js')
  const account = readSource('../../src/components/AccountSettingsComponent.vue')
  const users = readSource('../../src/components/UserManagementComponent.vue')
  const share = readSource('../../src/components/ShareConfigForm.vue')

  assert.doesNotMatch(api, /department/i)
  assert.doesNotMatch(store, /department/i)
  assert.doesNotMatch(account, /部门|department/i)
  assert.doesNotMatch(users, /部门|department/i)
  assert.doesNotMatch(share, /department/i)
})
