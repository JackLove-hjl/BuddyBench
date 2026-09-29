<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useImagePreview } from '../../composables/useImagePreview'

/**
 * 图片放大浮层(全站只挂一次,见 App.vue)。
 *
 * 交互:滚轮缩放(以光标为锚点)、放大后拖动平移、双击或点"100%"复位、
 * 点空白 / 按 Esc 关闭。打开时锁住页面滚动,否则浮层背后还在滚、关掉后位置会跳。
 */
const { current, closeImage } = useImagePreview()

const MIN_SCALE = 1
const MAX_SCALE = 8
const WHEEL_STEP = 1.15

const scale = ref(1)
const pan = ref({ x: 0, y: 0 })
const dragging = ref(false)
const imgEl = ref<HTMLImageElement | null>(null)
let dragStart = { x: 0, y: 0, panX: 0, panY: 0 }

const zoomText = computed(() => `${Math.round(scale.value * 100)}%`)
const imageStyle = computed(() => ({
  transform: `translate(${pan.value.x}px, ${pan.value.y}px) scale(${scale.value})`,
  cursor: scale.value > 1 ? (dragging.value ? 'grabbing' : 'grab') : 'zoom-in',
}))

/** 复位到 100% 并居中 */
function reset() {
  scale.value = 1
  pan.value = { x: 0, y: 0 }
}

/** 平移范围限制在放大出来的余量里,避免图片被拖出视野再也找不回来 */
function clampPan() {
  const el = imgEl.value
  if (!el) return
  const maxX = Math.max(0, (el.offsetWidth * (scale.value - 1)) / 2)
  const maxY = Math.max(0, (el.offsetHeight * (scale.value - 1)) / 2)
  pan.value = {
    x: Math.min(maxX, Math.max(-maxX, pan.value.x)),
    y: Math.min(maxY, Math.max(-maxY, pan.value.y)),
  }
}

/** 滚轮缩放:以光标为锚点 —— 缩放后光标下的那个点仍停在原处 */
function onWheel(e: WheelEvent) {
  const el = imgEl.value
  if (!el) return
  const box = el.getBoundingClientRect()
  // 光标相对元素中心的偏移(元素已被 transform,box 是变换后的框,按比例换算回未缩放系)
  const px = (e.clientX - (box.left + box.width / 2)) / scale.value
  const py = (e.clientY - (box.top + box.height / 2)) / scale.value
  const factor = e.deltaY < 0 ? WHEEL_STEP : 1 / WHEEL_STEP
  const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale.value * factor))
  if (next === scale.value) return
  const ratio = next / scale.value
  scale.value = next
  pan.value = { x: px - (px - pan.value.x) * ratio, y: py - (py - pan.value.y) * ratio }
  clampPan()
}

function zoomBy(factor: number) {
  scale.value = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale.value * factor))
  clampPan()
}

function onMouseDown(e: MouseEvent) {
  if (scale.value <= 1) return // 未放大时不拖动,手感更符合直觉
  e.preventDefault()
  dragging.value = true
  dragStart = { x: e.clientX, y: e.clientY, panX: pan.value.x, panY: pan.value.y }
  window.addEventListener('mousemove', onMouseMove)
  window.addEventListener('mouseup', onMouseUp)
}

function onMouseMove(e: MouseEvent) {
  pan.value = {
    x: dragStart.panX + (e.clientX - dragStart.x),
    y: dragStart.panY + (e.clientY - dragStart.y),
  }
  clampPan()
}

function onMouseUp() {
  dragging.value = false
  window.removeEventListener('mousemove', onMouseMove)
  window.removeEventListener('mouseup', onMouseUp)
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') closeImage()
}

watch(current, (value) => {
  reset()
  if (value) {
    window.addEventListener('keydown', onKeydown)
    document.body.style.overflow = 'hidden'
  } else {
    window.removeEventListener('keydown', onKeydown)
    document.body.style.overflow = ''
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('mousemove', onMouseMove)
  window.removeEventListener('mouseup', onMouseUp)
  document.body.style.overflow = ''
})
</script>

<template>
  <Teleport to="body">
    <div v-if="current" class="img-preview" role="dialog" aria-modal="true" @click="closeImage" @wheel.prevent="onWheel">
      <img
        ref="imgEl"
        class="img-preview-img"
        :style="imageStyle"
        :src="current.url"
        :alt="current.label || '图片预览'"
        draggable="false"
        @click.stop
        @dblclick.prevent="reset"
        @mousedown="onMouseDown"
      />
      <div class="img-preview-bar" @click.stop @wheel.stop>
        <span v-if="current.label" class="img-preview-label" :title="current.label">{{ current.label }}</span>
        <div class="img-preview-zoom">
          <button type="button" class="zoom-btn" title="缩小" @click="zoomBy(1 / WHEEL_STEP)">−</button>
          <button type="button" class="zoom-value" title="恢复 100%" @click="reset">{{ zoomText }}</button>
          <button type="button" class="zoom-btn" title="放大" @click="zoomBy(WHEEL_STEP)">+</button>
        </div>
        <span class="img-preview-hint">滚轮缩放 · 拖动查看 · Esc 关闭</span>
        <button type="button" class="img-preview-close" title="关闭" @click="closeImage">×</button>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.img-preview {
  position: fixed;
  inset: 0;
  z-index: 2000;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 32px;
  box-sizing: border-box;
  background: rgba(0, 0, 0, 0.82);
  cursor: zoom-out;
  overflow: hidden;
  user-select: none;
}
.img-preview-img {
  max-width: 100%;
  max-height: calc(100vh - 110px);
  object-fit: contain;
  border-radius: 8px;
  background: var(--bg-elevated);
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.45);
  transition: transform 0.05s linear;
  will-change: transform;
}
.img-preview-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  max-width: 100%;
  color: rgba(255, 255, 255, 0.85);
  font-size: 12px;
  cursor: default;
}
.img-preview-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 40vw;
  font-family: 'SFMono-Regular', Consolas, monospace;
}
.img-preview-zoom {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 2px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.08);
}
.zoom-btn,
.zoom-value {
  border: none;
  background: transparent;
  color: #fff;
  cursor: pointer;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1;
  padding: 4px 7px;
}
.zoom-value {
  min-width: 48px;
  font-variant-numeric: tabular-nums;
}
.zoom-btn:hover,
.zoom-value:hover {
  background: rgba(255, 255, 255, 0.18);
}
.img-preview-hint {
  color: rgba(255, 255, 255, 0.55);
}
.img-preview-close {
  border: 1px solid rgba(255, 255, 255, 0.3);
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
  width: 26px;
  height: 26px;
  border-radius: 6px;
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
}
.img-preview-close:hover {
  background: rgba(255, 255, 255, 0.2);
}
</style>
