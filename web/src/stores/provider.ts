import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  createProvider,
  deleteProvider,
  listProviders,
  syncProviderModels,
  type ProviderModelItem,
  updateProvider,
  type ProviderCreateBody,
  type ProviderInfo,
  type ProviderUpdateBody,
} from '../api/providers'
import { useModelStore } from './model'

export const useProviderStore = defineStore('provider', () => {
  const providers = ref<ProviderInfo[]>([])
  const loading = ref(false)

  async function fetchProviders() {
    loading.value = true
    try {
      const resp = await listProviders()
      providers.value = resp.items
    } finally {
      loading.value = false
    }
  }

  async function add(body: ProviderCreateBody): Promise<ProviderInfo> {
    const p = await createProvider(body)
    providers.value.push(p)
    // 后端创建时已自动同步模型
    await useModelStore().fetchModels(true)
    return p
  }

  async function edit(id: string, body: ProviderUpdateBody): Promise<ProviderInfo> {
    const p = await updateProvider(id, body)
    const idx = providers.value.findIndex((x) => x.id === id)
    if (idx >= 0) providers.value[idx] = p
    return p
  }

  async function remove(id: string): Promise<void> {
    await deleteProvider(id)
    providers.value = providers.value.filter((x) => x.id !== id)
    await useModelStore().fetchModels(true)
  }

  /** 发现端点上的候选模型名(只探测不写库,由用户在表单里补全上下文后保存) */
  async function discover(id: string): Promise<ProviderModelItem[]> {
    const resp = await syncProviderModels(id)
    return resp.models
  }

  return { providers, loading, fetchProviders, add, edit, remove, discover }
})
