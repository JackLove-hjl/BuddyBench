/**
 * 终端会话注册表(模块级单例)。
 *
 * 为什么不把会话放在组件里:右侧栏是 `v-if` 挂载的,切换标签、收起面板都会卸载组件。
 * 会话若跟着组件走,用户一收面板 shell 就被杀了、cd 与正在跑的命令全丢。
 * 所以 WebSocket 与输出缓冲活在这里,组件只负责把画面接到 xterm 上;重新挂载时用
 * 缓冲重放,画面与滚动内容保持连续。
 */
import { reactive } from 'vue'
import { terminalSocketUrl } from '../api/terminal'

export type TerminalStatus = 'connecting' | 'open' | 'closed' | 'error'

/** 会话向前端组件推送的事件(data 为原始输出,其余为状态) */
export interface TerminalEvent {
  kind: 'data' | 'status' | 'exit' | 'error'
  text?: string
  code?: number | null
}

export interface TerminalSession {
  id: string
  conversationId: string
  /** 后端 shell key(cmd / powershell / pwsh / bash);空串 = 用后端默认 */
  shell: string
  /** 服务端回报的实际 shell 名,显示在提示条上 */
  label: string
  status: TerminalStatus
  error: string
  cwd: string
  exitCode: number | null
  /** 累积的原始输出,供重挂时重放 */
  buffer: string
  cols: number
  rows: number
  ws: WebSocket | null
  listeners: Set<(event: TerminalEvent) => void>
}

/** 缓冲上限:超出后从换行处截断,避免长时间跑命令把内存吃满 */
const BUFFER_MAX = 400_000
/** 会话表(值本身是 reactive 的,组件读取属性即可获得响应式更新) */
const sessions = new Map<string, TerminalSession>()

function emit(session: TerminalSession, event: TerminalEvent) {
  for (const listener of session.listeners) listener(event)
}

function appendBuffer(session: TerminalSession, text: string) {
  session.buffer += text
  if (session.buffer.length <= BUFFER_MAX) return
  // 从换行处切开:直接截断可能把一条 ANSI 转义序列切成两半,重放时画面会错
  const cut = session.buffer.indexOf('\n', session.buffer.length - BUFFER_MAX)
  session.buffer = cut >= 0 ? session.buffer.slice(cut + 1) : session.buffer.slice(-BUFFER_MAX)
}

function connect(session: TerminalSession) {
  if (!session.conversationId) {
    session.status = 'error'
    session.error = '当前没有会话,终端需要先有会话与工作区'
    emit(session, { kind: 'status' })
    return
  }
  session.status = 'connecting'
  session.error = ''
  session.exitCode = null
  emit(session, { kind: 'status' })

  const ws = new WebSocket(
    terminalSocketUrl({
      conversationId: session.conversationId,
      shell: session.shell,
      cols: session.cols,
      rows: session.rows,
    }),
  )
  ws.binaryType = 'arraybuffer'
  session.ws = ws
  // 多字节字符可能被切在两个数据块之间,必须用流式解码
  const decoder = new TextDecoder('utf-8')

  ws.onmessage = (event) => {
    if (typeof event.data === 'string') {
      let payload: { type?: string; message?: string; code?: number | null; cwd?: string; label?: string; cols?: number; rows?: number }
      try {
        payload = JSON.parse(event.data)
      } catch {
        return
      }
      if (payload.type === 'ready') {
        session.status = 'open'
        session.cwd = payload.cwd || ''
        if (payload.label) session.label = payload.label
        if (payload.cols) session.cols = payload.cols
        if (payload.rows) session.rows = payload.rows
        emit(session, { kind: 'status' })
      } else if (payload.type === 'exit') {
        session.status = 'closed'
        session.exitCode = payload.code ?? null
        emit(session, { kind: 'exit', code: session.exitCode })
      } else if (payload.type === 'error') {
        session.status = 'error'
        session.error = payload.message || '终端不可用'
        emit(session, { kind: 'error', text: session.error })
      }
      return
    }
    const text = decoder.decode(new Uint8Array(event.data as ArrayBuffer), { stream: true })
    if (!text) return
    appendBuffer(session, text)
    emit(session, { kind: 'data', text })
  }

  ws.onclose = () => {
    session.ws = null
    if (session.status === 'open' || session.status === 'connecting') {
      session.status = 'closed'
      emit(session, { kind: 'exit', code: session.exitCode })
    }
  }
  ws.onerror = () => {
    if (!session.error) session.error = '终端连接中断'
  }
}

/** 新建会话(立即连接)。同 id 重复调用会复用已有会话。 */
export function createTerminalSession(opts: {
  id: string
  conversationId: string | null
  shell?: string
  label?: string
  cols?: number
  rows?: number
}): TerminalSession {
  const existing = sessions.get(opts.id)
  if (existing) return existing
  const session = reactive<TerminalSession>({
    id: opts.id,
    conversationId: opts.conversationId || '',
    shell: opts.shell || '',
    label: opts.label || '',
    status: 'connecting',
    error: '',
    cwd: '',
    exitCode: null,
    buffer: '',
    cols: opts.cols || 100,
    rows: opts.rows || 30,
    ws: null,
    listeners: new Set(),
  })
  sessions.set(opts.id, session)
  connect(session)
  return session
}

export function getTerminalSession(id: string): TerminalSession | undefined {
  return sessions.get(id)
}

/** 订阅会话事件,返回取消订阅函数。 */
export function subscribeTerminal(session: TerminalSession, listener: (event: TerminalEvent) => void): () => void {
  session.listeners.add(listener)
  return () => session.listeners.delete(listener)
}

export function sendTerminalInput(session: TerminalSession, data: string) {
  if (data && session.ws?.readyState === WebSocket.OPEN) session.ws.send(JSON.stringify({ type: 'input', data }))
}

export function sendTerminalResize(session: TerminalSession, cols: number, rows: number) {
  if (cols === session.cols && rows === session.rows) return
  session.cols = cols
  session.rows = rows
  if (session.ws?.readyState === WebSocket.OPEN) session.ws.send(JSON.stringify({ type: 'resize', cols, rows }))
}

/** 清空画面与缓冲(下次重挂不会又把旧内容放回来)。 */
export function clearTerminalBuffer(session: TerminalSession) {
  session.buffer = ''
}

/** 关掉会话并杀掉 shell(标签关闭时调用)。 */
export function closeTerminalSession(session: TerminalSession) {
  session.listeners.clear()
  const ws = session.ws
  session.ws = null
  sessions.delete(session.id)
  if (ws) {
    ws.onmessage = null
    ws.onclose = null
    ws.onerror = null
    try {
      ws.close()
    } catch {
      // 已经关了
    }
  }
}

/** 重启:同一个标签里换一个新 shell(原进程会被后端回收)。 */
export function restartTerminalSession(session: TerminalSession) {
  const ws = session.ws
  session.ws = null
  session.buffer = ''
  if (ws) {
    ws.onmessage = null
    ws.onclose = null
    ws.onerror = null
    try {
      ws.close()
    } catch {
      // 已经关了
    }
  }
  session.listeners.forEach((listener) => listener({ kind: 'status' }))
  connect(session)
}

/** 标签标题:与 cmd/PowerShell 窗口的叫法一致。 */
export function shellTitle(key: string): string {
  switch (key) {
    case 'cmd':
      return 'cmd.exe'
    case 'powershell':
      return 'powershell.exe'
    case 'pwsh':
      return 'pwsh.exe'
    case 'bash':
      return 'bash'
    default:
      return '终端'
  }
}
