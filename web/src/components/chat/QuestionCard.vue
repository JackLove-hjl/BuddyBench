<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { NButton } from 'naive-ui'
import { useConversationStore } from '../../stores/conversation'
import type { ApprovalAction, AskUserQuestion } from '../../types'

/**
 * 计划模式的需求澄清卡:模型用 ask_user 提问,这里渲染成「逐题翻页 + 选择引导 + 自定义输入」。
 *
 * - 一次只显示一题,点选项即作答并自动翻到下一题(自定义回答要先打字,不自动翻);
 * - 顶部圆点可跳题,底部「上一题 / 下一题」可来回改;
 * - 页脚「完成」把答案回传,「跳过」表示授权模型自行决定(它要在计划里写明假设);
 * - 答案走 HITL 的 `respond` 决策作为工具结果回到模型 —— 与审批卡共用同一套中断/恢复链路,
 *   所以挂起标记落在 meta.pending_approval 里,刷新后仍能作答。
 */
const props = defineProps<{ actions: ApprovalAction[] }>()

const convStore = useConversationStore()
const submitting = ref(false)
const collapsed = ref(false)
/** 当前显示第几题(一次只渲染一题) */
const current = ref(0)
/** 每题选中的下标;等于选项个数时表示"自定义回答" */
const picked = ref<Record<number, number>>({})
const custom = ref<Record<number, string>>({})

const LETTERS = 'ABCDEFGH'

/** 从挂起动作里取问题(结构不完整时只保留有 question 的项,渲染层不做假设) */
const questions = computed<AskUserQuestion[]>(() => {
  const action = props.actions.find((a) => a.name === 'ask_user')
  const raw = action?.args?.questions
  if (!Array.isArray(raw)) return []
  return raw
    .filter((q): q is AskUserQuestion => !!q && typeof (q as AskUserQuestion).question === 'string')
    .map((q) => ({
      question: q.question,
      options: Array.isArray(q.options)
        ? q.options.filter((o) => o && typeof o.label === 'string')
        : [],
    }))
})

/** 「自定义回答」在选项里的位置(排在预设选项之后) */
const customIndex = (q: AskUserQuestion) => q.options?.length || 0

/** 防越界:问题列表理论上不变,但防御一下空数组/越界下标 */
const index = computed(() => Math.min(current.value, questions.value.length - 1))
const question = computed(() => questions.value[index.value])

const customEl = ref<HTMLTextAreaElement | null>(null)

function isAnswered(i: number): boolean {
  const q = questions.value[i]
  if (!q) return false
  const p = picked.value[i]
  if (p === undefined) return false
  if (p === customIndex(q)) return !!custom.value[i]?.trim()
  return true
}

const answeredCount = computed(() => questions.value.filter((_, i) => isAnswered(i)).length)

function goto(i: number) {
  current.value = Math.max(0, Math.min(i, questions.value.length - 1))
}

function pick(qi: number, oi: number, q: AskUserQuestion) {
  picked.value[qi] = oi
  // 选中自定义项时准备好输入框的值并聚焦 —— 要先打字,所以不自动翻页
  if (oi === customIndex(q)) {
    if (custom.value[qi] === undefined) custom.value[qi] = ''
    void nextTick(() => customEl.value?.focus())
    return
  }
  // 点选项即作答,自动进入下一题;已是最后一题就停在原地(「完成」就在下面)
  if (qi < questions.value.length - 1) goto(qi + 1)
}

function answerText(q: AskUserQuestion, i: number): string {
  const p = picked.value[i]
  if (p === undefined) return ''
  if (p === customIndex(q)) return custom.value[i]?.trim() || ''
  const opt = q.options?.[p]
  if (!opt) return ''
  return opt.description ? `${opt.label}(${opt.description})` : opt.label
}

/** 回传给模型的答案文本;未作答的题也点明,避免模型脑补成"用户选了默认" */
function buildMessage(skipped: boolean): string {
  if (skipped) {
    return '用户跳过了这轮提问,授权你自行决定,并在计划里写明你的选择与假设。'
  }
  const lines = questions.value.map((q, i) => {
    const ans = answerText(q, i)
    return `${i + 1}. ${q.question}\n   → ${ans || '(未作答,请按你的判断处理并写明假设)'}`
  })
  return `用户对提问的回答:\n${lines.join('\n')}`
}

async function submit(skipped: boolean) {
  if (submitting.value) return
  if (!skipped && answeredCount.value === 0) return
  submitting.value = true
  try {
    await convStore.approve('respond', buildMessage(skipped))
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="question-card">
    <button class="question-head" @click="collapsed = !collapsed">
      <svg class="question-icon" viewBox="0 0 16 16" width="14" height="14" fill="currentColor">
        <path
          d="M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zm0 1.5a5 5 0 1 1 0 10A5 5 0 0 1 8 3zm-.1 2.2c1.3 0 2.2.75 2.2 1.8 0 .85-.5 1.3-1.15 1.75-.6.4-.75.65-.75 1.15v.2H7v-.3c0-.85.35-1.3 1-1.75.55-.4.8-.65.8-1.1 0-.5-.4-.9-1-.9s-1 .4-1.05 1H6.1c.05-1.1.85-1.85 1.8-1.85zM7.55 11h1.1v1.1h-1.1V11z"
        />
      </svg>
      <span class="question-title">问题</span>
      <span class="question-count">{{ answeredCount }}/{{ questions.length }}</span>
      <svg class="question-chevron" :class="{ collapsed }" viewBox="0 0 12 12" width="11" height="11" fill="none">
        <path d="M2 4.5l4 4 4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
    </button>

    <div v-if="!collapsed && question" class="question-body">
      <!-- 进度:圆点可跳题,右侧显示 当前/总数 -->
      <div class="question-progress">
        <button
          v-for="(_, i) in questions"
          :key="i"
          type="button"
          class="question-dot"
          :class="{ answered: isAnswered(i), active: i === index }"
          :title="`第 ${i + 1} 题${isAnswered(i) ? '(已作答)' : ''}`"
          @click="goto(i)"
        ></button>
        <span class="question-step">{{ index + 1 }} / {{ questions.length }}</span>
      </div>

      <div class="question-text">{{ index + 1 }}. {{ question.question }}</div>

      <button
        v-for="(opt, oi) in question.options"
        :key="oi"
        type="button"
        class="question-option"
        :class="{ picked: picked[index] === oi }"
        @click="pick(index, oi, question)"
      >
        <span class="question-letter">{{ LETTERS[oi] }}</span>
        <span class="question-opt-main">
          <span class="question-opt-label">{{ opt.label }}</span>
          <span v-if="opt.description" class="question-opt-desc">{{ opt.description }}</span>
        </span>
      </button>

      <button
        type="button"
        class="question-option"
        :class="{ picked: picked[index] === customIndex(question) }"
        @click="pick(index, customIndex(question), question)"
      >
        <span class="question-letter">{{ LETTERS[customIndex(question)] }}</span>
        <span class="question-opt-main">
          <span class="question-opt-label muted">输入自定义回答…</span>
        </span>
      </button>
      <textarea
        v-if="picked[index] === customIndex(question)"
        ref="customEl"
        v-model="custom[index]"
        class="question-custom"
        rows="2"
        placeholder="写下你的答案"
      ></textarea>

      <div class="question-nav">
        <n-button size="tiny" quaternary :disabled="index === 0" @click="goto(index - 1)">
          ‹ 上一题
        </n-button>
        <n-button
          size="tiny"
          quaternary
          :disabled="index >= questions.length - 1"
          @click="goto(index + 1)"
        >
          下一题 ›
        </n-button>
      </div>
    </div>

    <div class="question-actions">
      <n-button size="small" :disabled="submitting" @click="submit(true)">跳过</n-button>
      <n-button
        size="small"
        type="primary"
        :loading="submitting"
        :disabled="submitting || !answeredCount"
        @click="submit(false)"
      >
        完成
      </n-button>
    </div>
  </div>
</template>

<style scoped>
.question-card {
  margin: 8px 0;
  border: 1px solid var(--border-strong);
  border-radius: 10px;
  background: var(--bg-elevated);
  overflow: hidden;
}
.question-head {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  border: none;
  background: transparent;
  padding: 9px 12px;
  cursor: pointer;
  font-family: inherit;
  color: var(--text);
}
.question-head:hover {
  background: var(--bg-hover);
}
.question-icon {
  flex-shrink: 0;
  color: var(--accent);
}
.question-title {
  font-size: 13px;
  font-weight: 600;
}
.question-count {
  margin-left: auto;
  font-size: 12px;
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
}
.question-chevron {
  flex-shrink: 0;
  color: var(--text-tertiary);
  transition: transform 0.15s;
}
.question-chevron.collapsed {
  transform: rotate(-90deg);
}
.question-body {
  padding: 0 12px 4px;
  border-top: 1px solid var(--border);
}
/* 进度点:已作答淡蓝、当前题实心,可点击跳题 */
.question-progress {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 9px 0 0;
}
.question-dot {
  width: 8px;
  height: 8px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: var(--bg-active);
  cursor: pointer;
  transition: background 0.12s;
}
.question-dot.answered {
  background: var(--accent-soft);
}
.question-dot.active {
  background: var(--accent);
}
.question-step {
  margin-left: auto;
  font-size: 11px;
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
}
.question-text {
  font-size: 13px;
  line-height: 1.6;
  color: var(--text);
  margin: 8px 0;
}
.question-option {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  width: 100%;
  box-sizing: border-box;
  margin-bottom: 6px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: transparent;
  color: var(--text);
  font-family: inherit;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.12s, background 0.12s;
}
.question-option:hover {
  border-color: var(--accent);
  background: var(--bg-hover);
}
.question-option.picked {
  border-color: var(--accent);
  background: var(--accent-soft);
}
.question-letter {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  border-radius: 5px;
  background: var(--bg-active);
  color: var(--text-secondary);
  font-size: 11px;
  font-weight: 600;
  line-height: 18px;
  text-align: center;
}
.question-option.picked .question-letter {
  background: var(--accent);
  color: #fff;
}
.question-opt-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.question-opt-label {
  line-height: 1.5;
  word-break: break-word;
}
.question-opt-label.muted {
  color: var(--text-tertiary);
}
.question-opt-desc {
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-tertiary);
  word-break: break-word;
}
.question-custom {
  width: 100%;
  box-sizing: border-box;
  margin: 2px 0 6px;
  padding: 8px 10px;
  border: 1px solid var(--accent);
  border-radius: 8px;
  background: var(--bg);
  color: var(--text);
  font-family: inherit;
  font-size: 13px;
  line-height: 1.5;
  resize: vertical;
}
.question-custom:focus {
  outline: none;
}
.question-nav {
  display: flex;
  justify-content: space-between;
  padding: 2px 0 6px;
}
.question-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 10px 12px;
  border-top: 1px solid var(--border);
}
</style>
