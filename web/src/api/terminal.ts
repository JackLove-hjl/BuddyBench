/** 终端相关接口:可用 shell 列表 + WebSocket 地址组装。 */
import { fetchJson, getApiBase } from './client'

export interface ShellOption {
  key: string
  label: string
}

export interface ShellList {
  shells: ShellOption[]
  default: string
}

/** 本机可用的 shell(后端探测:没装的不会列出来)。 */
export async function listShells(): Promise<ShellList> {
  return await fetchJson<ShellList>('/api/workspaces/shells')
}

/**
 * 组装终端 WebSocket 地址。
 *
 * 浏览器的 `WebSocket` 构造函数不允许自定义请求头,拿不到 `Authorization`,
 * 所以 token 只能走查询串(后端在 WS handler 里手工 `verify_token`)。
 */
export function terminalSocketUrl(opts: {
  conversationId: string
  shell: string
  cols: number
  rows: number
}): string {
  const base = getApiBase()
  const origin = /^https?:\/\//i.test(base)
    ? base.replace(/^http/i, 'ws').replace(/\/+$/, '')
    : `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`
  const query = new URLSearchParams({
    token: localStorage.getItem('llm-token') || '',
    shell: opts.shell,
    cols: String(Math.max(2, Math.round(opts.cols))),
    rows: String(Math.max(2, Math.round(opts.rows))),
  })
  return `${origin}/api/workspaces/${opts.conversationId}/terminal?${query.toString()}`
}
