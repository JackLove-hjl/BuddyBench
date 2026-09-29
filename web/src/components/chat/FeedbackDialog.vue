<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { useMessage } from 'naive-ui'
import { submitFeedback, type FeedbackRating } from '../../api/feedback'

/**
 * 👎 之后弹出的反馈窗口:选分类(可多选)+ 填详情,提交给后端落库。
 *
 * 提交时会把当前对话/消息的标识一起带上,方便按消息回查上下文。
 */
const CATEGORIES = [
  '任务结果',
  '指令理解与遵循',
  '产品功能与交互',
  '稳定性和速度',
  '资源使用与费用',
  '安全隐私与权限',
  '其他',
]

const props = defineProps<{
  show: boolean
  messageId?: string | null
  conversationId?: string | null
  model?: string | null
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'submitted', rating: FeedbackRating): void
}>()

const message = useMessage()
const selected = ref<string[]>([])
const detail = ref('')
const submitting = ref(false)

// 每次打开都清空上一次的内容,避免误提交旧反馈
watch(
  () => props.show,
  (visible) => {
    if (visible) {
      selected.value = []
      detail.value = ''
    }
  },
)

function toggle(name: string) {
  const index = selected.value.indexOf(name)
  if (index >= 0) selected.value.splice(index, 1)
  else selected.value.push(name)
}

function close() {
  if (!submitting.value) emit('update:show', false)
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

async function submit() {
  if (submitting.value) return
  submitting.value = true
  try {
    await submitFeedback({
      rating: 'bad',
      conversation_id: props.conversationId ?? null,
      message_id: props.messageId ?? null,
      categories: selected.value,
      detail: detail.value.trim(),
      model: props.model ?? null,
    })
    emit('submitted', 'bad')
    emit('update:show', false)
    message.success('反馈已提交,感谢你的反馈')
  } catch (e) {
    message.error(`提交失败:${(e as Error).message}`)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <Teleport to="body">
    <div v-if="show" class="feedback-mask" @click="close">
      <div class="feedback-panel" role="dialog" aria-modal="true" @click.stop>
        <div class="feedback-head">
          <span class="feedback-title">提交反馈</span>
          <button type="button" class="feedback-close" title="关闭" @click="close">×</button>
        </div>

        <div class="feedback-cats">
          <button
            v-for="name in CATEGORIES"
            :key="name"
            type="button"
            class="feedback-cat"
            :class="{ active: selected.includes(name) }"
            @click="toggle(name)"
          >
            {{ name }}
          </button>
        </div>

        <textarea
          v-model="detail"
          class="feedback-input"
          rows="6"
          maxlength="5000"
          placeholder="填写详情以帮助我们改进体验,提交内容会包括当前对话的日志"
        />

        <button type="button" class="feedback-submit" :disabled="submitting" @click="submit">
          {{ submitting ? '提交中…' : '提交' }}
        </button>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.feedback-mask {
  position: fixed;
  inset: 0;
  z-index: 2100;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  box-sizing: border-box;
  background: rgba(0, 0, 0, 0.45);
}
.feedback-panel {
  width: 100%;
  max-width: 600px;
  max-height: 90vh;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 18px 18px 16px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--bg-elevated);
  box-shadow: var(--shadow);
}
.feedback-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.feedback-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text);
}
.feedback-close {
  border: none;
  background: transparent;
  color: var(--text-tertiary);
  font-size: 20px;
  line-height: 1;
  width: 28px;
  height: 28px;
  border-radius: 6px;
  cursor: pointer;
}
.feedback-close:hover {
  background: var(--bg-hover);
  color: var(--text);
}
.feedback-cats {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.feedback-cat {
  padding: 6px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-elevated);
  color: var(--text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s, background 0.15s;
}
.feedback-cat:hover {
  border-color: var(--border-strong);
  color: var(--text);
}
.feedback-cat.active {
  border-color: var(--accent);
  color: var(--accent);
  background: var(--accent-soft);
}
.feedback-input {
  width: 100%;
  box-sizing: border-box;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--bg);
  color: var(--text);
  font-size: 13px;
  line-height: 1.6;
  resize: vertical;
  min-height: 120px;
  font-family: inherit;
}
.feedback-input:focus {
  outline: none;
  border-color: var(--accent);
}
.feedback-submit {
  margin-top: 4px;
  padding: 11px 16px;
  border: none;
  border-radius: 10px;
  background: #1f2329;
  color: #fff;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: opacity 0.15s;
}
.feedback-submit:hover {
  opacity: 0.9;
}
.feedback-submit:disabled {
  opacity: 0.6;
  cursor: default;
}
</style>
