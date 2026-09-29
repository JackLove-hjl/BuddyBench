<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import SideBar from '../components/layout/SideBar.vue'
import ChatHeader from '../components/layout/ChatHeader.vue'
import MessageList from '../components/chat/MessageList.vue'
import ChatInput from '../components/chat/ChatInput.vue'
import SidePanel from '../components/common/SidePanel.vue'
import { useConversationStore } from '../stores/conversation'
import { useModelStore } from '../stores/model'
import { useSidePanel } from '../composables/useSidePanel'
import { clampPanelWidth } from '../utils/splitPane'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const convStore = useConversationStore()
const modelStore = useModelStore()
const { open: panelOpen, fullscreen } = useSidePanel()

/**
 * 对话容器分栏:打开右侧栏时把容器一分为二(默认均分、无遮罩),分隔条可拖动。
 * 全屏时侧栏占满整个容器(消息区隐藏),退出全屏回到原来的宽度。
 * 侧栏的开合状态由 useSidePanel 持久化(下次进来保持上次收起/展开)。
 */
const bodyEl = ref<HTMLElement | null>(null)
/** null = 均分(50%) */
const panelWidth = ref<number | null>(null)
const dragging = ref(false)

watch(panelOpen, (open) => {
  // 收起时复位:下次打开仍是均分(全屏由侧栏自己收起时复位)
  if (!open) panelWidth.value = null
})

const paneStyle = computed(() =>
  fullscreen.value ? { width: '100%' } : { width: panelWidth.value === null ? '50%' : `${panelWidth.value}px` },
)

function onDragMove(e: MouseEvent) {
  const rect = bodyEl.value?.getBoundingClientRect()
  if (!rect || rect.width === 0) return
  panelWidth.value = clampPanelWidth(rect.right - e.clientX, rect.width)
}

function stopDrag() {
  dragging.value = false
  window.removeEventListener('mousemove', onDragMove)
  window.removeEventListener('mouseup', stopDrag)
}

function startDrag(e: MouseEvent) {
  dragging.value = true
  window.addEventListener('mousemove', onDragMove)
  window.addEventListener('mouseup', stopDrag)
  e.preventDefault()
}

onBeforeUnmount(stopDrag)

async function onSend(text: string, attachments?: { url: string; filename: string; kind: 'image' | 'file'; content?: string }[]) {
  const result = await convStore.send(text, attachments)
  if (!result.ok && result.error) {
    message.error(result.error.message)
  }
}

watch(
  () => route.params.id,
  async (id) => {
    if (typeof id === 'string' && id.length > 0) {
      try {
        await convStore.selectConversation(id)
      } catch {
        message.error('会话不存在')
        void router.push('/')
      }
    } else {
      convStore.newConversation()
    }
  },
  { immediate: true },
)

onMounted(() => {
  // 模型列表回来后重算一次上下文占用:圆环分母取模型声明的输入窗口,
  // 列表没到之前它只能停在默认值(刷新页面时这条竞态就表现为"分母不对")
  void modelStore.fetchModels().then(() => convStore.syncContextUsage())
  void convStore.refreshList()
})
</script>

<template>
  <div class="chat-page">
    <SideBar />
    <main class="main">
      <ChatHeader />

      <div ref="bodyEl" class="chat-body" :class="{ dragging }">
        <!-- 消息区 + 输入框 -->
        <div v-if="!fullscreen" class="chat-pane">
          <div class="message-scroll">
            <MessageList />
          </div>
          <ChatInput :streaming="convStore.streaming" @send="onSend" @stop="convStore.stop" />
        </div>

        <!-- 右侧栏(标签页):无遮罩,与消息区并排;分隔条可拖动 -->
        <template v-if="panelOpen">
          <div
            v-if="!fullscreen"
            class="splitter"
            :class="{ active: dragging }"
            title="拖动调节宽度"
            @mousedown="startDrag"
          />
          <SidePanel class="file-pane" :style="paneStyle" />
        </template>
      </div>
    </main>
  </div>
</template>

<style scoped>
.chat-page {
  display: flex;
  flex: 1 1 0;
  min-height: 0;
  height: 100%;
}
.main {
  flex: 1 1 0;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  background: var(--bg);
}
/* 标题栏下的一整块:分栏容器 */
.chat-body {
  flex: 1 1 0;
  min-height: 0;
  display: flex;
  overflow: hidden;
}
.chat-body.dragging {
  user-select: none;
  cursor: col-resize;
}
.chat-pane {
  flex: 1 1 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
/* MessageList 自身不设 overflow,包一层带 overflow:auto 的容器,确保滚动条完全在自己区域内 */
.message-scroll {
  flex: 1 1 0;
  min-height: 0;
  overflow: hidden;
  position: relative;
}
.file-pane {
  flex: 0 0 auto;
}
.splitter {
  flex: 0 0 6px;
  align-self: stretch;
  background: var(--border);
  cursor: col-resize;
  transition: background 0.15s;
}
.splitter:hover,
.splitter.active {
  background: var(--accent);
}
</style>
