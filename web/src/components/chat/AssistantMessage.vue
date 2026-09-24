<script setup lang="ts">
import { computed } from 'vue'
import ReasoningBlock from './ReasoningBlock.vue'
import MarkdownContent from './MarkdownContent.vue'
import ToolCard from './ToolCard.vue'
import CompactNote from './CompactNote.vue'
import ApprovalCard from './ApprovalCard.vue'
import QuestionCard from './QuestionCard.vue'
import ErrorBanner from './ErrorBanner.vue'
import { buildRenderBlocks, type RenderBlock } from './renderBlocks'
import type { ApprovalAction, ChatMessage, PendingMessage, PendingSegment, ToolCall } from '../../types'

const props = withDefaults(
  defineProps<{
    message?: ChatMessage
    pending?: PendingMessage
    /**
     * 是否为会话里的最后一条消息。
     *
     * 挂起态(待审批 / 待回答)只在末尾才有意义:用户答复后本轮会继续跑并落一条新消息,
     * 而旧消息上的 meta.pending_approval 仍然留在库里 —— 不判断这一项,刷新后
     * 已经回答过的问题卡 / 审批卡会重新冒出来,点它还会拿一份过期的动作集去恢复中断。
     */
    isLast?: boolean
  }>(),
  { isLast: false },
)

const emit = defineEmits<{ (e: 'retry'): void }>()

const isStreaming = computed(() => !!props.pending)
const content = computed(() => props.pending?.content ?? props.message?.content ?? '')
const reasoning = computed(() => props.pending?.reasoning ?? props.message?.meta.reasoning ?? '')
const toolCalls = computed(() => {
  if (props.pending?.toolCalls) return props.pending.toolCalls
  const meta = props.message?.meta
  const recs = meta?.tool_records
  if (recs && recs.length) return recs
  // 兼容旧数据:tool_calls 可能是 ToolCall 对象数组(新)或工具名称字符串列表(旧),后者无法渲染工具卡
  const calls = meta?.tool_calls
  return Array.isArray(calls) && calls.length && typeof calls[0] === 'object' ? (calls as ToolCall[]) : []
})

// 按真实执行顺序渲染:流式用 pending.segments,历史回放用 meta.segments,
// 旧数据(无时间线)退化为「思考整体在前 + 工具卡随后」
const segments = computed<PendingSegment[]>(() => {
  if (props.pending?.segments?.length) return props.pending.segments
  const saved = props.message?.meta.segments
  if (saved && saved.length) return saved
  const out: PendingSegment[] = []
  if (reasoning.value.trim()) out.push({ kind: 'reasoning', content: reasoning.value })
  for (const tc of toolCalls.value) out.push({ kind: 'tool', call: tc })
  return out
})
const error = computed(() => props.pending?.error ?? props.message?.meta.error)
const interrupted = computed(() => props.pending?.status === 'interrupted')

/** 待审批动作:优先取流式状态,其次取落库的挂起标记(刷新后仍可批准/拒绝) */
const approvalActions = computed<ApprovalAction[]>(() => {
  if (props.pending) return props.pending.approval ?? []
  // 只有末尾消息的挂起标记才仍然有效(见 isLast 的说明)
  if (!props.isLast) return []
  return props.message?.meta.pending_approval?.actions ?? []
})

/** ask_user 挂起走需求澄清卡(选项 + 自定义输入),其余挂起走审批卡 */
const askUserActions = computed(() => approvalActions.value.filter((a) => a.name === 'ask_user'))

/**
 * 优先用原始事件流渲染:事件流里含正文 delta,因此能精确还原
 * 「正文 → 思考 → 工具 → 正文」这种真正交错的过程;
 * 没有事件流(旧数据或纯文本回答)时回退到 segments + content 的老路径。
 *
 * 合并规则(含"参数流卡片必须从 segments 补回")见 renderBlocks.ts。
 */
const blocks = computed<RenderBlock[] | null>(() =>
  buildRenderBlocks(
    props.pending?.events?.length ? props.pending.events : props.message?.meta.events,
    segments.value,
  ),
)

const showThinkingHint = computed(
  () => isStreaming.value && !content.value && !reasoning.value && !error.value && !approvalActions.value.length,
)

// 思考段是否仍"进行中":只有流式时、正文尚未开始输出、且该段是最后一个思考段才算活跃;
// 一旦开始输出正文或后续出现工具卡/新思考段,前序思考段立即折叠,不必等整个回答结束
function segmentStreaming(seg: PendingSegment, index: number): boolean {
  if (!isStreaming.value || seg.kind !== 'reasoning') return false
  if (props.pending && props.pending.content) return false
  return index === segments.value.length - 1
}

/** 事件流路径下的同一判断:只有末尾的思考块才处于活跃态 */
function blockStreaming(block: RenderBlock, index: number): boolean {
  if (!isStreaming.value || block.kind !== 'reasoning') return false
  const list = blocks.value
  return !!list && index === list.length - 1
}
</script>

<template>
  <div class="assistant-msg">
    <div class="avatar">
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 3l1.9 1.9a3.5 3.5 0 0 0 2.1 1.1l2.6.4-1.8 1.9a3.5 3.5 0 0 0-.9 2.2v2.6l-2.6-.4a3.5 3.5 0 0 0-2.6 1L12 17.5l-2.1-1.9a3.5 3.5 0 0 0-2.6-1l-2.6.4v-2.6a3.5 3.5 0 0 0-.9-2.2l-1.8-1.9 2.6-.4a3.5 3.5 0 0 0 2.1-1.1L12 3z" transform="rotate(0 12 12)" />
        <circle cx="12" cy="12" r="3" />
      </svg>
    </div>
    <div class="body">
      <!-- 事件流路径:正文/思考/工具按真实顺序交错 -->
      <template v-if="blocks">
        <template v-for="(block, i) in blocks" :key="`${block.kind}-${i}`">
          <ReasoningBlock
            v-if="block.kind === 'reasoning'"
            :content="block.text || ''"
            :streaming="blockStreaming(block, i)"
          />
          <ToolCard v-else-if="block.kind === 'tool' && block.call" :call="block.call" />
          <CompactNote v-else-if="block.kind === 'compact'" :summary="block.summary || ''" />
          <MarkdownContent v-else :content="block.text || ''" />
        </template>
      </template>

      <!-- 回退路径:segments(思考/工具/压缩)+ 整体正文 -->
      <template v-else>
        <template v-for="(seg, i) in segments" :key="`${seg.kind}-${i}`">
          <ReasoningBlock v-if="seg.kind === 'reasoning'" :content="seg.content" :streaming="segmentStreaming(seg, i)" />
          <ToolCard v-else-if="seg.kind === 'tool'" :call="seg.call" />
          <CompactNote v-else-if="seg.kind === 'compact'" :summary="seg.summary" />
        </template>
        <MarkdownContent v-if="content" :content="content" />
      </template>

      <QuestionCard v-if="askUserActions.length" :actions="askUserActions" />
      <ApprovalCard v-else-if="approvalActions.length" :actions="approvalActions" />
      <div v-if="showThinkingHint" class="thinking-hint">正在思考…</div>
      <ErrorBanner
        v-if="error"
        :message="error"
        :interrupted="interrupted"
        @retry="emit('retry')"
      />
    </div>
  </div>
</template>

<style scoped>
.assistant-msg {
  display: flex;
  gap: 12px;
  padding: 6px 0;
}
.avatar {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: linear-gradient(135deg, var(--accent), #8b5cf6);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
}
.body {
  flex: 1;
  min-width: 0;
  padding-top: 4px;
}
.tool-cards {
  display: flex;
  flex-direction: column;
}
.thinking-hint {
  color: var(--text-tertiary);
  font-size: 13px;
  animation: pulse 1.2s ease-in-out infinite;
}
@keyframes pulse {
  0%,
  100% {
    opacity: 0.45;
  }
  50% {
    opacity: 1;
  }
}
</style>
