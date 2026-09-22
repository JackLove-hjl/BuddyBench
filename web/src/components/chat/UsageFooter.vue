<script setup lang="ts">
import { computed } from 'vue'
import type { Usage } from '../../types'

const props = defineProps<{ usage?: Usage; model?: string }>()

const hasUsage = computed(
  () => !!(props.usage && (props.usage.prompt_tokens || props.usage.completion_tokens)),
)

const text = computed(() => {
  if (!hasUsage.value) return props.model ? `由 ${props.model} 生成` : ''
  const parts: string[] = []
  if (props.usage?.prompt_tokens) parts.push(`${props.usage.prompt_tokens} 输入`)
  if (props.usage?.completion_tokens) parts.push(`${props.usage.completion_tokens} 输出`)
  const model = props.model ? ` · ${props.model}` : ''
  return `Tokens:${parts.join(' / ')}${model}`
})
</script>

<template>
  <div v-if="text" class="usage-footer">{{ text }}</div>
</template>

<style scoped>
.usage-footer {
  margin-top: 8px;
  font-size: 11px;
  color: var(--text-tertiary);
  user-select: none;
}
</style>
