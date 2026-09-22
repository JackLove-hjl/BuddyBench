<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  message: string
  interrupted?: boolean
}>()

const emit = defineEmits<{ (e: 'retry'): void }>()

const label = computed(() => (props.interrupted ? '连接中断' : '回复出错'))
</script>

<template>
  <div class="error-banner">
    <svg viewBox="0 0 16 16" width="15" height="15" fill="currentColor">
      <path d="M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM8 3a5 5 0 1 1 0 10A5 5 0 0 1 8 3zm-.75 3.25h1.5v3.5h-1.5v-3.5zM8 10.75a.7.7 0 1 1 0 1.4.7.7 0 0 1 0-1.4z" />
    </svg>
    <span class="error-text">{{ label }}:{{ message }}</span>
    <button v-if="!interrupted" class="error-retry" @click="emit('retry')">重试</button>
    <span v-else class="error-retry-hint">刷新页面或重新发送可恢复</span>
  </div>
</template>

<style scoped>
.error-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  padding: 9px 12px;
  background: var(--danger-soft);
  border: 1px solid rgba(212, 61, 61, 0.25);
  border-radius: 8px;
  color: var(--danger);
  font-size: 13px;
  flex-wrap: wrap;
}
.error-text {
  flex: 1;
  word-break: break-all;
}
.error-retry {
  border: 1px solid currentColor;
  background: transparent;
  color: inherit;
  border-radius: 6px;
  padding: 3px 12px;
  font-size: 12px;
  cursor: pointer;
  font-family: inherit;
  transition: background 0.15s;
}
.error-retry:hover {
  background: var(--danger-soft);
}
.error-retry-hint {
  color: var(--text-tertiary);
  font-size: 12px;
}
</style>
