<script setup lang="ts">
import { computed, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { copyText } from '../../utils/clipboard'
import { submitFeedback, type FeedbackRating } from '../../api/feedback'
import { useConversationStore } from '../../stores/conversation'
import FeedbackDialog from './FeedbackDialog.vue'

/**
 * 回复下方的操作栏:复制 / 好的回答 / 有问题的回答。
 *
 * 👎 会先弹出反馈窗口(选分类 + 填详情)再提交,👍 直接提交。
 * 同一条消息只保留一条反馈:点过之后图标点亮,再点不重复提交。
 */
const props = defineProps<{
  content: string
  messageId?: string | null
  model?: string | null
}>()

const message = useMessage()
const convStore = useConversationStore()

const rating = ref<FeedbackRating | null>(null)
const feedbackOpen = ref(false)
const busy = ref(false)

const canCopy = computed(() => !!props.content.trim())

function basePayload(r: FeedbackRating) {
  return {
    rating: r,
    conversation_id: convStore.currentId,
    message_id: props.messageId ?? null,
    model: props.model ?? null,
  }
}

async function onCopy() {
  const ok = await copyText(props.content)
  if (ok) message.success('已复制')
  else message.error('复制失败,请手动选择复制')
}

async function onGood() {
  if (busy.value || rating.value === 'good') return
  busy.value = true
  try {
    await submitFeedback(basePayload('good'))
    rating.value = 'good'
    message.success('感谢你的反馈')
  } catch (e) {
    message.error(`提交失败:${(e as Error).message}`)
  } finally {
    busy.value = false
  }
}

function onBad() {
  if (rating.value === 'bad') return
  feedbackOpen.value = true
}
</script>

<template>
  <div class="msg-actions">
    <button type="button" class="action-btn" :disabled="!canCopy" title="复制" @click="onCopy">
      <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round">
        <rect x="5.6" y="5.6" width="8.4" height="8.4" rx="1.6" />
        <path d="M10.4 4.2V3.6A1.6 1.6 0 0 0 8.8 2H3.6A1.6 1.6 0 0 0 2 3.6v5.2a1.6 1.6 0 0 0 1.6 1.6h.6" />
      </svg>
    </button>

    <button
      type="button"
      class="action-btn"
      :class="{ active: rating === 'good' }"
      :disabled="busy"
      title="好的回答"
      @click="onGood"
    >
      <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">
        <path d="M5 7.2 7.6 2.4a1.6 1.6 0 0 1 1.5 1.6v2h3.2a1.2 1.2 0 0 1 1.2 1.4l-.8 4.4a1.2 1.2 0 0 1-1.2 1H5" />
        <rect x="2.2" y="7.2" width="2.8" height="6.6" rx="0.8" />
      </svg>
    </button>

    <button
      type="button"
      class="action-btn"
      :class="{ active: rating === 'bad' }"
      title="有问题的回答"
      @click="onBad"
    >
      <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">
        <path d="M11 8.8 8.4 13.6a1.6 1.6 0 0 1-1.5-1.6v-2H3.7a1.2 1.2 0 0 1-1.2-1.4l.8-4.4a1.2 1.2 0 0 1 1.2-1H11" />
        <rect x="11" y="2.2" width="2.8" height="6.6" rx="0.8" />
      </svg>
    </button>

    <FeedbackDialog
      v-model:show="feedbackOpen"
      :message-id="messageId"
      :conversation-id="convStore.currentId"
      :model="model"
      @submitted="rating = 'bad'"
    />
  </div>
</template>

<style scoped>
.msg-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  margin-top: 6px;
  margin-left: -6px;
}
.action-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.action-btn:hover:not(:disabled) {
  background: var(--bg-hover);
  color: var(--text-secondary);
}
.action-btn:disabled {
  opacity: 0.4;
  cursor: default;
}
.action-btn.active {
  color: var(--accent);
}
</style>
