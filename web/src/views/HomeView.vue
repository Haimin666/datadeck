<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { MessageSquare, ArrowRight } from '@lucide/vue'

const router = useRouter()
const userStore = useUserStore()
const greetings = [
  '你好，有什么可以帮你的？',
  '欢迎使用 DataDeck',
  '准备好开始了吗？',
  '今天想解决什么问题？'
]
const currentGreeting = ref(0)

onMounted(() => {
  if (userStore.isLoggedIn) {
    router.push('/agent')
  }
  setInterval(() => {
    currentGreeting.value = (currentGreeting.value + 1) % greetings.length
  }, 4000)
})

function handleStart() {
  if (userStore.isLoggedIn) {
    router.push('/agent')
  } else {
    router.push('/login')
  }
}
</script>

<template>
  <div class="home-page">
    <div class="ambient" aria-hidden="true">
      <span class="glow"></span>
      <span class="glow-accent"></span>
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
          <circle class="leaf" cx="60" cy="600" r="2.5" />
          <circle class="pulse-ring" cx="120" cy="180" r="6" />
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
          <circle class="leaf" cx="1120" cy="320" r="2.5" />
          <circle class="leaf" cx="1350" cy="440" r="4" />
          <circle class="leaf" cx="1230" cy="560" r="3" />
          <circle class="leaf" cx="1380" cy="620" r="2.5" />
          <circle class="pulse-ring" cx="1320" cy="200" r="6" />
          <circle class="hub" cx="1320" cy="200" r="6" />
          <circle class="hub" cx="1300" cy="740" r="5" />
        </g>
      </svg>
    </div>

    <div class="home-content">
      <div class="hero">
        <div class="hero-icon">
          <MessageSquare :size="48" />
        </div>
        <h1 class="hero-title">DataDeck</h1>
        <div class="subtitle-wrap">
          <p class="subtitle" :key="currentGreeting">{{ greetings[currentGreeting] }}</p>
        </div>
        <div class="hero-actions">
          <button class="button-base primary" @click="handleStart">
            <MessageSquare :size="18" />
            <span>开始对话</span>
            <ArrowRight :size="16" />
          </button>
          <a v-if="!userStore.isLoggedIn" href="/login" class="button-base secondary">
            登录
          </a>
        </div>
      </div>

      <footer class="footer">
        <div class="footer-content">
          <p class="copyright">Powered by DataDeck · 知识库问答 & Text2SQL</p>
        </div>
      </footer>
    </div>
  </div>
</template>

<style lang="less" scoped>
.home-page {
  height: 100vh;
  display: flex;
  flex-direction: column;
  position: relative;
  overflow: hidden;
  background: linear-gradient(135deg, #f0f4f8 0%, #e8f0f7 50%, #f5f7fa 100%);
}

.ambient {
  position: absolute;
  inset: 0;
  pointer-events: none;
  overflow: hidden;
}
.constellation { position: absolute; inset: 0; width: 100%; height: 100%; opacity: 0.35; }
.edges line { stroke: var(--main-300); stroke-width: 0.5; opacity: 0.4; }
.leaf { fill: var(--main-400); opacity: 0.5; }
.hub { fill: var(--main-500); opacity: 0.7; }
.pulse-ring { fill: none; stroke: var(--main-400); stroke-width: 1; animation: pulse-ring 2s ease-out infinite; }
.glow { position: absolute; width: 500px; height: 500px; border-radius: 50%; background: var(--main-400); filter: blur(100px); opacity: 0.12; top: -100px; left: -100px; animation: glow-drift 12s ease-in-out infinite alternate; }
.glow-accent { position: absolute; width: 400px; height: 400px; border-radius: 50%; background: #6366f1; filter: blur(80px); opacity: 0.1; bottom: -50px; right: -50px; animation: glow-drift-accent 15s ease-in-out infinite alternate; }

@keyframes pulse-ring { 0% { opacity: 0.7; transform: scale(1); } 70% { opacity: 0; transform: scale(2.4); } }
@keyframes glow-drift { from { transform: translate(0,0); } to { transform: translate(60px, 40px); } }
@keyframes glow-drift-accent { from { transform: translate(0,0); } to { transform: translate(-60px, -40px); } }
@keyframes drift-a { from { transform: translate(0,0); } to { transform: translate(16px, -12px); } }
@keyframes drift-b { from { transform: translate(0,0); } to { transform: translate(-18px, 10px); } }
.drift-a { animation: drift-a 10s ease-in-out infinite alternate; }
.drift-b { animation: drift-b 12s ease-in-out infinite alternate; }

.home-content {
  position: relative;
  z-index: 1;
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 2rem;
}

.hero { text-align: center; max-width: 520px; }
.hero-icon {
  width: 72px; height: 72px;
  margin: 0 auto 1.25rem;
  background: linear-gradient(135deg, var(--main-600), var(--main-500));
  border-radius: 20px;
  display: flex; align-items: center; justify-content: center;
  color: white;
  box-shadow: 0 8px 24px rgba(3,80,101,0.25);
}
.hero-title {
  font-size: 3rem;
  font-weight: 800;
  letter-spacing: -0.03em;
  background: linear-gradient(120deg, var(--main-900) 10%, var(--main-600) 60%, var(--main-500));
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  margin: 0 0 0.75rem;
}
.subtitle-wrap { min-height: 2.2em; }
.subtitle {
  font-size: 1.25rem;
  font-weight: 500;
  color: var(--gray-600);
  margin: 0;
  transition: opacity 0.4s ease, transform 0.4s ease;
}
.hero-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 1rem;
  margin-top: 2rem;
}
.button-base {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  padding: 0.625rem 2rem;
  border-radius: 999px;
  font-size: 1rem;
  font-weight: 600;
  cursor: pointer;
  border: 1px solid transparent;
  text-decoration: none;
  transition: background 0.2s, box-shadow 0.2s, transform 0.15s;
  min-height: 48px;
}
.button-base.primary {
  background: linear-gradient(135deg, var(--main-600), var(--main-500));
  color: white;
  box-shadow: 0 8px 24px -8px rgba(3,80,101,0.4);
  &:hover { transform: translateY(-1px); box-shadow: 0 12px 28px -8px rgba(3,80,101,0.5); }
}
.button-base.secondary {
  background: var(--color-trans-light);
  backdrop-filter: blur(8px);
  color: var(--main-700);
  border-color: var(--main-200);
  &:hover { background: var(--main-50); }
}
.footer { padding: 1.5rem 2rem; text-align: center; }
.copyright { color: var(--main-700); font-size: 0.875rem; opacity: 0.7; margin: 0; }

:global(.dark) {
  .home-page { background: linear-gradient(135deg, #0f172a 0%, #1e293b 50%, #0f172a 100%); }
  .button-base.secondary { background: var(--dark-10); &:hover { background: var(--dark-25); } }
}
</style>
