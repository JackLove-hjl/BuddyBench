<script setup lang="ts">
import { ref, watch } from 'vue'

const props = defineProps<{
  content: string
  streaming: boolean
}>()

// 流式进行中实时展开思考过程;回答完毕后自动折叠;
// 历史消息(非流式)与切换对话后默认折叠
const expanded = ref(false)
watch(
  () => props.streaming,
  (v) => {
    expanded.value = v
  },
  { immediate: true },
)
</script>

<template>
  <div v-if="content.trim()" class="reasoning">
    <button class="reasoning-toggle" @click="expanded = !expanded">
      <svg
        viewBox="0 0 16 16"
        width="13"
        height="13"
        fill="currentColor"
        :class="{ spin: streaming, 'expand-icon': true }"
      >
        <path v-if="streaming" d="M8 2a6 6 0 1 0 6 6h-1.5A4.5 4.5 0 1 1 8 3.5V2z" />
        <path v-else d="M8 3.5a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9zM8 2a6 6 0 1 0 0 12A6 6 0 0 0 8 2zm1 3.5h-1v3l2.5 1.5.5-.8-2-1.2V5.5z" />
      </svg>
      <span class="reasoning-label">{{ streaming ? '思考中…' : '已深度思考' }}</span>
      <svg viewBox="0 0 16 16" width="12" height="12" fill="currentColor" :class="{ rotated: expanded }" class="chevron">
        <path d="M4 6l4 4 4-4" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
    </button>
    <div v-if="expanded" class="reasoning-content">{{ content }}</div>
  </div>
</template>

<style scoped>
.reasoning {
  margin-bottom: 8px;
}
.reasoning-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: none;
  background: var(--bg-reasoning);
  color: var(--text-secondary);
  border-radius: 6px;
  padding: 5px 10px;
  font-size: 12px;
  cursor: pointer;
  font-family: inherit;
  transition: background 0.15s, color 0.15s;
}
.reasoning-toggle:hover {
  background: var(--bg-hover);
  color: var(--text);
}
.spin {
  animation: reasoning-spin 1s linear infinite;
}
@keyframes reasoning-spin {
  to {
    transform: rotate(360deg);
  }
}
.chevron {
  transition: transform 0.2s;
  color: var(--text-tertiary);
}
.chevron.rotated {
  transform: rotate(180deg);
}
.reasoning-content {
  margin-top: 8px;
  padding: 10px 12px;
  background: var(--bg-reasoning);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
