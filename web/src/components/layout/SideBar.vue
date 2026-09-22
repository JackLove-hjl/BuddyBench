<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, useMessage } from 'naive-ui'
import { useConversationStore } from '../../stores/conversation'
import BrandLogo from '../common/BrandLogo.vue'
import ConversationItem from '../conversation/ConversationItem.vue'
import UserMenu from './UserMenu.vue'

const convStore = useConversationStore()
const router = useRouter()
const message = useMessage()

/** 按工作区对会话分组:null/空 → "默认工作区";否则按绝对路径分组,名称取最后一段 */
interface ConvGroup {
  key: string
  label: string
  workspace_path: string | null
  items: typeof convStore.list
}

function workspaceLabel(path: string | null): { label: string; key: string } {
  if (!path) return { label: '默认工作区', key: '__default__' }
  const parts = path.replace(/[\\/]+$/, '').split(/[\\/]/)
  return { label: parts[parts.length - 1] || path, key: path }
}

const groups = computed<ConvGroup[]>(() => {
  const map = new Map<string, ConvGroup>()
  for (const c of convStore.list) {
    const { label, key } = workspaceLabel(c.workspace_path)
    if (!map.has(key)) {
      map.set(key, { key, label, workspace_path: c.workspace_path, items: [] })
    }
    map.get(key)!.items.push(c)
  }
  // 默认工作区排最前,其余按标签排序
  return Array.from(map.values()).sort((a, b) => {
    if (a.key === '__default__') return -1
    if (b.key === '__default__') return 1
    return a.label.localeCompare(b.label)
  })
})

function onSelect(id: string) {
  if (convStore.streaming) return
  if (router.currentRoute.value.path === `/chat/${id}`) {
    if (convStore.currentId !== id) void convStore.selectConversation(id)
    return
  }
  void router.push(`/chat/${id}`)
}

/** 会话项「⋯」菜单的三个动作:失败时给出提示(store 内部已做本地回滚) */
async function onRename(id: string, title: string) {
  try {
    await convStore.rename(id, title)
  } catch (e) {
    message.error((e as Error).message || '重命名失败')
  }
}

async function onPin(id: string, pinned: boolean) {
  try {
    await convStore.setPinned(id, pinned)
  } catch (e) {
    message.error((e as Error).message || '操作失败')
  }
}

async function onRemove(id: string) {
  try {
    await convStore.remove(id)
  } catch (e) {
    message.error((e as Error).message || '删除失败')
  }
}
</script>

<template>
  <aside class="sidebar">
    <div class="sidebar-top">
      <div class="brand">
        <span class="brand-logo">
          <BrandLogo :size="22" />
        </span>
        <span class="brand-name">BuddyBench</span>
      </div>
      <n-button class="new-btn" type="primary" block size="medium" @click="convStore.newConversation()">
        <template #icon>
          <svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor">
            <path d="M8 2a.5.5 0 0 1 .5.5v5h5a.5.5 0 0 1 0 1h-5v5a.5.5 0 0 1-1 0v-5h-5a.5.5 0 0 1 0-1h5v-5A.5.5 0 0 1 8 2z" />
          </svg>
        </template>
        开启新对话
      </n-button>
    </div>

    <div class="sidebar-list">
      <template v-for="g in groups" :key="g.key">
        <div class="conv-group-label" :title="g.workspace_path || undefined">
          <svg viewBox="0 0 16 16" width="12" height="12" fill="currentColor">
            <path d="M1.5 3.5a1 1 0 0 1 1-1h3l1.5 1.5h6a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1h-10a1 1 0 0 1-1-1v-8.5z" />
          </svg>
          {{ g.label }}
          <span class="conv-group-count">{{ g.items.length }}</span>
        </div>
        <ConversationItem
          v-for="c in g.items"
          :key="c.id"
          :conversation="c"
          :active="c.id === convStore.currentId"
          @select="onSelect"
          @rename="onRename"
          @pin="onPin"
          @remove="onRemove"
        />
      </template>
      <div v-if="!convStore.list.length" class="sidebar-empty">暂无会话</div>
    </div>

    <div class="sidebar-bottom">
      <UserMenu />
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  display: flex;
  flex-direction: column;
  width: 260px;
  flex-shrink: 0;
  height: 100%;
  min-height: 0;
  background: var(--bg-sidebar);
  border-right: 1px solid var(--border);
  padding: 12px;
  gap: 8px;
}
.sidebar-top {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-bottom: 4px;
}
.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 2px 4px;
}
.brand-logo {
  color: var(--accent);
  display: flex;
  align-items: center;
}
.brand-name {
  font-size: 17px;
  font-weight: 700;
  color: var(--text);
  letter-spacing: 0.3px;
}
.new-btn {
  font-weight: 500;
}
.sidebar-list {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-top: 4px;
}
.conv-group-label {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 8px 4px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-tertiary);
  text-transform: none;
}
.conv-group-label svg {
  flex-shrink: 0;
}
.conv-group-count {
  margin-left: auto;
  font-size: 11px;
  background: var(--bg-hover);
  border-radius: 8px;
  padding: 0 6px;
  line-height: 16px;
  font-weight: 500;
}
.sidebar-empty {
  text-align: center;
  color: var(--text-tertiary);
  font-size: 13px;
  padding: 24px 0;
}
.sidebar-bottom {
  display: flex;
  align-items: center;
  padding-top: 8px;
  border-top: 1px solid var(--border);
}
</style>
