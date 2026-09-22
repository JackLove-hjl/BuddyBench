<script setup lang="ts">
import { ref } from 'vue'
import { NPopover } from 'naive-ui'
import { useThemeStore, type ThemeMode } from '../../stores/theme'

const themeStore = useThemeStore()
const show = ref(false)

const modes: { value: ThemeMode; label: string; icon: 'sun' | 'moon' | 'monitor' }[] = [
  { value: 'light', label: '浅色', icon: 'sun' },
  { value: 'dark', label: '深色', icon: 'moon' },
  { value: 'system', label: '跟随系统', icon: 'monitor' },
]

function select(mode: ThemeMode) {
  themeStore.setMode(mode)
  show.value = false
}
</script>

<template>
  <n-popover
    v-model:show="show"
    trigger="click"
    placement="bottom-end"
    :show-arrow="false"
    :offset="8"
    :body-style="{ padding: '6px', borderRadius: '10px' }"
  >
    <template #trigger>
      <button class="theme-btn" title="主题设置" :aria-label="'主题:' + themeStore.mode">
        <svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor">
          <path d="M6 0a6 6 0 1 0 4.47 10.03A5.5 5.5 0 0 1 6 0zm.5 2A4.5 4.5 0 1 1 2 6.5c0-.25.02-.5.05-.74A3.5 3.5 0 0 0 6.5 2z" />
        </svg>
      </button>
    </template>

    <div class="theme-menu">
      <button
        v-for="m in modes"
        :key="m.value"
        class="theme-option"
        :class="{ active: themeStore.mode === m.value }"
        @click="select(m.value)"
      >
        <svg class="opt-icon" viewBox="0 0 16 16" width="15" height="15" fill="currentColor">
          <path
            v-if="m.icon === 'sun'"
            d="M8 2a.5.5 0 0 1 .5.5v1a.5.5 0 0 1-1 0v-1A.5.5 0 0 1 8 2zm0 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm5-3a.5.5 0 0 1-.5.5h-1a.5.5 0 0 1 0-1h1a.5.5 0 0 1 .5.5zM2 8a.5.5 0 0 1 .5-.5h1a.5.5 0 0 1 0 1h-1A.5.5 0 0 1 2 8zm10.95-4.95a.5.5 0 0 1 0 .7l-.7.7a.5.5 0 0 1-.7-.7l.7-.7a.5.5 0 0 1 .7 0zM3.05 12.95a.5.5 0 0 1 0-.7l.7-.7a.5.5 0 0 1 .7.7l-.7.7a.5.5 0 0 1-.7 0zm9.9 0a.5.5 0 0 1-.7 0l-.7-.7a.5.5 0 0 1 .7-.7l.7.7a.5.5 0 0 1 0 .7zM3.05 3.05a.5.5 0 0 1 .7 0l.7.7a.5.5 0 0 1-.7.7l-.7-.7a.5.5 0 0 1 0-.7zM8 12.5a.5.5 0 0 1 .5.5v1a.5.5 0 0 1-1 0v-1a.5.5 0 0 1 .5-.5z"
          />
          <path
            v-else-if="m.icon === 'moon'"
            d="M6 0a6 6 0 1 0 4.47 10.03A5.5 5.5 0 0 1 6 0zm.5 2A4.5 4.5 0 1 1 2 6.5c0-.25.02-.5.05-.74A3.5 3.5 0 0 0 6.5 2z"
          />
          <path
            v-else
            d="M3.5 3h7a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H9l.5 1.5h1a.5.5 0 0 1 0 1h-5a.5.5 0 0 1 0-1h1L7 11H3.5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zm0 1v6h7V4h-7z"
          />
        </svg>
        <span class="opt-label">{{ m.label }}</span>
        <svg v-if="themeStore.mode === m.value" class="opt-check" viewBox="0 0 16 16" width="14" height="14" fill="currentColor">
          <path d="M6.5 11.5l-3.5-3.5 1-1 2.5 2.5 5-5 1 1-6 6z" />
        </svg>
      </button>
    </div>
  </n-popover>
</template>

<style scoped>
.theme-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.theme-btn:hover {
  background: var(--bg-hover);
  color: var(--text);
}
.theme-menu {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 150px;
}
.theme-option {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  border: none;
  background: transparent;
  color: var(--text);
  padding: 7px 10px;
  border-radius: 7px;
  cursor: pointer;
  font-size: 13px;
  font-family: inherit;
  transition: background 0.12s;
  text-align: left;
}
.theme-option:hover {
  background: var(--bg-hover);
}
.theme-option.active {
  background: var(--accent-soft);
  color: var(--accent);
}
.opt-icon {
  flex-shrink: 0;
  color: inherit;
}
.opt-label {
  flex: 1;
}
.opt-check {
  flex-shrink: 0;
}
</style>
