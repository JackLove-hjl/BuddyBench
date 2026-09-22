<script setup lang="ts">
import { computed, ref } from 'vue'
import { NPopover } from 'naive-ui'
import { useModelStore } from '../../stores/model'
import type { ModelInfo } from '../../types'

const modelStore = useModelStore()
const popoverOpen = ref(false)

// 模型 id 格式: provider_name::model_id —— 按 provider 实例名分组,展示完整模型名
const groups = computed<[string, ModelInfo[]][]>(() => {
  const map = new Map<string, ModelInfo[]>()
  for (const m of modelStore.availableModels) {
    if (!map.has(m.provider)) map.set(m.provider, [])
    map.get(m.provider)!.push(m)
  }
  return Array.from(map.entries())
})

const currentModel = computed(() => {
  const id = modelStore.currentModel
  return modelStore.availableModels.find((m) => m.id === id) ?? null
})

// 无任何可用模型时提示去配置
const triggerLabel = computed(() => {
  if (currentModel.value) return currentModel.value.display_name
  return modelStore.models.length === 0 ? '请先配置模型' : '选择模型'
})

function choose(id: string) {
  popoverOpen.value = false
  modelStore.setCurrentModel(id)
}
</script>

<template>
  <div class="model-select">
    <n-popover
      v-model:show="popoverOpen"
      trigger="click"
      placement="top-start"
      :show-arrow="false"
      :offset="6"
      :body-style="{ padding: '6px', borderRadius: '10px' }"
    >
      <template #trigger>
        <button class="model-trigger" :disabled="!modelStore.availableModels.length" title="选择模型">
          <span class="model-trigger-label" :class="{ placeholder: !currentModel }">{{ triggerLabel }}</span>
          <svg class="model-chevron" viewBox="0 0 12 12" width="10" height="10" fill="currentColor">
            <path d="M2 4l4 4 4-4" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </button>
      </template>

      <div class="model-menu">
        <template v-for="[provider, models] in groups" :key="provider">
          <div class="model-group-title">{{ provider }}</div>
          <button
            v-for="m in models"
            :key="m.id"
            class="model-option"
            :class="{ active: m.id === currentModel?.id }"
            @click="choose(m.id)"
          >
            <span class="model-option-label" :title="m.display_name">{{ m.display_name }}</span>
            <svg v-if="m.id === currentModel?.id" class="model-check" viewBox="0 0 16 16" width="14" height="14" fill="currentColor">
              <path d="M6.5 11.5l-3.5-3.5 1-1 2.5 2.5 5-5 1 1-6 6z" />
            </svg>
          </button>
        </template>
        <div v-if="!groups.length" class="model-menu-empty">暂无可用模型,请先在设置中添加供应商</div>
      </div>
    </n-popover>
  </div>
</template>

<style scoped>
/* 与权限选择(PermissionSelect)触发器同款样式,仅不含图标 */
.model-select {
  display: flex;
  align-items: center;
}
.model-trigger {
  display: flex;
  align-items: center;
  gap: 6px;
  border: none;
  background: var(--bg-hover);
  color: var(--text);
  border-radius: 8px;
  padding: 5px 10px;
  cursor: pointer;
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  transition: background 0.15s;
  white-space: nowrap;
  max-width: 260px;
}
.model-trigger:hover:not(:disabled) {
  background: var(--bg-active);
}
.model-trigger:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}
.model-trigger-label {
  /* 行高取 16px 与权限触发器的 16px 图标等高,保证两个按钮实际高度一致
     (padding 相同:10 + 16 = 26px);用 line-height:1 会只有 12px 而矮 4px */
  line-height: 16px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.model-trigger-label.placeholder {
  color: var(--text-tertiary);
  font-weight: 400;
}
.model-chevron {
  color: var(--text-secondary);
  flex-shrink: 0;
}
.model-menu {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 240px;
  max-width: 360px;
  /* 固定最大高度 + 内部滚动,模型很多时不撑满屏幕 */
  max-height: min(320px, 50vh);
  overflow-y: auto;
  overscroll-behavior: contain;
}
.model-group-title {
  font-size: 11px;
  color: var(--text-tertiary);
  padding: 6px 10px 2px;
  letter-spacing: 0.5px;
}
.model-option {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  border: none;
  background: transparent;
  color: var(--text);
  padding: 8px 10px;
  border-radius: 7px;
  cursor: pointer;
  font-family: inherit;
  font-size: 13px;
  text-align: left;
  transition: background 0.12s;
}
.model-option:hover {
  background: var(--bg-hover);
}
.model-option.active {
  background: var(--accent-soft);
  color: var(--accent);
}
.model-option-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
  line-height: 1.3;
}
.model-check {
  flex-shrink: 0;
}
.model-menu-empty {
  font-size: 12px;
  color: var(--text-tertiary);
  padding: 10px;
}
</style>
