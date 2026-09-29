import { readonly, ref } from 'vue'
import { readWorkspaceFile, readWorkspaceFileByPath } from '../api/workspaces'
import { useConversationStore } from '../stores/conversation'
import { baseName, toWorkspaceRelative } from '../utils/workspacePath'
import { closeTerminalSession, createTerminalSession, getTerminalSession, shellTitle } from './useTerminal'

/**
 * 右侧栏(标签页形式)的全局状态。
 *
 * - 打开方式:点写入/编辑文件的工具卡(定位到"改完的完整文件")、点对话右上角的图标、
 *   或在"开始"页里打开工作区文件 / 新建终端;
 * - 标签页可多个并存、可关闭、可切换;
 * - 面板的开合状态持久化 —— 下次进来保持上次是收起还是展开。
 */
interface BaseTab {
  id: string
  title: string
}
export interface HomeTab extends BaseTab {
  kind: 'home'
}
export interface FileTab extends BaseTab {
  kind: 'file'
  /** 工具给的原始路径(绝对或工作区内相对) */
  path: string
  content: string
  /** 读不到时的说明(与 content 可同时存在:退回卡片全文) */
  note: string
  loading: boolean
}
export interface TerminalTab extends BaseTab {
  kind: 'terminal'
  /** 后端 shell key(cmd / powershell / pwsh / bash);空串 = 后端默认 */
  shell: string
  /** 终端会话 id(会话本身在 composables/useTerminal.ts 里,不随标签组件卸载) */
  sessionId: string
}
export type PanelTab = HomeTab | FileTab | TerminalTab

const OPEN_KEY = 'llm-side-panel'
/** 终端/开始页标签各自的自增序号(可以开多个,像 IDE 那样) */
let terminalSeq = 0
let launcherSeq = 0

const open = ref(localStorage.getItem(OPEN_KEY) === '1')
const fullscreen = ref(false)
// 一开始**没有标签页**:此时右侧栏显示开始页(不占标签),点「+」才会生成一个开始页标签
const tabs = ref<PanelTab[]>([])
const activeId = ref<string>('')

function persistOpen() {
  try {
    localStorage.setItem(OPEN_KEY, open.value ? '1' : '0')
  } catch {
    // 隐私模式下可能禁止写入,忽略
  }
}

/** 当前激活的标签被关掉后,把焦点落到还在的标签上(都关完了就回到"显示开始页"的空状态) */
function focusFallback(removedIndex: number) {
  if (tabs.value.some((t) => t.id === activeId.value)) return
  const next = tabs.value[Math.min(removedIndex, tabs.value.length - 1)]
  activeId.value = next ? next.id : ''
}

export function useSidePanel() {
  function openPanel() {
    open.value = true
    persistOpen()
  }

  function closePanel() {
    open.value = false
    fullscreen.value = false
    persistOpen()
  }

  function togglePanel() {
    if (open.value) closePanel()
    else openPanel()
  }

  function setActive(id: string) {
    activeId.value = id
  }

  function closeTab(id: string) {
    const index = tabs.value.findIndex((t) => t.id === id)
    if (index < 0) return
    const closing = tabs.value[index]
    if (closing?.kind === 'terminal') {
      // 关标签 = 结束会话:后端会杀掉 shell,不留后台进程
      const session = getTerminalSession(closing.sessionId)
      if (session) closeTerminalSession(session)
    }
    tabs.value.splice(index, 1)
    if (activeId.value === id) focusFallback(index)
  }

  /** 新增一个标签页,内容是开始页(右侧栏的「+」按钮) */
  function openLauncher() {
    openPanel()
    fullscreen.value = false
    const id = `home-${++launcherSeq}`
    tabs.value.push({ id, kind: 'home', title: '开始' })
    activeId.value = id
  }

  /** 打开(或聚焦)一个文件标签页:从工作区实际文件读内容,失败时退回卡片全文 */
  async function openFile(opts: {
    path: string
    workspaceRoot?: string | null
    conversationId?: string | null
    fallback?: string | null
  }) {
    const path = opts.path
    if (!path) return
    openPanel()
    fullscreen.value = false
    const existing = tabs.value.find((t) => t.kind === 'file' && (t as FileTab).path === path)
    if (existing) {
      activeId.value = existing.id
      return
    }

    const title = baseName(path) || path
    const tab: FileTab = { id: path, kind: 'file', title, path, content: opts.fallback || '', note: '', loading: true }
    tabs.value.push(tab)
    activeId.value = tab.id
    // 一定要改**数组里那一份引用**:push 进去的是原始对象,而渲染读的是 Vue 包装后的
    // 响应式代理 —— 继续改上面那个局部变量 content/loading,数据变了但不会触发任何
    // 视图更新,界面就永远停在"正在读取文件…"(要等别处触发一次重渲染才突然显示)。
    const liveTab = tabs.value[tabs.value.length - 1] as FileTab

    // 这里**不要**用全局序号做竞态保护:每个 FileTab 都是这次调用新建的,响应只会写给
    // 自己这一个标签;重复点击被上面的 existing 分支挡掉,不会有第二个请求。
    // 之前的写法是 `const current = ++seq; ... if (current !== seq) return` —— 只要期间
    // 又开过别的文件,先发出的那次就会在赋值前 return,那个标签就永久停在"正在读取文件…"
    // (接口早就 200 返回了,界面上却像一直在加载)。
    const rel = toWorkspaceRelative(path, opts.workspaceRoot)
    let note = ''
    let content = opts.fallback || ''
    if (!rel) {
      note = opts.workspaceRoot
        ? '该文件不在当前会话的工作区内,按卡片里的内容展示'
        : '当前会话未设置工作区,按卡片里的内容展示'
    } else {
      try {
        const resp = opts.conversationId
          ? await readWorkspaceFile(opts.conversationId, rel)
          : await readWorkspaceFileByPath(String(opts.workspaceRoot), rel)
        content = resp.content
      } catch (e) {
        note = `读取文件失败:${(e as Error).message}`
      }
    }
    liveTab.content = content
    liveTab.loading = false
    liveTab.note = content ? note : note ? `${note};可点卡片上的「查看变更」看本次改动` : '文件内容不可读'
  }

  /** 工作区文件浏览器里点开一个文件(相对路径) */
  async function openWorkspaceFile(opts: {
    relPath: string
    workspaceRoot?: string | null
    conversationId?: string | null
  }) {
    const abs =
      opts.workspaceRoot && opts.workspaceRoot.replace(/\\/g, '/').replace(/\/+$/, '').length
        ? `${opts.workspaceRoot.replace(/\\/g, '/').replace(/\/+$/, '')}/${opts.relPath}`
        : opts.relPath
    await openFile({ path: abs, workspaceRoot: opts.workspaceRoot, conversationId: opts.conversationId })
  }

  /**
   * 新建一个终端标签页:每个标签一个真实 shell(可以像 IDE 一样开多个)。
   *
   * 会话建立后**不随组件卸载** —— 收面板、切标签回来,shell 与画面都还在。
   */
  function openTerminal(shell = '') {
    openPanel()
    fullscreen.value = false
    const convStore = useConversationStore()
    const id = `term-${++terminalSeq}`
    const title = shell ? shellTitle(shell) : '终端'
    createTerminalSession({
      id,
      conversationId: convStore.currentId,
      shell,
      label: title,
      // 初始尺寸给个合理默认,挂载后 xterm 会按实际容器 fit 并纠正
      cols: 100,
      rows: 30,
    })
    tabs.value.push({ id, kind: 'terminal', title, shell, sessionId: id })
    activeId.value = id
  }

  return {
    open: readonly(open),
    fullscreen: readonly(fullscreen),
    tabs: readonly(tabs),
    activeId: readonly(activeId),
    openPanel,
    closePanel,
    togglePanel,
    setActive,
    closeTab,
    openLauncher,
    openFile,
    openWorkspaceFile,
    openTerminal,
    setFullscreen: (value: boolean) => (fullscreen.value = value),
  }
}
