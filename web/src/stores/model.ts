import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { listModels } from '../api/models'
import type { ModelInfo } from '../types'

const LAST_MODEL_KEY = 'llm-last-model'

export const useModelStore = defineStore('model', () => {
  const models = ref<ModelInfo[]>([])
  // 初值取上次选择:切换对话 / 新建对话 / 刷新页面都沿用同一份全局选择
  const currentModel = ref<string>(localStorage.getItem(LAST_MODEL_KEY) || '')
  const defaultModel = ref<string>('')
  const loading = ref(false)
  const loaded = ref(false)

  const availableModels = computed(() => models.value.filter((m) => m.available))
  /** 当前选中模型的完整信息(含声明的输入/输出上下文) */
  const currentModelInfo = computed(
    () => models.value.find((m) => m.id === currentModel.value) ?? null,
  )
  const hasModels = computed(() => availableModels.value.length > 0)

  /** 该模型是否仍存在且可用(用于校验持久化的选择是否失效) */
  function isUsable(id: string): boolean {
    return !!id && availableModels.value.some((m) => m.id === id)
  }

  async function fetchModels(force = false) {
    if (loaded.value && !force) return
    loading.value = true
    try {
      const resp = await listModels()
      models.value = resp.models
      defaultModel.value = resp.default_model || ''
      loaded.value = true
      // 优先级:上次选择(localStorage)> DEFAULT_MODEL(内置模型)> 第一个可用模型
      // 列表为空(用户未配置供应商)时清空选择,避免残留旧模型 id 显示
      if (models.value.length === 0) {
        currentModel.value = ''
        localStorage.removeItem(LAST_MODEL_KEY)
      } else if (!isUsable(currentModel.value)) {
        const preferred = defaultModel.value
          ? models.value.find((m) => m.id === defaultModel.value && m.available)
          : undefined
        const first = preferred || availableModels.value[0] || models.value[0]
        if (first) setCurrentModel(first.id)
      }
    } finally {
      loading.value = false
    }
  }

  function setCurrentModel(id: string) {
    currentModel.value = id
    if (id) {
      localStorage.setItem(LAST_MODEL_KEY, id)
    } else {
      localStorage.removeItem(LAST_MODEL_KEY)
    }
  }

  return {
    models,
    currentModel,
    defaultModel,
    loading,
    loaded,
    availableModels,
    currentModelInfo,
    hasModels,
    fetchModels,
    setCurrentModel,
  }
})
