import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

export type ThemeMode = 'light' | 'dark' | 'system'

const STORAGE_KEY = 'llm-theme'

function systemPrefersDark(): boolean {
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

function initialMode(): ThemeMode {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved === 'light' || saved === 'dark' || saved === 'system') return saved
  return 'system'
}

export const useThemeStore = defineStore('theme', () => {
  const mode = ref<ThemeMode>(initialMode())

  const resolved = computed(() => (mode.value === 'system' ? (systemPrefersDark() ? 'dark' : 'light') : mode.value))
  const isDark = computed(() => resolved.value === 'dark')

  let mql: MediaQueryList | null = null
  let mqlHandler: ((e: MediaQueryListEvent) => void) | null = null

  function apply() {
    document.documentElement.dataset.theme = resolved.value
    localStorage.setItem(STORAGE_KEY, mode.value)
  }

  function setMode(next: ThemeMode) {
    mode.value = next
    apply()
  }

  function init() {
    if (mql) return
    mql = window.matchMedia('(prefers-color-scheme: dark)')
    mqlHandler = () => {
      if (mode.value === 'system') apply()
    }
    mql.addEventListener('change', mqlHandler)
    apply()
  }

  function dispose() {
    mql?.removeEventListener('change', mqlHandler!)
    mql = null
    mqlHandler = null
  }

  return { mode, resolved, isDark, setMode, init, dispose }
})
