<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ used: number; window: number }>()

const R = 6.5
const CIRC = 2 * Math.PI * R

const pct = computed(() =>
  props.window > 0 ? Math.max(0, Math.min(100, Math.round((props.used / props.window) * 100))) : 0,
)
const color = computed(() => {
  if (pct.value >= 90) return '#ef4444'
  if (pct.value >= 60) return '#f59e0b'
  return 'var(--accent)'
})
const dashOffset = computed(() => CIRC * (1 - pct.value / 100))

const fmt = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}K` : `${n}`)

const tooltip = computed(() => `上下文使用率 ${pct.value}% (${fmt(props.used)} / ${fmt(props.window)} tokens)`)
</script>

<template>
  <div class="ctx-ring" :title="tooltip">
    <svg viewBox="0 0 16 16" width="16" height="16">
      <circle cx="8" cy="8" :r="R" fill="none" stroke="var(--border-strong)" stroke-width="1.5" />
      <circle
        cx="8"
        cy="8"
        :r="R"
        fill="none"
        :stroke="color"
        stroke-width="1.5"
        stroke-linecap="round"
        :stroke-dasharray="CIRC"
        :stroke-dashoffset="dashOffset"
        transform="rotate(-90 8 8)"
      />
    </svg>
  </div>
</template>

<style scoped>
.ctx-ring {
  display: flex;
  align-items: center;
  cursor: pointer;
  color: var(--text-tertiary);
  padding: 2px;
}
.ctx-ring svg {
  display: block;
}
</style>
