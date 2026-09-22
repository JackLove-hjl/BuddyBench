<script setup lang="ts">
import { computed, ref } from 'vue'
import { NPopover } from 'naive-ui'
import { useAuthStore } from '../../stores/auth'

const authStore = useAuthStore()
const show = ref(false)

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
      <button class="user-menu-item" @click="onLogout">
        <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor">
          <path d="M8 2.5a.5.5 0 0 1 .5.5v6a.5.5 0 0 1-1 0V3a.5.5 0 0 1 .5-.5zM4.5 4.2a.5.5 0 0 1-.12.7A5 5 0 1 0 8 3a.5.5 0 1 1-.42-.9A6 6 0 1 1 4.5 4.2z" transform="rotate(180 8 8)" />
        </svg>
        退出登录
      </button>
    </div>
  </n-popover>
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
  background: var(--danger-soft);
  color: var(--danger);
}
</style>
