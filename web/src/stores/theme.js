import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const useThemeStore = defineStore('theme', () => {
  const isDark = ref(localStorage.getItem('theme') === 'dark')

  const currentTheme = computed(() => ({
    token: {
      colorPrimary: '#035065',
      borderRadius: 6,
      fontFamily: "-apple-system, BlinkMacSystemFont, 'Noto Sans SC', sans-serif"
    },
    algorithm: isDark.value ? null : undefined
  }))

  function toggleTheme() {
    isDark.value = !isDark.value
    localStorage.setItem('theme', isDark.value ? 'dark' : 'light')
    document.documentElement.classList.toggle('dark', isDark.value)
  }

  return { isDark, currentTheme, toggleTheme }
})
