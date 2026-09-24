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

  /**
   * 直接贴底(无动画,并把「跟随底部」重新打开)。
   *
   * 用于「整屏内容被替换」的场景 —— 切换会话、刷新页面:这时平滑滚动没有意义,
   * 内容随后还会因为图片异步加载、长文重排继续变高,动画停在半路,用户看到的就是"没到底"。
   * 贴底之后 stickToBottom 为 true,后续的高度变化会被调用方继续跟随(见 MessageList 的 ResizeObserver)。
   */
  async function jumpToBottom() {
    const el = getEl()
    if (!el) return
    stickToBottom.value = true
    await nextTick()
    el.scrollTop = el.scrollHeight
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

  return { showScrollBtn, stickToBottom, scrollToBottom, jumpToBottom }
}
