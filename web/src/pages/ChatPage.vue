<script setup lang="ts">
import { onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import SideBar from '../components/layout/SideBar.vue'
import ChatHeader from '../components/layout/ChatHeader.vue'
import MessageList from '../components/chat/MessageList.vue'
import ChatInput from '../components/chat/ChatInput.vue'
import { useConversationStore } from '../stores/conversation'
import { useModelStore } from '../stores/model'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const convStore = useConversationStore()
const modelStore = useModelStore()

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
  void modelStore.fetchModels()
  void convStore.refreshList()
})
</script>

<template>
  <div class="chat-page">
    <SideBar />
    <main class="main">
      <ChatHeader />
      <div class="message-scroll">
        <MessageList />
      </div>
      <ChatInput :streaming="convStore.streaming" @send="onSend" @stop="convStore.stop" />
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
/* MessageList 自身不设 overflow,包一层带 overflow:auto 的容器,确保滚动条完全在自己区域内 */
.message-scroll {
  flex: 1 1 0;
  min-height: 0;
  overflow: hidden;
  position: relative;
}
</style>