/**
 * 剪贴板复制。
 *
 * navigator.clipboard 只在安全上下文(https / localhost)可用,局域网用 http 打开时
 * 它是 undefined —— 这里用临时 textarea + execCommand 兜底,避免「点了没反应」。
 */

/** 代码块复制按钮的三种文案(渲染按钮与点击反馈共用) */
export const COPY_LABEL = '复制'
export const COPIED_LABEL = '已复制'
export const COPY_FAILED_LABEL = '复制失败'

export async function copyText(text: string): Promise<boolean> {
  if (!text) return false

  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // 权限被拒 / 非聚焦文档 → 落到 execCommand 兜底
    }
  }

  try {
    const area = document.createElement('textarea')
    area.value = text
    area.setAttribute('readonly', '')
    // 必须留在文档里且可选中,否则 select() 无效;用 fixed + 移出视口避免闪烁
    area.style.position = 'fixed'
    area.style.top = '-9999px'
    document.body.appendChild(area)
    area.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(area)
    return ok
  } catch {
    return false
  }
}
