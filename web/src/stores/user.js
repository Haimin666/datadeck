import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi } from '@/apis/auth_api'

export const useUserStore = defineStore('user', () => {
  const token = ref(localStorage.getItem('user_token') || '')
  const userId = ref(null)
  const username = ref('')
  const uid = ref('')
  const userRole = ref('')

  const isLoggedIn = computed(() => !!token.value)
  const isAdmin = computed(() => userRole.value === 'admin' || userRole.value === 'superadmin')
  const isSuperAdmin = computed(() => userRole.value === 'superadmin')

  function applySession(data) {
    token.value = data.access_token
    userId.value = data.user_id
    username.value = data.username
    uid.value = data.uid
    userRole.value = data.role
    localStorage.setItem('user_token', data.access_token)
  }

  async function login(credentials) {
    try {
      const data = await authApi.login(credentials)
      applySession(data)
      return true
    } catch (error) {
      console.error('登录错误:', error)
      throw error
    }
  }

  function logout() {
    token.value = ''
    userId.value = null
    username.value = ''
    uid.value = ''
    userRole.value = ''
    localStorage.removeItem('user_token')
  }

  async function initialize(admin) {
    try {
      const data = await authApi.initialize(admin)
      applySession(data)
      return true
    } catch (error) {
      console.error('初始化管理员错误:', error)
      throw error
    }
  }

  async function checkFirstRun() {
    try {
      const data = await authApi.checkFirstRun()
      return data.first_run
    } catch (error) {
      console.error('检查首次运行状态错误:', error)
      return false
    }
  }

  function getAuthHeaders() {
    return { Authorization: `Bearer ${token.value}` }
  }

  async function getCurrentUser() {
    try {
      const userData = await authApi.getCurrentUser()
      userId.value = userData.id
      username.value = userData.username
      uid.value = userData.uid
      userRole.value = userData.role
      return userData
    } catch (error) {
      console.error('获取用户信息错误:', error)
      throw error
    }
  }

  async function updateProfile(profileData) {
    try {
      const userData = await authApi.updateProfile(profileData)
      if (typeof userData.username === 'string') username.value = userData.username
      return userData
    } catch (error) {
      console.error('更新个人资料错误:', error)
      throw error
    }
  }

  async function getUsers({ pageSize = 100 } = {}) {
    try {
      const users = []
      let skip = 0
      while (true) {
        const batch = await authApi.getUsers({ skip, limit: pageSize })
        users.push(...batch)
        if (batch.length < pageSize) break
        skip += pageSize
      }
      return users
    } catch (error) {
      console.error('获取用户列表错误:', error)
      throw error
    }
  }

  return {
    token, userId, username, uid, userRole,
    isLoggedIn, isAdmin, isSuperAdmin,
    login, logout, initialize, checkFirstRun,
    getAuthHeaders, getCurrentUser, updateProfile, getUsers
  }
})

export const checkAdminPermission = () => {
  const userStore = useUserStore()
  if (!userStore.isAdmin) throw new Error('需要管理员权限')
  return true
}

export const checkSuperAdminPermission = () => {
  const userStore = useUserStore()
  return userStore.isSuperAdmin
}
