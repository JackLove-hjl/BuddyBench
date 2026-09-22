<script setup lang="ts">
import { computed, ref } from 'vue'
import { NButton } from 'naive-ui'
import { useConversationStore } from '../../stores/conversation'
import type { ApprovalAction } from '../../types'
import MarkdownContent from './MarkdownContent.vue'

/**
 * 审批卡:两类挂起共用同一张卡。
 *
 * 1. **危险操作**(「工作区可写」下的写文件 / 执行命令等):展示工具名与参数,逐次批准或拒绝
 *    (参考 deepseek-harness 的 ask 审批,其预设里 workspace-write = ask、danger-full-access = never)。
 * 2. **计划评审**(`exit_plan_mode`):按 Markdown 渲染计划全文,并允许「继续修改」时附反馈
 *    —— 反馈会作为工具结果回到模型手里(对齐 harness PlanReviewPanel 的 Keep planning 语义)。
 */
const props = defineProps<{ actions: ApprovalAction[] }>()

const convStore = useConversationStore()
const busy = ref<'approve' | 'reject' | null>(null)
const feedback = ref('')

/** 计划评审:挂起的动作里有 exit_plan_mode 时取其计划正文 */
const planText = computed(() => {
  const action = props.actions.find((a) => a.name === 'exit_plan_mode')
  const plan = action?.args?.plan
  return typeof plan === 'string' ? plan.trim() : ''
})
const isPlanReview = computed(() => planText.value !== '')

async function decide(type: 'approve' | 'reject') {
  if (busy.value) return
  busy.value = type
  try {
    await convStore.approve(type, type === 'reject' ? feedback.value.trim() : undefined)
  } finally {
    busy.value = null
  }
}

function argsText(action: ApprovalAction): string {
  if (!action.args) return ''
  try {
    const text = JSON.stringify(action.args, null, 2)
    return text.length > 600 ? text.slice(0, 600) + '…' : text
  } catch {
    return String(action.args)
  }
}
</script>

<template>
  <div class="approval-card">
    <div class="approval-head">
      <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" class="approval-icon">
        <path d="M8 1.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM8 3a5 5 0 1 1 0 10A5 5 0 0 1 8 3zM7.4 5.5h1.2v4H7.4v-4zm0 5h1.2v1.2H7.4V10.5z" />
      </svg>
      <span class="approval-title">{{ isPlanReview ? '计划已就绪,等待你确认' : '需要你确认后才会执行' }}</span>
    </div>

    <!-- 计划评审:按 Markdown 渲染完整计划,而不是把它塞进 JSON 代码块 -->
    <div v-if="isPlanReview" class="approval-plan">
      <MarkdownContent :content="planText" />
    </div>

    <template v-else>
      <div v-for="(action, i) in props.actions" :key="i" class="approval-action">
        <div class="approval-name">{{ action.name }}</div>
        <pre v-if="argsText(action)" class="approval-args">{{ argsText(action) }}</pre>
      </div>
    </template>

    <div v-if="isPlanReview" class="approval-feedback">
      <textarea
        v-model="feedback"
        class="approval-feedback-input"
        rows="2"
        placeholder="可选:告诉模型还需要改什么(选「继续修改」时一并回传)"
      ></textarea>
    </div>

    <div class="approval-actions">
      <n-button size="small" :loading="busy === 'reject'" :disabled="!!busy" @click="decide('reject')">
        {{ isPlanReview ? '继续修改' : '拒绝' }}
      </n-button>
      <n-button size="small" type="primary" :loading="busy === 'approve'" :disabled="!!busy" @click="decide('approve')">
        {{ isPlanReview ? '批准并开始实施' : '批准执行' }}
      </n-button>
    </div>
  </div>
</template>

<style scoped>
.approval-card {
  margin: 8px 0;
  border: 1px solid var(--accent);
  border-radius: 10px;
  background: var(--bg-elevated);
  overflow: hidden;
}
.approval-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 13px;
  font-weight: 600;
}
.approval-icon {
  flex-shrink: 0;
}
.approval-action {
  padding: 8px 12px;
  border-top: 1px solid var(--border);
}
.approval-name {
  font-size: 12px;
  font-weight: 600;
  color: var(--text);
  font-family: 'SFMono-Regular', Consolas, monospace;
}
.approval-args {
  margin: 6px 0 0;
  padding: 8px 10px;
  background: var(--bg-code);
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 180px;
  overflow: auto;
}
/* 计划正文:可能很长,限高后可滚动,避免把输入框挤出视口 */
.approval-plan {
  padding: 4px 12px 8px;
  border-top: 1px solid var(--border);
  font-size: 13px;
  max-height: 380px;
  overflow: auto;
}
.approval-feedback {
  padding: 0 12px 8px;
}
.approval-feedback-input {
  width: 100%;
  box-sizing: border-box;
  resize: vertical;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-code);
  color: var(--text);
  font-family: inherit;
  font-size: 12px;
  line-height: 1.5;
  padding: 8px 10px;
}
.approval-feedback-input:focus {
  outline: none;
  border-color: var(--accent);
}
.approval-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 10px 12px;
  border-top: 1px solid var(--border);
}
</style>
