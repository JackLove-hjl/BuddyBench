import { COPIED_LABEL, COPY_FAILED_LABEL, copyText } from '../utils/clipboard'

/** 「已复制」提示停留时长(ms) */
const FEEDBACK_MS = 1600

/**
 * 代码块一键复制(事件委托)。
 *
 * 挂在 Markdown 正文 / 工具卡的根节点上即可:复制按钮是渲染出来的普通 HTML
 * (`<button class="code-copy-btn">`),委托写法不必逐个绑定,也天然适配
 * `v-html` 之后重建的 DOM 与流式期间不断刷新的代码块。
 */
export function useCodeCopy() {
  async function onCodeCopyClick(event: MouseEvent) {
    const button = (event.target as HTMLElement | null)?.closest<HTMLButtonElement>('.code-copy-btn')
    if (!button) return

    const pre = button.closest('.code-wrap')?.querySelector('pre')
    if (!pre) return

    // data-code 优先:命令块整体着色时带了 `$ ` 提示符,复制要拿不带前缀的原文
    const text = pre.dataset.code ?? pre.textContent ?? ''
    const label = button.textContent || ''

    const ok = await copyText(text)
    button.textContent = ok ? COPIED_LABEL : COPY_FAILED_LABEL
    button.classList.toggle('done', ok)
    window.setTimeout(() => {
      button.textContent = label
      button.classList.remove('done')
    }, FEEDBACK_MS)
  }

  return { onCodeCopyClick }
}
