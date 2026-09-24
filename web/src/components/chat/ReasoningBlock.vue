<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'

const props = defineProps<{
  content: string
  streaming: boolean
}>()

/**
 * 默认折叠(流式期间也不自动展开)。
 *
 * 思考内容动辄几千字,自动展开会把整屏占满,外层消息列表还会被一路推着滚 ——
 * 想看时点开即可;展开状态下自动跟随最新一行,用户往上翻后就不再打扰。
 */
const expanded = ref(false)
const contentEl = ref<HTMLElement | null>(null)

/** 贴底时才跟随:用户手动往上翻(离底超过阈值)就停止,避免把他的阅读位置拽走 */
function followTail() {
  const el = contentEl.value
  if (!el || !expanded.value) return
  if (el.scrollHeight - el.scrollTop - el.clientHeight > 60) return
  el.scrollTop = el.scrollHeight
}

watch(() => props.content, followTail)

// 刚展开时直接落到最新一行:首次展开离底很远,followTail 的贴底判断不会生效
watch(expanded, (v) => {
  if (!v) return
  void nextTick(() => {
    const el = contentEl.value
    if (el) el.scrollTop = el.scrollHeight
  })
})
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
    <div v-if="expanded" ref="contentEl" class="reasoning-content">{{ content }}</div>
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
  /* 思考可能几千字:限高 + 内部滚动,别把消息流顶走 */
  max-height: 240px;
  overflow-y: auto;
  /* 滚到上下边界时不把滚动传递给外层消息列表 */
  overscroll-behavior: contain;
}
</style>
