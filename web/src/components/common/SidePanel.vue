<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useSidePanel } from '../../composables/useSidePanel'
import { COPY_LABEL, COPIED_LABEL, COPY_FAILED_LABEL, copyText } from '../../utils/clipboard'
import { highlightCode, langFromPath } from '../../utils/highlight'
import PanelHome from './PanelHome.vue'
import PanelTerminal from './PanelTerminal.vue'

/**
 * 右侧栏:标签页形式,可同时打开多个文件 / 开始页 / 终端。
 *
 * - 没有任何标签页时直接显示开始页(不占标签);点页签行末尾的「+」新增一个标签页,
 *   内容同样是开始页 —— 由它作为入口再去开工作区文件或终端;
 * - 页签行最右侧是全屏/退出全屏与收起侧栏。
 * - 布局由父级(ChatPage)负责:无遮罩、默认均分、分隔条可拖动。
 */
const { tabs, activeId, fullscreen, setActive, closeTab, closePanel, setFullscreen, openLauncher } = useSidePanel()

const active = computed(() => tabs.value.find((t) => t.id === activeId.value) || tabs.value[0] || null)
const fileTab = computed(() => (active.value?.kind === 'file' ? active.value : null))
/** 没有标签页 = 显示开始页 */
const showLauncher = computed(() => !active.value || active.value.kind === 'home')

/** 超过这个体积就不跑 hljs:整篇着色在大文件上要几百毫秒 */
const HIGHLIGHT_MAX = 120_000

/**
 * 高亮结果。**刻意不写成 computed**:hljs 是同步的,给 100KB 的 HTML 整篇着色要几百
 * 毫秒,放在 computed 里会挡住这次渲染 —— 用户看到的就是「点开文件先白等一会儿」。
 * 这里改成:先把原文立刻渲染出来(<pre> 兜底),再在浏览器空闲时着色、完成后替换。
 */
const html = ref('')
let highlightHandle: number | null = null

function cancelHighlight() {
  if (highlightHandle === null) return
  if (typeof window.cancelIdleCallback === 'function') window.cancelIdleCallback(highlightHandle)
  else window.clearTimeout(highlightHandle)
  highlightHandle = null
}

watch(
  () => [fileTab.value?.path || '', fileTab.value?.content || ''] as const,
  ([path, content]) => {
    cancelHighlight()
    html.value = ''
    if (!content || content.length > HIGHLIGHT_MAX) return
    const lang = langFromPath(path)
    if (!lang) return
    const run = () => {
      highlightHandle = null
      // 着色期间可能已切走标签或内容被改写,这份结果就作废
      if (fileTab.value?.path !== path || fileTab.value?.content !== content) return
      html.value = highlightCode(content, lang, false)
    }
    if (typeof window.requestIdleCallback === 'function') {
      highlightHandle = window.requestIdleCallback(run, { timeout: 300 })
    } else {
      highlightHandle = window.setTimeout(run, 16)
    }
  },
  { immediate: true },
)

const gutter = computed(() => {
  const content = fileTab.value?.content || ''
  if (!content) return ''
  return Array.from({ length: content.split('\n').length }, (_, i) => i + 1).join('\n')
})

/** null=未点过 / true=已复制 / false=复制失败 */
const copied = ref<boolean | null>(null)
let copyTimer: number | null = null
const copyLabel = computed(() =>
  copied.value === true ? COPIED_LABEL : copied.value === false ? COPY_FAILED_LABEL : COPY_LABEL,
)

async function onCopy() {
  const ok = await copyText(fileTab.value?.content || '')
  copied.value = ok
  if (copyTimer !== null) window.clearTimeout(copyTimer)
  copyTimer = window.setTimeout(() => (copied.value = null), 1600)
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && !fullscreen.value) closePanel()
}

watch(
  () => fullscreen.value,
  (on) => {
    // 全屏是"占满对话容器",Esc 退出全屏而不是直接收起侧栏
    if (on) window.addEventListener('keydown', onKeydown)
    else window.removeEventListener('keydown', onKeydown)
  },
)
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  cancelHighlight()
  if (copyTimer !== null) window.clearTimeout(copyTimer)
})
</script>

<template>
  <aside class="side-panel">
    <!-- 页签行:最右侧是全屏/退出全屏 + 收起侧栏 -->
    <header class="tab-bar">
      <div class="tab-list">
        <div
          v-for="tab in tabs"
          :key="tab.id"
          class="tab"
          :class="{ active: tab.id === activeId }"
          :title="tab.title"
          @click="setActive(tab.id)"
        >
          <svg v-if="tab.kind === 'home'" class="tab-icon" viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round">
            <circle cx="8" cy="8" r="6" />
            <path d="M8 4.6V8l2.4 1.6" />
          </svg>
          <svg v-else-if="tab.kind === 'terminal'" class="tab-icon" viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">
            <rect x="1.8" y="2.8" width="12.4" height="10.4" rx="1.6" />
            <path d="M5 6.6 7 8.4l-2 1.8M8.8 10.4h2.4" />
          </svg>
          <svg v-else class="tab-icon file" viewBox="0 0 16 16" width="13" height="13" fill="currentColor">
            <path d="M3 1.5a1 1 0 0 1 1-1h5l4 4v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-12zM9 1.5v3h3l-3-3z" />
          </svg>
          <span class="tab-title">{{ tab.title }}</span>
          <button type="button" class="tab-close" title="关闭标签页" @click.stop="closeTab(tab.id)">×</button>
        </div>
        <!-- 新增标签页(内容为开始页) -->
        <button type="button" class="tab-add" title="新增标签页" @click="openLauncher()">
          <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
            <path d="M8 3.2v9.6M3.2 8h9.6" />
          </svg>
        </button>
      </div>

      <div class="tab-actions">
        <button
          type="button"
          class="tab-icon-btn"
          :title="fullscreen ? '退出全屏' : '全屏'"
          @click="setFullscreen(!fullscreen)"
        >
          <svg v-if="!fullscreen" viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">
            <path d="M6 2H2.6v3.4M10 2h3.4v3.4M6 14H2.6v-3.4M10 14h3.4v-3.4" />
          </svg>
          <svg v-else viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">
            <path d="M6 2v4H2M10 2v4h4M6 14v-4H2M10 14v-4h4" />
          </svg>
        </button>
        <button type="button" class="tab-icon-btn" title="收起侧栏" @click="closePanel">
          <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">
            <path d="M2.5 3.5h11v9h-11zM10.5 3.5v9M8.6 6.4 6.4 8l2.2 1.6" />
          </svg>
        </button>
      </div>
    </header>

    <!-- 开始页(空状态或开始页标签)/ 终端(每个终端标签一个实例:换标签即换 xterm,已有输出靠缓冲重放) -->
    <PanelHome v-if="showLauncher" />
    <PanelTerminal v-else-if="active?.kind === 'terminal'" :key="active.id" />

    <!-- 文件:完整内容(行号 + 高亮) -->
    <div v-else class="file-wrap">
      <div class="file-subhead">
        <span class="file-path" :title="fileTab?.path">{{ fileTab?.path }}</span>
        <button type="button" class="file-btn" title="复制全文" @click="onCopy">{{ copyLabel }}</button>
      </div>
      <div v-if="fileTab?.note" class="file-note">{{ fileTab.note }}</div>
      <div class="file-body">
        <div v-if="fileTab?.loading" class="file-status">正在读取文件…</div>
        <template v-else-if="fileTab?.content">
          <!-- eslint-disable-next-line vue/no-v-html -- hljs 输出已转义,见 utils/highlight.ts -->
          <pre class="file-gutter" aria-hidden="true">{{ gutter }}</pre>
          <!-- eslint-disable-next-line vue/no-v-html -- 同上 -->
          <pre v-if="html" class="file-code" v-html="html"></pre>
          <pre v-else class="file-code">{{ fileTab.content }}</pre>
        </template>
        <div v-else class="file-status">没有可展示的内容</div>
      </div>
    </div>
  </aside>
</template>

<style scoped>
/* 内嵌 pane:宽度由父级的 flex + inline width 决定 */
.side-panel {
  min-width: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  border-left: 1px solid var(--border);
  /* 与对话容器同一个背景色(原来是 --bg-elevated,比对话区亮/暗一档,看起来像两块布) */
  background: var(--bg);
}
.tab-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-reasoning);
  flex-shrink: 0;
}
.tab-list {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 4px;
  overflow-x: auto;
  scrollbar-width: none;
}
.tab-list::-webkit-scrollbar {
  display: none;
}
.tab {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 5px 6px 5px 9px;
  border: 1px solid transparent;
  border-radius: 8px;
  color: var(--text-secondary);
  font-size: 12.5px;
  cursor: pointer;
  flex-shrink: 0;
  max-width: 220px;
  transition: background 0.15s, color 0.15s;
}
.tab:hover {
  background: var(--bg-hover);
}
.tab.active {
  background: var(--bg-elevated);
  border-color: var(--border);
  color: var(--text);
}
.tab-icon {
  flex-shrink: 0;
}
.tab-icon.file {
  color: var(--accent);
}
.tab-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tab-close {
  border: none;
  background: transparent;
  color: var(--text-tertiary);
  font-size: 15px;
  line-height: 1;
  width: 18px;
  height: 18px;
  border-radius: 4px;
  cursor: pointer;
  flex-shrink: 0;
}
.tab-close:hover {
  background: var(--bg-active);
  color: var(--text);
}
.tab-add {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  flex-shrink: 0;
  transition: background 0.15s, color 0.15s;
}
.tab-add:hover {
  background: var(--bg-hover);
  color: var(--text);
}
.tab-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}
.tab-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.tab-icon-btn:hover {
  background: var(--bg-hover);
  color: var(--text);
}
.file-wrap {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.file-subhead {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-elevated);
  flex-shrink: 0;
}
.file-path {
  flex: 1;
  min-width: 0;
  font-size: 11px;
  color: var(--text-tertiary);
  font-family: 'SFMono-Regular', Consolas, monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  direction: rtl; /* 路径太长时优先显示尾部 */
  text-align: left;
}
.file-btn {
  padding: 2px 9px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-elevated);
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
  flex-shrink: 0;
}
.file-btn:hover {
  border-color: var(--accent);
  color: var(--accent);
}
.file-note {
  padding: 7px 10px;
  border-bottom: 1px solid var(--border);
  color: var(--danger);
  font-size: 12px;
  flex-shrink: 0;
}
.file-body {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  overflow: auto;
  /* 跟随侧栏背景(与对话容器同色),不再单独铺一层 --bg-code */
  background: transparent;
}
.file-status {
  padding: 16px;
  color: var(--text-tertiary);
  font-size: 13px;
}
.file-gutter,
.file-code {
  margin: 0;
  padding: 12px 0;
  font-size: 12.5px;
  line-height: 1.6;
  font-family: 'SFMono-Regular', Consolas, monospace;
  white-space: pre;
  tab-size: 2;
}
.file-gutter {
  position: sticky;
  left: 0;
  padding-left: 12px;
  padding-right: 10px;
  color: var(--text-tertiary);
  text-align: right;
  /* 行号栏要与侧栏同色:它是 sticky 的,必须不透明才不会漏出底下的代码 */
  background: var(--bg);
  border-right: 1px solid var(--border);
  user-select: none;
  min-width: 48px;
}
.file-code {
  padding-right: 16px;
  color: var(--text);
}
</style>
