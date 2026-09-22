import { fetchJson } from './client'

export interface ProviderModelItem {
  id: string
  display_name?: string
  /** 输入上下文上限(上下文使用率圆圈的分母 / 自动压缩阈值基数) */
  input_tokens?: number | null
  /** 最大输出 token 数 */
  output_tokens?: number | null
}

export interface ProviderInfo {
  id: string
  name: string
  provider_type: string
  base_url: string
  api_key_masked: string
  models: ProviderModelItem[]
  extra_models: string[]
  created_at: string
  updated_at: string
}

export interface ProviderListResp {
  items: ProviderInfo[]
  total: number
}

export interface ProviderCreateBody {
  name: string
  provider_type: string
  base_url: string
  api_key: string
  /** 手动录入的模型:名称 + 输入/输出上下文(不再从 /models 自动同步) */
  models?: ProviderModelItem[]
}

export interface ProviderUpdateBody {
  name?: string
  provider_type?: string
  base_url?: string
  api_key?: string
  models?: ProviderModelItem[]
}

export function listProviders(): Promise<ProviderListResp> {
  return fetchJson<ProviderListResp>('/api/providers')
}

export function createProvider(body: ProviderCreateBody): Promise<ProviderInfo> {
  return fetchJson<ProviderInfo>('/api/providers', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function updateProvider(id: string, body: ProviderUpdateBody): Promise<ProviderInfo> {
  return fetchJson<ProviderInfo>(`/api/providers/${id}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function deleteProvider(id: string): Promise<void> {
  return fetchJson<void>(`/api/providers/${id}`, { method: 'DELETE' })
}

export function syncProviderModels(id: string): Promise<{ models: ProviderModelItem[]; message: string }> {
  return fetchJson<{ models: ProviderModelItem[]; message: string }>(`/api/providers/${id}/sync`, {
    method: 'POST',
  })
}
