import type { SSEEvent, SSEHandlers } from '../types'

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) || ''
const TOKEN_KEY = 'llm-token'

/**
 * 流式对话:fetch POST + ReadableStream 分帧解析 SSE(EventSource 只支持 GET)。
 * 返回 AbortController 供停止;非 2xx 直接抛 ApiError(不进流解析)。
 */
export function streamChat(
  body: { conversation_id: string; model: string; message: string; attachments?: unknown[] },
  handlers: SSEHandlers,
): AbortController {
  return postStream(`${API_BASE}/api/chat`, body, handlers)
}

/**
 * 人工审批后恢复执行:危险操作 / 计划评审 / ask_user 提问被挂起时,
 * 前端用本接口提交决策。决策经后端 `Command(resume=...)` 注入中断点,返回的同样是 SSE 流。
 *
 * - approve / reject:批准或驳回;
 * - respond:ask_user 的作答(不执行工具,message 作为工具结果回给模型)。
 */
export function approveChat(
  body: {
    conversation_id: string
    model: string
    decisions: { type: 'approve' | 'reject' | 'respond'; message?: string }[]
  },
  handlers: SSEHandlers,
): AbortController {
  return postStream(`${API_BASE}/api/chat/approve`, body, handlers)
}

/** 统一的 POST + SSE 消费骨架 */
function postStream(
  url: string,
  body: unknown,
  handlers: SSEHandlers,
): AbortController {
  const controller = new AbortController()

  void (async () => {
    let res: Response
    try {
      const token = localStorage.getItem(TOKEN_KEY)
      res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify(body),
        signal: controller.signal,
      })
    } catch (e) {
      if ((e as Error).name === 'AbortError') return
      handlers.onError?.({ type: 'error', code: 'network', message: '网络连接失败' })
      return
    }

    if (!res.ok) {
      // 4xx 前置校验:读 detail → 兼容 {code,message} 对象与字符串
      let code = 'unknown'
      let message = `HTTP ${res.status}`
      try {
        const data = await res.json()
        if (data && typeof data === 'object') {
          code = data.code || code
          message = data.message || message
        } else if (typeof data === 'string') {
          message = data
        }
      } catch {
        // body 非 JSON
      }
      handlers.onError?.({ type: 'error', code, message }, { httpStatus: res.status })
      return
    }

    await readStream(res, handlers)
  })()

  return controller
}

/** 逐帧消费 SSE 响应体 */
async function readStream(res: Response, handlers: SSEHandlers) {
  const reader = res.body?.getReader()
  if (!reader) {
    handlers.onError?.({ type: 'error', code: 'internal', message: '响应无数据流' })
    return
  }

  const decoder = new TextDecoder('utf-8')
  let buf = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      // 按空行切帧,保留最后不完整帧
      let sep = buf.indexOf('\n\n')
      while (sep !== -1) {
        const frame = buf.slice(0, sep)
        buf = buf.slice(sep + 2)
        parseFrame(frame, handlers)
        sep = buf.indexOf('\n\n')
      }
    }
  } catch (e) {
    if ((e as Error).name === 'AbortError') return
    handlers.onError?.({ type: 'error', code: 'network', message: '连接中断' })
  }
}

function parseFrame(frame: string, handlers: SSEHandlers) {
  if (!frame.trim()) return
  let event = ''
  let data = ''
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) {
      // 多行 data 用 \n 连接防御
      data += (data ? '\n' : '') + line.slice(5).trimStart()
    }
  }
  if (!event || !data) return
  let ev: SSEEvent
  try {
    ev = JSON.parse(data) as SSEEvent
  } catch {
    return // JSON.parse 失败静默丢弃
  }
  switch (ev.type) {
    case 'delta':
      handlers.onDelta?.(ev.content)
      break
    case 'reasoning':
      handlers.onReasoning?.(ev.content)
      break
    case 'tool':
      handlers.onTool?.(ev)
      break
    case 'tool_args':
      handlers.onToolArgs?.(ev)
      break
    case 'compact':
      handlers.onCompact?.(ev)
      break
    case 'approval':
      handlers.onApproval?.(ev)
      break
    case 'done':
      handlers.onDone?.(ev)
      break
    case 'error':
      handlers.onError?.(ev)
      break
    default:
      break
  }
}
