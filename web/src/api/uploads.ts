import { getApiBase } from './client'

export interface UploadResult {
  url: string
  filename: string
  kind: 'image' | 'file'
  size: number
}

/** 上传文件(图片或文本),带 JWT;失败抛 ApiError 语义。 */
export async function uploadFile(file: File): Promise<UploadResult> {
  const token = localStorage.getItem('llm-token')
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(`${getApiBase()}/api/uploads`, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: form,
  })
  if (!res.ok) {
    let message = `HTTP ${res.status}`
    try {
      const data = await res.json()
      if (data && typeof data === 'object') {
        message = data.message || message
      }
    } catch {
      // ignore
    }
    throw new Error(message)
  }
  return res.json() as Promise<UploadResult>
}

const TEXT_EXTS = new Set([
  'txt', 'md', 'py', 'js', 'ts', 'json', 'csv', 'log', 'html', 'css', 'xml',
  'yaml', 'yml', 'sh', 'sql', 'ini', 'cfg', 'toml', 'java', 'c', 'cpp', 'h',
  'go', 'rs', 'rb',
])

export function isImageFile(name: string): boolean {
  const ext = name.split('.').pop()?.toLowerCase() || ''
  return ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'].includes(ext)
}

export function isTextFile(name: string): boolean {
  const ext = name.split('.').pop()?.toLowerCase() || ''
  return TEXT_EXTS.has(ext)
}

/** 读取文本文件内容(限 512KB,防超大文件拖垮请求)。 */
export async function readTextFile(file: File): Promise<string> {
  if (file.size > 512 * 1024) return ''
  return file.text()
}
