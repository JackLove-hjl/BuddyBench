<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { listShells, type ShellOption } from '../../api/terminal'
import { listWorkspaceFiles, type WorkspaceFilesResult } from '../../api/workspaces'
import { useConversationStore } from '../../stores/conversation'
import { useSidePanel } from '../../composables/useSidePanel'

/**
 * 侧栏「开始」页:没有标签页时的入口,也是点「+」新建标签页的默认内容 ——
 * 打开工作区文件 / 打开终端(cmd、PowerShell 各一张卡,只列本机真有的)。
 */
const convStore = useConversationStore()
const { openTerminal, openWorkspaceFile } = useSidePanel()

const mode = ref<'launcher' | 'files'>('launcher')
const loading = ref(false)
const error = ref('')
const result = ref<WorkspaceFilesResult | null>(null)
/** 本机可用的 shell(拉不到列表就不显示终端入口,免得点了打不开) */
const shells = ref<ShellOption[]>([])

onMounted(async () => {
  try {
    const res = await listShells()
    shells.value = res.shells
  } catch {
    shells.value = []
  }
})

async function load(path = '') {
  if (!convStore.currentId) {
    error.value = '当前没有会话'
    result.value = null
    return
  }
  if (!convStore.workspacePath) {
    error.value = '当前会话未设置工作区:请在输入框上方选择工作区后再试'
    result.value = null
    return
  }
  loading.value = true
  error.value = ''
  try {
    result.value = await listWorkspaceFiles(convStore.currentId, path)
  } catch (e) {
    error.value = (e as Error).message
    result.value = null
  } finally {
    loading.value = false
  }
}

function showFiles() {
  mode.value = 'files'
  void load('')
}

function enter(rel: string) {
  void load(rel)
}

function up() {
  void load(result.value?.parent || '')
}

function pick(rel: string) {
  void openWorkspaceFile({
    relPath: rel,
    workspaceRoot: convStore.workspacePath,
    conversationId: convStore.currentId,
  })
}
</script>

<template>
  <div class="panel-home">
    <template v-if="mode === 'launcher'">
      <div class="home-logo">
        <svg viewBox="0 0 24 24" width="34" height="34" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="9" />
          <path d="M15.5 8.5 13.8 13.8 8.5 15.5l1.7-5.3 5.3-1.7z" />
        </svg>
      </div>

      <button type="button" class="home-card" @click="showFiles">
        <span class="card-icon">
          <svg viewBox="0 0 16 16" width="18" height="18" fill="currentColor">
            <path d="M1.5 3.5a1 1 0 0 1 1-1h3l1.5 1.5h6a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1h-10a1 1 0 0 1-1-1v-8.5z" />
          </svg>
        </span>
        <span class="card-text">
          <b>工作区文件</b>
          <i>浏览会话工作区的文件</i>
        </span>
      </button>

      <button v-for="shell in shells" :key="shell.key" type="button" class="home-card" @click="openTerminal(shell.key)">
        <span class="card-icon">
          <svg viewBox="0 0 16 16" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">
            <rect x="1.8" y="2.8" width="12.4" height="10.4" rx="1.6" />
            <path d="M5 6.6 7 8.4l-2 1.8M8.8 10.4h2.4" />
          </svg>
        </span>
        <span class="card-text">
          <b>打开 {{ shell.label }}</b>
          <i>在工作区里开一个真实终端</i>
        </span>
      </button>
    </template>

    <template v-else>
      <div class="browse-head">
        <button type="button" class="browse-btn" :disabled="!result?.parent" @click="up">← 上一级</button>
        <span class="browse-path" :title="result?.current || '工作区根'">{{ result?.current || '工作区根' }}</span>
        <button type="button" class="browse-btn" @click="mode = 'launcher'">返回</button>
      </div>

      <div v-if="loading" class="browse-status">加载中…</div>
      <div v-else-if="error" class="browse-status err">{{ error }}</div>
      <div v-else class="browse-list">
        <div v-for="dir in result?.dirs || []" :key="dir.path" class="browse-item" @click="enter(dir.path)">
          <svg class="item-icon dir" viewBox="0 0 16 16" width="14" height="14" fill="currentColor">
            <path d="M1.5 3.5a1 1 0 0 1 1-1h3l1.5 1.5h6a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1h-10a1 1 0 0 1-1-1v-8.5z" />
          </svg>
          <span class="item-name">{{ dir.name }}</span>
        </div>
        <div v-for="file in result?.files || []" :key="file.path" class="browse-item file" @click="pick(file.path)">
          <svg class="item-icon" viewBox="0 0 16 16" width="14" height="14" fill="currentColor">
            <path d="M3 1.5a1 1 0 0 1 1-1h5l4 4v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-12zM9 1.5v3h3l-3-3z" />
          </svg>
          <span class="item-name">{{ file.name }}</span>
        </div>
        <div v-if="!(result?.dirs?.length || result?.files?.length)" class="browse-status">这个目录是空的</div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.panel-home {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 20px 18px;
  overflow: auto;
}
.home-logo {
  display: flex;
  justify-content: center;
  margin: 26px 0 30px;
  color: var(--border-strong);
}
.home-card {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  max-width: 460px;
  margin: 0 auto 12px;
  padding: 14px 16px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--bg-elevated);
  text-align: left;
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.home-card:hover {
  border-color: var(--accent);
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
}
.card-icon {
  display: inline-flex;
  color: var(--accent);
  flex-shrink: 0;
}
.card-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.card-text b {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text);
}
.card-text i {
  font-size: 12px;
  font-style: normal;
  color: var(--text-tertiary);
}
.browse-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.browse-btn {
  padding: 3px 9px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-elevated);
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
  flex-shrink: 0;
}
.browse-btn:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}
.browse-btn:disabled {
  opacity: 0.45;
  cursor: default;
}
.browse-path {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11.5px;
  color: var(--text-tertiary);
  font-family: 'SFMono-Regular', Consolas, monospace;
}
.browse-status {
  padding: 14px 2px;
  color: var(--text-tertiary);
  font-size: 12.5px;
}
.browse-status.err {
  color: var(--danger);
}
.browse-list {
  padding-top: 6px;
}
.browse-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 8px;
  border-radius: 8px;
  color: var(--text);
  font-size: 13px;
  cursor: pointer;
}
.browse-item:hover {
  background: var(--bg-hover);
}
.item-icon {
  color: var(--text-tertiary);
  flex-shrink: 0;
}
.item-icon.dir {
  color: var(--accent);
}
.item-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
