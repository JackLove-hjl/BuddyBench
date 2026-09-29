/**
 * 工具给的路径 → 工作区相对路径。
 *
 * 后端读文件的接口(`/api/workspaces/{id}/read`)只接受**工作区内相对路径**,
 * 而工具调用里带的往往是绝对路径(模型写的是 `D:\proj\a.html`)。这里做换算:
 * - 已经是相对路径 → 原样返回;
 * - 绝对路径且在工作区内 → 去掉工作区前缀;
 * - 在工作区外(例如模型写到别处)→ 返回 null,由调用方给可读提示。
 */

/** 统一分隔符并去掉首尾多余斜杠(Windows 盘符保持 `D:/x` 形式) */
function normalize(path: string): string {
  return path.trim().replace(/\\/g, '/').replace(/\/+$/, '')
}

/** 路径比较:Windows 盘符不区分大小写,统一按小写比较 */
function lower(path: string): string {
  return path.toLowerCase()
}

export function toWorkspaceRelative(filePath: string, workspaceRoot?: string | null): string | null {
  const target = normalize(filePath)
  if (!target) return null
  // 相对路径(模型偶尔给相对路径)= 已经是工作区内路径
  const isAbsolute = /^[a-zA-Z]:\//.test(target) || target.startsWith('/')
  if (!isAbsolute) return target
  const root = normalize(workspaceRoot || '')
  if (!root) return null
  const prefix = root + '/'
  if (lower(target) === lower(root)) return null // 目录本身不是文件
  if (!lower(target).startsWith(lower(prefix))) return null
  return target.slice(prefix.length)
}

/** 展示用文件名(路径最后一段) */
export function baseName(filePath: string): string {
  const normalized = normalize(filePath)
  if (!normalized) return ''
  const parts = normalized.split('/')
  return parts[parts.length - 1] || normalized
}
