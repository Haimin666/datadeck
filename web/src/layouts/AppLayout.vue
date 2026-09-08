<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { useAgentStore } from '@/stores/agent'
import { useThreadStore } from '@/stores/thread'
import { useThemeStore } from '@/stores/theme'
import { Sun, Moon, LogOut, MessageSquare, Settings } from '@lucide/vue'

const router = useRouter()
const userStore = useUserStore()
const agentStore = useAgentStore()
const threadStore = useThreadStore()
const themeStore = useThemeStore()
const sidebarCollapsed = ref(false)

onMounted(async () => {
  if (userStore.isLoggedIn) {
    await agentStore.initialize()
    await threadStore.fetchThreads()
  }
})

function handleLogout() {
  userStore.logout()
  threadStore.reset()
  router.push('/login')
}

function goToDashboard() {
  router.push('/dashboard')
}
</script>

<template>
  <div class="app-layout" :class="{ collapsed: sidebarCollapsed }">
    <!-- 侧边栏 -->
    <aside class="sidebar">
      <div class="sidebar-header">
        <div class="logo">
          <img src="/favicon.svg" alt="logo" class="logo-img" />
          <span class="logo-text" v-show="!sidebarCollapsed">DataDeck</span>
        </div>
        <button class="collapse-btn" @click="sidebarCollapsed = !sidebarCollapsed">
          <component :is="sidebarCollapsed ? 'ChevronRight' : 'ChevronLeft'" :size="16" />
        </button>
      </div>

      <nav class="sidebar-nav">
        <router-link to="/agent" class="nav-item" :class="{ active: $route.name === 'AgentComp' || $route.name === 'AgentCompWithThreadId' }">
          <MessageSquare :size="18" />
          <span v-show="!sidebarCollapsed">对话</span>
        </router-link>
        <router-link to="/dashboard" class="nav-item" v-if="userStore.isSuperAdmin">
          <Settings :size="18" />
          <span v-show="!sidebarCollapsed">仪表盘</span>
        </router-link>
      </nav>

      <div class="sidebar-footer">
        <button class="theme-toggle" @click="themeStore.toggleTheme()">
          <Sun v-if="themeStore.isDark" :size="16" />
          <Moon v-else :size="16" />
        </button>
        <button class="logout-btn" @click="handleLogout">
          <LogOut :size="16" />
          <span v-show="!sidebarCollapsed">退出</span>
        </button>
      </div>
    </aside>

    <!-- 主内容区 -->
    <main class="main-content">
      <router-view v-slot="{ Component }">
        <keep-alive>
          <component :is="Component" />
        </keep-alive>
      </router-view>
    </main>
  </div>
</template>

<style lang="less" scoped>
.app-layout {
  display: flex;
  height: 100vh;
  overflow: hidden;
  background: var(--gray-0);
}

.sidebar {
  width: 220px;
  min-width: 220px;
  height: 100vh;
  background: var(--gray-50);
  border-right: 1px solid var(--gray-100);
  display: flex;
  flex-direction: column;
  transition: width 0.2s ease;
  z-index: 100;

  &.collapsed {
    width: 56px;
    min-width: 56px;

    .logo-text,
    .sidebar-nav .nav-item span,
    .sidebar-footer span { display: none; }
  }
}

.sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 12px 12px 16px;
  height: var(--header-height);
  border-bottom: 1px solid var(--gray-100);
}

.logo {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 700;
  font-size: 16px;
  color: var(--gray-900);
}

.logo-img {
  width: 28px;
  height: 28px;
  flex-shrink: 0;
}

.logo-text {
  white-space: nowrap;
  overflow: hidden;
}

.collapse-btn {
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  cursor: pointer;
  border-radius: 6px;
  color: var(--gray-500);
  display: flex;
  align-items: center;
  justify-content: center;
  &:hover { background: var(--gray-100); }
}

.sidebar-nav {
  flex: 1;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-radius: 8px;
  color: var(--gray-600);
  text-decoration: none;
  font-size: 14px;
  font-weight: 500;
  transition: background 0.15s, color 0.15s;

  &:hover { background: var(--gray-100); color: var(--gray-800); }
  &.active { background: var(--main-50); color: var(--main-600); }
}

.sidebar-footer {
  padding: 12px;
  border-top: 1px solid var(--gray-100);
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.theme-toggle,
.logout-btn {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 12px;
  border: none;
  background: transparent;
  cursor: pointer;
  border-radius: 8px;
  color: var(--gray-600);
  font-size: 14px;
  &:hover { background: var(--gray-100); }
}

.logout-btn { color: var(--color-error-600); }
.logout-btn:hover { background: var(--color-error-50); }

.main-content {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
</style>
