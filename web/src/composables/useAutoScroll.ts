import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

/** 自动滚动:rAF 节流 + 距底部 < 80px 才跟随 + 回到底部按钮 */
export function useAutoScroll(containerRef: () => HTMLElement | null) {
  const showScrollBtn = ref(false)
  const stickToBottom = ref(true)
  let rafId = 0
  let ticking = false

  const getEl = () => containerRef()

  function isNearBottom(el: HTMLElement): boolean {
    return el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }

  function onScroll() {
    const el = getEl()
    if (!el) return
    stickToBottom.value = isNearBottom(el)
    showScrollBtn.value = !stickToBottom.value
  }

  function scheduleScrollCheck() {
    if (ticking) return
    ticking = true
    rafId = requestAnimationFrame(() => {
      ticking = false
      onScroll()
    })
  }

  async function scrollToBottom(force = false) {
    const el = getEl()
    if (!el) return
    if (!force && !stickToBottom.value) return
    await nextTick()
    el.scrollTo({ top: el.scrollHeight, behavior: force ? 'smooth' : 'auto' })
  }

  onMounted(() => {
    const el = getEl()
    if (el) {
      el.addEventListener('scroll', scheduleScrollCheck, { passive: true })
    }
  })

  onBeforeUnmount(() => {
    cancelAnimationFrame(rafId)
    const el = getEl()
    if (el) {
      el.removeEventListener('scroll', scheduleScrollCheck)
    }
  })

  return { showScrollBtn, scrollToBottom }
}
