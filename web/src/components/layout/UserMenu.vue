<script setup lang="ts">
import { computed, ref } from 'vue'
import { NPopover } from 'naive-ui'
import { useAuthStore } from '../../stores/auth'
import { useThemeStore, type ThemeMode } from '../../stores/theme'
import ProviderSettingsDialog from '../settings/ProviderSettingsDialog.vue'

const authStore = useAuthStore()
const themeStore = useThemeStore()
const show = ref(false)
const settingsOpen = ref(false)

// 主题与设置原先在对话右上角,统一收到个人中心里
const THEMES: { value: ThemeMode; label: string }[] = [
  { value: 'light', label: '浅色' },
  { value: 'dark', label: '深色' },
  { value: 'system', label: '跟随系统' },
]

function onSettings() {
  // 先开弹窗再收浮层:顺序反过来在弹窗还挂在浮层里的时代会丢事件(见模板注释)
  settingsOpen.value = true
  show.value = false
}

const username = computed(() => authStore.user?.username || '用户')
const avatarText = computed(() => (username.value || '?').slice(0, 1).toUpperCase())
const createdAt = computed(() => {
  if (!authStore.user?.created_at) return ''
  try {
    return new Date(authStore.user.created_at).toLocaleDateString('zh-CN')
  } catch {
    return ''
  }
})

function onLogout() {
  show.value = false
  authStore.logout()
}
</script>

<template>
  <n-popover v-model:show="show" trigger="click" placement="top-start" :show-arrow="false" :offset="8">
    <template #trigger>
      <div class="user-chip" title="个人中心">
        <div class="user-avatar">{{ avatarText }}</div>
        <span class="user-name">{{ username }}</span>
        <svg class="user-chevron" viewBox="0 0 12 12" width="10" height="10" fill="currentColor">
          <path d="M2 4l4 4 4-4" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </div>
    </template>

    <div class="user-menu">
      <div class="user-menu-head">
        <div class="user-menu-avatar">{{ avatarText }}</div>
        <div class="user-menu-info">
          <div class="user-menu-name">{{ username }}</div>
          <div v-if="createdAt" class="user-menu-date">注册于 {{ createdAt }}</div>
        </div>
      </div>
      <!-- 主题:直接三段切换,不再单开一个浮层 -->
      <div class="user-menu-label">主题</div>
      <div class="theme-seg">
        <button
          v-for="item in THEMES"
          :key="item.value"
          type="button"
          class="theme-seg-btn"
          :class="{ active: themeStore.mode === item.value }"
          @click="themeStore.setMode(item.value)"
        >
          {{ item.label }}
        </button>
      </div>

      <div class="user-menu-sep" />

      <button class="user-menu-item" @click="onSettings">
        <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="8" cy="8" r="2" />
          <path d="M8 1.5v1.2M8 13.3v1.2M1.5 8h1.2M13.3 8h1.2M3.4 3.4l.85.85M11.75 11.75l.85.85M3.4 12.6l.85-.85M11.75 4.25l.85-.85" />
        </svg>
        设置
      </button>

      <button class="user-menu-item danger" @click="onLogout">
        <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor">
          <path d="M8 2.5a.5.5 0 0 1 .5.5v6a.5.5 0 0 1-1 0V3a.5.5 0 0 1 .5-.5zM4.5 4.2a.5.5 0 0 1-.12.7A5 5 0 1 0 8 3a.5.5 0 1 1-.42-.9A6 6 0 1 1 4.5 4.2z" transform="rotate(180 8 8)" />
        </svg>
        退出登录
      </button>
    </div>
  </n-popover>

  <!--
    设置弹窗必须挂在 popover **外面**:naive-ui 的 popover 关闭时会卸载内容,
    挂在里面的写法是「点设置 → 先关 popover → 弹窗随内容一起被卸载」,弹窗根本打不开。
    放到外面后 popover 只是收起,弹窗独立存在。
  -->
  <ProviderSettingsDialog v-model:show="settingsOpen" />
</template>

<style scoped>
.user-chip {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 8px;
  cursor: pointer;
  width: 100%;
  transition: background 0.15s;
}
.user-chip:hover {
  background: var(--bg-hover);
}
.user-avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: linear-gradient(135deg, #f59e0b, #ef4444);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.user-name {
  font-size: 14px;
  color: var(--text);
  flex: 1;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.user-chevron {
  color: var(--text-tertiary);
  flex-shrink: 0;
}
.user-menu {
  min-width: 220px;
}
.user-menu-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 6px;
}
.user-menu-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: linear-gradient(135deg, #f59e0b, #ef4444);
  color: #fff;
  font-size: 16px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
}
.user-menu-info {
  min-width: 0;
}
.user-menu-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
}
.user-menu-date {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-top: 2px;
}
.user-menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  border: none;
  background: transparent;
  color: var(--text);
  padding: 8px 10px;
  border-radius: 7px;
  cursor: pointer;
  font-size: 13px;
  font-family: inherit;
  transition: background 0.12s;
  text-align: left;
}
.user-menu-item:hover {
  background: var(--bg-hover);
  color: var(--text);
}
/* 退出登录保持危险色;设置项用普通 hover */
.user-menu-item.danger:hover {
  background: var(--danger-soft);
  color: var(--danger);
}
.user-menu-label {
  padding: 4px 10px 6px;
  font-size: 12px;
  color: var(--text-tertiary);
}
.theme-seg {
  display: flex;
  gap: 4px;
  padding: 0 6px;
}
.theme-seg-btn {
  flex: 1;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: var(--bg-elevated);
  color: var(--text-secondary);
  font-size: 12.5px;
  font-family: inherit;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s, background 0.15s;
}
.theme-seg-btn:hover {
  border-color: var(--border-strong);
  color: var(--text);
}
.theme-seg-btn.active {
  border-color: var(--accent);
  color: var(--accent);
  background: var(--accent-soft);
}
.user-menu-sep {
  height: 1px;
  margin: 8px 10px;
  background: var(--border);
}
</style>
