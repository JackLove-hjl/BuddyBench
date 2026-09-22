<script setup lang="ts">
import { computed, ref } from 'vue'
import { useConversationStore } from '../../stores/conversation'
import ThemeToggle from '../common/ThemeToggle.vue'
import ProviderSettingsDialog from '../settings/ProviderSettingsDialog.vue'

const convStore = useConversationStore()
const settingsOpen = ref(false)

const title = computed(() => {
  const id = convStore.currentId
  if (!id) return '开启新对话'
  const found = convStore.list.find((c) => c.id === id)
  return found?.title || '开启新对话'
})
</script>

<template>
  <header class="chat-header">
    <h1 class="chat-title">{{ title }}</h1>
    <div class="chat-header-actions">
      <ThemeToggle />
      <button class="icon-btn" title="设置" @click="settingsOpen = true">
        <svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="8" cy="8" r="2" />
          <path d="M8 1.5v1.2M8 13.3v1.2M1.5 8h1.2M13.3 8h1.2M3.4 3.4l.85.85M11.75 11.75l.85.85M3.4 12.6l.85-.85M11.75 4.25l.85-.85" />
        </svg>
      </button>
    </div>

    <ProviderSettingsDialog v-model:show="settingsOpen" />
  </header>
</template>

<style scoped>
.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  height: 52px;
  padding: 0 20px;
  border-bottom: 1px solid var(--border);
  background: var(--bg);
}
.chat-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  min-width: 0;
}
.chat-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.icon-btn {
  border: none;
  background: transparent;
  color: var(--text-secondary);
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.icon-btn:hover {
  background: var(--bg-hover);
  color: var(--text);
}
</style>
