<script setup lang="ts">
import { computed } from 'vue'
import { useConversationStore } from '../../stores/conversation'
import { useSidePanel } from '../../composables/useSidePanel'

const convStore = useConversationStore()
const { open: panelOpen, openPanel } = useSidePanel()

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
      <!-- 侧栏收起时,在这里把右侧栏打开(打开状态会被记住) -->
      <button v-if="!panelOpen" class="icon-btn" title="打开右侧栏" @click="openPanel()">
        <svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">
          <rect x="1.8" y="2.8" width="12.4" height="10.4" rx="1.6" />
          <path d="M9.8 2.8v10.4" />
        </svg>
      </button>
    </div>
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
