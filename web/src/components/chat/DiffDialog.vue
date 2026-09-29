<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from 'vue'
import DiffView from './DiffView.vue'
import { diffStats, type DiffLine } from '../../utils/diff'

/**
 * 「查看变更」弹窗:完整 diff(卡片里只展示前若干行,这里给全部)。
 *
 * 点空白 / 按 Esc 关闭,与图片放大、反馈弹窗保持一致。
 */
const props = defineProps<{
  show: boolean
  /** 文件路径(标题) */
  path?: string
  lines: DiffLine[]
  lang?: string
}>()

const emit = defineEmits<{ (e: 'update:show', value: boolean): void }>()

const stats = computed(() => diffStats(props.lines))

function close() {
  emit('update:show', false)
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') close()
}

watch(
  () => props.show,
  (visible) => {
    if (visible) window.addEventListener('keydown', onKeydown)
    else window.removeEventListener('keydown', onKeydown)
  },
)
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <Teleport to="body">
    <div v-if="show" class="diff-mask" @click="close">
      <div class="diff-panel" role="dialog" aria-modal="true" @click.stop>
        <div class="diff-head">
          <span class="diff-title" :title="path">{{ path || '变更' }}</span>
          <span class="diff-stats">
            <span class="stat-add">+{{ stats.added }}</span>
            <span class="stat-del">−{{ stats.removed }}</span>
          </span>
          <button type="button" class="diff-close" title="关闭" @click="close">×</button>
        </div>
        <DiffView :lines="lines" :lang="lang" :code="lines.map((l) => l.text).join('\n')" />
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.diff-mask {
  position: fixed;
  inset: 0;
  z-index: 2100;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px 24px;
  box-sizing: border-box;
  background: rgba(0, 0, 0, 0.45);
}
.diff-panel {
  width: 100%;
  /* diff 越宽越好读:比消息区再宽一档 */
  max-width: 1280px;
  max-height: 100%;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--bg-elevated);
  box-shadow: var(--shadow);
  overflow: hidden;
}
.diff-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-reasoning);
}
.diff-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  font-family: 'SFMono-Regular', Consolas, monospace;
}
.diff-stats {
  display: flex;
  gap: 6px;
  font-size: 12px;
  font-family: 'SFMono-Regular', Consolas, monospace;
  flex-shrink: 0;
}
.stat-add {
  color: var(--success);
}
.stat-del {
  color: var(--danger);
}
.diff-close {
  border: none;
  background: transparent;
  color: var(--text-tertiary);
  font-size: 20px;
  line-height: 1;
  width: 28px;
  height: 28px;
  border-radius: 6px;
  cursor: pointer;
  flex-shrink: 0;
}
.diff-close:hover {
  background: var(--bg-hover);
  color: var(--text);
}
/* 弹窗里给 diff 全部高度(组件默认是卡片内的 360px) */
.diff-panel :deep(.diff-view) {
  max-height: none;
  flex: 1 1 auto;
  min-height: 0;
  border-top: none;
  padding: 10px 0;
  font-size: 12.5px;
}
</style>
