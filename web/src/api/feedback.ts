import { fetchJson } from './client'

export type FeedbackRating = 'good' | 'bad'

export interface FeedbackPayload {
  rating: FeedbackRating
  conversation_id?: string | null
  message_id?: string | null
  /** 问题分类标签(可多选),仅 👎 时带 */
  categories?: string[]
  /** 补充详情,仅 👎 时带 */
  detail?: string
  model?: string | null
}

export interface FeedbackResult {
  ok: boolean
  id: string
  rating: FeedbackRating
}

/** 提交回复反馈(👍 / 👎)。同一条消息再次提交会覆盖上一次的记录。 */
export function submitFeedback(payload: FeedbackPayload): Promise<FeedbackResult> {
  return fetchJson<FeedbackResult>('/api/feedback', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}
