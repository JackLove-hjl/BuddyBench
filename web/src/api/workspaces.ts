import { fetchJson } from './client'
import type { Conversation } from '../types'

export interface DirEntry {
  name: string
  path: string
}

export interface BrowseResult {
  /** 浏览起点时为空(此时看 roots) */
  current: string | null
  /** 上一级目录路径;已在根时为 null */
  parent: string | null
  /** 仅浏览起点时返回(Windows 盘符 / Linux 根) */
  roots?: string[]
  children: DirEntry[]
}

/** 设置会话工作区(绝对路径;传 null 清除)——本机模式直接登记本地路径 */
export function setWorkspace(conversationId: string, workspacePath: string | null): Promise<Conversation> {
  return fetchJson<Conversation>(`/api/workspaces/${conversationId}`, {
    method: 'PUT',
    body: JSON.stringify({ workspace_path: workspacePath }),
  })
}

/** 浏览本地目录(文件窗口式工作区选择) */
export function browseDirectories(path: string): Promise<BrowseResult> {
  const q = path ? `?path=${encodeURIComponent(path)}` : ''
  return fetchJson<BrowseResult>(`/api/workspaces/browse${q}`)
}

export interface WorkspaceFileEntry {
  name: string
  path: string
  kind: 'dir' | 'file'
  size?: number
}

export interface WorkspaceFilesResult {
  current: string
  parent: string | null
  root: string
  dirs: WorkspaceFileEntry[]
  files: WorkspaceFileEntry[]
}

/** 列出工作区目录/文件(@ 选择,有会话) */
export function listWorkspaceFiles(conversationId: string, path?: string): Promise<WorkspaceFilesResult> {
  const q = path ? `?path=${encodeURIComponent(path)}` : ''
  return fetchJson<WorkspaceFilesResult>(`/api/workspaces/${conversationId}/files${q}`)
}

/** 读取工作区文件内容(@ 引用时注入对话,有会话) */
export function readWorkspaceFile(conversationId: string, relPath: string): Promise<{ content: string; path: string }> {
  const q = `?path=${encodeURIComponent(relPath)}`
  return fetchJson<{ content: string; path: string }>(`/api/workspaces/${conversationId}/read${q}`)
}

/** 列出工作区目录/文件(按绝对路径,新对话尚未创建会话时使用) */
export function listWorkspaceFilesByPath(workspacePath: string, path?: string): Promise<WorkspaceFilesResult> {
  const q = path ? `&path=${encodeURIComponent(path)}` : ''
  return fetchJson<WorkspaceFilesResult>(`/api/workspaces/files?workspace_path=${encodeURIComponent(workspacePath)}${q}`)
}

/** 读取工作区文件内容(按绝对路径,新对话尚未创建会话时使用) */
export function readWorkspaceFileByPath(workspacePath: string, relPath: string): Promise<{ content: string; path: string }> {
  const q = `&path=${encodeURIComponent(relPath)}`
  return fetchJson<{ content: string; path: string }>(`/api/workspaces/read?workspace_path=${encodeURIComponent(workspacePath)}${q}`)
}
