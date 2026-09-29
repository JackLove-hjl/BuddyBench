<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useConversationStore } from '../../stores/conversation'
import { useAutoScroll } from '../../composables/useAutoScroll'
import UserMessage from './UserMessage.vue'
import AssistantMessage from './AssistantMessage.vue'
import ScrollToBottom from './ScrollToBottom.vue'
import BrandLogo from '../common/BrandLogo.vue'

const convStore = useConversationStore()
const container = ref<HTMLElement | null>(null)
const content = ref<HTMLElement | null>(null)
const { showScrollBtn, scrollToBottom, jumpToBottom } = useAutoScroll(() => container.value)

// 切换会话:currentId 一变就贴底(此时还是 loading 态,真正的贴底靠下面的 loading 下落)
watch(
  () => convStore.currentId,
  () => void jumpToBottom(),
)
// 加载完成(消息已渲染):再贴一次,这次才是有内容的底
watch(
  () => convStore.loading,
  (loading) => {
    if (!loading) void jumpToBottom()
  },
)

watch(
  () => convStore.pending?.content,
  () => void scrollToBottom(),
)
watch(
  () => convStore.pending?.reasoning,
  () => void scrollToBottom(),
)
watch(
  () => convStore.pending?.toolCalls.length,
  () => void scrollToBottom(),
)
watch(
  () => convStore.pending?.images.length,
  () => void scrollToBottom(),
)
// 本会话新增消息(用户刚发出 / 助手回复固化):平滑滚到底,让新内容自然进入视野
watch(
  () => convStore.messages.length,
  () => void scrollToBottom(true),
)

/**
 * 内容高度变化时继续贴住底部。
 *
 * 「切换会话已到底」会被随后才加载完成的图片顶上去 —— 图片没有内在尺寸,加载完才撑开高度,
 * 而此时滚动早已执行完毕。用 ResizeObserver 盯住内容高度:只要用户仍然停在底部
 * (stickToBottom,滚动时会自动更新)就跟到底;用户主动往上翻后就不再打扰。
 */
const resizeObserver = new ResizeObserver(() => {
  if (!convStore.loading) void scrollToBottom()
})
watch(content, (el, prev) => {
  if (prev) resizeObserver.unobserve(prev)
  if (el) resizeObserver.observe(el)
})
onBeforeUnmount(() => resizeObserver.disconnect())

// 切换会话时 messages 会先被清空再加载,这里把 loading 一并纳入判断,
// 否则加载期间会闪出"欢迎"空态
const showWelcome = computed(
  () => !convStore.loading && !convStore.messages.length && !convStore.pending,
)

function onRetry() {
  void convStore.retry()
}
</script>

<template>
  <div ref="container" class="message-list">
    <!-- 欢迎空态 -->
    <div v-if="showWelcome" class="welcome">
      <div class="welcome-logo">
        <BrandLogo :size="48" />
      </div>
      <h2 class="welcome-title">我是 BuddyBench,很高兴见到你!</h2>
      <div class="welcome-sub">标准模式 · Agent 智能体 · 支持文件读写 / Shell 命令 / 代码绘图</div>
      <div class="welcome-workspace">
        <span v-if="convStore.workspacePath" class="welcome-ws-tag">
          <svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor">
            <path d="M1.5 3.5a1 1 0 0 1 1-1h3l1.5 1.5h6a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1h-10a1 1 0 0 1-1-1v-8.5z" />
          </svg>
          工作区:{{ convStore.workspacePath }}
        </span>
        <span v-else class="welcome-ws-tag muted">工作区在输入框左上角选择,可让 Agent 读写本地文件</span>
      </div>
    </div>

    <!-- 会话消息加载中 -->
    <div v-else-if="convStore.loading" class="loading">
      <svg viewBox="0 0 16 16" width="22" height="22" fill="currentColor" class="loading-spin">
        <path d="M8 2a6 6 0 1 0 6 6h-1.5A4.5 4.5 0 1 1 8 3.5V2z" />
      </svg>
    </div>

    <template v-else>
      <div ref="content" class="message-container">
        <template v-for="(m, i) in convStore.messages" :key="m.id">
          <UserMessage v-if="m.role === 'user'" :content="m.content" :attachments="m.meta.attachments" />
          <AssistantMessage
            v-else
            :message="m"
            :is-last="i === convStore.messages.length - 1"
            @retry="onRetry"
          />
        </template>
        <AssistantMessage v-if="convStore.pending" :pending="convStore.pending" @retry="onRetry" />
      </div>
    </template>

    <ScrollToBottom :show="showScrollBtn" @click="() => scrollToBottom(true)" />
  </div>
</template>

<style scoped>
.message-list {
  height: 100%;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 24px 20px 8px;
  position: relative;
  box-sizing: border-box;
}
.message-container {
  /* 与输入框同宽:宽屏下两侧留白小一些(原先 760px 在 2K 屏上两侧各空掉一大块) */
  max-width: 1100px;
  margin: 0 auto;
}
.welcome {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  text-align: center;
}
.welcome-logo {
  color: var(--accent);
  margin-bottom: 8px;
}
.welcome-title {
  margin: 0;
  font-size: 22px;
  font-weight: 600;
  color: var(--text);
}
.welcome-sub {
  font-size: 14px;
  color: var(--text-tertiary);
}
.welcome-workspace {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 16px;
}
.welcome-ws-tag {
  font-size: 12px;
  color: var(--success);
}
.welcome-ws-tag.muted {
  color: var(--text-tertiary);
}
.loading {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}
.loading-spin {
  color: var(--text-tertiary);
  animation: loading-spin 0.9s linear infinite;
}
@keyframes loading-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>