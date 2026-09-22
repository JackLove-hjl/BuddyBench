import { fetchJson } from './client'
import type { ModelInfo } from '../types'

export interface ModelListResponse {
  models: ModelInfo[]
  /** .env DEFAULT_MODEL 对应的全局模型 id(内置模型分组);未配置时为空串 */
  default_model?: string
}

export function listModels(): Promise<ModelListResponse> {
  return fetchJson<ModelListResponse>('/api/models')
}
