<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useConversationStore } from '../../stores/conversation'
import { formatStepDuration, formatTurnDuration, turnElapsedMs } from '../../utils/turnTimer'
import type { ToolCall } from '../../types'

/**
 * 回复底部的「用时 X 秒」。
 *
 * 数值有两个来源,口径一致(都是"干活用时",等待用户审批/回答的那段不计入):
 * - 已落库的消息:后端写进 meta.duration_ms → **刷新后仍在**;
 * - 正在流式的这条:前端计时实时走秒,收到 done 帧后以后端值为准。
 *
 * 展开看本轮各步工具的耗时 —— 回答"这 57 秒花在哪了"。
 */
const props = withDefaults(
  defineProps<{
    /** 已确定的用时(ms):落库值或本地兜底值 */
    ms?: number | null
    /** 正在流式/正等用户决策:从 store 的计时状态机实时取 */
    live?: boolean
    /** 本轮的工具调用,展开后列出分步耗时 */
    steps?: ToolCall[]
    /** 下方还有回复内容时画一条分割线,把"用时"和正文分开 */
    divider?: boolean
  }>(),
  { ms: null, live: false, steps: () => [], divider: false },
)

const convStore = useConversationStore()
const expanded = ref(false)
const now = ref(Date.now())

// 只在实时阶段走心跳:最小展示单位是秒,心跳快一点只为让"跳秒"不迟滞
let ticker: number | null = null
function stopTicker() {
  if (ticker !== null) {
    window.clearInterval(ticker)
    ticker = null
  }
}
watch(
  () => props.live,
  (on) => {
    stopTicker()
    now.value = Date.now()
    if (on) ticker = window.setInterval(() => (now.value = Date.now()), 250)
  },
  { immediate: true },
)
onBeforeUnmount(stopTicker)

const totalMs = computed(() => (props.live ? turnElapsedMs(convStore.turnTiming, now.value) : props.ms ?? 0))
/** 还没跑够 1 秒就不显示,避免刚发出就冒出一个"用时 0 秒" */
const visible = computed(() => totalMs.value > 0)
const label = computed(() => `用时 ${formatTurnDuration(totalMs.value)}`)

/** 真正耗过时的步骤:0ms 的调用列出来只是噪声 */
const steps = computed(() => props.steps.filter((s) => (s.duration_ms ?? 0) > 0))
</script>

<template>
  <div v-if="visible" class="turn-duration" :class="{ divider }">
    <button
      type="button"
      class="turn-duration-btn"
      :title="expanded ? '收起用时明细' : '展开用时明细'"
      @click="expanded = !expanded"
    >
      <svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round">
        <circle cx="8" cy="8" r="6" />
        <path d="M8 4.6V8l2.4 1.6" />
      </svg>
      <span class="turn-duration-text">{{ label }}</span>
      <svg class="turn-duration-caret" :class="{ rotated: expanded }" viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
        <path d="M4 6l4 4 4-4" />
      </svg>
    </button>

    <div v-if="expanded" class="turn-duration-detail">
      <div v-for="(step, i) in steps" :key="`${step.name}-${i}`" class="turn-duration-step">
        <span class="step-name">{{ step.name }}</span>
        <span class="step-value">{{ formatStepDuration(step.duration_ms || 0) }}</span>
      </div>
      <div class="turn-duration-note">等待你审批或回答的时间不计入</div>
    </div>
  </div>
</template>

<style scoped>
.turn-duration {
  margin-top: 0;
}
/* 用时分隔线:计时在回复内容上方,底下用一条线把两者分开 */
.turn-duration.divider {
  padding-bottom: 6px;
  margin-bottom: 8px;
  border-bottom: 1px solid var(--border);
}
.turn-duration-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 6px;
  margin-left: -6px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-tertiary);
  font-size: 12px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.turn-duration-btn:hover {
  background: var(--bg-hover);
  color: var(--text-secondary);
}
.turn-duration-caret {
  transition: transform 0.15s;
}
.turn-duration-caret.rotated {
  transform: rotate(180deg);
}
.turn-duration-detail {
  margin-top: 2px;
  padding: 4px 8px;
  border-left: 2px solid var(--border);
  color: var(--text-tertiary);
  font-size: 12px;
}
.turn-duration-step {
  display: flex;
  gap: 8px;
  line-height: 1.7;
}
.step-name {
  font-family: 'SFMono-Regular', Consolas, monospace;
}
.step-value {
  color: var(--text-secondary);
}
.turn-duration-note {
  margin-top: 2px;
  color: var(--text-tertiary);
}
</style>
