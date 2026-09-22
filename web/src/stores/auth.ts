import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { fetchMe, login as apiLogin, register as apiRegister } from '../api/auth'
import type { UserInfo } from '../api/auth'
import { router } from '../router'

const TOKEN_KEY = 'llm-token'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>(localStorage.getItem(TOKEN_KEY) || '')
  const user = ref<UserInfo | null>(null)
  const initializing = ref(true)

  const isLoggedIn = computed(() => !!token.value)

  async function restore() {
    initializing.value = true
    if (!token.value) {
      initializing.value = false
      return
    }
    try {
      user.value = await fetchMe()
    } catch {
      // token 失效
      token.value = ''
      localStorage.removeItem(TOKEN_KEY)
      user.value = null
    } finally {
      initializing.value = false
    }
  }

  async function login(username: string, password: string) {
    const resp = await apiLogin({ username, password })
    token.value = resp.access_token
    localStorage.setItem(TOKEN_KEY, resp.access_token)
    user.value = await fetchMe()
  }

  async function register(username: string, password: string) {
    const resp = await apiRegister({ username, password })
    token.value = resp.access_token
    localStorage.setItem(TOKEN_KEY, resp.access_token)
    user.value = await fetchMe()
  }

  function logout() {
    token.value = ''
    localStorage.removeItem(TOKEN_KEY)
    user.value = null
    void router.push('/login')
  }

  return { token, user, initializing, isLoggedIn, restore, login, register, logout }
})
