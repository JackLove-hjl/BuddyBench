import { fetchJson } from './client'
import type { ChatMessage, Conversation, Permission } from '../types'

export interface ConversationListResp {
  items: Conversation[]
  total: number
}

export interface CreateConversationBody {
  title?: string
  permission?: Permission
  workspace_path?: string | null
  plan_mode?: boolean
}

export function createConversation(body: CreateConversationBody | string): Promise<Conversation> {
  const payload =
    typeof body === 'string' ? { title: body } : body
  return fetchJson<Conversation>('/api/conversations', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function listConversations(): Promise<ConversationListResp> {
  return fetchJson<ConversationListResp>('/api/conversations')
}

export function getMessages(conversationId: string): Promise<{ items: ChatMessage[]; total: number }> {
  return fetchJson<{ items: ChatMessage[]; total: number }>(
    `/api/conversations/${conversationId}/messages`,
  )
}

export function removeConversation(conversationId: string): Promise<void> {
  return fetchJson<void>(`/api/conversations/${conversationId}`, { method: 'DELETE' })
}

export interface CompactResult {
  compacted: boolean
  summary: string | null
  message?: string
}

/** 压缩会话上下文:后端生成摘要并清空 agent 记忆 */
export function compactConversation(conversationId: string, model: string): Promise<CompactResult> {
  return fetchJson<CompactResult>(`/api/conversations/${conversationId}/compact`, {
    method: 'POST',
    body: JSON.stringify({ model }),
  })
}

export function updateConversation(
  conversationId: string,
  body: {
    title?: string
    permission?: Permission
    workspace_path?: string | null
    plan_mode?: boolean
    pinned?: boolean
  },
): Promise<Conversation> {
  return fetchJson<Conversation>(`/api/conversations/${conversationId}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}
