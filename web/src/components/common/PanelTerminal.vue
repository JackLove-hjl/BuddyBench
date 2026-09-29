<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { useSidePanel } from '../../composables/useSidePanel'
import { useThemeStore } from '../../stores/theme'
import {
  clearTerminalBuffer,
  getTerminalSession,
  restartTerminalSession,
  sendTerminalInput,
  sendTerminalResize,
  subscribeTerminal,
  type TerminalSession,
} from '../../composables/useTerminal'

/**
 * 侧栏终端:真实的 ConPTY shell(与 cmd.exe / PowerShell 窗口等价)。
 *
 * xterm.js 负责终端模拟(ANSI、光标、回滚),后端只搬运字节。会话(WebSocket + 输出
 * 缓冲)存在 composables/useTerminal.ts 里,所以切标签、收面板回来画面与 shell 都还在;
 * 组件只做"把画面接上去"这一件事,卸载时把已有的缓冲重放一遍即可复原。
 */
const { tabs, activeId } = useSidePanel()
const themeStore = useThemeStore()

const tab = computed(() => tabs.value.find((t) => t.id === activeId.value))
const session = computed<TerminalSession | undefined>(() =>
  tab.value ? getTerminalSession(tab.value.id) : undefined,
)

const statusText = computed(() => {
  const s = session.value
  if (!s) return '未连接'
  switch (s.status) {
    case 'connecting':
      return '连接中…'
    case 'open':
      return '已连接'
    case 'closed':
      return s.exitCode === null ? '已退出' : `已退出(退出码 ${s.exitCode})`
    default:
      return s.error || '连接失败'
  }
})

const statusKind = computed(() => session.value?.status || 'closed')

/*
 * 配色跟随应用主题(背景与 --bg 同色,不自成一块底色)。
 *
 * ANSI 调色板按 VS Code 的主题取值:尤其是**亮色**主题里,ANSI 的 7 号(white)与
 * 15 号(brightWhite)必须映射成**深灰**而不是白色 —— 按字面理解成白色的话,在白色
 * 背景上就成了"隐身字"。PowerShell 恰好用 37(white)给命令参数上色,
 * `echo xxx` 的 xxx 就看不见了。
 */
const LIGHT_THEME = {
  background: '#ffffff',
  foreground: '#1f2329',
  cursor: '#4d6bfe',
  cursorAccent: '#ffffff',
  selectionBackground: '#c9d4ff',
  black: '#000000',
  red: '#cd3131',
  green: '#0a7a32',
  yellow: '#8a6d00',
  blue: '#0451a5',
  magenta: '#bc05bc',
  cyan: '#037a94',
  white: '#4a4a4a',
  brightBlack: '#5f6368',
  brightRed: '#b10000',
  brightGreen: '#00761b',
  brightYellow: '#7a5c00',
  brightBlue: '#0451a5',
  brightMagenta: '#a300a3',
  brightCyan: '#036e85',
  brightWhite: '#2f2f2f',
}
const DARK_THEME = {
  background: '#1a1a1a',
  foreground: '#f2f3f5',
  cursor: '#6c86ff',
  cursorAccent: '#1a1a1a',
  selectionBackground: '#3d4460',
  black: '#1a1a1a',
  red: '#f0645c',
  green: '#3ecf8e',
  yellow: '#e3b341',
  blue: '#6c86ff',
  magenta: '#c792ea',
  cyan: '#4dd0e1',
  white: '#f2f3f5',
  brightBlack: '#6e737b',
  brightRed: '#ff7b72',
  brightGreen: '#56d4a0',
  brightYellow: '#f0c674',
  brightBlue: '#7d94ff',
  brightMagenta: '#d8b4fe',
  brightCyan: '#67e8f9',
  brightWhite: '#ffffff',
}

const host = ref<HTMLElement | null>(null)
let term: Terminal | null = null
let fitAddon: FitAddon | null = null
let unsubscribe: (() => void) | null = null
let observer: ResizeObserver | null = null
let fitTimer: number | null = null

function themeOptions() {
  return themeStore.isDark ? DARK_THEME : LIGHT_THEME
}

/** 按当前容器尺寸重算行列,并把新尺寸告诉后端(否则 shell 会按旧宽度换行) */
function fit() {
  const current = session.value
  if (!fitAddon || !term || !current) return
  try {
    fitAddon.fit()
  } catch {
    return
  }
  sendTerminalResize(current, term.cols, term.rows)
}

function scheduleFit() {
  if (fitTimer !== null) window.clearTimeout(fitTimer)
  fitTimer = window.setTimeout(() => {
    fitTimer = null
    fit()
  }, 120)
}

function writeStatusLine(text: string, color = '90') {
  term?.write(`\r\n\x1b[${color}m${text}\x1b[0m\r\n`)
}

function onRestart() {
  const current = session.value
  if (!current) return
  term?.reset()
  restartTerminalSession(current)
}

function onClear() {
  const current = session.value
  if (!current) return
  clearTerminalBuffer(current)
  term?.clear()
}

/** 复制当前选中内容(没有选中返回 false,让调用方决定是否放行按键) */
async function copySelection(): Promise<boolean> {
  const selection = term?.getSelection()
  if (!selection) return false
  try {
    await navigator.clipboard.writeText(selection)
    return true
  } catch {
    return false
  }
}

/** 从剪贴板粘贴到当前终端(直接写进 PTY,和窗口里粘贴等价) */
async function pasteFromClipboard() {
  const current = session.value
  if (!current) return
  try {
    const text = await navigator.clipboard.readText()
    if (text) sendTerminalInput(current, text)
  } catch {
    writeStatusLine('无法读取剪贴板(浏览器未授权)', '33')
  }
}

onMounted(() => {
  const element = host.value
  const current = session.value
  if (!element) return

  term = new Terminal({
    fontFamily: '"Cascadia Mono", Consolas, "Microsoft YaHei Mono", "Courier New", monospace',
    fontSize: 13,
    lineHeight: 1.2,
    cursorBlink: true,
    scrollback: 8000,
    // 兜底:任何与背景对比度不足的前景色,xterm 会自动调整到可读(1 = 不调整)
    minimumContrastRatio: 4.5,
    theme: themeOptions(),
  })
  fitAddon = new FitAddon()
  term.loadAddon(fitAddon)
  term.open(element)

  // 键盘输入逐键送进 PTY(回显由 shell 自己产生,不做本地回显)
  term.onData((data) => {
    const active = session.value
    if (active) sendTerminalInput(active, data)
  })

  /*
   * 复制 / 粘贴:与 Windows 终端(cmd / PowerShell 窗口)一致的三条约定 ——
   *   Ctrl+Shift+C 复制选中内容;Ctrl+C 在有选中时复制、没选中时照常发 SIGINT 中断命令;
   *   Ctrl+Shift+V 粘贴(普通 Ctrl+V 由 xterm 的隐藏输入框原生处理,不必自己接)。
   * 返回 false 表示"这次按键不给 shell";clipboard 读写都发生在用户按键的回调里,
   * 浏览器视为用户手势,不会弹权限框。
   */
  term.attachCustomKeyEventHandler((event) => {
    if (event.type !== 'keydown' || !event.ctrlKey) return true
    if (event.code === 'KeyC' && (event.shiftKey || term?.hasSelection())) {
      void copySelection()
      return false
    }
    if (event.code === 'KeyV' && event.shiftKey) {
      void pasteFromClipboard()
      return false
    }
    return true
  })

  // 先把已有输出重放出来(切标签/收面板回来时画面连续)
  if (current?.buffer) term.write(current.buffer)
  if (current) {
    unsubscribe = subscribeTerminal(current, (event) => {
      if (event.kind === 'data') {
        if (event.text) term?.write(event.text)
      } else if (event.kind === 'error') {
        writeStatusLine(event.text || '终端不可用', '31')
      } else if (event.kind === 'exit') {
        writeStatusLine(event.code === null || event.code === undefined ? '[进程已退出]' : `[进程已退出,退出码 ${event.code}]`)
      }
    })
    // 挂载前就已失败(比如没有会话)的情况:把原因补写到画面上
    if (current.status === 'error' && current.error && !current.buffer) {
      writeStatusLine(current.error, '31')
    }
  }

  fit()
  term.focus()

  observer = new ResizeObserver(() => scheduleFit())
  observer.observe(element)
})

watch(
  () => themeStore.isDark,
  () => {
    if (term) term.options.theme = themeOptions()
  },
)

onBeforeUnmount(() => {
  if (fitTimer !== null) window.clearTimeout(fitTimer)
  fitTimer = null
  observer?.disconnect()
  observer = null
  unsubscribe?.()
  unsubscribe = null
  // 只销毁渲染器,会话(WebSocket/shell)继续活着
  term?.dispose()
  term = null
  fitAddon = null
})
</script>

<template>
  <div class="panel-terminal">
    <div ref="host" class="term-host" @click="term?.focus()" />

    <div class="term-bar">
      <span class="term-dot" :class="statusKind" />
      <span class="term-shell">{{ session?.label || tab?.title || '终端' }}</span>
      <span class="term-status" :class="statusKind">{{ statusText }}</span>
      <span class="term-spacer" />
      <span v-if="session?.cwd" class="term-cwd" :title="session.cwd">{{ session.cwd }}</span>
      <button type="button" class="term-btn" title="清空当前画面" @click="onClear">清空</button>
      <button type="button" class="term-btn" title="重新开一个 shell" @click="onRestart">重启</button>
    </div>
  </div>
</template>

<style scoped>
.panel-terminal {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  /* 与侧栏/对话容器同色 */
  background: var(--bg);
}
.term-host {
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
  padding: 6px 4px 4px 8px;
  cursor: text;
}
/* xterm 自己是绝对定位/固定高度,这里给出容器尺寸即可 */
.term-host :deep(.xterm) {
  height: 100%;
}
.term-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 10px;
  border-top: 1px solid var(--border);
  background: var(--bg-elevated);
  font-size: 11.5px;
  color: var(--text-tertiary);
  flex-shrink: 0;
}
.term-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--text-tertiary);
  flex-shrink: 0;
}
.term-dot.open {
  background: var(--success);
}
.term-dot.connecting {
  background: #e3b341;
}
.term-dot.error {
  background: var(--danger);
}
.term-shell {
  color: var(--text-secondary);
  font-family: 'SFMono-Regular', Consolas, monospace;
  max-width: 40%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.term-status.error {
  color: var(--danger);
}
.term-spacer {
  flex: 1;
  min-width: 0;
}
.term-cwd {
  max-width: 45%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  direction: rtl;
  font-family: 'SFMono-Regular', Consolas, monospace;
}
.term-btn {
  padding: 2px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-elevated);
  color: var(--text-secondary);
  font-size: 11.5px;
  cursor: pointer;
  flex-shrink: 0;
}
.term-btn:hover {
  border-color: var(--accent);
  color: var(--accent);
}
</style>
