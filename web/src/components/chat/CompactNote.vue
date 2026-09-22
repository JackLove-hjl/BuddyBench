<script setup lang="ts">
/** 上下文压缩提示:自动预判或溢出恢复触发时插在回答之前。 */
defineProps<{ summary: string }>()
</script>

<template>
  <div class="compact-note">
    <svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor" class="compact-icon">
      <path d="M2 3.5h12v1.5H2V3.5zm0 3.5h8v1.5H2V7zm0 3.5h12V12H2v-1.5z" />
    </svg>
    <span class="compact-label">上下文已压缩</span>
    <span class="compact-summary" :title="summary">{{ summary }}</span>
  </div>
</template>

<style scoped>
.compact-note {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 8px 0;
  padding: 6px 10px;
  border-radius: 8px;
  background: var(--bg-hover);
  font-size: 12px;
  color: var(--text-tertiary);
}
.compact-icon {
  flex-shrink: 0;
}
.compact-label {
  flex-shrink: 0;
  font-weight: 600;
  color: var(--text-secondary);
}
.compact-summary {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
