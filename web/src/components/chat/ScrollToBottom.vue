<script setup lang="ts">
defineProps<{ show: boolean }>()
defineEmits<{ (e: 'click'): void }>()
</script>

<template>
  <transition name="fade">
    <button v-if="show" class="scroll-to-bottom" @click="$emit('click')">
      <svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor">
        <path d="M8 3a.5.5 0 0 1 .5.5v7.29l2.65-2.65a.5.5 0 0 1 .7.7l-3.5 3.5a.5.5 0 0 1-.7 0l-3.5-3.5a.5.5 0 1 1 .7-.7l2.65 2.65V3.5A.5.5 0 0 1 8 3z" />
      </svg>
    </button>
  </transition>
</template>

<style scoped>
.scroll-to-bottom {
  position: absolute;
  right: 28px;
  bottom: 110px;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  border: 1px solid var(--border);
  background: var(--bg-elevated);
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  box-shadow: var(--shadow);
  transition: background 0.15s, color 0.15s, transform 0.15s;
  z-index: 10;
}
.scroll-to-bottom:hover {
  color: var(--accent);
  transform: translateY(-2px);
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s, transform 0.2s;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
  transform: translateY(8px);
}
</style>
