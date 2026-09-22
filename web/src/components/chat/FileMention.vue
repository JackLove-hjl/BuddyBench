<script setup lang="ts">
import { ref, watch } from 'vue'
import { useMessage } from 'naive-ui'
import {
  listWorkspaceFiles,
  listWorkspaceFilesByPath,
  readWorkspaceFile,
  readWorkspaceFileByPath,
  type WorkspaceFileEntry,
  type WorkspaceFilesResult,
} from '../../api/workspaces'
import { useConversationStore } from '../../stores/conversation'
import type { ChatAttachment } from './ChatInput.vue'

const props = defineProps<{ show: boolean }>()
const emit = defineEmits<{ (e: 'attach', att: ChatAttachment): void; (e: 'close'): void }>()

const message = useMessage()
const convStore = useConversationStore()

const loading = ref(false)
const data = ref<WorkspaceFilesResult | null>(null)
const currentRel = ref('')
const search = ref('')

// 打开时加载工作区根目录
watch(
  () => props.show,
  (v) => {
    if (v) {
      currentRel.value = ''
      search.value = ''
      void load('')
    }
  },
)

async function load(rel: string) {
  const ws = convStore.workspacePath
  if (!ws) return
  loading.value = true
  try {
    // 有会话走会话接口;新对话(未创建会话)用 localStorage 继承的工作区绝对路径
    data.value = convStore.currentId
      ? await listWorkspaceFiles(convStore.currentId, rel)
      : await listWorkspaceFilesByPath(ws, rel)
    currentRel.value = data.value.current
  } catch (e) {
    message.error((e as Error).message)
    emit('close')
  } finally {
    loading.value = false
  }
}

function enterDir(dir: WorkspaceFileEntry) {
  void load(dir.path)
}

function goParent() {
  if (data.value?.parent !== null && data.value?.parent !== undefined) {
    void load(data.value.parent)
  }
}

async function pickFile(f: WorkspaceFileEntry) {
  const ws = convStore.workspacePath
  if (!ws) return
  loading.value = true
  try {
    const { content } = convStore.currentId
      ? await readWorkspaceFile(convStore.currentId, f.path)
      : await readWorkspaceFileByPath(ws, f.path)
    const att: ChatAttachment = {
      url: '',
      filename: f.path,
      kind: 'file',
      content,
    }
    emit('attach', att)
    emit('close')
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

function formatSize(bytes?: number): string {
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`
}

const filteredFiles = () => {
  if (!data.value) return { dirs: [], files: [] }
  const q = search.value.toLowerCase()
  if (!q) return data.value
  return {
    dirs: data.value.dirs.filter((d) => d.name.toLowerCase().includes(q)),
    files: data.value.files.filter((f) => f.name.toLowerCase().includes(q)),
  }
}
</script>

<template>
  <div v-if="show" class="file-mention" @click.stop>
    <div class="mention-head">
      <button v-if="data?.parent !== null" class="mention-nav" title="上一级" @click="goParent">
        <svg viewBox="0 0 16 16" width="12" height="12" fill="currentColor">
          <path d="M4 8l5-5v3h5v4H9v3l-5-5z" />
        </svg>
      </button>
      <input v-model="search" class="mention-search" placeholder="搜索工作区文件…" />
    </div>

    <div class="mention-path">{{ currentRel || data?.root || '工作区根目录' }}</div>

    <div class="mention-list">
      <button v-for="d in filteredFiles().dirs" :key="d.path" class="mention-item dir" @click="enterDir(d)">
        <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" class="mention-icon">
          <path d="M1.5 3.5a1 1 0 0 1 1-1h3l1.5 1.5h6a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1h-10a1 1 0 0 1-1-1v-8.5z" />
        </svg>
        <span class="mention-name">{{ d.name }}</span>
        <span class="mention-kind">目录</span>
      </button>
      <button v-for="f in filteredFiles().files" :key="f.path" class="mention-item" @click="pickFile(f)">
        <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" class="mention-icon">
          <path d="M3 1.5a1 1 0 0 1 1-1h5l4 4v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-12zM9 1.5v3h3l-3-3z" />
        </svg>
        <span class="mention-name">{{ f.name }}</span>
        <span class="mention-size">{{ formatSize(f.size) }}</span>
      </button>
      <div v-if="!loading && !filteredFiles().dirs.length && !filteredFiles().files.length" class="mention-empty">
        {{ search ? '无匹配文件' : '工作区为空' }}
      </div>
      <div v-if="loading" class="mention-loading">加载中…</div>
    </div>

    <div class="mention-hint">点击文件引用到对话(内容将注入)</div>
  </div>
</template>

<style scoped>
.file-mention {
  position: absolute;
  bottom: calc(100% + 8px);
  left: 0;
  width: 360px;
  max-width: 100%;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: var(--shadow);
  padding: 6px;
  z-index: 50;
}
.mention-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}
.mention-nav {
  border: none;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  border-radius: 6px;
  padding: 3px 6px;
  font-family: inherit;
  flex-shrink: 0;
}
.mention-nav:hover {
  background: var(--bg-hover);
  color: var(--text);
}
.mention-search {
  flex: 1;
  border: 1px solid var(--border);
  background: var(--bg-hover);
  color: var(--text);
  border-radius: 6px;
  padding: 4px 8px;
  font-size: 12px;
  font-family: inherit;
  outline: none;
}
.mention-search:focus {
  border-color: var(--accent);
}
.mention-path {
  font-size: 11px;
  color: var(--text-tertiary);
  font-family: 'SFMono-Regular', Consolas, monospace;
  padding: 2px 4px 6px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.mention-list {
  max-height: 260px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.mention-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  border: none;
  background: transparent;
  color: var(--text);
  padding: 6px 8px;
  border-radius: 6px;
  cursor: pointer;
  font-family: inherit;
  font-size: 13px;
  text-align: left;
  transition: background 0.12s;
}
.mention-item:hover {
  background: var(--bg-hover);
}
.mention-icon {
  flex-shrink: 0;
  color: var(--text-tertiary);
}
.mention-item.dir .mention-icon {
  color: #f5a623;
}
.mention-name {
  flex: 1;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.mention-kind,
.mention-size {
  font-size: 11px;
  color: var(--text-tertiary);
  flex-shrink: 0;
}
.mention-empty,
.mention-loading {
  text-align: center;
  color: var(--text-tertiary);
  font-size: 12px;
  padding: 16px 0;
}
.mention-hint {
  font-size: 11px;
  color: var(--text-tertiary);
  padding: 6px 4px 2px;
  border-top: 1px solid var(--border);
  margin-top: 4px;
}
</style>
