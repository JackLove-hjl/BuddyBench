<script setup lang="ts">
import { computed } from 'vue'
import { filterCommands, type CommandDef } from './commands'

const props = defineProps<{
  show: boolean
  query: string
  commands: CommandDef[]
}>()

const emit = defineEmits<{ (e: 'select', cmd: CommandDef): void; (e: 'close'): void }>()

const filtered = computed(() => filterCommands(props.commands, props.query))
</script>

<template>
  <div v-if="show" class="command-menu" @click.stop>
    <button
      v-for="cmd in filtered"
      :key="cmd.key"
      class="command-item"
      @click="emit('select', cmd)"
    >
      <span class="command-key">/{{ cmd.key }}</span>
      <span class="command-desc">{{ cmd.description }}</span>
    </button>
    <div v-if="!filtered.length" class="command-empty">无匹配命令</div>
  </div>
</template>

<style scoped>
.command-menu {
  position: absolute;
  bottom: calc(100% + 8px);
  left: 0;
  min-width: 260px;
  max-width: 100%;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: var(--shadow);
  padding: 4px;
  z-index: 50;
  max-height: 240px;
  overflow-y: auto;
}
.command-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  border: none;
  background: transparent;
  padding: 8px 10px;
  border-radius: 7px;
  cursor: pointer;
  font-family: inherit;
  text-align: left;
  transition: background 0.12s;
}
.command-item:hover {
  background: var(--bg-hover);
}
.command-key {
  font-weight: 600;
  color: var(--accent);
  font-size: 13px;
  flex-shrink: 0;
  font-family: 'SFMono-Regular', Consolas, monospace;
}
.command-desc {
  font-size: 12px;
  color: var(--text-secondary);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.command-empty {
  padding: 10px;
  text-align: center;
  font-size: 12px;
  color: var(--text-tertiary);
}
</style>
