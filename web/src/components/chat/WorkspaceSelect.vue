<script setup lang="ts">
import { ref } from 'vue'
import { NButton, NInput, NModal, NSpin, useMessage } from 'naive-ui'
import { useConversationStore } from '../../stores/conversation'
import { browseDirectories, type BrowseResult, type DirEntry } from '../../api/workspaces'

const convStore = useConversationStore()
const message = useMessage()

const dialogOpen = ref(false)
const loading = ref(false)
const saving = ref(false)
const mode = ref<'browse' | 'manual'>('browse')
const browse = ref<BrowseResult>({ current: null, parent: null, children: [] })
const manualPath = ref('')

function open() {
  dialogOpen.value = true
  mode.value = 'browse'
  manualPath.value = convStore.workspacePath || ''
  void load(null)
}

async function load(path: string | null) {
  loading.value = true
  try {
    browse.value = await browseDirectories(path || '')
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

function enterDir(dir: DirEntry) {
  void load(dir.path)
}

function goRoot(root: string) {
  void load(root)
}

function goParent() {
  if (browse.value.parent) void load(browse.value.parent)
}

async function selectCurrent() {
  if (!browse.value.current) return
  saving.value = true
  try {
    await convStore.setWorkspacePath(browse.value.current)
    message.success('工作区已设置')
    dialogOpen.value = false
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    saving.value = false
  }
}

async function saveManual() {
  const p = manualPath.value.trim()
  if (!p) {
    message.warning('请输入工作区目录的绝对路径')
    return
  }
  saving.value = true
  try {
    await convStore.setWorkspacePath(p)
    message.success('工作区已设置')
    dialogOpen.value = false
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    saving.value = false
  }
}

async function clear() {
  try {
    await convStore.setWorkspacePath(null)
    message.success('已清除工作区')
    dialogOpen.value = false
  } catch (e) {
    message.error((e as Error).message)
  }
}
</script>

<template>
  <div class="workspace-select">
    <button
      class="ws-btn"
      :class="{ active: !!convStore.workspacePath }"
      :title="convStore.workspacePath || '选择工作区(Agent 文件操作的根目录)'"
      @click="open"
    >
      <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" class="ws-btn-icon">
        <path d="M1.5 3.5a1 1 0 0 1 1-1h3l1.5 1.5h6a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1h-10a1 1 0 0 1-1-1v-8.5z" />
      </svg>
      <span class="ws-label" :title="convStore.workspacePath || undefined">
        {{ convStore.workspacePath ? convStore.workspacePath : '选择工作区' }}
      </span>
    </button>

    <n-modal v-model:show="dialogOpen" preset="card" title="选择工作区" style="width: 560px" :bordered="false">
      <div class="ws-tabs">
        <button class="ws-tab" :class="{ active: mode === 'browse' }" @click="mode = 'browse'">浏览目录</button>
        <button class="ws-tab" :class="{ active: mode === 'manual' }" @click="mode = 'manual'">手动输入</button>
      </div>

      <!-- 浏览模式 -->
      <div v-show="mode === 'browse'" class="ws-body">
        <div class="ws-pathbar">
          <button v-if="browse.parent" class="ws-nav" title="上一级" @click="goParent">
            <svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor">
              <path d="M4 8l5-5v3h5v4H9v3l-5-5z" />
            </svg>
          </button>
          <span class="ws-current" :title="browse.current || ''">{{ browse.current || '选择盘符 / 根目录' }}</span>
          <button v-if="convStore.workspacePath" class="ws-clear" title="清除工作区" @click="clear">清除</button>
        </div>

        <n-spin :show="loading">
          <div class="ws-tree">
            <template v-if="browse.roots && browse.roots.length">
              <div class="ws-roots-title">选择磁盘:</div>
              <button v-for="r in browse.roots" :key="r" class="ws-root" @click="goRoot(r)">
                <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor">
                  <path d="M1.5 3.5a1 1 0 0 1 1-1h3l1.5 1.5h6a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1h-10a a1 1 0 0 1-1-1v-8.5z" />
                </svg>
                {{ r }}
              </button>
            </template>
            <template v-else>
              <button
                v-for="d in browse.children"
                :key="d.path"
                class="ws-dir"
                @click="enterDir(d)"
              >
                <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" class="ws-dir-icon">
                  <path d="M1.5 3.5a1 1 0 0 1 1-1h3l1.5 1.5h6a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1h-10a a1 1 0 0 1-1-1v-8.5z" />
                </svg>
                {{ d.name }}
              </button>
              <div v-if="!browse.children.length" class="ws-empty">该目录下没有子目录</div>
            </template>
          </div>
        </n-spin>

        <div class="ws-actions">
          <span class="ws-hint">点击目录进入双,确认后作为 Agent 工作区</span>
          <n-button size="small" :disabled="!browse.current" :loading="saving" type="primary" @click="selectCurrent">
            选择此目录
          </n-button>
        </div>
      </div>

      <!-- 手动输入模式 -->
      <div v-show="mode === 'manual'" class="ws-body">
        <p class="ws-tip">输入工作区目录的绝对路径,如 <code>D:\study\llm</code></p>
        <n-input v-model:value="manualPath" placeholder="D:\path\to\folder" />
        <div class="ws-actions">
          <span class="ws-hint">后端会校验目录存在性</span>
          <n-button size="small" :loading="saving" type="primary" @click="saveManual">确认</n-button>
        </div>
      </div>
    </n-modal>
  </div>
</template>

<style scoped>
.workspace-select {
  display: flex;
  align-items: center;
}
/* 与工具栏其它按钮(权限/模型)同款胶囊样式 */
.ws-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  border: none;
  background: var(--bg-hover);
  color: var(--text);
  border-radius: 8px;
  padding: 5px 10px;
  cursor: pointer;
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  white-space: nowrap;
  transition: background 0.15s, color 0.15s;
  max-width: 240px;
}
.ws-btn:hover {
  background: var(--bg-active);
}
.ws-btn.active {
  background: var(--accent-soft);
  color: var(--accent);
}
.ws-btn-icon {
  flex-shrink: 0;
  color: var(--text-secondary);
}
.ws-btn.active .ws-btn-icon {
  color: var(--accent);
}
.ws-label {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  /* 行高取 16px 与权限触发器的 16px 图标等高,三个按钮实际高度一致
     (同为 padding 10 + 内容 16 = 26px);用 line-height:1 会矮 2px */
  line-height: 16px;
}
.ws-tabs {
  display: flex;
  border-bottom: 1px solid var(--border);
  margin-bottom: 12px;
}
.ws-tab {
  flex: 1;
  border: none;
  background: transparent;
  padding: 8px 0;
  font-size: 13px;
  color: var(--text-secondary);
  cursor: pointer;
  font-family: inherit;
  border-bottom: 2px solid transparent;
  transition: color 0.15s, border-color 0.15s;
}
.ws-tab:hover {
  color: var(--text);
}
.ws-tab.active {
  color: var(--accent);
  border-bottom-color: var(--accent);
  font-weight: 600;
}
.ws-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.ws-tip {
  margin: 0;
  font-size: 12px;
  color: var(--text-secondary);
}
.ws-tip code {
  background: var(--bg-code);
  padding: 1px 5px;
  border-radius: 4px;
  font-family: monospace;
}
.ws-pathbar {
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--bg-hover);
  border-radius: 8px;
  padding: 6px 8px;
}
.ws-nav,
.ws-clear {
  border: none;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  border-radius: 6px;
  padding: 2px 6px;
  font-size: 12px;
  font-family: inherit;
}
.ws-nav:hover,
.ws-clear:hover {
  background: var(--bg-active);
  color: var(--text);
}
.ws-current {
  flex: 1;
  font-size: 12px;
  font-family: 'SFMono-Regular', Consolas, monospace;
  color: var(--text);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.ws-tree {
  min-height: 240px;
  max-height: 340px;
  overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 6px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.ws-roots-title {
  font-size: 12px;
  color: var(--text-tertiary);
  padding: 4px 8px;
}
.ws-root,
.ws-dir {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  border: none;
  background: transparent;
  color: var(--text);
  padding: 7px 8px;
  border-radius: 6px;
  cursor: pointer;
  font-family: inherit;
  font-size: 13px;
  text-align: left;
  transition: background 0.12s;
}
.ws-root:hover,
.ws-dir:hover {
  background: var(--bg-hover);
}
.ws-dir-icon {
  color: #f5a623;
  flex-shrink: 0;
}
.ws-root svg {
  color: var(--accent);
  flex-shrink: 0;
}
.ws-empty {
  text-align: center;
  color: var(--text-tertiary);
  font-size: 12px;
  padding: 24px 0;
}
.ws-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.ws-hint {
  font-size: 12px;
  color: var(--text-tertiary);
}
</style>