/** 统一 fetchJson:自动附加 JWT、非 2xx → ApiError(自动解包 FastAPI 的 detail,透出后端真实错误原因)。 */

export interface ApiErrorBody {
  code?: string
  message?: string
}

export class ApiError extends Error {
  status: number
  code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) || ''
const TOKEN_KEY = 'llm-token'

function parseDetail(detail: unknown): { code: string; message: string } {
  if (detail && typeof detail === 'object') {
    if (Array.isArray(detail)) {
      // FastAPI 422 校验错误:[{loc, msg, type}, ...]
      const msgs = detail
        .map((item) => (item && typeof item === 'object' ? (item as { msg?: string }).msg : undefined))
        .filter((m): m is string => !!m)
      if (msgs.length) return { code: 'validation_error', message: msgs.join(';') }
      return { code: 'validation_error', message: '请求参数不合法' }
    }
    const d = detail as ApiErrorBody
    return { code: d.code || 'unknown', message: d.message || '请求失败' }
  }
  if (typeof detail === 'string' && detail.trim()) {
    return { code: 'unknown', message: detail }
  }
  return { code: 'unknown', message: '请求失败' }
}

/** 从响应体中取出后端业务错误载荷,兼容 {detail: {...}} / {detail: "..."} / {code, message} 三种形态。 */
function extractDetail(body: unknown): unknown {
  if (body && typeof body === 'object' && !Array.isArray(body) && 'detail' in body) {
    return (body as { detail: unknown }).detail
  }
  return body
}

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem(TOKEN_KEY)
  return token ? { Authorization: `Bearer ${token}` } : {}
}

/** 401 时清除本地 token 并跳转登录页(除 /auth/* 自身请求)。 */
function handleUnauthorized() {
  const path = window.location.pathname
  if (path.includes('/login')) return
  localStorage.removeItem(TOKEN_KEY)
  if (!window.location.pathname.includes('/login')) {
    window.location.href = '/login'
  }
}

export async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...authHeaders(), ...(init?.headers || {}) },
    })
  } catch {
    throw new ApiError(0, 'network_error', '网络连接失败,请检查网络或后端服务是否可用')
  }
  if (res.status === 401 && !path.startsWith('/api/auth/')) {
    handleUnauthorized()
  }
  if (!res.ok) {
    let body: unknown = null
    try {
      body = await res.json()
    } catch {
      // body 非 JSON
    }
    const { code, message } = parseDetail(extractDetail(body))
    throw new ApiError(res.status, code, message || `HTTP ${res.status}`)
  }
  if (res.status === 204) {
    return undefined as T
  }
  return res.json() as Promise<T>
}

export function getApiBase(): string {
  return API_BASE
}
