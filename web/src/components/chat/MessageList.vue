<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useConversationStore } from '../../stores/conversation'
import { useAutoScroll } from '../../composables/useAutoScroll'
import UserMessage from './UserMessage.vue'
import AssistantMessage from './AssistantMessage.vue'
import ScrollToBottom from './ScrollToBottom.vue'
import BrandLogo from '../common/BrandLogo.vue'

const convStore = useConversationStore()
const container = ref<HTMLElement | null>(null)
const { showScrollBtn, scrollToBottom } = useAutoScroll(() => container.value)

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
watch(
  () => convStore.messages.length,
  () => void scrollToBottom(true),
)

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
      <div class="message-container">
        <template v-for="m in convStore.messages" :key="m.id">
          <UserMessage v-if="m.role === 'user'" :content="m.content" :attachments="m.meta.attachments" />
          <AssistantMessage v-else :message="m" @retry="onRetry" />
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
  max-width: 760px;
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