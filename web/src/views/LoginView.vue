<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { useAgentStore } from '@/stores/agent'
import { User, Lock, Loader2 } from '@lucide/vue'

const router = useRouter()
const userStore = useUserStore()
const agentStore = useAgentStore()

const loginId = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function handleLogin() {
  if (!loginId.value || !password.value) {
    error.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  error.value = ''
  try {
    await userStore.login({ loginId: loginId.value, password: password.value })
    await agentStore.initialize()
    const redirect = sessionStorage.getItem('redirect') || '/agent'
    sessionStorage.removeItem('redirect')
    router.push(redirect)
  } catch (e) {
    error.value = e?.response?.data?.detail?.[0]?.msg || e?.message || '登录失败，请重试'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-bg" aria-hidden="true">
      <svg class="constellation" viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice">
        <g class="drift drift-a">
          <g class="edges">
            <line x1="120" y1="180" x2="240" y2="120" />
            <line x1="120" y1="180" x2="90" y2="420" />
            <line x1="240" y1="120" x2="320" y2="300" />
            <line x1="90" y1="420" x2="210" y2="540" />
            <line x1="90" y1="420" x2="60" y2="600" />
            <line x1="210" y1="540" x2="150" y2="720" />
            <line x1="60" y1="600" x2="150" y2="720" />
            <line x1="320" y1="300" x2="90" y2="420" />
          </g>
          <circle class="leaf" cx="240" cy="120" r="3" />
          <circle class="leaf" cx="320" cy="300" r="2.5" />
          <circle class="leaf" cx="90" cy="420" r="4" />
          <circle class="leaf" cx="210" cy="540" r="3" />
          <circle class="hub" cx="120" cy="180" r="6" />
          <circle class="hub" cx="150" cy="720" r="5" />
        </g>
        <g class="drift drift-b">
          <g class="edges">
            <line x1="1320" y1="200" x2="1200" y2="140" />
            <line x1="1320" y1="200" x2="1350" y2="440" />
            <line x1="1200" y1="140" x2="1120" y2="320" />
            <line x1="1350" y1="440" x2="1230" y2="560" />
            <line x1="1350" y1="440" x2="1380" y2="620" />
            <line x1="1230" y1="560" x2="1300" y2="740" />
            <line x1="1380" y1="620" x2="1300" y2="740" />
            <line x1="1120" y1="320" x2="1350" y2="440" />
          </g>
          <circle class="leaf" cx="1200" cy="140" r="3" />
          <circle class="leaf" cx="1350" cy="440" r="4" />
          <circle class="hub" cx="1320" cy="200" r="6" />
          <circle class="hub" cx="1300" cy="740" r="5" />
        </g>
      </svg>
      <span class="glow"></span>
      <span class="glow-accent"></span>
    </div>

    <div class="login-container">
      <div class="login-card">
        <div class="login-header">
          <img src="/favicon.svg" alt="logo" class="login-logo" />
          <h1 class="login-title">DataDeck</h1>
          <p class="login-subtitle">知识库问答与 Text2SQL 平台</p>
        </div>

        <form class="login-form" @submit.prevent="handleLogin">
          <div class="form-item">
            <div class="input-wrap">
              <User :size="16" class="input-icon" />
              <input
                v-model="loginId"
                type="text"
                placeholder="用户名"
                autocomplete="username"
                :disabled="loading"
              />
            </div>
          </div>
          <div class="form-item">
            <div class="input-wrap">
              <Lock :size="16" class="input-icon" />
              <input
                v-model="password"
                type="password"
                placeholder="密码"
                autocomplete="current-password"
                :disabled="loading"
              />
            </div>
          </div>

          <div v-if="error" class="form-error">{{ error }}</div>

          <button type="submit" class="login-btn" :disabled="loading">
            <Loader2 v-if="loading" :size="16" class="spin" />
            <span>{{ loading ? '登录中...' : '登 录' }}</span>
          </button>
        </form>
      </div>
    </div>
  </div>
</template>

<style lang="less" scoped>
.login-page {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  overflow: hidden;
  background: linear-gradient(135deg, #f0f4f8 0%, #e8f0f7 50%, #f5f7fa 100%);
}

.login-bg {
  position: absolute;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
}

.constellation {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  opacity: 0.4;
}

.edges line {
  stroke: var(--main-300);
  stroke-width: 0.5;
  opacity: 0.5;
}
.leaf { fill: var(--main-400); opacity: 0.6; }
.hub { fill: var(--main-500); opacity: 0.8; }
.pulse-ring { fill: none; stroke: var(--main-400); stroke-width: 1; }

.glow, .glow-accent {
  position: absolute;
  width: 500px;
  height: 500px;
  border-radius: 50%;
  filter: blur(100px);
  opacity: 0.15;
}
.glow { background: var(--main-400); top: -100px; left: -100px; }
.glow-accent { background: #6366f1; bottom: -100px; right: -100px; }

@keyframes driftA { from { transform: translate(0,0); } to { transform: translate(16px,-12px); } }
@keyframes driftB { from { transform: translate(0,0); } to { transform: translate(-18px,10px); } }
@keyframes spin { to { transform: rotate(360deg); } }
.drift-a { animation: driftA 8s ease-in-out infinite alternate; }
.drift-b { animation: driftB 10s ease-in-out infinite alternate; }
.spin { animation: spin 1s linear infinite; }

.login-container {
  position: relative;
  z-index: 1;
  width: 100%;
  max-width: 400px;
  padding: 1rem;
}

.login-card {
  background: var(--gray-0);
  border: 1px solid var(--gray-100);
  border-radius: 16px;
  padding: 2.5rem 2rem;
  box-shadow: 0 8px 32px rgba(0,0,0,0.08);
}

.login-header {
  text-align: center;
  margin-bottom: 2rem;
}
.login-logo { width: 40px; height: 40px; }
.login-title {
  font-size: 1.75rem;
  font-weight: 700;
  color: var(--gray-900);
  margin: 0.5rem 0 0.25rem;
  letter-spacing: -0.02em;
}
.login-subtitle {
  font-size: 0.875rem;
  color: var(--gray-500);
  margin: 0;
}

.login-form { display: flex; flex-direction: column; gap: 1rem; }

.form-item {}

.input-wrap {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0 0.75rem;
  border: 1px solid var(--gray-200);
  border-radius: 8px;
  background: var(--gray-0);
  transition: border-color 0.15s, box-shadow 0.15s;
  &:focus-within {
    border-color: var(--main-400);
    box-shadow: 0 0 0 3px var(--main-50);
  }
}
.input-icon { color: var(--gray-400); flex-shrink: 0; }
.input-wrap input {
  flex: 1;
  border: none;
  background: transparent;
  padding: 0.625rem 0;
  font-size: 0.9375rem;
  color: var(--gray-900);
  outline: none;
  &::placeholder { color: var(--gray-400); }
  &:disabled { opacity: 0.6; }
}

.form-error {
  font-size: 0.8125rem;
  color: var(--color-error-600);
  background: var(--color-error-50);
  border: 1px solid var(--color-error-100);
  border-radius: 6px;
  padding: 0.5rem 0.75rem;
}

.login-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  padding: 0.75rem;
  border: none;
  border-radius: 8px;
  background: linear-gradient(135deg, var(--main-600), var(--main-500));
  color: white;
  font-size: 1rem;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.15s, transform 0.15s;
  &:hover:not(:disabled) { opacity: 0.9; transform: translateY(-1px); }
  &:disabled { opacity: 0.6; cursor: not-allowed; }
}

:global(.dark) {
  .login-page { background: linear-gradient(135deg, #0f172a 0%, #1e293b 50%, #0f172a 100%); }
  .login-card { background: var(--gray-50); border-color: var(--gray-100); }
}
</style>
